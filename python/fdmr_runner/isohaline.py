"""Where a tidally averaged salinity falls to a threshold, along an estuary's
axis, through time (specification 3.5 and 7.5): an isohaline position such as
X2.

The axis is a chain of a network reach's channels, found as the shortest path
by length between two points, and measured in the region's own distances: the
config gives anchor points with their published distances, and distance along
the chain is mapped piecewise-linearly between them (beyond the outer anchors,
one km of channel is one km). Each channel's value is placed at its midpoint
along the chain.

The field (such as specific conductance) is tidally averaged per channel with
Godin's filter (ref:godin1972), so the position is the tidally averaged one, as the isohaline's
definition requires. Walking up the axis from the sea, the position is where
the field first falls below the threshold, interpolated linearly between the
two channels either side. Where even the seaward end is below the threshold,
or the landward end above it, the position is outside the axis and is given as
None with which side it is on.
"""

from __future__ import annotations

import heapq
import math
from pathlib import Path

import numpy as np

from fdmr_plugins.fvcom.correct import tidal_mean

KM_PER_DEG_LAT = 111.195  # mean Earth radius 6371 km


def _xy(lon: float, lat: float, lat0: float) -> tuple[float, float]:
    """Local flat coordinates, km, about latitude lat0 (ample for tens of km)."""
    return lon * KM_PER_DEG_LAT * math.cos(math.radians(lat0)), lat * KM_PER_DEG_LAT


def chain(net: dict, start: tuple[float, float], end: tuple[float, float]) -> list[tuple[int, bool]]:
    """The channels (index into `elements`, and whether walked against their own
    up-to-down direction) on the shortest path by length from the node nearest
    `start` (lon, lat) to the node nearest `end`."""
    nodes = {n["id"]: (n["lon"], n["lat"]) for n in net["nodes"]}
    lat0 = (start[1] + end[1]) / 2

    def nearest(p: tuple[float, float]) -> int:
        px, py = _xy(*p, lat0)
        return min(nodes, key=lambda k: (_xy(*nodes[k], lat0)[0] - px) ** 2 + (_xy(*nodes[k], lat0)[1] - py) ** 2)

    adj: dict[int, list[tuple[int, float, int, bool]]] = {}
    for i, e in enumerate(net["elements"]):
        length = e["end_m"] - e["start_m"]
        adj.setdefault(e["up"], []).append((e["down"], length, i, False))
        adj.setdefault(e["down"], []).append((e["up"], length, i, True))
    a, b = nearest(start), nearest(end)
    dist, back = {a: 0.0}, {}
    heap = [(0.0, a)]
    while heap:
        d, n = heapq.heappop(heap)
        if n == b:
            break
        if d > dist.get(n, math.inf):
            continue
        for m, length, i, rev in adj.get(n, []):
            if d + length < dist.get(m, math.inf):
                dist[m] = d + length
                back[m] = (n, i, rev)
                heapq.heappush(heap, (d + length, m))
    if b not in back and a != b:
        raise ValueError("no path along the network between the axis's ends")
    out = []
    n = b
    while n != a:
        p, i, rev = back[n]
        out.append((i, rev))
        n = p
    return out[::-1]


class Axis:
    """A chain of channels, with distances along it in the region's own km."""

    def __init__(self, net: dict, anchors: list[dict]) -> None:
        if len(anchors) < 2:
            raise ValueError("an axis needs at least two anchors")
        anchors = sorted(anchors, key=lambda a: a["km"])
        self.links = chain(net, (anchors[0]["lon"], anchors[0]["lat"]), (anchors[-1]["lon"], anchors[-1]["lat"]))
        lat0 = sum(a["lat"] for a in anchors) / len(anchors)
        pts: list[tuple[float, float]] = []
        mid: list[float] = []  # each channel's midpoint, km of chain from its start
        run = 0.0
        for i, rev in self.links:
            line = net["lines"][i][::-1] if rev else net["lines"][i]
            xy = [_xy(lon, lat, lat0) for lon, lat in line]
            seg = [math.dist(xy[k], xy[k + 1]) for k in range(len(xy) - 1)]
            mid.append(run + sum(seg) / 2)
            for k, p in enumerate(line):
                if pts and k == 0:
                    continue  # the junction is the last channel's end
                pts.append((p[0], p[1]))
            run += sum(seg)
        self.lonlat = pts
        xy = np.array([_xy(lon, lat, lat0) for lon, lat in pts])
        self.along = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
        # Each anchor's place on the chain: the nearest point of the line.
        d_anchor = []
        for a in anchors:
            p = np.array(_xy(a["lon"], a["lat"], lat0))
            best = math.inf, 0.0
            for k in range(len(xy) - 1):
                u = xy[k + 1] - xy[k]
                L2 = float(u @ u)
                f = 0.0 if L2 == 0 else min(1.0, max(0.0, float((p - xy[k]) @ u) / L2))
                q = xy[k] + f * u
                dd = float(np.hypot(*(p - q)))
                if dd < best[0]:
                    best = dd, self.along[k] + f * math.sqrt(L2)
            d_anchor.append(best[1])
            a["off_km"] = round(best[0], 3)
        self.anchors = anchors
        self.d_anchor = np.array(d_anchor)
        if np.any(np.diff(self.d_anchor) <= 0):
            raise ValueError("the anchors do not lie in order along the axis")
        self.km_anchor = np.array([a["km"] for a in anchors], dtype=float)
        self.mid_km = self.km(np.array(mid))
        self.line_km = self.km(self.along)

    def km(self, d: np.ndarray) -> np.ndarray:
        """Chain distance (km from its start) to the region's km."""
        d = np.asarray(d, dtype=float)
        inside = np.interp(d, self.d_anchor, self.km_anchor)
        lo = self.km_anchor[0] + (d - self.d_anchor[0])
        hi = self.km_anchor[-1] + (d - self.d_anchor[-1])
        return np.where(d < self.d_anchor[0], lo, np.where(d > self.d_anchor[-1], hi, inside))

    def point(self, km: float) -> tuple[float, float]:
        """The (lon, lat) at a km along the axis."""
        lon = float(np.interp(km, self.line_km, [p[0] for p in self.lonlat]))
        lat = float(np.interp(km, self.line_km, [p[1] for p in self.lonlat]))
        return lon, lat


def crossing(km: np.ndarray, values: np.ndarray, threshold: float) -> tuple[float | None, str]:
    """Walking up the axis (km increasing), where `values` first fall below
    `threshold`: (km, "") or (None, "seaward") if already below at the first
    point, (None, "landward") if never, (None, "missing") with no values."""
    ok = np.isfinite(values)
    km, values = km[ok], values[ok]
    if not len(values):
        return None, "missing"
    if values[0] < threshold:
        return None, "seaward"
    for k in range(1, len(values)):
        if values[k] < threshold:
            v0, v1 = values[k - 1], values[k]
            f = (v0 - threshold) / (v0 - v1)
            return float(km[k - 1] + f * (km[k] - km[k - 1])), ""
    return None, "landward"


def _runs(ok: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) of each run of True in `ok`."""
    edges = np.diff(np.concatenate([[0], ok.astype(np.int8), [0]]))
    return list(zip(np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]))


def series(axis: Axis, day_files: list[Path], field: str, threshold: float, every_s: int = 3600) -> dict:
    """The isohaline's position from a network reach's water-quality day files:
    {"t0", "step_s", "km": [...], "outside": [...]} every `every_s` seconds,
    from the field tidally averaged per channel across all the days as one series."""
    import fdmr_py

    cols = [i for i, _ in axis.links]
    days = []
    for f in sorted(day_files):
        _, _, n, _, steps, ft0, fstep, flds = fdmr_py.read_field_file(str(f))
        vals = next((v for name, _, v in flds if name == field), None)
        days.append((ft0, fstep, steps, None if vals is None else np.asarray(vals, dtype=float).reshape(steps, n)[:, cols]))
    if not days:
        return {"t0": None, "step_s": every_s, "km": [], "outside": []}
    # Each day in its place in time; a missing day, or one without the field,
    # is missing (NaN), never closed up (#59).
    t0, step = min(d[0] for d in days), days[0][1]
    if any(d[1] != step for d in days):
        raise ValueError("day files with different time steps")
    x = np.full((max((d0 - t0) // step + k for d0, _, k, _ in days), len(cols)), np.nan)
    for d0, _, k, v in days:
        if v is not None:
            x[(d0 - t0) // step:(d0 - t0) // step + k] = v
    # Tidally averaged channel by channel, each unbroken run of values on its
    # own: a gap is never filled with unaveraged values (#71).
    m = np.full(x.shape, np.nan)
    for j in range(x.shape[1]):
        for a, b in _runs(np.isfinite(x[:, j])):
            m[a:b, j] = tidal_mean(x[a:b, j], 3600 / step)
    every = max(1, every_s // step)
    first = (-t0) % every_s // step  # on the whole hour
    kms, outside = [], []
    for k in range(first, len(m), every):
        p, why = crossing(axis.mid_km, m[k], threshold)
        kms.append(None if p is None else round(p, 2))
        outside.append(why)
    return {"t0": int(t0 + first * step), "step_s": every_s, "km": kms, "outside": outside}
