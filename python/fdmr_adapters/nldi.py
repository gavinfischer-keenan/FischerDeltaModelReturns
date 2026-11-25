"""USGS NLDI (Network-Linked Data Index): the national river network, NHDPlus.
A national adapter. Used once per region to fetch a river's centreline
downstream from a gauge, and where each gauge sits on it."""

from __future__ import annotations

from dataclasses import dataclass

from .http import HttpJsonClient, JsonClient

BASE = "https://api.water.usgs.gov/nldi/linked-data"


@dataclass(frozen=True)
class Flowline:
    comid: int
    coords: list[tuple[float, float]]  # (lon, lat), upstream to downstream


@dataclass(frozen=True)
class SitePosition:
    site: str
    comid: int
    measure: float  # percent along the flowline from its downstream end (NHDPlus convention)
    lon: float
    lat: float


class NldiAdapter:
    def __init__(self, client: JsonClient | None = None) -> None:
        self.client = client or HttpJsonClient()

    def downstream_mainstem(self, site: str, distance_km: float) -> list[Flowline]:
        """Flowlines on the main stem downstream of a USGS gauge, unordered."""
        d = self.client.get_json(f"{BASE}/nwissite/USGS-{site}/navigation/DM/flowlines?distance={distance_km:g}&f=json")
        return [
            Flowline(int(f["properties"]["nhdplus_comid"]), [tuple(c) for c in f["geometry"]["coordinates"]])
            for f in d["features"]
            if f["geometry"]["type"] == "LineString"
        ]

    def site_position(self, site: str) -> SitePosition:
        d = self.client.get_json(f"{BASE}/nwissite/USGS-{site}?f=json")
        f = d["features"][0]
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        return SitePosition(site, int(p["comid"]), float(p["measure"]), lon, lat)
