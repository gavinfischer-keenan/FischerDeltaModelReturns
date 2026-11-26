"""California Nevada River Forecast Center: hourly deterministic river forecasts
(specification section 6). A regional adapter.

CNRFC publishes, per forecast group, a zipped CSV issued several times a day:
hourly flows from about five days back to about ten days ahead, one column per
forecast point, in thousands of cubic feet per second. Columns typed QINE are
flow at the point, matching the USGS gauges there; others (local inflows, SQIN)
are not used. Values are stored in m3/s, each tagged with the issue it came
from, so a later issue replaces an earlier one hour by hour.

References: ref:cnrfc (references.toml).
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
import zipfile
from pathlib import Path
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


def issue_time(issue: str) -> int:
    """An issue stamp such as "2026112418" as seconds since 1970 (UTC)."""
    return timegm(strptime(issue, "%Y%m%d%H"))


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


def archived_issue(archive: Path, group: str, at: int) -> bytes | None:
    """The group's issue in force at time `at`: the latest archived issue
    issued at or before it (`archive/<group>/<issue>.zip`), or None."""
    d = Path(archive) / group
    best = None
    for f in d.glob("*.zip") if d.exists() else []:
        if issue_time(f.stem) <= at and (best is None or f.stem > best.stem):
            best = f
    return best.read_bytes() if best else None


class CnrfcAdapter:
    def __init__(self, store: Store, get=_get, archive: Path | None = None, keep_days: int = 60) -> None:
        """With `archive`, every issue fetched is kept there as it came
        (`<group>/<issue>.zip`, `keep_days` days), so a hindcast can use the
        forecast that was in force at its time."""
        self.store, self.get = store, get
        self.archive, self.keep_days = archive, keep_days

    def held(self, issue: str, points: Iterable[str]) -> bool:
        """Whether every point already holds this issue (checked at its issue hour)."""
        t = issue_time(issue)
        for p in points:
            v = self.store.values(SOURCE, p, "discharge", t, t + 1)
            if not v or v[0].qualifier != issue:
                return False
        return True

    def fetch(self, group: str, points: Iterable[str]) -> tuple[str, int]:
        """Fetch the latest issue for a group and store its flows for `points`.
        Returns (issue, number of values stored); 0 if that issue is already held."""
        points = list(points)
        html = self.get(INDEX).decode("utf-8", "replace")
        name = latest_file(group, html)
        if not name:
            raise ValueError(f"no CNRFC file listed for group {group}")
        if self.held(name.split("_", 1)[0], points):
            return name.split("_", 1)[0], 0
        data = self.get(f"{BASE}/csv/{name}")
        if self.archive is not None:
            import time as _time

            d = Path(self.archive) / group
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{name.split('_', 1)[0]}.zip").write_bytes(data)
            for old in d.glob("*.zip"):
                if issue_time(old.stem) < _time.time() - self.keep_days * 86_400:
                    old.unlink()
        issue, flows = parse(data, points)
        n = sum(self.store.put_values(SOURCE, p, "discharge", "m3/s", vs) for p, vs in flows.items())
        return issue, n
