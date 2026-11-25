"""Publish a region's flow field for the last N days (docs/format.md).

    python -m fdmr_runner.publish REGION_DIR STORE_FILE OUT_DIR [--days 14]

Writes OUT_DIR/<cycle>/ with index.json, geometry/<reach>.json and
<reach>/<YYYY-MM-DD>.fdf, then points OUT_DIR/current at the new cycle in one
step, so a reader never sees half a cycle. The past only, for now: forecast
days come with the river-forecast adapter.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import fdmr_py
from fdmr_adapters.usgs import SOURCE, UsgsAdapter
from fdmr_plugins.river_routing import channel, model
from fdmr_plugins.river_routing.centreline import Centreline
from fdmr_plugins.river_routing.wave import regular
from fdmr_store import Store

from .region import load_region

STEP = 900
DAY = 86_400


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


def publish(region_dir: Path, store: Store, out_dir: Path, days: int, reach: str = "river", datums: dict[str, float | None] | None = None) -> Path:
    region = load_region(region_dir)
    geo = json.loads((region_dir / f"{reach}.json").read_text(encoding="utf-8"))
    line = Centreline(coords=[tuple(c) for c in geo["coords"]], chainage=geo["chainage_m"])
    gauges = [g for g in region.gauges_for(reach) if g.role in ("release", "main")]
    chainage = {gauges[0].site: 0.0, **geo["gauges_m"]}

    # The model's "now": the latest time every main gauge has reported.
    latest = [store.latest(SOURCE, g.site, "discharge") for g in gauges]
    if any(x is None for x in latest):
        raise ValueError("a main gauge has no discharge in the store")
    now = min(t for t, _ in latest) // STEP * STEP
    t0 = (now - days * DAY) // DAY * DAY
    n = (now - t0) // STEP + 1

    since = int(time.time()) - 10 * 365 * DAY
    shapes = {g.site: channel.fit(store.channel_measurements(SOURCE, g.site), since=since) for g in gauges}
    known = [s for s in shapes.values() if s]
    if not known:
        raise ValueError("no gauge has enough channel measurements")
    records = []
    for g in gauges:
        shape = shapes[g.site] or min(  # nearest gauge with a fitted shape
            ((abs(chainage[o.site] - chainage[g.site]), shapes[o.site]) for o in gauges if shapes[o.site]),
            key=lambda x: x[0],
        )[1]
        vals = store.values(SOURCE, g.site, "discharge", t0, now + STEP)
        records.append(model.GaugeRecord(chainage[g.site], shape, regular([(v.t, v.value) for v in vals], t0, STEP, n)))

    field = model.build(max(chainage.values()), records, t0, STEP)
    slopes = segment_slopes(gauges, chainage, store, datums or {}, t0, now)
    dispersion = dispersion_field(field, records, slopes)
    cycle = utc(now, "%Y%m%dT%H%MZ")
    cdir = out_dir / cycle
    (cdir / reach).mkdir(parents=True, exist_ok=True)
    (cdir / "geometry").mkdir(exist_ok=True)

    els = field.elements
    (cdir / "geometry" / f"{reach}.json").write_text(json.dumps({
        "reach": reach,
        "dims": 1,
        "elements": [{"start_m": round(e.start, 1), "end_m": round(e.end, 1)} for e in els],
        "lines": element_lines(line, els),
    }, separators=(",", ":")), encoding="utf-8")

    day_names = []
    for d0 in range(t0, now + 1, DAY):
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

    index = {
        "format": "fdmr-flow-field",
        "format_version": [1, 0],
        "region": region.id,
        "cycle": utc(now),
        "created": utc(time.time()),
        "time": {"start": utc(t0), "step_s": STEP, "days": day_names},
        "now": utc(now),
        "data_age_s": {SOURCE: int(time.time() - now)},
        "reaches": [{
            "id": reach, "kind": "river-routing", "dims": 1, "elements": len(els), "layers": 1,
            "fields": ["flow", "area", "velocity", "volume", "dispersion"],
            "certainty": [1.0] * n,  # the past is built from measurements
        }],
        "status": "ok",
    }
    (cdir / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")

    tmp = out_dir / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(cycle)
    os.replace(tmp, out_dir / "current")
    return cdir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    region = load_region(args.region)
    usgs = UsgsAdapter(Store(":memory:"))
    datums = {g.site: usgs.datum_m(g.site) for g in region.gauges if g.role in ("release", "main")}
    cdir = publish(Path(args.region), Store(args.store), out, args.days, datums=datums)
    size = sum(f.stat().st_size for f in cdir.rglob("*") if f.is_file())
    print(f"published {cdir} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
