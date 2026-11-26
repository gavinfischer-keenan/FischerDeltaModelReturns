"""Where a channel network meets a mesh reach (specification 7.4), such as a
delta's channels running out into a bay that a 2D mesh covers.

The join is a line: the mesh's edge. Every piece of water belongs to one reach,
and anything crossing the line changes hands at the line.

- A network channel lying in the mesh's water from end to end is the mesh's
  (`owned`): the network still carries its flow, but draws and tracks nothing
  in it.
- A channel with one end in the mesh's water is cut where it enters (`cuts`):
  the part outside is the network's, the part inside the mesh's. A parcel or
  flow particle moving along it past the cut toward the inside passes to the
  mesh at the cut's place on the mesh; one crossing the mesh's handoff edges
  near a cut comes back into that channel at the cut, moving outward.
- Where a network channel wholly outside meets an owned channel at a junction
  inside the mesh (a channel ending right at the shore), the junction serves
  as its cut (`junctions`), as before cuts existed.

Each parcel belongs to one reach at a time, so mass is conserved whatever the
two models' flows do. The model runner then makes the mesh's flow through the
line by each cut the network's flow in that channel there, hour by hour, and
the mesh's water quality meet the network's there (fdmr_runner.bay).

The join is written into index.json (docs/format.md, handoffs) as

    {"from": <network>, "to": <mesh>, "owned": [channel index, ...],
     "cuts": [{"element": channel index, "at": 0 to 1 along the channel from
               its upstream end, "inside": "down" or "up" (which end is in
               the mesh), "x": m, "y": m, "lon": deg, "lat": deg}, ...],
     "junctions": [{"node": junction index, "id": junction id,
                    "element": network channel index, "fraction": 0 or 1,
                    "x": m, "y": m}, ...]}

where x, y are the place on the mesh in its own coordinates, just inside it.
"""

from __future__ import annotations

import numpy as np

from fdmr_plugins.fvcom.mesh import Locator

STEP_DEG = 0.0002  # sample channel lines about every 20 m to find where they enter the mesh


def _midpoint(line: list[list[float]]) -> tuple[float, float]:
    """The point halfway along a polyline (lon, lat), by length in degrees scaled for latitude."""
    pts = np.asarray(line, dtype=float)
    k = np.cos(np.radians(pts[:, 1].mean()))
    seg = np.hypot(np.diff(pts[:, 0]) * k, np.diff(pts[:, 1]))
    if seg.sum() == 0:
        return float(pts[0, 0]), float(pts[0, 1])
    s = np.concatenate([[0], np.cumsum(seg)])
    half = s[-1] / 2
    i = min(int(np.searchsorted(s, half)) - 1, len(seg) - 1)
    i = max(i, 0)
    f = (half - s[i]) / seg[i] if seg[i] > 0 else 0.0
    return float(pts[i, 0] + f * (pts[i + 1, 0] - pts[i, 0])), float(pts[i, 1] + f * (pts[i + 1, 1] - pts[i, 1]))


def sample(line: list[list[float]]) -> tuple[np.ndarray, np.ndarray]:
    """Points along a polyline about every STEP_DEG, and each one's place along
    it (0 at its first point, 1 at its last, by length)."""
    pts = np.asarray(line, dtype=float)
    k = np.cos(np.radians(pts[:, 1].mean()))
    seg = np.hypot(np.diff(pts[:, 0]) * k, np.diff(pts[:, 1]))
    total = float(seg.sum())
    if total == 0:
        return pts[:1], np.zeros(1)
    s = np.concatenate([[0], np.cumsum(seg)])
    at = np.linspace(0, total, max(2, int(total / STEP_DEG) + 1))
    lon = np.interp(at, s, pts[:, 0])
    lat = np.interp(at, s, pts[:, 1])
    return np.stack([lon, lat], axis=1), at / total


def _mesh_xy(loc: Locator, lon, lat, x, y, tri, lon0: float, lat0: float) -> tuple[float, float]:
    """A point's place in the mesh's own x, y (barycentric in lon/lat carries
    over); a point just off the mesh takes the nearest triangle's centre."""
    t = int(loc.find(np.array([lon0]), np.array([lat0]))[0])
    if t >= 0:
        a, b, c3 = tri[t]
        d = (lat[b] - lat[c3]) * (lon[a] - lon[c3]) + (lon[c3] - lon[b]) * (lat[a] - lat[c3])
        w0 = ((lat[b] - lat[c3]) * (lon0 - lon[c3]) + (lon[c3] - lon[b]) * (lat0 - lat[c3])) / d
        w1 = ((lat[c3] - lat[a]) * (lon0 - lon[c3]) + (lon[a] - lon[c3]) * (lat0 - lat[c3])) / d
        w2 = 1 - w0 - w1
        return float(w0 * x[a] + w1 * x[b] + w2 * x[c3]), float(w0 * y[a] + w1 * y[b] + w2 * y[c3])
    cx, cy = lon[tri].mean(axis=1), lat[tri].mean(axis=1)
    k = np.cos(np.radians(lat0))
    t = int(np.argmin(np.hypot((cx - lon0) * k, cy - lat0)))
    return float(x[tri[t]].mean()), float(y[tri[t]].mean())


def network_to_mesh(net_id: str, net: dict, mesh_id: str, mesh: dict) -> dict:
    """The join between a network reach's geometry and a mesh reach's (see above)."""
    lon, lat = np.asarray(mesh["lon"]), np.asarray(mesh["lat"])
    x, y = np.asarray(mesh["x"]), np.asarray(mesh["y"])
    tri = np.asarray(mesh["triangles"])
    loc = Locator(lon, lat, tri)
    owned: list[int] = []
    cuts: list[dict] = []
    for c, line in enumerate(net["lines"]):
        pts, at = sample(line)
        inside = loc.find(pts[:, 0], pts[:, 1]) >= 0
        if not inside.any():
            continue
        if inside[0] and inside[-1]:
            owned.append(c)
            continue
        if not inside[0] and not inside[-1]:
            continue  # passes over a corner of the mesh's water: left to the network
        # One end inside: cut where the line last leaves the outside, coming from the outside end.
        if inside[-1]:
            k = int(np.flatnonzero(~inside)[-1]) + 1  # first point of the inside run at the down end
            where, side = (at[k - 1] + at[k]) / 2, "down"
        else:
            k = int(np.flatnonzero(~inside)[0]) - 1  # last point of the inside run at the up end
            where, side = (at[k] + at[k + 1]) / 2, "up"
        px, py = _mesh_xy(loc, lon, lat, x, y, tri, float(pts[k, 0]), float(pts[k, 1]))
        cuts.append({"element": c, "at": round(float(where), 4), "inside": side, "x": round(px, 1), "y": round(py, 1),
                     "lon": round(float(pts[k, 0]), 5), "lat": round(float(pts[k, 1]), 5)})
    owned_set = set(owned)
    cut_set = {ct["element"] for ct in cuts}
    node_ix = {n["id"]: i for i, n in enumerate(net["nodes"])}
    elements = net["elements"]
    junctions = []
    for i, n in enumerate(net["nodes"]):
        touching = [(c, e) for c, e in enumerate(elements) if node_ix.get(e["up"]) == i or node_ix.get(e["down"]) == i]
        if not any(c in owned_set for c, _ in touching):
            continue
        # The network's own channels here that no cut already joins to the mesh.
        ours = [(c, e) for c, e in touching if c not in owned_set and c not in cut_set]
        if not ours:
            continue
        px, py = _mesh_xy(loc, lon, lat, x, y, tri, n["lon"], n["lat"])
        for c, e in ours:
            junctions.append({
                "node": i, "id": n["id"], "element": c, "fraction": 0 if node_ix.get(e["up"]) == i else 1,
                "x": round(px, 1), "y": round(py, 1),
            })
    return {"from": net_id, "to": mesh_id, "owned": owned, "cuts": cuts, "junctions": junctions}
