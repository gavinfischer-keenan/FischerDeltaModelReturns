"""HYDRO's results as published field files (docs/format.md).

HYDRO writes flow, area and water level for every channel every 5 minutes in
its HDF5 "tidefile", in feet and Pacific Standard Time. This publishes one
file per UTC day at 15-minute steps, in SI units:

- flow_up, flow_down: flow at the channel's upstream and downstream ends,
  positive downstream, m3/s, as 32-bit floats (a small slough carries a few
  m3/s beside channels carrying thousands);
- area: the channel's mean cross-section area, m2;
- level: the water surface, the mean of the two ends, m above DSM2's datum.

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


def write_days(tidefile: Path, reach: str, out_dir: Path, start: int, end: int, step: int = 900) -> list[str]:
    """Field files for every UTC day overlapping [start, end); returns the day names."""
    import h5py

    out_dir.mkdir(parents=True, exist_ok=True)
    days = []
    with h5py.File(tidefile, "r") as f:
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
            fdmr_py.write_field_file(
                str(out_dir / f"{name}.fdf"), reach, 1, n, 1, steps, a, step,
                [
                    ("flow_up", "m3/s", False, q[:, :, 0].ravel().tolist()),
                    ("flow_down", "m3/s", False, q[:, :, 1].ravel().tolist()),
                    ("area", "m2", True, ar.ravel().tolist()),
                    ("level", "m", True, lv.ravel().tolist()),
                ],
            )
            days.append(name)
    return days
