"""Named flowlines of the USGS's National Hydrography Dataset (NHD), through
The National Map's map service: a river line where the river network that
NLDI navigates does not follow the water (a floodway's low channel, such as
the Yolo Bypass's Tule Canal and toe drain, issue #15), and the export
canals' lines where the region takes them from NHD (fdmr_runner.canals).
Public domain.

A line is the flowlines of the given GNIS names inside a box, as pieces
keyed by position (to 1e-6 degrees) so pieces join where they meet, walked
from a start to an end (fdmr_adapters.lines).

References: ref:nhd (references.toml).
"""

from __future__ import annotations

import urllib.parse

from . import http
from .lines import keyed, line_between  # noqa: F401  (line_between: the walk along the pieces)

URL = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/6/query"
# Waterbody - Large Scale: lakes, ponds, reservoirs, flooded areas, as outlines.
WATERBODY_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/12/query"
ATTRIBUTION = "U.S. Geological Survey, National Hydrography Dataset (public domain)"


def get_json(url: str) -> dict:
    return http.get_json(url, accept="application/geo+json", timeout=120)


def _lines(geom: dict) -> list[list[list[float]]]:
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return geom["coordinates"]
    return []


def _query(where: str, fields: list[str], bbox: tuple[float, float, float, float], get) -> list[dict]:
    """The flowline features matching `where` (SQL) inside `bbox` (west, south, east, north)."""
    q = urllib.parse.urlencode({
        "where": where, "outFields": ",".join(fields), "returnGeometry": "true", "outSR": 4326, "inSR": 4326,
        "geometry": ",".join(str(x) for x in bbox), "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects", "f": "geojson",
    })
    return get(f"{URL}?{q}").get("features", [])


def _quoted(v: str) -> str:
    return "'{}'".format(str(v).replace("'", "''"))


def flowlines(names: list[str], bbox: tuple[float, float, float, float], get=get_json) -> list[list[tuple[int, float, float]]]:
    """The flowlines named `names` (GNIS) inside `bbox` (west, south, east,
    north), as pieces of line, each point keyed by its position."""
    where = " OR ".join(f"gnis_name={_quoted(n)}" for n in names)
    out = []
    for f in _query(where, ["gnis_name"], bbox, get):
        for ln in _lines(f["geometry"]):
            out.append([(hash((round(x, 6), round(y, 6))), x, y) for x, y, *_ in ln])
    return out


def pieces(attrs: dict[str, str], bbox: tuple[float, float, float, float], get=get_json) -> list[list[tuple[int, float, float]]]:
    """The flowlines whose attributes all equal `attrs` (such as
    {"gnis_name": "Delta-Mendota Canal"}) inside `bbox`, as pieces of line
    keyed by position, at 1e-6 degrees (lines.keyed)."""
    where = " AND ".join(f"{k}={_quoted(v)}" for k, v in attrs.items())
    return [keyed(ln) for f in _query(where, list(attrs), bbox, get) for ln in _lines(f["geometry"])]


def waterbody_at(lon: float, lat: float, get=get_json) -> tuple[dict, list[list[tuple[float, float]]]]:
    """The NHD waterbody (Waterbody - Large Scale) holding the point: its
    attributes and its outline's rings of (lon, lat)."""
    q = urllib.parse.urlencode({
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "true",
        "outSR": 4326, "f": "geojson",
    })
    feats = get(f"{WATERBODY_URL}?{q}").get("features", [])
    if not feats:
        raise ValueError(f"no NHD waterbody at {lon}, {lat}")
    f = max(feats, key=lambda x: float({k.lower(): v for k, v in x.get("properties", {}).items()}.get("areasqkm") or 0))
    g = f["geometry"]
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
    rings = [[(x, y) for x, y, *_ in ring] for poly in polys for ring in poly]
    return {k.lower(): v for k, v in f.get("properties", {}).items()}, rings
