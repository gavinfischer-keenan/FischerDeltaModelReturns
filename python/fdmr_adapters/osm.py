"""River lines from OpenStreetMap, through the Overpass API: a global adapter,
for regions outside the United States, where the USGS's river network (NLDI)
does not reach.

A river's line is taken from its mapped waterways (the main stream of its
waterway relation, else ways tagged `waterway` =
river or canal, by name), joined where they share a node, and walked by the
shortest path from the point nearest a start to the point nearest an end. The
data are OpenStreetMap contributors', under the Open Database Licence: the
line built from them, as published, carries that attribution and licence.

References: ref:osm (references.toml).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .http import USER_AGENT
from .lines import line_between  # noqa: F401  (the walk along the ways)

SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)"


def query(ql: str, retries: int = 4, sleep=time.sleep) -> dict:
    """One Overpass query, tried on each server with back-off (the public
    servers turn requests away when busy)."""
    last = None
    for attempt in range(retries):
        for url in SERVERS:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": ql}).encode(),
                                         headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.load(r)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last = e
        sleep(30 * 2**attempt)
    raise RuntimeError(f"Overpass gave no answer: {last}")


def ways(names: list[str], bbox: tuple[float, float, float, float], get=query) -> list[list[tuple[int, float, float]]]:
    """The named waterways inside bbox (west, south, east, north), each as its
    nodes (id, lon, lat) in order. A river mapped as a waterway relation marks
    its main stream apart from side channels of the same name: its main stream
    is taken where there is one, else every way of the name."""
    w, s, e, n = bbox
    rx = "^(" + "|".join(x.replace("(", "\\(").replace(")", "\\)") for x in names) + ")$"
    d = get(f'[out:json][timeout:120];relation["type"="waterway"]["name"~"{rx}"]({s},{w},{n},{e})->.r;'
            f'way(r.r:"main_stream")({s},{w},{n},{e});out geom;')
    if not d.get("elements"):
        d = get(f'[out:json][timeout:120];way["waterway"~"^(river|canal)$"]["name"~"{rx}"]({s},{w},{n},{e});out geom;')
    out = []
    for el in d.get("elements", []):
        if el.get("type") == "way" and el.get("geometry") and el.get("nodes"):
            out.append([(nid, g["lon"], g["lat"]) for nid, g in zip(el["nodes"], el["geometry"])])
    return out


