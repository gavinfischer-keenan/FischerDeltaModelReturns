"""Measured daily means for the experiments (issue #33), from the EDI packages
as downloaded (their zip files, unchanged). Writes, per experiment, a
measured.csv (date, station, quantity, value) and, where the package holds
DWR's own model runs, schism.csv (date, station, scenario, ec).

- false-river-barrier-2021: edi.1162.2 (ref:hartman2024edb, CC BY 4.0)
  - ContinuousWQ.csv: daily EC (uS/cm) at the DWR stations around the barrier
  - schism_station_output.csv: 15-minute EC at Bethel Island (BET), Clifton
    Court (CLC) and Old River at Bacon Island (BAC), measured ("observed") and
    from Bay-Delta SCHISM with and without the barrier ("edb", "no_edb")
  - Hydrodynamics_Data_2015_2020_2021.csv: 15-minute tidally filtered flow
    (NetFlow, cfs, the agency's own Godin filter) at False River, Dutch Slough,
    Holland Cut, Old River at Quimby, Old River at San Joaquin, Fisherman's Cut
- suisun-gates-2018: edi.876.9 (ref:hartman2026smscg, CC BY 4.0)
  - SMSCG_wq_data_2017-2024_final_edi.csv: 15-minute specific conductance

A day's mean needs 72 of its 96 quarter-hours (18 hours). Dates are the
packages' own, Pacific Standard Time.

    python tools/validation/experiments/prepare_edi.py DOWNLOADS regions/sf-bay-delta/experiments
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

M3S_PER_CFS = 0.3048**3
MIN_QUARTERS = 72


def rows(zip_path: Path, member: str):
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.endswith(member))
        with z.open(name) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


def number(s: str | None) -> float | None:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def daily_means(points, lo: str, hi: str, min_n: int = MIN_QUARTERS) -> dict[tuple, float]:
    """{key: mean} from ((date, *key), value) pairs, dates in [lo, hi], days with min_n values."""
    s: dict[tuple, float] = defaultdict(float)
    n: dict[tuple, int] = defaultdict(int)
    for key, v in points:
        if v is not None and lo <= key[0] <= hi:
            s[key] += v
            n[key] += 1
    return {k: s[k] / n[k] for k in s if n[k] >= min_n}


def write(path: Path, header: list[str], table: dict[tuple, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for k in sorted(table):
            w.writerow([*k, round(table[k], 2)])
    print(f"{path}: {len(table)} rows")


def false_river(z: Path, out: Path, lo: str = "2020-07-01", hi: str = "2021-12-31") -> None:
    measured: dict[tuple, float] = {}
    for r in rows(z, "ContinuousWQ.csv"):
        v = number(r["EC"])
        if v is not None and lo <= r["Date"] <= hi:
            measured[(r["Date"], r["Station"], "ec")] = v
    obs, model = [], []
    for r in rows(z, "schism_station_output.csv"):
        day = r["datetime"][:10]
        for col in ("bet", "clc", "bac"):
            v = number(r[col])
            if r["scenario"] == "observed":
                obs.append(((day, col.upper(), "ec"), v))
            elif r["scenario"] in ("edb", "no_edb"):
                model.append(((day, col.upper(), {"edb": "with barrier", "no_edb": "without barrier"}[r["scenario"]]), v))
    measured.update({k: v for k, v in daily_means(obs, lo, hi).items() if k not in measured})
    flows = (((r["DateTime"][:10], r["Station"], "net_flow"), None if (v := number(r["NetFlow"])) is None else v * M3S_PER_CFS)
             for r in rows(z, "Hydrodynamics_Data_2015_2020_2021.csv"))
    measured.update(daily_means(flows, lo, hi))
    write(out / "false-river-barrier-2021" / "measured.csv", ["date", "station", "quantity", "value"], measured)
    write(out / "false-river-barrier-2021" / "schism.csv", ["date", "station", "scenario", "ec"], daily_means(model, lo, hi))


def suisun(z: Path, out: Path, lo: str = "2017-12-01", hi: str = "2018-12-31") -> None:
    pts = (((r["date_time_pst"][:10].replace("/", "-"), r["station"], "ec"), number(r["specific_conductance_uScm"]))
           for r in rows(z, "SMSCG_wq_data_2017-2024_final_edi.csv") if r["year"] in ("2017", "2018"))
    write(out / "suisun-gates-2018" / "measured.csv", ["date", "station", "quantity", "value"], daily_means(pts, lo, hi))


if __name__ == "__main__":
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    false_river(src / "edi.1162.2.zip", out)
    suisun(src / "edi.876.9.zip", out)
