"""High-frequency radar surface currents (the US IOOS national network), from
an ERDDAP griddap dataset such as NOAA CoastWatch's `ucsdHfrW500` (US West
Coast, 500 m, hourly). A national adapter.

Returns gridded eastward and northward surface current, m/s, for a box and
the last N hours: the radar measures the top metre or so of the water.
Read from ERDDAP's NetCDF-3 file with scipy.
"""

from __future__ import annotations

import io
import urllib.request
from dataclasses import dataclass

import numpy as np

from .http import USER_AGENT


@dataclass(frozen=True)
class Currents:
    t: np.ndarray  # [time], s since 1970 UTC
    lat: np.ndarray  # [lat]
    lon: np.ndarray  # [lon]
    u: np.ndarray  # [time, lat, lon], m/s, NaN where none
    v: np.ndarray


def query(server: str, dataset: str, box: tuple[float, float, float, float], hours: int) -> str:
    """The griddap URL for the last `hours` hours over box (south, north, west, east)."""
    s, n, w, e = box
    sel = f"[last-{hours - 1}:1:last][({s}):1:({n})][({w}):1:({e})]"
    return f"{server.rstrip('/')}/griddap/{dataset}.nc?water_u{sel},water_v{sel}"


def parse_nc(data: bytes) -> Currents:
    from scipy.io import netcdf_file

    with netcdf_file(io.BytesIO(data), mmap=False) as f:
        def var(name):
            x = f.variables[name]
            a = np.array(x.data, dtype=float)
            fill = getattr(x, "_FillValue", None)
            if fill is not None:
                a[a == float(fill)] = np.nan
            return a

        u, v = var("water_u"), var("water_v")
        u[np.abs(u) > 10] = np.nan  # fill values that carry no attribute
        v[np.abs(v) > 10] = np.nan
        return Currents(var("time"), var("latitude"), var("longitude"), u, v)


def fetch(server: str, dataset: str, box: tuple[float, float, float, float], hours: int) -> Currents:
    req = urllib.request.Request(query(server, dataset, box, hours), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as r:
        return parse_nc(r.read())
