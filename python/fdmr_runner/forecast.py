"""Carrying a gauge's record past now with a flow forecast (specification 8.2).

Up to now the record is what the gauge measured. After now it follows the
forecast, shifted so it starts from the measured value: the gap between the two
at now is closed over the first day. Past the forecast's end, or where a gauge
has no forecast (such as a dam release), the last value is held.
"""

from __future__ import annotations

from collections.abc import Sequence


def on_grid(
    samples: Sequence[tuple[int, float | None]], t0: int, step: int, n: int, max_gap_s: int | None = None
) -> list[float | None]:
    """Samples resampled onto a regular grid, linear between them; None outside
    their span, and across any gap longer than `max_gap_s`."""
    pts = [(t, v) for t, v in samples if v is not None]
    out: list[float | None] = [None] * n
    j = 0
    for i in range(n):
        t = t0 + i * step
        while j + 1 < len(pts) and pts[j + 1][0] <= t:
            j += 1
        if not pts or t < pts[0][0] or t > pts[-1][0]:
            continue
        t1, v1 = pts[j]
        if t1 == t or j + 1 >= len(pts):
            out[i] = v1
        else:
            t2, v2 = pts[j + 1]
            if max_gap_s is not None and t2 - t1 > max_gap_s:
                continue
            out[i] = v1 + (v2 - v1) * (t - t1) / (t2 - t1)
    return out


def extend(
    measured: Sequence[float | None],
    forecast: Sequence[float | None] | None,
    i_now: int,
    n: int,
    blend_steps: int,
) -> tuple[list[float | None], int | None]:
    """The record on a grid of `n` steps: measured up to `i_now`, forecast after.

    Returns the series and the last step the forecast covers (None if it covers
    nothing after now). Measured gaps before now are left for the model to fill.
    """
    out: list[float | None] = list(measured[: i_now + 1]) + [None] * (n - i_now - 1)
    m_now = next((v for v in reversed(out[: i_now + 1]) if v is not None), None)
    if m_now is None:
        return out, None
    last, end = m_now, None
    if forecast is not None:
        f_now = forecast[i_now] if i_now < len(forecast) else None
        if f_now is None:
            f_now = next((v for v in forecast[i_now + 1 :] if v is not None), None)
        offset = m_now - f_now if f_now is not None else 0.0
        for i in range(i_now + 1, min(n, len(forecast))):
            f = forecast[i]
            if f is None:
                continue
            w = max(0.0, 1.0 - (i - i_now) / blend_steps) if blend_steps > 0 else 0.0
            last = max(f + offset * w, 0.0)
            out[i], end = last, i
    for i in range(i_now + 1, n):  # hold past the forecast, and in its gaps
        if out[i] is None:
            out[i] = last
        else:
            last = out[i]
    return out, end


def fit_tide(
    observed: Sequence[float | None], predicted: Sequence[float | None], step: int, max_shift_steps: int = 8
) -> tuple[float, float, float] | None:
    """How a gauge's water level follows a nearby station's tide predictions:
    (shift in steps, scale, offset), so that observed(i) ~ scale * predicted(i -
    shift) + offset. The shift comes from the peak of the cross-correlation
    (either way), then scale and offset by least squares. River flow damps
    and delays the tide, and the two stations are not the same place, so all
    three are fitted to the recent record. None if too little overlaps."""
    import fdmr_py

    both = [i for i in range(min(len(observed), len(predicted))) if observed[i] is not None and predicted[i] is not None]
    if len(both) < 96:
        return None
    lo, hi = both[0], both[-1] + 1
    obs = [observed[i] for i in range(lo, hi)]
    pre = [predicted[i] for i in range(lo, hi)]
    if any(v is None for v in obs) or any(v is None for v in pre):
        # Bridge short gaps so the correlation sees an even series.
        obs, pre = _bridge(obs), _bridge(pre)
    late = fdmr_py.cross_correlation_peak(pre, obs, float(step), max_shift_steps)  # observed after predicted
    early = fdmr_py.cross_correlation_peak(obs, pre, float(step), max_shift_steps)
    if late is None or early is None:
        return None
    shift = late[0] / step if late[1] >= early[1] else -early[0] / step
    sp = shifted(pre, shift)
    pairs = [(p, o) for p, o in zip(sp, obs) if p is not None and o is not None]
    n = len(pairs)
    mp, mo = sum(p for p, _ in pairs) / n, sum(o for _, o in pairs) / n
    var = sum((p - mp) ** 2 for p, _ in pairs)
    scale = sum((p - mp) * (o - mo) for p, o in pairs) / var if var > 0 else 1.0
    return shift, scale, mo - scale * mp


def shifted(series: Sequence[float | None], shift: float) -> list[float | None]:
    """series(i - shift), linear between samples; None where it would fall outside."""
    out: list[float | None] = []
    for i in range(len(series)):
        x = i - shift
        j = int(x // 1)
        f = x - j
        if j < 0 or j + 1 >= len(series) or series[j] is None or series[j + 1] is None:
            out.append(series[j] if 0 <= j < len(series) and f == 0 else None)
        else:
            out.append(series[j] * (1 - f) + series[j + 1] * f)
    return out


def _bridge(s: Sequence[float | None]) -> list[float]:
    out = list(s)
    last = next((v for v in out if v is not None), 0.0)
    for i, v in enumerate(out):
        if v is None:
            out[i] = last
        else:
            last = v
    return out  # type: ignore[return-value]
