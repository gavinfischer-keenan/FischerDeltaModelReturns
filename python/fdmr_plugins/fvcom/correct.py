"""Making a 2D field carry a given net outflow (specification 7.4).

Where a mesh meets a channel network, the two must agree on how much water
leaves on net, or parcels pile up or thin out at the join. A tidal model's net
flow there can be set by how it treats its own boundary rather than by the
river: a model whose upstream edge is an open tidal boundary can draw water
inland on net where the river pushes it out (one region's case is recorded in
its bay.toml).

The fix keeps the field's tides and adds a steady potential flow that carries
the difference from the join out to the open sea. On the mesh it solves

    div(H grad phi) = 0,   u = -grad(phi)

with a source of 1 m3/s spread along the join's edges (by their cross-
sections), phi = 0 on the open-sea edges and no flow through land. Weighting
by the depth H sends the flow mostly through the deep channels, as water
does. The result is the velocity per m3/s at every node; each hour it is
scaled by the difference between the target net flow and the field's own,
both tidally filtered (`tidal_mean`).
Linear finite elements; the correction's flux out at the sea equals the
source, by construction, to solver precision.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .mesh import Coarse

MIN_H = 0.5  # m: shoals that dry still pass a little water


def unit_outflow(c: Coarse, join: Sequence[Sequence[int]], sea: Sequence[Sequence[int]]) -> tuple[np.ndarray, np.ndarray]:
    """The depth-averaged velocity (m/s) at every node that carries 1 m3/s from
    the join's edges out through the sea edges, seaward positive."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import spsolve

    x, y, tri = c.x, c.y, c.tri
    n = len(x)
    a, b, d = tri[:, 0], tri[:, 1], tri[:, 2]
    area2 = (x[b] - x[a]) * (y[d] - y[a]) - (x[d] - x[a]) * (y[b] - y[a])
    # Gradients of the three basis functions in each triangle (constant there).
    gx = np.stack([y[b] - y[d], y[d] - y[a], y[a] - y[b]], axis=1) / area2[:, None]
    gy = np.stack([x[d] - x[b], x[a] - x[d], x[b] - x[a]], axis=1) / area2[:, None]
    h = np.maximum(c.depth[tri].mean(axis=1), MIN_H)
    w = h * area2 / 2
    rows, cols, vals = [], [], []
    for i in range(3):
        for j in range(3):
            rows.append(tri[:, i])
            cols.append(tri[:, j])
            vals.append(w * (gx[:, i] * gx[:, j] + gy[:, i] * gy[:, j]))
    k = coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)).tocsr()
    f = np.zeros(n)
    sec = np.array([np.hypot(x[p] - x[q], y[p] - y[q]) * max((c.depth[p] + c.depth[q]) / 2, MIN_H) for p, q in join])
    for (p, q), s in zip(join, sec / sec.sum()):
        f[p] += s / 2
        f[q] += s / 2
    fixed = np.unique(np.asarray(sea).ravel())
    free = np.setdiff1d(np.arange(n), fixed)
    phi = np.zeros(n)
    phi[free] = spsolve(k[free][:, free].tocsc(), f[free])
    # Velocity per triangle, then the area-weighted mean at each node.
    ut = -(gx * phi[tri]).sum(axis=1)
    vt = -(gy * phi[tri]).sum(axis=1)
    wa = np.zeros(n)
    u = np.zeros(n)
    v = np.zeros(n)
    for i in range(3):
        np.add.at(wa, tri[:, i], area2)
        np.add.at(u, tri[:, i], ut * area2)
        np.add.at(v, tri[:, i], vt * area2)
    return (u / np.where(wa > 0, wa, 1)).astype(np.float32), (v / np.where(wa > 0, wa, 1)).astype(np.float32)


def running_mean(x: np.ndarray, w: int) -> np.ndarray:
    """Running mean over w samples. Within half a window of either end, where
    a full window does not fit, the nearest full-window value is held: a mean
    over a fragment of a tide is no mean."""
    x = np.asarray(x, dtype=float)
    w = max(1, int(w))
    if len(x) <= w:
        return np.full(len(x), x.mean() if len(x) else 0.0)
    m = np.convolve(x, np.ones(w) / w, mode="same")
    half = w // 2
    m[:half] = m[half]
    m[len(m) - half:] = m[len(m) - half - 1]
    return m


def tidal_mean(x: np.ndarray, per_hour: float) -> np.ndarray:
    """The subtidal part of a series sampled `per_hour` times an hour: Godin's
    filter, running means over 24, 24 and 25 hours in turn, which takes out the
    diurnal and semidiurnal tides far more cleanly than one long running mean
    (Godin 1972, *The Analysis of Tides*, University of Toronto Press)."""
    m = np.asarray(x, dtype=float)
    for h in (24, 24, 25):
        m = running_mean(m, int(round(h * per_hour)))
    return m
