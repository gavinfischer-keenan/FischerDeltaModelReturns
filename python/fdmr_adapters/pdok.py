"""Rijkswaterstaat's national waterway network (NWB Vaarwegen) through PDOK,
the Dutch government's open geodata service: river lines for a Dutch region
(issue #23), where the USGS's river network does not reach. CC0.

A river's line is its waterway sections (`vaarwegvakken`) by waterway name
(`vwg_naam`, as the network spells it), joined and walked from a start to
an end (fdmr_adapters.lines). The network also carries Rijkswaterstaat's
river-kilometre markers (`kmmarkeringen`), the official chainage, which
`km_markers` reads for checking a built line. Read 26 Nov 2026.

References: ref:pdok_nwb_vaarwegen (references.toml).
"""

from __future__ import annotations

import urllib.parse

from . import http
from .lines import line_between  # noqa: F401  (the walk along the sections)

BASE = "https://api.pdok.nl/rws/nationaal-wegenbestand-vaarwegen/ogc/v1/collections"
ATTRIBUTION = "Rijkswaterstaat, NWB Vaarwegen (PDOK), CC0 1.0"


def get_json(url: str) -> dict:
    return http.get_json(url, accept="application/geo+json", timeout=120)


def _features(collection: str, query: dict, get=get_json) -> list[dict]:
    url = f"{BASE}/{collection}/items?" + urllib.parse.urlencode({"f": "json", "limit": 1000, **query})
    out = []
    while url:
        d = get(url)
        out += d.get("features", [])
        url = next((ln["href"] for ln in d.get("links", []) if ln.get("rel") == "next"), None)
    return out


def _lines(geom: dict) -> list[list[list[float]]]:
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return geom["coordinates"]
    return []


def sections(names: list[str], get=get_json) -> list[list[tuple[int, float, float]]]:
    """The named waterways' sections as pieces of line, each point keyed by its
    position (to 1e-6 degrees), so pieces join where they meet."""
    out = []
    for name in names:
        for f in _features("vaarwegvakken", {"vwg_naam": name}, get):
            for ln in _lines(f["geometry"]):
                out.append([(hash((round(x, 6), round(y, 6))), x, y) for x, y, *_ in ln])
    return out


def km_markers(bbox: tuple[float, float, float, float], get=get_json) -> list[tuple[float, float, float]]:
    """Rijkswaterstaat's river-kilometre markers in bbox (west, south, east,
    north): (lon, lat, km)."""
    out = []
    for f in _features("kmmarkeringen", {"bbox": ",".join(str(v) for v in bbox)}, get):
        km = f["properties"].get("gtlwaarde")
        g = f["geometry"]
        pts = [g["coordinates"]] if g["type"] == "Point" else g["coordinates"]
        out += [(p[0], p[1], float(km)) for p in pts if km is not None]
    return out
