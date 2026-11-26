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


def shape_at(records, x: float):
    """(segment, channel shape) at chainage x: the shapes fitted at the gauges
    either side, taken between them; a reach with one gauge has its shape throughout."""
    recs = sorted(records, key=lambda r: r.chainage)
    if len(recs) == 1:
        return 0, recs[0].shape
    chain = [r.chainage for r in recs]
    k = max(0, min(len(chain) - 2, sum(1 for c in chain[1:-1] if c <= x)))
    span = chain[k + 1] - chain[k]
    f = 0.0 if span <= 0 else min(1.0, max(0.0, (x - chain[k]) / span))
    return k, channel.between(recs[k].shape, recs[k + 1].shape, f)


def dispersion_field(field, records, slopes):
    """Fischer's longitudinal dispersion for every element and step, m2/s
    (specification section 7.1): K = 0.011 U^2 W^2 / (d u*), u* = sqrt(g d S).
    A reach with no gauge pair to give a slope takes the default."""
    out = [[0.0] * len(field.elements) for _ in range(field.steps)]
    for e, el in enumerate(field.elements):
        k, shape = shape_at(records, el.mid)
        slope = slopes[min(k, len(slopes) - 1)] if slopes else DEFAULT_SLOPE
        for i in range(field.steps):
            q = field.flow[i][e]
            d, w, u = shape.depth(q), shape.width(q), field.velocity[i][e]
            out[i][e] = fdmr_py.fischer_k(u, w, d, fdmr_py.shear_velocity(d, slope))
    return out


def element_widths(field, records) -> list[float]:
    """Each element's water-surface width, m, at its median flow over the
    published steps: the channel shape fitted at the gauges either side
    (USGS field measurements), taken between them as the routing does."""
    out = []
    for e, el in enumerate(field.elements):
        _, shape = shape_at(records, el.mid)
        q = sorted(field.flow[i][e] for i in range(field.steps))[field.steps // 2]
        out.append(round(shape.width(max(q, 1e-3)), 1))
    return out


def river_chain(region_dir: Path, reach: str):
    """The reach's release and main gauges, upstream to downstream, and their chainage (m)."""
    region = load_region(region_dir)
    geo = json.loads((region_dir / f"{reach}.json").read_text(encoding="utf-8"))
    gauges = [g for g in region.gauges_for(reach) if g.role in ("release", "main")]
    # The release gauge is at 0 unless the reach starts higher, at its dam.
    return gauges, {gauges[0].site: 0.0, **geo["gauges_m"]}


def fit_shapes(gauges, chainage, store: Store) -> dict:
    """Channel shape at each gauge from its USGS field measurements of the last
    ten years (for another agency's station, those of the USGS site at the
    same place, of any age if none are recent); a gauge with too few takes
    the nearest gauge's shape."""
    since = int(time.time()) - 10 * 365 * DAY

    def fit(g):
        if not g.place_site:
            return None
        ms = store.channel_measurements(SOURCE, g.place_site)
        return channel.fit(ms, since=since) or (channel.fit(ms) if g.source != SOURCE else None)

    shapes = {g.site: fit(g) for g in gauges}
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


def river_days(t0: int, end: int, n: int) -> list[tuple[str, int, int, int]]:
    """(name, day start, first step, end step) of each UTC day the window touches."""
    out = []
    for d0 in range(t0, end + 1, DAY):
        i0, i1 = (d0 - t0) // STEP, min(n, (d0 + DAY - t0) // STEP)
        if i0 < i1:
            out.append((utc(d0, "%Y-%m-%d"), d0, i0, i1))
    return out


def publish_river(region_dir: Path, region, store: Store, cdir: Path, reach: str, t0: int, now: int, end: int,
                  ahead: int, datums: dict | None, calibration: dict | None,
                  weather_cache: dict | None = None, done: dict | None = None) -> tuple[dict, list[int]]:
    """One river-routing reach's geometry and day files in `cdir`: its index entry,
    and the issue times of the river forecasts it used. `done` holds each reach
    already published, with its outflow and its water's temperature there, for
    the reaches it joins (a tributary's water mixes in)."""
    geo = json.loads((region_dir / f"{reach}.json").read_text(encoding="utf-8"))
    line = Centreline(coords=[tuple(c) for c in geo["coords"]], chainage=geo["chainage_m"])
    gauges, chainage = river_chain(region_dir, reach)
    i_now = (now - t0) // STEP
    n = (end - t0) // STEP + 1

    shapes = fit_shapes(gauges, chainage, store)
    records = []
    fc_ends: list[int | None] = []  # last forecast step at each gauge that has a forecast
    for g in gauges:
        shape = shapes[g.site]
        vals = store.values(g.source, g.site, g.parameter, t0, now + STEP)
        if not vals:
            raise ValueError(f"gauge {g.site} has no discharge in the store")
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
    field = model.build(geo.get("model_length_m", max(chainage.values())), records, t0, STEP,
                        wave_scale=wave_scale, water_factor=water_factor)
    slopes = segment_slopes(gauges, chainage, store, datums or {}, t0, now)
    dispersion = dispersion_field(field, records, slopes)
    # The solid forecast lasts the reach's set days, or as far as every gauge's
    # forecast reaches, whichever is shorter; with no forecast, nothing is solid.
    rc = next((r.get("certainty", {}) for r in region.reaches if r["id"] == reach), {})
    covered = (min(fc_ends) - i_now) * STEP if fc_ends and None not in fc_ends else 0
    solid = min(rc.get("solid_days", 5) * DAY, covered)
    floor = rc.get("floor", 0.2)
    certainty = [round(fdmr_py.certainty_at((i - i_now) * STEP, solid, ahead * DAY, floor), 4) for i in range(n)]

    # Water temperature (specification 7.5), with every tributary already done mixing in.
    temperature, temp_checks = None, []
    try:
        inflows = []
        for h in region.handoffs:
            if h["to"] == reach and "node" not in h and h["from"] in (done or {}):
                q_t, t_t = done[h["from"]]
                if t_t is None:
                    continue
                at = centreline_join(region_dir, h["from"], reach)
                e = min(range(len(field.elements)), key=lambda i: abs(field.elements[i].mid - at))
                inflows.append((e, q_t, t_t))
        res = river_temperature(region_dir, region, store, reach, line, field, records, t0, now, n,
                                weather_cache if weather_cache is not None else {}, inflows)
        if res is not None:
            temperature, temp_checks = res
    except Exception as e:  # noqa: BLE001 - the flow is published whatever happens to the temperature
        print(f"{reach}: temperature FAILED {e}", flush=True)
    if done is not None:
        import numpy as np

        done[reach] = (np.array([row[-1] for row in field.flow]), None if temperature is None else temperature[:, -1])

    (cdir / reach).mkdir(parents=True, exist_ok=True)
    els = field.elements
    widths = element_widths(field, records)
    (cdir / "geometry" / f"{reach}.json").write_text(json.dumps({
        "reach": reach,
        "dims": 1,
        "elements": [{"start_m": round(e.start, 1), "end_m": round(e.end, 1), "width_m": w} for e, w in zip(els, widths)],
        "lines": element_lines(line, els),
    }, separators=(",", ":")), encoding="utf-8")

    for name, d0, i0, i1 in river_days(t0, end, n):
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
        if temperature is not None:
            fdmr_py.write_field_file(
                str(cdir / reach / f"{name}.quality.fdf"), f"{reach}:quality", 1, len(els), 1, i1 - i0, d0, STEP,
                [("temperature", "degC", True, temperature[i0:i1].ravel().tolist())],
            )
    issues = [store.latest(cnrfc.SOURCE, g.forecast["point"], "discharge") for g in gauges if g.forecast]
    entry = {
        "id": reach, "kind": "river-routing", "dims": 1, "elements": len(els), "layers": 1,
        "fields": ["flow", "area", "velocity", "volume", "dispersion"],
        "certainty": certainty,
        **({"quality_fields": ["temperature"], "temperature_checks": temp_checks} if temperature is not None else {}),
        **({"swot_checks": sw} if (sw := swot_checks(region_dir, store, reach, line, els, widths, now)) else {}),
    }
    return entry, [f for _, f in (x for x in issues if x)]


TEMPERATURE = "water_temperature"


def centreline_join(region_dir: Path, a: str, b: str) -> float:
    """Where river reach `a` ends on reach `b`, m along b: b's centreline point
    nearest a's last point (from the region's centreline files)."""
    import math

    ga = json.loads((region_dir / f"{a}.json").read_text(encoding="utf-8"))
    gb = json.loads((region_dir / f"{b}.json").read_text(encoding="utf-8"))
    end = ga["coords"][-1]
    k = math.cos(math.radians(end[1]))
    i = min(range(len(gb["coords"])), key=lambda j: math.hypot((gb["coords"][j][0] - end[0]) * k, gb["coords"][j][1] - end[1]))
    return gb["chainage_m"][i]


def on_centreline(line: Centreline, lon: float, lat: float) -> tuple[float, float]:
    """(chainage m, distance m) of the centreline point nearest (lon, lat)."""
    import math

    k = math.cos(math.radians(lat))
    i = min(range(len(line.coords)), key=lambda j: math.hypot((line.coords[j][0] - lon) * k, line.coords[j][1] - lat))
    return line.chainage[i], math.hypot((line.coords[i][0] - lon) * k, line.coords[i][1] - lat) * 111_195


def river_temperature(region_dir: Path, region, store: Store, reach: str, line: Centreline, field, records,
                      t0: int, now: int, n: int, weather_cache: dict, inflows: list) -> tuple | None:
    """Water temperature for every element and step of a river reach
    (fdmr_plugins.river_routing.heat): the release's measured temperature
    carried down, warming and cooling with the weather of the nearest weather
    zone, tributaries mixing in, then corrected toward every station on the
    reach that measures it. None where the release's temperature is not
    measured. Returns (temperature [step, element], checks)."""
    import math
    import tomllib

    import numpy as np
    from fdmr_plugins.river_routing import heat

    from . import delta as dl

    cfg = next((r for r in region.reaches if r["id"] == reach), {})
    rel = cfg.get("release_temperature")
    rt = tomllib.loads((region_dir / "region.toml").read_text(encoding="utf-8")).get("river_temperature", {})
    zones = rt.get("weather", [])
    if not rel or not zones:
        return None
    times = t0 + STEP * np.arange(n)
    i_now = (now - t0) // STEP

    def measured(source: str, site: str, duration_cut: int) -> np.ndarray:
        vs = [(v.t, v.value) for v in store.values(source, site, TEMPERATURE, t0 - DAY, duration_cut + 1) if v.value is not None]
        g = forecast.on_grid(vs, t0, STEP, n, max_gap_s=6 * 3600)
        return np.array([np.nan if v is None else v for v in g], dtype=float)

    top = measured(rel["source"], rel["site"], now)
    if not np.isfinite(top[: i_now + 1]).any():
        return None
    # Past its last reading the release's water is held at its last 25 h mean (specification 7.5).
    known = np.where(np.isfinite(top))[0]
    last = known[-1]
    hold = float(np.nanmean(top[max(0, last - 99): last + 1]))
    top = np.interp(np.arange(n), known, top[known])
    top[last + 1:] = hold

    els = field.elements
    x_el = np.array([e.mid for e in els])
    lengths = [e.length for e in els]
    lonlat = [line.point_at(e.mid) for e in els]
    zone_of = [min(range(len(zones)), key=lambda z: math.hypot(zones[z]["lon"] - lo, zones[z]["lat"] - la)) for lo, la in lonlat]
    for z in set(zone_of):
        name = zones[z]["name"]
        if name not in weather_cache:
            w, _ = dl.weather(store, zones[z], t0, n, now, use_forecast=True)
            w = {k: np.array(v, dtype=float) for k, v in w.items()}
            w["sunlight"] = heat.sunlight(times, zones[z]["lat"], zones[z]["lon"], zones[z].get("elev_m", 0.0), w["cloud_cover"])
            weather_cache[name] = w
    weather = {}
    for k in ("air_temperature", "dew_point", "wind_speed", "cloud_cover", "sunlight"):
        cols = {z: weather_cache[zones[z]["name"]][k] for z in set(zone_of)}
        weather[k] = np.stack([cols[z] for z in zone_of], axis=1)

    q = np.array(field.flow, dtype=float)
    a = np.array(field.area, dtype=float)
    u = np.array(field.velocity, dtype=float)
    wa = np.array([shape_at(records, e.mid)[1].width_a for e in els])
    wb = np.array([shape_at(records, e.mid)[1].width_b for e in els])
    depth = a / np.clip(wa * np.clip(q, 1e-3, None) ** wb, 1.0, None)

    # Stations on the reach that measure water temperature (region.toml [[stations]]).
    stations, names = [], []
    for s in region.stations:
        if s.get("reach") != reach or TEMPERATURE not in s.get("measures", []):
            continue
        x, off = on_centreline(line, s["lon"], s["lat"])
        if off > 500:
            print(f"{reach}: temperature station {s['id']} is {off:.0f} m from the river; left out", flush=True)
            continue
        stations.append((x, measured(s.get("source", "cdec"), s["id"], now)))
        names.append(s["id"])
    initial = np.full(len(els), top[0])
    at0 = [(x, m[0]) for x, m in stations if np.isfinite(m[0])]
    if at0:
        xs, vs = zip(*sorted(at0))
        initial = np.interp(x_el, xs, vs)

    model = heat.run(lengths, u, depth, q, a, STEP, top, initial, weather, inflows)
    out = heat.correct(model, x_el, times.astype(float), stations)

    # Checks: each station's misfit before correction, and its miss when the
    # correction is made from the other stations alone.
    checks = []
    past = np.arange(n) <= i_now
    for k, (x, meas) in enumerate(stations):
        e = int(np.clip(np.searchsorted(x_el, x), 0, len(els) - 1))
        ok = past & np.isfinite(meas)
        if not ok.any():
            continue
        loo = heat.correct(model, x_el, times.astype(float), [s for j, s in enumerate(stations) if j != k])
        d0, d1 = meas[ok] - model[ok, e], meas[ok] - loo[ok, e]
        checks.append({"station": names[k], "km": round(x / 1000, 1), "readings": int(ok.sum()),
                       "mean_misfit": round(float(d0.mean()), 2), "rms_misfit": round(float(np.sqrt((d0 ** 2).mean())), 2),
                       "left_out_mean": round(float(d1.mean()), 2), "left_out_rms": round(float(np.sqrt((d1 ** 2).mean())), 2)})
    return out, checks


def swot_checks(region_dir: Path, store: Store, reach: str, line: Centreline, els, widths, now: int) -> dict | None:
    """The river's widths (at each element's median flow) against SWOT's
    measured widths over the last 60 days (region.toml [[river_swot]]): per
    SWOT reach, the median of its passes beside the model's width at the
    element nearest its centre, and over all of them the median ratio."""
    import tomllib

    import numpy as np

    cfg = tomllib.loads((region_dir / "region.toml").read_text(encoding="utf-8")).get("river_swot", [])
    rs = next((c for c in cfg if c["reach"] == reach), None)
    if not rs:
        return None
    mids = np.array([e.mid for e in els])
    rows = []
    for r in rs["reaches"]:
        vs = [v.value for v in store.values("swot", r["id"], "width", now - 60 * 86_400, now + 1) if v.value is not None]
        if len(vs) < 2:
            continue
        x, off = on_centreline(line, r["lon"], r["lat"])
        if off > 1000:
            continue
        e = int(np.argmin(np.abs(mids - x)))
        rows.append({"id": r["id"], "km": round(x / 1000, 1), "passes": len(vs),
                     "swot_width": round(float(np.median(vs)), 1), "model_width": widths[e]})
    if not rows:
        return None
    ratio = [r["model_width"] / r["swot_width"] for r in rows if r["swot_width"] > 0]
    return {"reaches": len(rows), "median_ratio": round(float(np.median(ratio)), 2), "per_reach": rows}


def join_at(cdir: Path, a: str, b: str) -> float:
    """Where line reach `a` ends on line reach `b`: the chainage (m) of b's
    nearest point to a's last point, from their published geometries."""
    import math

    ga = json.loads((cdir / "geometry" / f"{a}.json").read_text(encoding="utf-8"))
    gb = json.loads((cdir / "geometry" / f"{b}.json").read_text(encoding="utf-8"))
    end = ga["lines"][-1][-1]
    k = math.cos(math.radians(end[1]))
    best, at = math.inf, 0.0
    for el, line in zip(gb["elements"], gb["lines"]):
        seg = [0.0]
        for p, q in zip(line, line[1:]):
            seg.append(seg[-1] + math.hypot((q[0] - p[0]) * k, q[1] - p[1]))
        total = seg[-1] or 1.0
        for p, s in zip(line, seg):
            d = math.hypot((p[0] - end[0]) * k, p[1] - end[1])
            if d < best:
                best, at = d, el["start_m"] + (el["end_m"] - el["start_m"]) * s / total
    return round(at, 1)


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
    gauges, _ = river_chain(region_dir, reach)

    # The model's "now": the latest time every main gauge of the main river has reported.
    latest = [store.latest(g.source, g.site, g.parameter) for g in gauges]
    if any(x is None for x in latest):
        raise ValueError("a main gauge has no discharge in the store")
    now = min(t for t, _ in latest) // STEP * STEP
    t0 = (now - days * DAY) // DAY * DAY
    end = now + ahead * DAY
    n = (end - t0) // STEP + 1

    cycle = utc(now, "%Y%m%dT%H%MZ")
    cdir = out_dir / cycle
    (cdir / "geometry").mkdir(parents=True, exist_ok=True)

    # Every river-routing reach, the main one first: each from its own dam's
    # release (specification 3.13; issue #4). Another reach that cannot be
    # built this cycle is left out rather than holding back the rest.
    entries, fetched = [], []
    others = [r["id"] for r in region.reaches if r.get("kind") == "river-routing" and r["id"] != reach]

    def hops(r: str, seen: int = 0) -> int:
        h = next((x for x in region.handoffs if x["from"] == r and "node" not in x), None)
        return 1 + hops(h["to"], seen + 1) if h and seen < 10 else 0

    weather_cache: dict = {}
    done: dict = {}
    for rid in sorted([reach, *others], key=hops, reverse=True):  # upstream reaches first
        try:
            entry, issued = publish_river(region_dir, region, store, cdir, rid, t0, now, end, ahead, datums, calibration,
                                          weather_cache, done)
        except Exception as e:  # noqa: BLE001
            if rid == reach:
                raise
            print(f"{rid}: FAILED {e}", flush=True)
            continue
        entries.append(entry)
        fetched += issued
    entries.sort(key=lambda e: e["id"] != reach)  # the main river first
    day_names = [d[0] for d in river_days(t0, end, n)]

    data_age = {SOURCE: int(time.time() - now)}
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
        "reaches": entries,
        "status": "ok",
    }
    for extra in extra_reaches or []:
        add_reach(cdir, extra, index)
    ids = {r["id"] for r in index["reaches"]}
    joins = [{k: h[k] for k in ("from", "to", "node", "source") if k in h} for h in region.handoffs if h["from"] in ids and h["to"] in ids and "node" in h]
    # A river reach ending on another (a tributary joining a river): where along it.
    lines = {r["id"] for r in index["reaches"] if r.get("dims") == 1 and not r.get("network")}
    for h in region.handoffs:
        if h["from"] in lines and h["to"] in lines and "node" not in h:
            joins.append({"from": h["from"], "to": h["to"], "at_m": join_at(cdir, h["from"], h["to"]),
                          **({"source": h["source"]} if "source" in h else {})})
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
    # The last fetch's own account of what it could not get (fdmr_runner.fetch).
    fs = store.path.parent / f"fetch-status-{index['region']}.json" if store.path.name != ":memory:" else None
    if fs and fs.exists():
        index["fetch_status"] = json.loads(fs.read_text(encoding="utf-8"))
    # The prediction ledger (fdmr_runner.ledger): this cycle's predictions kept
    # when due, and every kept prediction scored against what was measured.
    if store.path.name != ":memory:":
        try:
            from . import ledger

            skill = ledger.run(region_dir, cdir, index, store, store.path.parent / "ledger" / f"{index['region']}.sqlite", int(time.time()))
            (cdir / "skill.json").write_text(json.dumps(skill, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            index["skill"] = "skill.json"
            print(f"ledger: kept {skill['kept_this_cycle']} values; {skill['cycles']} cycles scored", flush=True)
        except Exception as e:  # a record; the water is published whatever happens to it
            print(f"ledger FAILED {e}", flush=True)
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
    datums = {g.site: usgs.datum_m(g.site) for g in region.gauges if g.role in ("release", "main") and g.source == SOURCE}
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
