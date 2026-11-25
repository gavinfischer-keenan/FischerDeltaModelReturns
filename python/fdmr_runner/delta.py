"""Run DSM2 for the Delta on live inputs (specification 7.2).

    python -m fdmr_runner.delta REGION_DIR STORE_FILE WORK_DIR [--days 28]

Reads REGION_DIR/dsm2.toml, builds each live series from the store (15-minute,
tidally averaged where asked, short gaps bridged, the edges held), writes a run
folder under WORK_DIR, runs HYDRO, and compares DSM2's flows with the measured
check stations. For now the run ends at the last midnight (Pacific Standard
Time) that every live input reaches: the forecast comes next.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from statistics import mean

from fdmr_plugins.dsm2 import dss, study
from fdmr_plugins.river_routing.calibration import running_mean
from fdmr_plugins.river_routing.model import filled
from fdmr_store import Store

from .forecast import on_grid

STEP = 900
DAY = 86_400
TO_DSS = {"CFS": 1 / 0.3048**3, "FEET": 1 / 0.3048}  # from the store's SI units
DSM2_ROOT = Path("/srv/fdmr-data/dsm2")


def load(region_dir: Path) -> dict | None:
    p = region_dir / "dsm2.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def feeds(region_dir: Path) -> list[dict]:
    """Every series the Delta's DSM2 run reads or is checked against, for fetching."""
    cfg = load(region_dir) or {}
    return [*cfg.get("live", []), *cfg.get("live_gate", []), *cfg.get("checks", [])]


def grid(store: Store, f: dict, t0: int, n: int, average_h: float | None = None) -> list[float | None]:
    vals = store.values(f["source"], f["site"], f["parameter"], t0 - 3 * DAY, t0 + (n + 4 * 96) * STEP)
    g0 = t0 - 3 * DAY
    m = n + 3 * 96 + 4 * 96
    if f.get("duration") == "D":
        # A daily figure holds through its day, as in DWR's own inputs.
        full: list[float | None] = [None] * m
        for v in vals:
            if v.value is None:
                continue
            first = max(0, (v.t - g0 + STEP - 1) // STEP)
            for i in range(first, min(m, (v.t + DAY - g0 + STEP - 1) // STEP)):
                full[i] = v.value
    else:
        # Others are bridged across gaps of up to 6 hours.
        full = on_grid([(v.t, v.value) for v in vals], g0, STEP, m, max_gap_s=6 * 3600)
    if average_h:
        full = running_mean(full, int(average_h * 3600 / STEP))
    return full[3 * 96 : 3 * 96 + n]


def live_series(store: Store, f: dict, t0: int, n: int) -> study.LiveSeries:
    g = grid(store, f, t0, n, f.get("average_h"))
    if all(v is None for v in g):
        raise ValueError(f"no data for {f['source']} {f['site']} {f['parameter']}")
    k = TO_DSS[f["units"]]
    return study.LiveSeries(f["path"], f["units"], t0, [v * k for v in filled(g)])


def gate_states(readings: list[tuple[int, float]], open_above: float, confirm: int = 2) -> list[tuple[int, bool]]:
    """Open or closed from measured flow through a gate: open while |flow| is at
    least `open_above`. A change counts once `confirm` readings in a row agree,
    from the first of them, so gauge noise cannot flip it."""
    out: list[tuple[int, bool]] = []
    run: list[tuple[int, bool]] = []
    for t, q in readings:
        s = abs(q) >= open_above
        if not out:
            out.append((t, s))
            continue
        if s == out[-1][1]:
            run = []
            continue
        run.append((t, s))
        if len(run) >= confirm:
            out.append(run[0])
            run = []
    return out


def gate_setting(store: Store, g: dict, t0: int, t1: int) -> study.LiveSetting:
    vals = [(v.t, v.value) for v in store.values(g["source"], g["site"], g["parameter"], t0 - 2 * DAY, t1) if v.value is not None]
    states = gate_states(vals, g["open_above"])
    if not states:
        raise ValueError(f"no data for gate feed {g['source']} {g['site']}")
    before = [s for t, s in states if t <= t0]
    first = before[-1] if before else states[0][1]
    pts = [(t0, g["open"] if first else g["closed"])]
    for t, s in states:
        if t > t0 and (g["open"] if s else g["closed"]) != pts[-1][1]:
            pts.append((t, g["open"] if s else g["closed"]))
    return study.LiveSetting(g["path"], g.get("units", "UNSPECIF"), pts)


def latest_midnight(store: Store, live: list[dict]) -> int:
    """The last midnight, Pacific Standard Time, that every live input has reached."""
    last = min(store.latest(f["source"], f["site"], f["parameter"])[0] for f in live)
    return (last - dss.PST) // DAY * DAY + dss.PST


def check(store: Store, run_dir: Path, cfg: dict, c: dict, start: int, end: int) -> dict:
    """DSM2's flow at a check station against the measured one: tidal (25 h mean)
    and instantaneous differences, m3/s."""
    modifier = run_dir.joinpath("config.inp").read_text().split("DSM2MODIFIER", 1)[1].split()[0]
    out = run_dir / "output" / f"{modifier}_hydro.dss"
    n = (end - start) // STEP
    path = f"/{cfg['output_a']}/{c['output']}/FLOW//15MIN/{modifier.upper()}/"
    vals, _, _ = dss.Dss(str(DSM2_ROOT / "bin" / "dsstool")).read(str(out), path, start + STEP, n)
    model = [None if v is None else v / TO_DSS["CFS"] for v in vals]
    meas = grid(store, c, start + STEP, n)
    w = 100  # 25 hours of 15-minute steps
    tm, tmeas = running_mean(model, w), running_mean(meas, w)
    both = [(a, b) for a, b in zip(model, meas) if a is not None and b is not None]
    tide = [(a, b) for a, b in zip(tm, tmeas) if a is not None and b is not None]
    if not both:
        return {"name": c["name"], "n": 0}
    return {
        "name": c["name"], "n": len(both),
        "measured_mean": round(mean(b for _, b in both), 1),
        "model_mean": round(mean(a for a, _ in both), 1),
        "instant_mae": round(mean(abs(a - b) for a, b in both), 1),
        "tidal_mae": round(mean(abs(a - b) for a, b in tide), 1) if tide else None,
        "measured_range": round(max(b for _, b in both) - min(b for _, b in both), 1),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("work")
    ap.add_argument("--days", type=int, default=28, help="length of the run, days (warm-up plus the past window)")
    args = ap.parse_args(argv)
    region_dir = Path(args.region)
    cfg = load(region_dir)
    if cfg is None:
        print("no dsm2.toml in this region")
        return 1
    store = Store(args.store)
    end = latest_midnight(store, cfg["live"])
    start = end - args.days * DAY
    t0 = start - DAY
    n = (end + DAY - t0) // STEP + 1
    live = [live_series(store, f, t0, n) for f in cfg["live"]]
    live += [gate_setting(store, g, t0, end + DAY) for g in cfg.get("live_gate", [])]
    root = DSM2_ROOT
    settings = study.RunSettings(
        study=root / "inputs" / cfg["inputs"] / cfg["study"],
        replace=cfg["replace"], version=cfg["version"], version_settings=cfg["version_settings"],
        reference_year=cfg["reference_year"], tool=str(root / "bin" / "dsstool"), hydro=str(root / "bin" / "hydro"),
    )
    run_dir = Path(args.work) / dss.label(end)[0].lower()
    summary = study.build(settings, run_dir, start, end, live)
    print(f"run folder {run_dir}: {len(summary['live'])} live series, {summary['reference_year']} from {cfg['reference_year']}")
    secs = study.run_hydro(settings, run_dir)
    print(f"hydro: {args.days} days in {secs:.0f} s")
    results = [check(store, run_dir, cfg, c, start + 7 * DAY, end) for c in cfg.get("checks", [])]
    for r in results:
        print(json.dumps(r))
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
