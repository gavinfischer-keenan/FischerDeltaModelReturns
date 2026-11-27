"""The bloom forecast's rules against the surveys' visual Microcystis index
(issue #28).

Data: Bashevkin et al. (2023), IEP zooplankton synthesis 1972-2021, EDI
edi.539.4, its environment.csv (CC BY 4.0): every sample with a visual
Microcystis rating (1 absent, 2 low, 3 medium, 4 high, 5 very high), surface
temperature and surface salinity. Each sample takes the forecast's class from
its own temperature and salinity; the classes are compared by the share
rated present (2+), medium or more (3+) and high or more (4+). Intervals:
2000 bootstrap resamples of sampling days.

    python tools/validation/blooms/check_539.py environment.csv regions/sf-bay-delta/blooms.toml
"""

from __future__ import annotations

import sys
import tomllib

import numpy as np
import pandas as pd

NAMES = {0: "unlikely", 1: "possible", 2: "likely"}


def main(csv: str, rules: str) -> None:
    cfg = tomllib.load(open(rules, "rb"))
    gate, strong = cfg["temperature_c"]
    df = pd.read_csv(csv, usecols=["Source", "Date", "Temperature", "SalSurf", "Microcystis"], na_values=["NA", ""])
    df = df.dropna(subset=["Temperature", "SalSurf", "Microcystis"])
    df["score"] = pd.to_numeric(df["Microcystis"], errors="coerce")
    df = df.dropna(subset=["score"])
    t, s = df["Temperature"], df["SalSurf"]
    df["class"] = np.where(s >= cfg["salinity_below_psu"], 0, np.where(t >= strong, 2, np.where(t >= gate, 1, 0)))
    years = pd.to_datetime(df["Date"]).dt.year
    print(f"rated samples with temperature and salinity: {len(df)}; days {df['Date'].nunique()}; years {years.min()}-{years.max()}; "
          f"surveys {sorted(df['Source'].unique())}")
    print("classes:", {NAMES[k]: int((df['class'] == k).sum()) for k in (0, 1, 2)})
    rng = np.random.default_rng(20261127)
    days = df["Date"].unique()
    groups = {d: g for d, g in df.groupby("Date")}
    boot = []
    for _ in range(2000):
        b = pd.concat([groups[d] for d in rng.choice(days, size=len(days))])
        boot.append({(k, m): float((b.loc[b["class"] == k, "score"] >= m).mean()) for k in (0, 1, 2) for m in (2, 3, 4)})
    for k in (0, 1, 2):
        sub = df[df["class"] == k]
        cells = []
        for m, label in ((2, "present"), (3, "medium+"), (4, "high+")):
            v = float((sub["score"] >= m).mean())
            lo, hi = np.percentile([b[(k, m)] for b in boot if not np.isnan(b[(k, m)])], [2.5, 97.5])
            cells.append(f"{label} {v:.3f} [{lo:.3f}, {hi:.3f}]")
        print(f"  {NAMES[k]:9s} n={len(sub):5d}  " + "  ".join(cells))
    warm = df[df["Temperature"] >= gate]
    for name, sub in (("< limit", warm[warm["SalSurf"] < cfg["salinity_below_psu"]]), (">= limit", warm[warm["SalSurf"] >= cfg["salinity_below_psu"]])):
        print(f"warm (>= {gate} degC), salinity {name}: n={len(sub)}  present {(sub['score'] >= 2).mean():.3f}  medium+ {(sub['score'] >= 3).mean():.3f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
