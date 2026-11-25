"""USGS Water Data APIs (api.waterdata.usgs.gov, OGC API). A national adapter:
it serves every US region. Built on the new API only; the old WaterServices
shuts down in February 2027 (specification section 6).

Fetches continuous gauge values and channel measurements (the hand measurements
of flow, area, width and velocity that river calibration uses, specification
section 9.1), converts them to SI units and writes them to the local store.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from fdmr_store import ChannelMeasurement, Store, Value

from .http import HttpJsonClient, JsonClient

BASE = "https://api.waterdata.usgs.gov/ogcapi/v0"
SOURCE = "usgs"

FT = 0.3048
CFS = FT**3

# Standard parameter name -> (USGS parameter code, SI unit, factor from the USGS unit)
PARAMETERS: dict[str, tuple[str, str, dict[str, float]]] = {
    "discharge": ("00060", "m3/s", {"ft^3/s": CFS}),
    "gage_height": ("00065", "m", {"ft": FT}),
    "water_temperature": ("00010", "degC", {"degC": 1.0}),
    "specific_conductance": ("00095", "uS/cm", {"uS/cm": 1.0, "uS/cm @25C": 1.0}),
}

LENGTH = {"ft": FT, "m": 1.0}
AREA = {"ft^2": FT**2, "m^2": 1.0}
SPEED = {"ft/s": FT, "m/s": 1.0}
FLOW = {"ft^3/s": CFS, "m^3/s": 1.0}


def to_epoch(iso: str) -> int:
    """ISO 8601 time to epoch seconds. Times without a zone are UTC."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _num(props: dict, key: str, units: dict[str, float]) -> float | None:
    raw = props.get(key)
    if raw in (None, ""):
        return None
    unit = props.get(f"{key}_unit")
    if unit not in units:
        raise ValueError(f"unexpected unit {unit!r} for {key}")
    return float(raw) * units[unit]


class UsgsAdapter:
    def __init__(self, store: Store, client: JsonClient | None = None, page_size: int = 10000) -> None:
        self.store = store
        self.client = client or HttpJsonClient()
        self.page_size = page_size

    def _features(self, collection: str, query: dict[str, str]):
        url: str | None = f"{BASE}/collections/{collection}/items?" + urlencode(
            {"f": "json", "limit": str(self.page_size), **query}
        )
        while url:
            page = self.client.get_json(url)
            yield from page.get("features", [])
            url = next((link["href"] for link in page.get("links", []) if link.get("rel") == "next"), None)

    def datum_m(self, site: str) -> float | None:
        """The gauge's datum: the elevation of its zero, m. Gauge height plus datum
        is the water-surface elevation. None if USGS does not publish one."""
        d = self.client.get_json(f"{BASE}/collections/monitoring-locations/items/USGS-{site}?f=json")
        alt = d.get("properties", {}).get("altitude")
        return None if alt in (None, "") else float(alt) * FT

    def available(self, site: str, active_within_days: int = 30) -> set[str]:
        """Standard parameter names this gauge records as continuous values and
        has reported within the last `active_within_days`. One request."""
        codes = {code: name for name, (code, _, _) in PARAMETERS.items()}
        cutoff = time.time() - active_within_days * 86_400
        found: set[str] = set()
        for f in self._features("time-series-metadata", {"monitoring_location_id": f"USGS-{site}"}):
            p = f["properties"]
            end = p.get("end_utc") or p.get("end")
            # "00011" is the instantaneous statistic: continuous values, not daily means.
            if p.get("statistic_id") == "00011" and p.get("parameter_code") in codes and end:
                if to_epoch(end) >= cutoff:
                    found.add(codes[p["parameter_code"]])
        return found

    def fetch_values(self, site: str, parameter: str, time_range: str) -> int:
        """Fetch continuous values for one gauge and parameter into the store.

        `site` is the USGS site number, such as "11370500". `time_range` is an
        ISO 8601 interval or duration, such as "P14D" or
        "2026-11-01T00:00:00Z/2026-11-15T00:00:00Z". Returns the number stored.
        """
        code, si_unit, factors = PARAMETERS[parameter]
        values = []
        for f in self._features(
            "continuous",
            {"monitoring_location_id": f"USGS-{site}", "parameter_code": code, "time": time_range},
        ):
            p = f["properties"]
            unit = p.get("unit_of_measure")
            if unit not in factors:
                raise ValueError(f"unexpected unit {unit!r} for {parameter} at {site}")
            raw = p.get("value")
            values.append(
                Value(
                    t=to_epoch(p["time"]),
                    value=None if raw in (None, "") else float(raw) * factors[unit],
                    qualifier=p.get("approval_status"),
                )
            )
        return self.store.put_values(SOURCE, site, parameter, si_unit, values)

    def fetch_channel_measurements(self, site: str) -> int:
        """Fetch every channel measurement for one gauge into the store. Returns the number stored."""
        ms = []
        for f in self._features("channel-measurements", {"monitoring_location_id": f"USGS-{site}"}):
            p = f["properties"]
            flow = _num(p, "channel_flow", FLOW)
            if flow is None:
                continue
            ms.append(
                ChannelMeasurement(
                    t=to_epoch(p["time"]),
                    flow=flow,
                    area=_num(p, "channel_area", AREA),
                    width=_num(p, "channel_width", LENGTH),
                    velocity=_num(p, "channel_velocity", SPEED),
                )
            )
        return self.store.put_channel_measurements(SOURCE, site, ms)
