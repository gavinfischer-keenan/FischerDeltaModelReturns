"""The prediction ledger (issue #9): what each cycle predicted at every check
point, kept beside what the cycle was built from, and scored against what was
measured once the time has come.

- Check points: every map station where a model covers it (overlays.toml, the
  quality fields: temperature, salinity, conductance), every river gauge
  below a release (region.toml `role = "main"`: flow at its element), and
  every network flow check that names its channel (dsm2.toml [[checks]] with
  `channel`: the channel's flow, the mean of its two ends).
- Recorded: from a published cycle at least EVERY_S after the last one kept,
  each point's predicted value at the cycle's time and ahead of it, hourly to
  48 hours and six-hourly after (LEADS_S), read from the published files
  themselves, as the map shows them. Beside them, the cycle's own description
  of how it was made (index.json, without its large arrays).
- Scored: each prediction whose time has passed against the reading nearest
  that time (within MATCH_S), as measured less predicted, grouped by kind of
  cycle (`live`, or `hindcast`: a run made afterwards as of a past time),
  reach, quantity and lead time (LEAD_BANDS), over the last WINDOW_DAYS days;
  beside it the same for persistence, the benchmark that nothing changes (the
  last reading at the cycle's time, held), and the skill against it
  (1 - RMS / persistence's RMS: above 0 beats it, 1 is perfect).

The ledger is one SQLite file per region, apart from the measurement store.
Nothing here changes a prediction: it only keeps and scores them.
"""

from __future__ import annotations

import calendar
import json
import sqlite3
import time
import zlib
from pathlib import Path

import numpy as np

from fdmr_store import Store

from .region import load_region

HOUR = 3600
EVERY_S = 6 * HOUR - 15 * 60  # a cycle is kept when the last kept is this old (four a day)
LEADS_S = [h * HOUR for h in range(0, 48)] + [h * HOUR for h in range(48, 15 * 24 + 1, 6)]
MATCH_S = 30 * 60  # a reading counts for a prediction within this of its time
WINDOW_DAYS = 30
LEAD_BANDS = [("0-6 h", 0, 6), ("6-24 h", 6, 24), ("1-2 days", 24, 48), ("2-5 days", 48, 120), ("5-15 days", 120, 361)]
MODEL_NEAR_KM = 1.0
BIG_KEYS = ("certainty", "lines")  # index.json arrays too large to keep with every cycle

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (cycle INTEGER PRIMARY KEY, recorded INTEGER NOT NULL, made TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'live');
CREATE TABLE IF NOT EXISTS points (
    id INTEGER PRIMARY KEY, reach TEXT NOT NULL, source TEXT NOT NULL, site TEXT NOT NULL,
    parameter TEXT NOT NULL, field TEXT NOT NULL, element INTEGER NOT NULL, name TEXT,
    UNIQUE (reach, source, site, parameter, field, element));
CREATE TABLE IF NOT EXISTS predictions (
    cycle INTEGER NOT NULL, point INTEGER NOT NULL, t INTEGER NOT NULL, value REAL NOT NULL,
    PRIMARY KEY (cycle, point, t)) WITHOUT ROWID;
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.executescript(SCHEMA)
    if "kind" not in [r[1] for r in db.execute("PRAGMA table_info(cycles)")]:  # a ledger from before hindcasts
        db.execute("ALTER TABLE cycles ADD COLUMN kind TEXT NOT NULL DEFAULT 'live'")
        db.commit()
    return db


def stamp(t: str | int) -> int:
    """A published time ("2026-11-26T17:45Z") as UTC seconds."""
    return calendar.timegm(time.strptime(t, "%Y-%m-%dT%H:%MZ")) if isinstance(t, str) else int(t)


# --- Check points --------------------------------------------------------------------------


def check_points(region_dir: Path, cdir: Path, index: dict, store: Store) -> list[dict]:
    """Where predictions can be checked, each {reach, source, site, parameter,
    field, element, name}: `field` is the published field (`flow` meaning the
    mean of a network channel's two ends where it has two). A station counts
    for what it has measured (in the store)."""
    from . import delta, overlays

    reaches = {r["id"]: r for r in index["reaches"]}
    points: list[dict] = []
    # Map stations, against whichever reach's quality covers them (its geometry
    # only: the values are read later, for the points found).
    ocfg = overlays.load(region_dir) or {}
    lookups = overlays.model_lookups(cdir, index, load=False)
    for st in ocfg.get("stations", []):
        near = None
        for lk in lookups:
            c, d = lk.channel_near(st["lon"], st["lat"])
            if d <= MODEL_NEAR_KM and (near is None or d < near[2]):
                near = (lk, c, d)
        if not near:
            continue
        src = st.get("source", "cdec")
        for p, f in overlays.SHOWN.items():
            if f and f in reaches[near[0].reach].get("quality_fields", []) and store.latest(src, st["site"], p):
                points.append({"reach": near[0].reach, "source": src, "site": st["site"], "parameter": p,
                               "field": f, "element": int(near[1]), "name": st["name"]})
    # River gauges below each release: flow at the gauge's element.
    region = load_region(region_dir)
    for g in region.gauges:
        r = reaches.get(g.reach)
        line = region_dir / f"{g.reach}.json"
        if g.role != "main" or not r or r.get("network") or r.get("dims") != 1 or not line.exists():
            continue
        at = json.loads(line.read_text(encoding="utf-8")).get("gauges_m", {})
        x = at.get(g.site, at.get(g.usgs_site or ""))
        if x is None:
            continue
        els = json.loads((cdir / "geometry" / f"{g.reach}.json").read_text(encoding="utf-8"))["elements"]
        k = next((i for i, e in enumerate(els) if e["start_m"] <= x < e["end_m"]), len(els) - 1 if x >= els[-1]["end_m"] - 1 else None)
        if k is not None:
            points.append({"reach": g.reach, "source": g.source, "site": g.site, "parameter": g.parameter,
                           "field": "flow", "element": k, "name": g.name})
    # A network's flow checks that name their channel.
    dcfg = delta.load(region_dir) or {}
    net = dcfg.get("reach", "delta")
    if net in reaches and (cdir / "geometry" / f"{net}.json").exists():
        ids = {e["id"]: i for i, e in enumerate(json.loads((cdir / "geometry" / f"{net}.json").read_text(encoding="utf-8"))["elements"])}
        for c in dcfg.get("checks", []):
            if "channel" in c and c["channel"] in ids:
                points.append({"reach": net, "source": c.get("source", "cdec"), "site": c["site"], "parameter": c.get("parameter", "discharge"),
                               "field": "flow", "element": ids[c["channel"]], "name": c["name"]})
    return points


# --- Reading the published values ----------------------------------------------------------


def series(cdir: Path, entry: dict, time_axis: dict, field: str, columns: list[int], t_from: int) -> tuple[int, int, np.ndarray] | None:
    """A published field's values at some elements from the day holding
    `t_from` on: (t0, step, values[step, column]). `flow` on a network is the
    mean of `flow_up` and `flow_down`."""
    import fdmr_py

    quality = field not in ("flow", "flow_up", "flow_down", "area", "level", "velocity", "volume")
    two_ends = entry.get("network") or entry.get("kind") == "canal-network"  # flow at each end of a channel
    names = ["flow_up", "flow_down"] if field == "flow" and two_ends else [field]
    t0 = step = None
    parts = []
    for day in time_axis["days"]:
        if stamp(f"{day}T00:00Z") + 86_400 <= t_from:
            continue
        path = cdir / entry["id"] / (f"{day}.quality.fdf" if quality else f"{day}.fdf")
        if not path.exists():
            if parts:
                break
            continue
        _, _, n, _, steps, d0, st, flds = fdmr_py.read_field_file(str(path))
        got = {name: v for name, _, v in flds}
        if not all(nm in got for nm in names):
            return None
        t0, step = (d0, st) if t0 is None else (t0, step)
        parts.append(np.mean([np.asarray(got[nm], dtype=np.float32).reshape(steps, n)[:, columns] for nm in names], axis=0))
    return None if not parts else (t0, step, np.concatenate(parts))


def at_times(t0: int, step: int, v: np.ndarray, ts: np.ndarray) -> np.ndarray:
    """Values at times ts, linear between steps; NaN outside."""
    pos = (ts - t0) / step
    out = np.full((len(ts), v.shape[1]), np.nan)
    ok = (pos >= 0) & (pos <= len(v) - 1)
    i = np.floor(pos[ok]).astype(int)
    f = (pos[ok] - i)[:, None]
    j = np.minimum(i + 1, len(v) - 1)
    out[ok] = v[i] * (1 - f) + v[j] * f
    return out


# --- Recording -----------------------------------------------------------------------------


def made_of(index: dict) -> dict:
    """The cycle's own account of how it was made: index.json without its large arrays."""
    return {**{k: v for k, v in index.items() if k != "reaches"},
            "reaches": [{k: v for k, v in r.items() if k not in BIG_KEYS} for r in index["reaches"]]}


def record(db: sqlite3.Connection, region_dir: Path, cdir: Path, index: dict, store: Store, every_s: int = EVERY_S,
           kind: str = "live") -> int:
    """Keep this cycle's predictions if the last kept cycle of its kind is
    `every_s` old (a hindcast is always kept); returns how many values were
    kept (0 when it is too soon)."""
    now = stamp(index["now"])
    last = db.execute("SELECT MAX(cycle) FROM cycles WHERE kind=?", (kind,)).fetchone()[0]
    if kind == "live" and last is not None and now - last < every_s:
        return 0
    reaches = {r["id"]: r for r in index["reaches"]}
    pts = check_points(region_dir, cdir, index, store)
    ids = []
    for p in pts:
        db.execute("INSERT OR IGNORE INTO points (reach, source, site, parameter, field, element, name) VALUES (?,?,?,?,?,?,?)",
                   (p["reach"], p["source"], p["site"], p["parameter"], p["field"], p["element"], p["name"]))
        ids.append(db.execute("SELECT id FROM points WHERE reach=? AND source=? AND site=? AND parameter=? AND field=? AND element=?",
                              (p["reach"], p["source"], p["site"], p["parameter"], p["field"], p["element"])).fetchone()[0])
    ts = np.array([now + lead for lead in LEADS_S])
    rows = []
    groups: dict[tuple[str, str], list[int]] = {}
    for i, p in enumerate(pts):
        groups.setdefault((p["reach"], p["field"]), []).append(i)
    for (reach, field), members in groups.items():
        entry = reaches[reach]
        got = series(cdir, entry, entry.get("time", index["time"]), field, [pts[i]["element"] for i in members], now)
        if got is None:
            continue
        vals = at_times(*got, ts)
        for col, i in enumerate(members):
            rows += [(now, ids[i], int(t), float(v)) for t, v in zip(ts, vals[:, col]) if np.isfinite(v)]
    db.execute("INSERT OR REPLACE INTO cycles (cycle, recorded, made, kind) VALUES (?,?,?,?)",
               (now, int(time.time()), zlib.compress(json.dumps(made_of(index), separators=(",", ":")).encode()).hex(), kind))
    db.executemany("INSERT OR REPLACE INTO predictions VALUES (?,?,?,?)", rows)
    db.commit()
    return len(rows)


# --- Scoring -------------------------------------------------------------------------------


def _band(lead_s: int) -> str | None:
    h = lead_s / HOUR
    return next((name for name, a, b in LEAD_BANDS if a <= h < b), None)


def _summary(d: list[tuple[float, float | None]]) -> dict:
    """Pairs of (model misfit, persistence misfit), measured less predicted."""
    a = np.array([m for m, _ in d])
    out = {"pairs": int(a.size), "mean_misfit": round(float(a.mean()), 3), "rms_misfit": round(float(np.sqrt((a ** 2).mean())), 3)}
    p = np.array([x for _, x in d if x is not None])
    if p.size == a.size and p.size:
        rp = float(np.sqrt((p ** 2).mean()))
        out["rms_persistence"] = round(rp, 3)
        if rp > 0:
            out["skill_vs_persistence"] = round(1 - out["rms_misfit"] / rp, 3)
    return out


def score(db: sqlite3.Connection, store: Store, now: int, days: int = WINDOW_DAYS) -> dict:
    """Every kept prediction of the last `days` whose time has come, against
    the reading nearest its time: measured less predicted, by reach, quantity
    and lead time, overall and per point."""
    t_from = now - days * 86_400
    by_group: dict[tuple, list] = {}
    per_point = []
    kinds = dict(db.execute("SELECT cycle, kind FROM cycles").fetchall())
    for pid, reach, source, site, parameter, name in db.execute(
            "SELECT id, reach, source, site, parameter, name FROM points ORDER BY reach, name").fetchall():
        preds = db.execute("SELECT cycle, t, value FROM predictions WHERE point=? AND t>=? AND t<=?", (pid, t_from, now)).fetchall()
        if not preds:
            continue
        vs = [(v.t, v.value) for v in store.values(source, site, parameter, t_from - 30 * 86_400, now + MATCH_S) if v.value is not None]
        if not vs:
            continue
        mt = np.array([t for t, _ in vs])
        mv = np.array([v for _, v in vs])
        bands: dict[tuple[str, str], list] = {}
        for cycle, t, value in preds:
            j = int(np.searchsorted(mt, t))
            cand = [k for k in (j - 1, j) if 0 <= k < len(mt)]
            k = min(cand, key=lambda k: abs(mt[k] - t))
            band = _band(t - cycle)
            if abs(mt[k] - t) <= MATCH_S and band:
                c = int(np.searchsorted(mt, cycle, side="right")) - 1  # persistence: the last reading at the cycle's time
                persist = float(mv[k]) - float(mv[c]) if c >= 0 and cycle - mt[c] <= 6 * 3600 else None
                kind = kinds.get(cycle, "live")
                bands.setdefault((kind, band), []).append((float(mv[k]) - value, persist))
                by_group.setdefault((kind, reach, parameter, band), []).append((float(mv[k]) - value, persist))
        if bands:
            per_point.append({"reach": reach, "source": source, "site": site, "parameter": parameter, "name": name,
                              "leads": {f"{kd} {b}": _summary(d) for (kd, b), d in sorted(bands.items(), key=lambda kv: (kv[0][0], [n for n, *_ in LEAD_BANDS].index(kv[0][1])))}})
    order = [n for n, *_ in LEAD_BANDS]
    groups = [{"kind": k, "reach": r, "parameter": p, "lead": b, **_summary(d)}
              for (k, r, p, b), d in sorted(by_group.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], order.index(kv[0][3])))]
    n_cycles = db.execute("SELECT kind, COUNT(*) FROM cycles WHERE cycle>=? GROUP BY kind", (t_from,)).fetchall()
    return {"generated": now, "window_days": days, "cycles": sum(n for _, n in n_cycles), "cycles_by_kind": dict(n_cycles),
            "lead_bands": order, "groups": groups, "points": per_point}


def run(region_dir: Path, cdir: Path, index: dict, store: Store, ledger_path: Path, now: int) -> dict:
    """Keep this cycle (when due) and score the ledger: the cycle's skill.json."""
    db = connect(ledger_path)
    try:
        kept = record(db, region_dir, cdir, index, store)
        out = score(db, store, now)
        out["kept_this_cycle"] = kept
        return out
    finally:
        db.close()
