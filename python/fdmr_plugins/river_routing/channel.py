"""A gauge's channel shape against flow, from its channel measurements
(specification sections 7.1 and 9.1).

Fits cross-section area and top width as power laws of flow. Depth is area
over width; mean water velocity is flow over area: the speed water, and any
pollutant in it, travels at. Recent measurements only, because channels change.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import fdmr_py

from fdmr_store import ChannelMeasurement


@dataclass(frozen=True)
class ChannelShape:
    area_a: float
    area_b: float
    width_a: float
    width_b: float
    area_r2: float
    width_r2: float
    n: int

    def area(self, q: float) -> float:
        return self.area_a * q**self.area_b

    def width(self, q: float) -> float:
        return self.width_a * q**self.width_b

    def depth(self, q: float) -> float:
        return self.area(q) / self.width(q)

    def velocity(self, q: float) -> float:
        return q / self.area(q)


def fit(ms: Sequence[ChannelMeasurement], since: int | None = None, min_n: int = 8) -> ChannelShape | None:
    """Fit from measurements taken at or after `since` that have flow, area and width."""
    use = [m for m in ms if (since is None or m.t >= since) and m.area and m.width and m.flow > 0]
    if len(use) < min_n:
        return None
    a = fdmr_py.fit_power_law([m.flow for m in use], [m.area for m in use])
    w = fdmr_py.fit_power_law([m.flow for m in use], [m.width for m in use])
    if a is None or w is None:
        return None
    return ChannelShape(a[0], a[1], w[0], w[1], a[2], w[2], len(use))


def wide_channel(width_m: float, slope: float, chezy: float | None = None, manning_n: float | None = None) -> ChannelShape:
    """A channel's shape where no field measurements are published: a wide
    channel of fixed width W (its main channel), water-surface slope S and
    roughness, whose depth follows from flow by uniform flow,

        Chezy:    Q = C W d^(3/2) S^(1/2)   so  A = W d = W^(1/3) (Q / (C S^(1/2)))^(2/3)
        Manning:  Q = (1/n) W d^(5/3) S^(1/2)  so  A = W^(2/5) (Q n / S^(1/2))^(3/5)

    (wide channel: hydraulic radius taken as depth; Chow 1959, ref:chow1959,
    chapters 5 and 6). The width is held: valid while the river stays in its
    main channel, below bankfull; flow over the floodplains is not
    represented. r2 and n are 0: nothing was fitted."""
    if (chezy is None) == (manning_n is None):
        raise ValueError("give a Chezy coefficient or a Manning's n, not both")
    s = math.sqrt(slope)
    if chezy is not None:
        a, b = width_m ** (1 / 3) * (chezy * s) ** (-2 / 3), 2 / 3
    else:
        a, b = width_m ** 0.4 * (manning_n / s) ** 0.6, 0.6
    return ChannelShape(a, b, width_m, 0.0, 0.0, 0.0, 0)


def between(up: ChannelShape, down: ChannelShape, f: float) -> ChannelShape:
    """The shape a fraction `f` (0 = up, 1 = down) of the way between two gauges:
    coefficients interpolated in log space, exponents linearly."""
    lerp = lambda x, y: x + f * (y - x)  # noqa: E731
    glerp = lambda x, y: math.exp(lerp(math.log(x), math.log(y)))  # noqa: E731
    return ChannelShape(
        glerp(up.area_a, down.area_a), lerp(up.area_b, down.area_b),
        glerp(up.width_a, down.width_a), lerp(up.width_b, down.width_b),
        min(up.area_r2, down.area_r2), min(up.width_r2, down.width_r2), min(up.n, down.n),
    )
