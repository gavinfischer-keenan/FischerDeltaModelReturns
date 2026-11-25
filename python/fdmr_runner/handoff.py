"""Where a channel network meets a mesh reach (specification 7.4): the Delta's
DSM2 channels and the Bay's mesh at Chipps Island.

Both models cover Suisun Bay; one must track it. Every network channel whose
middle lies on the mesh belongs to the mesh reach: the network still carries
its flow but tracks nothing in it. Where a network channel meets one of those
at a junction, a parcel arriving there to go the mesh's way is passed to the
mesh, at the junction's place on the mesh; a parcel crossing the mesh's
handoff edge comes back into the network at the junction nearest where it
crossed, into the network's own channel there. Each parcel belongs to one
reach at a time, so mass is conserved whatever the two models' flows do.

The join is written into index.json (docs/format.md, handoffs) as

    {"from": <network>, "to": <mesh>, "owned": [channel index, ...],
     "junctions": [{"node": junction index, "id": junction id,
                    "element": network channel index, "fraction": 0 or 1,
                    "x": m, "y": m}, ...]}

where `element` and `fraction` are the network channel a parcel coming back
enters (fraction 0: its upstream end), and x, y the junction's place on the
mesh in the mesh's own coordinates (inside it: a junction off the mesh, such
as one just upstream of the cut, takes the nearest triangle's centre).
"""

from __future__ import annotations

import numpy as np

from fdmr_plugins.fvcom.mesh import Locator


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


def network_to_mesh(net_id: str, net: dict, mesh_id: str, mesh: dict) -> dict:
    """The join between a network reach's geometry and a mesh reach's (see above)."""
    lon, lat = np.asarray(mesh["lon"]), np.asarray(mesh["lat"])
    x, y = np.asarray(mesh["x"]), np.asarray(mesh["y"])
    tri = np.asarray(mesh["triangles"])
    loc = Locator(lon, lat, tri)
    mids = np.array([_midpoint(line) for line in net["lines"]])
    owned = np.flatnonzero(loc.find(mids[:, 0], mids[:, 1]) >= 0)
    owned_set = set(owned.tolist())
    node_ix = {n["id"]: i for i, n in enumerate(net["nodes"])}
    elements = net["elements"]
    cx, cy = lon[tri].mean(axis=1), lat[tri].mean(axis=1)
    junctions = []
    for i, n in enumerate(net["nodes"]):
        touching = [(c, e) for c, e in enumerate(elements) if node_ix.get(e["up"]) == i or node_ix.get(e["down"]) == i]
        if not any(c in owned_set for c, _ in touching):
            continue
        ours = [(c, e) for c, e in touching if c not in owned_set]
        if not ours:
            continue
        t = int(loc.find(np.array([n["lon"]]), np.array([n["lat"]]))[0])
        if t >= 0:
            a, b, c3 = tri[t]
            # Barycentric weights in lon/lat carry over to the mesh's own x, y.
            d = (lat[b] - lat[c3]) * (lon[a] - lon[c3]) + (lon[c3] - lon[b]) * (lat[a] - lat[c3])
            w0 = ((lat[b] - lat[c3]) * (n["lon"] - lon[c3]) + (lon[c3] - lon[b]) * (n["lat"] - lat[c3])) / d
            w1 = ((lat[c3] - lat[a]) * (n["lon"] - lon[c3]) + (lon[a] - lon[c3]) * (n["lat"] - lat[c3])) / d
            w2 = 1 - w0 - w1
            px, py = w0 * x[a] + w1 * x[b] + w2 * x[c3], w0 * y[a] + w1 * y[b] + w2 * y[c3]
        else:
            k = np.cos(np.radians(n["lat"]))
            t = int(np.argmin(np.hypot((cx - n["lon"]) * k, cy - n["lat"])))
            px, py = float(x[tri[t]].mean()), float(y[tri[t]].mean())
        for c, e in ours:
            junctions.append({
                "node": i, "id": n["id"], "element": c, "fraction": 0 if node_ix.get(e["up"]) == i else 1,
                "x": round(float(px), 1), "y": round(float(py), 1),
            })
    return {"from": net_id, "to": mesh_id, "owned": owned.tolist(), "junctions": junctions}
