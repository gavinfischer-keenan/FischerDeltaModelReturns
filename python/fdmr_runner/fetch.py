"""Fetch a region's gauge data into the local store.

    python -m fdmr_runner.fetch REGION_DIR STORE_FILE [--days 365]

For each gauge it asks which series the gauge records, then fetches those for
the period, and every channel measurement on record. Series the store already
holds up to the last `--fresh` minutes are skipped, so a rerun only fills gaps
and the hourly run fetches only the latest hour or so. Then it fetches the
latest river forecast for every gauge that names a forecast point, if that
issue is not already held. One gauge's failure does not stop the rest.

Each source has its own plan (the plan_* functions, in PLANS): what it asks
for, from where the store's copy ends (`window`). Every request is planned
first, then each host's requests are made in turn, the hosts side by side
(#34): `--pause` is the wait between two requests to the same host, never
between hosts, so no host is asked faster than before. A planned request runs
later, so each one binds what it needs when it is planned (its lambda's
defaults), never a loop variable that moves on.

    --forecast-only     skip the gauges and fetch only the forecasts
    --no-measurements   skip channel measurements (they change rarely; the
                        nightly run fetches them)
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from calendar import monthrange, timegm
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fdmr_adapters import erddap, http
from fdmr_adapters.areas import extent
from fdmr_adapters.cdec import SOURCE as CDEC
from fdmr_adapters.cdec import CdecAdapter
from fdmr_adapters.cdec_reports import CdecReports
from fdmr_adapters.cnrfc import SOURCE as CNRFC
from fdmr_adapters.cnrfc import CnrfcAdapter
from fdmr_adapters.cpc import CpcAdapter
from fdmr_adapters.ecostress import EcostressAdapter, fetch_land
from fdmr_adapters.ndbc import SOURCE as NDBC
from fdmr_adapters.ndbc import NdbcAdapter
from fdmr_adapters.noaa_tides import OBSERVED
from fdmr_adapters.noaa_tides import PARAMETER as TIDE
from fdmr_adapters.noaa_tides import SOURCE as TIDES
from fdmr_adapters.noaa_tides import NoaaTidesAdapter
from fdmr_adapters.noaa_weather import GHCNH, NoaaWeatherAdapter
from fdmr_adapters.nws_precip import NwsPrecipAdapter
from fdmr_adapters.openet import KEY_FILE as OPENET_KEY, OpenEtAdapter
from fdmr_adapters.opera import OperaAdapter
from fdmr_adapters.rws import SOURCE as RWS
from fdmr_adapters.rws import RwsAdapter
from fdmr_adapters.smap import SmapAdapter
from fdmr_adapters.snodas import SnodasAdapter
from fdmr_adapters.swot import SOURCE as SWOT
from fdmr_adapters.swot import SwotAdapter
from fdmr_adapters.usace_spk import UsaceSpkAdapter
from fdmr_adapters.usbr_cvo import SOURCE as USBR
from fdmr_adapters.usbr_cvo import UsbrCvoAdapter
from fdmr_adapters.usgs import DATUM, PARAMETERS, UsgsAdapter
from fdmr_adapters.usgs import SOURCE as USGS
from fdmr_store import Store

from . import bay, delta, land, overlays, soil, water_extent
from .region import Region, load_region

DAY = 86_400
DATUM_EVERY_S = 30 * DAY  # a gauge's datum is fetched again after this long (FDMR's choice: it changes only with a resurvey)
AVAILABLE_EVERY_S = DAY  # USGS's list of a gauge's series is asked again after this long (FDMR's choice: it changes rarely)
# A store's copy counts as reaching back to the start of a period if it
# begins within this of it (the first days a source has are often a little late).
SLACK_S = 2 * DAY
MIN_FRESH_S = 2 * 3600  # an hourly source is not asked again within this, whatever --fresh says
ARCHIVE_EVERY_S = 20 * 3600  # a source updated about daily is asked at most this often
OBSERVED_DAYS = 21  # the map's and the Bay's stations: the last three weeks
TIDE_LEVEL_DAYS = 35  # a tide gauge's levels: 30 days of tide residual, and some
SWOT_EVERY_S = 12 * 3600  # SWOT's passes come days apart: asked at most twice a day
SWOT_DAYS = 60
LAND_DAYS = 14  # soil moisture, rain and snowmelt: the last fortnight
OPENET_DAYS = 60
SEA_LEVEL_DAYS = 40
SEA_LEVEL_ASK_DAYS = 10  # the server refuses a longer window over a box
NWS_OBSERVED_DAYS = 7  # the Weather Service's observations: its last week

# The host each fetch goes to, by the first word of its label: one queue each,
# run side by side. A label that starts with a site number is USGS's.
HOSTS = {"cdec": "cdec.water.ca.gov", "noaa": "tidesandcurrents.noaa.gov", "nws": "api.weather.gov",
         "ghcnh": "ncei.noaa.gov", "ndbc": "ndbc.noaa.gov", "erddap": "erddap", "sea": "erddap",
         "ecostress": "earthdata", "opera": "earthdata", "smap": "earthdata", "swot": "earthdata",
         "openet": "openet", "snodas": "nsidc", "cpc": "cpc.ncep.noaa.gov", "usace": "usace",
         "usbr-cvo": "usbr.gov", "cnrfc": "cnrfc.noaa.gov", "rws": "rijkswaterstaat"}


def host_of(label: str) -> str:
    """The host a fetch, by its label, goes to (HOSTS)."""
    if label.startswith("nws precipitation"):
        return "water.noaa.gov"
    word = label.split(" ", 1)[0]
    return HOSTS.get(word, "usgs" if word[:1].isdigit() else "other")


def months(start: float, end: float) -> list[tuple[int, int]]:
    """The calendar months (Pacific Standard Time) from start to end."""
    a = datetime.fromtimestamp(start - 8 * 3600, timezone.utc)
    b = datetime.fromtimestamp(end - 8 * 3600, timezone.utc)
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_span(y: int, m: int) -> tuple[int, int]:
    """A month's first and last day starts (UTC s, midnights PST)."""
    return timegm((y, m, 1, 0, 0, 0)) + 8 * 3600, timegm((y, m, monthrange(y, m)[1], 0, 0, 0)) + 8 * 3600


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- The window: from where the store's copy ends ------------------------------


Coverage = tuple[int, int, int] | None  # the store's first and last time for a series and its count (Store.coverage), or None


def window(cov: Coverage, back: float, after: float = 1, slack: float | None = SLACK_S) -> float:
    """Where a series' fetch starts: `after` s past the end of the store's copy
    (0 to ask its last value again; negative to ask again over its last
    stretch, where a source revises), if that copy reaches back to `back`
    (begins within `slack` of it; any copy, with slack None), and never
    before `back`; otherwise from `back`."""
    if cov and (slack is None or cov[0] <= back + slack):
        return max(back, cov[1] + after)
    return back


def held(cov: Coverage, back: float, since: float, slack: float | None = SLACK_S) -> bool:
    """Whether the store's copy already reaches back to `back` (as `window`)
    and on to `since`: nothing to ask."""
    return bool(cov) and (slack is None or cov[0] <= back + slack) and cov[1] >= since


def predictions_from(cov: Coverage, now: float, keep_back: float, ask_back: float, keep_ahead: float) -> float | None:
    """Where tide predictions are asked from, kept `keep_back` s back and
    `keep_ahead` s ahead: None if they are; from a day before the copy's end
    if it reaches back that far; else from `ask_back` s back."""
    if cov and cov[0] <= now - keep_back and cov[1] >= now + keep_ahead:
        return None
    return now - ask_back if not cov or cov[0] > now - keep_back else cov[1] - DAY


# --- A run: its settings, the region's files, and its hosts' queues --------------


@dataclass
class Run:
    """One fetch run: the store, its window (`start` to `now`), the region's
    settings (each file read once), and the requests planned on each host's
    queue, with the account of what was asked and what failed."""

    args: argparse.Namespace
    region: Region
    store: Store
    now: float
    start: float
    dsm2: dict = field(default_factory=dict)
    overlays: dict = field(default_factory=dict)
    bay: dict = field(default_factory=dict)
    queues: dict[str, list[Callable[[], None]]] = field(default_factory=dict)
    attempts: int = 0
    failures: int = 0
    failed: list[dict] = field(default_factory=list)
    last_ask: dict[str, float] = field(default_factory=dict)
    tally: threading.Lock = field(default_factory=threading.Lock)  # the counts, shared by the hosts' threads

    def __post_init__(self) -> None:
        # The two agencies most plans ask, one adapter each for the run.
        self.usgs = UsgsAdapter(self.store)
        self.cdec = CdecAdapter(self.store)

    @property
    def region_dir(self) -> Path:
        return Path(self.args.region)

    @property
    def beside_store(self) -> Path:
        """Where the run keeps files beside the store (caches, archives, its account)."""
        return Path(self.args.store).parent

    @property
    def fresh_s(self) -> float:
        return self.args.fresh * 60

    @property
    def gauges_too(self) -> bool:
        """Whether this run fetches measurements, not only forecasts."""
        return not self.args.forecast_only

    def ask(self, label: str, fn: Callable[[], int], host: str) -> None:
        """Make one fetch now, on its host's thread: the pause after the last
        request to the same host first; a failure counted and named."""
        wait = self.last_ask.get(host, 0.0) + self.args.pause - time.time()
        if wait > 0:
            time.sleep(wait)
        with self.tally:
            self.attempts += 1
        try:
            print(f"{label:40s} {fn():7d}", flush=True)
        except http.PartialFetch as e:  # kept some, failed on some: both said, the failures recorded (#91)
            self.fail(label, e, kept=e.kept)
            print(f"{label:40s} {e.kept:7d}  PARTLY FAILED {e}", flush=True)
        except Exception as e:  # one gauge's outage must not stop the rest
            self.fail(label, e)
            print(f"{label:40s} FAILED {e}", flush=True)
        self.last_ask[host] = time.time()

    def fail(self, what: str, e: Exception, kept: int | None = None, counted: bool = True) -> None:
        with self.tally:
            if not counted:
                self.attempts += 1
            self.failures += 1
            self.failed.append({"what": what, "error": str(e)[:200], **({"kept": kept} if kept is not None else {})})

    def attempt(self, label: str, fn: Callable[[], int], host: str | None = None) -> None:
        """Plan one fetch on its host's queue (host_of its label)."""
        h = host or host_of(label)
        self.queues.setdefault(h, []).append(lambda: self.ask(label, fn, h))

    def run(self) -> float:
        """Every host's queue, side by side; each host's own requests in turn. The time it took, s."""
        t = time.time()
        with ThreadPoolExecutor(max_workers=max(1, len(self.queues))) as pool:
            for f in [pool.submit(lambda jobs=jobs: [job() for job in jobs]) for jobs in self.queues.values()]:
                f.result()
        return time.time() - t


# --- Each source's plan ------------------------------------------------------------


class UsgsSeries:
    """USGS's list of each gauge's series, asked at most daily
    (AVAILABLE_EVERY_S) and kept beside the store between runs."""

    def __init__(self, run: Run) -> None:
        self.run, self.usgs = run, run.usgs
        self.path = run.beside_store / "usgs-available.json"
        try:
            self.held = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except ValueError:
            self.held = {}

    def of(self, site: str) -> set[str]:
        r = self.run
        got = self.held.get(site)
        if got and got["fetched"] >= r.now - AVAILABLE_EVERY_S:
            return set(got["series"])
        wait = r.last_ask.get("usgs", 0.0) + r.args.pause - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            have = self.usgs.available(site)
        finally:
            r.last_ask["usgs"] = time.time()
        self.held[site] = {"fetched": int(r.now), "series": sorted(have)}
        return have

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.held), encoding="utf-8")
        tmp.replace(self.path)


def _usgs_values(r: Run, label: str, site: str, p: str, f0: float, sensor: str | None = None) -> None:
    r.attempt(label, lambda: r.usgs.fetch_values(site, p, f"{iso(f0)}/{iso(r.now)}", **({"sensor": sensor} if sensor else {})))


def _sensor(p: str) -> tuple[str, str | None]:
    """A parameter that names one of a gauge's sensors after it (`<parameter>_<sensor>`), split."""
    if p in PARAMETERS:
        return p, None
    base, _, sensor = p.rpartition("_")
    return base, sensor or None


def plan_gauges(r: Run, series: UsgsSeries) -> None:
    """The region's river gauges (region.toml [[gauges]]): CDEC's from where the
    store's copy ends; each USGS gauge's series, its channel measurements and
    (release and main gauges) its datum."""
    if not r.gauges_too:
        return
    for g in [g for g in r.region.gauges if g.source == CDEC]:
        f0 = window(r.store.coverage(CDEC, g.site, g.parameter), r.start)
        r.attempt(f"cdec {g.site} {g.parameter}", lambda g=g, f0=f0: r.cdec.fetch_values(g.site, g.parameter, int(f0), int(r.now), duration=g.duration or "H"))
        if g.usgs_site and not r.args.no_measurements:
            r.attempt(f"{g.usgs_site} channel_measurements", lambda g=g: r.usgs.fetch_channel_measurements(g.usgs_site))

    def usgs_gauge(g) -> None:
        """One USGS gauge, on USGS's thread: which series it records, then each
        series from where the store's copy ends."""
        try:
            have = series.of(g.site)
        except Exception as e:  # counted and named like any fetch (#75)
            r.fail(f"{g.site} series list", e, counted=False)
            print(f"{g.site} series list FAILED {e}", flush=True)
            return
        for p in (p for p in PARAMETERS if p in have):
            cov = r.store.coverage(USGS, g.site, p)
            if held(cov, r.start, r.now - r.fresh_s):  # held from (about) the start of the period to a moment ago
                print(f"{g.site} {p:31s} held    {g.name}", flush=True)
                continue
            f0 = window(cov, r.start, after=0)
            r.ask(f"{g.site} {p}", lambda p=p, f0=f0: r.usgs.fetch_values(g.site, p, f"{iso(f0)}/{iso(r.now)}"), "usgs")
        if not r.args.no_measurements:
            r.ask(f"{g.site} channel_measurements", lambda: r.usgs.fetch_channel_measurements(g.site), "usgs")

    for g in [g for g in r.region.gauges if g.source == USGS]:
        r.queues.setdefault("usgs", []).append(lambda g=g: usgs_gauge(g))
    # Each release and main gauge's datum, for the river's measured slope: kept
    # in the store and fetched again monthly, so publish never asks USGS itself (#52).
    for g in [g for g in r.region.gauges if g.source == USGS and g.role in ("release", "main")]:
        cov = r.store.coverage(USGS, g.site, DATUM)
        if not (cov and cov[1] >= r.now - DATUM_EVERY_S):
            r.attempt(f"{g.site} datum", lambda g=g: r.usgs.fetch_datum(g.site, int(r.now)))


def plan_stations(r: Run) -> None:
    """The region's measuring stations (region.toml [[stations]]), each by its
    agency, from where the store's copy ends. Rijkswaterstaat's are plan_rws's."""
    if not r.gauges_too:
        return
    for s in r.region.stations:
        if s["source"] == USGS:  # its readings by parameter
            for p in s["quantities"]:
                _usgs_values(r, f"{s['site']} {p}", s["site"], p, window(r.store.coverage(USGS, s["site"], p), r.start, after=0))
        elif s["source"] == CDEC:
            for p in s["quantities"]:
                cov = r.store.coverage(CDEC, s["site"], p)
                if held(cov, r.start, r.now - max(r.fresh_s, MIN_FRESH_S)):
                    print(f"{s['site']} {p:31s} held    {s['name']}", flush=True)
                    continue
                f0 = window(cov, r.start)
                r.attempt(f"cdec {s['site']} {p}", lambda s=s, p=p, f0=f0: r.cdec.fetch_values(s["site"], p, int(f0), int(r.now), duration=s.get("duration", "H")))


def plan_swot(r: Run) -> None:
    """SWOT's view of the rivers (region.toml [[river_swot]]): passes come days apart."""
    for rs in r.region.river_swot if r.gauges_too else []:
        sw = SwotAdapter(r.store)
        for x in rs["reaches"]:
            last = r.store.latest(SWOT, x["id"], "width")
            if last and last[1] >= r.now - SWOT_EVERY_S:
                continue
            r.attempt(f"swot {x['id']}", lambda x=x, sw=sw: sw.fetch(x["id"], iso(r.now - SWOT_DAYS * DAY), iso(r.now)))


def plan_map_stations(r: Run) -> None:
    """What the map shows (overlays.toml): water temperature from orbit, the
    stations of agencies other than CDEC, and CDEC's stations, dams, pumps and
    gates in one request per duration and set of sensors (those with readings
    held from where their newest ends, new ones from the start)."""
    o = r.overlays
    if not r.gauges_too:
        return
    if o.get("satellite_temperature"):
        sat = o["satellite_temperature"]
        keep = sat.get("days", 14) * DAY
        r.attempt("ecostress", lambda: EcostressAdapter(r.beside_store / sat["cache"]).fetch(tuple(sat["box"]), int(r.now - keep), int(r.now), keep))
    back = r.now - OBSERVED_DAYS * DAY
    for s in o.get("stations", []):
        src = s.get("source", CDEC)
        if src == NDBC:
            r.attempt(f"ndbc {s['site']}", lambda s=s: NdbcAdapter(r.store).fetch_met(s["site"]) if s.get("kind") == "weather" else NdbcAdapter(r.store).fetch(s["site"]))
        elif src == TIDES:
            for prod in s.get("products", ["water_temperature"]):
                pback = r.now - (TIDE_LEVEL_DAYS if prod == "water_level" else OBSERVED_DAYS) * DAY
                f0 = window(r.store.coverage(src, s["site"], OBSERVED[prod][0][1]), pback, slack=DAY)
                r.attempt(f"noaa {s['site']} {prod}", lambda s=s, f0=f0, prod=prod: NoaaTidesAdapter(r.store).fetch_observed(s["site"], prod, int(f0), int(r.now)))
            if "water_level" in s.get("products", []):  # its predictions, for the tide residual
                f0 = predictions_from(r.store.coverage(src, s["site"], TIDE), r.now, TIDE_LEVEL_DAYS * DAY, (TIDE_LEVEL_DAYS + 1) * DAY, DAY)
                if f0 is not None:
                    r.attempt(f"noaa {s['site']} {TIDE}", lambda s=s, f0=f0: NoaaTidesAdapter(r.store).fetch_predictions(s["site"], int(f0), int(r.now + 3 * DAY)))
        elif src == USGS:
            for p in s.get("measures", ["water_temperature", "specific_conductance"]):
                _usgs_values(r, f"{s['site']} {p}", s["site"], p, window(r.store.coverage(USGS, s["site"], p), back, after=0, slack=None))
        elif src.startswith("erddap:"):
            f0 = window(r.store.coverage(src, s["site"], "water_temperature"), back, slack=None)
            r.attempt(f"erddap {s['site']}", lambda s=s, f0=f0: erddap.ErddapAdapter(r.store, s["server"]).fetch(s["site"], iso(f0), ["water_temperature", "salinity"]))
    for dur, group, shown in overlays.fetch_groups(o):
        ends = {}
        for site in group:
            covs = [c for c in (r.store.coverage(CDEC, site, p) for p in shown) if c]
            if covs:
                ends[site] = max(c[1] for c in covs)
        old, new = [s for s in group if s in ends], [s for s in group if s not in ends]
        if old:
            f0 = max(r.start, min(ends[s] for s in old) - 3600)
            r.attempt(f"cdec {len(old)} stations ({dur}) from {iso(f0)}", lambda h=old, f0=f0, d=dur, sh=shown: sum(r.cdec.fetch_many(h, sh, int(f0), int(r.now), d).values()))
        if new:
            r.attempt(f"cdec {len(new)} new stations ({dur})", lambda n=new, d=dur, sh=shown: sum(r.cdec.fetch_many(n, sh, int(r.start), int(r.now), d).values()))


def plan_land(r: Run) -> None:
    """The land: its surface temperature along the rivers and at their weather
    zones' airports, from orbit (region.toml [river_temperature.land]); how wet
    it is where rain runs off (region.toml [soil_moisture], NASA SMAP), the rain
    and snowmelt reaching it, and the water it gives back to the air (OpenET,
    where the region asks); and where the water is, from radar passes
    (overlays.toml [water_extent])."""
    if not r.gauges_too:
        return
    places = land.places(r.region_dir)
    if places:
        lkeep = int(land.load(r.region_dir)[0].get("days", 14)) * DAY
        r.attempt("ecostress land", lambda: fetch_land(EcostressAdapter(r.beside_store / "ecostress"), r.store, places,
                                                       land.box(places), int(r.now - lkeep), int(r.now)))
    areas = soil.areas(r.region_dir)
    if areas:
        since = int(r.now - LAND_DAYS * DAY)
        r.attempt("smap soil moisture", lambda: SmapAdapter(r.store).fetch(areas, since, int(r.now)))
        r.attempt("nws precipitation", lambda: NwsPrecipAdapter(r.store).fetch(areas, since, int(r.now)))
        r.attempt("snodas snowmelt", lambda: SnodasAdapter(r.store).fetch(areas, since, int(r.now)))
        for name in (soil.load(r.region_dir) or {}).get("openet", []) if OPENET_KEY.exists() else []:
            area = next((a for a in areas if a["site"] == f"area:{name}"), None)
            for sample in soil.openet_samples(area) if area else []:
                r.attempt(f"openet {sample['site']}", lambda sample=sample: OpenEtAdapter(r.store).fetch(sample, int(r.now - OPENET_DAYS * DAY), int(r.now)))
    we = r.overlays.get("water_extent")
    if we:
        box = extent([{"box": a["box"]} for a in we["areas"]], margin=0.0)
        r.attempt("opera water maps", lambda: OperaAdapter(r.beside_store / we.get("cache", "opera")).fetch(box, water_extent.windows(we, int(r.now))))


def plan_ocean(r: Run) -> None:
    """The ocean's state at the model's edge (overlays.toml [ocean_state])."""
    ostate = r.overlays.get("ocean_state", {})
    if ostate.get("enso"):
        r.attempt("cpc enso indices", lambda: CpcAdapter(r.store).fetch())
    if ostate.get("sea_level"):
        sl = ostate["sea_level"]
        f0 = window(r.store.coverage(erddap.source(sl["server"]), sl["site"], "sea_level_anomaly"), r.now - SEA_LEVEL_DAYS * DAY, slack=None)
        split = r.now - SEA_LEVEL_ASK_DAYS * DAY

        def sea_level(f0: float = f0) -> int:
            a = erddap.ErddapAdapter(r.store, sl["server"])
            got = a.fetch_grid_mean(sl["dataset"], sl["variable"], tuple(sl["box"]), iso(max(f0, split)), sl["site"], "sea_level_anomaly", "m")
            if f0 < split:  # the older part on its own: the server refuses a longer window over a box
                got += a.fetch_grid_mean(sl["dataset"], sl["variable"], tuple(sl["box"]), iso(f0), sl["site"], "sea_level_anomaly", "m", until_iso=iso(split))
            return got

        r.attempt("sea level anomaly", sea_level)


def plan_reports(r: Run) -> None:
    """DWR's daily reservoir and snow reports, for the dams that show them
    (overlays.toml [[structures]]), and the Corps' daily flood-control report,
    for the release rules' reservoirs."""
    if not r.gauges_too:
        return
    structures = r.overlays.get("structures", [])
    res = [s["site"] for s in structures if s.get("storage_report")]
    if res or any(s.get("snow") for s in structures):
        reports = CdecReports(r.store)
        if res:
            r.attempt("cdec reservoir report", lambda: reports.fetch_storage(res, int(r.now)))
        r.attempt("cdec snow reports", lambda: reports.fetch_snow(int(r.now)))


def plan_delta_feeds(r: Run) -> None:
    """What the Delta's DSM2 run reads or is checked against (dsm2.toml; delta.feeds)."""
    if not r.gauges_too:
        return
    for f in delta.feeds(r.region_dir):
        src, site, p = f["source"], f["site"], f["parameter"]
        cov = r.store.coverage(src, site, p)
        if held(cov, r.start, r.now - max(r.fresh_s, MIN_FRESH_S)):
            print(f"{site} {p:31s} held    {f.get('name', f.get('path', ''))}", flush=True)
            continue
        f0 = window(cov, r.start)
        if src == USGS and f.get("duration") == "D":
            r.attempt(f"{site} {p} (daily)", lambda site=site, p=p, f0=f0: r.usgs.fetch_daily(site, p, f"{iso(f0 - DAY)}/{iso(r.now)}"))
        elif src == USGS:
            base, sensor = _sensor(p)
            r.attempt(f"{site} {p}", lambda site=site, b=base, sn=sensor, f0=f0: r.usgs.fetch_values(site, b, f"{iso(f0)}/{iso(r.now)}", sensor=sn))
        elif src == CDEC:
            dur = f.get("duration", "H")
            r.attempt(f"cdec {site} {p}", lambda site=site, p=p, f0=f0, dur=dur: r.cdec.fetch_values(site, p, int(f0), int(r.now), duration=dur))


def plan_weather(r: Run) -> None:
    """Weather, for the Delta's water temperature (dsm2.toml [temperature]) and
    each river weather zone's (region.toml [river_temperature]). NOAA's archive
    comes a year's file per station and runs days behind, so it is fetched
    once a day; the Weather Service's observations (its last week) and
    forecast every run."""
    for tcfg in [c for c in [r.dsm2.get("temperature"), *r.region.river_temperature.get("weather", [])] if c]:
        wx = NoaaWeatherAdapter(r.store)
        for s in tcfg["stations"] if r.gauges_too else []:
            if "ndbc" in s:  # an NDBC weather station: its file holds the last 45 days
                r.attempt(f"ndbc {s['ndbc']} weather", lambda s=s: NdbcAdapter(r.store).fetch_met(s["ndbc"]))
                continue
            cov = r.store.coverage(GHCNH, s["ghcnh"], "air_temperature")
            fetched = r.store.latest(GHCNH, s["ghcnh"], "air_temperature")
            if cov and cov[0] <= r.start + SLACK_S and fetched and fetched[1] >= r.now - ARCHIVE_EVERY_S:
                print(f"ghcnh {s['ghcnh']} {'':25s} held    {s['name']}", flush=True)
            else:
                f0 = window(cov, r.start, after=-DAY)  # its last day again: the archive fills in late
                r.attempt(f"ghcnh {s['ghcnh']}", lambda s=s, f0=f0, wx=wx: wx.fetch_archive(s["ghcnh"], int(f0), int(r.now)))
            r.attempt(f"nws {s['nws']} observations", lambda s=s, wx=wx: wx.fetch_observations(s["nws"], int(r.now - NWS_OBSERVED_DAYS * DAY)))
        if "forecast" in tcfg:
            fc = tcfg["forecast"]
            r.attempt(f"nws forecast {fc['office']}/{fc['x']},{fc['y']}", lambda wx=wx, fc=fc: wx.fetch_forecast(fc["office"], fc["x"], fc["y"])[1])


def plan_bay(r: Run) -> None:
    """The Bay's inputs (bay.toml): the outflow its extension is fitted to (a
    long record), the stations its salinity and temperature are corrected by
    (CDEC's come with the map's; a USGS bay station has an upper and a lower
    sensor, each its own series), and USGS's daily outflows it is checked
    against."""
    b = r.bay
    if not r.gauges_too or not b:
        return
    ext = b.get("extension")
    if ext and ext["outflow"]["source"] == CDEC:
        o = ext["outflow"]
        back = r.now - (ext["fit_days"] + 10) * DAY
        cov = r.store.coverage(CDEC, o["site"], o["parameter"])
        if cov and cov[1] >= r.now - DAY:
            print(f"cdec {o['site']} {o['parameter']:26s} held", flush=True)
        else:
            f0 = window(cov, back)
            r.attempt(f"cdec {o['site']} {o['parameter']}", lambda o=o, f0=f0: r.cdec.fetch_values(o["site"], o["parameter"], int(f0), int(r.now), duration=o.get("duration", "D")))
    back = r.now - OBSERVED_DAYS * DAY
    for st in (b.get("quality") or {}).get("stations", []):
        if st["source"].startswith("erddap:"):
            f0 = window(r.store.coverage(st["source"], st["site"], "water_temperature"), back, slack=None)
            r.attempt(f"erddap {st['site']}", lambda st=st, f0=f0: erddap.ErddapAdapter(r.store, st["server"]).fetch(st["site"], iso(f0), ["water_temperature", "salinity"]))
        elif st["source"] == NDBC:  # its file holds the last 45 days
            if st.get("file") == "standard":  # a buoy: sea temperature in its standard file
                r.attempt(f"ndbc {st['site']}", lambda s=st["site"]: NdbcAdapter(r.store).fetch_met(s))
            else:
                r.attempt(f"ndbc {st['site']}", lambda s=st["site"]: NdbcAdapter(r.store).fetch(s))
        elif st["source"] == TIDES:
            f0 = window(r.store.coverage(TIDES, st["site"], "water_temperature"), back, slack=None)
            r.attempt(f"noaa {st['site']} water_temperature", lambda s=st["site"], f0=f0: NoaaTidesAdapter(r.store).fetch_water_temperature(s, int(f0), int(r.now)))
        elif st["source"] == USGS:
            for p in [*st.get("conductance", []), *st.get("temperature", [])]:
                base, _, sensor = p.rpartition("_") if p.endswith(("_upper", "_lower")) else (p, "", "")
                cov = r.store.coverage(USGS, st["site"], p)
                if held(cov, back, r.now - max(r.fresh_s, MIN_FRESH_S), slack=None):
                    print(f"{st['site']} {p:31s} held    {st['name']}", flush=True)
                    continue
                f0 = window(cov, back)
                r.attempt(f"{st['site']} {p}", lambda s=st["site"], b=base, sn=sensor or None, f0=f0: r.usgs.fetch_values(s, b, f"{iso(f0)}/{iso(r.now)}", sensor=sn))
    for c in b.get("outflow_checks", []):
        if c["source"] == USGS and c.get("duration") == "D":
            f0 = window(r.store.coverage(USGS, c["site"], c["parameter"]), r.start, after=-3 * DAY, slack=None)  # its last days again: revised
            r.attempt(f"{c['site']} {c['parameter']} (daily)", lambda c=c, f0=f0: r.usgs.fetch_daily(c["site"], c["parameter"], f"{iso(f0)}/{iso(r.now)}"))


def plan_depletion(r: Run) -> None:
    """The Delta's daily island water use (dsm2.toml [depletion]): the months the
    Delta's runs reach back over, each fetched until it is complete, and the
    month in progress (and the one before, still being revised) once a day."""
    d = r.dsm2.get("depletion")
    if not (d and d["source"] == USBR and r.gauges_too):
        return
    cvo = UsbrCvoAdapter(r.store)
    back = r.now - (r.dsm2.get("days_back", 28) + 2) * DAY
    last = r.store.latest(USBR, d["site"], d["parameter"])
    fetched_lately = last is not None and last[1] >= r.now - ARCHIVE_EVERY_S
    for y, m in months(back, r.now):
        m0, m1 = month_span(y, m)
        got = r.store.values(USBR, d["site"], d["parameter"], m0, m1 + 1)
        recent = m1 > r.now - 40 * DAY  # still being revised
        if (recent and fetched_lately) or (not recent and len(got) >= (m1 - m0) // DAY + 1):
            print(f"usbr-cvo {y}-{m:02d} {d['parameter']:24s} held", flush=True)
            continue
        r.attempt(f"usbr-cvo {y}-{m:02d}", lambda y=y, m=m: cvo.fetch_month(y, m, int(r.now)))


def plan_forecasts(r: Run) -> None:
    """Forecasts: the Corps' flood-control report for the release rules'
    reservoirs; the River Forecast Center's, one file per forecast group
    holding all its points (the gauges', their release rules' and the Delta's
    inputs'); tide predictions for the Delta's edge, kept a month back and a
    fortnight ahead."""
    spk = {g.release_rule["reservoir"]: g.release_rule["report"] for g in r.region.gauges if g.release_rule and "report" in g.release_rule}
    if spk and r.gauges_too:
        r.attempt("usace flood-control report", lambda: UsaceSpkAdapter(r.store).fetch(spk))
    groups: dict[str, list[str]] = {}
    for g in r.region.gauges:
        if g.forecast and g.forecast.get("source") == CNRFC:
            groups.setdefault(g.forecast["group"], []).append(g.forecast["point"])
        for key in ("inflow", "release_forecast"):  # a release rule's forecasts (fdmr_runner.releases)
            fc = (g.release_rule or {}).get(key)
            if fc and fc["point"] not in groups.setdefault(fc["group"], []):
                groups[fc["group"]].append(fc["point"])
    delta_forecasts = delta.forecasts(r.region_dir)
    for fc in delta_forecasts:
        if fc["source"] == CNRFC and fc["point"] not in groups.setdefault(fc["group"], []):
            groups[fc["group"]].append(fc["point"])
    tides = NoaaTidesAdapter(r.store)
    for fc in delta_forecasts if r.gauges_too else []:
        if fc["source"] != TIDES:
            continue
        f0 = predictions_from(r.store.coverage(TIDES, fc["station"], TIDE), r.now, 35 * DAY, 40 * DAY, 15 * DAY)
        if f0 is None:
            print(f"noaa {fc['station']} {TIDE:26s} held", flush=True)
            continue
        r.attempt(f"noaa {fc['station']} {TIDE}", lambda st=fc["station"], f0=f0: tides.fetch_predictions(st, int(f0), int(r.now + 16 * DAY)))
    cnrfc = CnrfcAdapter(r.store, archive=r.beside_store / "cnrfc-issues")  # kept for hindcasts (#9)

    def cnrfc_group(group: str, points: list[str]) -> int:
        issue, n = cnrfc.fetch(group, points)
        print(f"cnrfc {group:34s} issue {issue}" + ("" if n else " (held)"), flush=True)
        return n

    for group, points in groups.items():
        r.attempt(f"cnrfc {group}", lambda g=group, p=points: cnrfc_group(g, p))


def plan_rws(r: Run) -> None:
    """Rijkswaterstaat's stations (region.toml [[stations]], source "rws") and
    river gauges: each quantity from where the store's record ends, and the
    agency's own forecasts afresh."""
    if not r.gauges_too:
        return
    live = [s for s in r.region.stations if s["source"] == RWS]
    live += [{"source": g.source, "site": g.site, "quantities": [g.parameter, "water_level"],
              "forecasts": [g.parameter] if g.forecast and g.forecast.get("source") == g.source else []}
             for g in r.region.gauges if g.source == RWS
             and not any(s.get("site") == g.site and g.parameter in s.get("quantities", []) for s in live)]
    if not live:
        return
    rws = RwsAdapter(r.store)
    for st in live:
        for q in st.get("quantities", []):
            f0 = window(r.store.coverage(RWS, st["site"], q), r.start)
            r.attempt(f"rws {st['site']} {q}", lambda st=st, q=q, f0=f0: rws.fetch(st["site"], q, int(f0), int(r.now),
                                                                  depth_cm=st.get("depth_cm") if q in st.get("depth_for", ["chloride", "water_temperature"]) else None))
        for q in st.get("forecasts", []):
            r.attempt(f"rws {st['site']} {q} forecast", lambda st=st, q=q: rws.fetch(st["site"], q, int(r.now) - 3 * 3600, int(r.now) + 3 * DAY, forecast=True))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("--days", type=int, default=365, help="how far back to fetch (default 365)")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between requests, to be polite")
    ap.add_argument("--fresh", type=float, default=30, help="minutes within which a series counts as up to date")
    ap.add_argument("--forecast-only", action="store_true")
    ap.add_argument("--no-measurements", action="store_true")
    args = ap.parse_args(argv)

    region_dir = Path(args.region)
    now = time.time()
    r = Run(args, load_region(region_dir), Store(args.store), now, now - args.days * DAY,
            dsm2=delta.load(region_dir) or {}, overlays=(overlays.load(region_dir) or {}) if not args.forecast_only else {},
            bay=bay.load(region_dir) or {})
    series = UsgsSeries(r)
    plan_gauges(r, series)
    plan_stations(r)
    plan_swot(r)
    plan_map_stations(r)
    plan_land(r)
    plan_ocean(r)
    plan_reports(r)
    plan_delta_feeds(r)
    plan_weather(r)
    plan_bay(r)
    plan_depletion(r)
    plan_forecasts(r)
    plan_rws(r)
    took = r.run()
    print("by host: " + ", ".join(f"{h} {len(j)}" for h, j in sorted(r.queues.items(), key=lambda kv: -len(kv[1])))
          + f"; {took:.0f} s", flush=True)
    series.save()
    r.store.close()
    print(f"done: {len(r.region.gauges)} gauges, {r.attempts} fetches, {r.failures} failures")
    # The run's own account, published with the next cycle (index.json
    # `fetch_status`): a failed source stays visible by name without one
    # network blip marking the whole cycle failed. Only a run where every
    # fetch failed is a failure.
    status = {"finished": int(time.time()), "attempts": r.attempts, "failures": r.failures, "failed": r.failed}
    r.beside_store.joinpath(f"fetch-status-{r.region.id}.json").write_text(json.dumps(status), encoding="utf-8")
    return 1 if r.attempts and r.failures == r.attempts else 0


if __name__ == "__main__":
    sys.exit(main())
