"""A river's centreline as one ordered line, with distance along it ("chainage").

Built from the national river network's flowlines, which arrive unordered and
each run upstream to downstream. They are chained end to start, from the
release gauge's flowline down. Gauges are placed by their flowline and their
measure (percent from the flowline's downstream end).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

EARTH_RADIUS_M = 6_371_008.8  # m: the mean Earth radius R1 of the Geodetic Reference System 1980 (Moritz 2000, ref:moritz2000grs80)


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lon, lat) points, m."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def line_length(coords: Sequence[tuple[float, float]]) -> float:
    return sum(distance_m(a, b) for a, b in zip(coords, coords[1:]))


@dataclass
class Centreline:
    coords: list[tuple[float, float]]
    chainage: list[float]  # m from the start, one per coordinate
    flowline_start: dict[int, float] = field(default_factory=dict)  # comid -> chainage of its upstream end
    flowline_length: dict[int, float] = field(default_factory=dict)

    @property
    def length(self) -> float:
        return self.chainage[-1]

    def position_of(self, comid: int, measure: float) -> float:
        """Chainage of a point given by its flowline and NHDPlus measure (0 = downstream end)."""
        return self.flowline_start[comid] + (1 - measure / 100) * self.flowline_length[comid]

    def point_at(self, s: float) -> tuple[float, float]:
        """(lon, lat) at chainage s, linear between vertices."""
        s = min(max(s, 0.0), self.length)
        lo, hi = 0, len(self.chainage) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.chainage[mid] <= s:
                lo = mid
            else:
                hi = mid
        span = self.chainage[hi] - self.chainage[lo]
        f = 0.0 if span == 0 else (s - self.chainage[lo]) / span
        (x0, y0), (x1, y1) = self.coords[lo], self.coords[hi]
        return (x0 + f * (x1 - x0), y0 + f * (y1 - y0))


def nearest(line: "Centreline", lon: float, lat: float) -> tuple[float, float]:
    """The chainage (m) of the point on the line nearest (lon, lat), and how far
    off the line that point is (m). Uses a local flat projection, fine over the
    few hundred metres that matter."""
    kx = math.cos(math.radians(lat)) * EARTH_RADIUS_M * math.pi / 180
    ky = EARTH_RADIUS_M * math.pi / 180
    best = (float("inf"), 0.0)
    for (x0, y0), (x1, y1), s0, s1 in zip(line.coords, line.coords[1:], line.chainage, line.chainage[1:]):
        ax, ay = (x1 - x0) * kx, (y1 - y0) * ky
        px, py = (lon - x0) * kx, (lat - y0) * ky
        seg2 = ax * ax + ay * ay
        f = 0.0 if seg2 == 0 else min(1.0, max(0.0, (px * ax + py * ay) / seg2))
        d = math.hypot(px - f * ax, py - f * ay)
        if d < best[0]:
            best = (d, s0 + f * (s1 - s0))
    return best[1], best[0]


def chain(flowlines: Sequence[tuple[int, list[tuple[float, float]]]], start_comid: int, tol_deg: float = 1e-6) -> Centreline:
    """Chain flowlines end to start, beginning at `start_comid`."""
    by_start: dict[tuple[float, float], tuple[int, list[tuple[float, float]]]] = {}
    for comid, coords in flowlines:
        by_start[(round(coords[0][0] / tol_deg), round(coords[0][1] / tol_deg))] = (comid, coords)
    by_comid = {c: (c, xy) for c, xy in flowlines}
    if start_comid not in by_comid:
        raise ValueError(f"flowline {start_comid} is not among those fetched")
    line = Centreline(coords=[], chainage=[])
    current: tuple[int, list[tuple[float, float]]] | None = by_comid[start_comid]
    seen: set[int] = set()
    s = 0.0
    while current and current[0] not in seen:
        comid, coords = current
        seen.add(comid)
        line.flowline_start[comid] = s
        length = line_length(coords)
        line.flowline_length[comid] = length
        for i, xy in enumerate(coords):
            if line.coords and i == 0:
                continue  # shared with the previous flowline's last point
            if line.coords:
                s += distance_m(line.coords[-1], xy)
            line.coords.append(xy)
            line.chainage.append(s)
        end = coords[-1]
        current = by_start.get((round(end[0] / tol_deg), round(end[1] / tol_deg)))
    return line
