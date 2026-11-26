"""NOAA's National Data Buoy Center: the real-time ocean files of its stations.
A national adapter (United States).

`https://www.ndbc.noaa.gov/data/realtime2/<STATION>.ocean` holds a station's
last 45 days of water-quality readings, newest first, space-separated, two
header lines (names, then units), `MM` for missing. Some stations carry
another programme's sondes, such as the National Estuarine Research
Reserves'; their values are provisional.

Stored under source `ndbc`: water_temperature (degC, column OTMP) and
salinity (psu, column SAL).
"""

from __future__ import annotations

import urllib.request
from calendar import timegm
from collections.abc import Iterable

from fdmr_store import Store, Value

from .http import USER_AGENT

SOURCE = "ndbc"
URL = "https://www.ndbc.noaa.gov/data/realtime2/{station}.ocean"
COLUMNS = {"water_temperature": ("OTMP", "degC", (-2.0, 40.0)), "salinity": ("SAL", "psu", (0.0, 42.0))}


def parse(lines: Iterable[str]) -> dict[str, list[Value]]:
    """Each parameter's readings, oldest first."""
    rows = [ln.split() for ln in lines if ln.strip()]
    head = rows[0]
    head[0] = head[0].lstrip("#")
    col = {name: i for i, name in enumerate(head)}
    out: dict[str, list[Value]] = {p: [] for p in COLUMNS}
    for r in rows[1:]:
        if r[0].startswith("#"):
            continue
        t = timegm((int(r[0]), int(r[1]), int(r[2]), int(r[3]), int(r[4]), 0))
        for p, (name, _, (lo, hi)) in COLUMNS.items():
            if name not in col or col[name] >= len(r) or r[col[name]] == "MM":
                continue
            v = float(r[col[name]])
            if lo <= v <= hi:
                out[p].append(Value(t, v, None))
    return {p: sorted(vs, key=lambda v: v.t) for p, vs in out.items()}


def _get_lines(url: str) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace").splitlines()


class NdbcAdapter:
    def __init__(self, store: Store, get_lines=_get_lines) -> None:
        self.store = store
        self.get_lines = get_lines

    def fetch(self, station: str) -> int:
        """Store a station's last 45 days; returns how many readings."""
        data = parse(self.get_lines(URL.format(station=station)))
        return sum(self.store.put_values(SOURCE, station, p, COLUMNS[p][1], vs) for p, vs in data.items() if vs)
