"""The US National Weather Service's daily precipitation analysis: radar
estimates adjusted to rain gauges by the River Forecast Centers (multisensor
"Stage IV"), on a polar-stereographic grid of about 4.8 km (the HRAP grid),
each file the 24 hours to 12:00 UTC on its date, beside the PRISM 1991-2020
normal for that day. A national adapter (US). No login.

Stored under source `nws-precip`, site the area's name, at 12:00 UTC on the
file's date (the end of its 24 hours), as the mean over the area's cells:
`precipitation_24h` and `precipitation_normal_24h` (mm).

References: ref:nws_precip (references.toml).
"""

from __future__ import annotations

import io
import math
import time

import numpy as np

from .areas import members
from . import http

SOURCE = "nws-precip"
URL = "https://water.noaa.gov/resources/downloads/precip/stageIV/{y}/{m:02d}/{d:02d}/nws_precip_1day_{y}{m:02d}{d:02d}_conus.nc"
INCH_MM = 25.4
EARTH_R = 6_371_200.0  # the grid's own sphere (its crs attribute)
LON0, LAT_TS = -105.0, 60.0


def lonlat(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The grid's projected x, y (m) to longitude and latitude (degrees): the
    north polar stereographic projection on a sphere, true at 60 N, inverse
    (Snyder 1987, ref:snyder1987, Map Projections: A Working Manual, USGS Professional Paper
    1395, the stereographic projection, polar aspect on the sphere). The
    grid's south-west cell comes out at 23.120 N, 119.024 W, within 0.003
    degrees (about 300 m, a fifteenth of a cell) of the HRAP grid's published
    first point, 23.117 N, 119.023 W."""
    k0 = (1 + math.sin(math.radians(LAT_TS))) / 2
    rho = np.hypot(x, y)
    c = 2 * np.arctan(rho / (2 * EARTH_R * k0))
    lat = np.degrees(np.pi / 2 - c)
    lon = LON0 + np.degrees(np.arctan2(x, -y))
    return lon, lat


def _get(url: str) -> bytes:
    return http.request(url, timeout=180)


def read(data: bytes) -> dict[str, np.ndarray]:
    import h5py

    out = {}
    with h5py.File(io.BytesIO(data), "r") as f:
        for k in ("observation", "normal", "x", "y"):
            a = np.array(f[k], dtype=float)
            a[a < -1e30] = np.nan
            out[k] = a
        out["time"] = float(np.array(f["time"]))
    return out


class NwsPrecipAdapter:
    def __init__(self, store, get=_get) -> None:
        self.store = store
        self.get = get

    def fetch(self, areas: list[dict], start: int, end: int) -> int:
        """Each area's daily precipitation for the days in [start, end] not yet
        stored; returns how many values were stored."""
        from fdmr_store import Value

        n = 0
        day = start // 86_400 * 86_400
        cache: dict | None = None
        while day <= end:
            t = day + 12 * 3600
            have = self.store.latest(SOURCE, areas[0]["site"], "precipitation_24h")
            if have and have[0] >= t:
                day += 86_400
                continue
            y, m, d = time.gmtime(day)[:3]
            try:
                g = read(self.get(URL.format(y=y, m=m, d=d)))
            except Exception as e:  # a day not yet posted (or a failure): tried again next time
                print(f"nws-precip {y}-{m:02d}-{d:02d} not read: {e}", flush=True)
                break
            if cache is None:
                xx, yy = np.meshgrid(g["x"], g["y"])
                lon, lat = lonlat(xx, yy)
                cache = {a["site"]: members(lat, lon, a) for a in areas}
            for site, mask in cache.items():
                for key, p in (("observation", "precipitation_24h"), ("normal", "precipitation_normal_24h")):
                    v = g[key][mask]
                    v = v[np.isfinite(v)]
                    if v.size:
                        n += self.store.put_values(SOURCE, site, p, "mm", [Value(t, float(v.mean()) * INCH_MM, None)])
            day += 86_400
        return n
