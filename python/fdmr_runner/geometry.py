"""Build a region's river geometry from the national river network, once.

    python -m fdmr_runner.geometry REGION_DIR [--reach river] [--extra-km 2]

Writes REGION_DIR/<reach>.json: the main stem's centreline from the release
gauge to just past the last main gauge, distance along it ("chainage", m),
each main gauge's chainage, and where each tributary joins. A reach whose
region.toml entry gives a `mouth` (lon, lat) runs on to the main stem's point
nearest it instead, where it hands on to the next reach (`model_length_m`).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

from fdmr_adapters.nldi import NldiAdapter
from fdmr_plugins.river_routing.centreline import chain

from .region import load_region


MAX_SNAP_M = 300  # a station placed by its coordinates must lie this close to the river


def position(nldi: NldiAdapter, g) -> "object":
    """Where a gauge sits on the national river network: its USGS site's
    position, or its own coordinates snapped to the nearest flowline (checked)."""
    if g.place_site:
        return nldi.site_position(g.place_site)
    if g.lon is None or g.lat is None:
        raise ValueError(f"gauge {g.site} has no USGS site and no coordinates")
    p = nldi.point_position(g.lon, g.lat, g.site)
    k = math.cos(math.radians(g.lat))
    off = math.hypot((p.lon - g.lon) * k, p.lat - g.lat) * 111_195
    if off > MAX_SNAP_M:
        raise ValueError(f"gauge {g.site} is {off:.0f} m from any river: check its coordinates")
    return p


def build(region_dir: Path, reach: str, extra_km: float, nldi: NldiAdapter, pause: float = 1.0) -> dict:
    region = load_region(region_dir)
    cfg = next((r for r in region.reaches if r["id"] == reach), {})
    gauges = region.gauges_for(reach)
    release = next(g for g in gauges if g.role == "release")
    mains = [g for g in gauges if g.role == "main"]

    # The reach starts at its dam where one is given (`dam`, lon and lat), else at its release gauge.
    rel = position(nldi, release)
    top = nldi.point_position(cfg["dam"][0], cfg["dam"][1], "dam") if cfg.get("dam") else rel
    flowlines = nldi.downstream_from(top.comid, distance_km=1500)
    line = chain([(f.comid, f.coords) for f in flowlines], start_comid=top.comid)
    main_ids = set(line.flowline_start)
    start = line.position_of(top.comid, top.measure)

    positions: dict[str, float] = {}
    if cfg.get("dam"):
        if rel.comid not in main_ids:
            raise ValueError(f"release gauge {release.site} is not on the river below the dam")
        positions[release.site] = line.position_of(rel.comid, rel.measure) - start
    for g in mains:
        time.sleep(pause)
        p = position(nldi, g)
        if p.comid in main_ids:
            positions[g.site] = line.position_of(p.comid, p.measure) - start
            continue
        # NHDPlus files some gauges on a side flowline (a split channel): the
        # nearest point of the main stem serves, if close.
        k = math.cos(math.radians(p.lat))
        near = min(range(len(line.coords)), key=lambda i: math.hypot((line.coords[i][0] - p.lon) * k, line.coords[i][1] - p.lat))
        off = math.hypot((line.coords[near][0] - p.lon) * k, line.coords[near][1] - p.lat) * 111_195
        if off > MAX_SNAP_M or line.chainage[near] < start:
            raise ValueError(f"main gauge {g.site} is not on the main stem downstream of {release.site} ({off:.0f} m off)")
        positions[g.site] = line.chainage[near] - start

    joins: dict[str, float] = {}
    for g in gauges:
        if g.role != "tributary":
            continue
        time.sleep(pause)
        path = nldi.downstream_mainstem(g.place_site, distance_km=400) if g.place_site else nldi.downstream_from(position(nldi, g).comid, 400)
        # The first flowline downstream of the tributary gauge that is on the main
        # stem is where it joins: its upstream end.
        on_main = [f.comid for f in path if f.comid in main_ids]
        if not on_main:
            raise ValueError(f"tributary {g.site} does not reach the main stem")
        joins[g.site] = min(line.flowline_start[c] for c in on_main) - start

    end = max([0.0, *positions.values()]) + extra_km * 1000
    mouth = cfg.get("mouth")
    if cfg.get("joins"):
        # Where it meets the reach it joins: the first point of its main stem
        # within 150 m of that reach's centreline.
        other = json.loads((Path(region_dir) / f"{cfg['joins']}.json").read_text(encoding="utf-8"))["coords"]
        kk = math.cos(math.radians(other[0][1]))
        for xy, s in zip(line.coords, line.chainage):
            if s - start > 500 and min(math.hypot((xy[0] - o[0]) * kk, xy[1] - o[1]) for o in other[:: 1]) * 111_195 < 150:
                mouth = list(xy)
                break
        else:
            raise ValueError(f"{reach} does not reach {cfg['joins']}")
    if mouth:
        k = math.cos(math.radians(mouth[1]))
        near = min(range(len(line.coords)), key=lambda i: math.hypot((line.coords[i][0] - mouth[0]) * k, line.coords[i][1] - mouth[1]))
        end = line.chainage[near] - start
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
        **({"dam": cfg["dam"]} if cfg.get("dam") else {}),
        "tributary_joins_m": {k: round(v, 1) for k, v in sorted(joins.items(), key=lambda kv: kv[1])},
        **({"model_length_m": chainage[-1]} if mouth else {}),
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
