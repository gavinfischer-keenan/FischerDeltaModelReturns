"""NOAA tide predictions and station water temperature (CO-OPS data API). A national adapter.

Astronomical tide predictions for a station, every 15 minutes, in metres
above the station's MLLW datum, in UTC. They are the tide alone: no river
flow, wind or storm surge. Whoever uses them fits their offset to a gauge
(specification 6.1: a tidal model's seaward boundary).
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
CHUNK_DAYS = 30  # the API serves 15-minute predictions a month at a time


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

    def fetch_water_temperature(self, station: str, start: int, end: int) -> int:
        """Store a station's measured water temperature (degC, every 6 minutes)
        for [start, end]; returns how many readings."""
        day = lambda s: datetime.fromtimestamp(s, timezone.utc).strftime("%Y%m%d")  # noqa: E731
        vals: dict[int, Value] = {}
        t = start
        while t <= end:
            t1 = min(end, t + (CHUNK_DAYS - 1) * 86_400)
            q = urlencode({
                "product": WATER_TEMPERATURE, "station": station, "begin_date": day(t), "end_date": day(t1),
                "time_zone": "gmt", "units": "metric", "format": "json", "application": "fdmr",
            })
            page = self.client.get_json(f"{BASE}?{q}")
            if "error" in page:
                raise ValueError(f"NOAA {station} water temperature: {page['error'].get('message', page['error'])}")
            for p in page.get("data", []):
                if p.get("v") in (None, ""):
                    continue
                ts = timegm(strptime(p["t"], "%Y-%m-%d %H:%M"))
                v = float(p["v"])
                if -2.0 <= v <= 40.0:
                    vals[ts] = Value(ts, v, None)
            t = t1 + 86_400
        return self.store.put_values(SOURCE, station, WATER_TEMPERATURE, "degC", [vals[k] for k in sorted(vals)])
