"""Dead-zone storage in groyne fields, a stream piece (issue #27).

Global physics: a cloud in a river lined with groyne fields is held back by
the water trapped between the groynes, by a lag coefficient (its centroid's
travel time over the water's, less 1) of about the dead-zone ratio beta, the
groyne fields' share of the cross-section (van Mazijk 1996, ref:vanmazijk1996,
section 4.4). The eddy that exchanges the groyne field's water with the flow
needs a velocity head u^2/2g above the head lost along a groyne field: below
0.01-0.02 m "the strength of the eddy as a mixing tool will reduce" (p. 217),
and where weirs hold the water at low flow, about 0.003 m, the clouds travel at
the water's speed. So the piece's ratio is beta switched by the velocity head,
off below 0.01 m and fully on above 0.02 m, linear between. The engine holds
a share beta/(1+beta) of the parcels in storage (crates/fdmr-compute).

Place property: each reach's beta and velocity head against the discharge at
a named gauge, with their source (region.toml `groyne_fields`). A reach
without groyne fields has none, and the piece does nothing there.

Judged on every measured lag the source tabulates (15 reach-releases, 1986-
1991; tools/validation/rhine_dye/lag_rule.py): within the measurement
uncertainty in 11 of 11 on two of the three branches (none fitted), not on
the third, whose measured lags are about zero.
"""

from __future__ import annotations

import numpy as np

HEAD_OFF_M = 0.01  # m: below this velocity head the groyne fields' eddies do not exchange (van Mazijk 1996, p. 217)
HEAD_ON_M = 0.02  # m: above this they exchange fully (van Mazijk 1996, p. 217)


def switch(head_m: float) -> float:
    """How far the groyne fields' exchange is on, 0 to 1, at a velocity head."""
    return float(np.clip((head_m - HEAD_OFF_M) / (HEAD_ON_M - HEAD_OFF_M), 0.0, 1.0))


def ratio(cfg: dict, q: float) -> float:
    """The effective storage ratio at discharge q (at the place's gauge): its
    beta(q) switched by its velocity head(q), each interpolated in the place's
    tables and held beyond their ends."""
    b = np.array(cfg["beta"], dtype=float)
    h = np.array(cfg["velocity_head_m"], dtype=float)
    beta = float(np.interp(q, b[:, 0], b[:, 1]))
    head = float(np.interp(q, h[:, 0], h[:, 1]))
    return beta * switch(head)
