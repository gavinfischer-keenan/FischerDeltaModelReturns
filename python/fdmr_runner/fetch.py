"""Fetch a region's gauge data into the local store.

    python -m fdmr_runner.fetch REGION_DIR STORE_FILE [--days 365]

For each gauge it asks which series the gauge records, then fetches those for
the period, and every channel measurement on record. Series the store already
holds up to the last `--fresh` minutes are skipped, so a rerun only fills gaps
and the hourly run fetches only the latest hour or so. Then it fetches the
latest river forecast for every gauge that names a forecast point, if that
issue is not already held. One gauge's failure does not stop the rest.

Every request is planned first, then each host's requests are made in turn,
the hosts side by side (#34): `--pause` is the wait between two requests to
the same host, never between hosts, so no host is asked faster than before.
A planned request runs later, so each one binds what it needs when it is
planned (its lambda's defaults), never a loop variable that moves on.

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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fdmr_adapters.cdec import SOURCE as CDEC
from fdmr_adapters.cdec import CdecAdapter
from fdmr_adapters.cnrfc import CnrfcAdapter
from fdmr_adapters.noaa_tides import PARAMETER as TIDE
from fdmr_adapters.noaa_tides import NoaaTidesAdapter
from fdmr_adapters.noaa_weather import GHCNH, NoaaWeatherAdapter
from fdmr_adapters.usbr_cvo import SOURCE as USBR
from fdmr_adapters.usbr_cvo import UsbrCvoAdapter
from fdmr_adapters.usgs import DATUM, PARAMETERS, SOURCE, UsgsAdapter
from fdmr_store import Store

from . import delta
from .region import load_region

DATUM_EVERY_S = 30 * 86_400  # a gauge's datum is fetched again after this long (FDMR's choice: it changes only with a resurvey)
AVAILABLE_EVERY_S = 86_400  # USGS's list of a gauge's series is asked again after this long (FDMR's choice: it changes rarely)

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
    from calendar import monthrange, timegm

    return timegm((y, m, 1, 0, 0, 0)) + 8 * 3600, timegm((y, m, monthrange(y, m)[1], 0, 0, 0)) + 8 * 3600


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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

    region = load_region(args.region)
    store = Store(args.store)
    usgs = UsgsAdapter(store)
    cdec_gauge = CdecAdapter(store)
    now = time.time()
    start = now - args.days * 86_400
    failures = 0
    attempts = 0
    failed: list[dict] = []
    tally = threading.Lock()  # the counts, shared by the hosts' threads
    queues: dict[str, list] = {}  # host -> its planned fetches, made in turn (#34)
    last_ask: dict[str, float] = {}  # host -> when it was last asked

    def ask(label: str, fn, host: str) -> None:
        """Make one fetch now, on its host's thread: the pause after the last
        request to the same host first; a failure counted and named."""
        nonlocal failures, attempts
        wait = last_ask.get(host, 0.0) + args.pause - time.time()
        if wait > 0:
            time.sleep(wait)
        with tally:
            attempts += 1
        try:
            print(f"{label:40s} {fn():7d}", flush=True)
        except Exception as e:  # one gauge's outage must not stop the rest
            with tally:
                failures += 1
                failed.append({"what": label, "error": str(e)[:200]})
            print(f"{label:40s} FAILED {e}", flush=True)
        last_ask[host] = time.time()

    def attempt(label: str, fn, host: str | None = None) -> None:
        """Plan one fetch on its host's queue (host_of its label)."""
        h = host or host_of(label)
        queues.setdefault(h, []).append(lambda: ask(label, fn, h))

    # USGS's list of each gauge's series, asked at most daily (AVAILABLE_EVERY_S).
    avail_file = Path(args.store).parent / "usgs-available.json"
    try:
        avail = json.loads(avail_file.read_text(encoding="utf-8")) if avail_file.exists() else {}
    except ValueError:
        avail = {}

    def available(site: str) -> set[str]:
        held = avail.get(site)
        if held and held["fetched"] >= now - AVAILABLE_EVERY_S:
            return set(held["series"])
        wait = last_ask.get("usgs", 0.0) + args.pause - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            have = usgs.available(site)
        finally:
            last_ask["usgs"] = time.time()
        avail[site] = {"fetched": int(now), "series": sorted(have)}
        return have

    cdec_gauges = [g for g in region.gauges if g.source == CDEC]
    for g in [] if args.forecast_only else cdec_gauges:
        cov = store.coverage(CDEC, g.site, g.parameter)
        from_t = max(start, cov[1] + 1) if cov and cov[0] <= start + 2 * 86_400 else start
        attempt(f"cdec {g.site} {g.parameter}", lambda g=g, f0=from_t: cdec_gauge.fetch_values(g.site, g.parameter, int(f0), int(now), duration=g.duration or "H"))
        if g.usgs_site and not args.no_measurements:
            attempt(f"{g.usgs_site} channel_measurements", lambda g=g: usgs.fetch_channel_measurements(g.usgs_site))
    def usgs_gauge(g) -> None:
        """One USGS gauge, on USGS's thread: which series it records, then each
        series from where the store's copy ends."""
        nonlocal failures, attempts
        try:
            have = available(g.site)
        except Exception as e:
            with tally:  # counted and named like any fetch (#75)
                attempts += 1
                failures += 1
                failed.append({"what": f"{g.site} series list", "error": str(e)[:200]})
            print(f"{g.site} series list FAILED {e}", flush=True)
            return
        for p in (p for p in PARAMETERS if p in have):
            cov = store.coverage(SOURCE, g.site, p)
            # Already held from (about) the start of the period to a moment ago: skip.
            if cov and cov[0] <= start + 2 * 86_400 and cov[1] >= now - args.fresh * 60:
                print(f"{g.site} {p:31s} held    {g.name}", flush=True)
                continue
            from_t = max(start, cov[1]) if cov and cov[0] <= start + 2 * 86_400 else start
            ask(f"{g.site} {p}", lambda p=p, f=from_t: usgs.fetch_values(g.site, p, f"{iso(f)}/{iso(now)}"), "usgs")
        if not args.no_measurements:
            ask(f"{g.site} channel_measurements", lambda: usgs.fetch_channel_measurements(g.site), "usgs")

    for g in [] if args.forecast_only else [g for g in region.gauges if g.source == SOURCE]:
        queues.setdefault("usgs", []).append(lambda g=g: usgs_gauge(g))
    # Each release and main gauge's datum, for the river's measured slope: kept
    # in the store and fetched again monthly (it changes only with a resurvey),
    # so publish never asks USGS itself (#52).
    for g in [] if args.forecast_only else [g for g in region.gauges if g.source == SOURCE and g.role in ("release", "main")]:
        cov = store.coverage(SOURCE, g.site, DATUM)
        if cov and cov[1] >= now - DATUM_EVERY_S:
            continue
        attempt(f"{g.site} datum", lambda g=g: usgs.fetch_datum(g.site, int(now)))
    # The region's measuring stations (region.toml [[stations]]), each by its
    # agency, from where the store's copy ends. Rijkswaterstaat's come with
    # the river forecasts, below.
    cdec = CdecAdapter(store)
    for s in [] if args.forecast_only else region.stations:
        if s["source"] == SOURCE:  # a USGS station: its readings by parameter
            for p in s["quantities"]:
                cov = store.coverage(SOURCE, s["site"], p)
                from_t = max(start, cov[1]) if cov and cov[0] <= start + 2 * 86_400 else start
                attempt(f"{s['site']} {p}", lambda s=s, p=p, f=from_t: usgs.fetch_values(s["site"], p, f"{iso(f)}/{iso(now)}"))
            continue
        if s["source"] != CDEC:
            continue
        for p in s["quantities"]:
            cov = store.coverage(CDEC, s["site"], p)
            if cov and cov[0] <= start + 2 * 86_400 and cov[1] >= now - max(args.fresh * 60, 2 * 3600):
                print(f"{s['site']} {p:31s} held    {s['name']}", flush=True)
                continue
            from_t = max(start, cov[1] + 1) if cov and cov[0] <= start + 2 * 86_400 else start
            attempt(f"cdec {s['site']} {p}", lambda s=s, p=p, f=from_t: cdec.fetch_values(s["site"], p, int(f), int(now), duration=s.get("duration", "H")))
    # Stations, dams, pumps and gates shown on the map (overlays.toml): asked
    # in one request per duration and set of sensors. Stations with readings already held
    # are asked from where their newest ends; new ones (and any that have
    # never answered) from the start of the period.
    from . import overlays

    ocfg = (overlays.load(Path(args.region)) or {}) if not args.forecast_only else {}
    # SWOT's view of the rivers (region.toml [[river_swot]]): passes come days
    # apart, so asked at most twice a day.
    from fdmr_adapters.swot import SOURCE as SWOT
    from fdmr_adapters.swot import SwotAdapter

    for rs in [] if args.forecast_only else region.river_swot:
        sw = SwotAdapter(store)
        for r in rs["reaches"]:
            last = store.latest(SWOT, r["id"], "width")
            if last and last[1] >= now - 12 * 3600:
                continue
            attempt(f"swot {r['id']}", lambda r=r, sw=sw: sw.fetch(r["id"], iso(now - 60 * 86_400), iso(now)))
    # Water temperature seen from orbit (overlays.toml [satellite_temperature]):
    # each new pass's water pixels, kept beside the store.
    if not args.forecast_only and ocfg.get("satellite_temperature"):
        from fdmr_adapters.ecostress import EcostressAdapter

        sat = ocfg["satellite_temperature"]
        keep = sat.get("days", 14) * 86_400
        attempt("ecostress", lambda: EcostressAdapter(Path(args.store).parent / sat["cache"]).fetch(
            tuple(sat["box"]), int(now - keep), int(now), keep))
    # The map's stations from other agencies than CDEC (overlays.toml `source`).
    from fdmr_adapters import erddap as _erddap
    from fdmr_adapters.ndbc import NdbcAdapter as _NdbcO
    from fdmr_adapters.noaa_tides import NoaaTidesAdapter as _Tides

    for s in ocfg.get("stations", []):
        src = s.get("source", CDEC)
        if src == "ndbc":
            attempt(f"ndbc {s['site']}", lambda s=s: _NdbcO(store).fetch_met(s["site"]) if s.get("kind") == "weather" else _NdbcO(store).fetch(s["site"]))
        elif src == "noaa-tides":
            from fdmr_adapters.noaa_tides import OBSERVED as _OBS

            for prod in s.get("products", ["water_temperature"]):
                back = (35 if prod == "water_level" else 21) * 86_400  # 30 days of tide residual
                cov = store.coverage(src, s["site"], _OBS[prod][0][1])
                f0 = max(now - back, cov[1] + 1) if cov and cov[0] <= now - back + 86_400 else now - back
                attempt(f"noaa {s['site']} {prod}", lambda s=s, f0=f0, prod=prod: _Tides(store).fetch_observed(s["site"], prod, int(f0), int(now)))
            if "water_level" in s.get("products", []):  # its predictions, for the tide residual
                cov = store.coverage(src, s["site"], TIDE)
                if not (cov and cov[0] <= now - 35 * 86_400 and cov[1] >= now + 86_400):
                    f0 = now - 36 * 86_400 if not cov or cov[0] > now - 35 * 86_400 else cov[1] - 86_400
                    attempt(f"noaa {s['site']} {TIDE}", lambda s=s, f0=f0: _Tides(store).fetch_predictions(s["site"], int(f0), int(now + 3 * 86_400)))
        elif src == SOURCE:
            for p in s.get("measures", ["water_temperature", "specific_conductance"]):
                cov = store.coverage(SOURCE, s["site"], p)
                f0 = max(now - 21 * 86_400, cov[1]) if cov else now - 21 * 86_400
                attempt(f"{s['site']} {p}", lambda s=s, p=p, f0=f0: usgs.fetch_values(s["site"], p, f"{iso(f0)}/{iso(now)}"))
        elif src.startswith("erddap:"):
            cov = store.coverage(src, s["site"], "water_temperature")
            f0 = max(now - 21 * 86_400, cov[1] + 1) if cov else now - 21 * 86_400
            attempt(f"erddap {s['site']}", lambda s=s, f0=f0: _erddap.ErddapAdapter(store, s["server"]).fetch(s["site"], iso(f0), ["water_temperature", "salinity"]))
    for dur, group, shown in overlays.fetch_groups(ocfg):
        ends = {}
        for site in group:
            covs = [c for c in (store.coverage(CDEC, site, p) for p in shown) if c]
            if covs:
                ends[site] = max(c[1] for c in covs)
        held, new = [s for s in group if s in ends], [s for s in group if s not in ends]
        if held:
            f0 = max(start, min(ends[s] for s in held) - 3600)
            attempt(f"cdec {len(held)} stations ({dur}) from {iso(f0)}", lambda h=held, f0=f0, d=dur, sh=shown: sum(cdec.fetch_many(h, sh, int(f0), int(now), d).values()))
        if new:
            attempt(f"cdec {len(new)} new stations ({dur})", lambda n=new, d=dur, sh=shown: sum(cdec.fetch_many(n, sh, int(start), int(now), d).values()))
    # The land's surface temperature along the rivers and at their weather
    # zones' airports, from orbit (region.toml [river_temperature.land]).
    from . import land as _land

    lplaces = [] if args.forecast_only else _land.places(Path(args.region))
    if lplaces:
        from fdmr_adapters.ecostress import EcostressAdapter, fetch_land

        lkeep = int(_land.load(Path(args.region))[0].get("days", 14)) * 86_400
        attempt("ecostress land", lambda: fetch_land(EcostressAdapter(Path(args.store).parent / "ecostress"), store, lplaces,
                                                     _land.box(lplaces), int(now - lkeep), int(now)))
    # How wet the land is where rain runs off into the water (region.toml
    # [soil_moisture]), from NASA SMAP.
    from . import soil as _soil

    sareas = [] if args.forecast_only else _soil.areas(Path(args.region))
    if sareas:
        from fdmr_adapters.smap import SmapAdapter

        attempt("smap soil moisture", lambda: SmapAdapter(store).fetch(sareas, int(now - 14 * 86_400), int(now)))
        # The water reaching the same ground: rain (radar and gauges) and snowmelt.
        from fdmr_adapters.nws_precip import NwsPrecipAdapter
        from fdmr_adapters.snodas import SnodasAdapter

        attempt("nws precipitation", lambda: NwsPrecipAdapter(store).fetch(sareas, int(now - 14 * 86_400), int(now)))
        attempt("snodas snowmelt", lambda: SnodasAdapter(store).fetch(sareas, int(now - 14 * 86_400), int(now)))
        # The water the land gives back to the air, measured (OpenET), where
        # the region asks for it ([soil_moisture] openet).
        from fdmr_adapters.openet import KEY_FILE, OpenEtAdapter

        for name in (_soil.load(Path(args.region)) or {}).get("openet", []) if KEY_FILE.exists() else []:
            area = next((a for a in sareas if a["site"] == f"area:{name}"), None)
            for sample in _soil.openet_samples(area) if area else []:
                attempt(f"openet {sample['site']}", lambda sample=sample: OpenEtAdapter(store).fetch(sample, int(now - 60 * 86_400), int(now)))
    # Where the water is, from radar passes (overlays.toml [water_extent]).
    we = ocfg.get("water_extent")
    if not args.forecast_only and we:
        from fdmr_adapters.areas import extent as _extent
        from fdmr_adapters.opera import OperaAdapter

        from . import water_extent as _we

        wbox = _extent([{"box": a["box"]} for a in we["areas"]], margin=0.0)
        attempt("opera water maps", lambda: OperaAdapter(Path(args.store).parent / we.get("cache", "opera")).fetch(
            wbox, _we.windows(we, int(now))))
    # The ocean's state at the model's edge (overlays.toml [ocean_state]).
    ostate = ocfg.get("ocean_state", {})
    if ostate.get("enso"):
        from fdmr_adapters.cpc import CpcAdapter

        attempt("cpc enso indices", lambda: CpcAdapter(store).fetch())
    if ostate.get("sea_level"):
        sl = ostate["sea_level"]
        cov = store.coverage(_erddap.source(sl["server"]), sl["site"], "sea_level_anomaly")
        f0 = cov[1] + 1 if cov else now - 40 * 86_400
        # Asked ten days at a time: the server refuses a longer window over a box.
        attempt("sea level anomaly", lambda sl=sl, f0=f0: _erddap.ErddapAdapter(store, sl["server"]).fetch_grid_mean(
            sl["dataset"], sl["variable"], tuple(sl["box"]), iso(max(f0, now - 10 * 86_400)), sl["site"], "sea_level_anomaly", "m")
            + (_erddap.ErddapAdapter(store, sl["server"]).fetch_grid_mean(
                sl["dataset"], sl["variable"], tuple(sl["box"]), iso(f0), sl["site"], "sea_level_anomaly", "m", until_iso=iso(now - 10 * 86_400))
               if f0 < now - 10 * 86_400 else 0))
    # DWR's daily reservoir and snow reports, for the dams that show them.
    res = [s["site"] for s in ocfg.get("structures", []) if s.get("storage_report")]
    if not args.forecast_only and (res or any(s.get("snow") for s in ocfg.get("structures", []))):
        from fdmr_adapters.cdec_reports import CdecReports

        reports = CdecReports(store)
        if res:
            attempt("cdec reservoir report", lambda: reports.fetch_storage(res, int(now)))
        attempt("cdec snow reports", lambda: reports.fetch_snow(int(now)))
    # Feeds of other model kinds' flow sources, such as the Delta's DSM2 inputs.
    for f in [] if args.forecast_only else delta.feeds(Path(args.region)):
        src, site, p = f["source"], f["site"], f["parameter"]
        cov = store.coverage(src, site, p)
        if cov and cov[0] <= start + 2 * 86_400 and cov[1] >= now - max(args.fresh * 60, 2 * 3600):
            print(f"{site} {p:31s} held    {f.get('name', f.get('path', ''))}", flush=True)
            continue
        from_t = max(start, cov[1] + 1) if cov and cov[0] <= start + 2 * 86_400 else start
        if src == SOURCE and f.get("duration") == "D":
            attempt(f"{site} {p} (daily)", lambda site=site, p=p, f0=from_t: usgs.fetch_daily(site, p, f"{iso(f0 - 86_400)}/{iso(now)}"))
        elif src == SOURCE:
            # A parameter may name one of the gauge's sensors after it ("turbidity_fnu_median").
            from fdmr_adapters.usgs import PARAMETERS as _UP

            base, _, sensor = (p, "", "") if p in _UP else p.rpartition("_")
            attempt(f"{site} {p}", lambda site=site, b=base, sn=sensor or None, f0=from_t: usgs.fetch_values(site, b, f"{iso(f0)}/{iso(now)}", sensor=sn))
        elif src == CDEC:
            dur = f.get("duration", "H")
            attempt(f"cdec {site} {p}", lambda site=site, p=p, f0=from_t, dur=dur: cdec.fetch_values(site, p, int(f0), int(now), duration=dur))
    # Weather, for the Delta's water temperature. NOAA's archive comes a year's
    # file per station and runs days behind, so it is fetched once a day; the
    # Weather Service's observations (its last week) and forecast every run.
    tcfg = (delta.load(Path(args.region)) or {}).get("temperature")
    rt = region.river_temperature
    # The Delta's weather, and each river weather zone's (region.toml [river_temperature]).
    for tcfg in [c for c in [tcfg, *rt.get("weather", [])] if c]:
        wx = NoaaWeatherAdapter(store)
        for s in [] if args.forecast_only else tcfg["stations"]:
            if "ndbc" in s:  # an NDBC weather station: its file holds the last 45 days
                from fdmr_adapters.ndbc import NdbcAdapter as _Ndbc

                attempt(f"ndbc {s['ndbc']} weather", lambda s=s: _Ndbc(store).fetch_met(s["ndbc"]))
                continue
            cov = store.coverage(GHCNH, s["ghcnh"], "air_temperature")
            fetched = store.latest(GHCNH, s["ghcnh"], "air_temperature")
            if cov and cov[0] <= start + 2 * 86_400 and fetched and fetched[1] >= now - 20 * 3600:
                print(f"ghcnh {s['ghcnh']} {'':25s} held    {s['name']}", flush=True)
            else:
                f0 = max(start, cov[1] - 86_400) if cov and cov[0] <= start + 2 * 86_400 else start
                attempt(f"ghcnh {s['ghcnh']}", lambda s=s, f0=f0, wx=wx: wx.fetch_archive(s["ghcnh"], int(f0), int(now)))
            attempt(f"nws {s['nws']} observations", lambda s=s, wx=wx: wx.fetch_observations(s["nws"], int(now - 7 * 86_400)))
        if "forecast" in tcfg:
            fc = tcfg["forecast"]
            attempt(f"nws forecast {fc['office']}/{fc['x']},{fc['y']}", lambda wx=wx, fc=fc: wx.fetch_forecast(fc["office"], fc["x"], fc["y"])[1])
    # The outflow a mesh reach's extension is fitted to (bay.toml [extension]):
    # a long record, from where the store's copy ends.
    from . import bay

    ext = (bay.load(Path(args.region)) or {}).get("extension")
    if ext and not args.forecast_only and ext["outflow"]["source"] == CDEC:
        o = ext["outflow"]
        back = now - (ext["fit_days"] + 10) * 86_400
        cov = store.coverage(CDEC, o["site"], o["parameter"])
        f0 = max(back, cov[1] + 1) if cov and cov[0] <= back + 2 * 86_400 else back
        if cov and cov[1] >= now - 86_400:
            print(f"cdec {o['site']} {o['parameter']:26s} held", flush=True)
        else:
            attempt(f"cdec {o['site']} {o['parameter']}", lambda o=o, f0=f0: cdec.fetch_values(o["site"], o["parameter"], int(f0), int(now), duration=o.get("duration", "D")))
    # The stations the Bay's salinity and temperature are corrected by (bay.toml
    # [quality]); CDEC's come with the overlays. A USGS bay station has an upper
    # and a lower sensor, each its own series (`<parameter>_<sensor>`).
    bcfg = bay.load(Path(args.region)) or {}
    qcfg = bcfg.get("quality")
    from fdmr_adapters.ndbc import SOURCE as NDBC
    from fdmr_adapters.ndbc import NdbcAdapter
    from fdmr_adapters.noaa_tides import SOURCE as TIDES

    for st in [] if (args.forecast_only or not qcfg) else qcfg["stations"]:
        if st["source"].startswith("erddap:"):
            from fdmr_adapters import erddap as _erddap_q

            cov = store.coverage(st["source"], st["site"], "water_temperature")
            f0 = max(now - 21 * 86_400, cov[1] + 1) if cov else now - 21 * 86_400
            attempt(f"erddap {st['site']}", lambda st=st, f0=f0: _erddap_q.ErddapAdapter(store, st["server"]).fetch(st["site"], iso(f0), ["water_temperature", "salinity"]))
            continue
        if st["source"] == NDBC:  # its file holds the last 45 days
            if st.get("file") == "standard":  # a buoy: sea temperature in its standard file
                attempt(f"ndbc {st['site']}", lambda s=st["site"]: NdbcAdapter(store).fetch_met(s))
            else:
                attempt(f"ndbc {st['site']}", lambda s=st["site"]: NdbcAdapter(store).fetch(s))
            continue
        if st["source"] == TIDES:
            cov = store.coverage(TIDES, st["site"], "water_temperature")
            f0 = max(now - 21 * 86_400, cov[1] + 1) if cov else now - 21 * 86_400
            attempt(f"noaa {st['site']} water_temperature", lambda s=st["site"], f0=f0: NoaaTidesAdapter(store).fetch_water_temperature(s, int(f0), int(now)))
            continue
        if st["source"] != SOURCE:
            continue
        for p in [*st.get("conductance", []), *st.get("temperature", [])]:
            base, _, sensor = p.rpartition("_") if p.endswith(("_upper", "_lower")) else (p, "", "")
            cov = store.coverage(SOURCE, st["site"], p)
            back = now - 21 * 86_400
            if cov and cov[1] >= now - max(args.fresh * 60, 2 * 3600):
                print(f"{st['site']} {p:31s} held    {st['name']}", flush=True)
                continue
            f0 = max(back, cov[1] + 1) if cov and cov[0] <= back + 2 * 86_400 else back
            attempt(f"{st['site']} {p}", lambda s=st["site"], b=base, sn=sensor or None, f0=f0: usgs.fetch_values(s, b, f"{iso(f0)}/{iso(now)}", sensor=sn))
    # Measured outflows the network's outflow is checked against (bay.toml
    # [[outflow_checks]]): USGS's daily values.
    for c in [] if (args.forecast_only or not bcfg) else bcfg.get("outflow_checks", []):
        if c["source"] == SOURCE and c.get("duration") == "D":
            cov = store.coverage(SOURCE, c["site"], c["parameter"])
            f0 = max(start, cov[1] - 3 * 86_400) if cov else start
            attempt(f"{c['site']} {c['parameter']} (daily)", lambda c=c, f0=f0: usgs.fetch_daily(c["site"], c["parameter"], f"{iso(f0)}/{iso(now)}"))
    # The Delta's daily island water use (dsm2.toml [depletion]): the months the
    # Delta's runs reach back over, each fetched until it is complete, and the
    # month in progress (and the one before, still being revised) once a day.
    dcfg = (delta.load(Path(args.region)) or {}).get("depletion")
    if dcfg and dcfg["source"] == USBR and not args.forecast_only:
        cvo = UsbrCvoAdapter(store)
        back = now - ((delta.load(Path(args.region)) or {}).get("days_back", 28) + 2) * 86_400
        last = store.latest(USBR, dcfg["site"], dcfg["parameter"])
        fetched_lately = last is not None and last[1] >= now - 20 * 3600
        for y, m in months(back, now):
            m0, m1 = month_span(y, m)
            got = store.values(USBR, dcfg["site"], dcfg["parameter"], m0, m1 + 1)
            recent = m1 > now - 40 * 86_400  # still being revised
            if (recent and fetched_lately) or (not recent and len(got) >= (m1 - m0) // 86_400 + 1):
                print(f"usbr-cvo {y}-{m:02d} {dcfg['parameter']:24s} held", flush=True)
                continue
            attempt(f"usbr-cvo {y}-{m:02d}", lambda y=y, m=m: cvo.fetch_month(y, m, int(now)))
    # River forecasts: one file per forecast group holds all its points.
    groups: dict[str, list[str]] = {}
    for g in region.gauges:
        if g.forecast and g.forecast.get("source") == "cnrfc":
            groups.setdefault(g.forecast["group"], []).append(g.forecast["point"])
        for key in ("inflow", "release_forecast"):  # a release rule's forecasts (fdmr_runner.releases)
            fc = (g.release_rule or {}).get(key)
            if fc and fc["point"] not in groups.setdefault(fc["group"], []):
                groups[fc["group"]].append(fc["point"])
    # The Corps' daily flood-control report, for the release rules' reservoirs.
    spk = {g.release_rule["reservoir"]: g.release_rule["report"] for g in region.gauges if g.release_rule and "report" in g.release_rule}
    if spk and not args.forecast_only:
        from fdmr_adapters.usace_spk import UsaceSpkAdapter

        attempt("usace flood-control report", lambda: UsaceSpkAdapter(store).fetch(spk))
    for fc in delta.forecasts(Path(args.region)):
        if fc["source"] == "cnrfc" and fc["point"] not in groups.setdefault(fc["group"], []):
            groups[fc["group"]].append(fc["point"])
    # Tide predictions: kept a month back and a fortnight ahead.
    tides = NoaaTidesAdapter(store)
    for fc in [] if args.forecast_only else delta.forecasts(Path(args.region)):
        if fc["source"] != "noaa-tides":
            continue
        cov = store.coverage("noaa-tides", fc["station"], TIDE)
        if cov and cov[0] <= now - 35 * 86_400 and cov[1] >= now + 15 * 86_400:
            print(f"noaa {fc['station']} {TIDE:26s} held", flush=True)
            continue
        f0 = now - 40 * 86_400 if not cov or cov[0] > now - 35 * 86_400 else cov[1] - 86_400
        attempt(f"noaa {fc['station']} {TIDE}", lambda st=fc["station"], f0=f0: tides.fetch_predictions(st, int(f0), int(now + 16 * 86_400)))
    cnrfc = CnrfcAdapter(store, archive=Path(args.store).parent / "cnrfc-issues")  # kept for hindcasts (#9)
    def cnrfc_group(group: str, points: list[str]) -> int:
        issue, n = cnrfc.fetch(group, points)
        print(f"cnrfc {group:34s} issue {issue}" + ("" if n else " (held)"), flush=True)
        return n

    for group, points in groups.items():
        attempt(f"cnrfc {group}", lambda g=group, p=points: cnrfc_group(g, p))
    # Stations read from Rijkswaterstaat (region.toml [[stations]], source
    # "rws"; fdmr_adapters.rws): each quantity from where the store's record
    # ends, and the agency's own forecasts afresh.
    live = [s for s in region.stations if s["source"] == "rws"] if not args.forecast_only else []
    # A river gauge of Rijkswaterstaat's is read the same way: its discharge, and its forecast if it has one.
    live = live + [{"source": g.source, "site": g.site, "quantities": [g.parameter, "water_level"],
                    "forecasts": [g.parameter] if g.forecast and g.forecast.get("source") == g.source else []}
                   for g in region.gauges if g.source == "rws" and not args.forecast_only
                   and not any(s.get("site") == g.site and g.parameter in s.get("quantities", []) for s in live)]
    if live:
        from fdmr_adapters.rws import SOURCE as RWS, RwsAdapter

        rws = RwsAdapter(store)
        for st in live:
            if st.get("source") != RWS:
                continue
            for q in st.get("quantities", []):
                cov = store.coverage(RWS, st["site"], q)
                f0 = max(start, cov[1] + 1) if cov and cov[0] <= start + 2 * 86_400 else start
                attempt(f"rws {st['site']} {q}", lambda st=st, q=q, f0=f0: rws.fetch(st["site"], q, int(f0), int(now),
                                                                      depth_cm=st.get("depth_cm") if q in st.get("depth_for", ["chloride", "water_temperature"]) else None))
            for q in st.get("forecasts", []):
                attempt(f"rws {st['site']} {q} forecast", lambda st=st, q=q: rws.fetch(st["site"], q, int(now) - 3 * 3600, int(now) + 3 * 86_400, forecast=True))
    # Every host's queue, side by side; each host's own requests in turn.
    t_fetch = time.time()
    with ThreadPoolExecutor(max_workers=max(1, len(queues))) as pool:
        for f in [pool.submit(lambda jobs=jobs: [job() for job in jobs]) for jobs in queues.values()]:
            f.result()
    print("by host: " + ", ".join(f"{h} {len(j)}" for h, j in sorted(queues.items(), key=lambda kv: -len(kv[1])))
          + f"; {time.time() - t_fetch:.0f} s", flush=True)
    tmp = avail_file.with_name(avail_file.name + ".tmp")
    tmp.write_text(json.dumps(avail), encoding="utf-8")
    tmp.replace(avail_file)
    store.close()
    print(f"done: {len(region.gauges)} gauges, {attempts} fetches, {failures} failures")
    # The run's own account, published with the next cycle (index.json
    # `fetch_status`): a failed source stays visible by name without one
    # network blip marking the whole cycle failed. Only a run where every
    # fetch failed is a failure.
    status = {"finished": int(time.time()), "attempts": attempts, "failures": failures, "failed": failed}
    Path(args.store).parent.joinpath(f"fetch-status-{region.id}.json").write_text(json.dumps(status), encoding="utf-8")
    return 1 if attempts and failures == attempts else 0


if __name__ == "__main__":
    sys.exit(main())
