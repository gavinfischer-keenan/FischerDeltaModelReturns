"""Cutting and coarsening an FVCOM mesh for the compute engine.

NOAA's mesh is far finer than a browser needs (San Francisco Bay's: 54,120
nodes, 102,264 triangles). The coarse mesh is built from a subset of its own
nodes: shoreline nodes first, then the rest, each kept only if no kept node is
closer than its spacing. Spacing follows the size of NOAA's own triangles
there (`k` times it, within limits), so narrow straits keep several nodes
across while open water thins out. The kept nodes are triangulated, triangles
whose centre falls on land are dropped, and only the one connected body of
water is kept (creeks too narrow to keep break off and go).

Each coarse node stands for the water nearest to it: values move to the coarse
mesh as area-weighted means of NOAA's triangles (currents) and nodes (level,
temperature, salinity) in that patch.

Boundary edges of the coarse mesh are land, open sea (water leaves the system),
or a handoff to another reach, each set by the region (see `classify`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Fine:
    x: np.ndarray  # m, the file's own projection
    y: np.ndarray
    lon: np.ndarray
    lat: np.ndarray
    tri: np.ndarray  # (n, 3), 0-based
    depth: np.ndarray  # m below datum, at nodes
    node_area: np.ndarray  # m2 (FVCOM's art1)


@dataclass
class Coarse:
    fine_node: np.ndarray  # each coarse node's fine node index
    lon: np.ndarray
    lat: np.ndarray
    x: np.ndarray
    y: np.ndarray
    tri: np.ndarray  # (m, 3)
    depth: np.ndarray  # m, patch mean
    el_owner: np.ndarray  # coarse node of each fine triangle, -1 outside
    node_owner: np.ndarray  # coarse node of each fine node, -1 outside
    el_area: np.ndarray  # m2 of each fine triangle
    node_area: np.ndarray  # m2 of each fine node's control area

    def save(self, path: Path) -> None:
        np.savez_compressed(path, **self.__dict__)

    @staticmethod
    def load(path: Path) -> Coarse:
        d = np.load(path)
        return Coarse(**{k: d[k] for k in d.files})


def read_fine(path: Path) -> Fine:
    import h5py

    with h5py.File(path, "r") as f:
        lon = f["lon"][:].astype(float)
        return Fine(
            x=f["x"][:].astype(float), y=f["y"][:].astype(float),
            lon=np.where(lon > 180, lon - 360, lon), lat=f["lat"][:].astype(float),
            tri=f["nv"][:].T.astype(np.int64) - 1, depth=f["h"][:].astype(float), node_area=f["art1"][:].astype(float),
        )


def tri_area(x: np.ndarray, y: np.ndarray, t: np.ndarray) -> np.ndarray:
    return 0.5 * np.abs((x[t[:, 1]] - x[t[:, 0]]) * (y[t[:, 2]] - y[t[:, 0]]) - (x[t[:, 2]] - x[t[:, 0]]) * (y[t[:, 1]] - y[t[:, 0]]))


def edges_of(t: np.ndarray) -> np.ndarray:
    """Every triangle's three edges, each as a sorted node pair: (3n, 2)."""
    return np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)


def boundary_edges(t: np.ndarray) -> np.ndarray:
    e, n = np.unique(edges_of(t), axis=0, return_counts=True)
    return e[n == 1]


def poisson_pick(x: np.ndarray, y: np.ndarray, order: np.ndarray, spacing: np.ndarray, cell: float) -> np.ndarray:
    """Greedy: walk `order`, keeping a node unless a kept one is nearer than the
    smaller of their spacings."""
    grid: dict[tuple[int, int], list[int]] = {}
    kept = []
    for i in order:
        r = spacing[i]
        gx, gy, span = int(x[i] // cell), int(y[i] // cell), int(np.ceil(r / cell))
        clear = True
        for ix in range(gx - span, gx + span + 1):
            for iy in range(gy - span, gy + span + 1):
                for j in grid.get((ix, iy), ()):
                    if (x[j] - x[i]) ** 2 + (y[j] - y[i]) ** 2 < min(r, spacing[j]) ** 2:
                        clear = False
                        break
                if not clear:
                    break
            if not clear:
                break
        if clear:
            kept.append(i)
            grid.setdefault((gx, gy), []).append(i)
    return np.array(kept, dtype=np.int64)


class Locator:
    """Which fine triangle holds a point (or -1)."""

    def __init__(self, x: np.ndarray, y: np.ndarray, tri: np.ndarray) -> None:
        from scipy.spatial import cKDTree

        self.x, self.y, self.tri = x, y, tri
        self.tree = cKDTree(np.c_[x[tri].mean(axis=1), y[tri].mean(axis=1)])

    def find(self, px: np.ndarray, py: np.ndarray, k: int = 12) -> np.ndarray:
        _, idx = self.tree.query(np.c_[px, py], k=k)
        found = np.full(len(px), -1)
        x, y = self.x, self.y
        for j in range(idx.shape[1]):
            t = self.tri[idx[:, j]]
            x1, y1, x2, y2, x3, y3 = x[t[:, 0]], y[t[:, 0]], x[t[:, 1]], y[t[:, 1]], x[t[:, 2]], y[t[:, 2]]
            d = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
            a = ((y2 - y3) * (px - x3) + (x3 - x2) * (py - y3)) / d
            b = ((y3 - y1) * (px - x3) + (x1 - x3) * (py - y3)) / d
            hit = (found < 0) & (a >= -1e-9) & (b >= -1e-9) & (a + b <= 1 + 1e-9)
            found[hit] = idx[hit, j]
        return found


def largest_piece(tri: np.ndarray) -> np.ndarray:
    """The triangles of the largest edge-connected piece."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    e = edges_of(tri)
    owner = np.tile(np.arange(len(tri)), 3)
    key = e[:, 0] * (int(e.max()) + 1) + e[:, 1]
    o = np.argsort(key, kind="stable")
    same = key[o][1:] == key[o][:-1]
    a = coo_matrix((np.ones(same.sum()), (owner[o][:-1][same], owner[o][1:][same])), shape=(len(tri), len(tri)))
    _, label = connected_components(a, directed=False)
    return tri[label == np.bincount(label).argmax()]


def coarsen(fine: Fine, keep: np.ndarray, k: float, min_m: float, max_m: float) -> Coarse:
    """A coarse mesh of the fine triangles flagged in `keep`."""
    from scipy.spatial import Delaunay, cKDTree

    el = fine.tri[keep]
    used = np.unique(el)
    border = np.unique(boundary_edges(el))
    spacing = np.clip(k * np.sqrt(2 * fine.node_area), min_m, max_m)
    inner = np.setdiff1d(used, border)
    order = np.concatenate([border[np.argsort(spacing[border], kind="stable")], inner[np.argsort(spacing[inner], kind="stable")]])
    pick = poisson_pick(fine.x, fine.y, order, spacing, min_m)
    tri = Delaunay(np.c_[fine.x[pick], fine.y[pick]]).simplices
    loc = Locator(fine.x, fine.y, el)
    p = pick[tri]
    tri = tri[loc.find(fine.x[p].mean(axis=1), fine.y[p].mean(axis=1)) >= 0]
    tri = largest_piece(tri)
    used_c = np.unique(tri)
    remap = np.full(len(pick), -1)
    remap[used_c] = np.arange(len(used_c))
    pick, tri = pick[used_c], remap[tri]
    # Orient every triangle counter-clockwise.
    x, y = fine.x[pick], fine.y[pick]
    cw = (x[tri[:, 1]] - x[tri[:, 0]]) * (y[tri[:, 2]] - y[tri[:, 0]]) - (x[tri[:, 2]] - x[tri[:, 0]]) * (y[tri[:, 1]] - y[tri[:, 0]]) < 0
    tri[cw] = tri[cw][:, [0, 2, 1]]
    # Each fine triangle and node inside the coarse mesh belongs to its nearest coarse node.
    inside_el = np.zeros(len(fine.tri), bool)
    inside_el[np.flatnonzero(keep)] = True
    cloc = Locator(x, y, tri)
    cx, cy = fine.x[fine.tri].mean(axis=1), fine.y[fine.tri].mean(axis=1)
    inside_el &= cloc.find(cx, cy) >= 0
    near = cKDTree(np.c_[x, y])
    el_owner = np.where(inside_el, near.query(np.c_[cx, cy])[1], -1)
    node_in = np.zeros(len(fine.x), bool)
    node_in[np.unique(fine.tri[inside_el])] = True
    node_owner = np.where(node_in, near.query(np.c_[fine.x, fine.y])[1], -1)
    # Every coarse node keeps at least its own fine node, and the fine triangle
    # nearest it: a shoreline node whose surrounding triangles fall just outside
    # the coarse mesh would otherwise get no value at all.
    node_owner[pick] = np.arange(len(pick))
    empty = np.flatnonzero(np.bincount(el_owner[el_owner >= 0], minlength=len(pick)) == 0)
    if len(empty):
        kept = np.flatnonzero(keep)
        _, j = cKDTree(np.c_[cx[kept], cy[kept]]).query(np.c_[x[empty], y[empty]])
        el_owner[kept[j]] = empty
    c = Coarse(fine_node=pick, lon=fine.lon[pick], lat=fine.lat[pick], x=x, y=y, tri=tri,
               depth=np.zeros(len(pick)), el_owner=el_owner, node_owner=node_owner,
               el_area=tri_area(fine.x, fine.y, fine.tri), node_area=fine.node_area)
    c.depth = node_mean(c, fine.depth)
    return c


def node_mean(c: Coarse, values: np.ndarray) -> np.ndarray:
    """A field at fine nodes as the area-weighted mean over each coarse node's patch."""
    ok = c.node_owner >= 0
    w = np.bincount(c.node_owner[ok], weights=c.node_area[ok], minlength=len(c.x))
    s = np.bincount(c.node_owner[ok], weights=(values * c.node_area)[ok], minlength=len(c.x))
    return np.where(w > 0, s / np.where(w > 0, w, 1), np.nan)


def element_mean(c: Coarse, values: np.ndarray) -> np.ndarray:
    """A field at fine triangles (such as currents) as the area-weighted mean over each patch."""
    ok = c.el_owner >= 0
    w = np.bincount(c.el_owner[ok], weights=c.el_area[ok], minlength=len(c.x))
    s = np.bincount(c.el_owner[ok], weights=(values * c.el_area)[ok], minlength=len(c.x))
    return np.where(w > 0, s / np.where(w > 0, w, 1), np.nan)


def classify(c: Coarse, open_sea: dict, handoffs: list[dict]) -> dict[str, list[list[int]]]:
    """Boundary edges that are not land: {"open": [[a, b], ...], <handoff id>: [...]}.

    `open_sea`: an edge is open sea if both its nodes lie west of `west_of`
    (degrees) and deeper than `deeper_than_m`; the coast is shallow there.
    Each handoff: an edge is that handoff's if both nodes lie east of its
    `east_of` (degrees), that is, on the cut the reach ends at.
    """
    out: dict[str, list[list[int]]] = {"open": []}
    for a, b in boundary_edges(c.tri).tolist():
        for h in handoffs:
            if c.lon[a] > h["east_of"] and c.lon[b] > h["east_of"]:
                out.setdefault(h["id"], []).append([a, b])
                break
        else:
            if (c.lon[a] < open_sea["west_of"] and c.lon[b] < open_sea["west_of"]
                    and c.depth[a] > open_sea["deeper_than_m"] and c.depth[b] > open_sea["deeper_than_m"]):
                out["open"].append([a, b])
    return out


def geometry(c: Coarse, reach: str, edges: dict[str, list[list[int]]]) -> dict:
    """The reach's geometry file (docs/format.md, 2D)."""
    return {
        "reach": reach, "dims": 2,
        "lon": [round(float(v), 5) for v in c.lon], "lat": [round(float(v), 5) for v in c.lat],
        "x": [round(float(v), 1) for v in c.x], "y": [round(float(v), 1) for v in c.y],
        "depth_m": [round(float(v), 2) for v in c.depth],
        "triangles": c.tri.tolist(),
        "edges": edges,
    }
