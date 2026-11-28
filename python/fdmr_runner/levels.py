"""The models' water levels against the stations' measured levels (issue #9's
list: a standing check of the tidal models' stage).

A station's gauge and a model rarely share a datum: a gauge often reads on its
own local zero. So the check compares what does not depend on the datum:

- `rms_m`: the moment-to-moment miss once each series' own mean over the
  window is taken away (m);
- `range_ratio`: the model's tidal range against the station's (the spread
  between the 5th and 95th percentiles of each);
- `lag_min`: how late the model's tide runs, by the lag that best lines the two
  up (minutes; negative is early);
- `mean_offset_m`: the model's mean less the station's: meaningful only where
  both are on the same datum, so given for the record, not as a check.

Every map station (overlays.toml) that measures its level (`gage_height`) and
stands within MODEL_NEAR_KM of a reach that publishes `level` (a network's
channel or a mesh's node; a channel another reach owns is skipped) is checked
over the last WINDOW_DAYS days the cycle has.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from fdmr_store import Store

from . import ledger, overlays

WINDOW_DAYS = 7  # days of the cycle's measured past checked (FDMR's choice)
MODEL_NEAR_KM = 1.0  # km: a station is checked against a reach it stands within this of (FDMR's choice)
MAX_LAG_S = 3 * 3600  # s: the largest shift searched when lining the tides up, 3 hours (FDMR's choice)
MIN_FRACTION = 0.6  # of the window's steps with both values


def lookups(cdir: Path, index: dict) -> list[tuple[overlays.NetworkValues, dict]]:
    out = []
    for r in index["reaches"]:
        if "level" not in [f["name"] if isinstance(f, dict) else f for f in r.get("fields", [])]:
            continue
        lk = overlays.NetworkValues(cdir, {**r, "time": r.get("time", index["time"])}, [])
        lk.skip = frozenset(c for h in index.get("handoffs", []) if h.get("from") == r["id"] for c in h.get("owned", []))
        out.append((lk, r))
    return out


def compare(model: np.ndarray, measured: np.ndarray, step_s: int) -> dict | None:
    """The datum-free comparison of two series on the same steps (NaN where missing)."""
    ok = np.isfinite(model) & np.isfinite(measured)
    if ok.sum() < MIN_FRACTION * len(model) or ok.sum() < 8:
        return None
    m = np.where(ok, model - model[ok].mean(), np.nan)
    s = np.where(ok, measured - measured[ok].mean(), np.nan)
    rng = lambda a: float(np.nanpercentile(a, 95) - np.nanpercentile(a, 5))  # noqa: E731
    best, best_k = -np.inf, 0
    for k in range(-MAX_LAG_S // step_s, MAX_LAG_S // step_s + 1):
        a, b = (m[k:], s[:len(s) - k]) if k >= 0 else (m[:k], s[-k:])
        both = np.isfinite(a) & np.isfinite(b)
        if both.sum() > 8:
            c = float(np.mean(a[both] * b[both]))
            if c > best:
                best, best_k = c, k
    return {"steps": int(ok.sum()), "rms_m": round(float(np.sqrt(np.nanmean((m - s) ** 2))), 3),
            "range_ratio": round(rng(m) / rng(s), 3) if rng(s) > 0 else None, "lag_min": int(best_k * step_s // 60),
            "mean_offset_m": round(float(model[ok].mean() - measured[ok].mean()), 3)}


def level_checks(cfg: dict, cdir: Path, index: dict, store: Store, now: int) -> list[dict]:
    from .forecast import on_grid

    lks = lookups(cdir, index)
    out = []
    t_from = now - WINDOW_DAYS * 86_400
    # Each station's nearest channel first; then each reach's level read once,
    # for all its stations' channels together.
    placed = []
    for st in cfg.get("stations", []):
        near = None
        for lk, r in lks:
            c, d = lk.channel_near(st["lon"], st["lat"])
            if d <= MODEL_NEAR_KM and (near is None or d < near[3]):
                near = (lk, r, c, d)
        if near:
            placed.append((st, *near))
    levels: dict[str, tuple | None] = {}
    for rid in dict.fromkeys(r["id"] for _, _, r, _, _ in placed):
        r = next(r for _, _, r, _, _ in placed if r["id"] == rid)
        cols = sorted({c for _, _, rr, c, _ in placed if rr["id"] == rid})
        got = ledger.series(cdir, r, r.get("time", index["time"]), "level", cols, t_from)
        levels[rid] = None if got is None else (got[0], got[1], got[2], {c: j for j, c in enumerate(cols)})
    for st, lk, r, c, d in placed:
        src = overlays.station_source(cfg, st)
        got = levels.get(r["id"])
        if got is None:
            continue
        t0, step, all_v, col = got
        v = all_v[:, [col[c]]]
        ts = t0 + step * np.arange(len(v))
        keep = (ts >= t_from) & (ts <= now)
        if not keep.any():
            continue
        vals = [(x.t, x.value) for x in store.values(src, st["site"], "gage_height", t_from - 3600, now + 1) if x.value is not None]
        if len(vals) < 8:
            continue
        g0 = int(ts[keep][0])
        meas = np.array([np.nan if x is None else x for x in on_grid(vals, g0, step, int(keep.sum()), max_gap_s=3600)], dtype=float)
        res = compare(v[keep, 0].astype(float), meas, step)
        if res:
            out.append({"site": st["site"], "name": st["name"], "source": src, "model_reach": r["id"], "model_km": round(d, 2), **res})
    return out

