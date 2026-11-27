"""NOAA's National Data Buoy Center: the real-time ocean files of its stations.
A national adapter (United States).

`https://www.ndbc.noaa.gov/data/realtime2/<STATION>.ocean` holds a station's
last 45 days of water-quality readings, newest first, space-separated, two
header lines (names, then units), `MM` for missing. Some stations carry
another programme's sondes, such as the National Estuarine Research
Reserves'; their values are provisional.

Stored under source `ndbc`: water_temperature (degC, column OTMP) and
salinity (psu, column SAL). A buoy without an ocean file gives its
sea-surface temperature in the standard file (column WTMP), fetched with the
weather (`fetch_met`).

References: ref:ndbc (references.toml).
"""

from __future__ import annotations

from calendar import timegm
from collections.abc import Iterable

from fdmr_store import Store, Value

from . import http

SOURCE = "ndbc"
URL = "https://www.ndbc.noaa.gov/data/realtime2/{station}.ocean"
COLUMNS = {"water_temperature": ("OTMP", "degC", (-2.0, 40.0)), "salinity": ("SAL", "psu", (0.0, 42.0))}
# The standard meteorological file (`.txt`): weather, in the weather adapter's parameter names.
MET_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station}.txt"
MET_COLUMNS = {
    "air_temperature": ("ATMP", "degC", (-40.0, 55.0)), "dew_point": ("DEWP", "degC", (-50.0, 40.0)),
    "wind_speed": ("WSPD", "m/s", (0.0, 60.0)), "air_pressure": ("PRES", "hPa", (850.0, 1090.0)),
    "wind_direction": ("WDIR", "deg", (0.0, 360.0)), "wind_gust": ("GST", "m/s", (0.0, 80.0)),
    "water_temperature": ("WTMP", "degC", (-2.0, 40.0)),  # a buoy's sea-surface temperature
}


def parse(lines: Iterable[str], columns: dict = COLUMNS) -> dict[str, list[Value]]:
    """Each parameter's readings, oldest first."""
    rows = [ln.split() for ln in lines if ln.strip()]
    head = rows[0]
    head[0] = head[0].lstrip("#")
    col = {name: i for i, name in enumerate(head)}
    out: dict[str, list[Value]] = {p: [] for p in columns}
    for r in rows[1:]:
        if r[0].startswith("#"):
            continue
        t = timegm((int(r[0]), int(r[1]), int(r[2]), int(r[3]), int(r[4]), 0))
        for p, (name, _, (lo, hi)) in columns.items():
            if name not in col or col[name] >= len(r) or r[col[name]] == "MM":
                continue
            v = float(r[col[name]])
            if lo <= v <= hi:
                out[p].append(Value(t, v, None))
    return {p: sorted(vs, key=lambda v: v.t) for p, vs in out.items()}


def _get_lines(url: str) -> list[str]:
    return http.get_text(url, timeout=60).splitlines()


class NdbcAdapter:
    def __init__(self, store: Store, get_lines=_get_lines) -> None:
        self.store = store
        self.get_lines = get_lines

    def fetch(self, station: str) -> int:
        """Store a station's last 45 days; returns how many readings."""
        data = parse(self.get_lines(URL.format(station=station)))
        return sum(self.store.put_values(SOURCE, station, p, COLUMNS[p][1], vs) for p, vs in data.items() if vs)

    def fetch_met(self, station: str) -> int:
        """Store a station's last 45 days of weather; returns how many readings."""
        data = parse(self.get_lines(MET_URL.format(station=station)), MET_COLUMNS)
        return sum(self.store.put_values(SOURCE, station, p, MET_COLUMNS[p][1], vs) for p, vs in data.items() if vs)
