"""Line pieces from a GeoJSON file a region names by its address (#72): such
as a state agency's published canal centrelines (regions/<id>/canals.toml
[sources]). The features whose properties match are taken as pieces of line
keyed by position (fdmr_adapters.lines.keyed), to be walked from a start to
an end (lines.line_between). The file's licence and reference are the
region's to state, beside its address.
"""

from __future__ import annotations

from . import http
from .lines import keyed

TIMEOUT_S = 180  # a whole state's centrelines can be tens of megabytes


def download(url: str, get=None) -> dict:
    """The file at `url`, as GeoJSON."""
    if get is not None:
        return get(url)
    return http.get_json(url, accept="application/geo+json, application/json", timeout=TIMEOUT_S)


def pieces(collection: dict, attrs: dict[str, str]) -> list[list[tuple[int, float, float]]]:
    """The line features of a FeatureCollection whose properties all equal
    `attrs`, as pieces of line keyed by position."""
    out = []
    for f in collection.get("features", []):
        if not all(f.get("properties", {}).get(k) == v for k, v in attrs.items()):
            continue
        g = f.get("geometry") or {}
        lines = [g["coordinates"]] if g.get("type") == "LineString" else g.get("coordinates", []) if g.get("type") == "MultiLineString" else []
        out += [keyed(ln) for ln in lines]
    return out
