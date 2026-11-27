"""Named flowlines of the USGS's National Hydrography Dataset (NHD), through
The National Map's map service: a river line where the river network that
NLDI navigates does not follow the water (a floodway's low channel, such as
the Yolo Bypass's Tule Canal and toe drain, issue #15). Public domain.

A line is the flowlines of the given GNIS names inside a box, as pieces
keyed by position (to 1e-6 degrees) so pieces join where they meet, walked
from a start to an end (fdmr_adapters.lines). The canals use the same
service (fdmr_runner.canals).

References: ref:nhd (references.toml).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from .http import USER_AGENT
from .lines import line_between  # noqa: F401  (the walk along the pieces)

URL = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/6/query"
ATTRIBUTION = "U.S. Geological Survey, National Hydrography Dataset (public domain)"


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def _lines(geom: dict) -> list[list[list[float]]]:
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return geom["coordinates"]
    return []


def flowlines(names: list[str], bbox: tuple[float, float, float, float], get=get_json) -> list[list[tuple[int, float, float]]]:
    """The flowlines named `names` (GNIS) inside `bbox` (west, south, east,
    north), as pieces of line, each point keyed by its position."""
    where = " OR ".join("gnis_name='{}'".format(n.replace("'", "''")) for n in names)
    q = urllib.parse.urlencode({
        "where": where, "outFields": "gnis_name", "returnGeometry": "true", "outSR": 4326, "inSR": 4326,
        "geometry": ",".join(str(x) for x in bbox), "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects", "f": "geojson",
    })
    out = []
    for f in get(f"{URL}?{q}").get("features", []):
        for ln in _lines(f["geometry"]):
            out.append([(hash((round(x, 6), round(y, 6))), x, y) for x, y, *_ in ln])
    return out
