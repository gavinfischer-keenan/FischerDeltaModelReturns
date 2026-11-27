"""Held-out test of the channel shape fit (fdmr_plugins.river_routing.channel):
the hinged fit, used where it beats a single power law by more than 10 in BIC,
against the single power law alone.

For every USGS gauge of a region with field measurements: fit both on the
measurements of the ten years before the last two, then predict the last two
years' measured velocity (flow over area) and top width. Prints each gauge's
log RMSE, single then as fitted, and both pooled.

    python tools/validation/channel_hinge/holdout.py STORE.sqlite regions/sf-bay-delta/region.toml

Run on the model runner, whose store holds the measurements (fdmr_runner.fetch).
"""

from __future__ import annotations

import sys
import time
import tomllib

import numpy as np
from fdmr_plugins.river_routing import channel
from fdmr_store import Store

YEAR = 365 * 86_400


def main(store_path: str, region_toml: str) -> None:
    st = Store(store_path)
    now = int(time.time())
    cfg = tomllib.load(open(region_toml, "rb"))
    sites: dict[str, str] = {}
    for g in cfg["gauges"]:
        s = g.get("usgs_site") or (g["site"] if g.get("source", "usgs") == "usgs" else None)
        if s:
            sites[s] = g.get("name", "")
    pooled: dict[str, list[float]] = {"single": [], "fitted": []}
    for site, name in sites.items():
        ms = [m for m in st.channel_measurements("usgs", site) if m.area and m.width and m.flow > 0 and m.t >= now - 12 * YEAR]
        train = [m for m in ms if m.t < now - 2 * YEAR]
        test = [m for m in ms if m.t >= now - 2 * YEAR]
        fitted = channel.fit(train)
        if fitted is None or len(test) < 3:
            continue
        single = channel.ChannelShape(fitted.area_a, fitted.area_b, fitted.width_a, fitted.width_b, 0, 0, 0)
        if fitted.q_break > 0:  # the single law, refitted, for comparison
            import fdmr_py

            a = fdmr_py.fit_power_law([m.flow for m in train], [m.area for m in train])
            w = fdmr_py.fit_power_law([m.flow for m in train], [m.width for m in train])
            single = channel.ChannelShape(a[0], a[1], w[0], w[1], a[2], w[2], len(train))
        row = []
        for label, shape in (("single", single), ("fitted", fitted)):
            ev = np.array([np.log(shape.velocity(m.flow)) - np.log(m.flow / m.area) for m in test])
            ew = np.array([np.log(shape.width(m.flow)) - np.log(m.width) for m in test])
            pooled[label] += [*(ev**2), *(ew**2)]
            row.append((float(np.sqrt((ev**2).mean())), float(np.sqrt((ew**2).mean()))))
        tag = f"hinged at {fitted.q_break:7.1f} m3/s" if fitted.q_break > 0 else "single"
        print(f"| {site} | {name[:34]} | {len(train)} | {len(test)} | {tag} | {row[0][0]:.3f} → {row[1][0]:.3f} | {row[0][1]:.3f} → {row[1][1]:.3f} |")
    for k, v in pooled.items():
        print(f"{k}: pooled log RMSE (velocity and width together) {np.sqrt(np.mean(v)):.4f} over {len(v)} values")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
