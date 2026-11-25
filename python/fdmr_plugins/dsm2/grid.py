"""DSM2's channel network as published geometry (docs/format.md, network reaches).

Channels and their junctions come from HYDRO's own output (so they match the
run exactly); centrelines and junction positions from DWR's grid shapefiles,
which are in UTM metres. Each channel's line is turned to run from its
upstream junction to its downstream one, the direction DSM2 counts flow as
positive.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

FT = 0.3048

# GRS 1980 (NAD83), Krüger's series for the transverse Mercator.
_A, _F, _K0 = 6378137.0, 1 / 298.257222101, 0.9996
_N = _F / (2 - _F)
_AA = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64)
_ALPHA = (_N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16, 13 * _N**2 / 48 - 3 * _N**3 / 5, 61 * _N**3 / 240)
_BETA = (_N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96, _N**2 / 48 + _N**3 / 15, 17 * _N**3 / 480)
_DELTA = (2 * _N - 2 * _N**2 / 3 - 2 * _N**3, 7 * _N**2 / 3 - 8 * _N**3 / 5, 56 * _N**3 / 15)


def utm_to_lonlat(x: float, y: float, zone: int) -> tuple[float, float]:
    """Northern-hemisphere UTM metres to (longitude, latitude), degrees."""
    xi, eta = y / (_K0 * _AA), (x - 500_000.0) / (_K0 * _AA)
    xp = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    ep = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    chi = math.asin(math.sin(xp) / math.cosh(ep))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, 1))
    lon0 = math.radians(zone * 6 - 183)
    return math.degrees(lon0 + math.atan2(math.sinh(ep), math.cos(xp))), math.degrees(lat)


def lonlat_to_utm(lon: float, lat: float, zone: int) -> tuple[float, float]:
    """(longitude, latitude) degrees to northern-hemisphere UTM metres."""
    phi, lam = math.radians(lat), math.radians(lon - (zone * 6 - 183))
    e = math.sqrt(_F * (2 - _F))
    t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
    xi, eta = math.atan2(t, math.cos(lam)), math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    x = eta + sum(a * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, a in enumerate(_ALPHA, 1))
    y = xi + sum(a * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, a in enumerate(_ALPHA, 1))
    return 500_000.0 + _K0 * _AA * x, _K0 * _AA * y


@dataclass(frozen=True)
class Channel:
    number: int
    up: int  # upstream junction (DSM2 node number)
    down: int
    length_m: float
    line: list[tuple[float, float]]  # lon, lat, upstream end first


def orient(line: Sequence[tuple[float, float]], up: tuple[float, float] | None, down: tuple[float, float] | None) -> list[tuple[float, float]]:
    """The line running from the upstream junction to the downstream one."""
    pts = list(line)
    if up is None or down is None or len(pts) < 2:
        return pts
    d = lambda a, b: (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2  # noqa: E731
    keep = d(pts[0], up) + d(pts[-1], down)
    flip = d(pts[0], down) + d(pts[-1], up)
    return pts[::-1] if flip < keep else pts


def load(shapefile_prefix: str, channels: Sequence[tuple[int, int, int, float]], zone: int = 10, decimals: int = 5) -> tuple[list[Channel], dict[int, tuple[float, float]]]:
    """Channels (number, up node, down node, length in feet, from HYDRO) with
    their centrelines, and junction positions, from DWR's grid shapefiles."""
    import shapefile  # pyshp

    def ll(p):
        lon, lat = utm_to_lonlat(p[0], p[1], zone)
        return round(lon, decimals), round(lat, decimals)

    nodes = {}
    with shapefile.Reader(f"{shapefile_prefix}nodes") as r:
        for sr in r.iterShapeRecords():
            nodes[int(sr.record[0])] = ll(sr.shape.points[0])
    lines = {}
    with shapefile.Reader(f"{shapefile_prefix}channels_centerlines") as r:
        for sr in r.iterShapeRecords():
            lines[int(sr.record[0])] = [ll(p) for p in sr.shape.points]
    out = []
    for number, up, down, length_ft in channels:
        line = lines.get(number) or [p for p in (nodes.get(up), nodes.get(down)) if p]
        out.append(Channel(number, up, down, length_ft * FT, orient(line, nodes.get(up), nodes.get(down))))
    return out, nodes


def geometry(reach: str, channels: Sequence[Channel], nodes: dict[int, tuple[float, float]], max_points: int = 24) -> dict:
    """The published geometry of a network reach (docs/format.md)."""
    def thin(line):
        if len(line) <= max_points:
            return line
        k = (len(line) - 1) / (max_points - 1)
        return [line[round(i * k)] for i in range(max_points)]

    used = sorted({n for c in channels for n in (c.up, c.down)})
    return {
        "reach": reach,
        "dims": 1,
        "network": True,
        "elements": [
            {"id": c.number, "up": c.up, "down": c.down, "start_m": 0.0, "end_m": round(c.length_m, 1)} for c in channels
        ],
        "nodes": [{"id": n, "lon": nodes[n][0], "lat": nodes[n][1]} for n in used if n in nodes],
        "lines": [[list(p) for p in thin(c.line)] for c in channels],
    }


def read_channels(tidefile: Path) -> list[tuple[int, int, int, float]]:
    """(number, up node, down node, length ft) of every channel, in HYDRO's order."""
    import h5py

    with h5py.File(tidefile, "r") as f:
        t = f["hydro/input/channel"][:]
        order = list(f["hydro/geometry/channel_number"][:])
    by_number = {int(r["chan_no"]): (int(r["chan_no"]), int(r["upnode"]), int(r["downnode"]), float(r["length"])) for r in t}
    return [by_number[int(n)] for n in order]
