"""Water temperature along a river (specification 7.5): the release's water
carried downstream, warming and cooling with the weather.

The heat budget at the water's surface, W/m2, in the standard forms of Chapra
(1997, ref:chapra1997, Surface Water-Quality Modeling, Lecture 30, "Heat budgets"):

- sunlight absorbed: (1 - 0.06) Rs, about 6 % reflected;
- long-wave from the sky (Brunt): s (Ta + 273.15)^4 (0.6 + 0.031 sqrt(e_air)) (1 - 0.03),
  times (1 + 0.17 C^2) for cloud C (as in QUAL2K: Chapra, Pelletier and Tao
  2008, ref:chapra2008qual2k, QUAL2K documentation, after TVA 1972, ref:tva1972);
- long-wave from the water: 0.97 s (Tw + 273.15)^4;
- conduction to the air: c1 f(U) (Tw - Ta), c1 = 0.47 mmHg/degC (Bowen);
- evaporation: f(U) (e_s(Tw) - e_air),
  f(U) = 19.0 + 0.95 U^2 cal/cm2/d/mmHg, U the wind 7 m up (m/s);

with s the Stefan-Boltzmann constant, vapour pressures in mmHg,
e(T) = 4.596 exp(17.27 T / (237.3 + T)), e_air = e(dew point). Sunlight at
the surface is FAO-56's clear-sky radiation (Allen et al. 1998, ref:allen1998fao56, Crop
Evapotranspiration, eqs. 21-37: extraterrestrial radiation from the sun's
angle, times 0.75 + 2e-5 z) reduced for cloud by (1 - 0.65 C^2) (as in Chapra
1997, after TVA 1972, ref:tva1972). The water heats or cools at H / (rho c_p d), d its depth.

Along the river the temperature moves with the water (upwind, in as many
sub-steps as the flow needs), and inflowing water mixes in. The model runner
then corrects the result toward measured temperatures (`correct`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

SIGMA = 5.670374e-8  # W/m2/K4
RHO_CP = 1000.0 * 4186.0  # J/m3/K
CAL_CM2_D = 41_840.0 / 86_400.0  # 1 cal/cm2/day in W/m2
ALBEDO = 0.06  # share of sunlight the water reflects, about 6 % (Chapra 1997, Lecture 30, ref:chapra1997)
EMISSIVITY = 0.97  # water's long-wave emissivity (Chapra 1997, Lecture 30, ref:chapra1997)
BRUNT_A, BRUNT_B, LONGWAVE_REFLECTED = 0.6, 0.031, 0.03
BOWEN = 0.47  # mmHg/degC
CLOUD_LONGWAVE = 0.17  # sky long-wave times (1 + 0.17 C^2) for cloud C (QUAL2K, ref:chapra2008qual2k, after TVA 1972, ref:tva1972)
WIND_7M = (7.0 / 10.0) ** (1 / 7)  # wind measured 10 m up, at 7 m (one-seventh power law)
GSC = 1367.0  # W/m2, solar constant


def vapour_mmhg(t: np.ndarray) -> np.ndarray:
    return 4.596 * np.exp(17.27 * t / (237.3 + t))


def sunlight(t: np.ndarray, lat: float, lon: float, elev_m: float, cloud: np.ndarray) -> np.ndarray:
    """Short-wave radiation reaching the water, W/m2, at UTC times `t` (s):
    FAO-56's clear-sky value for the hour, less cloud."""
    t = np.asarray(t, dtype=float)
    day = np.array([_doy(x) for x in t], dtype=float)  # day of the year
    b = 2 * math.pi * (day - 81) / 364
    sc = 0.1645 * np.sin(2 * b) - 0.1255 * np.cos(b) - 0.025 * np.sin(b)  # equation of time, h (FAO-56 eq. 32)
    hour = (t % 86_400) / 3600.0  # UTC
    solar_time = hour + lon / 15.0 + sc  # lon east positive
    w = math.pi / 12 * (solar_time - 12)
    dr = 1 + 0.033 * np.cos(2 * math.pi * day / 365)
    decl = 0.409 * np.sin(2 * math.pi * day / 365 - 1.39)
    phi = math.radians(lat)
    cos_z = np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.cos(w)
    ra = GSC * dr * np.clip(cos_z, 0, None)
    rso = (0.75 + 2e-5 * elev_m) * ra
    return rso * (1 - 0.65 * np.clip(cloud, 0, 1) ** 2)


def _doy(t: float) -> int:
    import time

    return time.gmtime(t).tm_yday


def net_flux(tw: np.ndarray, ta: np.ndarray, td: np.ndarray, wind_10m: np.ndarray, rs: np.ndarray,
             cloud: np.ndarray | float = 0.0) -> np.ndarray:
    """Net heat into the water through its surface, W/m2 (positive warms)."""
    e_air = vapour_mmhg(td)
    f_u = (19.0 + 0.95 * (wind_10m * WIND_7M) ** 2) * CAL_CM2_D  # W/m2 per mmHg
    sky = (SIGMA * (ta + 273.15) ** 4 * (BRUNT_A + BRUNT_B * np.sqrt(np.clip(e_air, 0, None))) * (1 - LONGWAVE_REFLECTED)
           * (1 + CLOUD_LONGWAVE * np.clip(cloud, 0, 1) ** 2))
    back = EMISSIVITY * SIGMA * (tw + 273.15) ** 4
    conduction = BOWEN * f_u * (tw - ta)
    evaporation = f_u * (vapour_mmhg(tw) - e_air)
    return (1 - ALBEDO) * rs + sky - back - conduction - evaporation


def run(
    lengths: Sequence[float],
    velocity: np.ndarray,  # [step, element], m/s
    depth: np.ndarray,  # [step, element], m
    flow: np.ndarray,  # [step, element], m3/s
    area: np.ndarray,  # [step, element], m2
    step: float,
    boundary: np.ndarray,  # [step], degC: the water entering the top
    initial: np.ndarray,  # [element], degC
    weather: dict[str, np.ndarray],  # air_temperature, dew_point, wind_speed, cloud_cover, sunlight: [step, element]
    inflows: Sequence[tuple[int, np.ndarray, np.ndarray]] = (),  # (element, flow m3/s [step], temperature degC [step])
) -> np.ndarray:
    """Water temperature, degC, for every published step and element."""
    dx = np.asarray(lengths, dtype=float)
    steps, n = velocity.shape
    out = np.empty((steps, n))
    t = np.array(initial, dtype=float)
    out[0] = t
    for i in range(1, steps):
        u = np.clip(velocity[i - 1], 0, None)
        sub = max(1, int(math.ceil(float(np.max(u * step / dx)) / 0.9)))
        dt = step / sub
        h = np.clip(depth[i - 1], 0.05, None)
        for _ in range(sub):
            up = np.concatenate([[boundary[i - 1]], t[:-1]])
            t = t - u * dt / dx * (t - up)
            t = t + dt * net_flux(t, weather["air_temperature"][i - 1], weather["dew_point"][i - 1],
                                  weather["wind_speed"][i - 1], weather["sunlight"][i - 1],
                                  weather["cloud_cover"][i - 1] if "cloud_cover" in weather else 0.0) / (RHO_CP * h)
            for e, q, temp in inflows:
                if np.isfinite(temp[i - 1]) and q[i - 1] > 0:
                    vol = area[i - 1][e] * dx[e]
                    t[e] += dt * q[i - 1] * (temp[i - 1] - t[e]) / max(vol, 1.0)
        out[i] = t
    return out


def correct(model: np.ndarray, x_el: np.ndarray, t: np.ndarray, stations: Sequence[tuple[float, np.ndarray]],
            hold_s: float = 86_400.0) -> np.ndarray:
    """The model moved to meet measured temperatures: at each station (chainage
    m, measured degC per step, NaN where none) its misfit, linear in chainage
    between stations and held beyond the outermost. Past a station's last
    reading its misfit fades over `hold_s` (e-folding)."""
    xs, cols = [], []
    for x, meas in sorted(stations, key=lambda s: s[0]):
        e = int(np.clip(np.searchsorted(x_el, x), 0, len(x_el) - 1))
        d = meas - model[:, e]
        ok = np.isfinite(d)
        if not ok.any():
            continue  # a station with no reading in the window corrects nothing
        last = np.where(ok)[0][-1]
        fade = np.where(t > t[last], np.exp(-(t - t[last]) / hold_s), 1.0)
        xs.append(x)
        cols.append(np.interp(t, t[ok], d[ok]) * fade)
    if not xs:
        return model.copy()
    xs = np.array(xs)
    misfit = np.stack(cols, axis=1)
    out = model.copy()
    for i in range(len(t)):
        out[i] += np.interp(x_el, xs, misfit[i])
    return out


def reach_temperature(field, shapes, step: int, top, weather: dict, inflows: list, stations: list, names: list[str],
                      times, i_now: int):
    """A river reach's water temperature for every step and element (moved
    here from the publisher, issue #57): the release's temperature `top`
    carried down by `run` through the reach's flow field (its elements'
    `shapes` give their widths), started from the stations' first readings,
    then corrected toward `stations` ((chainage m, readings per step)) by
    `correct`. Returns (temperature [step, element], checks): each station's
    misfit before the correction, and its miss when the correction is made
    from the other stations alone (left out), over the past steps."""
    import numpy as np

    els = field.elements
    x_el = np.array([e.mid for e in els])
    lengths = [e.length for e in els]
    q = np.array(field.flow, dtype=float)
    a = np.array(field.area, dtype=float)
    u = np.array(field.velocity, dtype=float)
    width = np.stack([shapes[j].width(np.maximum(q[:, j], 1e-3)) for j in range(len(els))], axis=1)
    depth = a / np.clip(width, 1.0, None)
    n = q.shape[0]
    initial = np.full(len(els), top[0])
    at0 = [(x, m[0]) for x, m in stations if np.isfinite(m[0])]
    if at0:
        xs, vs = zip(*sorted(at0))
        initial = np.interp(x_el, xs, vs)

    model = run(lengths, u, depth, q, a, step, top, initial, weather, inflows)
    out = correct(model, x_el, times, stations)

    checks = []
    past = np.arange(n) <= i_now
    for k, (x, meas) in enumerate(stations):
        e = int(np.clip(np.searchsorted(x_el, x), 0, len(els) - 1))
        ok = past & np.isfinite(meas)
        if not ok.any():
            continue
        loo = correct(model, x_el, times, [s for j, s in enumerate(stations) if j != k])
        d0, d1 = meas[ok] - model[ok, e], meas[ok] - loo[ok, e]
        checks.append({"station": names[k], "km": round(x / 1000, 1), "readings": int(ok.sum()),
                       "mean_misfit": round(float(d0.mean()), 2), "rms_misfit": round(float(np.sqrt((d0 ** 2).mean())), 2),
                       "left_out_mean": round(float(d1.mean()), 2), "left_out_rms": round(float(np.sqrt((d1 ** 2).mean())), 2)})
    return out, checks
