"""A DSM2 run on live inputs, built from one of DWR's studies (specification 7.2).

DWR's input files are used unchanged. The run gets its own config.inp, with
the run's dates, and with each replaced DSS file (BNDRYINPUT and the like)
pointing at a fresh file in the run folder. That file holds:

- our live series, written under DWR's paths with our own version as F part;
- every other series DWR's inputs read from it, copied from DWR's file for the
  same calendar days of a reference year (DWR's average-year practice for
  inputs nobody measures live, such as island water use).

Island water use can follow a live daily total (`Depletion`): the reference
year's per-node series keep their pattern, and each day the difference
between their net depletion (uses less returns) and the live total is added
to the returns where the live day is wetter, or to the uses where it is
drier. The day's total is then the live one exactly. Where it is wetter, the
extra is rain running off, so it goes to each node in proportion to that
node's returns in the reference year's rain season (December to March), when
returns are runoff; in the irrigation season they are drainage from the
farms, gathered in a few places. Where it is drier, the extra use goes to
each node in proportion to its uses over the run.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import dss, inp


@dataclass(frozen=True)
class LiveSeries:
    path: str  # A to E parts, as "/A/B/C//E/" (F is the run's version)
    units: str  # DSS units, such as CFS or FEET
    start: int  # UTC time of the first value: one day before the run starts
    values: Sequence[float]  # every interval of the path's E part to a day after the run, in `units`


@dataclass(frozen=True)
class LiveSetting:
    """A live setting that changes now and then, such as a gate's position,
    written as an irregular series under DWR's path."""

    path: str  # A to E parts
    units: str
    points: Sequence[tuple[int, float]]  # (UTC time, value), from at or before the run's padded start


RAIN_SEASON = (12, 1, 2, 3)  # months whose island returns are rain running off


@dataclass(frozen=True)
class Depletion:
    """A live daily total of island water use (net channel depletion: water
    taken from the channels less water returned to them) for the series in
    one replaced file."""

    file: str  # the replaced setting holding the per-node series, such as DICUFILE
    use: Sequence[str]  # C parts of water taken from the channels, such as DIV-FLOW
    returns: Sequence[str]  # C parts of water returned to them, such as DRAIN-FLOW
    days: dict[int, float]  # day's start (UTC s, a midnight PST) -> net depletion, in the series' units
    add_use: str = ""  # which `use` part takes added use (default the first)
    wetter: bool = True  # follow the live total where it is below the reference (rain); False keeps the reference there


def adjust_depletion(series: dict[str, list[float | None]], kinds: dict[str, str], use: Sequence[str],
                     returns: Sequence[str], targets: list[float | None], add_use: str = "",
                     return_shares: dict[str, float] | None = None, wetter: bool = True) -> tuple[dict[str, list[float | None]], list[float]]:
    """Per-node daily series made to total `targets` (see the module's note).
    `series`: path -> values, all the same days; `kinds`: path -> its C part.
    `return_shares`: how added returns are shared (path -> weight; by default
    each return's mean over `series`). `wetter` False: a day whose target is
    below the reference's net (wetter) is left as the reference.
    A None target leaves the day as it is; a missing value is left missing and
    takes no share. Returns the new series and each day's change to the net
    depletion (target less reference; 0 where kept)."""
    import numpy as np

    paths = list(series)
    n = len(targets)
    arr = {p: np.array([np.nan if v is None else v for v in series[p]], dtype=float) for p in paths}
    sign = {p: (1.0 if kinds[p] in use else -1.0 if kinds[p] in returns else 0.0) for p in paths}
    ref = np.zeros(n)
    for p in paths:
        ref += sign[p] * np.nan_to_num(arr[p][:n])
    ret = [p for p in paths if kinds[p] in returns]
    tak = [p for p in paths if kinds[p] == (add_use or use[0])]

    def shares(ps: list[str]) -> dict[str, float]:
        m = {p: float(np.nanmean(arr[p])) if np.isfinite(arr[p]).any() else 0.0 for p in ps}
        m = {p: max(v, 0.0) for p, v in m.items()}
        s = sum(m.values())
        return {p: (v / s if s > 0 else 1.0 / len(ps)) for p, v in m.items()} if ps else {}

    w_ret, w_tak = shares(ret), shares(tak)
    if return_shares is not None:
        s = sum(max(return_shares.get(p, 0.0), 0.0) for p in ret)
        if s > 0:
            w_ret = {p: max(return_shares.get(p, 0.0), 0.0) / s for p in ret}
    change = [0.0] * n
    for i, tgt in enumerate(targets):
        if tgt is None:
            continue
        d = ref[i] - tgt  # > 0: the live day used less (or ran off more) than the reference
        if d > 0 and not wetter:
            continue
        live_w = {p: w for p, w in (w_ret if d > 0 else w_tak).items() if np.isfinite(arr[p][i])}
        s = sum(live_w.values())
        if s <= 0:
            continue
        for p, w in live_w.items():
            arr[p][i] += abs(d) * w / s
        change[i] = float(tgt - ref[i])
    out = {p: [None if not np.isfinite(v) else float(v) for v in arr[p]] for p in paths}
    return out, change


@dataclass(frozen=True)
class RunSettings:
    study: Path  # DWR's study folder, holding config.inp and hydro.inp
    replace: Sequence[str]  # config settings whose DSS files are written afresh
    version: str  # F part of the live series
    version_settings: Sequence[str]  # settings that hold the F part of DWR's live-type series
    reference_year: int
    tool: str  # dsstool
    hydro: str  # the hydro program
    qual: str = ""  # the qual program
    mains: Sequence[str] = ("hydro.inp",)  # the study's main files, run in order
    depletion: Depletion | None = None  # island water use following a live daily total
    # Where DSM2's programs keep their temporary files. Short: QUAL fails to
    # read a long temp_dir (27 Nov 2026). On a machine whose /tmp is memory,
    # a folder on disk (#36).
    temp_dir: str = "/tmp"


def same_day_in(year: int, t: int) -> int:
    """The same calendar time (in Pacific Standard Time) in another year; 29 Feb maps to 28 Feb."""
    d = datetime.fromtimestamp(t - dss.PST, timezone.utc)
    day = 28 if (d.month, d.day) == (2, 29) else d.day
    return int(d.replace(year=year, day=day).timestamp()) + dss.PST


def reference_days(points: Sequence[tuple[int, float]], year: int, t0: int, days: int) -> list[tuple[int, float]]:
    """An irregular series for `days` days from t0 (a midnight, PST), copied from
    the same calendar days of `year`: the value in force at the start, then each
    reference day's changes at the same times of day."""
    out: list[tuple[int, float]] = []
    first = same_day_in(year, t0)
    before = [v for t, v in points if t <= first]
    if before:
        out.append((t0, before[-1]))
    for k in range(days):
        day = t0 + k * 86_400
        ref = same_day_in(year, day)
        for t, v in points:
            if ref < t < ref + 86_400 or (k > 0 and t == ref):
                if not out or out[-1][1] != v:
                    out.append((day + (t - ref), v))
    return out


def replaced_name(file_token: str, env: dict[str, str], replace: Sequence[str]) -> str | None:
    """The replaced setting a FILE column names, following settings that are
    only another name for one (DWR's QUALBNDINP is just ${BNDRYINPUT})."""
    seen = set()
    while (m := inp.VAR.fullmatch(file_token.strip())) and m.group(1) not in seen:
        name = m.group(1)
        if name in replace:
            return name
        seen.add(name)
        file_token = env.get(name, "")
    return None


def monthly(points: Sequence[tuple[int, float]], end: int) -> list[tuple[int, float]]:
    """An irregular series with the value in force repeated at the start of
    every month (PST) to `end`. DSM2 reads irregular series in blocks, a month
    for most settings, and looks only at the current and previous block: a
    value set further back is not found (seen on a 120-day run, 25 Nov 2026)."""
    pts = sorted(points)
    if not pts:
        return pts
    out, i = [], 0
    d = datetime.fromtimestamp(pts[0][0] - dss.PST, timezone.utc)
    m = datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=timezone.utc)
    while True:
        tm = int(m.timestamp()) + dss.PST
        if tm > end:
            break
        while i < len(pts) and pts[i][0] < tm:
            out.append(pts[i])
            i += 1
        if not (i < len(pts) and pts[i][0] == tm):
            out.append((tm, out[-1][1]))
        m = datetime(m.year + (m.month == 12), m.month % 12 + 1, 1, tzinfo=timezone.utc)
    out.extend(pts[i:])
    return out


def ae(path: str) -> str:
    """A path's A to E parts, without D (the block date) or F."""
    p = dss.parts(path)
    return f"/{p[0]}/{p[1]}/{p[2]}//{p[4]}/".upper()


def dsm2_date(t: int) -> tuple[str, str]:
    d = datetime.fromtimestamp(t - dss.PST, timezone.utc)
    return d.strftime("%d%b%Y").upper(), d.strftime("%H%M")


def build(settings: RunSettings, run_dir: Path, start: int, end: int, live: Sequence[LiveSeries | LiveSetting]) -> dict:
    """Write a run folder for [start, end] (UTC, whole days in PST) and return a
    summary of what each replaced file holds."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "output").mkdir(exist_ok=True)
    config_text = (settings.study / "config.inp").read_text(encoding="utf-8", errors="replace")
    env_dwr = inp.envvars(config_text)
    env_dwr = {k: str((settings.study / inp.expand(v, env_dwr)).resolve()) if k in ("DSM2INPUTDIR", "TSINPUTDIR", "STUDYDIR") else v for k, v in env_dwr.items()}
    sd, st = dsm2_date(start)
    ed, et = dsm2_date(end)
    new = {
        "START_DATE": sd, "START_TIME": st, "END_DATE": ed, "END_TIME": et,
        "QUAL_START_DATE": dsm2_date(start + 86_400)[0], "GTM_START_DATE": dsm2_date(start + 86_400)[0], "GTM_END_DATE": ed,
        "DSM2INPUTDIR": env_dwr["DSM2INPUTDIR"], "TSINPUTDIR": env_dwr["TSINPUTDIR"],
        "STUDYDIR": str(run_dir), "TEMPDIR": settings.temp_dir,
        **{name: str(run_dir / f"{name.lower()}.dss") for name in settings.replace},
        **{name: settings.version for name in settings.version_settings},
    }
    (run_dir / "config.inp").write_text(inp.set_envvars(config_text, new), encoding="utf-8")
    for main in settings.mains:
        text = (settings.study / main).read_text(encoding="utf-8", errors="replace")
        if main.startswith("qual"):
            text = with_hdf5_output(text, f"${{DSM2OUTPUTDIR}}/{Path(main).stem}.h5")
        (run_dir / main).write_text(text, encoding="utf-8")
    env_run = {**env_dwr, **{k: v for k, v in inp.envvars((run_dir / "config.inp").read_text(encoding="utf-8")).items()}}

    # Every series the run reads from a replaced file.
    refs = []
    included = [f for main in settings.mains for f in inp.included(run_dir / main, env_dwr)]
    for f in dict.fromkeys(included):
        for file_token, path in inp.series(f.read_text(encoding="utf-8", errors="replace")):
            name = replaced_name(file_token, env_dwr, settings.replace)
            if name:
                refs.append((name, inp.expand(path, env_dwr), inp.expand(path, env_run)))

    d = dss.Dss(settings.tool)
    by_ae = {ae(s.path): s for s in live}
    used, filled = set(), []
    written = set()
    cache: dict[tuple[str, str], tuple[list, str, str, int]] = {}
    t0, t1 = start - 86_400, end + 86_400  # a day either side of the run
    dep = settings.depletion
    held: dict[str, tuple[str, str, list, str, str]] = {}  # island water use, written once adjusted
    rain_mean: dict[str, float] = {}
    for name, dwr_path, run_path in refs:
        out_file = new[name]
        if (out_file, run_path) in written:
            continue
        written.add((out_file, run_path))
        src = inp.expand(f"${{{name}}}", env_dwr)
        live_setting = by_ae.get(ae(run_path))
        if isinstance(live_setting, LiveSetting):
            d.write_irregular(out_file, run_path, monthly(list(live_setting.points), t1), live_setting.units, "INST-VAL")
            used.add(ae(run_path))
            continue
        if dss.is_irregular(run_path):
            # Settings that change now and then, such as gates, some many times a
            # day with the tide: each run day gets the matching reference day's
            # changes at the same times of day, after the value in force at the
            # run's start.
            key = (src, dwr_path)
            if key not in cache:
                y1 = int(datetime(settings.reference_year, 12, 31, tzinfo=timezone.utc).timestamp()) + dss.PST
                y0 = int(datetime(settings.reference_year - 1, 1, 1, tzinfo=timezone.utc).timestamp()) + dss.PST
                pts, units, kind = d.read_irregular(src, dwr_path, y0, y1)
                if not pts or pts[0][0] > y0 + 366 * 86_400:  # a rarely changed setting: look further back
                    pts, units, kind = d.read_irregular(src, dwr_path, int(datetime(1980, 1, 1, tzinfo=timezone.utc).timestamp()), y1)
                cache[key] = (pts, units, kind, 0)
            pts, units, kind, _ = cache[key]
            out_pts = reference_days(pts, settings.reference_year, t0, (t1 - t0) // 86_400 + 1)
            if not out_pts:
                raise ValueError(f"{dwr_path} in {src} has no value in force in {settings.reference_year}")
            d.write_irregular(out_file, run_path, monthly(out_pts, t1), units or "UNDEF", kind or "INST-VAL")
            filled.append(run_path)
            continue
        step = dss.interval(run_path)
        n = (t1 - t0) // step + 1
        s = by_ae.get(ae(run_path))
        s = s if isinstance(s, LiveSeries) else None
        if s is not None:
            if s.start != t0 or len(s.values) < n:
                raise ValueError(f"live series {s.path} does not cover the run")
            d.write(out_file, run_path, t0, list(s.values[:n]), s.units, "INST-VAL")
            used.add(ae(run_path))
            continue
        # Reference year: read the whole year once, then pick the same calendar days.
        key = (src, dwr_path)
        if key not in cache:
            y0 = int(datetime(settings.reference_year, 1, 1, tzinfo=timezone.utc).timestamp()) + dss.PST + step
            ny = (366 * 86_400) // step
            vals, units, kind = d.read(src, dwr_path, y0, ny)
            cache[key] = (vals, units, kind, y0)
        vals, units, kind, y0 = cache[key]
        out = []
        for i in range(n):
            j = (same_day_in(settings.reference_year, t0 + i * step) - y0) // step
            out.append(vals[j] if 0 <= j < len(vals) else None)
        if dep and name == dep.file and step == 86_400 and dss.parts(run_path)[2].upper() in (*dep.use, *dep.returns):
            held[run_path] = (out_file, dss.parts(run_path)[2].upper(), out, units or "UNDEF", kind or "INST-VAL")
            # The reference year's rain-season mean (value j is the day ending y0 + j steps).
            wet = [v for j, v in enumerate(vals) if v is not None
                   and datetime.fromtimestamp(y0 + (j - 1) * step - dss.PST, timezone.utc).month in RAIN_SEASON]
            rain_mean[run_path] = sum(wet) / len(wet) if wet else 0.0
        else:
            d.write(out_file, run_path, t0, out, units or "UNDEF", kind or "INST-VAL")
        filled.append(run_path)
    depletion_change: list[float] = []
    if held:
        # A daily value is stamped at its day's end: value i is the day from t0 + (i - 1) days.
        n_days = len(next(iter(held.values()))[2])
        targets = [dep.days.get(t0 + (i - 1) * 86_400) for i in range(n_days)]
        new_vals, depletion_change = adjust_depletion({p: h[2] for p, h in held.items()}, {p: h[1] for p, h in held.items()},
                                                      dep.use, dep.returns, targets, dep.add_use, rain_mean, dep.wetter)
        for p, (out_file, _, _, units, kind) in held.items():
            d.write(out_file, p, t0, new_vals[p], units, kind)
    missing = sorted(set(by_ae) - used)
    if missing:
        raise ValueError(f"live series not used by the study: {missing}")
    return {"live": sorted(used), "reference_year": len(filled), "files": {k: new[k] for k in settings.replace}, "env": env_dwr,
            "depletion": {"series": len(held), "days": sum(1 for c in depletion_change if c != 0.0),
                          "change": depletion_change, "start": t0 - 86_400}}


def with_hdf5_output(text: str, path: str, interval: str = "15min") -> str:
    """A QUAL main file that also writes every channel's concentrations to HDF5.
    DWR's salinity study writes only station output; the map needs every
    channel. Only the run's own copy is changed."""
    out, inside, done = [], False, False
    for line in text.splitlines():
        s = inp.strip_comment(line).strip()
        if s.upper() == "IO_FILE":
            inside = True
        elif inside and s.split()[:2] == ["qual", "hdf5"]:
            done = True
        elif inside and s.upper() == "END":
            if not done:
                out.append(f"qual       hdf5     out              {interval}    {path}")
            inside = False
        out.append(line)
    return "\n".join(out) + "\n"


def _unlimited_stack() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))


def run(program: str, main: str, run_dir: Path, unlimited_stack: bool = False) -> float:
    """Run a DSM2 program on one of the run's main files; returns the seconds it
    took. Its screen output goes to <main>.log. Raises on failure.
    `unlimited_stack`: GTM keeps large arrays on the stack and crashes reading
    HYDRO's cross-section tables under the usual 8 MB limit (checked 26 Nov
    2026, a segmentation fault in hdf_util read_xsect_tbl)."""
    t = time.time()
    log = run_dir / f"{Path(main).stem}.log"
    with open(log, "w", encoding="utf-8") as out:
        r = subprocess.run([program, main], cwd=run_dir, stdout=out, stderr=subprocess.STDOUT,
                           preexec_fn=_unlimited_stack if unlimited_stack else None)
    if r.returncode != 0:
        raise RuntimeError(f"{Path(program).name} failed with status {r.returncode}; see {log}")
    return time.time() - t


def run_hydro(settings: RunSettings, run_dir: Path) -> float:
    return run(settings.hydro, "hydro.inp", run_dir)


def run_qual(settings: RunSettings, run_dir: Path, main: str) -> float:
    return run(settings.qual, main, run_dir)
