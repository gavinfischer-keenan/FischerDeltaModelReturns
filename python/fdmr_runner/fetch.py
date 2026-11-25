"""Fetch a region's gauge data into the local store.

    python -m fdmr_runner.fetch REGION_DIR STORE_FILE [--period P365D] [--parameters discharge,...]

Fetches each gauge's continuous values for the period, and every channel
measurement on record. Safe to rerun: values are replaced, not duplicated.
"""

from __future__ import annotations

import argparse
import sys
import time

from fdmr_adapters.usgs import PARAMETERS, UsgsAdapter
from fdmr_store import Store

from .region import load_region


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("--period", default="P365D", help="ISO 8601 duration or interval (default P365D)")
    ap.add_argument("--parameters", default=",".join(PARAMETERS), help="comma-separated parameter names")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between requests, to be polite")
    args = ap.parse_args(argv)

    region = load_region(args.region)
    store = Store(args.store)
    usgs = UsgsAdapter(store)
    params = [p for p in args.parameters.split(",") if p]
    failures = 0
    for g in region.gauges:
        for p in params:
            try:
                n = usgs.fetch_values(g.site, p, args.period)
                print(f"{g.site} {p:22s} {n:7d}  {g.name}", flush=True)
            except Exception as e:  # one gauge's outage must not stop the rest
                failures += 1
                print(f"{g.site} {p:22s} FAILED {e}", flush=True)
            time.sleep(args.pause)
        try:
            n = usgs.fetch_channel_measurements(g.site)
            print(f"{g.site} {'channel_measurements':22s} {n:7d}", flush=True)
        except Exception as e:
            failures += 1
            print(f"{g.site} channel_measurements   FAILED {e}", flush=True)
        time.sleep(args.pause)
    store.close()
    print(f"done: {len(region.gauges)} gauges, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
