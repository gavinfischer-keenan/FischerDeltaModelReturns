"""How wet the land is where rain runs off into the water (issue #12;
region.toml [soil_moisture]; fdmr_adapters.smap).

The same rain runs off wet ground and soaks into dry. The water that no gauge
measures comes off the land nearest the water: along the rivers below their
dams (what falls above a dam is caught and shows in its inflow), on the Delta's
islands, and round the Bay. So soil moisture is kept for:

- `river:<reach>`: the land within `corridor_m` of each river's centreline;
- `area:<name>`: each of [[soil_moisture.areas]], a box (west, south, east,
  north).

Each cycle publishes every area's latest values, and how its root zone has
changed over the last week: a record for the validation work (#9), to see
whether storm misses follow wet ground, before any use as an input.
"""

from __future__ import annotations

import json
import math
import tomllib
from pathlib import Path

from fdmr_store import Store

SOURCE = "smap"
POINT_SPACING_M = 2_000.0  # centreline points kept for a corridor: far closer than its 9 km cells
FRESH_S = 4 * 86_400  # SMAP's Level 4 runs about two days behind


def load(region_dir: Path) -> dict | None:
    return tomllib.loads((region_dir / "region.toml").read_text(encoding="utf-8")).get("soil_moisture")


def areas(region_dir: Path) -> list[dict]:
    cfg = load(region_dir)
    if not cfg:
        return []
    out = []
    reaches = [r["id"] for r in tomllib.loads((region_dir / "region.toml").read_text(encoding="utf-8")).get("reaches", [])
               if r.get("kind") == "river-routing"]
    for reach in reaches:
        path = region_dir / f"{reach}.json"
        if not path.exists():
            continue
        c = json.loads(path.read_text(encoding="utf-8"))
        keep, last = [], -math.inf
        for (lo, la), x in zip(c["coords"], c["chainage_m"]):
            if x - last >= POINT_SPACING_M:
                keep.append((lo, la))
                last = x
        out.append({"site": f"river:{reach}", "points": keep, "radius_m": float(cfg.get("corridor_m", 10_000.0))})
    for a in cfg.get("areas", []):
        out.append({"site": f"area:{a['name']}", "box": list(a["box"])})
    return out


def _latest(store: Store, site: str, p: str, t0: int, t1: int):
    vs = [v for v in store.values(SOURCE, site, p, t0, t1) if v.value is not None]
    return vs[-1] if vs else None


def summary(region_dir: Path, store: Store, now: int) -> dict | None:
    """Each area's latest soil moisture and its root zone's change over a week."""
    al = areas(region_dir)
    if not al:
        return None
    rows = []
    for a in al:
        rz = _latest(store, a["site"], "soil_moisture_rootzone", now - FRESH_S, now + 1)
        if rz is None:
            continue
        row = {"site": a["site"], "t": rz.t, "rootzone": round(rz.value, 3)}
        for p, k, nd in (("soil_moisture_surface", "surface", 3), ("soil_wetness_rootzone", "wetness", 3),
                         ("soil_moisture_rootzone_percentile", "percentile", 1)):
            v = _latest(store, a["site"], p, rz.t, rz.t + 1)
            if v is not None:
                row[k] = round(v.value, nd)
        week = _latest(store, a["site"], "soil_moisture_rootzone", rz.t - 7 * 86_400 - 3 * 3600, rz.t - 7 * 86_400 + 1)
        if week is not None:
            row["rootzone_change_7d"] = round(rz.value - week.value, 3)
        rows.append(row)
    return {"source": "NASA SMAP Level 4 SPL4SMGP v008 (NSIDC DAAC)", "corridor_m": float(load(region_dir).get("corridor_m", 10_000.0)),
            "areas": rows}
