"""Carrying a 2D field past the end of its forecast (specification 7.3).

At every node, each of u, v and the water level is fitted, by least squares
over a long record of past fields, to

    f(t) = a0 + b q(t) + sum_k [ c_k cos(w_k t) + s_k sin(w_k t) ]

where q is the river outflow into the estuary (m3/s) and w_k the speeds of the
tidal constituents below. The tides carry the water back and forth; the b q
term is the net drift that grows with outflow, which in an estuary's upper
bays matters as much as the tide. Wind is not in it.

Constituent speeds are the standard astronomical ones (degrees per mean solar
hour; Schureman 1958, ref:schureman1958, *Manual of Harmonic Analysis and Prediction of Tides*,
US Coast and Geodetic Survey Special Publication 98). The 18.6-year nodal
modulation of each constituent is left out: fitted over the year just past and
used for the two weeks after it, it changes by well under 1%. The record may be
sampled every 2 hours (Nyquist period 4 hours): every constituent here is
slower than that; the sixth-diurnal tides are left out for that reason.
Seasonal constituents (Sa, Ssa) are left out too: over one year they cannot be
told apart from the seasons of the outflow itself.

Past the forecast's end the fitted field takes over, plus the recent
non-tidal offset (`anomaly`): the fit's mean miss over the last days before the
end, carried on unchanged. A constant mean for the year cannot follow slow
changes of sea level: in San Francisco Bay NOAA's mean level rose from about
0.1 m in winter 2025-26 to 0.4 m in autumn 2026 (checked 25 Nov 2026), and
without the offset a 14-day hindcast was 0.3 m low throughout. It is then
joined to the forecast's last hour by an offset that fades over `blend_h` hours.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Degrees per mean solar hour (Schureman 1958).
SPEEDS = {
    "M2": 28.9841042, "S2": 30.0000000, "N2": 28.4397295, "K2": 30.0821373, "2N2": 27.8953548,
    "K1": 15.0410686, "O1": 13.9430356, "P1": 14.9589314, "Q1": 13.3986609,
    "M4": 57.9682084, "MS4": 58.9841042, "MN4": 57.4238337, "MK3": 44.0251729,
    "Mf": 1.0980331, "MSf": 1.0158958,
}
EPOCH = 946_684_800  # 2000-01-01T00:00Z: phases are reckoned from here
Q_SCALE = 1000.0  # outflow in thousands of m3/s, so the columns are of a size


def design(t: np.ndarray, q: np.ndarray, names: Sequence[str] = tuple(SPEEDS)) -> np.ndarray:
    """The least-squares design matrix: one row per time (s since 1970)."""
    h = (np.asarray(t, dtype=float) - EPOCH) / 3600.0
    cols = [np.ones_like(h), np.asarray(q, dtype=float) / Q_SCALE]
    for n in names:
        w = np.radians(SPEEDS[n]) * h
        cols += [np.cos(w), np.sin(w)]
    return np.column_stack(cols)


@dataclass
class Fit:
    names: tuple[str, ...]
    coef: dict[str, np.ndarray]  # field -> (columns, nodes)
    skill: dict[str, np.ndarray]  # field -> share of each node's variance explained, 0 to 1
    t0: int
    t1: int

    def predict(self, t: np.ndarray, q: np.ndarray) -> dict[str, np.ndarray]:
        x = design(t, q, self.names)
        return {k: (x @ c).astype(np.float32) for k, c in self.coef.items()}

    def save(self, path: Path) -> None:
        np.savez(path, names=np.array(self.names), t0=self.t0, t1=self.t1,
                 **{f"coef_{k}": v for k, v in self.coef.items()}, **{f"skill_{k}": v for k, v in self.skill.items()})

    @staticmethod
    def load(path: Path) -> Fit:
        d = np.load(path)
        return Fit(tuple(str(n) for n in d["names"]),
                   {k[5:]: d[k] for k in d.files if k.startswith("coef_")},
                   {k[6:]: d[k] for k in d.files if k.startswith("skill_")}, int(d["t0"]), int(d["t1"]))


def fit(t: np.ndarray, q: np.ndarray, fields: Mapping[str, np.ndarray], names: Sequence[str] = tuple(SPEEDS)) -> Fit:
    """Fit each field (times x nodes) at every node. Nodes with a missing value
    at some time are fitted on the times they have."""
    x = design(t, q, names)
    coef, skill = {}, {}
    for k, y in fields.items():
        y = np.asarray(y, dtype=float)
        ok = np.isfinite(y).all(axis=1)
        c, *_ = np.linalg.lstsq(x[ok], y[ok], rcond=None)
        resid = y[ok] - x[ok] @ c
        var = y[ok].var(axis=0)
        coef[k] = c
        skill[k] = np.where(var > 0, 1 - resid.var(axis=0) / np.where(var > 0, var, 1), 1.0)
    return Fit(tuple(names), coef, skill, int(np.min(t)), int(np.max(t)))


def join(forecast_last: Mapping[str, np.ndarray], predicted_at_last: Mapping[str, np.ndarray],
         predicted: Mapping[str, np.ndarray], hours_after: np.ndarray, blend_h: float = 6.0) -> dict[str, np.ndarray]:
    """The fitted field after the forecast's end, shifted to start from the
    forecast's last hour, the shift fading linearly over `blend_h` hours."""
    w = np.clip(1.0 - np.asarray(hours_after, dtype=float) / blend_h, 0.0, 1.0)[:, None]
    return {k: (predicted[k] + w * (forecast_last[k] - predicted_at_last[k])[None, :]).astype(np.float32) for k in predicted}


def anomaly(f: Fit, t: np.ndarray, q: np.ndarray, observed: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The fit's mean miss over a recent stretch (times t, fields times x nodes):
    the slow, non-tidal part the fit leaves out. Over several tidal cycles the
    tidal part of the miss averages away."""
    p = f.predict(t, q)
    return {k: np.nanmean(np.asarray(observed[k], dtype=float) - p[k], axis=0).astype(np.float32) for k in f.coef if k in observed}
