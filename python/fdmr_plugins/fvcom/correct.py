"""Making a 2D field carry a given net outflow (specification 7.4).

Where a mesh meets a channel network, the two must agree on how much water
leaves on net, or parcels pile up or thin out at the join. A tidal model's net
flow there can be set by how it treats its own boundary rather than by the
river: a model whose upstream edge is an open tidal boundary can draw water
inland on net where the river pushes it out (one region's case is recorded in
its bay.toml).

The fix adds a potential flow that carries the difference from the join out
to the open sea. On the mesh it solves

    div(H grad phi) = 0,   u = -grad(phi)

with a source of 1 m3/s spread along the join's edges (by their cross-
sections), phi = 0 on the open-sea edges and no flow through land. Weighting
by the depth H sends the flow mostly through the deep channels, as water
does. The result is the velocity per m3/s at every node; the user scales it
each hour (a region's runner: by the difference between the network's flow
across the join and the field's own at that hour, so both carry the same).
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


def water_distances(c: Coarse, sources: Sequence[int]) -> np.ndarray:
    """Distance (m) from each source node to every node, travelling along the
    mesh's edges: through water, never across land. Shape (sources, nodes)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra

    e = np.concatenate([c.tri[:, [0, 1]], c.tri[:, [1, 2]], c.tri[:, [2, 0]]])
    e = np.unique(np.sort(e, axis=1), axis=0)
    w = np.hypot(c.x[e[:, 0]] - c.x[e[:, 1]], c.y[e[:, 0]] - c.y[e[:, 1]])
    n = len(c.x)
    g = coo_matrix((np.concatenate([w, w]), (np.concatenate([e[:, 0], e[:, 1]]), np.concatenate([e[:, 1], e[:, 0]]))), shape=(n, n)).tocsr()
    return dijkstra(g, indices=list(sources))


def spread(dist: np.ndarray, misfit: np.ndarray, near_m: float = 1000.0) -> np.ndarray:
    """A correction at every node from misfits (measured less modelled) at
    stations: inverse-distance-squared weights by distance through the water
    (`water_distances`), so each station speaks for the water nearest it and
    a station across land counts for little. `near_m` softens the weight
    within a station's own reach. A station whose misfit is NaN is left out.
    Shape: (nodes,)."""
    ok = np.isfinite(misfit)
    if not ok.any():
        return np.zeros(dist.shape[1])
    w = 1.0 / (dist[ok] + near_m) ** 2  # zero where a node is not reached
    s = w.sum(axis=0)
    return np.where(s > 0, (w * misfit[ok][:, None]).sum(axis=0) / np.where(s > 0, s, 1.0), 0.0)


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
    (Godin 1972, ref:godin1972, *The Analysis of Tides*, University of Toronto Press)."""
    m = np.asarray(x, dtype=float)
    for h in (24, 24, 25):
        m = running_mean(m, int(round(h * per_hour)))
    return m


def match_crossings(coarse, join_edges: list[list[int]], open_edges: list[list[int]], cxy: np.ndarray, data: dict,
                    ts: np.ndarray, td: np.ndarray, Q: np.ndarray) -> dict:
    """Every hour's currents with flows added so that the mesh's flow through
    the join by each crossing is the network's flow in that channel there
    (moved here from the runner's Bay step, issue #58; specification 7.4).

    `join_edges`: the mesh's edges at the join; each counts toward the
    nearest crossing in `cxy` (mesh x, y). Each crossing's added flow runs
    from its edges to the open sea (`unit_outflow`); as they touch a little at
    shared nodes, each hour's amounts are solved together. `data` maps each
    hour in `ts` to its fields {u, v, level}; `td`, `Q`: the network's flow
    at each crossing (m3/s, Delta to Bay positive), interpolated to the hours.

    Returns {data (corrected), seg (each edge's crossing), active (the
    crossings that have edges), noaa, target, after (flows by crossing,
    m3/s), added_speed (m/s, the largest current added), compare (a function
    of two flow series giving their net and tidal statistics)}."""
    from .flux import edge_flux, edge_normals

    e = join_edges
    en = edge_normals(coarse, e)
    mid = np.array([[(coarse.x[a] + coarse.x[b]) / 2, (coarse.y[a] + coarse.y[b]) / 2] for a, b in e])
    seg = np.array([int(np.argmin(np.hypot(*(cxy - m).T))) for m in mid])
    active = sorted(set(seg.tolist()))
    unit = {s: unit_outflow(coarse, [e[i] for i in np.flatnonzero(seg == s)], open_edges) for s in active}
    data = dict(data)
    target = np.stack([np.interp(ts, td, Q[:, s]) for s in active], axis=1)  # Delta to Bay positive

    def by_crossing(flux_out: np.ndarray) -> np.ndarray:
        return np.array([-flux_out[seg == s].sum() for s in active])

    noaa = np.zeros((len(ts), len(active)))
    added_speed = 0.0
    for i, tt in enumerate(ts):
        d = dict(data[tt])
        lev = d["level"]
        noaa[i] = by_crossing(edge_flux(coarse, en, d["u"], d["v"], lev))
        f = np.stack([by_crossing(edge_flux(coarse, en, unit[s][0], unit[s][1], lev)) for s in active], axis=1)
        alpha = np.linalg.solve(f, target[i] - noaa[i])
        du = sum(alpha[k] * unit[s][0] for k, s in enumerate(active))
        dv = sum(alpha[k] * unit[s][1] for k, s in enumerate(active))
        added_speed = max(added_speed, float(np.hypot(du, dv).max()))
        d["u"] = (d["u"] + du).astype(np.float32)
        d["v"] = (d["v"] + dv).astype(np.float32)
        data[tt] = d
    after = np.array([by_crossing(edge_flux(coarse, en, data[tt]["u"], data[tt]["v"], data[tt]["level"])) for tt in ts])
    # Away from the record's ends, where the tidal filter holds its edge values.
    inner = slice(72, len(ts) - 72) if len(ts) > 200 else slice(None)

    def compare(a: np.ndarray, b: np.ndarray) -> dict:
        na, nb = tidal_mean(a, 1.0), tidal_mean(b, 1.0)
        ta, tb = (a - na)[inner], (b - nb)[inner]
        big = (np.abs(a) > 0.1 * max(float(np.abs(b).max()), 1.0))[inner]
        opposite = float(np.mean(np.sign(a[inner][big]) != np.sign(b[inner][big]))) if big.any() else 0.0
        return {"noaa_net": round(float(na[inner].mean()), 1), "dsm2_net": round(float(nb[inner].mean()), 1),
                "noaa_tidal": round(float(ta.std()), 1), "dsm2_tidal": round(float(tb.std()), 1),
                "tidal_correlation": round(float(np.corrcoef(ta, tb)[0, 1]), 3) if ta.std() > 0 and tb.std() > 0 else None,
                "opposite_share": round(opposite, 3)}

    return {"data": data, "seg": seg, "active": active, "noaa": noaa, "target": target, "after": after,
            "added_speed": added_speed, "compare": compare}
