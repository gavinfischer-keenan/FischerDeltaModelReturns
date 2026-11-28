"""Distances on the ground, written once (#69): a local flat plane about a
latitude, distances in it, and the nearest point on a line.

Over the tens of kilometres a reach, a station's search radius or an area
spans, the Earth is flat to well under 0.1%: a degree of latitude is
M_PER_DEG metres everywhere, and a degree of longitude that times the cosine
of the latitude. M_PER_DEG is the arc of one degree on a sphere of the mean
Earth radius, 6,371 km: the Geodetic Reference System 1980's mean radius R1,
6,371,008.8 m (Moritz 2000, ref:moritz2000grs80), to the metre's
hundredth of a part per million that a rounding of the degree to 111,195 m
makes. Longer paths along a river use great circles on the same radius
(fdmr_plugins.river_routing.centreline).

Points are (lon, lat) in degrees. Functions take plain floats, or numpy
arrays where noted. Satellite tiles on the Sentinel-2 grid are in UTM: `utm`
and `lonlat_from_utm` project to and from it exactly (Karney 2011), not on
the flat plane.
"""

from __future__ import annotations

import math

import numpy as np

M_PER_DEG = 111_195.0  # m per degree of latitude: 2 pi 6,371 km / 360 (GRS80 R1, ref:moritz2000grs80)


def xy(lon, lat, lat0: float):
    """Metres east and north of (0, 0) on the plane about latitude `lat0`
    (floats or numpy arrays)."""
    return lon * M_PER_DEG * math.cos(math.radians(lat0)), lat * M_PER_DEG


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """The distance between two points, m, on the plane about the first's latitude."""
    k = math.cos(math.radians(a[1]))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * M_PER_DEG


def distances_m(lon, lat, p: tuple[float, float]):
    """The distance from each of many points (numpy arrays) to `p`, m, on the plane about `p`."""
    return np.hypot((np.asarray(lon) - p[0]) * math.cos(math.radians(p[1])), np.asarray(lat) - p[1]) * M_PER_DEG


def along(line: list[tuple[float, float]]) -> list[float]:
    """Each vertex's distance along the line from its first, m."""
    out = [0.0]
    for a, b in zip(line, line[1:]):
        out.append(out[-1] + distance_m(a, b))
    return out


def nearest_on_line(line: list[tuple[float, float]], p: tuple[float, float]) -> tuple[int, float, float, float]:
    """Where on the polyline `line` is nearest `p`: (segment index, fraction
    along that segment 0 to 1, distance off the line m, distance along the
    line m). The nearest point on any segment, not the nearest vertex."""
    if not line:
        raise ValueError("an empty line")
    if len(line) == 1:
        return 0, 0.0, distance_m(p, line[0]), 0.0
    k = math.cos(math.radians(p[1]))
    px, py = p[0] * k, p[1]
    best = (math.inf, 0, 0.0)
    for i, (a, b) in enumerate(zip(line, line[1:])):
        ax, ay, bx, by = a[0] * k, a[1], b[0] * k, b[1]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        f = 0.0 if L2 == 0 else min(1.0, max(0.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        d2 = (ax + f * dx - px) ** 2 + (ay + f * dy - py) ** 2
        if d2 < best[0]:
            best = (d2, i, f)
    d2, i, f = best
    cum = along(line)
    return i, f, math.sqrt(d2) * M_PER_DEG, cum[i] + f * (cum[i + 1] - cum[i])


def shortest_path(adj: dict, src, dst) -> list[tuple] | None:
    """The shortest path through a network by Dijkstra's method (Dijkstra 1959,
    ref:dijkstra1959), written once (#72): `adj` maps each node to its links
    as (neighbour, length, what the link is). Returns [(src, None), (node,
    the link reaching it), ..., (dst, link)], or None where `dst` cannot be
    reached. Ties go to the link listed first."""
    import heapq

    dist, back, heap = {src: 0.0}, {}, [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == dst:
            break
        if d > dist.get(u, math.inf):
            continue
        for v, w, link in adj.get(u, []):
            if d + w < dist.get(v, math.inf):
                dist[v], back[v] = d + w, (u, link)
                heapq.heappush(heap, (d + w, v))
    if dst != src and dst not in back:
        return None
    path, n = [], dst
    while n != src:
        u, link = back[n]
        path.append((n, link))
        n = u
    path.append((src, None))
    return path[::-1]


# --- UTM: the satellites' tiles (#92) --------------------------------------------


def utm(lon, lat, zone: int) -> tuple[np.ndarray, np.ndarray]:
    """WGS84 longitude and latitude (degrees) to UTM easting and northing (m),
    northern hemisphere: the transverse Mercator projection by Krüger's series
    to the third order in n (Karney 2011, ref:karney2011, J. Geodesy 85:475, equations 7-11),
    good to well under a metre within the zone."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    n = f / (2 - f)
    big_a = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    alpha = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16, 13 * n ** 2 / 48 - 3 * n ** 3 / 5, 61 * n ** 3 / 240)
    e = math.sqrt(f * (2 - f))
    phi = np.radians(np.asarray(lat, dtype=float))
    lam = np.radians(np.asarray(lon, dtype=float)) - math.radians(zone * 6 - 183)
    t = np.sinh(np.arctanh(np.sin(phi)) - e * np.arctanh(e * np.sin(phi)))
    xi = np.arctan2(t, np.cos(lam))
    eta = np.arctanh(np.sin(lam) / np.sqrt(1 + t * t))
    east = eta + sum(al * np.cos(2 * j * xi) * np.sinh(2 * j * eta) for j, al in enumerate(alpha, 1))
    north = xi + sum(al * np.sin(2 * j * xi) * np.cosh(2 * j * eta) for j, al in enumerate(alpha, 1))
    return 500_000.0 + k0 * big_a * east, k0 * big_a * north


def lonlat_from_utm(east, north, zone: int) -> tuple[np.ndarray, np.ndarray]:
    """UTM easting and northing (m, northern hemisphere) back to WGS84
    longitude and latitude (degrees): the inverse of `utm`, by Krüger's series
    to the third order in n (Karney 2011, ref:karney2011, J. Geodesy 85:475, equations 15-21)."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    n = f / (2 - f)
    big_a = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    beta = (n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96, n ** 2 / 48 + n ** 3 / 15, 17 * n ** 3 / 480)
    delta = (2 * n - 2 * n ** 2 / 3 - 2 * n ** 3, 7 * n ** 2 / 3 - 8 * n ** 3 / 5, 56 * n ** 3 / 15)
    xi = np.asarray(north, dtype=float) / (k0 * big_a)
    eta = (np.asarray(east, dtype=float) - 500_000.0) / (k0 * big_a)
    xi_p = xi - sum(b * np.sin(2 * j * xi) * np.cosh(2 * j * eta) for j, b in enumerate(beta, 1))
    eta_p = eta - sum(b * np.cos(2 * j * xi) * np.sinh(2 * j * eta) for j, b in enumerate(beta, 1))
    chi = np.arcsin(np.sin(xi_p) / np.cosh(eta_p))
    lat = chi + sum(d * np.sin(2 * j * chi) for j, d in enumerate(delta, 1))
    lon = math.radians(zone * 6 - 183) + np.arctan2(np.sinh(eta_p), np.cos(xi_p))
    return np.degrees(lon), np.degrees(lat)
