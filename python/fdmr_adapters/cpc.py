"""NOAA's Climate Prediction Center: the El Nino-Southern Oscillation indices.
A global index, from a national agency. No login.

- Weekly sea-surface temperatures and their anomalies against the 1991-2020
  base in the Nino 1+2, 3, 3.4 and 4 regions of the tropical Pacific
  (`wksst9120.for`), each week stored at its centre day: source `cpc`, sites
  `nino12`, `nino3`, `nino34`, `nino4`, parameter `sst_anomaly` (degC).
- The Oceanic Nino Index (`oni.ascii.txt`): Nino 3.4's anomaly over three
  overlapping months, stored at the middle month's 15th: site `oni`,
  parameter `sst_anomaly` (degC). CPC calls El Nino conditions when it is at
  or above +0.5 for five overlapping seasons, La Nina at or below -0.5.
"""

from __future__ import annotations

import urllib.request
from calendar import timegm
from time import strptime

from fdmr_store import Store, Value

from .http import USER_AGENT

SOURCE = "cpc"
WEEKLY = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"
ONI = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
REGIONS = ("nino12", "nino3", "nino34", "nino4")
SEASON_MIDDLE = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6, "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("ascii", "replace")


def parse_weekly(text: str) -> dict[str, list[Value]]:
    """Each region's weekly anomaly, by the week's centre day."""
    out: dict[str, list[Value]] = {r: [] for r in REGIONS}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 9 or not parts[0][:2].isdigit():
            continue
        t = timegm(strptime(parts[0].title(), "%d%b%Y"))
        for k, region in enumerate(REGIONS):
            out[region].append(Value(t, float(parts[2 + 2 * k]), None))
    return out


def parse_oni(text: str) -> list[Value]:
    """Each season's anomaly, at its middle month's 15th (DJF is January of its year)."""
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 4 and parts[0] in SEASON_MIDDLE:
            month = SEASON_MIDDLE[parts[0]]
            out.append(Value(timegm((int(parts[1]), month, 15, 0, 0, 0)), float(parts[3]), None))
    return out


class CpcAdapter:
    def __init__(self, store: Store, get=_get) -> None:
        self.store = store
        self.get = get

    def fetch(self) -> int:
        n = 0
        for region, vs in parse_weekly(self.get(WEEKLY)).items():
            n += self.store.put_values(SOURCE, region, "sst_anomaly", "degC", vs)
        n += self.store.put_values(SOURCE, "oni", "sst_anomaly", "degC", parse_oni(self.get(ONI)))
        return n
