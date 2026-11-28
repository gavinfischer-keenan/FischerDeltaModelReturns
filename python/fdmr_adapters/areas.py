"""Which cells of a gridded product fall in an area: shared by the adapters that
average a grid over areas (fdmr_adapters.smap, nws_precip, snodas).

An area is a `box` (west, south, east, north, degrees) or `points` (lon, lat)
with a `radius_m`: the cells whose centres lie within that distance of any
point. Distances are on a local flat approximation (good to well under 1% over
the tens of kilometres an area spans).
"""

from __future__ import annotations

import math

import numpy as np

import fdmr_geo



def members(lat: np.ndarray, lon: np.ndarray, area: dict) -> np.ndarray:
    """A boolean mask the shape of `lat` and `lon` (cell centres, degrees)."""
    if "box" in area:
        w, s, e, n = area["box"]
        return (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
    m = np.zeros(lat.shape, bool)
    for plo, pla in area["points"]:
        # On the plane about the point (fdmr_geo): the equator's degree lengths
        # stood in here before, 0.4% short north-south (#69).
        dx = (lon - plo) * fdmr_geo.M_PER_DEG * math.cos(math.radians(pla))
        dy = (lat - pla) * fdmr_geo.M_PER_DEG
        m |= dx * dx + dy * dy <= area["radius_m"] ** 2
    return m


def extent(areas: list[dict], margin: float = 0.15) -> tuple[float, float, float, float]:
    """West, south, east, north round every area, with a margin (degrees)."""
    pts = [p for a in areas for p in (a.get("points") or [(a["box"][0], a["box"][1]), (a["box"][2], a["box"][3])])]
    return (min(p[0] for p in pts) - margin, min(p[1] for p in pts) - margin, max(p[0] for p in pts) + margin, max(p[1] for p in pts) + margin)
