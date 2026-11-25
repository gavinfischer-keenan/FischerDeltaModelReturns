"""California Nevada River Forecast Center: hourly deterministic river forecasts
(specification section 6). A regional adapter.

CNRFC publishes, per forecast group, a zipped CSV issued several times a day:
hourly flows from about five days back to about ten days ahead, one column per
forecast point, in thousands of cubic feet per second. Columns typed QINE are
flow at the point, matching the USGS gauges there; others (local inflows, SQIN)
are not used. Values are stored in m3/s, each tagged with the issue it came
from, so a later issue replaces an earlier one hour by hour.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
import zipfile
from calendar import timegm
from collections.abc import Iterable
from time import strptime

from fdmr_store import Store, Value

from .http import USER_AGENT

BASE = "https://www.cnrfc.noaa.gov"
INDEX = f"{BASE}/deterministicHourlyProductCSV.php"
SOURCE = "cnrfc"
KCFS = 1000 * 0.3048**3  # thousand cubic feet per second, in m3/s


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def latest_file(group: str, index_html: str) -> str | None:
    """The newest file name for a forecast group, from the index page."""
    names = sorted(set(re.findall(rf"(\d{{10}})_{re.escape(group)}_csv_export\.zip", index_html)))
    return f"{names[-1]}_{group}_csv_export.zip" if names else None


def parse(zip_bytes: bytes, points: Iterable[str]) -> tuple[str, dict[str, list[Value]]]:
    """Flows for the wanted forecast points, from one zipped CSV.
    Returns (issue, {point: values in m3/s})."""
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    name = z.namelist()[0]
    issue = name.split("_", 1)[0]
    rows = list(csv.reader(io.StringIO(z.read(name).decode("utf-8", "replace"))))
    head, kind = rows[0], rows[1]
    want = set(points)
    cols = {h: i for i, (h, k) in enumerate(zip(head, kind)) if h in want and k == "QINE"}
    out: dict[str, list[Value]] = {p: [] for p in cols}
    for r in rows[2:]:
        if not r or not r[0].strip():
            continue
        t = timegm(strptime(r[0].strip(), "%Y-%m-%d %H:%M:%S"))
        for p, i in cols.items():
            if i < len(r) and r[i].strip():
                out[p].append(Value(t, float(r[i]) * KCFS, issue))
    return issue, out


class CnrfcAdapter:
    def __init__(self, store: Store, get=_get) -> None:
        self.store, self.get = store, get

    def fetch(self, group: str, points: Iterable[str]) -> tuple[str, int]:
        """Fetch the latest issue for a group and store its flows for `points`.
        Returns (issue, number of values stored)."""
        html = self.get(INDEX).decode("utf-8", "replace")
        name = latest_file(group, html)
        if not name:
            raise ValueError(f"no CNRFC file listed for group {group}")
        issue, flows = parse(self.get(f"{BASE}/csv/{name}"), points)
        n = sum(self.store.put_values(SOURCE, p, "discharge", "m3/s", vs) for p, vs in flows.items())
        return issue, n
