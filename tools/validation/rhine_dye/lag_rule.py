"""Groyne-field storage as a stream piece (issue #27): does the delay of a
cloud behind the water follow the dead-zone ratio, switched by the velocity
head?

Data: van Mazijk (1996, ref:vanmazijk1996), read from the thesis scan.
- Lag coefficients, the measured cloud's centroid travel time over the water's
  flow time, minus 1. Table 4.4.6 (releases of April 1989, September 1990 and
  June 1991) and Table 4.4.7 (the Sandoz spill of November 1986, given for two
  discharges because the flow fell while the cloud passed). Each has two flow
  times, from the Rhine Alarm Model and from SOBEK, and so two lags; their
  spread is part of the uncertainty, with the thesis's own sigma.
- Dead-zone ratio beta against the discharge at Lobith, per branch, for the
  restriction beta_i <= 0.5 (the solid lines of Figs 4.4.10, 4.4.12, 4.4.13),
  read at the gridlines (about +-0.005).
- Mean velocity head u^2/2g against the discharge, per branch (Fig. 4.4.15),
  read at the plotted points (about +-0.001 m).

The rule (section 4.4.3, p. 217): groyne-field storage delays a cloud by
about beta, but its eddies need a velocity head larger than the head lost
along a groyne field; "for very small values of the velocity head (less than
0.01 or 0.02 m) the strength of the eddy as a mixing tool will reduce". So the
piece's switch is
    g = 0 below 0.01 m, 1 above 0.02 m, linear between,
and its predicted lag is beta(Q) * g(h_v(Q)). Nothing is fitted: beta and h_v
are the branches' published geometry, and the thresholds are the thesis's.
Compared with no storage (lag 0, the model today) and with one beta per
branch whatever the flow (beta at 2,400 m3/s).

    python tools/validation/rhine_dye/lag_rule.py
"""

from __future__ import annotations

import numpy as np

# Fig. 4.4.10 / 4.4.12 / 4.4.13, solid line (beta_i <= 0.5): (Q at Lobith m3/s, beta)
BETA = {
    "Waal": [(900, 0.160), (1700, 0.185), (2100, 0.205), (2300, 0.212), (2500, 0.228), (2750, 0.252), (2950, 0.262), (3250, 0.255)],
    "Lek": [(900, 0.290), (1700, 0.300), (2100, 0.310), (2400, 0.320), (2800, 0.338), (3000, 0.340), (3250, 0.330)],
    "IJssel": [(900, 0.200), (1700, 0.208), (2300, 0.220), (2500, 0.235), (2900, 0.280), (3250, 0.280)],
}
# Fig. 4.4.15: (Q, velocity head m)
HEAD = {
    "Waal": [(900, 0.030), (1000, 0.033), (1400, 0.044), (1800, 0.052), (2300, 0.059), (2400, 0.061), (2750, 0.068), (3000, 0.071)],
    "Lek": [(900, 0.003), (1000, 0.003), (1400, 0.004), (1800, 0.008), (2300, 0.030), (2400, 0.032), (2750, 0.040), (3000, 0.043)],
    "IJssel": [(900, 0.016), (1000, 0.018), (1400, 0.025), (1800, 0.029), (2300, 0.032), (2400, 0.033), (2750, 0.037), (3000, 0.039)],
}
# Tables 4.4.6 and 4.4.7: (release, Q at Lobith, branch, reach, lag after RAM, lag after SOBEK, sigma)
LAGS = [
    ("04/1989", 2979, "Waal", "Lobith-Vuren", 0.29, 0.32, 0.16),
    ("04/1989", 2979, "Lek", "Lobith-Hagestein", 0.37, 0.21, 0.14),
    ("04/1989", 2979, "IJssel", "Lobith-Kampen", 0.07, 0.01, 0.08),
    ("09/1990", 954, "Waal", "Lobith-Vuren", 0.16, 0.06, 0.11),
    ("09/1990", 954, "Waal", "Nijmegen-Vuren", 0.11, 0.03, 0.14),
    ("09/1990", 954, "IJssel", "Lobith-Kampen", -0.02, -0.07, 0.05),
    ("06/1991", 2383, "Waal", "Bimmen-Vuren", 0.25, 0.28, 0.15),
    ("06/1991", 2383, "Waal", "Nijmegen-Vuren", 0.28, 0.33, 0.20),
    ("06/1991", 2383, "Lek", "Bimmen-Hagestein", 0.48, 0.17, 0.13),
    ("11/1986 Sandoz", 2316, "Waal", "Lobith-Vuren", 0.28, 0.30, 0.15),
    ("11/1986 Sandoz", 2316, "Lek", "Lobith-Hagestein", 0.46, 0.14, 0.12),
    ("11/1986 Sandoz", 2316, "IJssel", "Lobith-Kampen", -0.02, -0.06, 0.07),
    ("11/1986 Sandoz", 2010, "Waal", "Lobith-Vuren", 0.21, 0.24, 0.14),
    ("11/1986 Sandoz", 2010, "Lek", "Lobith-Hagestein", 0.36, -0.17, 0.10),
    ("11/1986 Sandoz", 2010, "IJssel", "Lobith-Kampen", -0.10, -0.10, 0.07),
]
H_OFF, H_ON = 0.01, 0.02  # m: van Mazijk (1996) p. 217


def at(table: list[tuple[float, float]], q: float) -> float:
    x, y = zip(*table)
    return float(np.interp(q, x, y))


def switch(h: float) -> float:
    return float(np.clip((h - H_OFF) / (H_ON - H_OFF), 0.0, 1.0))


def lag(branch: str, q: float, gated: bool = True) -> float:
    return at(BETA[branch], q) * (switch(at(HEAD[branch], q)) if gated else 1.0)


def main() -> None:
    rows = []
    print(f"{'release':15s} {'Q':>5s} {'reach':17s} {'measured (RAM/SOBEK)':>21s} {'h_v':>6s} {'beta':>5s}  {'piece':>5s} {'const':>5s}")
    for rel, q, br, reach, ram, sob, sig in LAGS:
        mid = (ram + sob) / 2
        piece, const = lag(br, q), at(BETA[br], 2400)
        rows.append((br, rel, mid, abs(ram - sob) / 2, sig, piece, const))
        print(f"{rel:15s} {q:5d} {reach:17s} {ram:+.2f}/{sob:+.2f} (mid {mid:+.2f}) {at(HEAD[br], q):6.3f} {at(BETA[br], q):5.2f}  {piece:5.2f} {const:5.2f}")
    print()
    for name, sel in (("all 15", lambda r: True), ("Waal and Lek (11)", lambda r: r[0] != "IJssel"), ("IJssel (4)", lambda r: r[0] == "IJssel")):
        s = [r for r in rows if sel(r)]
        mid = np.array([r[2] for r in s])
        unc = np.array([np.hypot(r[3], r[4]) for r in s])
        out = []
        for label, pred in (("no storage", np.zeros(len(s))), ("one beta per branch", np.array([r[6] for r in s])),
                            ("beta(Q) switched by velocity head", np.array([r[5] for r in s]))):
            err = pred - mid
            out.append(f"{label}: MAE {np.abs(err).mean():.3f}, mean {err.mean():+.3f}, within uncertainty {int((np.abs(err) <= unc).sum())}/{len(s)}")
        print(f"{name}:\n  " + "\n  ".join(out))


if __name__ == "__main__":
    main()
