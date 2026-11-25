"""Fetch NOAA's Bay forecast and publish it as the Bay reach (specification 7.3).

    python -m fdmr_runner.bay REGION_DIR WORK_DIR [--publish OUT_DIR] [--days 14]

Reads REGION_DIR/bay.toml. The first run downloads one whole field file, reads
from it where each variable lies (fdmr_plugins.fvcom.layout) and builds the
coarse mesh (fdmr_plugins.fvcom.mesh), kept in WORK_DIR. Every run then makes
sure WORK_DIR holds each hour from `--days` back to the end of the latest
cycle's forecast, each fetched once as a byte range and kept as its fields on
the coarse mesh: the past from nowcasts, which do not change, and the forecast
from the latest cycle, replaced each cycle. With `--publish`, the hours become
a reach in OUT_DIR/<stamp>, with OUT_DIR/latest pointing at it, for the
region's publish step to take in (`--with-reach`).

Every hour is then corrected so that its net flow across the join to the Delta
is DSM2's (fdmr_plugins.fvcom.correct): NOAA's own net flow there is set by its
open tidal boundary upstream, not by the river.

Past NOAA's forecast (48 hours) the Bay is carried on to the end of the Delta's
forecast by a fit of tides plus outflow drift (fdmr_plugins.fvcom.extend),
refitted daily to the archive (`--archive` fetches it) and driven by the
outflow forecast: the latest measured daily outflow (bay.toml [extension]),
moved by however much DSM2's tidally averaged flow across the Delta's join to
the Bay changes after it (`--store`, `--delta`). Temperature and salinity are
held at NOAA's last forecast hour there.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fdmr_adapters import noaa_ofs
from fdmr_plugins.fvcom import extend, fields, mesh
from fdmr_plugins.fvcom.layout import Layout, read_layout

HOUR = 3600
DAY = 86_400
# Hours before the forecast's end over which the extension's non-tidal offset is taken.
ANOMALY_H = 72
# Raised whenever the coarsening changes, so meshes and hours made the old way are made again.
MESH_VERSION = 2


def stamp(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y%m%dT%H%MZ")


def load(region_dir: Path) -> dict | None:
    p = region_dir / "bay.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def prepare(cfg: dict, work: Path, cycle: int) -> tuple[Layout, mesh.Coarse, dict]:
    """The file layout and coarse mesh, made once (again if the mesh settings change)."""
    work.mkdir(parents=True, exist_ok=True)
    template = work / "template.nc"
    if not template.exists():
        noaa_ofs.download(noaa_ofs.file_url(cfg["ofs"], cycle, "f", 0), template)
    lay_file = work / "layout.json"
    if not lay_file.exists():
        lay_file.write_text(read_layout(template).to_json(), encoding="utf-8")
    layout = Layout.from_json(lay_file.read_text(encoding="utf-8"))
    settings = json.dumps({"mesh": cfg["mesh"], "version": MESH_VERSION}, sort_keys=True)
    mesh_file, made_with = work / "mesh.npz", work / "mesh.settings.json"
    if mesh_file.exists() and made_with.exists() and made_with.read_text(encoding="utf-8") == settings:
        coarse = mesh.Coarse.load(mesh_file)
    else:
        m = cfg["mesh"]
        fine = mesh.read_fine(template)
        keep = np.all((fine.lon[fine.tri] < m["east_of"]) & (fine.lon[fine.tri] > m["west_of"]), axis=1)
        coarse = mesh.coarsen(fine, keep, m["k"], m["min_m"], m["max_m"])
        coarse.save(mesh_file)
        made_with.write_text(settings, encoding="utf-8")
        shutil.rmtree(work / "hours", ignore_errors=True)  # hours on the old mesh
        shutil.rmtree(work / "forecast", ignore_errors=True)
    edges = mesh.classify(coarse, cfg["mesh"]["open_sea"], cfg["mesh"].get("handoffs", []))
    return layout, coarse, edges


def fetch_hour(cfg: dict, layout: Layout, coarse: mesh.Coarse, t: int, source: tuple[int, str, int], path: Path, quality: bool = True) -> str:
    url = noaa_ofs.file_url(cfg["ofs"], *source)
    lo, hi = layout.span() if quality else layout.span(fields.FLOW_VARS)
    when, f = fields.hour(layout, noaa_ofs.fetch_range(url, lo, hi), lo, coarse, quality)
    if when != t:
        raise ValueError(f"{url} holds {stamp(when)}, not {stamp(t)}: the file layout may have changed")
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, **f)
    tmp.replace(path)
    return url.rsplit("/", 1)[1]


def gather(cfg: dict, work: Path, layout: Layout, coarse: mesh.Coarse, now: int, latest: int, days: int) -> dict[int, Path]:
    """Every hour from `days` back to the end of the latest forecast, fetched if missing."""
    hours_dir, fc_dir = work / "hours", work / "forecast" / stamp(latest)
    hours_dir.mkdir(parents=True, exist_ok=True)
    fc_dir.mkdir(parents=True, exist_ok=True)
    start = (now - days * DAY) // HOUR * HOUR
    want: dict[int, Path] = {}
    todo = []
    for t in range(start, latest + cfg["forecast_hours"] * HOUR + 1, HOUR):
        path = (hours_dir if t <= latest else fc_dir) / f"{stamp(t)}.npz"
        want[t] = path
        if not path.exists():
            todo.append((t, noaa_ofs.source_of(t, cfg["cycles"], latest), path))
    failed = 0
    with ThreadPoolExecutor(4) as pool:
        futures = {pool.submit(fetch_hour, cfg, layout, coarse, t, src, p): t for t, src, p in todo}
        for fut, t in futures.items():
            try:
                fut.result()
            except Exception as e:  # one missing hour must not stop the rest
                failed += 1
                print(f"{stamp(t)} FAILED {e}", flush=True)
    print(f"hours: {len(want)} wanted, {len(todo) - failed} fetched, {failed} failed", flush=True)
    # Older forecasts and hours long past are not needed again.
    for old in (work / "forecast").iterdir():
        if old != fc_dir:
            shutil.rmtree(old, ignore_errors=True)
    for p in hours_dir.glob("*.npz"):
        if p.stem < stamp(start - 2 * DAY):
            p.unlink()
    return {t: p for t, p in want.items() if p.exists()}


def archive(cfg: dict, work: Path, layout: Layout, coarse: mesh.Coarse, latest: int, days: int, every_h: int) -> int:
    """The flow every `every_h` hours over the `days` before the latest cycle, from
    NOAA's archive of nowcasts, for fitting the extension (fdmr_plugins.fvcom.extend).
    Kept in WORK_DIR/archive, fetched once each. Returns how many are held."""
    adir = work / "archive"
    adir.mkdir(parents=True, exist_ok=True)
    step = every_h * HOUR
    first = (latest - days * DAY) // step * step
    todo = [(t, adir / f"{stamp(t)}.npz") for t in range(first, latest + 1, step) if not (adir / f"{stamp(t)}.npz").exists()]
    failed = 0
    with ThreadPoolExecutor(4) as pool:
        futures = {pool.submit(fetch_hour, cfg, layout, coarse, t, noaa_ofs.source_of(t, cfg["cycles"], latest), p, False): t for t, p in todo}
        for k, (fut, t) in enumerate(futures.items()):
            try:
                fut.result()
            except Exception as e:  # a missing hour leaves a gap, not a stop
                failed += 1
                print(f"archive {stamp(t)} FAILED {e}", flush=True)
            if k % 200 == 199:
                print(f"archive: {k + 1} of {len(todo)}", flush=True)
    for p in adir.glob("*.npz"):
        if p.stem < stamp(first):
            p.unlink()
    held = len(list(adir.glob("*.npz")))
    print(f"archive: {len(todo) - failed} fetched, {failed} failed, {held} held", flush=True)
    return held


def daily_outflow(store_path: str, o: dict, t0: int, t1: int) -> tuple[list[tuple[int, float]], int]:
    """The measured daily outflow from t0: (day start, m3/s) for each day held,
    and the end of the last day held. CDEC stamps a daily value at the start of
    its day in Pacific Standard Time."""
    from fdmr_store import Store

    s = Store(store_path)
    vals = [(v.t, v.value) for v in s.values(o["source"], o["site"], o["parameter"], t0 - 2 * DAY, t1 + 1) if v.value is not None]
    s.close()
    if not vals:
        raise ValueError(f"no outflow record for {o['site']}")
    return vals, vals[-1][0] + DAY


def on_hours(days: list[tuple[int, float]], t: np.ndarray) -> np.ndarray:
    """Daily values held through each day, at times t; held at the last past its end."""
    starts = np.array([d for d, _ in days])
    q = np.array([v for _, v in days])
    return q[np.clip(np.searchsorted(starts, t, side="right") - 1, 0, len(q) - 1)]


def flow_stack(files: list[Path]) -> dict[str, np.ndarray]:
    """u, v and level from hour files, stacked (hours x nodes), each file read and closed."""
    out: dict[str, list] = {"u": [], "v": [], "level": []}
    for f in files:
        with np.load(f) as d:
            for k in out:
                out[k].append(d[k])
    return {k: np.stack(v) for k, v in out.items()}


def refit(cfg: dict, work: Path, store_path: str, now: int) -> extend.Fit | None:
    """The extension's fit, made again when a day old: the archive against the outflow."""
    path = work / "extension_fit.npz"
    if path.exists() and path.stat().st_mtime > now - 20 * HOUR:
        return extend.Fit.load(path)
    files = sorted((work / "archive").glob("*.npz"))
    if len(files) < 30 * 12:
        print(f"extension: only {len(files)} archived hours; not fitted", flush=True)
        return extend.Fit.load(path) if path.exists() else None
    ts = np.array([int(datetime.strptime(f.stem, "%Y%m%dT%H%MZ").replace(tzinfo=timezone.utc).timestamp()) for f in files])
    days, _ = daily_outflow(store_path, cfg["extension"]["outflow"], int(ts.min()), int(ts.max()))
    t0 = time.time()
    f = extend.fit(ts, on_hours(days, ts), flow_stack(files))
    f.save(path)
    print(f"extension: fitted {len(ts)} archived hours in {time.time() - t0:.0f} s; median share of variance explained: "
          + ", ".join(f"{k} {np.median(v):.3f}" for k, v in f.skill.items()), flush=True)
    return f


def dsm2_outflow(delta_dir: Path, join: dict, tidal: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """DSM2's flow from the Delta into the Bay across the join (m3/s; with
    `tidal`, tidally filtered, fdmr_plugins.fvcom.correct.tidal_mean), at its published steps:
    the flow in each of the Delta's own channels at a join junction, toward the
    junction. Near the record's ends the nearest full-window mean is held."""
    import fdmr_py

    ts, qs = [], []
    for f in sorted((delta_dir / "delta").glob("*.fdf")):
        if f.name.count(".") > 1:  # .nodes, .links, .quality
            continue
        _, _, n, _, steps, t0, step, flds = fdmr_py.read_field_file(str(f))
        get = {name: np.asarray(v, dtype=float).reshape(steps, n) for name, _, v in flds}
        q = np.zeros(steps)
        for j in join["junctions"]:
            c = j["element"]
            q += get["flow_down"][:, c] if j["fraction"] == 1 else -get["flow_up"][:, c]
        ts.append(t0 + step * np.arange(steps))
        qs.append(q)
    t, q = np.concatenate(ts), np.concatenate(qs)
    if not tidal or len(t) < 2:
        return t, q
    from fdmr_plugins.fvcom.correct import tidal_mean

    return t, tidal_mean(q, HOUR / (t[1] - t[0]))


def mesh_outflow(coarse: mesh.Coarse, edges: list[list[int]], hours: dict[int, Path]) -> tuple[np.ndarray, np.ndarray]:
    """The mesh's flow across a set of its boundary edges, out of the mesh
    (m3/s), at each hour held: the depth-averaged velocity across each edge
    times its length and the water's depth there, both ends averaged."""
    tri = coarse.tri
    normals = []
    for a, b in edges:
        k = next(i for i, tr in enumerate(tri) if a in tr and b in tr)
        cx, cy = coarse.x[tri[k]].mean(), coarse.y[tri[k]].mean()
        dx, dy = coarse.x[b] - coarse.x[a], coarse.y[b] - coarse.y[a]
        nx, ny = dy, -dx  # length times a unit normal
        mx, my = (coarse.x[a] + coarse.x[b]) / 2, (coarse.y[a] + coarse.y[b]) / 2
        if nx * (mx - cx) + ny * (my - cy) < 0:  # point it away from the triangle: outward
            nx, ny = -nx, -ny
        normals.append((a, b, nx, ny))
    ts = np.array(sorted(hours))
    q = np.zeros(len(ts))
    for i, tt in enumerate(ts):
        d = np.load(hours[tt]) if not isinstance(hours[tt], dict) else hours[tt]
        for a, b, nx, ny in normals:
            u, v = (d["u"][a] + d["u"][b]) / 2, (d["v"][a] + d["v"][b]) / 2
            h = (coarse.depth[a] + d["level"][a] + coarse.depth[b] + d["level"][b]) / 2
            q[i] += (u * nx + v * ny) * max(h, 0.0)
    return ts, q


def overlap(coarse: mesh.Coarse, edges: dict, hours: dict[int, Path], delta_dir: Path, reach: str, now: int) -> dict:
    """Where the two models meet (specification 7.4): their flows across the
    join over the past days both hold, Delta to Bay positive. `net` is the mean
    (m3/s), `tidal` the standard deviation about it (m3/s, the size of the tide)."""
    from .handoff import network_to_mesh

    net = json.loads((delta_dir / "geometry" / "delta.json").read_text(encoding="utf-8"))
    jn = network_to_mesh("delta", net, reach, mesh.geometry(coarse, reach, edges))
    td, qd = dsm2_outflow(delta_dir, jn)
    past = {t: p for t, p in hours.items() if t <= now}
    tm, qm = mesh_outflow(coarse, edges.get("delta", []), past)
    qm = -qm  # out of the mesh is into the Delta; Delta to Bay positive
    lo, hi = max(td.min(), tm.min()), min(td.max(), tm.max())
    pick_d, pick_m = (td >= lo) & (td <= hi), (tm >= lo) & (tm <= hi)
    if pick_d.sum() < 100 or pick_m.sum() < 25:
        return {}
    dsm2 = qd[pick_d]
    noaa = qm[pick_m]
    utc = lambda x: datetime.fromtimestamp(int(x), timezone.utc).strftime("%Y-%m-%dT%H:%MZ")  # noqa: E731
    out = {
        "where": f"the join from the network reach to {reach}", "from": utc(lo), "to": utc(hi),
        "dsm2": {"net": round(float(dsm2.mean()), 1), "tidal": round(float(dsm2.std()), 1)},
        "noaa": {"net": round(float(noaa.mean()), 1), "tidal": round(float(noaa.std()), 1)},
    }
    print(f"overlap at the join, {out['from']} to {out['to']}: net DSM2 {out['dsm2']['net']}, NOAA {out['noaa']['net']} m3/s; "
          f"tidal DSM2 {out['dsm2']['tidal']}, NOAA {out['noaa']['tidal']} m3/s", flush=True)
    return out


def correct_outflow(coarse: mesh.Coarse, edges: dict, hours: dict[int, Path | dict], delta_dir: Path, reach: str) -> tuple[dict[int, dict], dict]:
    """Every hour, with a steady flow added so that its tidally filtered net flow
    across the join to the Delta is DSM2's (fdmr_plugins.fvcom.correct). The
    tides are left as they are. Returns the hours and a summary for the reach."""
    from fdmr_plugins.fvcom.correct import tidal_mean, unit_outflow

    from .handoff import network_to_mesh

    cu, cv = unit_outflow(coarse, edges["delta"], edges["open"])
    # Scaled so its flux across the join, measured as the field's own is, is exactly 1 m3/s.
    _, unit = mesh_outflow(coarse, edges["delta"], {0: {"u": cu, "v": cv, "level": np.zeros(len(cu), np.float32)}})
    cu, cv = cu / -unit[0], cv / -unit[0]
    ts = np.array(sorted(hours))
    data = {tt: (hours[tt] if isinstance(hours[tt], dict) else dict(np.load(hours[tt]))) for tt in ts}
    _, out = mesh_outflow(coarse, edges["delta"], data)
    noaa = tidal_mean(-out, 1.0)  # seaward, tidally filtered (hourly)
    net = json.loads((delta_dir / "geometry" / "delta.json").read_text(encoding="utf-8"))
    jn = network_to_mesh("delta", net, reach, mesh.geometry(coarse, reach, edges))
    td, qd = dsm2_outflow(delta_dir, jn, tidal=True)
    target = np.interp(ts, td, qd)
    raw = data

    def apply(dq: np.ndarray) -> tuple[dict[int, dict], np.ndarray]:
        out_ = {}
        for i, tt in enumerate(ts):
            d = dict(raw[tt])
            d["u"] = (d["u"] + dq[i] * cu).astype(np.float32)
            d["v"] = (d["v"] + dq[i] * cv).astype(np.float32)
            out_[tt] = d
        return out_, -mesh_outflow(coarse, edges["delta"], out_)[1]

    # The added flow's flux moves a little with the water level, so measure how
    # much of it comes through, tidally filtered, and divide by that one gain.
    dq = target - noaa
    _, after = apply(dq)
    got = tidal_mean(after, 1.0) - noaa
    gain = float((got * dq).sum() / (dq * dq).sum()) if (dq * dq).sum() > 0 else 1.0
    dq = dq / gain
    data, after = apply(dq)
    after = -after  # back to out of the mesh, as below
    # How well the corrected tidal mean follows DSM2's, away from the record's ends.
    inner = slice(72, len(ts) - 72) if len(ts) > 200 else slice(None)
    miss = tidal_mean(-after, 1.0)[inner] - target[inner]
    summary = {
        "noaa_net": round(float(noaa[inner].mean()), 1), "dsm2_net": round(float(target[inner].mean()), 1),
        "added_mean": round(float(dq.mean()), 1), "added_range": [round(float(dq.min()), 1), round(float(dq.max()), 1)],
        "net_after": round(float(tidal_mean(-after, 1.0)[inner].mean()), 1), "largest_miss": round(float(np.abs(miss).max()), 1),
        "gain": round(gain, 3),
    }
    print(f"outflow correction: tidally filtered net across the join, NOAA {summary['noaa_net']} m3/s seaward, DSM2 {summary['dsm2_net']}; "
          f"added {summary['added_mean']} on average ({summary['added_range'][0]} to {summary['added_range'][1]}); "
          f"after {summary['net_after']}, at most {summary['largest_miss']} from DSM2's at any hour", flush=True)
    return data, summary


def extension_hours(cfg: dict, fit: extend.Fit, hours: dict[int, Path], latest: int, end: int, store_path: str,
                    delta_dir: Path | None, coarse: mesh.Coarse, edges: dict) -> dict[int, dict[str, np.ndarray]]:
    """Hours after NOAA's forecast to `end`, from the fit, joined to the forecast's last hour."""
    last = max(hours)
    t = np.arange(last + HOUR, end, HOUR)
    if not len(t):
        return {}
    days, measured_to = daily_outflow(store_path, cfg["extension"]["outflow"], last - 30 * DAY, last)
    # The last week's measured mean, moved by however much DSM2's tidally filtered flow
    # across the join changes from its own mean over that week: DSM2's subtidal
    # flow there swings by hundreds of m3/s from day to day as the marsh and the
    # Delta fill and drain with the spring-neap cycle, so one moment of it is no anchor.
    week = [v for d, v in days if d >= measured_to - 7 * DAY]
    q_week = float(np.mean(week))
    q = np.full(len(t), q_week)
    how = f"held at the last week's measured mean, {q_week:.0f} m3/s"
    if delta_dir is not None and (delta_dir / "geometry" / "delta.json").exists():
        from .handoff import network_to_mesh

        net = json.loads((delta_dir / "geometry" / "delta.json").read_text(encoding="utf-8"))
        jn = network_to_mesh("delta", net, cfg["reach"], mesh.geometry(coarse, cfg["reach"], edges))
        dt_, dq = dsm2_outflow(delta_dir, jn, tidal=True)
        in_week = (dt_ >= measured_to - 7 * DAY) & (dt_ < measured_to)
        if in_week.sum() > 96:
            q = q_week + (np.interp(t, dt_, dq) - float(dq[in_week].mean()))
            how = (f"the last week's measured mean {q_week:.0f} m3/s, moved with DSM2's forecast "
                   f"({q.min():.0f} to {q.max():.0f})")
    pred = fit.predict(np.concatenate([[last], t]), np.concatenate([[q[0]], q]))
    # The slow, non-tidal offset of the last 3 days, carried on (extend.anomaly).
    recent = [tt for tt in sorted(hours) if tt > last - ANOMALY_H * HOUR]
    rec = flow_stack([hours[tt] for tt in recent])
    tr = np.array(recent)
    days_q = on_hours(days, tr)
    off = extend.anomaly(fit, tr, days_q, rec)
    pred = {k: v + off[k][None, :] for k, v in pred.items()}
    base = dict(np.load(hours[last]))
    joined = extend.join(base, {k: v[0] for k, v in pred.items()}, {k: v[1:] for k, v in pred.items()}, (t - last) / HOUR)
    print(f"extension: {len(t)} hours to {stamp(int(t[-1]))}; outflow {how}", flush=True)
    return {int(tt): {**{k: joined[k][i] for k in joined}, "temperature": base["temperature"], "salinity": base["salinity"]}
            for i, tt in enumerate(t)}


def publish(cfg: dict, region_dir: Path, coarse: mesh.Coarse, edges: dict, hours: dict[int, Path | dict], now: int, latest: int, out_root: Path,
            keep: int = 3, extras: dict | None = None) -> Path:
    import fdmr_py

    from .region import load_region

    reach = cfg["reach"]
    pub = out_root / stamp(latest)
    if pub.exists():
        shutil.rmtree(pub)  # never rewrite a published file in place
    (pub / "geometry").mkdir(parents=True)
    (pub / "geometry" / f"{reach}.json").write_text(json.dumps(mesh.geometry(coarse, reach, edges), separators=(",", ":"), allow_nan=False), encoding="utf-8")
    data = {t: (p if isinstance(p, dict) else dict(np.load(p))) for t, p in sorted(hours.items())}
    start, end = min(data), max(data) + HOUR
    days = fields.write_days(data, reach, pub / reach, start, end)
    rc = next((r for r in load_region(region_dir).reaches if r["id"] == reach), {})
    cert = rc.get("certainty", {})
    solid, floor = cert.get("solid_days", 2) * DAY, cert.get("floor", 0.2)
    utc = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")  # noqa: E731
    entry = {
        "id": reach, "kind": rc.get("kind", "2d-field"), "dims": 2, "elements": len(coarse.x), "layers": 1,
        "fields": list(fields.FIELDS), "quality_fields": list(fields.QUALITY),
        "time": {"start": utc(start), "step_s": HOUR, "days": days},
        "now": utc(now // HOUR * HOUR),
        "certainty": [round(fdmr_py.certainty_at(t - now, solid, end - now, floor), 4) for t in range(start, end, HOUR)],
        "flow_source": f"{rc.get('flow_source', 'noaa-ofs')} cycle {stamp(latest)}",
        **(extras or {}),
    }
    (pub / "reach.json").write_text(json.dumps(entry, separators=(",", ":")), encoding="utf-8")
    tmp = out_root / ".latest.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(pub.name)
    tmp.replace(out_root / "latest")
    olds = sorted(p for p in out_root.iterdir() if p.is_dir() and not p.is_symlink() and p.name != pub.name)
    for p in olds[: max(0, len(olds) - (keep - 1))]:
        shutil.rmtree(p)
    return pub


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("work")
    ap.add_argument("--publish", help="publish the reach into this folder (its `latest` link)")
    ap.add_argument("--days", type=int, help="days of the past (default: days_back in bay.toml)")
    ap.add_argument("--archive", action="store_true", help="also fetch the archive the extension is fitted to (bay.toml [extension])")
    ap.add_argument("--store", help="the local store, for the outflow the extension needs")
    ap.add_argument("--delta", help="the Delta's published reach, for DSM2's outflow forecast")
    ap.add_argument("--end", type=int, default=14, help="days after today the extension runs to (default 14, as the Delta)")
    args = ap.parse_args(argv)
    region_dir, work = Path(args.region), Path(args.work)
    cfg = load(region_dir)
    if cfg is None:
        print("no bay.toml in this region")
        return 0
    now = int(time.time())
    latest = noaa_ofs.latest_cycle(cfg["ofs"], cfg["cycles"], now, cfg["forecast_hours"])
    if latest is None:
        print("no complete NOAA cycle in the last day")
        return 1
    print(f"latest cycle {stamp(latest)}", flush=True)
    layout, coarse, edges = prepare(cfg, work, latest)
    print(f"mesh: {len(coarse.x)} nodes, {len(coarse.tri)} triangles; edges: "
          + ", ".join(f"{k} {len(v)}" for k, v in edges.items()), flush=True)
    if args.archive and "extension" in cfg:
        x = cfg["extension"]
        t = time.time()
        archive(cfg, work, layout, coarse, latest, x["fit_days"], x["fit_every_h"])
        print(f"archive in {time.time() - t:.0f} s", flush=True)
    t = time.time()
    hours = gather(cfg, work, layout, coarse, now, latest, args.days or cfg.get("days_back", 14))
    print(f"gathered in {time.time() - t:.0f} s", flush=True)
    extras: dict = {}
    if args.delta and (Path(args.delta) / "geometry" / "delta.json").exists() and hours:
        try:
            ov = overlap(coarse, edges, hours, Path(args.delta).resolve(), cfg["reach"], now)
            if ov:
                extras["overlap"] = ov
        except Exception as e:
            print(f"overlap FAILED {e}", flush=True)
    if args.store and "extension" in cfg and hours:
        try:
            fit = refit(cfg, work, args.store, now)
            if fit is not None:
                from .delta import pst_midnight

                end = pst_midnight(now) + args.end * DAY + HOUR
                hours = {**hours, **extension_hours(cfg, fit, hours, latest, end, args.store,
                                                    Path(args.delta).resolve() if args.delta else None, coarse, edges)}
        except Exception as e:  # without the extension the Bay still has NOAA's 48 hours
            print(f"extension FAILED {e}", flush=True)
    if any(isinstance(p, dict) for p in hours.values()):
        extras["extension_from"] = datetime.fromtimestamp(min(t for t, p in hours.items() if isinstance(p, dict)), timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    if args.delta and (Path(args.delta) / "geometry" / "delta.json").exists() and hours and "delta" in edges:
        try:
            hours, extras["outflow_correction"] = correct_outflow(coarse, edges, hours, Path(args.delta).resolve(), cfg["reach"])
        except Exception as e:  # uncorrected, the Bay still has NOAA's own currents
            print(f"outflow correction FAILED {e}", flush=True)
    if args.publish:
        pub = publish(cfg, region_dir, coarse, edges, hours, now, latest, Path(args.publish), extras=extras)
        size = sum(p.stat().st_size for p in pub.rglob("*") if p.is_file())
        print(f"published {pub} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
