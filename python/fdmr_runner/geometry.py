"""Build a region's river geometry from a river network, once: the USGS's
(NLDI) in the United States, OpenStreetMap's or Rijkswaterstaat's elsewhere,
or NHD's named flowlines where NLDI's network does not follow the water (a
reach's `line`: source osm, pdok or nhd).

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

import fdmr_geo
from fdmr_adapters.nldi import NldiAdapter
from fdmr_plugins.river_routing.centreline import chain

from .region import load_region


MAX_SNAP_M = 300  # a station placed by its coordinates must lie this close to the river


# Building a reach's centreline from the national hydrography (NLDI), once,
# when a region is set up (FDMR's choices, generous: a trace past the river's
# end is cut where the reach ends).
TRACE_KM = 1500  # how far downstream a reach's line is traced, km
JOIN_TRACE_KM = 400  # how far downstream a tributary is traced to find where it joins, km
JOIN_AFTER_M = 500  # a tributary joins no nearer its own start than this, m
JOIN_WITHIN_M = 150  # it joins where it comes within this of the reach's centreline, m

def position(nldi: NldiAdapter, g) -> "object":
    """Where a gauge sits on the national river network: its USGS site's
    position, or its own coordinates snapped to the nearest flowline (checked)."""
    if g.place_site:
        return nldi.site_position(g.place_site)
    if g.lon is None or g.lat is None:
        raise ValueError(f"gauge {g.site} has no USGS site and no coordinates")
    p = nldi.point_position(g.lon, g.lat, g.site)
    k = math.cos(math.radians(g.lat))
    off = math.hypot((p.lon - g.lon) * k, p.lat - g.lat) * fdmr_geo.M_PER_DEG
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
    flowlines = nldi.downstream_from(top.comid, distance_km=TRACE_KM)
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
        off = math.hypot((line.coords[near][0] - p.lon) * k, line.coords[near][1] - p.lat) * fdmr_geo.M_PER_DEG
        if off > MAX_SNAP_M or line.chainage[near] < start:
            raise ValueError(f"main gauge {g.site} is not on the main stem downstream of {release.site} ({off:.0f} m off)")
        positions[g.site] = line.chainage[near] - start

    joins: dict[str, float] = {}
    for g in gauges:
        if g.role != "tributary":
            continue
        time.sleep(pause)
        path = nldi.downstream_mainstem(g.place_site, distance_km=JOIN_TRACE_KM) if g.place_site else nldi.downstream_from(position(nldi, g).comid, JOIN_TRACE_KM)
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
            if s - start > JOIN_AFTER_M and min(math.hypot((xy[0] - o[0]) * kk, xy[1] - o[1]) for o in other) * fdmr_geo.M_PER_DEG < JOIN_WITHIN_M:
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


def along(coords: list[tuple[float, float]]) -> list[float]:
    """Distance along a line of (lon, lat) points, m (locally flat Earth, each step)."""
    out = [0.0]
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        k = math.cos(math.radians((y0 + y1) / 2))
        out.append(out[-1] + math.hypot((x1 - x0) * k, y1 - y0) * fdmr_geo.M_PER_DEG)
    return out


def build_from_line(region_dir: Path, reach: str, coords: list[tuple[float, float]], source: str, max_snap_m: float = MAX_SNAP_M) -> dict:
    """A reach's geometry from a river line given whole, upstream to downstream
    (outside the United States, where there is no NLDI): each release and main
    gauge placed at the line's point nearest its coordinates (`lon`, `lat` in
    region.toml), checked to lie within `max_snap_m`; the reach runs from its
    release gauge to the line's end, or to the point nearest its `mouth`."""
    region = load_region(region_dir)
    cfg = next((r for r in region.reaches if r["id"] == reach), {})
    chain_m = along(coords)

    def nearest(lon: float, lat: float) -> tuple[int, float]:
        k = math.cos(math.radians(lat))
        i = min(range(len(coords)), key=lambda j: math.hypot((coords[j][0] - lon) * k, coords[j][1] - lat))
        return i, math.hypot((coords[i][0] - lon) * k, coords[i][1] - lat) * fdmr_geo.M_PER_DEG

    gauges = [g for g in region.gauges_for(reach) if g.role in ("release", "main")]
    at: dict[str, float] = {}
    for g in gauges:
        if g.lon is None or g.lat is None:
            raise ValueError(f"gauge {g.site} has no coordinates")
        i, off = nearest(g.lon, g.lat)
        if off > max_snap_m:
            raise ValueError(f"gauge {g.site} is {off:.0f} m from the line: check its coordinates or the line")
        at[g.site] = chain_m[i]
    release = next(g for g in gauges if g.role == "release")
    start = at[release.site]
    end = chain_m[-1]
    if cfg.get("mouth"):
        end = chain_m[nearest(*cfg["mouth"])[0]]
    if any(v < start for v in at.values()):
        raise ValueError(f"a main gauge of {reach} lies upstream of its release gauge {release.site}")
    out_c, out_s = [], []
    for xy, s in zip(coords, chain_m):
        if start <= s <= end:
            out_c.append([round(xy[0], 6), round(xy[1], 6)])
            out_s.append(round(s - start, 1))
    return {
        "reach": reach, "source": source, "built": time.strftime("%Y-%m-%d", time.gmtime()),
        "length_m": out_s[-1], "coords": out_c, "chainage_m": out_s,
        "gauges_m": {k: round(v - start, 1) for k, v in sorted(at.items(), key=lambda kv: kv[1])},
        "tributary_joins_m": {}, **({"model_length_m": out_s[-1]} if cfg.get("mouth") else {}),
    }


def river_km_check(geo: dict, gauges, markers: list[tuple[float, float, float]], within_m: float = 1000) -> dict:
    """Each gauge's distance from the release gauge along the built line,
    against the agency's own river kilometres there (its nearest marker within
    `within_m`): {site: {line_km, river_km}}, for checking the line."""
    out = {}
    pos = {g.site: (g.lon, g.lat) for g in gauges if g.lon is not None}
    def km_at(lon, lat):
        k = math.cos(math.radians(lat))
        best = min(markers, key=lambda m: math.hypot((m[0] - lon) * k, m[1] - lat), default=None)
        if best is None or math.hypot((best[0] - lon) * k, best[1] - lat) * fdmr_geo.M_PER_DEG > within_m:
            return None
        return best[2]
    first = next(iter(geo["gauges_m"]))
    k0 = km_at(*pos[first]) if first in pos else None
    for site, m in geo["gauges_m"].items():
        k = km_at(*pos[site]) if site in pos else None
        out[site] = {"line_km": round(m / 1000, 1), "river_km": None if k is None or k0 is None else round(k - k0, 1)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("--reach", default="river")
    ap.add_argument("--extra-km", type=float, default=2.0)
    args = ap.parse_args(argv)
    cfg = next((r for r in load_region(args.region).reaches if r["id"] == args.reach), {})
    if cfg.get("line", {}).get("source") == "osm":
        # Outside the United States: the river's line from OpenStreetMap, from the
        # release gauge to the line's `end` (region.toml `line`).
        from fdmr_adapters import osm

        ln = cfg["line"]
        rel = next(g for g in load_region(args.region).gauges_for(args.reach) if g.role == "release")
        coords = osm.line_between(osm.ways(ln["names"], tuple(ln["bbox"])), (rel.lon, rel.lat), tuple(ln["end"]))
        out = build_from_line(Path(args.region), args.reach, coords, f"OpenStreetMap waterways {', '.join(ln['names'])}; {osm.ATTRIBUTION}",
                              max_snap_m=ln.get("max_snap_m", MAX_SNAP_M))
        out["licence"] = "ODbL 1.0: this line is derived from OpenStreetMap"
        if ln.get("km_markers") == "pdok":  # checked against Rijkswaterstaat's river kilometres
            from fdmr_adapters import pdok

            out["river_km_check"] = river_km_check(out, load_region(args.region).gauges_for(args.reach),
                                                   pdok.km_markers(tuple(ln["bbox"])))
    elif cfg.get("line", {}).get("source") == "pdok":
        # In the Netherlands: Rijkswaterstaat's national waterway network, and its
        # river kilometres to check the built line against.
        from fdmr_adapters import pdok

        ln = cfg["line"]
        rel = next(g for g in load_region(args.region).gauges_for(args.reach) if g.role == "release")
        bridges: list = []
        coords = pdok.line_between(pdok.sections(ln["names"]), (rel.lon, rel.lat), tuple(ln["end"]),
                                   join_m=ln.get("join_m", 25.0), bridges=bridges)
        out = build_from_line(Path(args.region), args.reach, coords, f"Rijkswaterstaat NWB Vaarwegen {', '.join(ln['names'])}; {pdok.ATTRIBUTION}",
                              max_snap_m=ln.get("max_snap_m", MAX_SNAP_M))
        if bridges:  # gaps in the network crossed straight (such as a lock channel beside a weir)
            out["bridged_gaps"] = [{"lon": x, "lat": y, "m": d} for x, y, d in bridges]
        out["river_km_check"] = river_km_check(out, load_region(args.region).gauges_for(args.reach),
                                               pdok.km_markers(tuple(ln["bbox"])))
    elif cfg.get("line", {}).get("source") == "nhd":
        # Where NLDI's network does not follow the water (a floodway's low
        # channel): NHD's flowlines by name, joined and walked.
        from fdmr_adapters import nhd

        ln = cfg["line"]
        rel = next(g for g in load_region(args.region).gauges_for(args.reach) if g.role == "release")
        start = tuple(ln.get("start", (rel.lon, rel.lat)))
        bridges = []
        coords = nhd.line_between(nhd.flowlines(ln["names"], tuple(ln["bbox"])), start, tuple(ln["end"]),
                                  join_m=ln.get("join_m", 25.0), bridges=bridges)
        out = build_from_line(Path(args.region), args.reach, coords, f"NHD flowlines {', '.join(ln['names'])}; {nhd.ATTRIBUTION}",
                              max_snap_m=ln.get("max_snap_m", MAX_SNAP_M))
        if bridges:
            out["bridged_gaps"] = [{"lon": x, "lat": y, "m": d} for x, y, d in bridges]
    else:
        out = build(Path(args.region), args.reach, args.extra_km, NldiAdapter())
    path = Path(args.region) / f"{args.reach}.json"
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"{path}: {out['length_m'] / 1000:.1f} km, {len(out['coords'])} points")
    for site, s in out["gauges_m"].items():
        chk = out.get("river_km_check", {}).get(site, {})
        print(f"  main gauge {site} at {s / 1000:7.1f} km" + (f"  (river km: {chk['river_km']})" if chk.get("river_km") is not None else ""))
    for site, s in out["tributary_joins_m"].items():
        print(f"  tributary {site} joins at {s / 1000:7.1f} km")
    return 0


if __name__ == "__main__":
    sys.exit(main())
