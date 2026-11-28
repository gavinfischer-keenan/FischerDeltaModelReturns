"""Where each pool's water is (a flooded island, a forebay, a marina's
lagoons), as points spread evenly over it, so the map draws what is in a pool
on its water rather than on a disc that crosses its banks.

A pool (DSM2's reservoir) has a volume and a level but no shape. Its shape
comes from a named map source in the region's DSM2 config (`[pool_water]`):
OpenStreetMap elements (`osm`, under the Open Database Licence) or the NHD
waterbody holding a point (`nhd_at`, public domain). Each is chosen by hand
from what the maps hold there, and its area is checked against DSM2's. Points
lie on a regular grid over the outline (inside: an odd number of edge
crossings), about one per POINT_SPACING_M, at most MAX_POINTS, thinned
evenly. Pools with no mapped outline of their own keep the disc.

Run once, and again when a source changes:
    python -m fdmr_runner.pool_water ../regions/sf-bay-delta
It writes regions/<id>/pool_water.json, which the Delta's publish reads.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import fdmr_geo
from fdmr_adapters import nhd, osm

POINT_SPACING_M = 60.0  # one point per 60 m x 60 m: dots on water, never more than the eye resolves
MAX_POINTS = 1500  # per pool: plenty for the particles a pool holds (8 per km2, worker.ts)
M_PER_DEG = fdmr_geo.M_PER_DEG  # m per degree of latitude (fdmr_geo)


def inside(x: float, y: float, edges: list[list[tuple[float, float]]]) -> bool:
    """Whether (x, y) is inside the area the edges bound: a ray east from it
    crosses them an odd number of times (rings or loose pieces of them)."""
    n = 0
    for line in edges:
        for (x0, y0), (x1, y1) in zip(line, line[1:]):
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                n += 1
    return n % 2 == 1


def points(edges: list[list[tuple[float, float]]], spacing_m: float = POINT_SPACING_M,
           max_points: int = MAX_POINTS) -> tuple[list[tuple[float, float]], float]:
    """Points on a regular grid inside the outline, and the outline's area, m2
    (grid cells inside times a cell's area)."""
    xs = [x for ln in edges for x, _ in ln]
    ys = [y for ln in edges for _, y in ln]
    lat0 = (min(ys) + max(ys)) / 2
    dy = spacing_m / M_PER_DEG
    dx = dy / math.cos(math.radians(lat0))
    pts = []
    y = min(ys) + dy / 2
    while y < max(ys):
        x = min(xs) + dx / 2
        while x < max(xs):
            if inside(x, y, edges):
                pts.append((round(x, 5), round(y, 5)))
            x += dx
        y += dy
    area = len(pts) * spacing_m**2
    if len(pts) > max_points:
        step = len(pts) / max_points
        pts = [pts[int(i * step)] for i in range(max_points)]
    return pts, area


def build(region_dir: Path, osm_get=osm.query, nhd_get=nhd.get_json) -> dict:
    cfg = tomllib.loads((region_dir / "dsm2.toml").read_text(encoding="utf-8"))
    out = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "pools": {}}
    for pool, src in cfg.get("pool_water", {}).items():
        if "osm" in src:
            edges = osm.outlines(list(src["osm"]), get=osm_get)
            source = f"OpenStreetMap {', '.join(src['osm'])}; {osm.ATTRIBUTION}"
        elif "nhd_at" in src:
            attrs, edges = nhd.waterbody_at(src["nhd_at"]["lon"], src["nhd_at"]["lat"], get=nhd_get)
            source = f"NHD Waterbody - Large Scale {attrs.get('permanent_identifier', '')}; {nhd.ATTRIBUTION}"
        else:
            raise ValueError(f"[pool_water] {pool}: give osm or nhd_at")
        pts, area = points(edges)
        out["pools"][pool] = {"source": source, "area_m2": round(area), "points": pts}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    args = ap.parse_args(argv)
    region_dir = Path(args.region)
    out = build(region_dir)
    (region_dir / "pool_water.json").write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
    for pool, p in out["pools"].items():
        print(f"{pool}: {len(p['points'])} points over {p['area_m2'] / 1e6:.2f} km2 of mapped water ({p['source'][:60]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
