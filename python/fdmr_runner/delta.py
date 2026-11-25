"""Run DSM2 for the Delta on live inputs, through the forecast (specification 7.2, 8.2).

    python -m fdmr_runner.delta REGION_DIR STORE_FILE WORK_DIR [--days 28] [--ahead 14] [--as-of 2026-11-22T00:00]

Reads REGION_DIR/dsm2.toml and builds each live input from the store on a
15-minute grid up to now, then carries it on:

- a tide (forecast source noaa-tides): NOAA's predictions, fitted to the
  gauge's last week for timing, size and level;
- a river (forecast source cnrfc): the River Forecast Center's forecast,
  joined to the measured flow over the first day, as the river model does;
- anything else, such as pumping: held at its last value.

Tidal averaging, where an input asks for it, comes after, over the joined series. Gates are held
in their last state. The run covers `--days` before today's midnight (Pacific
Standard Time) and `--ahead` after it.

`--as-of` pretends it is an earlier time: nothing measured after it is used,
and river forecasts are left out (the store keeps only the latest issue, which
would know the answer). DSM2's forecast is then checked against what the
stations measured.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from fdmr_adapters.noaa_tides import PARAMETER as TIDE
from fdmr_plugins.dsm2 import dss, study
from fdmr_plugins.river_routing.calibration import running_mean
from fdmr_plugins.river_routing.model import filled
from fdmr_store import Store

from .forecast import extend, fit_tide, on_grid, shifted

STEP = 900
DAY = 86_400
TO_DSS = {"CFS": 1 / 0.3048**3, "FEET": 1 / 0.3048}  # from the store's SI units
DSM2_ROOT = Path("/srv/fdmr-data/dsm2")
BLEND = 96  # forecasts join the measured series over a day


def load(region_dir: Path) -> dict | None:
    p = region_dir / "dsm2.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def feeds(region_dir: Path) -> list[dict]:
    """Every series the Delta's DSM2 run reads or is checked against, for fetching."""
    cfg = load(region_dir) or {}
    return [*cfg.get("live", []), *cfg.get("live_gate", []), *cfg.get("checks", [])]


def forecasts(region_dir: Path) -> list[dict]:
    """The forecast of every live input that has one, for fetching."""
    return [f["forecast"] for f in (load(region_dir) or {}).get("live", []) if "forecast" in f]


def grid(store: Store, f: dict, t0: int, n: int, cutoff: int | None = None) -> list[float | None]:
    """A series on the 15-minute grid from t0, as measured up to `cutoff`."""
    end = t0 + (n + 1) * STEP if cutoff is None else min(cutoff + 1, t0 + (n + 1) * STEP)
    vals = [v for v in store.values(f["source"], f["site"], f["parameter"], t0 - 3 * DAY, end) if v.value is not None]
    g0 = t0 - 3 * DAY
    m = n + 3 * 96
    if f.get("duration") == "D":
        # A daily figure holds through its day, as in DWR's own inputs.
        full: list[float | None] = [None] * m
        for v in vals:
            first = max(0, (v.t - g0 + STEP - 1) // STEP)
            last = (min(v.t + DAY, end) - g0 + STEP - 1) // STEP
            for i in range(first, min(m, last)):
                full[i] = v.value
    else:
        # Others are bridged across gaps of up to 6 hours.
        full = on_grid([(v.t, v.value) for v in vals], g0, STEP, m, max_gap_s=6 * 3600)
    return full[3 * 96 :]


def carried_on(store: Store, f: dict, t0: int, n: int, now: int, use_river_forecasts: bool) -> tuple[list[float], str]:
    """A live input from t0 for n steps: measured up to now, then its forecast or
    held. Returns the series (SI units) and how it was carried on."""
    g = grid(store, f, t0, n, cutoff=now)
    known = [i for i, v in enumerate(g) if v is not None and i <= (now - t0) // STEP]
    if not known:
        raise ValueError(f"no data for {f['source']} {f['site']} {f['parameter']}")
    i_now = known[-1]
    fc = f.get("forecast")
    how = "held"
    if fc and fc["source"] == "noaa-tides":
        pv = store.values("noaa-tides", fc["station"], TIDE, t0 - DAY, t0 + (n + 1) * STEP)
        pred = on_grid([(v.t, v.value) for v in pv], t0, STEP, n)
        week = max(0, i_now - 7 * 96)
        fit = fit_tide(g[week : i_now + 1], pred[week : i_now + 1], STEP)
        if fit is None:
            raise ValueError(f"cannot fit tide predictions {fc['station']} to {f['site']}")
        shift, scale, offset = fit
        tide = [None if v is None else scale * v + offset for v in shifted(pred, shift)]
        g, _ = extend(g, tide, i_now, n, BLEND)
        how = f"tide {fc['station']}: {shift * STEP / 60:+.0f} min, x{scale:.3f}, {offset:+.3f} m"
    elif fc and fc["source"] == "cnrfc" and use_river_forecasts:
        fv = store.values("cnrfc", fc["point"], "discharge", t0, t0 + (n + 1) * STEP)
        if fv:
            g, last = extend(g, on_grid([(v.t, v.value) for v in fv], t0, STEP, n), i_now, n, BLEND)
            how = f"forecast {fc['point']} to {dss.label(t0 + last * STEP)[0] if last else 'now'}"
    if how == "held":
        g = g[: i_now + 1] + [None] * (n - i_now - 1)
    if f.get("average_h"):
        g = running_mean(g, int(f["average_h"] * 3600 / STEP))
    return filled(g), how


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


def gate_setting(store: Store, g: dict, t0: int, now: int) -> study.LiveSetting:
    """A gate's position from its measured flow up to now, held after."""
    vals = [(v.t, v.value) for v in store.values(g["source"], g["site"], g["parameter"], t0 - 2 * DAY, now + 1) if v.value is not None]
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


def pst_midnight(t: int) -> int:
    return (t - dss.PST) // DAY * DAY + dss.PST


def check(store: Store, run_dir: Path, cfg: dict, c: dict, start: int, end: int) -> dict:
    """DSM2's flow at a check station against the measured one over [start, end):
    means, the moment-to-moment and the tidally averaged (25 h) differences, m3/s."""
    modifier = run_dir.joinpath("config.inp").read_text().split("DSM2MODIFIER", 1)[1].split()[0]
    out = run_dir / "output" / f"{modifier}_hydro.dss"
    n = (end - start) // STEP
    path = f"/{cfg['output_a']}/{c['output']}/FLOW//15MIN/{modifier.upper()}/"
    vals, _, _ = dss.Dss(str(DSM2_ROOT / "bin" / "dsstool")).read(str(out), path, start + STEP, n)
    model = [None if v is None else v / TO_DSS["CFS"] for v in vals]
    meas = grid(store, c, start + STEP, n)
    w = 100  # 25 hours
    tm, tmeas = running_mean(model, w), running_mean(meas, w)
    both = [(a, b) for a, b in zip(model, meas) if a is not None and b is not None]
    tide = [(a, b) for a, b in zip(tm, tmeas) if a is not None and b is not None]
    if not both:
        return {"name": c["name"], "n": 0}
    return {
        "name": c["name"], "hours": round(len(both) / 4),
        "measured_mean": round(mean(b for _, b in both), 1),
        "model_mean": round(mean(a for a, _ in both), 1),
        "instant_mae": round(mean(abs(a - b) for a, b in both), 1),
        "tidal_mae": round(mean(abs(a - b) for a, b in tide), 1) if tide else None,
        "measured_range": round(max(b for _, b in both) - min(b for _, b in both), 1),
    }


def publish_run(run_dir: Path, cfg: dict, region_dir: Path, now: int, end: int, out_root: Path, keep: int = 3) -> Path:
    """Publish a finished run as a network reach (docs/format.md): geometry,
    daily field files from 14 days before now to the end of the forecast, and
    the reach's entry for index.json. Points out_root/latest at it."""
    from fdmr_plugins.dsm2 import fields, grid

    from .region import load_region

    modifier = run_dir.joinpath("config.inp").read_text().split("DSM2MODIFIER", 1)[1].split()[0]
    tidefile = run_dir / "output" / f"{modifier}.h5"
    channels, nodes = grid.load(str(DSM2_ROOT / "inputs" / cfg["grid"]), grid.read_channels(tidefile))
    reach = cfg.get("reach", "delta")
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%MZ")
    pub = out_root / stamp
    (pub / "geometry").mkdir(parents=True, exist_ok=True)
    (pub / "geometry" / f"{reach}.json").write_text(json.dumps(grid.geometry(reach, channels, nodes), separators=(",", ":")), encoding="utf-8")
    start = (now - 14 * DAY) // DAY * DAY
    days = fields.write_days(tidefile, reach, pub / reach, start, end)
    rc = next((r for r in load_region(region_dir).reaches if r["id"] == reach), {}).get("certainty", {})
    solid, floor = rc.get("solid_days", 5) * DAY, rc.get("floor", 0.2)
    import fdmr_py

    utc = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")  # noqa: E731
    steps = (end - start) // STEP
    entry = {
        "id": reach, "kind": "1d-tidal-network", "dims": 1, "network": True,
        "elements": len(channels), "layers": 1, "fields": fields.FIELDS,
        "time": {"start": utc(start), "step_s": STEP, "days": days},
        "now": utc(now),
        "certainty": [round(fdmr_py.certainty_at(start + i * STEP - now, solid, end - now, floor), 4) for i in range(steps)],
        "flow_source": f"dsm2 run {run_dir.name}",
    }
    (pub / "reach.json").write_text(json.dumps(entry, separators=(",", ":")), encoding="utf-8")
    tmp = out_root / ".latest.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(stamp)
    tmp.replace(out_root / "latest")
    # Keep the newest few.
    import shutil

    olds = sorted(p for p in out_root.iterdir() if p.is_dir() and not p.is_symlink() and p.name != stamp)
    for p in olds[: max(0, len(olds) - (keep - 1))]:
        shutil.rmtree(p)
    return pub


def prune_runs(work: Path, keep: int = 2) -> None:
    """Keep the newest few run folders: each holds HYDRO's full output, hundreds of MB."""
    import shutil

    runs = sorted((p for p in work.iterdir() if p.is_dir() and p.name[:2].isdigit()), key=lambda p: p.stat().st_mtime)
    for p in runs[: max(0, len(runs) - keep)]:
        shutil.rmtree(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("work")
    ap.add_argument("--days", type=int, default=28, help="days before today: warm-up plus the past window")
    ap.add_argument("--ahead", type=int, default=14, help="days of forecast")
    ap.add_argument("--as-of", help="pretend it is this UTC time, to check the forecast against what happened")
    ap.add_argument("--publish", help="publish the run as a reach into this folder (its `latest` link)")
    args = ap.parse_args(argv)
    region_dir = Path(args.region)
    cfg = load(region_dir)
    if cfg is None:
        print("no dsm2.toml in this region")
        return 1
    store = Store(args.store)
    hindcast = args.as_of is not None
    now = int(datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc).timestamp()) if hindcast else int(time.time())
    today = pst_midnight(now)
    start, end = today - args.days * DAY, today + args.ahead * DAY
    t0 = start - DAY
    n = (end + DAY - t0) // STEP + 1
    live: list[study.LiveSeries | study.LiveSetting] = []
    for f in cfg["live"]:
        series, how = carried_on(store, f, t0, n, now, use_river_forecasts=not hindcast)
        live.append(study.LiveSeries(f["path"], f["units"], t0, [v * TO_DSS[f["units"]] for v in series]))
        print(f"{f['path']:44s} {f['source']} {f['site']:9s} {how}")
    live += [gate_setting(store, g, t0, now) for g in cfg.get("live_gate", [])]
    root = DSM2_ROOT
    settings = study.RunSettings(
        study=root / "inputs" / cfg["inputs"] / cfg["study"],
        replace=cfg["replace"], version=cfg["version"], version_settings=cfg["version_settings"],
        reference_year=cfg["reference_year"], tool=str(root / "bin" / "dsstool"), hydro=str(root / "bin" / "hydro"),
    )
    run_dir = Path(args.work) / (datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%MZ") + ("-asof" if hindcast else ""))
    summary = study.build(settings, run_dir, start, end, live)
    print(f"run folder {run_dir}: {len(summary['live'])} live inputs, {summary['reference_year']} from {cfg['reference_year']}")
    secs = study.run_hydro(settings, run_dir)
    print(f"hydro: {args.days + args.ahead} days in {secs:.0f} s")
    for c in cfg.get("checks", []):
        print("past    ", json.dumps(check(store, run_dir, cfg, c, start + 7 * DAY, now // STEP * STEP)))
        if hindcast:
            print("forecast", json.dumps(check(store, run_dir, cfg, c, now // STEP * STEP, min(end, int(time.time())) // STEP * STEP)))
    store.close()
    if args.publish and not hindcast:
        pub = publish_run(run_dir, cfg, region_dir, now // STEP * STEP, end, Path(args.publish))
        size = sum(p.stat().st_size for p in pub.rglob("*") if p.is_file())
        print(f"published {pub} ({size / 1e6:.1f} MB)")
        prune_runs(Path(args.work))
    return 0


if __name__ == "__main__":
    sys.exit(main())
