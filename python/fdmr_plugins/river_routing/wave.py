"""Calibrating a segment's wave travel time from release changes (specification section 9.1).

Every change in an upstream release is a natural experiment: the change
arrives at the next gauge downstream some hours later. This finds each change,
measures its arrival lag by cross-correlating the two gauges' rates of change
around it, and fits travel time against flow as a power law, T = a·Qᵇ.

This calibrates the wave only. It must never be used as pollutant travel time:
the wave outruns the water (specification section 7.1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import fdmr_py


@dataclass(frozen=True)
class Event:
    """One release change and its measured arrival downstream."""

    t: int  # time of the change at the upstream gauge, s since 1970
    flow: float  # midway between the upstream flows before and after the change, m3/s
    rise: float  # size of the change, m3/s (negative for a cut)
    lag: float  # measured wave travel time, s


@dataclass(frozen=True)
class WaveFit:
    a: float
    b: float
    r2: float
    events: list[Event]

    def travel_time(self, q: float) -> float:
        return self.a * q**self.b


def regular(series: Sequence[tuple[int, float | None]], t0: int, step: int, n: int) -> list[float | None]:
    """Resample (t, value) pairs onto a regular grid by nearest sample within half a step."""
    out: list[float | None] = [None] * n
    for t, v in series:
        i = round((t - t0) / step)
        if 0 <= i < n and abs(t - (t0 + i * step)) <= step / 2:
            out[i] = v
    return out


def find_changes(flow: Sequence[float | None], step: int, min_change: float, min_relative: float, quiet_s: int) -> list[int]:
    """Indices where flow changes sharply: by at least `min_change` (m3/s) and
    `min_relative` of the flow, over one hour, with no other change within `quiet_s`."""
    per_hour = max(1, 3600 // step)
    found: list[int] = []
    last = -(10**9)
    for i in range(per_hour, len(flow)):
        a, b = flow[i - per_hour], flow[i]
        if a is None or b is None:
            continue
        d = b - a
        if abs(d) >= min_change and abs(d) >= min_relative * max(a, b):
            if (i - last) * step >= quiet_s:
                found.append(i)
            last = i
    return found


def measure_events(
    upstream: Sequence[float | None],
    downstream: Sequence[float | None],
    t0: int,
    step: int,
    *,
    min_change: float = 10.0,
    min_relative: float = 0.08,
    before_s: int = 6 * 3600,
    after_s: int = 36 * 3600,
    max_lag_s: int = 30 * 3600,
    min_response: float = 0.6,
    max_response: float = 1.4,
) -> list[Event]:
    """Lag of each sharp upstream change at the downstream gauge.

    An event counts only if the upstream net change is a real step and the
    downstream net change matches it within `min_response`..`max_response`."""
    events: list[Event] = []
    for i in find_changes(upstream, step, min_change, min_relative, quiet_s=after_s):
        lo, hi = i - before_s // step, i + after_s // step
        if lo < 1 or hi >= len(upstream):
            continue
        up, down = upstream[lo - 1 : hi], downstream[lo - 1 : hi]
        if any(v is None for v in up) or any(v is None for v in down):
            continue  # gaps: skip the event rather than guess
        # A real step: the net change from before to after, not just an hourly wiggle.
        rise = up[-1] - up[0]
        if abs(rise) < min_change or abs(rise) < min_relative * max(up[0], up[-1]):
            continue
        # The downstream gauge must answer with a matching step. If it does not,
        # something else moved it too, such as a storm on the tributaries.
        answer = down[-1] - down[0]
        if not (min_response <= answer / rise <= max_response):
            continue
        du = [b - a for a, b in zip(up, up[1:])]
        dd = [b - a for a, b in zip(down, down[1:])]
        lag = fdmr_py.lag_by_cross_correlation(du, dd, float(step), max_lag_s // step)
        if lag is None or lag <= 0:
            continue
        # The flow the front travels at: midway between the flows before and after.
        events.append(Event(t=t0 + i * step, flow=(up[0] + up[-1]) / 2, rise=rise, lag=lag))
    return events


def fit(events: list[Event]) -> WaveFit | None:
    if len(events) < 3:
        return None
    r = fdmr_py.fit_power_law([e.flow for e in events], [e.lag for e in events])
    if r is None:
        return None
    a, b, r2, _ = r
    return WaveFit(a=a, b=b, r2=r2, events=events)
