"""Reclamation's Central Valley Operations Office: the daily Delta Outflow
Computation (www.usbr.gov/mp/cvo), the water balance DWR and Reclamation keep
for the Delta under the State Water Board's D-1641 (Figure 3 there gives its
terms):

    outflow index (NDOI) = Delta inflow - net Delta consumptive use (NDCU) - exports

NDCU is the Delta's gross channel depletion less the runoff from rain on the
Delta, estimated from stations within it; it turns negative when it rains.
Nothing else publishes it daily, so it is read from the computation's PDF: one
per month, `doutMMYY.pdf`, and the month in progress as `doutdly.pdf`. Values
are preliminary and may change; each fetch replaces the month's values.

Each day's values are stored at the day's start, Pacific Standard Time, as
CDEC's daily values are, in m3/s.
"""

from __future__ import annotations

import io
import re
import urllib.request
from calendar import timegm
from datetime import datetime, timezone

from fdmr_store import Store, Value

from .http import USER_AGENT

BASE = "https://www.usbr.gov/mp/cvo/vungvari/"
SOURCE = "usbr-cvo"
SITE = "DOUT"  # the Delta Outflow Computation
PST_TO_UTC = 8 * 3600
CFS = 0.3048**3

# The computation's columns, in order, for a complete day's row (22 numbers),
# named by role: the northern river's inflow (the previous day's), a
# wastewater plant, the bypass and small streams, the east-side streams, the
# southern river (that day, and its 7-day and monthly means), and so on.
COLUMNS = [
    "north_river", "wastewater", "bypass_misc", "east_side", "south_river", "south_river_7day", "south_river_monthly", "delta_inflow",
    "ndcu", "clifton_court", "tracy", "contra_costa", "byron_bethany", "north_bay", "delta_exports", "exports_3day",
    "ndoi", "ndoi_7day", "ndoi_monthly", "export_inflow_daily", "export_inflow_3day", "export_inflow_14day",
]
STORED = ["delta_inflow", "ndcu", "delta_exports", "ndoi"]  # m3/s
ROW = re.compile(r"^\s*(\d{2})/(\d{2})/(\d{2})\s+(.*)$")


def month_url(year: int, month: int, current: bool) -> str:
    return BASE + ("doutdly.pdf" if current else f"dout{month:02d}{year % 100:02d}.pdf")


def parse(text: str) -> dict[int, dict[str, float]]:
    """Each complete day's row of the computation's text: {day start (UTC s): {column: cfs}}.
    A day still being filled in (fewer than all the columns) is left out."""
    if "NDCU" not in text or "NDOI" not in text:
        raise ValueError("not a Delta Outflow Computation: no NDCU or NDOI column")
    out = {}
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        nums = re.findall(r"\(?-?[\d,]+\)?%?", m.group(4))
        if len(nums) != len(COLUMNS):
            continue
        vals = []
        for s in nums:
            neg = s.startswith("(") or s.startswith("-")
            vals.append((-1 if neg else 1) * float(s.strip("()%-").replace(",", "")))
        mm, dd, yy = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
        out[timegm((yy, mm, dd, 0, 0, 0)) + PST_TO_UTC] = dict(zip(COLUMNS, vals))
    return out


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


class UsbrCvoAdapter:
    def __init__(self, store: Store, get=None) -> None:
        self.store = store
        self.get = get or self._get

    @staticmethod
    def _get(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    def fetch_month(self, year: int, month: int, now: int) -> int:
        """Store one month's daily values; returns how many days."""
        d = datetime.fromtimestamp(now - PST_TO_UTC, timezone.utc)
        rows = parse(pdf_text(self.get(month_url(year, month, (d.year, d.month) == (year, month)))))
        rows = {t: r for t, r in rows.items() if datetime.fromtimestamp(t - PST_TO_UTC, timezone.utc).month == month}
        for p in STORED:
            self.store.put_values(SOURCE, SITE, p, "m3/s", [Value(t, rows[t][p] * CFS, "preliminary") for t in sorted(rows)])
        return len(rows)
