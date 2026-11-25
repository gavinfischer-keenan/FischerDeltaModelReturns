"""HYDRO's results as published field files (docs/format.md).

HYDRO writes flow, area and water level for every channel every 5 minutes in
its HDF5 "tidefile", in feet and Pacific Standard Time. This publishes one
file per UTC day at 15-minute steps, in SI units:

- flow_up, flow_down: flow at the channel's upstream and downstream ends,
  positive downstream, m3/s, as 32-bit floats (a small slough carries a few
  m3/s beside channels carrying thousands);
- area: the channel's mean cross-section area, m2;
- level: the water surface, the mean of the two ends, m above DSM2's datum;

Water quality goes in a file of its own, `<day>.quality.fdf`, so that moving
parcels does not mean downloading it: with QUAL's output, ec (uS/cm, specific
conductance as QUAL computes it) and salinity (psu, PSS-78 from the math
library), each the channel's mean; with the fingerprint study's output,
source:<group> for each water source, the share (0 to 1) of the channel's
water that came from it.

A reader works out velocity (flow / area) and volume (area x length) itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import fdmr_py

from .dss import PST

FT = 0.3048
CFS = FT**3
DAY = 86_400
FIELDS = ["flow_up", "flow_down", "area", "level"]


def tidefile_start(attr: bytes | str) -> int:
    """The UTC time of a tidefile's first record, from its start_time attribute (PST)."""
    s = attr.decode() if isinstance(attr, bytes) else str(attr)
    return int(datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()) + PST


def write_days(
    tidefile: Path, reach: str, out_dir: Path, start: int, end: int, step: int = 900,
    node_ids: list[int] | None = None, extras=None, exports: set[str] | None = None,
    qual_file: Path | None = None,
    sources_file: Path | None = None,
) -> list[str]:
    """Field files for every UTC day overlapping [start, end); returns the day names.

    With `node_ids` and `extras` (grid.Extras), each day also gets:

    - `<day>.nodes.fdf`: one element per junction (in `node_ids` order), then
      one per pool. `inflow`, `export` and `withdrawal` (m3/s) are the water
      coming in from outside there, taken by the named exports, and taken by
      anything else (farms, diversions). `volume` (m3) is each pool's water,
      no value at plain junctions.
    - `<day>.links.fdf`: one element per pool connection, `flow` (m3/s),
      positive from the pool to its junction (HYDRO's sign, checked 25 Nov 2026
      against each pool's water budget).
    """
    import h5py
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    days = []
    qh = h5py.File(qual_file, "r") if qual_file else None
    k_ec = None
    if qh is not None:
        names = [x.decode().strip() for x in qh["output/constituent_names"][:]]
        q_chan, q_pool = qh["output/channel avg concentration"], qh["output/reservoir concentration"]
        q_t0 = tidefile_start(q_chan.attrs["start_time"][0])
        q_step = 900  # QUAL's HDF5 output interval (study.with_hdf5_output)
        k_ec = names.index("ec") if "ec" in names else None
    sh = h5py.File(sources_file, "r") if sources_file else None
    if sh is not None:
        s_names = [x.decode().strip() for x in sh["output/constituent_names"][:]]
        s_chan = sh["output/channel avg concentration"]
        s_t0 = tidefile_start(s_chan.attrs["start_time"][0])
    with h5py.File(tidefile, "r") as f:
        if node_ids is not None and extras is not None:
            k_of = {n: i for i, n in enumerate(node_ids)}
            k_of.update({("pool", p["id"]): len(node_ids) + j for j, p in enumerate(extras.pools)})
            target = np.array([k_of.get(e["node"], -1) if "node" in e else k_of[("pool", e["pool"])] for e in extras.external])
            is_export = np.array([e["name"] in (exports or set()) for e in extras.external])
            n_aux = len(node_ids) + len(extras.pools)
        flow, area, stage = f["hydro/data/channel flow"], f["hydro/data/channel avg area"], f["hydro/data/channel stage"]
        # The tidefile's "channel stage" is depth above the channel bed at each end
        # (checked 25 Nov 2026: 59.9 ft over a bed at -54.5 ft at a main river
        # channel); the water surface is the bed plus that depth.
        bottom = f["hydro/geometry/channel_bottom"][:]  # (2, channels), ft
        t5 = tidefile_start(flow.attrs["start_time"][0])
        every = step // 300
        n5 = flow.shape[0]
        for d0 in range(start // DAY * DAY, end, DAY):
            a, b = max(d0, start), min(d0 + DAY, end)
            a += (-(a - t5)) % step  # first step on the grid and inside the tidefile
            i0, i1 = (a - t5) // 300, min(n5, (b - t5 + 299) // 300)
            if i0 < 0 or i0 >= i1:
                continue
            q = flow[i0:i1:every, :, :] * CFS
            steps, n = q.shape[0], q.shape[1]
            ar = area[i0:i1:every, :] * FT**2
            lv = (stage[i0:i1:every, :, :] + bottom.T[None, :, :]).mean(axis=2) * FT
            name = datetime.fromtimestamp(d0, timezone.utc).strftime("%Y-%m-%d")
            fields = [
                ("flow_up", "m3/s", False, q[:, :, 0].ravel().tolist()),
                ("flow_down", "m3/s", False, q[:, :, 1].ravel().tolist()),
                ("area", "m2", True, ar.ravel().tolist()),
                ("level", "m", True, lv.ravel().tolist()),
            ]
            quality = []
            pool_ec = None
            if k_ec is not None:
                j0 = (a - q_t0) // q_step
                ec = np.full((steps, n), np.nan)
                pool_ec = np.full((steps, len(extras.pools) if extras else 0), np.nan)
                if j0 >= 0:
                    have = q_chan[j0 : j0 + steps, k_ec, :]
                    ec[: have.shape[0]] = have
                    if extras is not None:
                        hp = q_pool[j0 : j0 + steps, k_ec, :]
                        pool_ec[: hp.shape[0]] = hp
                flat = ec.ravel().tolist()
                quality += [
                    ("ec", "uS/cm", True, flat),
                    ("salinity", "psu", True, fdmr_py.practical_salinity(flat)),
                ]
            if sh is not None:
                j0 = (a - s_t0) // 900
                for k, g in enumerate(s_names):
                    share = np.full((steps, n), np.nan)
                    if j0 >= 0:
                        have = s_chan[j0 : j0 + steps, k, :]
                        share[: have.shape[0]] = np.clip(have / 100.0, 0.0, 1.0)
                    quality.append((f"source:{g}", "1", True, share.ravel().tolist()))
            fdmr_py.write_field_file(str(out_dir / f"{name}.fdf"), reach, 1, n, 1, steps, a, step, fields)
            if quality:
                fdmr_py.write_field_file(str(out_dir / f"{name}.quality.fdf"), f"{reach}:quality", 1, n, 1, steps, a, step, quality)
            if node_ids is not None and extras is not None:
                qx = f["hydro/data/qext flow"][i0:i1:every, :] * CFS  # positive into the system
                inflow = np.zeros((steps, n_aux))
                export = np.zeros((steps, n_aux))
                withdrawal = np.zeros((steps, n_aux))
                ok = target >= 0
                np.add.at(inflow.T, target[ok], np.clip(qx[:, ok], 0, None).T)
                taken = np.clip(-qx, 0, None)
                np.add.at(export.T, target[ok & is_export], taken[:, ok & is_export].T)
                np.add.at(withdrawal.T, target[ok & ~is_export], taken[:, ok & ~is_export].T)
                volume = np.full((steps, n_aux), np.nan)
                h = f["hydro/data/reservoir height"][i0:i1:every, :] * FT
                for j, p in enumerate(extras.pools):
                    volume[:, len(node_ids) + j] = np.clip(h[:, j] - p["bottom_m"], 0, None) * p["area_m2"]
                node_fields = [
                    ("inflow", "m3/s", True, inflow.ravel().tolist()),
                    ("export", "m3/s", True, export.ravel().tolist()),
                    ("withdrawal", "m3/s", True, withdrawal.ravel().tolist()),
                    ("volume", "m3", False, volume.ravel().tolist()),
                ]
                if pool_ec is not None:
                    ec_nodes = np.full((steps, n_aux), np.nan)
                    ec_nodes[:, len(node_ids):] = pool_ec
                    node_fields.append(("ec", "uS/cm", True, ec_nodes.ravel().tolist()))
                fdmr_py.write_field_file(
                    str(out_dir / f"{name}.nodes.fdf"), f"{reach}:nodes", 1, n_aux, 1, steps, a, step, node_fields
                )
                links = f["hydro/data/reservoir flow"][i0:i1:every, :] * CFS
                fdmr_py.write_field_file(
                    str(out_dir / f"{name}.links.fdf"), f"{reach}:links", 1, links.shape[1], 1, steps, a, step,
                    [("flow", "m3/s", False, links.ravel().tolist())],
                )
            days.append(name)
    for h in (qh, sh):
        if h is not None:
            h.close()
    return days
