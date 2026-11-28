"""Land surface temperature along the rivers against the land at the airports
whose weather drives their heat (issue #11; region.toml
[river_temperature.land]).

The river heat model takes each element's air temperature, humidity, wind
and cloud from the nearest weather zone's airport ([[river_temperature.weather]]).
Land between the airports can be warmer or cooler than at them. From
orbit (fdmr_adapters.ecostress, `fetch_land`) the land's own surface
temperature is measured at each pass over two kinds of place:

- `band:<reach>:<zone>`: the land within `band_m` of a river's centreline,
  where it takes that zone's weather;
- `airport:<zone>`: the land within `airport_m` of the zone's airport.

At each pass that saw both, their difference (band less airport) is how much
warmer the land along that stretch of river was than the land at its airport.
Kept passes over the last `days` are summarised by day and night apart (local
solar hour 8 to 17 counts as day, 20 to 5 as night, the rest neither): by
day sun-warmed land runs far above the air, by night below it. A persistent
contrast is a sign that the airport's air misrepresents the air over that
stretch, a lead for the heat model's tuning (#9), not a correction.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from fdmr_store import Store

from .region import load_region

SOURCE, PARAMETER = "ecostress", "land_surface_temperature"
MATCH_S = 600  # a band and its airport count as the same pass within this
POINT_SPACING_M = 200.0  # centreline points kept for the bands, about this far apart


# Local solar hours counted as day and as night when comparing the land's
# temperature with the airport's (FDMR's choice: well clear of the hours
# around sunrise and sunset, when the land heats and cools fastest).
DAY_HOURS = (8, 17)  # from 08:00 to 17:00 local solar time
NIGHT_HOURS = (20, 5)  # from 20:00 to 05:00


def load(region_dir: Path) -> tuple[dict, list[dict]] | None:
    rt = load_region(region_dir).river_temperature
    if not rt.get("land") or not rt.get("weather"):
        return None
    return rt["land"], rt["weather"]


def places(region_dir: Path) -> list[dict]:
    """The bands along every river (split by the weather zone each part takes)
    and the land round every zone's airport: {site, points, radius_m}."""
    got = load(region_dir)
    if not got:
        return []
    land, zones = got
    reaches = [r["id"] for r in load_region(region_dir).reaches if r.get("kind") == "river-routing"]
    out = []
    for reach in reaches:
        path = region_dir / f"{reach}.json"
        if not path.exists():
            continue
        c = json.loads(path.read_text(encoding="utf-8"))
        coords, ch = c["coords"], c["chainage_m"]
        keep, last = [], -math.inf
        for (lo, la), x in zip(coords, ch):
            if x - last >= POINT_SPACING_M:
                keep.append((lo, la))
                last = x
        # Each point takes the nearest zone, as the heat model's elements do.
        by_zone: dict[int, list] = {}
        for lo, la in keep:
            z = min(range(len(zones)), key=lambda z: math.hypot(zones[z]["lon"] - lo, zones[z]["lat"] - la))
            by_zone.setdefault(z, []).append((lo, la))
        for z, pts in sorted(by_zone.items()):
            out.append({"site": f"band:{reach}:{zones[z]['name']}", "points": pts, "radius_m": float(land.get("band_m", 1000.0))})
    for zone in zones:
        out.append({"site": f"airport:{zone['name']}", "points": [(zone["lon"], zone["lat"])], "radius_m": float(land.get("airport_m", 2000.0))})
    return out


def box(pl: list[dict], margin: float = 0.05) -> tuple[float, float, float, float]:
    """West, south, east, north round every place."""
    pts = np.array([p for q in pl for p in q["points"]])
    return (float(pts[:, 0].min() - margin), float(pts[:, 1].min() - margin), float(pts[:, 0].max() + margin), float(pts[:, 1].max() + margin))


def part_of_day(t: int, lon: float) -> str | None:
    h = ((t % 86_400) / 3600 + lon / 15) % 24
    return "day" if DAY_HOURS[0] <= h < DAY_HOURS[1] else "night" if h >= NIGHT_HOURS[0] or h < NIGHT_HOURS[1] else None


def contrast(region_dir: Path, store: Store, now: int) -> dict | None:
    """Each river stretch's land against its airport's, over the last `days`."""
    got = load(region_dir)
    if not got:
        return None
    land, zones = got
    days = int(land.get("days", 14))
    t0 = now - days * 86_400
    lon_of = {z["name"]: z["lon"] for z in zones}
    airports = {z["name"]: [(v.t, v.value) for v in store.values(SOURCE, f"airport:{z['name']}", PARAMETER, t0, now + 1) if v.value is not None]
                for z in zones}
    rows = []
    for p in places(region_dir):
        if not p["site"].startswith("band:"):
            continue
        _, reach, zone = p["site"].split(":", 2)
        air = airports.get(zone, [])
        parts: dict[str, list[tuple[float, float]]] = {}
        for v in store.values(SOURCE, p["site"], PARAMETER, t0, now + 1):
            if v.value is None:
                continue
            a = min(air, key=lambda x: abs(x[0] - v.t), default=None)
            when = part_of_day(v.t, lon_of[zone])
            if a and abs(a[0] - v.t) <= MATCH_S and when:
                parts.setdefault(when, []).append((v.value, a[1]))
        row = {"reach": reach, "zone": zone}
        for when, pairs in parts.items():
            b = np.array(pairs)
            row[when] = {"passes": len(b), "band_mean": round(float(b[:, 0].mean()), 2), "airport_mean": round(float(b[:, 1].mean()), 2),
                         "contrast": round(float((b[:, 0] - b[:, 1]).mean()), 2)}
        if len(row) > 2:
            rows.append(row)
    return {"source": "NASA ECOSTRESS ECO_L2T_LSTE v003 (LP DAAC), land surface temperature",  # ref:ecostress_lst "days": days,
            "band_m": float(land.get("band_m", 1000.0)), "airport_m": float(land.get("airport_m", 2000.0)), "stretches": rows}
