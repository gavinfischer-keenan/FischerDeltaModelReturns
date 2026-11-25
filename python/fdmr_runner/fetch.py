"""Fetch a region's gauge data into the local store.

    python -m fdmr_runner.fetch REGION_DIR STORE_FILE [--days 365]

For each gauge it asks which series the gauge records, then fetches those for
the period, and every channel measurement on record. Series the store already
covers are skipped, so a rerun only fills gaps. One gauge's failure does not
stop the rest.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

from fdmr_adapters.usgs import PARAMETERS, SOURCE, UsgsAdapter
from fdmr_store import Store

from .region import load_region


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("--days", type=int, default=365, help="how far back to fetch (default 365)")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between requests, to be polite")
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

    for g in region.gauges:
        try:
            have = usgs.available(g.site)
        except Exception as e:
            failures += 1
            print(f"{g.site} series list FAILED {e}", flush=True)
            continue
        time.sleep(args.pause)
        for p in (p for p in PARAMETERS if p in have):
            cov = store.coverage(SOURCE, g.site, p)
            # Already held from (about) the start of the period to the last day: skip.
            if cov and cov[0] <= start + 2 * 86_400 and cov[1] >= now - 86_400:
                print(f"{g.site} {p:31s} held    {g.name}", flush=True)
                continue
            from_t = max(start, cov[1]) if cov and cov[0] <= start + 2 * 86_400 else start
            attempt(f"{g.site} {p}", lambda p=p, f=from_t: usgs.fetch_values(g.site, p, f"{iso(f)}/{iso(now)}"))
        attempt(f"{g.site} channel_measurements", lambda: usgs.fetch_channel_measurements(g.site))
    store.close()
    print(f"done: {len(region.gauges)} gauges, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
