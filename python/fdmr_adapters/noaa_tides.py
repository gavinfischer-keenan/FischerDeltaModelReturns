"""NOAA tide predictions and what its tide stations measure (CO-OPS data
API). A national adapter.

Astronomical tide predictions for a station, every 15 minutes, in metres
above the station's MLLW datum, in UTC. They are the tide alone: no river
flow, wind or storm surge. Whoever uses them fits their offset to a gauge
(specification 6.1: a tidal model's seaward boundary).

Measured, every 6 minutes (`fetch_observed`, products in OBSERVED): water
temperature (degC), water level (m above the station's MLLW datum, the same
datum as the predictions, so measured less predicted is the weather's and the
river's part of the water level: the tide residual, or storm surge), air
pressure (hPa) and wind (speed and gust m/s, direction degrees true, from).

References: ref:noaa_coops (references.toml).
"""

from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone
from time import strptime
from urllib.parse import urlencode

from fdmr_store import Store, Value

from .http import HttpJsonClient, JsonClient

BASE = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
SOURCE = "noaa-tides"
PARAMETER = "tide_prediction"
WATER_TEMPERATURE = "water_temperature"
CHUNK_DAYS = 30  # the API serves 15-minute predictions, and 6-minute readings, a month at a time
# A product: each reading's field, our parameter, its unit and the plausible range.
OBSERVED = {
    "water_temperature": [("v", "water_temperature", "degC", (-2.0, 40.0))],
    "water_level": [("v", "water_level", "m", (-5.0, 10.0))],
    "air_pressure": [("v", "air_pressure", "hPa", (850.0, 1090.0))],
    "wind": [("s", "wind_speed", "m/s", (0.0, 60.0)), ("d", "wind_direction", "deg", (0.0, 360.0)), ("g", "wind_gust", "m/s", (0.0, 80.0))],
}


class NoaaTidesAdapter:
    def __init__(self, store: Store, client: JsonClient | None = None) -> None:
        self.store = store
        self.client = client or HttpJsonClient()

    def fetch_predictions(self, station: str, start: int, end: int) -> int:
        """Store predictions for [start, end] (s since 1970 UTC); returns how many."""
        day = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%Y%m%d")  # noqa: E731
        vals: dict[int, Value] = {}
        t = start
        while t <= end:
            t1 = min(end, t + (CHUNK_DAYS - 1) * 86_400)
            q = urlencode({
                "product": "predictions", "station": station, "begin_date": day(t), "end_date": day(t1),
                "datum": "MLLW", "time_zone": "gmt", "units": "metric", "interval": "15", "format": "json",
                "application": "fdmr",
            })
            page = self.client.get_json(f"{BASE}?{q}")
            if "error" in page:
                raise ValueError(f"NOAA tides {station}: {page['error'].get('message', page['error'])}")
            for p in page.get("predictions", []):
                ts = timegm(strptime(p["t"], "%Y-%m-%d %H:%M"))
                vals[ts] = Value(ts, float(p["v"]), "MLLW")
            t = t1 + 86_400
        return self.store.put_values(SOURCE, station, PARAMETER, "m", [vals[k] for k in sorted(vals)])

    def fetch_observed(self, station: str, product: str, start: int, end: int) -> int:
        """Store a station's measured `product` (OBSERVED) for [start, end];
        returns how many readings."""
        day = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%Y%m%d")  # noqa: E731
        fields = OBSERVED[product]
        vals: dict[str, dict[int, Value]] = {p: {} for _, p, _, _ in fields}
        t = start
        while t <= end:
            t1 = min(end, t + (CHUNK_DAYS - 1) * 86_400)
            q = {"product": product, "station": station, "begin_date": day(t), "end_date": day(t1),
                 "time_zone": "gmt", "units": "metric", "format": "json", "application": "fdmr"}
            if product == "water_level":
                q["datum"] = "MLLW"
            page = self.client.get_json(f"{BASE}?{urlencode(q)}")
            if "error" in page:
                raise ValueError(f"NOAA {station} {product}: {page['error'].get('message', page['error'])}")
            for row in page.get("data", []):
                ts = timegm(strptime(row["t"], "%Y-%m-%d %H:%M"))
                for f, p, _, (lo, hi) in fields:
                    if row.get(f) in (None, ""):
                        continue
                    v = float(row[f])
                    if lo <= v <= hi:
                        vals[p][ts] = Value(ts, v, None)
            t = t1 + 86_400
        return sum(self.store.put_values(SOURCE, station, p, unit, [vals[p][k] for k in sorted(vals[p])])
                   for _, p, unit, _ in fields if vals[p])

    def fetch_water_temperature(self, station: str, start: int, end: int) -> int:
        """Store a station's measured water temperature (degC, every 6 minutes)
        for [start, end]; returns how many readings."""
        return self.fetch_observed(station, "water_temperature", start, end)
