"""The bloom forecast's rules (regions/sf-bay-delta/blooms.toml) against the
surveys' measured Microcystis (issue #28).

Data: Kurobe, Lehman, Lesmeister and Teh (2022), cyanoHAB data for the upper
San Francisco Estuary, 2014-2019, EDI edi.1076.1, doi:10.6073/pasta/
b4e180cb925266171285375a3e202635 (CC BY 4.0). Each sample is given the
forecast's class from its own measured water temperature and salinity
(unlikely, possible, likely), and the classes are compared by Microcystis
cells (qPCR, cells/mL), colony biovolume (tow net, um3/L) and total
microcystin (ug/L): the share detected, the median and the upper quartile.
Intervals: 2000 bootstrap resamples of sampling days (samples on one day are
not independent).

    python tools/validation/blooms/check_1076.py DATA.csv regions/sf-bay-delta/blooms.toml
"""

from __future__ import annotations

import sys
import tomllib

import numpy as np
import pandas as pd

MEASURES = {"ucd.qpcr.total.MIC": "cells (qPCR, /mL)", "dwr.MIC_totbvL": "biovolume (um3/L)", "ucd.ppia.MC.total.ugL": "microcystin (ug/L)"}
NAMES = {0: "unlikely", 1: "possible", 2: "likely"}


def classes(t: pd.Series, s: pd.Series, gate: float, strong: float, below: float) -> pd.Series:
    c = np.where(t >= strong, 2, np.where(t >= gate, 1, 0))
    c = np.where(s >= below, 0, c)
    return pd.Series(np.where(t.notna() & s.notna(), c, -1), index=t.index)


def summary(df: pd.DataFrame, col: str, rng: np.random.Generator, reps: int = 2000) -> dict:
    out = {}
    days = df["Collection_Date"].unique()
    by_day = {d: g for d, g in df.groupby("Collection_Date")}
    for k in (0, 1, 2):
        sub = df[(df["class"] == k) & df[col].notna()]
        if not len(sub):
            continue
        det = float((sub[col] > 0).mean())
        boot = []
        for _ in range(reps):
            pick = rng.choice(days, size=len(days))
            b = pd.concat([by_day[d] for d in pick])
            b = b[(b["class"] == k) & b[col].notna()]
            if len(b):
                boot.append((b[col] > 0).mean())
        lo, hi = np.percentile(boot, [2.5, 97.5])
        out[NAMES[k]] = {"n": len(sub), "detected": round(det, 2), "ci": (round(lo, 2), round(hi, 2)),
                         "median": float(sub[col].median()), "q75": float(sub[col].quantile(0.75))}
    return out


def main(csv: str, rules: str) -> None:
    cfg = tomllib.load(open(rules, "rb"))
    gate, strong = cfg["temperature_c"]
    df = pd.read_csv(csv, na_values=["NA", ""])
    df["class"] = classes(df["field.Water.temp"], df["field.Salinity"], gate, strong, cfg["salinity_below_psu"])
    df = df[df["class"] >= 0]
    print(f"samples with temperature and salinity: {len(df)}; days {df['Collection_Date'].nunique()}; "
          f"temperature {df['field.Water.temp'].min():.1f}-{df['field.Water.temp'].max():.1f} degC; "
          f"salinity {df['field.Salinity'].min():.2f}-{df['field.Salinity'].max():.2f}")
    print("classes:", {NAMES[k]: int((df['class'] == k).sum()) for k in (0, 1, 2)})
    rng = np.random.default_rng(20261127)
    for col, label in MEASURES.items():
        print(f"\n{label}")
        for name, r in summary(df, col, rng).items():
            print(f"  {name:9s} n={r['n']:3d}  detected {r['detected']:.2f} [{r['ci'][0]:.2f}, {r['ci'][1]:.2f}]  "
                  f"median {r['median']:.4g}  upper quartile {r['q75']:.4g}")
    sal = df[df["field.Salinity"] >= cfg["salinity_below_psu"]]
    if len(sal):
        print(f"\nat or above {cfg['salinity_below_psu']} psu: {len(sal)} samples, cells detected in "
              f"{(sal['ucd.qpcr.total.MIC'] > 0).mean():.2f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
