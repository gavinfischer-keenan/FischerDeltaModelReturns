"""The cyanobacteria bloom forecast (issue #28): where, day by day over the
cycle's forecast, the water the model predicts favours Microcystis.

Applies the region's published-evidence rules (regions/<id>/blooms.toml,
every number with its source) to the cycle just published: each listed
reach's daily mean water temperature and salinity, per element. Writes
`blooms.json` beside the cycle's index (docs/format.md). Fits nothing, and is
not yet scored against the surveys' bloom index (blooms.toml says which).

Per day and element: 0 unlikely (salinity at or above the limit, or water
colder than the season's gate), 1 possible (between the gate and the
temperature at which Microcystis outgrows diatoms), 2 likely (above it).
"""

from __future__ import annotations

import tomllib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .fishing import daily, element_places


def load(region_dir: Path) -> dict | None:
    p = Path(region_dir) / "blooms.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def classes(t: np.ndarray, s: np.ndarray, gate: float, strong: float, salinity_below: float) -> np.ndarray:
    """Each element's class: 0 unlikely, 1 possible, 2 likely; -1 where unknown."""
    c = np.where(t >= strong, 2, np.where(t >= gate, 1, 0))
    c = np.where(s >= salinity_below, 0, c)
    return np.where(np.isfinite(t) & np.isfinite(s), c, -1)


def forecast(region_dir: Path, cdir: Path, index: dict) -> dict | None:
    cfg = load(region_dir)
    if not cfg:
        return None
    now = datetime.strptime(index["now"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    reaches = {r["id"] for r in index["reaches"]}
    days = sorted({d for r in index["reaches"] for d in r.get("time", {}).get("days", []) if d >= now.strftime("%Y-%m-%d")})
    gate, strong = cfg["temperature_c"]
    out: dict = {"generated_for_cycle": index["cycle"], "now": index["now"], "days": [], "rules": cfg}
    for day in days:
        entry: dict = {"date": day, "reaches": {}, "counts": {}, "warmest": []}
        warm = []
        for reach in cfg["reaches"]:
            if reach not in reaches:
                continue
            t, s = daily(cdir, reach, day, "temperature"), daily(cdir, reach, day, "salinity")
            if t is None or s is None:
                continue
            c = classes(t, s, gate, strong, cfg["salinity_below_psu"])
            entry["reaches"][reach] = [int(v) for v in c]
            entry["counts"][reach] = {"unlikely": int((c == 0).sum()), "possible": int((c == 1).sum()), "likely": int((c == 2).sum())}
            pl = element_places(cdir, reach)
            for i in np.flatnonzero(c > 0):
                warm.append({"reach": reach, "element": int(i), "class": int(c[i]), "temperature": round(float(t[i]), 1),
                             "salinity": round(float(s[i]), 2), **({"lon": pl[i][0], "lat": pl[i][1]} if pl and i < len(pl) else {})})
        entry["warmest"] = sorted(warm, key=lambda w: -w["temperature"])[:10]
        out["days"].append(entry)
    return out
