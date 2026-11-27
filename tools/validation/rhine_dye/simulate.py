"""Bimmen -> downstream: the measured Bimmen curve sent down a reach by the
engine's physics (#29: parabolic shear profile, lateral mixing 0.23 d u*,
reflected at the banks) plus transient storage in groyne fields (dead-zone
ratio beta = storage / flowing volume; a parcel in the flow enters storage at
rate a (1/s), leaves at a / beta, returning at the bank). Prints the arrival
curve's moments and peak for each exchange rate a, to compare with the
measured downstream curve."""
import json
import sys

import numpy as np

REENTER = "anywhere"
C = (0.011 * 472.5 * 0.23) ** 0.5
rng = np.random.default_rng(7)


def curve(pts):
    pts = sorted(pts)
    ts, ps = [], []
    for t, p in pts:
        if ts and t - ts[-1] < 0.02:
            ps[-1] = max(ps[-1], p)
        else:
            ts.append(t)
            ps.append(max(p, 0.0))
    return np.array(ts), np.array(ps)


def moments(g, c):
    m0 = np.trapezoid(c, g)
    mean = np.trapezoid(g * c, g) / m0
    var = np.trapezoid((g - mean) ** 2 * c, g) / m0
    sk = np.trapezoid((g - mean) ** 3 * c, g) / m0 / var**1.5
    return m0, mean, var**0.5, sk, float(c.max()), float(g[c.argmax()])


def run(t_in, p_in, L, U, W, d, ustar, beta, a, n=40_000, dt=60.0):
    # release times drawn from the Bimmen curve (flux-weighted: concentration x constant flow)
    g = np.arange(t_in[0], t_in[-1], 0.001)
    w = np.interp(g, t_in, p_in)
    t0 = rng.choice(g, size=n, p=w / w.sum()) * 86400
    x = np.zeros(n)
    eta = rng.uniform(-1, 1, n)
    stored = np.zeros(n, bool)
    if beta > 0 and a > 0:  # start in equilibrium between flow and storage
        stored = rng.uniform(size=n) < beta / (1 + beta)
    eps = 0.23 * d * ustar
    arrive = np.full(n, np.nan)
    t = t0.min()
    while np.isnan(arrive).any() and t < 13 * 86400:
        live = (t >= t0) & np.isnan(arrive)
        flow = live & ~stored
        x[flow] += U * (1 + C * (1 / 3 - eta[flow] ** 2)) * dt
        eta[flow] += 2 / W * np.sqrt(2 * eps * dt) * rng.standard_normal(flow.sum())
        eta = np.where(eta > 1, 2 - eta, np.where(eta < -1, -2 - eta, eta))
        if beta > 0 and a > 0:
            go_in = flow & (rng.uniform(size=n) < 1 - np.exp(-a * dt))
            come_out = live & stored & (rng.uniform(size=n) < 1 - np.exp(-a / beta * dt))
            stored = (stored | go_in) & ~come_out
            eta[come_out] = rng.uniform(-1, 1, come_out.sum()) if REENTER == "anywhere" else np.sign(eta[come_out] + 1e-9) * 0.98
        crossed = live & (x >= L)
        arrive[crossed] = t
        t += dt
    days = arrive[np.isfinite(arrive)] / 86400
    h, e = np.histogram(days, bins=np.arange(5, 13, 0.02))
    return (e[:-1] + e[1:]) / 2, h.astype(float)


if __name__ == "__main__":
    d = json.load(open(sys.argv[1]))
    beta = float(sys.argv[2])
    L, U, W, depth, ustar = (float(v) for v in sys.argv[3:8])
    bim = curve([(t, p) for t, p in d["squares"] if t < 8.4])
    down = curve([(t, p) for t, p in d["crosses"] if t > 6.7])
    mb = moments(np.arange(bim[0][0], bim[0][-1], 0.002), np.interp(np.arange(bim[0][0], bim[0][-1], 0.002), *bim))
    md = moments(np.arange(down[0][0], down[0][-1], 0.002), np.interp(np.arange(down[0][0], down[0][-1], 0.002), *down))
    print(f"measured downstream: centroid {md[1]:.3f} d, sd {md[2]:.3f}, skew {md[3]:+.2f}, peak at {md[5]:.2f} d, peak/area {md[4]/md[0]:.2f}")
    for a in [float(v) for v in (sys.argv[8].split(",") if len(sys.argv) > 8 else ["0", "1e-5", "2e-5", "3e-5"])]:
        g, h = run(*bim, L, U, W, depth, ustar, beta if a > 0 else 0.0, a)
        m = moments(g, h)
        meas = np.interp(g, down[0], down[1], left=0.0, right=0.0)
        rmse = float(np.sqrt(np.mean((h / np.trapezoid(h, g) - meas / np.trapezoid(meas, g)) ** 2)))
        print(f"a={a:7.0e}: centroid {m[1]:.3f} d, sd {m[2]:.3f}, skew {m[3]:+.2f}, peak at {m[5]:.2f} d, peak/area {m[4]/m[0]:.2f}, shape RMSE {rmse:.3f} /d")
