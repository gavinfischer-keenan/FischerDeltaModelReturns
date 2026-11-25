"""One FVCOM hour on the coarse mesh, and the published day files.

Currents are depth-averaged: FVCOM's sigma layers in NOAA's files are evenly
spaced (checked 25 Nov 2026), so the depth average is the mean over layers.
Temperature and salinity are depth-averaged the same way; a 3D field is a
future plug-in (specification 4.1).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .layout import Layout
from .mesh import Coarse, element_mean, node_mean

FVCOM_EPOCH = datetime(2013, 1, 1, tzinfo=timezone.utc)  # NOAA's "seconds since 2013-01-01"
FIELDS = ("u", "v", "level")
QUALITY = ("temperature", "salinity")


FLOW_VARS = ("time", "zeta", "u", "v")  # what the flow alone needs, as for the archive


def hour(layout: Layout, block: bytes, block_start: int, c: Coarse, quality: bool = True) -> tuple[int, dict[str, np.ndarray]]:
    """(time, fields on the coarse mesh) from a byte block of one hourly file;
    without `quality`, only the flow (u, v, level)."""
    t = layout.decode("time", block, block_start)[0]
    when = int((FVCOM_EPOCH + timedelta(seconds=float(t))).timestamp())
    u = layout.decode("u", block, block_start)[0].mean(axis=0)
    v = layout.decode("v", block, block_start)[0].mean(axis=0)
    out = {
        "u": element_mean(c, u).astype(np.float32),
        "v": element_mean(c, v).astype(np.float32),
        "level": node_mean(c, layout.decode("zeta", block, block_start)[0]).astype(np.float32),
    }
    if quality:
        out["temperature"] = node_mean(c, layout.decode("temp", block, block_start)[0].mean(axis=0)).astype(np.float32)
        out["salinity"] = node_mean(c, layout.decode("salinity", block, block_start)[0].mean(axis=0)).astype(np.float32)
    return when, out


def write_days(hours: dict[int, dict[str, np.ndarray]], reach: str, out_dir: Path, start: int, end: int, step: int = 3600) -> list[str]:
    """Day files from hourly fields (time -> fields), for every whole hour in
    [start, end) held. Like the Delta's: `<day>.fdf` holds u, v (m/s) and level
    (m); `<day>.quality.fdf` temperature (degC) and salinity (psu). Elements
    are the mesh's nodes. A day ends where the hours held run out."""
    import fdmr_py

    out_dir.mkdir(parents=True, exist_ok=True)
    days = []
    for d0 in range(start // 86_400 * 86_400, end, 86_400):
        ts = []
        t = max(d0, start)
        t += (-t) % step
        while t < min(d0 + 86_400, end) and t in hours:
            ts.append(t)
            t += step
        if not ts:
            continue
        n = len(hours[ts[0]]["u"])
        name = datetime.fromtimestamp(d0, timezone.utc).strftime("%Y-%m-%d")

        def stack(k: str) -> list[float]:
            return np.stack([hours[t][k] for t in ts]).ravel().tolist()

        fdmr_py.write_field_file(str(out_dir / f"{name}.fdf"), reach, 2, n, 1, len(ts), ts[0], step,
                                 [("u", "m/s", True, stack("u")), ("v", "m/s", True, stack("v")), ("level", "m", True, stack("level"))])
        fdmr_py.write_field_file(str(out_dir / f"{name}.quality.fdf"), f"{reach}:quality", 2, n, 1, len(ts), ts[0], step,
                                 [("temperature", "degC", True, stack("temperature")), ("salinity", "psu", True, stack("salinity"))])
        days.append(name)
    return days
