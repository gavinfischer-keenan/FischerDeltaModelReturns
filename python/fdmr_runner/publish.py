"""Publish a region's flow field for the last N days and the next M (docs/format.md).

    python -m fdmr_runner.publish REGION_DIR STORE_FILE OUT_DIR [--days 14] [--ahead 14] [--keep 3]

Writes OUT_DIR/<cycle>/ with index.json, geometry/<reach>.json and
<reach>/<YYYY-MM-DD>.fdf, built in a folder of its own and renamed into place
whole, then points OUT_DIR/current at it in one step, so a reader never sees
half a cycle (a cycle published again with the same "now" is <cycle>.r2). Older cycles beyond the newest
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
from fdmr_plugins.river_routing import channel, form, model
from fdmr_plugins.river_routing.centreline import Centreline
from fdmr_plugins.river_routing.wave import regular
from fdmr_store import Store

from . import forecast
from .region import load_region

STEP = 900  # s: the published time step, 15 minutes (docs/format.md)
STALE_REACH_S = 13 * 3600  # a linked reach (the Delta, the Bay) older than this, two missed 6-hourly runs, is reported (FDMR's choice)
DAY = 86_400
BLEND_S = DAY  # the gap between forecast and measurement at now closes over a day


def utc(t: float, fmt: str = "%Y-%m-%dT%H:%MZ") -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime(fmt)


def stamp(t: str) -> int:
    """A published time ("2026-11-26T17:45Z") as UTC seconds."""
    return int(datetime.strptime(t, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp())


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
    """The friction slope between each pair of consecutive main gauges, for the
    shear velocity u* = sqrt(g d S) that goes with the depth and velocity used.

    Where both gauges' channel is the region's sourced uniform-flow channel
    (gauge `channel`), their depth and velocity come from its slope, so u* takes
    that slope too (the mean of the two): a measured surface slope there would
    pair a weir pool's flat water with the open river's depth and speed.
    Elsewhere (channel shapes from field measurements), the water-surface slope:
    the drop in (datum + median gauge height) over the distance; a gauge
    reporting its water level against the region's datum itself
    (Rijkswaterstaat's, in m NAP) gives it directly. Missing or implausible
    slopes take a neighbour's, or a default."""
    pairs = list(zip(gauges, gauges[1:]))
    sourced = [(a.channel["slope"] + b.channel["slope"]) / 2 if a.channel and b.channel else None for a, b in pairs]
    if all(s is not None for s in sourced):
        return sourced
    measured = _surface_slopes(gauges, chainage, store, datums, t0, now)
    return [s if s is not None else m for s, m in zip(sourced, measured)]


def _surface_slopes(gauges, chainage, store, datums, t0, now) -> list[float]:
    """Water-surface slope between each pair of consecutive main gauges (see segment_slopes)."""
    def surface(g):
        if g.source == "rws":
            wl = [v.value for v in store.values("rws", g.site, "water_level", t0, now + STEP) if v.value is not None]
            return sorted(wl)[len(wl) // 2] if wl else None
        d = datums.get(g.site)
        if d is None:
            return None
        gh = [v.value for v in store.values(SOURCE, g.site, "gage_height", t0, now + STEP) if v.value is not None]
        return d + sorted(gh)[len(gh) // 2] if gh else None
    z = [surface(g) for g in gauges]
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


ELDER_LATERAL = 0.23  # lateral mixing eps = 0.23 d u*, Elder's, as Fischer 1966 adopts it (ref:fischer1966, eq. 23)


def lateral_field(field, records, slopes):
    """Lateral mixing for every element and step, m2/s: eps = 0.23 d u*
    (Fischer 1966, ref:fischer1966, section II-F), u* = sqrt(g d S), the same
    depth and slope as the dispersion coefficient. With the element's width it
    sets how fast a parcel wanders across the channel (the engine's shear
    spreading, issue #29)."""
    out = [[0.0] * len(field.elements) for _ in range(field.steps)]
    for e, el in enumerate(field.elements):
        k, shape = shape_at(records, el.mid)
        slope = slopes[min(k, len(slopes) - 1)] if slopes else DEFAULT_SLOPE
        for i in range(field.steps):
            d = shape.depth(max(field.flow[i][e], 1e-3))
            out[i][e] = ELDER_LATERAL * d * fdmr_py.shear_velocity(d, slope)
    return out


def bridged(values) -> tuple[list[float] | None, int]:
    """A series with its gaps (None) bridged in a straight line between
    readings and held at the ends, and how many steps were gaps; (None, n)
    with no reading at all. A hindcast's window can start before the first
    reading (27 Nov 2026)."""
    import numpy as np

    vals = [None if v is None else float(v) for v in values]
    known = [i for i, v in enumerate(vals) if v is not None]
    if not known:
        return None, len(vals)
    if len(known) == len(vals):
        return vals, 0
    filled = np.interp(np.arange(len(vals)), known, [vals[i] for i in known])
    return [float(v) for v in filled], len(vals) - len(known)


def storage_field(store: Store, cfg: dict | None, field, t0: int, now: int, n: int, i_now: int, hindcast: bool,
                  assumed: list[dict]):
    """The dead-zone storage ratio for every element and step where the reach
    has groyne fields (issue #27; fdmr_plugins.river_routing.storage): the
    place's beta and velocity head at its gauge's discharge, measured, then
    Rijkswaterstaat's forecast where it has one, then held. None elsewhere."""
    if not cfg:
        return None
    from fdmr_plugins.river_routing import storage

    vals = store.values(cfg["source"], cfg["site"], "discharge", t0, now + STEP)
    if not vals:
        assumed.append({"input": f"{cfg['source']} {cfg['site']} discharge (groyne fields)", "how": "no reading: no storage"})
        return None
    measured = regular([(v.t, v.value) for v in vals], t0, STEP, i_now + 1)
    fc = None
    if cfg["source"] == "rws" and not hindcast:
        fv = store.values("rws", cfg["site"], "discharge_forecast", now - DAY, now + 30 * DAY)
        fc = forecast.on_grid([(v.t, v.value) for v in fv if v.t > now - 3 * 3600], t0, STEP, n) if fv else None
    q, _ = forecast.extend(measured, fc, i_now, n, BLEND_S // STEP)
    assumed.append({"input": f"{cfg['source']} {cfg['site']} discharge (groyne fields)",
                    "how": "Rijkswaterstaat's forecast, joined over a day, then held" if fc is not None else "held at its last value",
                    "last_reading": min(vals[-1].t, now)})
    qs, gaps = bridged(q)
    if qs is None:
        return None
    if gaps:
        assumed.append({"input": f"{cfg['source']} {cfg['site']} discharge (groyne fields)",
                        "how": f"{gaps} of {len(qs)} steps without a reading: bridged between readings, held at the ends"})
    m = len(field.elements)
    return [[storage.ratio(cfg, qs[i])] * m for i in range(field.steps)]


def element_fickian(field, records, slopes) -> list[float]:
    """How far below a release in each element Fischer's dispersion holds, m,
    at the element's median flow: U times six of Fischer's Lagrangian time
    scales, 1.8 U l^2 / (r u*) (Fischer 1966, ref:fischer1966, eq. 57: Taylor's
    theory holds only past t/T = 6). l is the half width, Fischer's length for a
    symmetric channel (an asymmetric one, its fastest water nearer one bank,
    takes longer, up to four times); r is the mean depth, as for K above."""
    out = []
    for e, el in enumerate(field.elements):
        k, shape = shape_at(records, el.mid)
        slope = slopes[min(k, len(slopes) - 1)] if slopes else DEFAULT_SLOPE
        i = sorted(range(field.steps), key=lambda i: field.flow[i][e])[field.steps // 2]
        q = max(field.flow[i][e], 1e-3)
        d, w, u = shape.depth(q), shape.width(q), abs(field.velocity[i][e])
        out.append(round(fdmr_py.fickian_distance(u, w / 2, d, fdmr_py.shear_velocity(d, slope))))
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
        if g.channel:  # no field measurements here: the region's sourced main channel
            c = g.channel
            return channel.wide_channel(c["width_m"], c["slope"], chezy=c.get("chezy"), manning_n=c.get("manning_n"))
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
                  weather_cache: dict | None = None, done: dict | None = None, hindcast: bool = False,
                  problems: list[dict] | None = None) -> tuple[dict, list[int]]:
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
    assumed: list[dict] = []  # how each gauge was carried past its last reading (#22)
    fc_ends: list[int | None] = []  # last forecast step at each gauge that has a forecast
    for g in gauges:
        shape = shapes[g.site]
        vals = store.values(g.source, g.site, g.parameter, t0, now + STEP)
        if not vals:
            raise ValueError(f"gauge {g.site} has no discharge in the store")
        measured = regular([(v.t, v.value) for v in vals], t0, STEP, i_now + 1)
        fc = None
        if g.forecast and g.forecast.get("source") == cnrfc.SOURCE and not hindcast:
            fv = store.values(cnrfc.SOURCE, g.forecast["point"], "discharge", now - DAY, end + DAY)
            fc = forecast.on_grid([(v.t, v.value) for v in fv], t0, STEP, n) if fv else None
        elif g.forecast and g.forecast.get("source") == cnrfc.SOURCE and store.path.name != ":memory:":
            # A hindcast takes the forecast that was in force at its time, if it was kept.
            zb = cnrfc.archived_issue(store.path.parent / "cnrfc-issues", g.forecast["group"], now)
            if zb:
                issue, got = cnrfc.parse(zb, [g.forecast["point"]])
                fv = got.get(g.forecast["point"], [])
                fc = forecast.on_grid([(v.t, v.value) for v in fv], t0, STEP, n) if fv else None
        elif g.forecast and g.forecast.get("source") == "rws" and not hindcast:
            # Rijkswaterstaat's own forecast at the gauge (fdmr_adapters.rws), as fetched.
            fv = store.values("rws", g.site, "discharge_forecast", now - DAY, end + DAY)
            fc = forecast.on_grid([(v.t, v.value) for v in fv if v.t > now - 3 * 3600], t0, STEP, n) if fv else None
        rule_rec = None
        if fc is None and g.role == "release" and g.release_rule and not hindcast:
            from . import releases

            m_now = next((v for v in reversed(measured) if v is not None), None)
            if m_now is not None:
                fc, rule_rec = releases.rule_forecast(store, g.release_rule, m_now, t0, STEP, n, i_now, now)
        flow, fc_end = forecast.extend(measured, fc, i_now, n, BLEND_S // STEP)
        if fc is not None and rule_rec is None:
            fc_ends.append(fc_end)
        last_t = min(vals[-1].t, now)
        if rule_rec is not None:
            how = rule_rec.pop("how") + (f", to {utc(t0 + fc_end * STEP)}, then held" if fc is not None and fc_end is not None else "")
            assumed.append({"input": f"{g.source} {g.site} {g.parameter} ({g.role})", "how": how, "last_reading": last_t, **rule_rec})
            records.append(model.GaugeRecord(chainage[g.site], shape, flow))
            continue
        if fc is None:
            how = "held at its last value" + (" (the reach's top: no forecast or release schedule is published for it)" if g.role == "release" and not g.channel
                                              else " (no forecast is published for this gauge)" if g.channel else "")
        else:
            who = "Rijkswaterstaat's forecast" if g.forecast.get("source") == "rws" else f"River Forecast Center {g.forecast['point']}"
            how = (f"{who}, joined over a day" +
                   (f", to {utc(t0 + fc_end * STEP)}, then held" if fc_end is not None else ", which covers nothing ahead: held"))
        assumed.append({"input": f"{g.source} {g.site} {g.parameter} ({g.role})", "how": how, "last_reading": last_t})
        records.append(model.GaugeRecord(chainage[g.site], shape, flow))

    wave_scale, water_factor = corrections(calibration, reach, gauges)
    field = model.build(geo.get("model_length_m", max(chainage.values())), records, t0, STEP,
                        wave_scale=wave_scale, water_factor=water_factor)
    slopes = segment_slopes(gauges, chainage, store, datums or {}, t0, now)
    dispersion = dispersion_field(field, records, slopes)
    # Parcels spread by the cross-channel shear unless the region chose Fischer's K alone (#29).
    from fdmr_plugins import registry

    chosen = next((r.get("maths") for r in region.reaches if r["id"] == reach), None)
    shear = registry.choices("river-routing", chosen)["dispersion"].name == "cross-channel-shear"
    lateral = lateral_field(field, records, slopes) if shear else None
    storage = storage_field(store, next((r.get("groyne_fields") for r in region.reaches if r["id"] == reach), None),
                            field, t0, now, n, i_now, hindcast, assumed)
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
                                weather_cache if weather_cache is not None else {}, inflows, use_forecast=not hindcast)
        if res is not None:
            temperature, temp_checks = res
            assumed.append({"input": "release temperature", "how": "held at its last 25 h mean past its last reading"})
            assumed.append({"input": "weather (nearest airport's)", "how": "Weather Service forecast joined over a day, then its last day again; air pressure held at its 25 h mean"})
    except Exception as e:  # noqa: BLE001 - the flow is published whatever happens to the temperature
        print(f"{reach}: temperature FAILED {e}", flush=True)
        if problems is not None:
            problems.append({"step": f"{reach} temperature", "error": str(e)[:300]})
    if done is not None:
        import numpy as np

        done[reach] = (np.array([row[-1] for row in field.flow]), None if temperature is None else temperature[:, -1])

    (cdir / reach).mkdir(parents=True, exist_ok=True)
    els = field.elements
    widths = element_widths(field, records)
    fickian = element_fickian(field, records, slopes)
    (cdir / "geometry" / f"{reach}.json").write_text(json.dumps({
        "reach": reach,
        "dims": 1,
        "elements": [{"start_m": round(e.start, 1), "end_m": round(e.end, 1), "width_m": w, "fickian_m": x}
                     for e, w, x in zip(els, widths, fickian)],
        "lines": element_lines(line, els),
        # Each element's form from its line: turn, bending, sinuosity (#27).
        "form": form.forms(line.point_at, [(e.start, e.end) for e in els]),
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
                *([("lateral_mixing", "m2/s", True, flat(lateral))] if lateral is not None else []),
                *([("storage_ratio", "1", True, flat(storage))] if storage is not None else []),
            ],
        )
        if temperature is not None:
            fdmr_py.write_field_file(
                str(cdir / reach / f"{name}.quality.fdf"), f"{reach}:quality", 1, len(els), 1, i1 - i0, d0, STEP,
                [("temperature", "degC", True, temperature[i0:i1].ravel().tolist())],
            )
    issues = [store.latest(cnrfc.SOURCE, g.forecast["point"], "discharge") if g.forecast.get("source") == cnrfc.SOURCE
              else store.latest(g.forecast["source"], g.site, f"{g.parameter}_forecast") for g in gauges if g.forecast]
    entry = {
        "id": reach, "kind": "river-routing", "dims": 1, "elements": len(els), "layers": 1,
        "fields": ["flow", "area", "velocity", "volume", "dispersion", *(["lateral_mixing"] if lateral is not None else []),
                   *(["storage_ratio"] if storage is not None else [])],
        "certainty": certainty,
        **({"quality_fields": ["temperature"], "temperature_checks": temp_checks} if temperature is not None else {}),
        **({"swot_checks": sw} if (sw := swot_checks(region_dir, store, reach, line, els, widths, now)) else {}),
        "assumptions": assumed,
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
                      t0: int, now: int, n: int, weather_cache: dict, inflows: list, use_forecast: bool = True) -> tuple | None:
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
            w, _ = dl.weather(store, zones[z], t0, n, now, use_forecast=use_forecast)
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
    shapes = [shape_at(records, e.mid)[1] for e in els]
    width = np.array([[shapes[j].width(max(float(q[i, j]), 1e-3)) for j in range(len(els))] for i in range(q.shape[0])])
    depth = a / np.clip(width, 1.0, None)

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


class _Skip(Exception):
    """Leaves out a step a hindcast does not take."""


def model_version(region_dir: Path, calibration: dict | None) -> dict:
    """What this cycle ran with, as short fingerprints (#9): the code (every
    module of the Python packages and the compiled engine), the region's
    package (its configs and centrelines), and the calibration. Two cycles
    with the same fingerprints ran the same maths on the same settings."""
    import hashlib

    def digest(files: list[Path], base: Path) -> str:
        h = hashlib.sha256()
        for f in sorted(files):
            h.update(str(f.relative_to(base)).encode())
            h.update(f.read_bytes())
        return h.hexdigest()[:12]

    pkg = Path(__file__).resolve().parents[1]
    code = [f for f in pkg.rglob("*.py") if "tests" not in f.parts]
    if getattr(fdmr_py, "__file__", None):
        code.append(Path(fdmr_py.__file__).resolve())
    code_base = Path(os.path.commonpath([str(f) for f in code]))
    region_files = [f for f in Path(region_dir).rglob("*") if f.is_file()]
    return {"code": digest(code, code_base), "region": digest(region_files, Path(region_dir)),
            "calibration": hashlib.sha256(json.dumps(calibration or {}, sort_keys=True).encode()).hexdigest()[:12]}


def publish(
    region_dir: Path,
    store: Store,
    out_dir: Path,
    days: int,
    reach: str | None = None,
    datums: dict[str, float | None] | None = None,
    ahead: int = 14,
    calibration: dict | None = None,
    extra_reaches: list[Path] | None = None,
    as_of: int | None = None,
    trial: dict | None = None,
) -> Path:
    """Publish a cycle. With `as_of` (a hindcast), the cycle's "now" is that
    time: nothing measured after it is read; river forecasts are the issue in
    force then where it was kept (fdmr_adapters.cnrfc, archived from 26 Nov
    2026), and release rules and weather forecasts hold; its overlays and
    ledger are left to the caller. With `trial` ({"name", "maths": {reach or
    "*": {part: name}}}, issue #26), the reaches run those registered maths in
    place of the region's, and the cycle says so (index.json `trial`)."""
    region = load_region(region_dir)
    if trial:
        from fdmr_plugins import registry

        for r in region.reaches:
            # "*" reaches every reach whose kind has that part; a named reach must have it.
            parts = {**registry.REGISTER.get(r.get("kind"), {}), **registry.REGISTER["all"]}
            every = {p: m for p, m in trial["maths"].get("*", {}).items() if p in parts}
            over = {**every, **trial["maths"].get(r["id"], {})}
            if over and r.get("kind") in registry.REGISTER:
                r["maths"] = {**(r.get("maths") or {}), **over}
                registry.choices(r["kind"], r["maths"])  # an unknown or unregistered choice stops the trial
    if reach is None:  # the main reach: the region's first river-routing reach
        reach = next(r["id"] for r in region.reaches if r.get("kind") == "river-routing")
    gauges, _ = river_chain(region_dir, reach)
    hindcast = as_of is not None

    # The model's "now": the latest time every main gauge of the main river has reported.
    latest = [store.latest(g.source, g.site, g.parameter) for g in gauges]
    if any(x is None for x in latest):
        raise ValueError("a main gauge has no discharge in the store")
    now = (as_of if hindcast else min(t for t, _ in latest)) // STEP * STEP
    t0 = (now - days * DAY) // DAY * DAY
    end = now + ahead * DAY
    n = (end - t0) // STEP + 1

    # Built in a folder of its own and renamed into place when whole: a cycle
    # whose "now" has not moved (a gauge that has not reported since) gets a
    # new folder beside the live one, never written over it (#53).
    cycle = utc(now, "%Y%m%dT%H%MZ")
    cdir = out_dir / f".building-{cycle}-{os.getpid()}"
    if cdir.exists():
        shutil.rmtree(cdir)
    (cdir / "geometry").mkdir(parents=True)

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
    # Everything that failed or was left out this cycle (#55): published in
    # index.json, and the cycle's status is "degraded" if there is any.
    problems: list[dict] = []
    for rid in sorted([reach, *others], key=hops, reverse=True):  # upstream reaches first
        try:
            entry, issued = publish_river(region_dir, region, store, cdir, rid, t0, now, end, ahead, datums, calibration,
                                          weather_cache, done, hindcast=hindcast, problems=problems)
        except Exception as e:  # noqa: BLE001
            if rid == reach:
                raise
            print(f"{rid}: FAILED {e}", flush=True)
            problems.append({"step": f"{rid} (left out of this cycle)", "error": str(e)[:300]})
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
        "model_version": model_version(region_dir, calibration),
        "cycle": utc(now),
        "created": utc(time.time()),
        "time": {"start": utc(t0), "step_s": STEP, "days": day_names},
        "now": utc(now),
        "data_age_s": data_age,
        **({"trial": {"name": trial["name"], "maths": trial["maths"]}} if trial else {}),
        "reaches": entries,
        "status": "ok",
    }
    reach_age: dict[str, int] = {}
    for extra in extra_reaches or []:
        if not (extra / "reach.json").exists():
            problems.append({"step": f"linked reach {extra}", "error": "not published: no reach.json there"})
            continue
        add_reach(cdir, extra, index)
        linked = index["reaches"][-1]
        reach_age[linked["id"]] = age = int(time.time()) - stamp(linked["now"])
        if age > STALE_REACH_S and not hindcast:
            problems.append({"step": f"linked reach {linked['id']}", "error": f"last run {age // 3600} h ago"})
        problems += [{"step": f"{linked['id']} {p['step']}", "error": p["error"]} for p in linked.get("problems", [])]
    if reach_age:
        index["reach_age_s"] = reach_age  # how old each reach published on its own schedule is
    # The maths each reach ran, from the register (fdmr_plugins.registry, #10):
    # a region's unknown choice stops the cycle rather than being swapped.
    from fdmr_plugins import registry

    chosen = {r["id"]: r.get("maths") for r in region.reaches}
    for r in index["reaches"]:
        r["maths"] = registry.record(r["kind"], chosen.get(r["id"]))
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
    if hindcast:
        index["hindcast"] = {"as_of": utc(now), "forecasts": "river forecasts: the River Forecast Center issue in force at the time where kept (kept from 26 Nov 2026), otherwise held; release rules and weather forecasts held"}
    # What is drawn over the water: X2, the stations' readings (fdmr_runner.overlays).
    try:
        if hindcast:
            raise _Skip
        from . import overlays

        ov = overlays.build(region_dir, cdir, index, store, int(time.time()))
        if ov is not None:
            (cdir / "overlays.json").write_text(json.dumps(ov, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            index["overlays"] = "overlays.json"
    except _Skip:
        pass
    except Exception as e:  # the water is published whatever happens to its overlays
        print(f"overlays FAILED {e}", flush=True)
        problems.append({"step": "overlays", "error": str(e)[:300]})
    # The sturgeon forecast (fdmr_runner.fishing, issue #19): the region's
    # published-evidence rules applied to this cycle's water.
    if not hindcast:
        try:
            from . import fishing

            fx = fishing.forecast(region_dir, cdir, index)
            if fx is not None:
                (cdir / "fishing.json").write_text(json.dumps(fx, separators=(",", ":"), allow_nan=False), encoding="utf-8")
                index["fishing"] = "fishing.json"
        except Exception as e:  # the water is published whatever happens to its forecast
            print(f"fishing FAILED {e}", flush=True)
            problems.append({"step": "fishing forecast", "error": str(e)[:300]})
        # The bloom forecast (fdmr_runner.blooms, issue #28), likewise.
        try:
            from . import blooms

            bx = blooms.forecast(region_dir, cdir, index)
            if bx is not None:
                (cdir / "blooms.json").write_text(json.dumps(bx, separators=(",", ":"), allow_nan=False), encoding="utf-8")
                index["blooms"] = "blooms.json"
        except Exception as e:  # the water is published whatever happens to its forecast
            print(f"blooms FAILED {e}", flush=True)
            problems.append({"step": "bloom forecast", "error": str(e)[:300]})
    # The last fetch's own account of what it could not get (fdmr_runner.fetch).
    fs = store.path.parent / f"fetch-status-{index['region']}.json" if store.path.name != ":memory:" else None
    if fs and fs.exists():
        index["fetch_status"] = json.loads(fs.read_text(encoding="utf-8"))
    # The cycle's own steps before this one, as cycle.sh recorded them (runner/schedule).
    cs = store.path.parent / f"cycle-steps-{index['region']}.json" if store.path.name != ":memory:" else None
    if cs and cs.exists() and not hindcast:
        try:
            steps = json.loads(cs.read_text(encoding="utf-8"))
            index["cycle_steps"] = steps
            problems += [{"step": s["step"], "error": "failed"} for s in steps if not s.get("ok")]
        except ValueError as e:
            problems.append({"step": "cycle steps", "error": f"unreadable: {e}"})
    # The prediction ledger (fdmr_runner.ledger): this cycle's predictions kept
    # when due, and every kept prediction scored against what was measured.
    if store.path.name != ":memory:" and not hindcast:
        try:
            from . import ledger

            skill = ledger.run(region_dir, cdir, index, store, store.path.parent / "ledger" / f"{index['region']}.sqlite", int(time.time()))
            (cdir / "skill.json").write_text(json.dumps(skill, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            index["skill"] = "skill.json"
            print(f"ledger: kept {skill['kept_this_cycle']} values; {skill['cycles']} cycles scored", flush=True)
        except Exception as e:  # a record; the water is published whatever happens to it
            print(f"ledger FAILED {e}", flush=True)
            problems.append({"step": "prediction ledger", "error": str(e)[:300]})
    if problems:
        index["problems"] = problems
        index["status"] = "degraded"
    (cdir / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")

    final, k = out_dir / cycle, 1
    while final.exists():  # the same "now" published again: the next revision
        k += 1
        final = out_dir / f"{cycle}.r{k}"
    os.rename(cdir, final)
    tmp = out_dir / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(final.name)
    os.replace(tmp, out_dir / "current")
    return final


CYCLE = re.compile(r"\d{8}T\d{4}Z(\.r\d+)?$")  # a cycle's folder; .r2, .r3: the same "now" published again


def _order(name: str) -> tuple[str, int]:
    base, _, r = name.partition(".r")
    return base, int(r) if r else 1


def prune(out_dir: Path, keep: int) -> list[str]:
    """Remove all but the newest `keep` cycles, never the current one, and any
    folder an interrupted publish left half built over an hour ago. Returns what was removed."""
    current = os.readlink(out_dir / "current") if (out_dir / "current").is_symlink() else None
    cycles = sorted((p.name for p in out_dir.iterdir() if p.is_dir() and not p.is_symlink() and CYCLE.match(p.name)), key=_order)
    gone = [c for c in cycles[: max(0, len(cycles) - keep)] if c != current]
    gone += [p.name for p in out_dir.glob(".building-*") if p.is_dir() and time.time() - p.stat().st_mtime > 3600]
    for c in gone:
        shutil.rmtree(out_dir / c)
    return gone


def held_datums(region, store: Store) -> dict[str, float | None]:
    """Each release and main gauge's datum (m) as fetch kept it in the store
    (fdmr_runner.fetch, #52); None where none is held: no measured slope there."""
    usgs = UsgsAdapter(store)
    return {g.site: usgs.held_datum(g.site)[1] for g in region.gauges if g.role in ("release", "main") and g.source == SOURCE}


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
    ap.add_argument("--as-of", help="a hindcast: publish as of this past time (UTC, 2026-11-21T00:00)")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    region = load_region(args.region)
    store = Store(args.store)
    datums = held_datums(region, store)
    cal_path = Path(args.calibration) if args.calibration else None
    calibration = json.loads(cal_path.read_text(encoding="utf-8")) if cal_path and cal_path.exists() else None
    extra = [Path(p) for p in args.with_reach]  # one not there is a problem the cycle reports (#55)
    cdir = publish(Path(args.region), store, out, args.days, datums=datums, ahead=args.ahead,
                   calibration=calibration, extra_reaches=extra,
                   as_of=int(datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc).timestamp()) if args.as_of else None)
    size = sum(f.stat().st_size for f in cdir.rglob("*") if f.is_file())
    print(f"published {cdir} ({size / 1e6:.1f} MB)")
    gone = prune(out, max(1, args.keep))
    if gone:
        print(f"removed {len(gone)} older cycles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
