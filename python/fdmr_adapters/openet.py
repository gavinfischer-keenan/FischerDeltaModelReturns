"""OpenET: evapotranspiration measured from Landsat and other satellites by an
ensemble of six models, at field scale, for the western US. A national
adapter (US). Needs an API key (~/.config/fdmr/openet-api-key).

The ensemble's daily evapotranspiration (mm: the water the land gives back to
the air, from soil and crops) averaged over an area's outline, against
gridMET's reference evapotranspiration. Stored under source `openet`, site
the area's name, parameter `evapotranspiration` (mm per day), at each day's
00:00 UTC.
"""

from __future__ import annotations

import json
import time
import urllib.request
from calendar import timegm
from pathlib import Path

from fdmr_store import Store, Value

from .http import USER_AGENT

SOURCE = "openet"
URL = "https://openet-api.org/raster/timeseries/polygon"
KEY_FILE = Path.home() / ".config" / "fdmr" / "openet-api-key"


def _post(body: dict, key: str) -> list:
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": key, "Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def outline(area: dict) -> list[float]:
    """An area's box as the flat lon, lat ring OpenET takes."""
    w, s, e, n = area["box"]
    return [w, s, e, s, e, n, w, n, w, s]


class OpenEtAdapter:
    def __init__(self, store: Store, key: str | None = None, post=_post) -> None:
        self.store = store
        self.key = key if key is not None else KEY_FILE.read_text(encoding="utf-8").strip()
        self.post = post

    def fetch(self, area: dict, start: int, end: int) -> int:
        """An area's daily evapotranspiration from the day after the last
        stored (or `start`) to `end`; returns how many days were stored."""
        have = self.store.latest(SOURCE, area["site"], "evapotranspiration")
        t0 = max(start, have[0] + 86_400) if have else start
        if t0 > end:
            return 0
        day = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t))  # noqa: E731
        rows = self.post({"date_range": [day(t0), day(end)], "interval": "daily", "geometry": outline(area),
                          "model": "Ensemble", "variable": "ET", "reference_et": "gridMET", "reducer": "mean",
                          "units": "mm", "file_format": "JSON"}, self.key)
        vals = [Value(timegm(time.strptime(r["time"][:10], "%Y-%m-%d")), float(r["et"]), None) for r in rows if r.get("et") is not None]
        return self.store.put_values(SOURCE, area["site"], "evapotranspiration", "mm", vals) if vals else 0
