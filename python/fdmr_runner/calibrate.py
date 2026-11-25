"""Nightly river calibration (specification 9.1).

    python -m fdmr_runner.calibrate REGION_DIR STORE_FILE CALIBRATION_DIR [--days 365]

For each segment between main gauges of each river-routing reach:

- wave: every sharp flow change at the upstream gauge that arrives cleanly at
  the next one is a measured wave travel time; their median ratio to the
  model's estimate is the segment's wave scale;
- water: every tracer signal (water temperature, specific conductance) that
  passes two gauges is a measured water travel time; their median ratio to the
  model's estimate is the water factor of every segment between them.

Tonight's values move the stored ones only part of the way (calibration.blend).
Writes CALIBRATION_DIR/<region>.json, which the publisher reads, and appends
the same, with skill scores before and after, to <region>.log.jsonl. Skill
scores are for us; the app does not show them.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from fdmr_adapters.usgs import SOURCE
from fdmr_plugins.river_routing import calibration as cal
from fdmr_plugins.river_routing.wave import measure_events, regular
from fdmr_store import Store

from .publish import fit_shapes, river_chain
from .region import load_region

STEP = 900
TRACERS = {  # parameter: smallest swing in the smoothed signal that counts as a signal
    "specific_conductance": 5.0,  # µS/cm
    "water_temperature": 0.5,  # °C
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def calibrate_reach(region_dir: Path, store: Store, reach: str, days: int, previous: dict) -> dict:
    gauges, chainage = river_chain(region_dir, reach)
    # Whole days ending at the last midnight (UTC), so the analysis windows line
    # up the same way every night and nights can be compared.
    now = int(time.time()) // 86_400 * 86_400
    t0 = now - days * 86_400
    n = (now - t0) // STEP
    shapes = fit_shapes(gauges, chainage, store)
    stations = [cal.Station(chainage[g.site], shapes[g.site]) for g in gauges]
    flow = {g.site: regular([(v.t, v.value) for v in store.values(SOURCE, g.site, "discharge", t0, now)], t0, STEP, n) for g in gauges}
    prev = {(s["up"], s["down"]): s for s in previous.get("segments", [])}
    factors_before = [prev.get((a.site, b.site), {}).get("water_factor", 1.0) for a, b in zip(gauges, gauges[1:])]
    segs = []
    wave_skill = []
    for k, (g1, g2) in enumerate(zip(gauges, gauges[1:])):
        old = prev.get((g1.site, g2.site), {})
        events = measure_events(flow[g1.site], flow[g2.site], t0, STEP)
        default = [cal.wave_travel(stations, k, e.flow, factors_before) for e in events]
        new_scale = cal.scale([e.lag / d for e, d in zip(events, default)])
        scale = cal.blend(old.get("wave_scale"), new_scale) or 1.0
        before = old.get("wave_scale", 1.0)
        wave_skill.append({
            "up": g1.site, "down": g2.site,
            "before": cal.skill([(d * before, e.lag) for e, d in zip(events, default)]),
            "after": cal.skill([(d * scale, e.lag) for e, d in zip(events, default)]),
        })
        ratios = [e.lag / d for e, d in zip(events, default)]
        sp = cal.spread(ratios)
        segs.append({
            "up": g1.site, "down": g2.site,
            "length_m": round(chainage[g2.site] - chainage[g1.site]),
            "wave_scale": round(scale, 4),
            "water_factor": factors_before[k],
            "wave_events": len(events),
            "wave_spread": None if sp is None else round(sp, 3),
            "wave_used": new_scale is not None,
        })

    # Tracers: consecutive gauges among those that record each one.
    estimates: dict[int, list[float]] = {}
    tracer_skill = []
    for param, swing in TRACERS.items():
        have = [g for g in gauges if store.coverage(SOURCE, g.site, param)]
        for g1, g2 in zip(have, have[1:]):
            x0, x1 = chainage[g1.site], chainage[g2.site]
            up = regular([(v.t, v.value) for v in store.values(SOURCE, g1.site, param, t0, now)], t0, STEP, n)
            down = regular([(v.t, v.value) for v in store.values(SOURCE, g2.site, param, t0, now)], t0, STEP, n)
            predict = lambda q, f=factors_before: cal.water_travel(stations, x0, x1, q, f)  # noqa: E731
            events = cal.tracer_events(up, down, flow[g1.site], t0, STEP, predict, min_swing=swing)
            pred = [predict(e.flow) for e in events]
            ratio = cal.scale([p / e.lag for e, p in zip(events, pred)])  # >1: the water is faster than modelled
            spanned = [k for k in range(len(segs)) if x0 <= stations[k].chainage < x1]
            if ratio is not None:
                for k in spanned:
                    estimates.setdefault(k, []).append(factors_before[k] * ratio)
            sp = cal.spread([p / e.lag for e, p in zip(events, pred)])
            tracer_skill.append({
                "tracer": param, "up": g1.site, "down": g2.site,
                "spread": None if sp is None else round(sp, 3), "used": ratio is not None,
                "before": cal.skill([(p, e.lag) for e, p in zip(events, pred)]),
                "after": cal.skill([(p / ratio if ratio else p, e.lag) for e, p in zip(events, pred)]),
            })
    for k, est in estimates.items():
        new = min(cal.SCALE_LIMITS[1], max(cal.SCALE_LIMITS[0], sum(est) / len(est)))
        segs[k]["water_factor"] = round(cal.blend(factors_before[k], new), 4)
    return {"reach": reach, "segments": segs, "skill": {"wave": wave_skill, "tracer": tracer_skill}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=365)
    args = ap.parse_args(argv)
    region_dir, out = Path(args.region), Path(args.out)
    region = load_region(region_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{region.id}.json"
    previous = load(path)
    store = Store(args.store)
    reaches = []
    for r in region.reaches:
        if r.get("kind") != "river-routing":
            continue
        prev = next((x for x in previous.get("reaches", []) if x["reach"] == r["id"]), {})
        reaches.append(calibrate_reach(region_dir, store, r["id"], args.days, prev))
    store.close()
    result = {
        "region": region.id,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "days": args.days,
        "reaches": reaches,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=1), encoding="utf-8")
    tmp.replace(path)
    with (out / f"{region.id}.log.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(result, separators=(",", ":")) + "\n")
    for r in reaches:
        for s, w in zip(r["segments"], r["skill"]["wave"]):
            print(f"{r['reach']} {s['up']}->{s['down']}: wave scale {s['wave_scale']} from {s['wave_events']} changes"
                  f" (spread {s['wave_spread']}, {'used' if s['wave_used'] else 'not used'}),"
                  f" error {w['before'].get('mae_h', '-')} h -> {w['after'].get('mae_h', '-')} h; water factor {s['water_factor']}")
        for t in r["skill"]["tracer"]:
            print(f"{r['reach']} {t['tracer']} {t['up']}->{t['down']}: {t['before']['n']} signals, spread {t['spread']},"
                  f" {'used' if t['used'] else 'not used'}; error {t['before'].get('mae_h', '-')} h -> {t['after'].get('mae_h', '-')} h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
