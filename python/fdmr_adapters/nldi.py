"""USGS NLDI (Network-Linked Data Index): the national river network, NHDPlus.
A national adapter. Used once per region to fetch a river's centreline
downstream from a gauge, and where each gauge sits on it.

References: ref:nldi (references.toml).
"""

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

    def downstream_from(self, comid: int, distance_km: float) -> list[Flowline]:
        """Flowlines on the main stem downstream of a flowline, unordered."""
        d = self.client.get_json(f"{BASE}/comid/{comid}/navigation/DM/flowlines?distance={distance_km:g}&f=json")
        return [
            Flowline(int(f["properties"]["nhdplus_comid"]), [tuple(c) for c in f["geometry"]["coordinates"]])
            for f in d["features"]
            if f["geometry"]["type"] == "LineString"
        ]

    def point_position(self, lon: float, lat: float, name: str = "") -> SitePosition:
        """A point snapped to its nearest flowline: the flowline NLDI finds
        there, and the point of it nearest, as a measure (percent from its
        downstream end) and a position."""
        import math

        d = self.client.get_json(f"{BASE}/comid/position?coords=POINT({lon:.6f}%20{lat:.6f})&f=json")
        f = d["features"][0]
        line = [tuple(c[:2]) for c in f["geometry"]["coordinates"]]  # upstream to downstream
        k = math.cos(math.radians(lat))
        seg = [0.0]
        for a, b in zip(line, line[1:]):
            seg.append(seg[-1] + math.hypot((b[0] - a[0]) * k, b[1] - a[1]))
        i = min(range(len(line)), key=lambda j: math.hypot((line[j][0] - lon) * k, line[j][1] - lat))
        measure = 100.0 * (1 - seg[i] / seg[-1]) if seg[-1] > 0 else 50.0
        return SitePosition(name, int(f["properties"]["comid"]), measure, line[i][0], line[i][1])

    def site_position(self, site: str) -> SitePosition:
        d = self.client.get_json(f"{BASE}/nwissite/USGS-{site}?f=json")
        f = d["features"][0]
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        return SitePosition(site, int(p["comid"]), float(p["measure"]), lon, lat)
