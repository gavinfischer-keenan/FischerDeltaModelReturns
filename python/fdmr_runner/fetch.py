"""Fetch a region's gauge data into the local store.

    python -m fdmr_runner.fetch REGION_DIR STORE_FILE [--days 365]

For each gauge it asks which series the gauge records, then fetches those for
the period, and every channel measurement on record. Series the store already
holds up to the last `--fresh` minutes are skipped, so a rerun only fills gaps
and the hourly run fetches only the latest hour or so. Then it fetches the
latest river forecast for every gauge that names a forecast point, if that
issue is not already held. One gauge's failure does not stop the rest.

    --forecast-only     skip the gauges and fetch only the forecasts
    --no-measurements   skip channel measurements (they change rarely; the
                        nightly run fetches them)
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fdmr_adapters.cdec import SOURCE as CDEC
from fdmr_adapters.cdec import CdecAdapter
from fdmr_adapters.cnrfc import CnrfcAdapter
from fdmr_adapters.noaa_tides import PARAMETER as TIDE
from fdmr_adapters.noaa_tides import NoaaTidesAdapter
from fdmr_adapters.noaa_weather import GHCNH, NoaaWeatherAdapter
from fdmr_adapters.usgs import PARAMETERS, SOURCE, UsgsAdapter
from fdmr_store import Store

from . import delta
from .region import load_region


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
    now = time.time()
    start = now - args.days * 86_400
    failures = 0

    def attempt(label: str, fn) -> None:
        nonlocal failures
        try:
            print(f"{label:40s} {fn():7d}", flush=True)
        except Exception as e:  # one gauge's outage must not stop the rest
            failures += 1
            print(f"{label:40s} FAILED {e}", flush=True)
        time.sleep(args.pause)

    for g in [] if args.forecast_only else region.gauges:
        try:
            have = usgs.available(g.site)
        except Exception as e:
            failures += 1
            print(f"{g.site} series list FAILED {e}", flush=True)
            continue
        time.sleep(args.pause)
        for p in (p for p in PARAMETERS if p in have):
            cov = store.coverage(SOURCE, g.site, p)
            # Already held from (about) the start of the period to a moment ago: skip.
            if cov and cov[0] <= start + 2 * 86_400 and cov[1] >= now - args.fresh * 60:
                print(f"{g.site} {p:31s} held    {g.name}", flush=True)
                continue
            from_t = max(start, cov[1]) if cov and cov[0] <= start + 2 * 86_400 else start
            attempt(f"{g.site} {p}", lambda p=p, f=from_t: usgs.fetch_values(g.site, p, f"{iso(f)}/{iso(now)}"))
        if not args.no_measurements:
            attempt(f"{g.site} channel_measurements", lambda: usgs.fetch_channel_measurements(g.site))
    # Tracer stations (CDEC): hourly readings, from where the store's copy ends.
    cdec = CdecAdapter(store)
    for s in [] if args.forecast_only else region.stations:
        if s.get("source") != CDEC:
            continue
        for p in s.get("measures", []):
            cov = store.coverage(CDEC, s["id"], p)
            if cov and cov[0] <= start + 2 * 86_400 and cov[1] >= now - max(args.fresh * 60, 2 * 3600):
                print(f"{s['id']} {p:31s} held    {s['name']}", flush=True)
                continue
            from_t = max(start, cov[1] + 1) if cov and cov[0] <= start + 2 * 86_400 else start
            attempt(f"cdec {s['id']} {p}", lambda s=s, p=p, f=from_t: cdec.fetch_values(s["id"], p, int(f), int(now)))
    # Stations shown on the map (overlays.toml): every sensor of every station
    # asked in one request per duration. Stations with readings already held
    # are asked from where their newest ends; new ones (and any that have
    # never answered) from the start of the period.
    from . import overlays

    ocfg = (overlays.load(Path(args.region)) or {}) if not args.forecast_only else {}
    shown = list(overlays.SHOWN)
    for dur in sorted({s.get("duration", "H") for s in ocfg.get("stations", [])}):
        group = [s["site"] for s in ocfg["stations"] if s.get("duration", "H") == dur and s.get("source", CDEC) == CDEC]
        ends = {}
        for site in group:
            covs = [c for c in (store.coverage(CDEC, site, p) for p in shown) if c]
            if covs:
                ends[site] = max(c[1] for c in covs)
        held, new = [s for s in group if s in ends], [s for s in group if s not in ends]
        if held:
            f0 = max(start, min(ends[s] for s in held) - 3600)
            attempt(f"cdec {len(held)} stations ({dur}) from {iso(f0)}", lambda h=held, f0=f0, d=dur: sum(cdec.fetch_many(h, shown, int(f0), int(now), d).values()))
        if new:
            attempt(f"cdec {len(new)} new stations ({dur})", lambda n=new, d=dur: sum(cdec.fetch_many(n, shown, int(start), int(now), d).values()))
    # Feeds of other model kinds' flow sources, such as the Delta's DSM2 inputs.
    for f in [] if args.forecast_only else delta.feeds(Path(args.region)):
        src, site, p = f["source"], f["site"], f["parameter"]
        cov = store.coverage(src, site, p)
        if cov and cov[0] <= start + 2 * 86_400 and cov[1] >= now - max(args.fresh * 60, 2 * 3600):
            print(f"{site} {p:31s} held    {f.get('name', f.get('path', ''))}", flush=True)
            continue
        from_t = max(start, cov[1] + 1) if cov and cov[0] <= start + 2 * 86_400 else start
        if src == SOURCE:
            attempt(f"{site} {p}", lambda site=site, p=p, f0=from_t: usgs.fetch_values(site, p, f"{iso(f0)}/{iso(now)}"))
        elif src == CDEC:
            dur = f.get("duration", "H")
            attempt(f"cdec {site} {p}", lambda site=site, p=p, f0=from_t, dur=dur: cdec.fetch_values(site, p, int(f0), int(now), duration=dur))
    # Weather, for the Delta's water temperature. NOAA's archive comes a year's
    # file per station and runs days behind, so it is fetched once a day; the
    # Weather Service's observations (its last week) and forecast every run.
    tcfg = (delta.load(Path(args.region)) or {}).get("temperature")
    if tcfg:
        wx = NoaaWeatherAdapter(store)
        for s in [] if args.forecast_only else tcfg["stations"]:
            cov = store.coverage(GHCNH, s["ghcnh"], "air_temperature")
            fetched = store.latest(GHCNH, s["ghcnh"], "air_temperature")
            if cov and cov[0] <= start + 2 * 86_400 and fetched and fetched[1] >= now - 20 * 3600:
                print(f"ghcnh {s['ghcnh']} {'':25s} held    {s['name']}", flush=True)
            else:
                f0 = max(start, cov[1] - 86_400) if cov and cov[0] <= start + 2 * 86_400 else start
                attempt(f"ghcnh {s['ghcnh']}", lambda s=s, f0=f0: wx.fetch_archive(s["ghcnh"], int(f0), int(now)))
            attempt(f"nws {s['nws']} observations", lambda s=s: wx.fetch_observations(s["nws"], int(now - 7 * 86_400)))
        if "forecast" in tcfg:
            fc = tcfg["forecast"]
            attempt(f"nws forecast {fc['office']}/{fc['x']},{fc['y']}", lambda: wx.fetch_forecast(fc["office"], fc["x"], fc["y"])[1])
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
            attempt(f"cdec {o['site']} {o['parameter']}", lambda: cdec.fetch_values(o["site"], o["parameter"], int(f0), int(now), duration=o.get("duration", "D")))
    # River forecasts: one file per forecast group holds all its points.
    groups: dict[str, list[str]] = {}
    for g in region.gauges:
        if g.forecast and g.forecast.get("source") == "cnrfc":
            groups.setdefault(g.forecast["group"], []).append(g.forecast["point"])
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
    cnrfc = CnrfcAdapter(store)
    for group, points in groups.items():
        try:
            issue, n = cnrfc.fetch(group, points)
            print(f"cnrfc {group:34s} {n:7d} issue {issue}" + ("" if n else " (held)"), flush=True)
        except Exception as e:
            failures += 1
            print(f"cnrfc {group:34s} FAILED {e}", flush=True)
        time.sleep(args.pause)
    store.close()
    print(f"done: {len(region.gauges)} gauges, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
