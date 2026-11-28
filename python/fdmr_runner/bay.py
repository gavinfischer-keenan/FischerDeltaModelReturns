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

import fdmr_geo
import numpy as np

from fdmr_adapters import noaa_ofs
from fdmr_plugins.fvcom import extend, fields, mesh
from fdmr_plugins.fvcom.flux import mesh_outflow
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
        # Everything held at the old mesh's nodes: hours, forecasts, the archive
        # the extension is fitted to, and the fit itself.
        for old in ("hours", "forecast", "archive"):
            shutil.rmtree(work / old, ignore_errors=True)
        for old in work.glob("extension_fit*.npz"):
            old.unlink()
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


def crossings(join: dict) -> list[dict]:
    """Every place the network crosses into the mesh (fdmr_runner.handoff):
    its cuts, and any junction serving as one (a junction is a cut at its
    channel's end)."""
    out = [dict(c) for c in join.get("cuts", [])]
    for j in join.get("junctions", []):
        out.append({"element": j["element"], "at": float(j["fraction"]), "inside": "down" if j["fraction"] == 1 else "up",
                    "x": j["x"], "y": j["y"]})
    return out


def dsm2_cut_flows(delta_dir: Path, join: dict) -> tuple[np.ndarray, np.ndarray]:
    """DSM2's flow into the mesh at each crossing (m3/s, Delta to Bay
    positive), at its published steps: (times, flows[step, crossing]). A
    channel's flow at a place along it is taken linear between its two ends."""
    import fdmr_py

    cr = crossings(join)
    ts, qs = [], []
    for f in sorted((delta_dir / network_id(delta_dir)).glob("*.fdf")):
        if f.name.count(".") > 1:  # .nodes, .links, .quality
            continue
        _, _, n, _, steps, t0, step, flds = fdmr_py.read_field_file(str(f))
        get = {name: np.asarray(v, dtype=float).reshape(steps, n) for name, _, v in flds}
        q = np.zeros((steps, len(cr)))
        for k, c in enumerate(cr):
            e, a = c["element"], c["at"]
            along = get["flow_up"][:, e] * (1 - a) + get["flow_down"][:, e] * a  # positive downstream
            q[:, k] = along if c["inside"] == "down" else -along
        ts.append(t0 + step * np.arange(steps))
        qs.append(q)
    return np.concatenate(ts), np.concatenate(qs)


def dsm2_outflow(delta_dir: Path, join: dict, tidal: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """DSM2's flow from the Delta into the Bay across the join (m3/s; with
    `tidal`, tidally filtered, fdmr_plugins.fvcom.correct.tidal_mean), at its
    published steps: the sum over the crossings (dsm2_cut_flows). Near the
    record's ends the nearest full-window mean is held: the Bay's outflow
    correction needs a value at every step, and its ends are the forecast's,
    where it fades anyway (not "missing" as X2 and the skill scores use, #71)."""
    t, q = dsm2_cut_flows(delta_dir, join)
    q = q.sum(axis=1)
    if not tidal or len(t) < 2:
        return t, q
    from fdmr_plugins.fvcom.correct import tidal_mean

    return t, tidal_mean(q, HOUR / (t[1] - t[0]))


def network_id(net_dir: Path) -> str | None:
    """The id of the network reach published in `net_dir` (its reach.json):
    the mesh's handoff edges toward it are keyed by it too (bay.toml
    [[mesh.handoffs]] id), so no reach's name is written into this code (#68)."""
    f = Path(net_dir) / "reach.json"
    return json.loads(f.read_text(encoding="utf-8"))["id"] if f.exists() else None


def has_network(net_dir: Path) -> bool:
    """Whether a network reach is published in `net_dir`, with its geometry."""
    nid = network_id(net_dir)
    return nid is not None and (Path(net_dir) / "geometry" / f"{nid}.json").exists()


def join_of(delta_dir: Path, coarse: mesh.Coarse, reach: str, edges: dict) -> dict:
    """The join between the network reach published in `delta_dir` and this mesh."""
    from .handoff import network_to_mesh

    nid = network_id(delta_dir)
    net = json.loads((delta_dir / "geometry" / f"{nid}.json").read_text(encoding="utf-8"))
    return network_to_mesh(nid, net, reach, mesh.geometry(coarse, reach, edges))


def add_cut_edges(coarse: mesh.Coarse, edges: dict, join: dict, near_m: float) -> dict:
    """The handoff edges, with every other boundary edge within `near_m` of a
    crossing added: where a channel enters the mesh's water through what the
    mesh takes for shore (a slough's mouth), water and what it carries must
    pass both ways there too."""
    taken = {tuple(sorted(e)) for es in edges.values() for e in es}
    pts = np.array([[c["x"], c["y"]] for c in crossings(join)])
    if not len(pts):
        return edges
    extra = []
    for a, b in mesh.boundary_edges(coarse.tri).tolist():
        if tuple(sorted((a, b))) in taken:
            continue
        m = np.array([(coarse.x[a] + coarse.x[b]) / 2, (coarse.y[a] + coarse.y[b]) / 2])
        if np.hypot(*(pts - m).T).min() <= near_m:
            extra.append([a, b])
    return {**edges, join["from"]: edges.get(join["from"], []) + extra}




def overlap(coarse: mesh.Coarse, edges: dict, hours: dict[int, Path], delta_dir: Path, reach: str, now: int) -> dict:
    """Where the two models meet (specification 7.4): their flows across the
    join over the past days both hold, Delta to Bay positive. `net` is the mean
    (m3/s), `tidal` the standard deviation about it (m3/s, the size of the tide)."""
    jn = join_of(delta_dir, coarse, reach, edges)
    td, qd = dsm2_outflow(delta_dir, jn)
    past = {t: p for t, p in hours.items() if t <= now}
    tm, qm = mesh_outflow(coarse, edges.get(network_id(delta_dir), []), past)
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


def compare_days(model: dict[int, float], measured: dict[int, float], dry: dict[int, bool]) -> dict:
    """Daily model against daily measured values on the days both have: the
    means and their difference, over all days and split into dry and wet days
    (a day with no `dry` entry counts in all days only). A daily mean swings
    with the spring-neap filling and draining of the water, so a split with
    fewer than `MIN_SPLIT_DAYS` days gives only its count."""
    def summary(days: list[int], least: int = 1) -> dict:
        if len(days) < max(least, 1):
            return {"days": len(days)}
        m, g = np.array([model[d] for d in days]), np.array([measured[d] for d in days])
        return {"days": len(days), "model_mean": round(float(m.mean()), 1), "measured_mean": round(float(g.mean()), 1),
                "difference": round(float((m - g).mean()), 1)}

    both = sorted(d for d in model if d in measured and np.isfinite(model[d]) and np.isfinite(measured[d]))
    return {"all": summary(both), "dry": summary([d for d in both if dry.get(d) is True], MIN_SPLIT_DAYS),
            "wet": summary([d for d in both if dry.get(d) is False], MIN_SPLIT_DAYS)}


MIN_SPLIT_DAYS = 5  # a dry or wet split with fewer days than this gives only its count, not a mean (FDMR's choice)


def outflow_checks(cfg: dict, delta_dir: Path, jn: dict, store_path: str, now: int) -> list[dict]:
    """The network's net outflow across the join (tidally filtered, daily
    means over the days published) against each measured outflow in bay.toml
    [[outflow_checks]], split by whether the day was dry (bay.toml
    [outflow_dry]: a daily series at or above zero on a dry day)."""
    from fdmr_store import Store

    from .delta import pst_midnight

    td, qd = dsm2_outflow(delta_dir, jn, tidal=True)
    days = range(pst_midnight(int(td[0])) + DAY, min(pst_midnight(now), int(td[-1]) - DAY) + 1, DAY)
    model = {}
    for d in days:
        sel = (td >= d) & (td < d + DAY)
        if sel.sum() >= 18 * 4:
            model[d] = float(qd[sel].mean())
    store = Store(store_path)
    dc = cfg.get("outflow_dry")
    dry = {}
    if dc:
        for v in store.values(dc["source"], dc["site"], dc["parameter"], days.start, days.stop):
            if v.value is not None:
                dry[v.t] = v.value >= 0
    out = []
    for c in cfg.get("outflow_checks", []):
        meas = {v.t: v.value for v in store.values(c["source"], c["site"], c["parameter"], days.start, days.stop) if v.value is not None}
        r = {"name": c["name"], "site": c["site"], **compare_days(model, meas, dry)}
        out.append(r)
        a = r["all"]
        if a["days"]:
            print(f"outflow check, {c['name']}: {a['days']} days, network {a['model_mean']} against {a['measured_mean']} m3/s"
                  + (f"; dry days {r['dry']['model_mean']} against {r['dry']['measured_mean']}" if "model_mean" in r["dry"] else ""), flush=True)
    store.close()
    return out


def correct_outflow(coarse: mesh.Coarse, edges: dict, hours: dict[int, Path | dict], delta_dir: Path, reach: str) -> tuple[dict[int, dict], dict]:
    """Every hour, with flows added so that the mesh's flow through the join by
    each crossing is the network's flow in that channel there, at that hour,
    tide and net alike (specification 7.4): the same water crosses from both
    sides, in the same direction, at every crossing, so parcels and flow
    particles pass without piling up or thinning out, and no circulation
    through the join (in by one channel, out by another) survives. Each
    handoff edge counts toward its nearest crossing. Each crossing's added
    flow runs from its edges to the open sea (fdmr_plugins.fvcom.correct); as
    they touch a little at shared nodes, the hour's amounts are solved
    together. The mesh's tides elsewhere are its own. Returns the hours and a
    summary: per crossing and in total, both models' net and tidal flow before
    the correction, how often they ran opposite ways, and what was added."""
    from fdmr_plugins.fvcom.correct import match_crossings

    jn = join_of(delta_dir, coarse, reach, edges)
    cr = crossings(jn)
    net = json.loads((delta_dir / "geometry" / f"{network_id(delta_dir)}.json").read_text(encoding="utf-8"))
    ts = np.array(sorted(hours))
    data = {tt: (hours[tt] if isinstance(hours[tt], dict) else dict(np.load(hours[tt]))) for tt in ts}
    td, Q = dsm2_cut_flows(delta_dir, jn)
    m = match_crossings(coarse, edges[network_id(delta_dir)], edges["open"], np.array([[c["x"], c["y"]] for c in cr]), data, ts, td, Q)
    data, seg, active, noaa, target, after, added_speed, compare = (
        m["data"], m["seg"], m["active"], m["noaa"], m["target"], m["after"], m["added_speed"], m["compare"])

    per = []
    for k, s in enumerate(active):
        c = cr[s]
        per.append({"channel": net["elements"][c["element"]]["id"], "lon": c.get("lon"), "lat": c.get("lat"),
                    "edges": int((seg == s).sum()), **compare(noaa[:, k], target[:, k]),
                    "largest_miss": round(float(np.abs(after[:, k] - target[:, k]).max()), 2)})
    unmatched = [net["elements"][cr[s]["element"]]["id"] for s in range(len(cr)) if s not in active]
    total = compare(noaa.sum(axis=1), target.sum(axis=1))
    added = target.sum(axis=1) - noaa.sum(axis=1)
    summary = {
        **total,
        "added_mean": round(float(added.mean()), 1), "added_range": [round(float(added.min()), 1), round(float(added.max()), 1)],
        "largest_miss": round(float(np.abs(after - target).max()), 2), "largest_added_speed": round(added_speed, 3),
        "crossings": per, "crossings_without_edges": unmatched,
    }
    print(f"outflow correction: {len(active)} crossings matched hour by hour (largest miss {summary['largest_miss']} m3/s, "
          f"largest current added {summary['largest_added_speed']} m/s); in total, net NOAA {total['noaa_net']} m3/s seaward, "
          f"DSM2 {total['dsm2_net']}; tide NOAA {total['noaa_tidal']}, DSM2 {total['dsm2_tidal']}", flush=True)
    for p in per:
        print(f"  channel {p['channel']}: before, net NOAA {p['noaa_net']}, DSM2 {p['dsm2_net']}; tide {p['noaa_tidal']} against "
              f"{p['dsm2_tidal']} (correlation {p['tidal_correlation']}); opposite ways {p['opposite_share']:.0%} of the time", flush=True)
    return data, summary


def station_daily(store, st: dict, params: list[str], to_value, days: list[int]) -> np.ndarray:
    """A station's daily mean (by day start, PST) over its sensors: each
    sensor's own daily mean, then the mean of those; NaN where no sensor has
    18 or more hours of the day."""
    out = np.full(len(days), np.nan)
    for i, d in enumerate(days):
        means = []
        for p in params:
            vs = [(v.t, v.value) for v in store.values(st["source"], st["site"], p, d, d + DAY - 1) if v.value is not None]
            if len({t // HOUR for t, _ in vs}) >= 18:
                means.append(float(np.mean(to_value(np.array([v for _, v in vs])))))
        if means:
            out[i] = float(np.mean(means))
    return out


def network_quality(delta_dir: Path, join: dict, field: str) -> tuple[np.ndarray, np.ndarray] | None:
    """The network's value of a water-quality field in each crossing's channel,
    at its published steps: (times, values[step, crossing]); None without it."""
    import fdmr_py

    cr = crossings(join)
    ts, vs = [], []
    for f in sorted((delta_dir / network_id(delta_dir)).glob("*.quality.fdf")):
        _, _, n, _, steps, t0, step, flds = fdmr_py.read_field_file(str(f))
        got = {name: np.asarray(v, dtype=float).reshape(steps, n) for name, _, v in flds}
        if field not in got:
            return None
        ts.append(t0 + step * np.arange(steps))
        vs.append(got[field][:, [c["element"] for c in cr]])
    return (np.concatenate(ts), np.concatenate(vs)) if ts else None


def seam_quality(data: dict[int, dict], coarse: mesh.Coarse, delta_dir: Path, join: dict, fields: tuple[str, ...], seam_m: float) -> dict:
    """The mesh's water quality made to meet the network's at the join, hour by
    hour: at each crossing's nearest node the mesh takes the network's value
    in that channel, and the difference fades out along the water over
    `seam_m` (weight (1 - d/seam_m)^2, the crossings' shares summed and kept
    to at most 1). Returns, per field, the mean step across each crossing
    before and after."""
    from fdmr_plugins.fvcom.correct import seam

    cr = crossings(join)
    if not cr:
        return {}
    nodes = [int(np.argmin(np.hypot(coarse.x - c["x"], coarse.y - c["y"]))) for c in cr]
    series = {f: nq for f in fields if (nq := network_quality(delta_dir, join, f)) is not None}
    out = seam(data, coarse, nodes, series, seam_m)
    for field, r in out.items():
        b = np.array([np.nan if x is None else x for x in r["step_before"]], dtype=float)
        a = np.array([np.nan if x is None else x for x in r["step_after"]], dtype=float)
        print(f"{field} seam at the join: mean step across the crossings {np.nanmean(b):.2f} before, {np.nanmean(a):.2f} after "
              f"(largest {np.nanmax(b):.2f} before)", flush=True)
    return out


def currents_check(cfg: dict, coarse: mesh.Coarse, hours: dict[int, Path | dict], now: int, radar=None) -> dict | None:
    """The mesh's currents against high-frequency radar's measured surface
    currents (bay.toml [currents_check]), every past hour both have, at each
    radar cell with data at least half the hours and a mesh node within
    `near_m`. The radar sees the top metre or so; the mesh's currents are
    averaged over the depth, so the radar's run faster in a typical profile.
    Gives the mean speeds, the RMS of the vector difference, and the complex
    correlation (Kundu 1976, ref:kundu1976: its magnitude, 0 to 1, and the angle the mesh's
    currents turn from the radar's, degrees anticlockwise)."""
    cc = cfg.get("currents_check")
    if not cc:
        return None
    from fdmr_adapters import hfradar

    past = sorted(tt for tt in hours if tt <= now)
    if not past:
        return None
    n_hours = int((now - past[0]) // HOUR) + 2
    r = radar or hfradar.fetch(cc["server"], cc["dataset"], tuple(cc["box"]), min(n_hours, cc.get("hours", 24 * 16)))
    ok = np.isfinite(r.u) & np.isfinite(r.v)
    share = ok.mean(axis=0)
    cells = [(iy, ix) for iy, ix in zip(*np.where(share >= 0.5))]
    near_m = cc.get("near_m", 400.0)
    pairs_r, pairs_m, used = [], [], set()
    node_of = {}
    for iy, ix in cells:
        d = np.hypot((coarse.lon - r.lon[ix]) * np.cos(np.radians(r.lat[iy])), coarse.lat - r.lat[iy]) * fdmr_geo.M_PER_DEG
        k = int(np.argmin(d))
        if d[k] <= near_m:
            node_of[(iy, ix)] = k
    if not node_of:
        return {"cells": 0, "note": "no radar cell lies near a mesh node"}
    times = {int(round(t / HOUR)) * HOUR: i for i, t in enumerate(r.t)}
    for tt in past:
        i = times.get(int(tt))
        if i is None:
            continue
        h = hours[tt] if isinstance(hours[tt], dict) else dict(np.load(hours[tt]))
        if "u" not in h:
            continue
        for (iy, ix), k in node_of.items():
            if ok[i, iy, ix]:
                pairs_r.append(complex(r.u[i, iy, ix], r.v[i, iy, ix]))
                pairs_m.append(complex(float(h["u"][k]), float(h["v"][k])))
                used.add(tt)
    if not pairs_r:
        return {"cells": len(node_of), "hours": 0, "note": "no hour in common"}
    from fdmr_plugins.fvcom.correct import vector_skill

    return {"cells": len(node_of), "hours": len(used), **vector_skill(np.array(pairs_r), np.array(pairs_m)),
            "source": f"{cc['server']} {cc['dataset']}"}


def correct_quality(cfg: dict, coarse: mesh.Coarse, hours: dict[int, Path | dict], store_path: str) -> tuple[dict[int, dict], dict]:
    """The Bay's salinity and temperature corrected by the stations' measured
    misfit (bay.toml [quality]): each day, at each station, the measured daily
    mean less the model's at the station's node; spread over the mesh along the
    water (fdmr_plugins.fvcom.correct.spread); moved smoothly from day to day,
    and past the last measured day held at each station's mean misfit over its
    last three. The summary gives, per station, the mean misfit before and the
    mean miss left when the station itself is left out (an honest check: the
    correction there is made from the other stations alone)."""
    import fdmr_py
    from fdmr_plugins.fvcom.correct import station_correction, water_distances
    from fdmr_store import Store

    q = cfg["quality"]
    ts = np.array(sorted(hours))
    data = {tt: (hours[tt] if isinstance(hours[tt], dict) else dict(np.load(hours[tt]))) for tt in ts}
    have = [tt for tt in ts if "salinity" in data[tt] and "temperature" in data[tt]]
    store = Store(store_path)
    sts, nodes = [], []
    for st in q["stations"]:
        d = np.hypot((coarse.lon - st["lon"]) * np.cos(np.radians(st["lat"])), coarse.lat - st["lat"]) * fdmr_geo.M_PER_DEG
        k = int(np.argmin(d))
        if d[k] <= q.get("near_m", 2000.0):
            sts.append(st)
            nodes.append(k)
    dist = water_distances(coarse, nodes)
    from .delta import pst_midnight

    days = list(range(pst_midnight(int(ts[0])), int(ts[-1]) + 1, DAY))
    conv = {"salinity": lambda ec: np.asarray(fdmr_py.practical_salinity([float(x) for x in ec])), "temperature": lambda x: x}
    summary: dict = {}
    for field, key in (("salinity", "conductance"), ("temperature", "temperature")):
        # A station reporting salinity itself (psu) gives it directly (`salinity`).
        meas = np.array([station_daily(store, st, st["salinity"], lambda x: x, days) if field == "salinity" and "salinity" in st
                         else station_daily(store, st, st.get(key, []), conv[field], days) for st in sts])
        data, summary[field] = station_correction(data, have, days, field, nodes, dist, sts, meas)
        loo = summary[field]["stations"]
        print(f"{field} correction: " + "; ".join(f"{s} misfit {v['misfit']:+.2f}, left out {v['left_out']:.2f}" for s, v in loo.items()), flush=True)
    store.close()
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
    if delta_dir is not None and has_network(delta_dir):
        jn = join_of(delta_dir, coarse, cfg["reach"], edges)
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
    data = {t: (p if isinstance(p, dict) else dict(np.load(p))) for t, p in sorted(hours.items())}
    k = mesh.diffusivity(coarse, data)  # each node's own mixing (#10), from the hours published
    (pub / "geometry" / f"{reach}.json").write_text(json.dumps(mesh.geometry(coarse, reach, edges, k), separators=(",", ":"), allow_nan=False), encoding="utf-8")
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
    delta_dir = Path(args.delta).resolve() if args.delta and has_network(Path(args.delta)) else None
    if delta_dir is not None:
        edges = add_cut_edges(coarse, edges, join_of(delta_dir, coarse, cfg["reach"], edges), cfg.get("join", {}).get("cut_edges_m", 400.0))
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
    problems: list[dict] = []  # what failed in this run, published with it (#55)
    if args.delta and has_network(Path(args.delta)) and hours:
        try:
            ov = overlap(coarse, edges, hours, Path(args.delta).resolve(), cfg["reach"], now)
            if ov:
                extras["overlap"] = ov
            if args.store and cfg.get("outflow_checks"):
                jn = join_of(Path(args.delta).resolve(), coarse, cfg["reach"], edges)
                extras["outflow_checks"] = outflow_checks(cfg, Path(args.delta).resolve(), jn, args.store, now)
        except Exception as e:
            print(f"overlap FAILED {e}", flush=True)
            problems.append({"step": "overlap", "error": str(e)[:300]})
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
            problems.append({"step": "extension", "error": str(e)[:300]})
    assumed: list[dict] = [{"input": "NOAA's own forecast of the mesh", "how": "used as NOAA gives it, to its 48th hour"}]
    if any(isinstance(p, dict) for p in hours.values()):
        extras["extension_from"] = datetime.fromtimestamp(min(t for t, p in hours.items() if isinstance(p, dict)), timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        assumed.append({"input": "the Bay past NOAA's forecast", "how": f"from {extras['extension_from']}: tides plus outflow drift, fitted to the archive and driven by the Delta's forecast outflow; temperature and salinity held at NOAA's last forecast hour"})
    if args.delta and has_network(Path(args.delta)) and hours and network_id(Path(args.delta)) in edges:
        try:
            hours, extras["outflow_correction"] = correct_outflow(coarse, edges, hours, Path(args.delta).resolve(), cfg["reach"])
        except Exception as e:  # uncorrected, the Bay still has NOAA's own currents
            print(f"outflow correction FAILED {e}", flush=True)
            problems.append({"step": "outflow correction", "error": str(e)[:300]})
    if args.store and "quality" in cfg and hours:
        assumed.append({"input": "the Bay's salinity and temperature correction", "how": "each station's misfit spread along the water; past the last measured day, held at each station's mean misfit over its last three days"})
        try:
            hours, extras["quality_correction"] = correct_quality(cfg, coarse, hours, args.store)
        except Exception as e:  # uncorrected, the Bay keeps NOAA's own salinity and temperature
            print(f"quality correction FAILED {e}", flush=True)
            problems.append({"step": "quality correction", "error": str(e)[:300]})
    if delta_dir is not None and hours:
        try:
            hours = {tt: (p if isinstance(p, dict) else dict(np.load(p))) for tt, p in hours.items()}
            extras["quality_seam"] = seam_quality(hours, coarse, delta_dir, join_of(delta_dir, coarse, cfg["reach"], edges),
                                                  ("salinity", "temperature"), cfg.get("join", {}).get("seam_m", 2000.0))
        except Exception as e:  # the mesh keeps its own values at the join
            print(f"quality seam FAILED {e}", flush=True)
            problems.append({"step": "quality seam", "error": str(e)[:300]})
    if hours and cfg.get("currents_check"):
        try:
            extras["currents_check"] = currents_check(cfg, coarse, hours, now)
            print(f"currents check: {extras['currents_check']}", flush=True)
        except Exception as e:  # a check; the currents are published whatever it says
            print(f"currents check FAILED {e}", flush=True)
            problems.append({"step": "currents check", "error": str(e)[:300]})
    extras["assumptions"] = assumed
    if problems:
        extras["problems"] = problems
    if args.publish:
        pub = publish(cfg, region_dir, coarse, edges, hours, now, latest, Path(args.publish), extras=extras)
        size = sum(p.stat().st_size for p in pub.rglob("*") if p.is_file())
        print(f"published {pub} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
