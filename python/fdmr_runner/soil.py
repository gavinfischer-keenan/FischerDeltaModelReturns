"""How wet the land is where rain runs off into the water (issue #12;
region.toml [soil_moisture]; fdmr_adapters.smap).

The same rain runs off wet ground and soaks into dry. The water that no gauge
measures comes off the land nearest the water: along the rivers below their
dams (what falls above a dam is caught and shows in its inflow), on the Delta's
islands, and round the Bay. So soil moisture is kept for:

- `river:<reach>`: the land within `corridor_m` of each river's centreline;
- `area:<name>`: each of [[soil_moisture.areas]], a box (west, south, east,
  north).

With it, the water reaching the same ground: the National Weather Service's
daily precipitation analysis (fdmr_adapters.nws_precip: radar adjusted to
gauges, beside the PRISM normal) and SNODAS's daily snowmelt and snowpack
(fdmr_adapters.snodas: modelled). Each cycle publishes every area's latest
soil moisture and how its root zone has changed over the last week, its rain
over the last day and week against normal, and its snowmelt over the week: a
record for the validation work (#9), to see whether storm misses follow wet
ground and heavy rain or melt, before any use as an input.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from fdmr_adapters import nws_precip, openet, smap, snodas
from fdmr_store import Store

from .region import load_region

SOURCE = smap.SOURCE
POINT_SPACING_M = 2_000.0  # centreline points kept for a corridor: far closer than its 9 km cells
FRESH_S = 4 * 86_400  # SMAP's Level 4 runs about two days behind


OPENET_AGE_DAYS = 45  # OpenET's evapotranspiration runs days to weeks behind: shown up to this old (FDMR's choice)

def load(region_dir: Path) -> dict | None:
    return load_region(region_dir).soil_moisture


def areas(region_dir: Path) -> list[dict]:
    cfg = load(region_dir)
    if not cfg:
        return []
    out = []
    reaches = [r["id"] for r in load_region(region_dir).reaches if r.get("kind") == "river-routing"]
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


SAMPLE_GRID = (3, 2)  # OpenET samples across an area: columns, rows
SAMPLE_SIZE = (0.10, 0.08)  # degrees of longitude, latitude: about 8.8 by 8.9 km, 19,000 acres (OpenET allows 50,000 a query)


def openet_samples(area: dict) -> list[dict]:
    """An area too large for one OpenET query, sampled by a regular grid of
    small boxes spread evenly across it (not chosen by hand): `site/sN`."""
    w, s, e, n = area["box"]
    cols, rows = SAMPLE_GRID
    dx, dy = SAMPLE_SIZE
    out = []
    for j in range(rows):
        for i in range(cols):
            cx = w + (i + 0.5) * (e - w) / cols
            cy = s + (j + 0.5) * (n - s) / rows
            out.append({"site": f"{area['site']}/s{len(out) + 1}", "box": [round(cx - dx / 2, 4), round(cy - dy / 2, 4), round(cx + dx / 2, 4), round(cy + dy / 2, 4)]})
    return out


def _latest(store: Store, site: str, p: str, t0: int, t1: int):
    vs = [v for v in store.values(SOURCE, site, p, t0, t1) if v.value is not None]
    return vs[-1] if vs else None


def _sum(store: Store, source: str, site: str, p: str, t0: int, t1: int) -> tuple[float, int]:
    vs = [v.value for v in store.values(source, site, p, t0, t1) if v.value is not None]
    return float(sum(vs)), len(vs)


def water_in(store: Store, site: str, now: int) -> dict:
    """An area's rain over its latest day and the 7 days to it, against the
    normal for those days, and its snowmelt over the 7 days to the latest day
    SNODAS gives, with its snowpack then (mm)."""
    out: dict = {}
    last = store.latest(nws_precip.SOURCE, site, "precipitation_24h")
    if last and last[0] >= now - 4 * 86_400:
        t = last[0]
        day, _ = _sum(store, nws_precip.SOURCE, site, "precipitation_24h", t, t + 1)
        week, nw = _sum(store, nws_precip.SOURCE, site, "precipitation_24h", t - 6 * 86_400, t + 1)
        normal, nn = _sum(store, nws_precip.SOURCE, site, "precipitation_normal_24h", t - 6 * 86_400, t + 1)
        out.update({"rain_day_end": t, "rain_24h_mm": round(day, 1), "rain_7d_mm": round(week, 1), "rain_7d_days": nw})
        if nn == nw and normal > 0:
            out["rain_7d_percent_normal"] = round(100 * week / normal)
    samples = [f"{site}/s{k}" for k in range(1, SAMPLE_GRID[0] * SAMPLE_GRID[1] + 1)]
    lasts = [x[0] for x in (store.latest(openet.SOURCE, s, "evapotranspiration") for s in samples) if x]
    if lasts and min(lasts) >= now - OPENET_AGE_DAYS * 86_400:  # OpenET runs days to weeks behind
        t = min(lasts)  # the last day every sample has
        weeks = [_sum(store, openet.SOURCE, s, "evapotranspiration", t - 6 * 86_400, t + 1) for s in samples]
        weeks = [w for w, n in weeks if n == 7]
        if weeks:
            out.update({"et_day_end": t + 86_400, "et_7d_mm": round(sum(weeks) / len(weeks), 1), "et_samples": len(weeks)})
    last = store.latest(snodas.SOURCE, site, "snow_water_equivalent")
    if last and last[0] >= now - 4 * 86_400:
        t = last[0]
        melt, nm = _sum(store, snodas.SOURCE, site, "snowmelt_24h", t - 6 * 86_400, t + 1)
        swe, _ = _sum(store, snodas.SOURCE, site, "snow_water_equivalent", t, t + 1)
        out.update({"snow_day_end": t, "snowmelt_7d_mm": round(melt, 1), "snowmelt_7d_days": nm, "snow_water_mm": round(swe, 1)})
    return out


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
        row.update(water_in(store, a["site"], now))
        rows.append(row)
    return {"source": "NASA SMAP Level 4 SPL4SMGP v008 (NSIDC DAAC)",  # ref:smap_l4 "corridor_m": float(load(region_dir).get("corridor_m", 10_000.0)),
            "areas": rows}
