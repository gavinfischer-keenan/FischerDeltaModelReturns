"""ERDDAP tabledap servers, such as CeNCOOS's (the Central and Northern
California Ocean Observing System, part of US IOOS). A national adapter.

A dataset's readings come as CSV: a header row of variable names, a row of
units, then one row per time. Stored under source `erddap:<server host>`,
site the datasetID, in this app's parameter names: water_temperature (degC,
from sea_water_temperature) and salinity (psu, from
sea_water_practical_salinity).

A griddap dataset can also be averaged over a box each time step
(`fetch_grid_mean`), such as satellite sea level anomaly offshore.
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from calendar import timegm
from time import strptime
from urllib.parse import quote

from fdmr_store import Store, Value

from .http import USER_AGENT

VARIABLES = {
    "water_temperature": ("sea_water_temperature", "degC", (-2.0, 40.0)),
    "salinity": ("sea_water_practical_salinity", "psu", (0.0, 42.0)),
}


def source(server: str) -> str:
    """The store's source name for a server, by its host."""
    return "erddap:" + server.split("//", 1)[-1].split("/", 1)[0]


def parse(text: str) -> dict[str, list[Value]]:
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    out: dict[str, list[Value]] = {p: [] for p in VARIABLES}
    for r in rows[2:]:
        rec = dict(zip(head, r))
        try:
            t = timegm(strptime(rec["time"][:19], "%Y-%m-%dT%H:%M:%S"))
        except (KeyError, ValueError):
            continue
        for p, (name, _, (lo, hi)) in VARIABLES.items():
            v = rec.get(name, "")
            if v in ("", "NaN"):
                continue
            x = float(v)
            if lo <= x <= hi:
                out[p].append(Value(t, x, None))
    return out


def _get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


class ErddapAdapter:
    def __init__(self, store: Store, server: str, get_text=_get_text) -> None:
        self.store, self.server, self.get_text = store, server.rstrip("/"), get_text

    def fetch(self, dataset: str, since_iso: str, variables: list[str]) -> int:
        """Store a dataset's readings since `since_iso` (UTC) for the variables
        it carries (by this app's names); returns how many."""
        names = ",".join(["time", *[VARIABLES[p][0] for p in variables]])
        url = f"{self.server}/tabledap/{dataset}.csv?{names}&time%3E={quote(since_iso)}"
        try:
            data = parse(self.get_text(url))
        except urllib.error.HTTPError as e:
            if e.code == 404:  # ERDDAP answers 404 when there is no reading in the window
                return 0
            raise
        src = source(self.server)
        return sum(self.store.put_values(src, dataset, p, VARIABLES[p][1], vs) for p, vs in data.items() if vs)

    def fetch_grid_mean(self, dataset: str, variable: str, box: tuple[float, float, float, float], since_iso: str,
                        site: str, parameter: str, unit: str, until_iso: str | None = None) -> int:
        """Store a griddap variable's mean over a box (west, south, east, north)
        at each time from `since_iso` to `until_iso` (or the latest), under
        `site` and `parameter`."""
        w, s, e, n = box
        sel = f"[({since_iso}):1:({until_iso or 'last'})][({s}):1:({n})][({w}):1:({e})]"
        url = f"{self.server}/griddap/{dataset}.csv?{variable}{quote(sel, safe='():,')}"
        try:
            text = self.get_text(url)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return 0
            raise
        rows = list(csv.reader(io.StringIO(text)))
        col = rows[0].index(variable)
        sums: dict[int, list[float]] = {}
        for r in rows[2:]:
            if len(r) > col and r[col] not in ("", "NaN"):
                t = timegm(strptime(r[0][:19], "%Y-%m-%dT%H:%M:%S"))
                sums.setdefault(t, []).append(float(r[col]))
        vals = [Value(t, sum(v) / len(v), None) for t, v in sorted(sums.items())]
        return self.store.put_values(source(self.server), site, parameter, unit, vals) if vals else 0

