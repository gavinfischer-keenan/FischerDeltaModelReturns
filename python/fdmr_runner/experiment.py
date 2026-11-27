"""Experiments (issue #33): a past intervention in the Delta, such as a
barrier or a gate operation, run on DWR's historical inputs once as it was and
once with the intervention taken away (regions/<region>/experiments.toml).
"The model said X would happen; they did it; Y happened."

Each scenario is DWR's historical study, unchanged except for its dates and,
for a scenario with an `override`, the run's own copy of DWR's gate file, in
which the named settings hold the given value over the given days. HYDRO and
QUAL run as in the live cycle (fdmr_runner.delta). From their station output:

- salinity (EC, uS/cm): each day's mean of QUAL's 15-minute values;
- net flow (m3/s): HYDRO's 15-minute flow through Godin's tidal filter
  (ref:godin1972), then each day's mean, as the agencies filter their gauges.

A day counts when it has 18 hours of values. Each scenario is scored against
the measured daily means over the judged days and over the days the
intervention was in place: mean, mean difference (model less measured) and mean
absolute difference. The intervention's effect, as the model sees it, is the
scenario without it less the scenario as it was, day by day; where the package
holds another model's runs with and without (DWR's SCHISM), its effect is
shown beside ours.

    python -m fdmr_runner.experiment REGION WORK OUT [--only NAME] [--rescore] [--lock FILE]

WORK holds each scenario's run folder, its daily series (daily.json) and the
record of its run (run.json: when, how long, the code's and the region's
fingerprints as the live cycle records them, DWR's input set and the DSM2
build); a scenario with a daily.json is not run again unless --rerun. OUT gets
<name>.json, with every run's record, and index.json (served as
data/<region>/experiments/). This is part of the model's validation; the
results are written up in tools/validation/experiments/results.md.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import shutil
import sys
import time
import tomllib
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fdmr_plugins.dsm2 import dss, inp, study
from fdmr_plugins.fvcom.correct import tidal_mean

from .delta import DSM2_ROOT, TO_DSS
from .delta import load as load_dsm2
from .publish import model_version

DAY = 86_400
STEP = 900  # s: DWR's station output, 15 minutes
MIN_STEPS = 72  # of 96 a day: 18 hours


def load(region_dir: Path) -> list[dict]:
    p = region_dir / "experiments.toml"
    return tomllib.load(open(p, "rb")).get("experiment", []) if p.exists() else []


def midnight(date: str) -> int:
    """UTC time of a date's midnight, Pacific Standard Time (DSM2's clock)."""
    return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()) + dss.PST


def day_of(t: int) -> str:
    return datetime.fromtimestamp(t - dss.PST, timezone.utc).strftime("%Y-%m-%d")


def overridden(points: list[tuple[int, float]], value: float, a: int, b: int | None) -> list[tuple[int, float]]:
    """An irregular setting with `value` in force from a to b (b None: to the
    end); at b the value the original had in force there comes back."""
    out = [(t, v) for t, v in points if t < a]
    out.append((a, value))
    if b is not None:
        at_b = [v for t, v in points if t <= b]
        if at_b:
            out.append((b, at_b[-1]))
        out += [(t, v) for t, v in points if t > b]
    return out


def in_run(points: list[tuple[int, float]], t0: int, t1: int) -> list[tuple[int, float]]:
    """The value in force at t0, then every change to t1."""
    before = [v for t, v in points if t <= t0]
    head = [(t0, before[-1])] if before else []
    return head + [(t, v) for t, v in points if t0 < t <= t1]


def daily(values: list[float | None], first: int, step: int = STEP, min_n: int = MIN_STEPS) -> dict[str, float]:
    """Each PST day's mean of a regular series whose first value is stamped at
    `first` (a value stamped t covers the step ending at t)."""
    s: dict[str, float] = defaultdict(float)
    n: dict[str, int] = defaultdict(int)
    for i, v in enumerate(values):
        if v is not None and np.isfinite(v):
            d = day_of(first + i * step - step)
            s[d] += v
            n[d] += 1
    return {d: s[d] / n[d] for d in s if n[d] >= min_n}


def subtidal(values: list[float | None]) -> list[float | None]:
    x = np.array([np.nan if v is None else v for v in values], dtype=float)
    m = tidal_mean(x, 3600 / STEP)
    return [None if not np.isfinite(v) else float(v) for v in m]


def compare(model: dict[str, float], measured: dict[str, float], lo: str, hi: str) -> dict:
    """Model against measured daily means over the days [lo, hi)."""
    days = [d for d in sorted(measured) if lo <= d < hi and d in model]
    if not days:
        return {"days": 0}
    m = np.array([model[d] for d in days])
    g = np.array([measured[d] for d in days])
    return {"days": len(days), "measured_mean": round(float(g.mean()), 1), "model_mean": round(float(m.mean()), 1),
            "mean_difference": round(float((m - g).mean()), 1), "mean_abs_difference": round(float(np.abs(m - g).mean()), 1)}


def effect(without: dict[str, float], with_: dict[str, float], lo: str, hi: str) -> dict:
    days = [d for d in sorted(with_) if lo <= d < hi and d in without]
    if not days:
        return {"days": 0}
    e = np.array([without[d] - with_[d] for d in days])
    return {"days": len(days), "mean": round(float(e.mean()), 1), "largest": round(float(e[np.abs(e).argmax()]), 1)}


def monthly(series: dict[str, float], lo: str, hi: str) -> dict[str, float]:
    s: dict[str, list[float]] = defaultdict(list)
    for d, v in series.items():
        if lo <= d < hi:
            s[d[:7]].append(v)
    return {m: round(float(np.mean(v)), 1) for m, v in sorted(s.items()) if len(v) >= 15}


def read_table(path: Path, key_col: str) -> dict[tuple[str, str], dict[str, float]]:
    """{(station, key): {date: value}} from a measured.csv (key: quantity) or schism.csv (key: scenario)."""
    out: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[(r["station"], r[key_col])][r["date"]] = float(r.get("value") or r.get("ec"))
    return out


@contextmanager
def held(lock: str | None):
    if not lock:
        yield
        return
    with open(lock, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def settings_for(cfg: dict) -> study.RunSettings:
    root = DSM2_ROOT
    return study.RunSettings(
        study=root / "inputs" / cfg["inputs"] / cfg["study"], replace=[], version=cfg["version"], version_settings=[],
        reference_year=cfg["reference_year"], tool=str(root / "bin" / "dsstool"), hydro=str(root / "bin" / "hydro"),
        qual=str(root / "bin" / "qual"), mains=["hydro.inp", "qual_ec.inp"],
    )


def run_scenario(cfg: dict, exp: dict, scen: dict, run_dir: Path, lock: str | None) -> dict[str, dict[str, float]]:
    """Run one scenario; returns {"<station>/<quantity>": {date: daily mean}}."""
    s = settings_for(cfg)
    start, end = midnight(exp["run_from"]), midnight(exp["judge_to"]) + DAY
    if run_dir.exists():
        shutil.rmtree(run_dir)
    summary = study.build(s, run_dir, start, end, [])
    config = run_dir / "config.inp"
    # Not /tmp, which is memory on the runner; and short: QUAL failed to read
    # the run folder's own tmp (77 characters) as its temp_dir (27 Nov 2026).
    tmp = DSM2_ROOT.parent / "qt"
    tmp.mkdir(exist_ok=True)
    new = {"TEMPDIR": str(tmp)}
    if scen.get("override"):
        src = Path(inp.expand("${GATEFILE}", summary["env"]))
        gates = run_dir / "gates.dss"
        shutil.copyfile(src, gates)
        d = dss.Dss(s.tool)
        t0, t1 = start - DAY, end + DAY
        for o in scen["override"]:
            pts, units, kind = d.read_irregular(str(gates), o["path"], midnight("1980-01-01"), t1)
            a = midnight(o["from"]) if "from" in o else t0
            b = midnight(o["to"]) if "to" in o else None
            new_pts = in_run(overridden(pts, float(o["value"]), a, b), t0, t1)
            # DWR's records are in a form dsstool's write garbles: delete the
            # run's blocks, then write the run's whole series afresh (27 Nov 2026).
            for block in dss.irregular_blocks(o["path"], t0, t1):
                d.delete(str(gates), block)
            written = study.monthly(new_pts, t1)
            d.write_irregular(str(gates), o["path"], written, units or "UNSPECIF", kind or "INST-VAL")
            back, _, _ = d.read_irregular(str(gates), o["path"], t0, t1)
            if [(t, round(v, 4)) for t, v in back] != [(t, round(v, 4)) for t, v in written if t <= t1]:
                raise RuntimeError(f"{o['path']}: the run's gate file does not read back as written")
            print(f"  {o['path']} = {o['value']} from {o.get('from', 'the start')} to {o.get('to', 'the end')}", flush=True)
        new["GATEFILE"] = str(gates)
    config.write_text(inp.set_envvars(config.read_text(encoding="utf-8"), new), encoding="utf-8")
    with held(lock):
        secs = study.run_hydro(s, run_dir)
        print(f"  hydro {secs:.0f} s", flush=True)
        secs = study.run_qual(s, run_dir, "qual_ec.inp")
        print(f"  qual {secs:.0f} s", flush=True)
    return read_stations(cfg, exp, run_dir, start, end)


def read_stations(cfg: dict, exp: dict, run_dir: Path, start: int, end: int) -> dict[str, dict[str, float]]:
    modifier = (run_dir / "config.inp").read_text().split("DSM2MODIFIER", 1)[1].split()[0]
    d = dss.Dss(str(DSM2_ROOT / "bin" / "dsstool"))
    first = start + DAY + STEP  # QUAL starts a day after HYDRO
    n = (end - first) // STEP
    out: dict[str, dict[str, float]] = {}
    for st in exp["station"]:
        name = st.get("output", st["name"])
        if st["quantity"] == "ec":
            f = run_dir / "output" / f"{modifier}_qual.dss"
            path = f"/{cfg['output_a'].replace('HYDRO', 'QUAL')}/{name}/EC//15MIN/{modifier.upper()}+FROM-ALL/"
            scale = 1.0
        else:
            f = run_dir / "output" / f"{modifier}_hydro.dss"
            path = f"/{cfg['output_a']}/{name}/FLOW//15MIN/{modifier.upper()}/"
            scale = 1 / TO_DSS["CFS"]
        try:
            vals, _, _ = d.read(str(f), path, first, n)
        except Exception as e:  # a station the study does not write out
            print(f"  {path}: not read ({e})", file=sys.stderr)
            continue
        vals = [None if v is None else v * scale for v in vals]
        if st["quantity"] == "net_flow":
            vals = subtidal(vals)
        out[f"{st['name']}/{st['quantity']}"] = {k: round(v, 3) for k, v in daily(vals, first).items()}
    return out


def score(exp: dict, series: dict[str, dict[str, dict[str, float]]], measured, other) -> dict:
    """The experiment's published result: per station, each scenario against
    the measured, the effect (without less as built) beside another model's."""
    jl, jh, el, eh = exp["judge_from"], exp["judge_to"], exp["effect_from"], exp["effect_to"]
    base = next(s["name"] for s in exp["scenario"] if not s.get("without"))
    without = next(s["name"] for s in exp["scenario"] if s.get("without"))
    om = exp.get("other_model")
    stations = []
    for st in exp["station"]:
        key = f"{st['name']}/{st['quantity']}"
        meas = measured.get((st["name"], st["quantity"]), {})
        row = {"station": st["name"], "place": st["place"], "quantity": st["quantity"],
               "unit": "uS/cm" if st["quantity"] == "ec" else "m3/s", "scenarios": {}}
        for sc in exp["scenario"]:
            m = series.get(sc["name"], {}).get(key)
            if m is None:
                continue
            row["scenarios"][sc["name"]] = {"judged": compare(m, meas, jl, jh), "in_place": compare(m, meas, el, eh)}
        a, b = series.get(base, {}).get(key), series.get(without, {}).get(key)
        if a and b:
            row["effect"] = effect(b, a, el, eh)
        months = {"measured": monthly(meas, jl, jh)}
        for sc in exp["scenario"]:
            if key in series.get(sc["name"], {}):
                months[sc["name"]] = monthly(series[sc["name"]][key], jl, jh)
        if om and st["quantity"] == "ec":
            w, wo = other.get((st["name"], om["with"])), other.get((st["name"], om["without"]))
            if w and wo:
                row["other_model"] = {"effect": effect(wo, w, el, eh), "with": compare(w, meas, el, eh),
                                      "without": compare(wo, meas, el, eh)}
                months[f"{om['name']}: {om['with']}"] = monthly(w, jl, jh)
                months[f"{om['name']}: {om['without']}"] = monthly(wo, jl, jh)
        row["monthly"] = months
        ip = row["scenarios"]
        if base in ip and without in ip and ip[base]["in_place"].get("days") and ip[without]["in_place"].get("days"):
            row["closer"] = base if ip[base]["in_place"]["mean_abs_difference"] <= ip[without]["in_place"]["mean_abs_difference"] else without
        stations.append(row)
    return {"name": exp["name"], "title": exp["title"], "what": exp["what"], "refs": exp.get("refs", []),
            "run_from": exp["run_from"], "judged": [jl, jh], "in_place": [el, eh],
            "scenarios": [{"name": s["name"], "label": s["label"], "override": s.get("override", [])} for s in exp["scenario"]],
            "as_built": base, "without": without, "other_model": om["name"] if om else None, "stations": stations}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("work")
    ap.add_argument("out")
    ap.add_argument("--only", help="one experiment by name")
    ap.add_argument("--rerun", action="store_true", help="run scenarios again even if their daily series exist")
    ap.add_argument("--rescore", action="store_true", help="only score the daily series already in WORK")
    ap.add_argument("--keep", action="store_true", help="keep DSM2's run folders (they are large)")
    ap.add_argument("--lock", help="hold this lock while DSM2 runs (the cycle's lock)")
    args = ap.parse_args(argv)
    region_dir, work, out = Path(args.region), Path(args.work), Path(args.out)
    cfg = load_dsm2(region_dir)
    exps = [e for e in load(region_dir) if not args.only or e["name"] == args.only]
    out.mkdir(parents=True, exist_ok=True)
    for exp in exps:
        print(f"{exp['name']}: {exp['title']}", flush=True)
        series, runs = {}, []
        for sc in exp["scenario"]:
            folder = work / exp["name"] / sc["name"]
            daily_file, record = folder / "daily.json", folder / "run.json"
            if not (daily_file.exists() and not args.rerun) and not args.rescore:
                print(f" {sc['name']}: {sc['label']}", flush=True)
                t = time.time()
                started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
                result = run_scenario(cfg, exp, sc, folder / "run", args.lock)
                daily_file.write_text(json.dumps(result))
                record.write_text(json.dumps({"scenario": sc["name"], "label": sc["label"], "started": started,
                                              "seconds": round(time.time() - t), "model_version": model_version(region_dir, None),
                                              "inputs": cfg["inputs"], "dsm2": cfg["output_a"],
                                              "period": [exp["run_from"], exp["judge_to"]], "override": sc.get("override", [])}))
                if not args.keep:
                    shutil.rmtree(folder / "run")
                print(f"  done in {time.time() - t:.0f} s: {len(result)} station series", flush=True)
            if daily_file.exists():
                series[sc["name"]] = json.loads(daily_file.read_text())
                runs.append(json.loads(record.read_text()) if record.exists() else {"scenario": sc["name"], "label": sc["label"]})
        exp_dir = region_dir / "experiments" / exp["name"]
        measured = read_table(exp_dir / "measured.csv", "quantity")
        other = read_table(exp_dir / exp["other_model"]["file"], "scenario") if exp.get("other_model") else {}
        result = score(exp, series, measured, other)
        result["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        result["inputs"] = cfg["inputs"]
        result["runs"] = runs
        (out / f"{exp['name']}.json").write_text(json.dumps(result, indent=1))
    names = [p.stem for p in sorted(out.glob("*.json")) if p.stem != "index"]
    index = [{"name": n, "title": json.loads((out / f"{n}.json").read_text())["title"]} for n in names]
    (out / "index.json").write_text(json.dumps({"experiments": index}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
