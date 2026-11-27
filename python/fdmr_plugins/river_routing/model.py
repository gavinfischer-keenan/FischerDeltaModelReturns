"""The river-routing model kind: a flow field along a river from its gauges
(specification section 7.1).

The river is split into elements about 1 km long. For each element and time
step it gives flow, cross-section area, mean water velocity (flow over area,
the speed water and anything in it travel at) and volume.

Between two main gauges, flow is a blend of the two gauges' records, each
shifted by the wave's travel time to the element, so a change at the upstream
gauge moves downstream at wave speed and both gauges are matched exactly.
Past now, the gauges' records are carried on by river forecasts (the model
runner does this before calling build).

Wave speed starts as 1.5 x water velocity, the wide-channel estimate. The
nightly calibration (calibration.py) gives each segment two corrections: a
wave scale on that travel time, and a water factor on the water velocity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .channel import ChannelShape, between

WAVE_TO_WATER = 1.5  # a flood wave's speed over the water's, 3/2 in a wide channel with Chezy friction (kinematic wave, c = dQ/dA; e.g. Chow 1959, ref:chow1959); refitted nightly per segment


@dataclass(frozen=True)
class Element:
    start: float  # chainage, m
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2


@dataclass(frozen=True)
class GaugeRecord:
    chainage: float  # m
    shape: ChannelShape
    flow: Sequence[float | None]  # on the model's time grid, m3/s


@dataclass
class FlowField:
    elements: list[Element]
    t0: int
    step: int
    flow: list[list[float]]  # [step][element], m3/s
    area: list[list[float]]  # m2
    velocity: list[list[float]]  # m/s

    @property
    def steps(self) -> int:
        return len(self.flow)

    def volume(self, i: int, e: int) -> float:
        return self.area[i][e] * self.elements[e].length


def elements_for(length: float, target: float = 1000.0) -> list[Element]:
    n = max(1, round(length / target))
    dx = length / n
    return [Element(k * dx, (k + 1) * dx) for k in range(n)]


def filled(series: Sequence[float | None]) -> list[float]:
    """Fill gaps by holding the last value (and the first value backwards)."""
    out: list[float | None] = list(series)
    first = next((v for v in out if v is not None), None)
    if first is None:
        raise ValueError("a gauge has no values in the period")
    last = first
    for i, v in enumerate(out):
        if v is None:
            out[i] = last
        else:
            last = v
    return out  # type: ignore[return-value]


def at(series: Sequence[float], x: float) -> float:
    """Value at fractional index x, linear between samples, held beyond the ends."""
    if x <= 0:
        return series[0]
    if x >= len(series) - 1:
        return series[-1]
    i = int(x)
    f = x - i
    return series[i] * (1 - f) + series[i + 1] * f


def build(
    length: float,
    gauges: Sequence[GaugeRecord],
    t0: int,
    step: int,
    element_m: float = 1000.0,
    wave_scale: Sequence[float] | None = None,
    water_factor: Sequence[float] | None = None,
) -> FlowField:
    """Flow field for elements from chainage 0 to `length`, between gauges
    ordered upstream to downstream. Elements above the first gauge or below the
    last take that gauge's record. `wave_scale` and `water_factor` hold one
    calibrated correction per segment between consecutive gauges (default 1)."""
    gs = sorted(gauges, key=lambda g: g.chainage)
    ws = list(wave_scale) if wave_scale else [1.0] * max(1, len(gs) - 1)
    wf = list(water_factor) if water_factor else [1.0] * max(1, len(gs) - 1)
    flows = [filled(g.flow) for g in gs]
    n = len(flows[0])
    els = elements_for(length, element_m)

    # Which pair of gauges each element lies between, and how far along.
    place = []
    for e in els:
        k = max(0, min(len(gs) - 2, sum(1 for g in gs[1:-1] if g.chainage <= e.mid)))
        up, down = gs[k], gs[min(k + 1, len(gs) - 1)]
        span = down.chainage - up.chainage
        f = 0.0 if span <= 0 else min(1.0, max(0.0, (e.mid - up.chainage) / span))
        place.append((k, f, between(up.shape, down.shape, f)))

    # Wave travel time from each upstream gauge to each element: the sum over the
    # elements on the way of length / (1.5 x water velocity), at that segment's
    # typical flow.
    typical = [sorted(q)[len(q) // 2] for q in flows]
    travel_to = [0.0] * len(els)
    seg_total: dict[int, float] = {}
    for idx, (k, f, shape) in enumerate(place):
        q = typical[k] * (1 - f) + typical[min(k + 1, len(gs) - 1)] * f
        c = WAVE_TO_WATER * shape.velocity(q) * wf[k]
        prev = travel_to[idx - 1] if idx > 0 and place[idx - 1][0] == k else 0.0
        travel_to[idx] = prev + els[idx].length / c * ws[k]
        seg_total[k] = travel_to[idx]

    flow = [[0.0] * len(els) for _ in range(n)]
    area = [[0.0] * len(els) for _ in range(n)]
    vel = [[0.0] * len(els) for _ in range(n)]
    for e, (k, f, shape) in enumerate(place):
        up, down = flows[k], flows[min(k + 1, len(gs) - 1)]
        t_up = travel_to[e] / step
        t_down = (seg_total.get(k, 0.0) - travel_to[e]) / step
        for i in range(n):
            q = (1 - f) * at(up, i - t_up) + f * at(down, i + t_down)
            q = max(q, 1e-3)
            a = shape.area(q) / wf[k]  # faster water through the same flow: a smaller effective area
            flow[i][e], area[i][e], vel[i][e] = q, a, q / a
    return FlowField(els, t0, step, flow, area, vel)
