"""A published reach's fields over its days, read back from a cycle (#59).

Each reach writes one field file per day (docs/format.md): `<day>.fdf` for its
flow fields, `<day>.quality.fdf` for its water's quality, `<day>.nodes.fdf`
for its junctions. Everything that reads them back over several days (the
stations' model values, the prediction ledger, the level checks) reads them
here, by one rule for a day that is missing:

    a missing day is missing: its steps are NaN, in their place in time.

Each day's file is placed by its own start time, so a value is never shifted
onto another day's time, and a day that is not there never closes the gap.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SUFFIX = {"flow": ".fdf", "quality": ".quality.fdf", "nodes": ".nodes.fdf"}


def read_days(cdir: Path, reach: str, days: list[str], names: list[str], kind: str = "flow",
              columns: list[int] | None = None, skip_before: int | None = None,
              dtype=np.float64) -> tuple[int | None, int | None, dict[str, np.ndarray]]:
    """The fields `names` of one reach over `days`: (t0, step, {name: values[step, column]}),
    every column, or those in `columns`. A day whose file is missing, or that
    lacks a field, is NaN there. Days ending at or before `skip_before` (UTC s)
    are not read. (None, None, {}) when no day's file is there."""
    import fdmr_py

    got: list[tuple[int, int, int, int, dict]] = []
    for day in days:
        path = Path(cdir) / reach / f"{day}{SUFFIX[kind]}"
        if not path.exists():
            continue
        _, _, n, _, steps, d0, step, flds = fdmr_py.read_field_file(str(path))
        if skip_before is not None and d0 + steps * step <= skip_before:
            continue
        vals = {name: v for name, _, v in flds if name in names}
        got.append((d0, step, steps, n, {k: np.asarray(v, dtype=dtype).reshape(steps, n) for k, v in vals.items()}))
    if not got:
        return None, None, {}
    step = got[0][1]
    t0 = min(d0 for d0, *_ in got)
    rows = max((d0 - t0) // step + steps for d0, _, steps, _, _ in got)
    width = len(columns) if columns is not None else got[0][3]
    out = {name: np.full((rows, width), np.nan, dtype=dtype) for name in names}
    for d0, st, steps, _, fields in got:
        if st != step:
            raise ValueError(f"{reach}: days with different time steps ({st} s and {step} s)")
        i = (d0 - t0) // step
        for name, v in fields.items():
            out[name][i:i + steps] = v[:, columns] if columns is not None else v
    return t0, step, out


def at_times(t0: int, step: int, v: np.ndarray, ts: np.ndarray) -> np.ndarray:
    """Values (rows of steps) at times ts, linear between steps; NaN outside."""
    pos = (np.asarray(ts) - t0) / step
    out = np.full((len(pos), v.shape[1]), np.nan)
    ok = (pos >= 0) & (pos <= len(v) - 1)
    i = np.floor(pos[ok]).astype(int)
    f = (pos[ok] - i)[:, None]
    j = np.minimum(i + 1, len(v) - 1)
    out[ok] = v[i] * (1 - f) + v[j] * f
    return out
