"""A river's line from mapped pieces of it, whatever their source: the pieces
joined where they share a point, walked by the shortest path from the point
nearest a start to the point nearest an end. Used by the OpenStreetMap and
PDOK adapters."""

from __future__ import annotations

import heapq
import math

import fdmr_geo


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    k = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * fdmr_geo.M_PER_DEG


def line_between(segments: list[list[tuple[int, float, float]]], start: tuple[float, float], end: tuple[float, float],
                 join_m: float = 25.0, bridges: list | None = None) -> list[tuple[float, float]]:
    """The shortest path along the ways, from the node nearest `start` to the
    node nearest `end` (lon, lat), as (lon, lat) points. Ways join where they
    share a node; direction is ignored (rivers are mapped both ways in places)."""
    pos: dict[int, tuple[float, float]] = {}
    adj: dict[int, list[tuple[int, float]]] = {}
    for seg in segments:
        for (a, ax, ay), (b, bx, by) in zip(seg, seg[1:]):
            pos[a], pos[b] = (ax, ay), (bx, by)
            d = _dist((ax, ay), (bx, by))
            adj.setdefault(a, []).append((b, d))
            adj.setdefault(b, []).append((a, d))
    if not pos:
        raise ValueError("no waterways found")
    # Pieces mapped separately may end a few metres short of each other (one
    # waterway's sections against the next's): join each piece's end to the
    # nearest point of another piece within `join_m`.
    owner = {nid: k for k, seg in enumerate(segments) for nid, _, _ in seg}
    # Stretches already joined (pieces sharing points), so a gap is crossed
    # only between stretches, never inside one.
    root = list(range(len(segments)))

    def find(i: int) -> int:
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i
    first: dict[int, int] = {}
    for k, seg in enumerate(segments):
        for nid, _, _ in seg:
            if nid in first:
                root[find(k)] = find(first[nid])
            first.setdefault(nid, k)
    comp = {nid: find(owner[nid]) for nid in pos}
    for k, seg in enumerate(segments):
        for nid, x, y in (seg[0], seg[-1]):
            near = min((o for o in pos if comp[o] != comp[nid]), key=lambda o: _dist(pos[o], (x, y)), default=None)
            if near is not None and near not in dict(adj.get(nid, [])):
                d = _dist(pos[near], (x, y))
                if d <= join_m:
                    adj.setdefault(nid, []).append((near, d))
                    adj.setdefault(near, []).append((nid, d))
    s = min(pos, key=lambda i: _dist(pos[i], start))
    t = min(pos, key=lambda i: _dist(pos[i], end))
    best, prev, heap = {s: 0.0}, {}, [(0.0, s)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == t:
            break
        if d > best.get(u, math.inf):
            continue
        for v, w in adj.get(u, []):
            if d + w < best.get(v, math.inf):
                best[v], prev[v] = d + w, u
                heapq.heappush(heap, (d + w, v))
    if t not in best:
        raise ValueError("the start and end are not joined by these waterways")
    path = [t]
    while path[-1] != s:
        path.append(prev[path[-1]])
    path.reverse()
    if bridges is not None:  # the gaps the line was carried across, straight: (lon, lat, m)
        for u, v in zip(path, path[1:]):
            if comp[u] != comp[v] and _dist(pos[u], pos[v]) > 1.0:
                bridges.append((round(pos[u][0], 5), round(pos[u][1], 5), round(_dist(pos[u], pos[v]))))
    return [pos[i] for i in path]
