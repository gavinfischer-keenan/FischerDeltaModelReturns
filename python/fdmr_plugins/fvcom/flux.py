"""The flow across a mesh's boundary edges (issue #58: the mesh's numerics
belong with the mesh, not in the runner's Bay step).

An edge is two boundary nodes of the coarse mesh (fdmr_plugins.fvcom.mesh).
Its outward normal, times its length, points away from the one triangle it
belongs to. The flow out through it is the depth-averaged velocity across
it times its length and the water's depth there, each the mean of the
edge's two ends: the depth is still-water depth plus the water level,
never below zero.
"""

from __future__ import annotations

import numpy as np

from .mesh import Coarse


def edge_normals(coarse: Coarse, edges: list[list[int]]) -> np.ndarray:
    """Each boundary edge's nodes and its length times its outward unit normal:
    rows (a, b, nx, ny)."""
    tri = coarse.tri
    out = []
    for a, b in edges:
        k = next(i for i, tr in enumerate(tri) if a in tr and b in tr)
        cx, cy = coarse.x[tri[k]].mean(), coarse.y[tri[k]].mean()
        dx, dy = coarse.x[b] - coarse.x[a], coarse.y[b] - coarse.y[a]
        nx, ny = dy, -dx  # length times a unit normal
        mx, my = (coarse.x[a] + coarse.x[b]) / 2, (coarse.y[a] + coarse.y[b]) / 2
        if nx * (mx - cx) + ny * (my - cy) < 0:  # point it away from the triangle: outward
            nx, ny = -nx, -ny
        out.append((a, b, nx, ny))
    return np.array(out, dtype=float).reshape(-1, 4)


def edge_flux(coarse: Coarse, en: np.ndarray, u: np.ndarray, v: np.ndarray, level: np.ndarray) -> np.ndarray:
    """The flow out of the mesh through each edge (m3/s), for one moment's
    velocities `u`, `v` (m/s) and water level (m) at the nodes."""
    a, b = en[:, 0].astype(int), en[:, 1].astype(int)
    h = np.maximum((coarse.depth[a] + level[a] + coarse.depth[b] + level[b]) / 2, 0.0)
    return ((u[a] + u[b]) / 2 * en[:, 2] + (v[a] + v[b]) / 2 * en[:, 3]) * h


def mesh_outflow(coarse: Coarse, edges: list[list[int]], hours: dict) -> tuple[np.ndarray, np.ndarray]:
    """The mesh's flow out across a set of its boundary edges (m3/s), at each
    hour held: `hours` maps a time to its fields ({u, v, level} at the nodes,
    or a file of them). The edges' flows are added in order."""
    en = edge_normals(coarse, edges)
    ts = np.array(sorted(hours))
    q = np.zeros(len(ts))
    for i, tt in enumerate(ts):
        d = np.load(hours[tt]) if not isinstance(hours[tt], dict) else hours[tt]
        q[i] = sum(edge_flux(coarse, en, d["u"], d["v"], d["level"]).tolist())
    return ts, q
