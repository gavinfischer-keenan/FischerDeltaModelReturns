"""The US Army Corps of Engineers' daily "Flood Control Requirements" report
for the reservoirs its district oversees (its Water Control Data System, URL
below). A regional adapter. No login.

For each reservoir under the Corps' flood-control rules: its gross pool, its
top of conservation (the most it may hold that day, the rest kept empty for
floods, set by its Water Control Manual for the date and, at some dams, the
basin's wetness), its storage, and how far it is above that top (negative is
flood space still empty). Stored under source `usace-spk`, site the name
given for the reservoir, at the report's data time (24:00 of its date,
Pacific Standard Time): `top_of_conservation` and `above_top_of_conservation`
(m3; the report gives acre-feet).

Folsom's top of conservation varies with upstream storage and forecast
volumes, and the report gives it per forecast time; the latest is taken.
"""

from __future__ import annotations

import re
import time
import urllib.request
from calendar import timegm

from fdmr_store import Store, Value

from .http import USER_AGENT

SOURCE = "usace-spk"
URL = "https://www.spk-wc.usace.army.mil/fcgi-bin/midnight.py?days=0&report=FCR"
AF = 1_233.48184
PST = 8 * 3600


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def _num(s: str) -> float | None:
    s = s.strip().replace(",", "")
    return float(s) if re.fullmatch(r"-?\d+(\.\d+)?", s) else None


def parse(html: str) -> tuple[int | None, dict[str, dict]]:
    """The report's data time and, per reservoir name, {gross, toc, storage, above} (acre-feet)."""
    text = re.sub(r"<[^>]+>", "", html)
    m = re.search(r"Data Ending 2400 hours\s+(\d{1,2} [A-Z]{3} \d{4})", text)
    t = timegm(time.strptime(m.group(1).title(), "%d %b %Y")) + 86_400 + PST if m else None
    out: dict[str, dict] = {}
    last_name = None
    row = re.compile(r"^\s*([A-Za-z][A-Za-z .'/-]*?):\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+\d+\s+(-?[\d,]+)\s*\(")
    dated = re.compile(r"^\s*\d{1,2}[A-Z]{3}\d{4} \d{1,2}\s+([\d,]+)\s+([\d,]+)\s+\d+\s+(-?[\d,]+)\s*\(")
    for line in text.splitlines():
        r = row.match(line)
        if r:
            out[r.group(1).strip()] = {"gross": _num(r.group(2)), "toc": _num(r.group(3)), "storage": _num(r.group(4)), "above": _num(r.group(5))}
            last_name = None
            continue
        head = re.match(r"^\s*([A-Za-z][A-Za-z .'/-]*?):\s+([\d,]+)\s*(Forecasted Volumes.*)?$", line)
        if head:
            last_name = head.group(1).strip()
            out.setdefault(last_name, {"gross": _num(head.group(2))})
            continue
        d = dated.match(line)
        if d and last_name:  # a reservoir whose top varies with forecasts: its latest line wins
            out[last_name].update({"toc": _num(d.group(1)), "storage": _num(d.group(2)), "above": _num(d.group(3))})
    return t, {k: v for k, v in out.items() if v.get("toc") is not None}


class UsaceSpkAdapter:
    def __init__(self, store: Store, get=_get) -> None:
        self.store = store
        self.get = get

    def fetch(self, names: dict[str, str]) -> int:
        """`names`: the report's reservoir name for each site to store under."""
        t, rows = parse(self.get(URL))
        if t is None:
            raise ValueError("Corps report: no data time found")
        n = 0
        for site, name in names.items():
            r = rows.get(name)
            if not r:
                continue
            n += self.store.put_values(SOURCE, site, "top_of_conservation", "m3", [Value(t, r["toc"] * AF, None)])
            if r.get("above") is not None:
                n += self.store.put_values(SOURCE, site, "above_top_of_conservation", "m3", [Value(t, r["above"] * AF, None)])
        return n
