"""The export canals as a reach of their own (issue #5; specification 7.4;
regions/<id>/canals.toml): building their lines once, and publishing them
for each network run. The maths, a canal's line, channels and the water's
travel down it, is the canal-network plug-in's (fdmr_plugins.canal); the
lines' sources are read through their adapters (fdmr_adapters.nhd, and
fdmr_adapters.geojson for a file the region names in canals.toml [sources]).

What a network reach's pumps take does not vanish: it carries on down the
canals. The join (index.json `handoffs`, `outlets`) hands the network's
parcels and flow particles into each canal's first junction.

    python -m fdmr_runner.canals build <region>      # lines, once: canals.json
    python -m fdmr_runner.canals publish <region> <network's published folder> <out>
"""

from __future__ import annotations

import argparse
import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from fdmr_adapters import geojson, nhd
from fdmr_adapters.lines import line_between
from fdmr_plugins import canal
import numpy as np

from . import writer

REACH = canal.REACH
SNAP_M = 30.0  # ends of a source's pieces closer than this join
# Published steps, s. The pumping behind the flow is measured daily and the
# water's quality changes over hours, so hourly is ample and keeps the files small.
STEP_S = 3600
NHD = "nhd"  # canals.toml's name for the national lines (fdmr_adapters.nhd); any other is a [sources] file
BOX_DEG = 0.2  # NHD is searched in the box around a canal's points, widened by this


def load(region_dir: Path) -> dict | None:
    p = Path(region_dir) / "canals.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def fetch_pieces(c: dict, sources: dict, files: dict) -> list[list[tuple[int, float, float]]]:
    """The pieces of line making up a canal (`source`, `pieces`), keyed by
    position: from NHD, or a GeoJSON file the region names (canals.toml
    [sources]; each downloaded once into `files`)."""
    src, want = c["source"], c["pieces"]
    if src in sources:
        if src not in files:
            files[src] = geojson.download(sources[src])
        return geojson.pieces(files[src], want)
    if src == NHD:
        pts = [c["start"], c["end"], *([c["via"]] if "via" in c else [])]
        box = (min(p[0] for p in pts) - BOX_DEG, min(p[1] for p in pts) - BOX_DEG,
               max(p[0] for p in pts) + BOX_DEG, max(p[1] for p in pts) + BOX_DEG)
        return nhd.pieces(want, box)
    raise ValueError(f"unknown line source {src}")


def walk(pieces) -> canal.Walk:
    """The line along the pieces from one point to another (the shortest path; ends within SNAP_M join)."""
    return lambda a, b: [(x, y) for x, y in line_between(pieces, tuple(a), tuple(b), join_m=SNAP_M)]


def build(region_dir: Path) -> dict:
    """Every canal's line, from its sources (written to canals.json)."""
    cfg = load(region_dir)
    files: dict = {}
    out = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "source": cfg.get("source_note", ""), "canals": {}}
    for c in cfg["canal"]:
        line, via_m = canal.canal_line(c, walk(fetch_pieces(c, cfg.get("sources", {}), files)))
        out["canals"][c["id"]] = {"length_m": round(canal.length(line), 1), "coords": [list(p) for p in line]}
        if via_m is not None:
            out["canals"][c["id"]]["via_m"] = round(via_m, 1)
    return out


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
    geo, members, area = canal.geometry(cfg, lines)
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
    flow = np.zeros((len(t), n))
    values = {f: np.full((len(t), n), np.nan) for f in qfields}
    for c, idx in enumerate(members):
        flow[:, idx] = q[:, [c]]
        lengths = np.array([geo["elements"][i]["end_m"] for i in idx])
        for f, v in canal.carried(t, q[:, c], {f: qual[f][:, c] for f in qfields}, area[idx], lengths).items():
            values[f][:, idx] = v

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
        "id": REACH, "kind": canal.KIND, "dims": 1, "network": True, "elements": n, "layers": 1,
        "fields": ["flow_up", "flow_down", "area"], "quality_fields": [f for f in qfields if f in units],
        "time": {**net["time"], "step_s": STEP_S, "days": names}, "now": net["now"],
        "certainty": net["certainty"][:: max(1, STEP_S // net["time"]["step_s"])],
        "flow_source": f"the {net_id} reach's exports ({net.get('flow_source', '')})",
        # What it does not model, said where the reach is published (#72).
        **({"assumptions": [{"input": f"the {net_id} reach's water quality at each intake", "how": canal.ASSUMED}]}
           if qfields else {}),
    }
    (pub / "reach.json").write_text(json.dumps(entry, separators=(",", ":")), encoding="utf-8")
    writer.swap_link(Path(out_root), stamp)
    writer.prune_folders(Path(out_root), keep, stamp)
    return pub


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
        out = build(region)
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
