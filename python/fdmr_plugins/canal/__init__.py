"""The canal-network kind (issue #5, #72; specification 7.4; maths.md
canal-network): an export canal as a chain of short plain channels in one
network reach, from the export where its water leaves a network to as far
as that export alone describes its water.

Its flow at each moment is the network's own export there, the same all
along it (water does not pile up in a canal held full by its checks); its
speed is that flow over the canal's sourced cross-section; its water quality
is the intake's, carried down at the water's own travel time (plug flow) and
otherwise unchanged (ASSUMED, published with the reach).

Here is only the maths: a canal's line from its config, its channels and
their sections, and when the water at each place left the intake. Fetching
the lines is the adapters' (fdmr_adapters.nhd, .geojson); reading the
network's run and writing the reach is the runner's (fdmr_runner.canals).
"""

from __future__ import annotations

import math
from typing import Callable

import fdmr_geo
import numpy as np

FT = 0.3048
INCH = 0.0254
REACH = "canals"
KIND = "canal-network"
SEGMENT_M = 1000.0  # the longest channel a canal is cut into, so quality can change along it

# What the reach assumes and does not model, published with it (index.json
# `assumptions`, shown on assumptions.html; #72).
ASSUMED = (
    "carried down at the water's travel time and otherwise unchanged to the canal's end (over 100 km for the "
    "longest): no heating or cooling, mixing along the canal, settling, growth or decay, and nothing added or "
    "taken off on the way"
)


def metres(a, b) -> float:
    """The distance between two points (lon, lat), m, on the plane about their mean latitude."""
    k = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * fdmr_geo.M_PER_DEG * k, (a[1] - b[1]) * fdmr_geo.M_PER_DEG)


def length(line) -> float:
    return sum(metres(a, b) for a, b in zip(line, line[1:]))


def cut_at(line, m: float) -> list[tuple[float, float]]:
    """The line's first `m` metres."""
    out, done = [line[0]], 0.0
    for a, b in zip(line, line[1:]):
        d = metres(a, b)
        if done + d >= m:
            f = (m - done) / d if d > 0 else 0.0
            out.append((round(a[0] + (b[0] - a[0]) * f, 6), round(a[1] + (b[1] - a[1]) * f, 6)))
            return out
        out.append(b)
        done += d
    return out


Walk = Callable[[tuple[float, float], tuple[float, float]], list[tuple[float, float]]]


def canal_line(canal: dict, walk: Walk) -> tuple[list[tuple[float, float]], float | None]:
    """The canal's line from its config, and how far along it its `via` is (m,
    or None): `walk(a, b)` gives the line along the canal's pieces from a to
    b. From `start` (by `via`) to `end`, cut at `length_km`, or `past_via_km`
    beyond `via`."""
    via_m = None
    if "via" in canal:
        head = walk(canal["start"], canal["via"])
        tail = walk(canal["via"], canal["end"])
        if "past_via_km" in canal:
            tail = cut_at(tail, canal["past_via_km"] * 1000)
        line, via_m = head + tail[1:], length(head)
    else:
        line = walk(canal["start"], canal["end"])
    if "length_km" in canal:
        line = cut_at(line, canal["length_km"] * 1000)
    return line, via_m


# --- Cross-sections and channels -------------------------------------------------


def section_shape(sec: dict, f: float = 0.5) -> tuple[float, float]:
    """(area, m2; water-surface width, m) of a section, `f` of the way along it."""
    if "diameter_in" in sec:
        d = sec["diameter_in"]
        d = d[0] + (d[1] - d[0]) * f if isinstance(d, list) else d
        d *= INCH
        return math.pi * d * d / 4, d
    b, y, z = sec["bottom_ft"] * FT, sec["depth_ft"] * FT, sec["side"]
    return (b + z * y) * y, b + 2 * z * y


def section_ends(canal: dict, total_m: float, via_m: float | None = None) -> list[float]:
    """Where each section ends, m from the canal's start."""
    out = []
    for s in canal["section"]:
        if "to_km" in s:
            out.append(min(s["to_km"] * 1000, total_m))
        elif s.get("to_via"):
            out.append(min(via_m if via_m is not None else total_m, total_m))
        else:
            out.append(total_m)
    out[-1] = total_m
    return out


def channels(canal: dict, line, via_m: float | None = None, most: float = SEGMENT_M) -> list[dict]:
    """The canal cut into channels of at most `most` m, breaking where its
    section changes: each {coords, length_m, area_m2, width_m}."""
    cum = np.concatenate([[0.0], np.cumsum([metres(a, b) for a, b in zip(line, line[1:])])])
    total = float(cum[-1])
    ends = section_ends(canal, total, via_m)
    out, a = [], 0.0
    for sec, b in zip(canal["section"], ends):
        if b <= a:
            continue
        n = max(1, math.ceil((b - a) / most - 1e-9))
        for k in range(n):
            x0, x1 = a + (b - a) * k / n, a + (b - a) * (k + 1) / n
            area, width = section_shape(sec, ((x0 + x1) / 2 - a) / (b - a))
            out.append({"coords": _between(line, cum, x0, x1), "length_m": x1 - x0, "area_m2": area, "width_m": width})
        a = b
    return out


def _between(line, cum, x0: float, x1: float) -> list[list[float]]:
    def at(x):
        i = int(np.clip(np.searchsorted(cum, x) - 1, 0, len(line) - 2))
        f = (x - cum[i]) / (cum[i + 1] - cum[i]) if cum[i + 1] > cum[i] else 0.0
        return [round(line[i][0] + (line[i + 1][0] - line[i][0]) * f, 6), round(line[i][1] + (line[i + 1][1] - line[i][1]) * f, 6)]

    return [at(x0), *[list(line[i]) for i in range(len(line)) if x0 < cum[i] < x1], at(x1)]


def _channels_of(cfg: dict, lines: dict) -> list[list[dict]]:
    return [channels(c, lines["canals"][c["id"]]["coords"], lines["canals"][c["id"]].get("via_m")) for c in cfg["canal"]]


def geometry(cfg: dict, lines: dict) -> tuple[dict, list[list[int]], np.ndarray]:
    """The reach's network geometry (docs/format.md), each canal's channel
    indices, and every channel's area (m2). `inlets` names, for each canal,
    the network export feeding it and the junction its water enters at."""
    elements, nodes, glines, exports, inlets, members, areas = [], [], [], [], [], [], []
    for c, chs in zip(cfg["canal"], _channels_of(cfg, lines)):
        first = len(nodes) + 1
        nodes.append({"id": first, "lon": chs[0]["coords"][0][0], "lat": chs[0]["coords"][0][1]})
        idx = []
        for k, ch in enumerate(chs):
            nid = first + k + 1
            nodes.append({"id": nid, "lon": ch["coords"][-1][0], "lat": ch["coords"][-1][1]})
            idx.append(len(elements))
            elements.append({"id": len(elements) + 1, "up": nid - 1, "down": nid, "start_m": 0.0,
                             "end_m": round(ch["length_m"], 1), "width_m": round(ch["width_m"], 1)})
            glines.append(ch["coords"])
            areas.append(ch["area_m2"])
        members.append(idx)
        exports.append({"name": f"{c['id']}_end", "label": f"{c['name']}: {c['end_label']}", "node": nodes[-1]["id"]})
        inlets.append({"export": c["export"], "node": first, "canal": c["name"]})
    geo = {"reach": REACH, "dims": 1, "network": True, "elements": elements, "nodes": nodes, "lines": glines,
           "exports": exports, "inlets": inlets}
    return geo, members, np.array(areas)


# --- Flow and water quality ------------------------------------------------------


def departures(t: np.ndarray, q: np.ndarray, volume_to: np.ndarray) -> np.ndarray:
    """When the water at each place left the intake: for each time `t[k]` and
    place `volume_to[s]` m3 of canal from the intake, the time `t'` at which
    the flow `q` (m3/s, at `t`) since then has filled it. Water already in the
    canal at the record's start counts as leaving at the start. [k, s]."""
    dt = np.diff(t, prepend=t[0])
    v = np.cumsum(np.clip(q, 0, None) * dt)  # m3 pumped since the start, by t
    v = v + np.arange(len(v)) * 1e-9  # strictly rising, for interpolation
    target = v[:, None] - volume_to[None, :]
    return np.where(target <= v[0], t[0], np.interp(target, v, t))


def filled(x: np.ndarray) -> np.ndarray:
    """NaNs filled from the nearest value before (or, at the start, after)."""
    x = np.asarray(x, dtype=float).copy()
    ok = np.isfinite(x)
    if not ok.any():
        return x
    idx = np.where(ok, np.arange(len(x)), 0)
    np.maximum.accumulate(idx, out=idx)
    x = x[idx]
    first = np.argmax(ok)
    x[:first] = x[first]
    return x


def carried(t: np.ndarray, q: np.ndarray, intake: dict[str, np.ndarray], area: np.ndarray,
            lengths: np.ndarray) -> dict[str, np.ndarray]:
    """One canal's water quality, each field [time, channel]: the intake's
    value (`intake[field]` at `t`; gaps filled from the nearest value before)
    when the water at each channel's middle left it, the canal's channels
    (`area` m2, `lengths` m, intake first) filled by the flow `q` (m3/s)."""
    vol = np.cumsum(area * lengths)
    left = departures(t, q, vol - area * lengths / 2)
    return {f: np.interp(left, t, filled(v)) for f, v in intake.items()}
