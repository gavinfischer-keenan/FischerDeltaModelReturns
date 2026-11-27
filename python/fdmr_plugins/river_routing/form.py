"""The form of each river element, from its line (issue #27): how much it
turns and how winding it is, so that maths modifiers for a kind of place (a
bend, a straight run) can be matched to every place of that kind, model-wide.

For an element from chainage `start` to `end`, the line is sampled every
`step_m` (default 100 m, so a mapped line's small wiggles are not counted as
bends), and from the headings between samples:

- turn_deg: the net change of heading over the element, degrees, positive to
  the left (anticlockwise seen from above), in (-180, 180];
- abs_turn_deg: the sum of the absolute changes of heading, degrees: the
  bending however it alternates;
- sinuosity: the element's length along the line over the straight distance
  between its ends (1 for a straight element).

Grade is not here: the river lines carry no elevation, and the water
surface's slope between gauges is worked out by the publisher from the
gauges' levels (fdmr_runner.publish segment_slopes).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

Point = tuple[float, float]  # lon, lat


def _xy(p: Point, lat0: float) -> tuple[float, float]:
    k = math.cos(math.radians(lat0))
    return p[0] * k * 111_195, p[1] * 111_195


def heading(a: Point, b: Point) -> float:
    """Direction from a to b, degrees anticlockwise from east (locally flat)."""
    lat0 = (a[1] + b[1]) / 2
    (x0, y0), (x1, y1) = _xy(a, lat0), _xy(b, lat0)
    return math.degrees(math.atan2(y1 - y0, x1 - x0))


def turn(h0: float, h1: float) -> float:
    """The change from heading h0 to h1, degrees, in (-180, 180]."""
    d = (h1 - h0) % 360.0
    return d - 360.0 if d > 180.0 else d


def element_form(point_at: Callable[[float], Point], start: float, end: float, step_m: float = 100.0) -> dict:
    """turn_deg, abs_turn_deg and sinuosity of the element [start, end] of a
    line, `point_at(chainage)` giving its (lon, lat)."""
    n = max(2, math.ceil((end - start) / step_m))
    pts = [point_at(start + (end - start) * i / n) for i in range(n + 1)]
    hs = [heading(a, b) for a, b in zip(pts, pts[1:]) if a != b]
    turns = [turn(a, b) for a, b in zip(hs, hs[1:])]
    lat0 = pts[0][1]
    (x0, y0), (x1, y1) = _xy(pts[0], lat0), _xy(pts[-1], lat0)
    chord = math.hypot(x1 - x0, y1 - y0)
    return {
        "turn_deg": round(sum(turns), 1),
        "abs_turn_deg": round(sum(abs(t) for t in turns), 1),
        "sinuosity": round((end - start) / chord, 3) if chord > 0 else None,
    }


def forms(point_at: Callable[[float], Point], bounds: Sequence[tuple[float, float]], step_m: float = 100.0) -> dict[str, list]:
    """Each element's form, as lists in element order: {turn_deg, abs_turn_deg, sinuosity}."""
    rows = [element_form(point_at, a, b, step_m) for a, b in bounds]
    return {k: [r[k] for r in rows] for k in ("turn_deg", "abs_turn_deg", "sinuosity")}
