"""Publish a region's flow field for the last N days and the next M (docs/format.md).

    python -m fdmr_runner.publish REGION_DIR STORE_FILE OUT_DIR [--days 14] [--ahead 14] [--keep 3]

Writes OUT_DIR/<cycle>/ with index.json, geometry/<reach>.json and
<reach>/<YYYY-MM-DD>.fdf, then points OUT_DIR/current at the new cycle in one
step, so a reader never sees half a cycle. Older cycles beyond the newest
`--keep` are removed; a browser still reading one has had an hour to finish.

After now, each main gauge follows its river forecast (fdmr_runner.forecast);
the dam release, which has no forecast, is held. Every step carries the reach's
certainty: 1 to the end of the solid forecast, then falling to the floor at the
end (specification 8.2).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import fdmr_py
from fdmr_adapters import cnrfc
from fdmr_adapters.usgs import SOURCE, UsgsAdapter
from fdmr_plugins.river_routing import channel, model
from fdmr_plugins.river_routing.centreline import Centreline
from fdmr_plugins.river_routing.wave import regular
from fdmr_store import Store

from . import forecast
from .region import load_region

STEP = 900
DAY = 86_400
BLEND_S = DAY  # the gap between forecast and measurement at now closes over a day


def utc(t: float, fmt: str = "%Y-%m-%dT%H:%MZ") -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime(fmt)


def element_lines(line: Centreline, elements: list[model.Element], max_points: int = 16) -> list[list[list[float]]]:
    """A short polyline per element, for drawing: its ends and up to `max_points` points between."""
    out = []
    for e in elements:
        inside = [i for i, s in enumerate(line.chainage) if e.start < s < e.end]
        if len(inside) > max_points:
            k = len(inside) / max_points
            inside = [inside[int(j * k)] for j in range(max_points)]
        pts = [line.point_at(e.start), *(line.coords[i] for i in inside), line.point_at(e.end)]
        out.append([[round(x, 6), round(y, 6)] for x, y in pts])
    return out


DEFAULT_SLOPE = 3e-4  # a typical lowland river, used only where no gauge pair gives one


def segment_slopes(gauges, chainage, store, datums, t0, now) -> list[float]:
    """Water-surface slope between each pair of consecutive main gauges: the drop
    in (datum + median gauge height) over the distance. Missing or implausible
    slopes take a neighbour's, or a default."""
    def surface(site):
        d = datums.get(site)
        if d is None:
            return None
        gh = [v.value for v in store.values(SOURCE, site, "gage_height", t0, now + STEP) if v.value is not None]
        return d + sorted(gh)[len(gh) // 2] if gh else None
    z = [surface(g.site) for g in gauges]
    s = []
    for (g1, z1), (g2, z2) in zip(zip(gauges, z), list(zip(gauges, z))[1:]):
        dist = chainage[g2.site] - chainage[g1.site]
        slope = (z1 - z2) / dist if z1 is not None and z2 is not None and dist > 0 else None
        s.append(slope if slope is not None and 1e-6 < slope < 0.05 else None)
    for i, v in enumerate(s):  # fill gaps from the nearest segment that has a slope
        if v is None:
            near = [x for x in sorted(range(len(s)), key=lambda j: abs(j - i)) if s[x] is not None]
            s[i] = s[near[0]] if near else DEFAULT_SLOPE
    return s


def dispersion_field(field, records, slopes):
    """Fischer's longitudinal dispersion for every element and step, m2/s
    (specification section 7.1): K = 0.011 U^2 W^2 / (d u*), u* = sqrt(g d S)."""
    chain = sorted(r.chainage for r in records)
    shapes = [r.shape for r in sorted(records, key=lambda r: r.chainage)]
    out = [[0.0] * len(field.elements) for _ in range(field.steps)]
    for e, el in enumerate(field.elements):
        k = max(0, min(len(chain) - 2, sum(1 for c in chain[1:-1] if c <= el.mid)))
        span = chain[k + 1] - chain[k]
        f = 0.0 if span <= 0 else min(1.0, max(0.0, (el.mid - chain[k]) / span))
        shape = channel.between(shapes[k], shapes[k + 1], f)
        slope = slopes[min(k, len(slopes) - 1)]
        for i in range(field.steps):
            q = field.flow[i][e]
            d, w, u = shape.depth(q), shape.width(q), field.velocity[i][e]
            out[i][e] = fdmr_py.fischer_k(u, w, d, fdmr_py.shear_velocity(d, slope))
    return out


def element_widths(field, records) -> list[float]:
    """Each element's water-surface width, m, at its median flow over the
    published steps: the channel shape fitted at the gauges either side
    (USGS field measurements), taken between them as the routing does."""
    chain = sorted(r.chainage for r in records)
    shapes = [r.shape for r in sorted(records, key=lambda r: r.chainage)]
    out = []
    for e, el in enumerate(field.elements):
        k = max(0, min(len(chain) - 2, sum(1 for c in chain[1:-1] if c <= el.mid)))
        span = chain[k + 1] - chain[k]
        f = 0.0 if span <= 0 else min(1.0, max(0.0, (el.mid - chain[k]) / span))
        shape = channel.between(shapes[k], shapes[k + 1], f)
        q = sorted(field.flow[i][e] for i in range(field.steps))[field.steps // 2]
        out.append(round(shape.width(max(q, 1e-3)), 1))
    return out


def river_chain(region_dir: Path, reach: str):
    """The reach's release and main gauges, upstream to downstream, and their chainage (m)."""
    region = load_region(region_dir)
    geo = json.loads((region_dir / f"{reach}.json").read_text(encoding="utf-8"))
    gauges = [g for g in region.gauges_for(reach) if g.role in ("release", "main")]
    return gauges, {gauges[0].site: 0.0, **geo["gauges_m"]}


def fit_shapes(gauges, chainage, store: Store) -> dict:
    """Channel shape at each gauge from its USGS field measurements of the last
    ten years; a gauge with too few takes the nearest gauge's shape."""
    since = int(time.time()) - 10 * 365 * DAY
    shapes = {g.site: channel.fit(store.channel_measurements(SOURCE, g.site), since=since) for g in gauges}
    if not any(shapes.values()):
        raise ValueError("no gauge has enough channel measurements")
    return {
        g.site: shapes[g.site] or min(
            ((abs(chainage[o.site] - chainage[g.site]), shapes[o.site]) for o in gauges if shapes[o.site]),
            key=lambda x: x[0],
        )[1]
        for g in gauges
    }


def corrections(calibration: dict | None, reach: str, gauges) -> tuple[list[float], list[float]]:
    """Wave scale and water factor for each segment between consecutive gauges (1 where uncalibrated)."""
    segs = {}
    for r in (calibration or {}).get("reaches", []):
        if r["reach"] == reach:
            segs = {(s["up"], s["down"]): s for s in r["segments"]}
    pairs = [segs.get((a.site, b.site), {}) for a, b in zip(gauges, gauges[1:])]
    return [p.get("wave_scale", 1.0) for p in pairs], [p.get("water_factor", 1.0) for p in pairs]


def add_reach(cdir: Path, src: Path, index: dict) -> None:
    """Add a reach published on its own schedule (such as the Delta's DSM2 run,
    every 6 hours) to this cycle: its files hard-linked in, its entry, with its
    own time axis, added to the index (format 1.1)."""
    src = src.resolve()
    entry = json.loads((src / "reach.json").read_text(encoding="utf-8"))
    for f in src.rglob("*"):
        if f.is_file() and f.name != "reach.json":
            dest = cdir / f.relative_to(src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and os.path.samefile(f, dest):
                continue
            tmp = dest.with_name(dest.name + ".tmp")
            if tmp.exists():
                tmp.unlink()
            os.link(f, tmp)
            os.replace(tmp, dest)  # a newer run replaces the file whole, never in place
    index["reaches"].append(entry)
    index["format_version"] = [1, 1]


def publish(
    region_dir: Path,
    store: Store,
    out_dir: Path,
    days: int,
    reach: str = "river",
    datums: dict[str, float | None] | None = None,
    ahead: int = 14,
    calibration: dict | None = None,
    extra_reaches: list[Path] | None = None,
) -> Path:
    region = load_region(region_dir)
    geo = json.loads((region_dir / f"{reach}.json").read_text(encoding="utf-8"))
    line = Centreline(coords=[tuple(c) for c in geo["coords"]], chainage=geo["chainage_m"])
    gauges, chainage = river_chain(region_dir, reach)

    # The model's "now": the latest time every main gauge has reported.
    latest = [store.latest(SOURCE, g.site, "discharge") for g in gauges]
    if any(x is None for x in latest):
        raise ValueError("a main gauge has no discharge in the store")
    now = min(t for t, _ in latest) // STEP * STEP
    t0 = (now - days * DAY) // DAY * DAY
    i_now = (now - t0) // STEP
    end = now + ahead * DAY
    n = (end - t0) // STEP + 1

    shapes = fit_shapes(gauges, chainage, store)
    records = []
    fc_ends: list[int | None] = []  # last forecast step at each gauge that has a forecast
    for g in gauges:
        shape = shapes[g.site]
        vals = store.values(SOURCE, g.site, "discharge", t0, now + STEP)
        measured = regular([(v.t, v.value) for v in vals], t0, STEP, i_now + 1)
        fc = None
        if g.forecast and g.forecast.get("source") == cnrfc.SOURCE:
            fv = store.values(cnrfc.SOURCE, g.forecast["point"], "discharge", now - DAY, end + DAY)
            fc = forecast.on_grid([(v.t, v.value) for v in fv], t0, STEP, n) if fv else None
        flow, fc_end = forecast.extend(measured, fc, i_now, n, BLEND_S // STEP)
        if fc is not None:
            fc_ends.append(fc_end)
        records.append(model.GaugeRecord(chainage[g.site], shape, flow))

    wave_scale, water_factor = corrections(calibration, reach, gauges)
    field = model.build(max(chainage.values()), records, t0, STEP, wave_scale=wave_scale, water_factor=water_factor)
    slopes = segment_slopes(gauges, chainage, store, datums or {}, t0, now)
    dispersion = dispersion_field(field, records, slopes)
    # The solid forecast lasts the reach's set days, or as far as every gauge's
    # forecast reaches, whichever is shorter; with no forecast, nothing is solid.
    rc = next((r.get("certainty", {}) for r in region.reaches if r["id"] == reach), {})
    covered = (min(fc_ends) - i_now) * STEP if fc_ends and None not in fc_ends else 0
    solid = min(rc.get("solid_days", 5) * DAY, covered)
    floor = rc.get("floor", 0.2)
    certainty = [round(fdmr_py.certainty_at((i - i_now) * STEP, solid, ahead * DAY, floor), 4) for i in range(n)]

    cycle = utc(now, "%Y%m%dT%H%MZ")
    cdir = out_dir / cycle
    (cdir / reach).mkdir(parents=True, exist_ok=True)
    (cdir / "geometry").mkdir(exist_ok=True)

    els = field.elements
    widths = element_widths(field, records)
    (cdir / "geometry" / f"{reach}.json").write_text(json.dumps({
        "reach": reach,
        "dims": 1,
        "elements": [{"start_m": round(e.start, 1), "end_m": round(e.end, 1), "width_m": w} for e, w in zip(els, widths)],
        "lines": element_lines(line, els),
    }, separators=(",", ":")), encoding="utf-8")

    day_names = []
    for d0 in range(t0, end + 1, DAY):
        i0, i1 = (d0 - t0) // STEP, min(n, (d0 + DAY - t0) // STEP)
        if i0 >= i1:
            continue
        name = utc(d0, "%Y-%m-%d")
        day_names.append(name)
        rows = range(i0, i1)
        flat = lambda m: [float(m[i][e]) for i in rows for e in range(len(els))]  # noqa: E731
        fdmr_py.write_field_file(
            str(cdir / reach / f"{name}.fdf"), reach, 1, len(els), 1, i1 - i0, d0, STEP,
            [
                ("flow", "m3/s", True, flat(field.flow)),
                ("area", "m2", True, flat(field.area)),
                ("velocity", "m/s", True, flat(field.velocity)),
                ("volume", "m3", True, [float(field.area[i][e] * els[e].length) for i in rows for e in range(len(els))]),
                ("dispersion", "m2/s", True, flat(dispersion)),
            ],
        )

    data_age = {SOURCE: int(time.time() - now)}
    issues = [store.latest(cnrfc.SOURCE, g.forecast["point"], "discharge") for g in gauges if g.forecast]
    fetched = [f for _, f in (x for x in issues if x)]
    if fetched:
        data_age[cnrfc.SOURCE] = int(time.time() - min(fetched))
    index = {
        "format": "fdmr-flow-field",
        "format_version": [1, 0],
        "region": region.id,
        "cycle": utc(now),
        "created": utc(time.time()),
        "time": {"start": utc(t0), "step_s": STEP, "days": day_names},
        "now": utc(now),
        "data_age_s": data_age,
        "reaches": [{
            "id": reach, "kind": "river-routing", "dims": 1, "elements": len(els), "layers": 1,
            "fields": ["flow", "area", "velocity", "volume", "dispersion"],
            "certainty": certainty,
        }],
        "status": "ok",
    }
    for extra in extra_reaches or []:
        add_reach(cdir, extra, index)
    ids = {r["id"] for r in index["reaches"]}
    joins = [{k: h[k] for k in ("from", "to", "node", "source") if k in h} for h in region.handoffs if h["from"] in ids and h["to"] in ids and "node" in h]
    # A network reach meeting a mesh reach (the Delta and the Bay): the join is
    # worked out from their two geometries (fdmr_runner.handoff).
    kinds = {r["id"]: r for r in index["reaches"]}
    for h in region.handoffs:
        a, b = kinds.get(h["from"]), kinds.get(h["to"])
        if a and b and a.get("network") and b.get("dims") == 2:
            from .handoff import network_to_mesh

            geo = lambda r: json.loads((cdir / "geometry" / f"{r}.json").read_text(encoding="utf-8"))  # noqa: E731
            joins.append(network_to_mesh(h["from"], geo(h["from"]), h["to"], geo(h["to"])))
        elif a and b and a.get("network") and b.get("kind") == "canal-network":
            # A network's exports carrying on into the canals (fdmr_runner.canals):
            # each named export enters its canal's first junction.
            g = json.loads((cdir / "geometry" / f"{h['to']}.json").read_text(encoding="utf-8"))
            joins.append({"from": h["from"], "to": h["to"], "outlets": [{"export": i["export"], "node": i["node"]} for i in g["inlets"]]})
    if joins:
        index["handoffs"] = joins  # format 1.1: where parcels leaving one reach enter the next
    # What is drawn over the water: X2, the stations' readings (fdmr_runner.overlays).
    try:
        from . import overlays

        ov = overlays.build(region_dir, cdir, index, store, int(time.time()))
        if ov is not None:
            (cdir / "overlays.json").write_text(json.dumps(ov, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            index["overlays"] = "overlays.json"
    except Exception as e:  # the water is published whatever happens to its overlays
        print(f"overlays FAILED {e}", flush=True)
    (cdir / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")

    tmp = out_dir / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(cycle)
    os.replace(tmp, out_dir / "current")
    return cdir


CYCLE = re.compile(r"\d{8}T\d{4}Z$")


def prune(out_dir: Path, keep: int) -> list[str]:
    """Remove all but the newest `keep` cycles, never the current one. Returns what was removed."""
    current = os.readlink(out_dir / "current") if (out_dir / "current").is_symlink() else None
    cycles = sorted(p.name for p in out_dir.iterdir() if p.is_dir() and not p.is_symlink() and CYCLE.match(p.name))
    gone = [c for c in cycles[: max(0, len(cycles) - keep)] if c != current]
    for c in gone:
        shutil.rmtree(out_dir / c)
    return gone


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--ahead", type=int, default=14)
    ap.add_argument("--keep", type=int, default=3, help="cycles to keep, the newest first")
    ap.add_argument("--calibration", help="the calibration file written by fdmr_runner.calibrate")
    ap.add_argument("--with-reach", action="append", default=[], help="a reach published on its own schedule, to include (repeatable)")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    region = load_region(args.region)
    usgs = UsgsAdapter(Store(":memory:"))
    datums = {g.site: usgs.datum_m(g.site) for g in region.gauges if g.role in ("release", "main")}
    cal_path = Path(args.calibration) if args.calibration else None
    calibration = json.loads(cal_path.read_text(encoding="utf-8")) if cal_path and cal_path.exists() else None
    extra = [Path(p) for p in args.with_reach if (Path(p) / "reach.json").exists()]
    cdir = publish(Path(args.region), Store(args.store), out, args.days, datums=datums, ahead=args.ahead,
                   calibration=calibration, extra_reaches=extra)
    size = sum(f.stat().st_size for f in cdir.rglob("*") if f.is_file())
    print(f"published {cdir} ({size / 1e6:.1f} MB)")
    gone = prune(out, max(1, args.keep))
    if gone:
        print(f"removed {len(gone)} older cycles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
