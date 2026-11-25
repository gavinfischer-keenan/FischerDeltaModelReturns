"""Nightly river calibration from the river's own experiments (specification 9.1).

Two corrections per segment between gauges, kept apart because the wave
outruns the water (specification 7.1):

- wave scale: how much longer or shorter flow changes take to arrive than the
  model's channel-shape estimate (1.5 x water velocity). Measured from release
  changes arriving at the next gauge (wave.measure_events).
- water factor: how much faster or slower the water moves than the gauge-site
  channel shapes say. Measured from natural tracer signals, such as a change in
  temperature or conductance, passing two gauges.

Each is one number per segment applied to the physically based relation, so
it keeps its dependence on flow and is robust with only a handful of events.
Skill scores compare predicted and observed travel times, before and after.
This module works on plain series; the model runner fetches and logs.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import median, quantiles

import fdmr_py

from .channel import ChannelShape, between
from .model import WAVE_TO_WATER

DAY = 86_400
SCALE_LIMITS = (0.3, 3.0)  # a correction outside this says the data is wrong, not the river
MAX_SPREAD = 0.4  # the middle half of the events must lie within this fraction of their median


@dataclass(frozen=True)
class Station:
    chainage: float
    shape: ChannelShape


def segment_of(stations: Sequence[Station], x: float) -> tuple[int, float]:
    """The segment a chainage lies in (index of its upstream station) and how far along it, 0..1."""
    k = max(0, min(len(stations) - 2, sum(1 for s in stations[1:-1] if s.chainage <= x)))
    span = stations[k + 1].chainage - stations[k].chainage
    return k, (0.0 if span <= 0 else min(1.0, max(0.0, (x - stations[k].chainage) / span)))


def water_travel(
    stations: Sequence[Station], x0: float, x1: float, q: float, factors: Sequence[float] | None = None, pieces: int = 20
) -> float:
    """Time (s) for water to travel from chainage x0 to x1 at flow q, through the
    channel shapes between stations, each segment's velocity times its factor.
    Each segment's part is integrated on its own, so the factor changes exactly
    at the station."""
    cuts = sorted({x0, x1, *(s.chainage for s in stations if x0 < s.chainage < x1)})
    total = 0.0
    for a, b in zip(cuts, cuts[1:]):
        dx = (b - a) / pieces
        for j in range(pieces):
            k, f = segment_of(stations, a + (j + 0.5) * dx)
            v = between(stations[k].shape, stations[k + 1].shape, f).velocity(q)
            total += dx / (v * (factors[k] if factors else 1.0))
    return total


def wave_travel(stations: Sequence[Station], k: int, q: float, factors: Sequence[float] | None = None) -> float:
    """The model's wave travel time (s) down segment k before any wave correction."""
    return water_travel(stations, stations[k].chainage, stations[k + 1].chainage, q, factors) / WAVE_TO_WATER


@dataclass(frozen=True)
class TracerEvent:
    t: int  # start of the window, s since 1970
    flow: float  # median flow at the upstream gauge over the window, m3/s
    lag: float  # observed travel time, s
    r: float  # correlation at the peak


def running_mean(x: Sequence[float | None], w: int) -> list[float | None]:
    """Centred running mean over w samples; None where fewer than 80% are present."""
    out: list[float | None] = [None] * len(x)
    h = w // 2
    s, c = 0.0, 0
    # A plain sliding sum over the present values.
    vals = [(v if v is not None else 0.0, v is not None) for v in x]
    for i in range(min(len(x), w)):
        s += vals[i][0]
        c += vals[i][1]
    for i in range(h, len(x) - (w - h)):
        if c >= 0.8 * w:
            out[i] = s / c
        lo, hi = i - h, i - h + w
        s += vals[hi][0] - vals[lo][0]
        c += vals[hi][1] - vals[lo][1]
    return out


def tracer_events(
    upstream: Sequence[float | None],
    downstream: Sequence[float | None],
    flow: Sequence[float | None],
    t0: int,
    step: int,
    predicted: Callable[[float], float],
    *,
    window_s: int = 4 * DAY,
    smooth_s: int = DAY,
    diff_s: int = 6 * 3600,
    min_r: float = 0.85,
    min_swing: float = 0.0,
) -> list[TracerEvent]:
    """Travel times of tracer signals from one gauge to another.

    The daily cycle is removed with a one-day running mean, then each series
    becomes its change over `diff_s`, so slow drifts do not dominate. In each
    window (not overlapping, so each signal counts once) the lag is the peak of
    the cross-correlation, searched up to twice the model's prediction. A lag
    counts only with a strong match (`min_r`), a real signal (`min_swing` in
    the smoothed upstream series) and a value well inside the search range: a
    weather change that warms the whole river at once shows as a lag near zero
    and is dropped. Weather swings that repeat every few days can still match
    at the wrong lag, which is why `scale` also asks the events to agree.
    """
    su, sd = running_mean(upstream, smooth_s // step), running_mean(downstream, smooth_s // step)
    d = diff_s // step

    def change(s: list[float | None]) -> list[float | None]:
        return [None if i < d or s[i] is None or s[i - d] is None else s[i] - s[i - d] for i in range(len(s))]

    du, dd = change(su), change(sd)
    win = window_s // step
    events: list[TracerEvent] = []
    for s0 in range(0, len(du) - win, win):
        qs = [q for q in flow[s0 : s0 + win] if q is not None]
        if len(qs) < 0.8 * win:
            continue
        q = median(qs)
        p = predicted(q)
        max_lag = int(2 * p / step)
        end = s0 + win + max_lag
        if end > len(du):
            break
        a, b = du[s0:end], dd[s0:end]
        if any(v is None for v in a) or any(v is None for v in b):
            continue
        swing = [v for v in su[s0 : s0 + win] if v is not None]
        if not swing or max(swing) - min(swing) < min_swing:
            continue
        peak = fdmr_py.cross_correlation_peak(a, b, float(step), max_lag)
        if peak is None:
            continue
        lag, r = peak
        if r >= min_r and 0.3 * p <= lag <= 1.9 * p:
            events.append(TracerEvent(t=t0 + s0 * step, flow=q, lag=lag, r=r))
    return events


def spread(values: Sequence[float]) -> float | None:
    """Interquartile range over the median: how far the events disagree."""
    if len(values) < 3:
        return None
    q1, _, q3 = quantiles(values, n=4, method="inclusive")
    m = median(values)
    return (q3 - q1) / m if m > 0 else None


def scale(observed_over_predicted: Sequence[float], max_spread: float = MAX_SPREAD) -> float | None:
    """The median correction, clamped. None without at least three events, or
    when they disagree too much to say anything (`max_spread`)."""
    s = spread(observed_over_predicted)
    if s is None or s > max_spread:
        return None
    return min(SCALE_LIMITS[1], max(SCALE_LIMITS[0], median(observed_over_predicted)))


def skill(pairs: Sequence[tuple[float, float]]) -> dict:
    """Predicted against observed travel times (s): count, mean absolute error and bias, in hours."""
    if not pairs:
        return {"n": 0}
    err = [(p - o) / 3600 for p, o in pairs]
    return {"n": len(err), "mae_h": round(sum(abs(e) for e in err) / len(err), 2), "bias_h": round(sum(err) / len(err), 2)}


def blend(old: float | None, new: float | None, keep: float = 0.7) -> float | None:
    """Move a parameter part of the way to tonight's estimate, so one bad night cannot swing it."""
    if new is None:
        return old
    if old is None:
        return new
    return keep * old + (1 - keep) * new
