"""A gauge's channel shape against flow, from its channel measurements
(specification sections 7.1 and 9.1).

Fits cross-section area and top width as power laws of flow. Depth is area
over width; mean water velocity is flow over area: the speed water, and any
pollutant in it, travels at. Recent measurements only, because channels change.

Where the measurements show two regimes (a channel that spills onto its
floodplain, a riffle that drowns, a tidal reach whose area hardly changes at
low flow), area and width are each a hinged power law: one exponent below a
break flow, another above it, joined there (continuous). The hinged fit is
kept only when it beats the single law by more than 10 in the Bayesian
information criterion, "very strong" evidence (Kass and Raftery 1995,
ref:kassraftery1995), on the pooled log residuals of area and width. Empirical
(FDMR): judged on held-out measurements at every gauge, it cut the error of
the last two years' velocities and widths from 0.273 to 0.260 overall (log
RMSE, 30 gauges); of the 14 hinged, velocity was better at 9 and width at 7
(tools/validation/channel_hinge/results.md, 27 Nov 2026).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import fdmr_py
import numpy as np

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
    # A hinged shape: above `q_break` (m3/s; 0 for none) the exponents are
    # area_b2 and width_b2, the curves continuous at the break.
    q_break: float = 0.0
    area_b2: float = 0.0
    width_b2: float = 0.0

    def area(self, q: float) -> float:
        if self.q_break > 0 and q > self.q_break:
            return self.area_a * self.q_break**self.area_b * (q / self.q_break) ** self.area_b2
        return self.area_a * q**self.area_b

    def width(self, q: float) -> float:
        if self.q_break > 0 and q > self.q_break:
            return self.width_a * self.q_break**self.width_b * (q / self.q_break) ** self.width_b2
        return self.width_a * q**self.width_b

    def depth(self, q: float) -> float:
        return self.area(q) / self.width(q)

    def velocity(self, q: float) -> float:
        return q / self.area(q)


HINGE_MIN_SIDE = 5  # measurements needed on each side of a break (FDMR's choice)
HINGE_MIN_BIC = 10.0  # "very strong" evidence for the hinged fit (Kass and Raftery 1995, ref:kassraftery1995)


def _hinge(x: np.ndarray, la: np.ndarray, lw: np.ndarray):
    """The best break (log flow) for hinged lines through log area and log
    width against log flow: (pooled sum of squares, break, area coefficients,
    width coefficients), each [intercept, slope below, change of slope above];
    None if no break is allowed. Area must grow with flow on both sides, width
    must not shrink above."""
    best = None
    for xb in np.unique(x):
        if (x <= xb).sum() < HINGE_MIN_SIDE or (x > xb).sum() < HINGE_MIN_SIDE:
            continue
        d = np.stack([np.ones_like(x), x, np.clip(x - xb, 0, None)], 1)
        ca, *_ = np.linalg.lstsq(d, la, rcond=None)
        cw, *_ = np.linalg.lstsq(d, lw, rcond=None)
        if ca[1] <= 0 or ca[1] + ca[2] <= 0 or cw[1] + cw[2] < 0:
            continue
        sse = float(((d @ ca - la) ** 2).sum() + ((d @ cw - lw) ** 2).sum())
        if best is None or sse < best[0]:
            best = (sse, float(xb), ca, cw)
    return best


def fit(ms: Sequence[ChannelMeasurement], since: int | None = None, min_n: int = 8) -> ChannelShape | None:
    """Fit from measurements taken at or after `since` that have flow, area and
    width: a power law, or a hinged one where the measurements strongly
    support two regimes (module docstring)."""
    use = [m for m in ms if (since is None or m.t >= since) and m.area and m.width and m.flow > 0]
    if len(use) < min_n:
        return None
    a = fdmr_py.fit_power_law([m.flow for m in use], [m.area for m in use])
    w = fdmr_py.fit_power_law([m.flow for m in use], [m.width for m in use])
    if a is None or w is None:
        return None
    single = ChannelShape(a[0], a[1], w[0], w[1], a[2], w[2], len(use))
    x = np.log([m.flow for m in use])
    la, lw = np.log([m.area for m in use]), np.log([m.width for m in use])
    h = _hinge(x, la, lw)
    if h is None:
        return single
    sse1 = float(((math.log(a[0]) + a[1] * x - la) ** 2).sum() + ((math.log(w[0]) + w[1] * x - lw) ** 2).sum())
    n2 = 2 * len(use)

    def bic(sse: float, k: int) -> float:
        return n2 * math.log(max(sse, 1e-12) / n2) + k * math.log(n2)

    if bic(sse1, 4) - bic(h[0], 7) <= HINGE_MIN_BIC:
        return single
    _, xb, ca, cw = h
    d = np.stack([np.ones_like(x), x, np.clip(x - xb, 0, None)], 1)

    def r2(c: np.ndarray, y: np.ndarray) -> float:
        return 1 - float(((d @ c - y) ** 2).sum()) / float(((y - y.mean()) ** 2).sum())

    return ChannelShape(math.exp(ca[0]), float(ca[1]), math.exp(cw[0]), float(cw[1]), r2(ca, la), r2(cw, lw), len(use),
                        q_break=math.exp(xb), area_b2=float(ca[1] + ca[2]), width_b2=float(cw[1] + cw[2]))


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


@dataclass(frozen=True)
class Blend:
    """A shape between two gauges' where either is hinged: its log area and log
    width at each flow interpolated between theirs (for two plain power laws
    the same as interpolating their coefficients, as `between` does)."""

    up: "ChannelShape | Blend"
    down: "ChannelShape | Blend"
    f: float

    def area(self, q: float) -> float:
        return math.exp(math.log(self.up.area(q)) * (1 - self.f) + math.log(self.down.area(q)) * self.f)

    def width(self, q: float) -> float:
        return math.exp(math.log(self.up.width(q)) * (1 - self.f) + math.log(self.down.width(q)) * self.f)

    def depth(self, q: float) -> float:
        return self.area(q) / self.width(q)

    def velocity(self, q: float) -> float:
        return q / self.area(q)


def between(up: "ChannelShape | Blend", down: "ChannelShape | Blend", f: float) -> "ChannelShape | Blend":
    """The shape a fraction `f` (0 = up, 1 = down) of the way between two gauges:
    coefficients interpolated in log space, exponents linearly; where either is
    hinged, log area and log width interpolated at each flow (`Blend`)."""
    if not (isinstance(up, ChannelShape) and isinstance(down, ChannelShape)) or up.q_break > 0 or down.q_break > 0:
        return up if f <= 0 else down if f >= 1 else Blend(up, down, f)
    lerp = lambda x, y: x + f * (y - x)  # noqa: E731
    glerp = lambda x, y: math.exp(lerp(math.log(x), math.log(y)))  # noqa: E731
    return ChannelShape(
        glerp(up.area_a, down.area_a), lerp(up.area_b, down.area_b),
        glerp(up.width_a, down.width_a), lerp(up.width_b, down.width_b),
        min(up.area_r2, down.area_r2), min(up.width_r2, down.width_r2), min(up.n, down.n),
    )
