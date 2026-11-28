"""The export canals as a reach of their own (issue #5; specification 7.4;
regions/<id>/canals.toml).

What a network reach's pumps take does not vanish: it carries on down the
canals. Each canal is a chain of short plain channels in one network reach
(docs/format.md, network geometry), from the export where its water leaves
the network to as far as that export alone describes its water. Its flow at
each moment is the network's own published export there, the same all along
it (water does not pile up in a canal held full by its checks); its speed is
that flow over the canal's sourced cross-section; at its end the water is
carried on beyond the map. Its water quality is the intake's, carried down at
the water's own travel time. The join (index.json `handoffs`, `outlets`)
hands the network's parcels and flow particles into each canal's first
junction.

    python -m fdmr_runner.canals build <region>      # lines, once: canals.json
    python -m fdmr_runner.canals publish <region> <network's published folder> <out>
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import tomllib
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import fdmr_geo
from fdmr_adapters import nhd
import numpy as np

from . import writer

FT = 0.3048
INCH = 0.0254
REACH = "canals"
SEGMENT_M = 1000.0  # the longest channel a canal is cut into, so quality can change along it
SNAP_M = 30.0  # ends of a source's pieces closer than this join
# Published steps, s. The pumping behind the flow is measured daily and the
# water's quality changes over hours, so hourly is ample and keeps the files small.
STEP_S = 3600
# Where each line source is read: the national one through its adapter; any
# other the region names in canals.toml [sources] (#66).
SOURCES = {"nhd": nhd.URL}


def load(region_dir: Path) -> dict | None:
    p = Path(region_dir) / "canals.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def metres(a, b) -> float:
    k = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * fdmr_geo.M_PER_DEG * k, (a[1] - b[1]) * fdmr_geo.M_PER_DEG)


def length(line) -> float:
    return sum(metres(a, b) for a, b in zip(line, line[1:]))


def stitch(pieces: list[list[tuple[float, float]]], start, end, snap: float = SNAP_M) -> list[tuple[float, float]]:
    """One line from the piece end nearest `start` to the one nearest `end`:
    the shortest path through the pieces, whose ends join where closer than `snap` m."""
    ends = [(line[w], i, w) for i, line in enumerate(pieces) for w in (0, -1)]
    adj: dict[tuple[int, int], list[tuple[tuple[int, int], float]]] = {}
    for _, i, w in ends:
        adj.setdefault((i, w), []).append(((i, -1 - w), length(pieces[i])))  # along the piece, to its other end
    for a in range(len(ends)):
        for b in range(a + 1, len(ends)):
            if ends[a][1] != ends[b][1] and metres(ends[a][0], ends[b][0]) < snap:
                adj[ends[a][1:]].append((ends[b][1:], 0.0))
                adj[ends[b][1:]].append((ends[a][1:], 0.0))
    src = min(ends, key=lambda e: metres(e[0], start))[1:]
    dst = min(ends, key=lambda e: metres(e[0], end))[1:]
    dist, prev, q = {src: 0.0}, {}, [(0.0, src)]
    while q:
        d, u = heapq.heappop(q)
        if u == dst:
            break
        if d > dist[u]:
            continue
        for v, w in adj[u]:
            if d + w < dist.get(v, math.inf):
                dist[v], prev[v] = d + w, u
                heapq.heappush(q, (d + w, v))
    if dst not in dist:
        raise ValueError("the pieces do not join from start to end")
    path = [dst]
    while path[-1] != src:
        path.append(prev[path[-1]])
    path.reverse()
    line: list[tuple[float, float]] = []
    for a, b in zip(path, path[1:]):
        if a[0] == b[0]:  # along piece a[0], from end a[1] to end b[1]
            seg = pieces[a[0]] if a[1] == 0 else pieces[a[0]][::-1]
            line += seg if not line else seg[1:]
    return line


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


def _lines(geom: dict) -> list[list[tuple[float, float]]]:
    cs = [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"]
    return [[(round(p[0], 6), round(p[1], 6)) for p in c] for c in cs]


def fetch_pieces(canal: dict, client, cache: dict, urls: dict | None = None) -> list[list[tuple[float, float]]]:
    """The source's line pieces making up a canal (`source`, `pieces`): from a
    GeoJSON download the region names (canals.toml [sources]), or NHD."""
    src, want = canal["source"], canal["pieces"]
    if src in (urls or {}):
        if src not in cache:
            cache[src] = client.get_json(urls[src])
        feats = [f for f in cache[src]["features"] if all(f["properties"].get(k) == v for k, v in want.items())]
    elif src == "nhd":
        pts = [canal["start"], canal["end"], *([canal["via"]] if "via" in canal else [])]
        box = (min(p[0] for p in pts) - 0.2, min(p[1] for p in pts) - 0.2, max(p[0] for p in pts) + 0.2, max(p[1] for p in pts) + 0.2)
        where = " AND ".join(f"{k}='{v}'" for k, v in want.items())
        q = urllib.parse.urlencode({
            "where": where, "outFields": ",".join(want), "returnGeometry": "true", "outSR": 4326, "inSR": 4326,
            "geometry": ",".join(str(x) for x in box), "geometryType": "esriGeometryEnvelope", "f": "geojson",
        })
        feats = client.get_json(f"{SOURCES[src]}?{q}")["features"]
    else:
        raise ValueError(f"unknown line source {src}")
    return [line for f in feats for line in _lines(f["geometry"])]


def canal_line(canal: dict, pieces) -> list[tuple[float, float]]:
    """The canal's line from its config: start (by `via`) to end, cut at `length_km`
    or `past_via_km` beyond `via`."""
    if "via" in canal:
        head = stitch(pieces, canal["start"], canal["via"])
        tail = stitch(pieces, canal["via"], canal["end"])
        if "past_via_km" in canal:
            tail = cut_at(tail, canal["past_via_km"] * 1000)
        line = head + tail[1:]
    else:
        line = stitch(pieces, canal["start"], canal["end"])
    if "length_km" in canal:
        line = cut_at(line, canal["length_km"] * 1000)
    return line


def build(region_dir: Path, client) -> dict:
    """Every canal's line, from its sources (written to canals.json)."""
    cfg = load(region_dir)
    cache: dict = {}
    out = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "source": cfg.get("source_note", ""), "canals": {}}
    for c in cfg["canal"]:
        line = canal_line(c, fetch_pieces(c, client, cache, cfg.get("sources")))
        out["canals"][c["id"]] = {"length_m": round(length(line), 1), "coords": [list(p) for p in line]}
        if "via" in c:
            out["canals"][c["id"]]["via_m"] = round(length(stitch(fetch_pieces(c, client, cache, cfg.get("sources")), c["start"], c["via"])), 1)
    return out


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


def geometry(cfg: dict, lines: dict) -> tuple[dict, list[list[int]]]:
    """The reach's network geometry (docs/format.md), and each canal's channel indices.
    `inlets` names, for each canal, the network export feeding it and the junction its water enters at."""
    elements, nodes, glines, exports, inlets, members = [], [], [], [], [], []
    for c in cfg["canal"]:
        built = lines["canals"][c["id"]]
        chs = channels(c, built["coords"], built.get("via_m"))
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
        members.append(idx)
        exports.append({"name": f"{c['id']}_end", "label": f"{c['name']}: {c['end_label']}", "node": nodes[-1]["id"]})
        inlets.append({"export": c["export"], "node": first, "canal": c["name"]})
    geo = {"reach": REACH, "dims": 1, "network": True, "elements": elements, "nodes": nodes, "lines": glines,
           "exports": exports, "inlets": inlets}
    return geo, members


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


def _filled(x: np.ndarray) -> np.ndarray:
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


def _intake(net_geo: dict, export: str) -> tuple[int, list[int]]:
    """A network export's junction-or-pool index (nodes, then pools) and the
    channels whose water it takes (those at its junction, or at its pool's)."""
    e = next(x for x in net_geo["exports"] if x["name"] == export)
    node_ix = {n["id"]: i for i, n in enumerate(net_geo["nodes"])}
    if "node" in e:
        at, joints = node_ix[e["node"]], {e["node"]}
    else:
        pools = net_geo["pools"]
        p = next(i for i, x in enumerate(pools) if x["id"] == e["pool"])
        at, joints = len(net_geo["nodes"]) + p, set(pools[p]["nodes"])
    chans = [i for i, el in enumerate(net_geo["elements"]) if el["up"] in joints or el["down"] in joints]
    return at, chans


def publish(region_dir: Path, net_dir: Path, out_root: Path, keep: int = 3) -> Path:
    """Publish the canals for the network run published in `net_dir` (its
    reach.json, geometry and day files): out_root/<stamp>/, and out_root/latest."""
    import shutil

    import fdmr_py

    cfg = load(region_dir)
    lines = json.loads((Path(region_dir) / "canals.json").read_text(encoding="utf-8"))
    net_dir = Path(net_dir).resolve()
    net = json.loads((net_dir / "reach.json").read_text(encoding="utf-8"))
    net_id = net["id"]
    net_geo = json.loads((net_dir / "geometry" / f"{net_id}.json").read_text(encoding="utf-8"))
    geo, members = geometry(cfg, lines)
    intakes = [_intake(net_geo, c["export"]) for c in cfg["canal"]]
    # The water's own quality is carried down the canals; what describes the
    # network's bed (its reach.json `bed_fields`: settled mud, erosion) stays
    # where it is (#49).
    bed = set(net.get("bed_fields", []))
    qfields = [f for f in net.get("quality_fields", []) if f not in bed]
    units: dict[str, str] = {}  # each field's unit, as the network's own file gives it

    # The network's export and intake water quality, every published step.
    ts, qs, quals, days = [], [], {f: [] for f in qfields}, []
    for d in net["time"]["days"]:
        nf = net_dir / net_id / f"{d}.nodes.fdf"
        if not nf.exists():
            continue
        _, _, n_aux, _, steps, t0, step, flds = fdmr_py.read_field_file(str(nf))
        ex = np.asarray(dict((n, v) for n, _, v in flds)["export"], dtype=float).reshape(steps, n_aux)
        days.append((d, t0, step, steps))
        ts.append(t0 + step * np.arange(steps))
        qs.append(np.stack([ex[:, at] for at, _ in intakes], axis=1))
        qf = net_dir / net_id / f"{d}.quality.fdf"
        got = {}
        if qf.exists():
            _, _, n_ch, _, qsteps, _, _, qflds = fdmr_py.read_field_file(str(qf))
            got = {n: np.asarray(v, dtype=float).reshape(qsteps, n_ch) for n, _, v in qflds}
            units.update({n: u for n, u, _ in qflds})
        for f in qfields:
            if f in got:
                with np.errstate(all="ignore"):
                    quals[f].append(np.stack([np.nanmean(got[f][:, ch], axis=1) for _, ch in intakes], axis=1))
            else:
                quals[f].append(np.full((steps, len(intakes)), np.nan))
    t = np.concatenate(ts).astype(float)
    q = np.clip(np.concatenate(qs), 0, None)  # [time, canal]
    qual = {f: np.concatenate(v) for f, v in quals.items()}

    n = len(geo["elements"])
    area = np.array([el_area for el_area in _areas(cfg, lines)])
    flow = np.zeros((len(t), n))
    values = {f: np.full((len(t), n), np.nan) for f in qfields}
    for c, idx in enumerate(members):
        flow[:, idx] = q[:, [c]]
        vol = np.cumsum(area[idx] * np.array([geo["elements"][i]["end_m"] for i in idx]))
        centre = vol - area[idx] * np.array([geo["elements"][i]["end_m"] for i in idx]) / 2
        left = departures(t, q[:, c], centre)
        for f in qfields:
            values[f][:, idx] = np.interp(left, t, _filled(qual[f][:, c]))

    # Junction file: the water comes in at each canal's first junction and is
    # carried on at its last (the reach's named exports).
    node_ix = {nd["id"]: i for i, nd in enumerate(geo["nodes"])}
    stamp = net_dir.name
    pub = Path(out_root) / stamp
    if pub.exists():
        shutil.rmtree(pub)
    (pub / "geometry").mkdir(parents=True)
    (pub / REACH).mkdir()
    (pub / "geometry" / f"{REACH}.json").write_text(json.dumps(geo, separators=(",", ":")), encoding="utf-8")
    k = 0
    names = []
    for d, t0, net_step, net_steps in days:
        every = max(1, STEP_S // net_step)
        sl = slice(k, k + net_steps, every)
        k += net_steps
        step, steps = net_step * every, len(range(0, net_steps, every))
        fl = flow[sl]
        fdmr_py.write_field_file(str(pub / REACH / f"{d}.fdf"), REACH, 1, n, 1, steps, t0, step, [
            ("flow_up", "m3/s", True, fl.ravel().tolist()),
            ("flow_down", "m3/s", True, fl.ravel().tolist()),
            ("area", "m2", True, np.broadcast_to(area, fl.shape).ravel().tolist()),
        ])
        m = len(geo["nodes"])
        inflow, export = np.zeros((steps, m)), np.zeros((steps, m))
        for c, inl in enumerate(geo["inlets"]):
            inflow[:, node_ix[inl["node"]]] = q[sl, c]
            export[:, node_ix[geo["exports"][c]["node"]]] = q[sl, c]
        fdmr_py.write_field_file(str(pub / REACH / f"{d}.nodes.fdf"), f"{REACH}:nodes", 1, m, 1, steps, t0, step, [
            ("inflow", "m3/s", True, inflow.ravel().tolist()),
            ("export", "m3/s", True, export.ravel().tolist()),
            ("withdrawal", "m3/s", True, np.zeros((steps, m)).ravel().tolist()),
            ("volume", "m3", True, np.full((steps, m), np.nan).ravel().tolist()),
        ])
        if qfields:
            fdmr_py.write_field_file(str(pub / REACH / f"{d}.quality.fdf"), f"{REACH}:quality", 1, n, 1, steps, t0, step,
                                     [(f, units[f], True, values[f][sl].ravel().tolist()) for f in qfields if f in units])
        names.append(d)
    entry = {
        "id": REACH, "kind": "canal-network", "dims": 1, "network": True, "elements": n, "layers": 1,
        "fields": ["flow_up", "flow_down", "area"], "quality_fields": [f for f in qfields if f in units],
        "time": {**net["time"], "step_s": STEP_S, "days": names}, "now": net["now"],
        "certainty": net["certainty"][:: max(1, STEP_S // net["time"]["step_s"])],
        "flow_source": f"the {net_id} reach's exports ({net.get('flow_source', '')})",
    }
    (pub / "reach.json").write_text(json.dumps(entry, separators=(",", ":")), encoding="utf-8")
    writer.swap_link(Path(out_root), stamp)
    writer.prune_folders(Path(out_root), keep, stamp)
    return pub


def _areas(cfg: dict, lines: dict) -> list[float]:
    return [ch["area_m2"] for c in cfg["canal"]
            for ch in channels(c, lines["canals"][c["id"]]["coords"], lines["canals"][c["id"]].get("via_m"))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the canals' lines from their sources into canals.json")
    b.add_argument("region")
    p = sub.add_parser("publish", help="publish the canals for a network run")
    p.add_argument("region")
    p.add_argument("network", help="the network's published run (its `latest` link)")
    p.add_argument("out")
    args = ap.parse_args(argv)
    region = Path(args.region)
    if load(region) is None:
        print("no canals.toml in this region")
        return 0
    if args.cmd == "build":
        from fdmr_adapters.http import HttpJsonClient

        out = build(region, HttpJsonClient(timeout_s=180))
        (region / "canals.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        for k, v in out["canals"].items():
            print(f"{k}: {v['length_m'] / 1000:.1f} km, {len(v['coords'])} points")
        return 0
    Path(args.out).mkdir(parents=True, exist_ok=True)
    pub = publish(region, Path(args.network), Path(args.out))
    print(f"published {pub}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
