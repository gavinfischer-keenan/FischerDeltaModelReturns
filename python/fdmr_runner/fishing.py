"""The sturgeon forecast (issue #19): where, day by day over the cycle's
forecast, the water the model predicts suits a region's sturgeon.

Applies the region's published-evidence rules (regions/<id>/fishing.toml,
every number with its source) to the cycle just published: each network
channel's and mesh node's daily mean salinity and temperature, and each
listed river's daily mean flow at its end. Writes `fishing.json` beside the
cycle's index (docs/format.md). Fits nothing: the rules are the sources',
and the forecast is unvalidated until catch or survey data are in hand
(fishing.toml says which).

Per species and day:
- a target species (white sturgeon): a score 0-100 for every element of the
  listed reaches, season x salinity band (0 in a closed month); elements whose
  temperature is outside the range fish were caught in are listed, not scored
  down; listed rivers whose day's mean flow rises by the given amount over the
  day before, in the migration months, are flagged;
- a protected species (green sturgeon): the elements where it is found in the
  season and the temperature and salinity ranges it has been tracked in, to
  avoid.
"""

from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import fdmr_py

DAY = 86_400


def load(region_dir: Path) -> dict | None:
    p = Path(region_dir) / "fishing.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def daily(cdir: Path, reach: str, day: str, field: str, quality: bool = True) -> np.ndarray | None:
    """The day's mean of one field, per element, or None if the cycle has none."""
    f = cdir / reach / f"{day}.{'quality.' if quality else ''}fdf"
    if not f.exists():
        return None
    _, _, n, _, steps, _, _, flds = fdmr_py.read_field_file(str(f))
    for name, _, vals in flds:
        if name == field:
            a = np.asarray(vals, dtype=float).reshape(steps, n)
            with np.errstate(invalid="ignore"):
                return np.nanmean(a, axis=0) if np.isfinite(a).any() else None
    return None


def salinity_score(s: np.ndarray, bands: list[dict], migrating: bool) -> np.ndarray:
    """Each element's score for its salinity: the first band it is below."""
    out = np.full(s.shape, np.nan)
    for b in reversed(bands):  # the lowest band that holds wins
        v = b.get("score_in_migration", b["score"]) if migrating else b["score"]
        out = np.where(s < b["below"], v, out)
    return np.where(np.isfinite(s), out, np.nan)


def element_places(cdir: Path, reach: str) -> list[tuple[float, float]] | None:
    """A point for each element, to name where it is: a channel's middle vertex, a node."""
    g = cdir / "geometry" / f"{reach}.json"
    if not g.exists():
        return None
    d = json.loads(g.read_text(encoding="utf-8"))
    if "lon" in d and "lat" in d:
        return list(zip(d["lon"], d["lat"]))
    if "lines" in d and d.get("network"):
        return [tuple(line[len(line) // 2]) for line in d["lines"]]
    return None


def forecast(region_dir: Path, cdir: Path, index: dict) -> dict | None:
    cfg = load(region_dir)
    if not cfg:
        return None
    now = datetime.strptime(index["now"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    reaches = {r["id"]: r for r in index["reaches"]}
    days = sorted({d for r in index["reaches"] for d in r.get("time", {}).get("days", []) if d >= now.strftime("%Y-%m-%d")})
    places = {r: element_places(cdir, r) for r in reaches}
    out: dict = {"generated_for_cycle": index["cycle"], "now": index["now"], "days": days, "species": {}}

    w = cfg.get("white_sturgeon")
    if w:
        peak = max(w["monthly_catch_percent"])
        sp: dict = {"name": w["name"], "status": w["status"], "kind": "target", "days": []}
        prev_flow: dict[str, float] = {}
        for day in days:
            month = int(day[5:7])
            season = 0.0 if month in w["closed_months"] else w["monthly_catch_percent"][month - 1] / peak
            migrating = month in w["migration_months"]
            entry = {"date": day, "season": round(season, 3), "closed": month in w["closed_months"], "reaches": {}, "outside_caught_temperature": {}, "migration": []}
            best = []
            for reach in w["reaches"]:
                if reach not in reaches:
                    continue
                s, t = daily(cdir, reach, day, "salinity"), daily(cdir, reach, day, "temperature")
                if s is None:
                    continue
                score = season * salinity_score(s, w["salinity_bands"], migrating)
                entry["reaches"][reach] = [None if not np.isfinite(v) else int(round(100 * v)) for v in score]
                if t is not None:
                    lo, hi = w["caught_temperature_c"]
                    entry["outside_caught_temperature"][reach] = int(np.sum((t < lo) | (t > hi)))
                pl = places.get(reach)
                for i in np.argsort(-np.nan_to_num(score, nan=-1))[:10]:
                    if np.isfinite(score[i]):
                        best.append({"reach": reach, "element": int(i), "score": int(round(100 * score[i])),
                                     "salinity": round(float(s[i]), 2), "temperature": None if t is None else round(float(t[i]), 1),
                                     **({"lon": pl[i][0], "lat": pl[i][1]} if pl and i < len(pl) else {})})
            entry["best"] = sorted(best, key=lambda b: -b["score"])[:10]
            # The bands are coarse, so many places share a score: say how many.
            top = max((v for sc in entry["reaches"].values() for v in sc if v is not None), default=None)
            entry["best_score"] = top
            entry["places_at_best"] = {r: sum(1 for v in sc if v == top) for r, sc in entry["reaches"].items()} if top is not None else {}
            for reach in w.get("migration_rivers", []):
                q = daily(cdir, reach, day, "flow", quality=False)
                if q is None:
                    continue
                end = float(q[-1])
                if migrating and reach in prev_flow and end - prev_flow[reach] >= w["migration_rise_m3s"]:
                    entry["migration"].append({"reach": reach, "flow_m3s": round(end, 1), "rise_m3s": round(end - prev_flow[reach], 1)})
                prev_flow[reach] = end
            sp["days"].append(entry)
        out["species"]["white_sturgeon"] = sp

    g = cfg.get("green_sturgeon")
    if g:
        sp = {"name": g["name"], "status": g["status"], "kind": "protected", "days": []}
        for day in days:
            month = int(day[5:7])
            entry = {"date": day, "in_season": month in g["months"], "reaches": {}}
            for reach in g["reaches"]:
                if reach not in reaches or month not in g["months"]:
                    continue
                s, t = daily(cdir, reach, day, "salinity"), daily(cdir, reach, day, "temperature")
                if s is None or t is None:
                    continue
                inside = (s >= g["salinity_psu"][0]) & (s <= g["salinity_psu"][1]) & (t >= g["temperature_c"][0]) & (t <= g["temperature_c"][1])
                entry["reaches"][reach] = [int(i) for i in np.flatnonzero(inside)]
            sp["days"].append(entry)
        out["species"]["green_sturgeon"] = sp
    out["rules"] = cfg
    return out
