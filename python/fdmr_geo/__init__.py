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
arrays where noted.
"""

from __future__ import annotations

import math

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
    import numpy as np

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
