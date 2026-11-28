"""NHDPlus Version 2's value-added attributes for flowlines (by COMID), from
the USGS's water data GeoServer: each flowline's length and slope (m/m, from
smoothed elevations along it; NHDPlus gives 0.00001 where a flowline is flat
or its slope could not be worked out). Public domain.

References: ref:nhdplus (references.toml).
"""

from __future__ import annotations

import urllib.parse

from . import http

URL = "https://api.water.usgs.gov/geoserver/wmadata/ows"
ATTRIBUTION = "U.S. Geological Survey and U.S. EPA, NHDPlus Version 2 (public domain)"
BATCH = 80  # COMIDs per request: the filter goes in the URL


def get_json(url: str) -> dict:
    return http.get_json(url, timeout=120)


def slopes(comids: list[int], get=get_json, names: dict[int, str] | None = None) -> dict[int, tuple[float, float]]:
    """{comid: (slope m/m, length km)} for the flowlines NHDPlus has. With
    `names`, each flowline's GNIS name is put in it too."""
    out: dict[int, tuple[float, float]] = {}
    for i in range(0, len(comids), BATCH):
        ids = ",".join(str(int(c)) for c in comids[i:i + BATCH])
        q = urllib.parse.urlencode({
            "service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "wmadata:nhdflowline_network",
            "outputFormat": "application/json", "propertyName": "comid,slope,lengthkm,gnis_name", "CQL_FILTER": f"comid IN ({ids})",
        })
        for f in get(f"{URL}?{q}").get("features", []):
            p = f.get("properties", {})
            if p.get("slope") is not None and p.get("lengthkm") is not None and float(p["slope"]) >= 0:
                out[int(p["comid"])] = (float(p["slope"]), float(p["lengthkm"]))
                if names is not None:
                    names[int(p["comid"])] = (p.get("gnis_name") or "").strip()
    return out
