"""Build a region's river geometry from the national river network, once.

    python -m fdmr_runner.geometry REGION_DIR [--reach river] [--extra-km 2]

Writes REGION_DIR/<reach>.json: the main stem's centreline from the release
gauge to just past the last main gauge, distance along it ("chainage", m),
each main gauge's chainage, and where each tributary joins.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from fdmr_adapters.nldi import NldiAdapter
from fdmr_plugins.river_routing.centreline import chain

from .region import load_region


def build(region_dir: Path, reach: str, extra_km: float, nldi: NldiAdapter, pause: float = 1.0) -> dict:
    region = load_region(region_dir)
    gauges = region.gauges_for(reach)
    release = next(g for g in gauges if g.role == "release")
    mains = [g for g in gauges if g.role == "main"]

    rel = nldi.site_position(release.site)
    flowlines = nldi.downstream_mainstem(release.site, distance_km=1500)
    line = chain([(f.comid, f.coords) for f in flowlines], start_comid=rel.comid)
    main_ids = set(line.flowline_start)
    start = line.position_of(rel.comid, rel.measure)

    positions: dict[str, float] = {}
    for g in mains:
        time.sleep(pause)
        p = nldi.site_position(g.site)
        if p.comid not in main_ids:
            raise ValueError(f"main gauge {g.site} is not on the main stem downstream of {release.site}")
        positions[g.site] = line.position_of(p.comid, p.measure) - start

    joins: dict[str, float] = {}
    for g in gauges:
        if g.role != "tributary":
            continue
        time.sleep(pause)
        path = nldi.downstream_mainstem(g.site, distance_km=400)
        # The first flowline downstream of the tributary gauge that is on the main
        # stem is where it joins: its upstream end.
        on_main = [f.comid for f in path if f.comid in main_ids]
        if not on_main:
            raise ValueError(f"tributary {g.site} does not reach the main stem")
        joins[g.site] = min(line.flowline_start[c] for c in on_main) - start

    end = max(positions.values()) + extra_km * 1000
    coords, chainage = [], []
    for xy, s in zip(line.coords, line.chainage):
        s -= start
        if 0 <= s <= end:
            coords.append([round(xy[0], 6), round(xy[1], 6)])
            chainage.append(round(s, 1))
    return {
        "reach": reach,
        "source": "USGS NLDI, NHDPlus main stem",
        "built": time.strftime("%Y-%m-%d", time.gmtime()),
        "length_m": chainage[-1],
        "coords": coords,
        "chainage_m": chainage,
        "gauges_m": {k: round(v, 1) for k, v in sorted(positions.items(), key=lambda kv: kv[1])},
        "tributary_joins_m": {k: round(v, 1) for k, v in sorted(joins.items(), key=lambda kv: kv[1])},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("--reach", default="river")
    ap.add_argument("--extra-km", type=float, default=2.0)
    args = ap.parse_args(argv)
    out = build(Path(args.region), args.reach, args.extra_km, NldiAdapter())
    path = Path(args.region) / f"{args.reach}.json"
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"{path}: {out['length_m'] / 1000:.1f} km, {len(out['coords'])} points")
    for site, s in out["gauges_m"].items():
        print(f"  main gauge {site} at {s / 1000:7.1f} km")
    for site, s in out["tributary_joins_m"].items():
        print(f"  tributary {site} joins at {s / 1000:7.1f} km")
    return 0


if __name__ == "__main__":
    sys.exit(main())
