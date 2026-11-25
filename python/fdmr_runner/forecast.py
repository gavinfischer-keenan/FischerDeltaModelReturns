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
