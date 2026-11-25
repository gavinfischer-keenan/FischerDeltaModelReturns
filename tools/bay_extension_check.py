"""Check the Bay's extension (fdmr_plugins.fvcom.extend) against NOAA itself.

    python tools/bay_extension_check.py REGION_DIR WORK_DIR STORE [DAYS]

Fits tides plus outflow drift to the archive in WORK_DIR, leaving out the last
DAYS (default 14), then predicts those days from the measured outflow and
compares with NOAA's own hourly nowcasts for them (WORK_DIR/hours), at every
coarse node and at a few named places:

- skill: 1 - mean squared error / variance of NOAA's series (1 is perfect, 0
  no better than NOAA's mean), for u, v and the water level;
- the mean current over the days (what carries a plume on), predicted against NOAA's;
- the same fitted without the outflow term (tides only), to show what it adds;
- the fitted tide at San Francisco's tide gauge (9414290) against NOAA's
  published harmonic constants there (amplitudes, m).

A check on the model runner, not part of the app.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fdmr_plugins.fvcom import extend, mesh
from fdmr_runner import bay

PLACES = {
    "Golden Gate": (-122.478, 37.812),
    "Central Bay": (-122.390, 37.840),
    "Carquinez Strait": (-122.200, 38.060),
    "Suisun Bay": (-122.020, 38.070),
    "South Bay": (-122.180, 37.600),
    "SF tide gauge": (-122.4659, 37.8063),
}
# NOAA CO-OPS, station 9414290, harmonic constants (5-year vector average), m.
NOAA_SF = {"M2": 0.576, "K1": 0.370, "O1": 0.230, "S2": 0.137}


def stamps(files: list[Path]) -> np.ndarray:
    return np.array([int(datetime.strptime(f.stem, "%Y%m%dT%H%MZ").replace(tzinfo=timezone.utc).timestamp()) for f in files])


def main() -> None:
    region, work, store = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    days = int(sys.argv[4]) if len(sys.argv) > 4 else 14
    cfg = bay.load(region)
    c = mesh.Coarse.load(work / "mesh.npz")
    hours = sorted((work / "hours").glob("*.npz"))
    th = stamps(hours)
    cut = int(th.max()) - days * 86_400
    arch = [f for f in sorted((work / "archive").glob("*.npz"))]
    ta = stamps(arch)
    keep = ta < cut
    arch, ta = [f for f, k in zip(arch, keep) if k], ta[keep]
    out, _ = bay.daily_outflow(store, cfg["extension"]["outflow"], int(ta.min()), int(th.max()))
    qa = bay.on_hours(out, ta)
    y = bay.flow_stack(arch)
    full = extend.fit(ta, qa, y)
    tides = extend.fit(ta, np.zeros_like(qa), y)
    test = [(t, f) for t, f in zip(th, hours) if t > cut]
    tt = np.array([t for t, _ in test])
    truth = bay.flow_stack([f for _, f in test])
    qt = bay.on_hours(out, tt)
    pf, pt = full.predict(tt, qt), tides.predict(tt, np.zeros_like(qt))
    # With the recent non-tidal offset carried on, as the published extension does:
    # the fit's mean miss over the archived hours of the 3 days before the cut.
    recent = [(a, f) for a, f in zip(ta, arch) if a > cut - bay.ANOMALY_H * 3600]
    tr = np.array([a for a, _ in recent])
    off = extend.anomaly(full, tr, bay.on_hours(out, tr), bay.flow_stack([f for _, f in recent]))
    po = {k: pf[k] + off[k][None, :] for k in pf}

    def skill(p: np.ndarray, o: np.ndarray) -> np.ndarray:
        var = o.var(axis=0)
        return 1 - ((p - o) ** 2).mean(axis=0) / np.where(var > 0, var, np.nan)

    print(f"fitted on {len(ta)} archived hours to {datetime.fromtimestamp(cut, timezone.utc):%Y-%m-%d}, "
          f"predicted {len(tt)} hours ({days} days) against NOAA's nowcasts")
    for k in ("u", "v", "level"):
        s = skill(pf[k], truth[k])
        so = skill(po[k], truth[k])
        print(f"  {k:5s} skill, median over {len(c.x)} nodes: {np.nanmedian(so):.3f} with the recent offset, {np.nanmedian(s):.3f} without, "
              f"{np.nanmedian(skill(pt[k], truth[k])):.3f} tides only; rms error median {np.median(np.sqrt(((po[k] - truth[k]) ** 2).mean(axis=0))):.3f} "
              f"(without the offset {np.median(np.sqrt(((pf[k] - truth[k]) ** 2).mean(axis=0))):.3f})")
    print(f"  {'place':18s} {'u skill':>8s} {'v skill':>8s} {'mean flow NOAA':>16s} {'fit':>14s} {'tides only':>14s}")
    for name, (lon, lat) in PLACES.items():
        k = int(np.argmin(np.hypot((c.lon - lon) * np.cos(np.radians(lat)), c.lat - lat)))
        mean = lambda d: f"{np.hypot(d['u'][:, k].mean(), d['v'][:, k].mean()):.3f} m/s"  # noqa: E731
        print(f"  {name:18s} {skill(po['u'], truth['u'])[k]:8.3f} {skill(po['v'], truth['v'])[k]:8.3f} "
              f"{mean(truth):>16s} {mean(po):>14s} {mean(pt):>14s}")
    k = int(np.argmin(np.hypot((c.lon - PLACES["SF tide gauge"][0]) * 0.79, c.lat - PLACES["SF tide gauge"][1])))
    names = list(full.names)
    amp = {n: float(np.hypot(*full.coef["level"][2 + 2 * names.index(n): 4 + 2 * names.index(n), k])) for n in NOAA_SF}
    print("  SF tide gauge, fitted amplitude against NOAA's published (m): "
          + ", ".join(f"{n} {amp[n]:.3f}/{NOAA_SF[n]:.3f}" for n in NOAA_SF))
    json.dump({"cut": cut, "days": days}, open(work / "extension_check.json", "w"))


if __name__ == "__main__":
    main()
