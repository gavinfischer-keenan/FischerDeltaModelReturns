"""Satellite turbidity against the turbidity stations (issue #16), before any
use of it: every satellite source is scored against stations first (#9).

Satellite: NASA's Harmonized Landsat Sentinel-2 surface reflectance, v2.0
(HLSL30 from Landsat 8/9, HLSS30 from Sentinel-2; 30 m; ref:hls2), red (B04)
and near-infrared (B05 on Landsat, B8A on Sentinel-2) with its Fmask.
Turbidity, FNU, by Dogliotti et al. (2015, ref:dogliotti2015):
T = A rho / (1 - rho / C), red A 228.1, C 0.1641; NIR A 3078.9, C 0.2112; red
where red rho < 0.05, NIR where red rho >= 0.07, blended linearly between
(coefficients as ACOLITE's defaults, data/Shared/algorithms/Dogliotti). HLS's
red and NIR bands centre at 655-665 and 865 nm, not the algorithm's 645 and
859 nm; HLS's atmospheric correction is made for land; both are part of what
this check measures.

A station's pass counts where the 3x3 pixels around it are all water, with no
cloud, cloud shadow, cirrus or cloud adjacency (Fmask bits 0-3; water bit 5),
and the station has a reading within an hour of the pass. Stations: the
region's overlay stations with turbidity in the store (CDEC, NTU or FNU,
compared as read).

    python tools/validation/satellite_turbidity/hls_check.py REGION_DIR STORE OUT_DIR [--since 2026-07-01] [--cloud 40] [--tiles T10SFH,T10SEH]

Writes OUT_DIR/matchups.csv and prints the scores. Downloads each pass's
three bands to OUT_DIR/tmp and deletes them once read.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import tomllib
from calendar import timegm
from pathlib import Path
from urllib.parse import urlencode

import numpy as np

from fdmr_adapters.ecostress import TOKEN_FILE, _download, read_tif, utm
from fdmr_adapters.http import HttpJsonClient
from fdmr_store import Store

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
NIR = {"HLSL30": "B05", "HLSS30": "B8A"}
SCALE = 1e-4  # HLS reflectance
A_RED, C_RED, A_NIR, C_NIR, LO, HI = 228.1, 0.1641, 3078.9, 0.2112, 0.05, 0.07  # Dogliotti et al. 2015
BAD = 0b1111  # Fmask: cirrus, cloud, adjacent to cloud or shadow, cloud shadow
WATER = 1 << 5
MATCH_S = 3600


def dogliotti(red: float, nir: float) -> float:
    t_red = A_RED * red / (1 - red / C_RED)
    t_nir = A_NIR * nir / (1 - nir / C_NIR)
    if red < LO:
        return t_red
    if red >= HI:
        return t_nir
    w = (red - LO) / (HI - LO)
    return (1 - w) * t_red + w * t_nir


def passes(box, start: int, end: int, cloud: float) -> list[dict]:
    stamp = lambda x: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(x))  # noqa: E731
    out = []
    for short in NIR:
        page = 1
        while True:
            q = urlencode({"short_name": short, "version": "2.0", "bounding_box": ",".join(map(str, box)),
                           "temporal": f"{stamp(start)},{stamp(end)}", "page_size": 200, "page_num": page,
                           "cloud_cover": f"0,{cloud}"})
            entries = HttpJsonClient().get_json(f"{CMR}?{q}")["feed"]["entry"]
            for e in entries:
                links = {lk["href"].rsplit(".", 2)[-2]: lk["href"] for lk in e.get("links", [])
                         if lk["href"].startswith("https") and lk["href"].endswith(".tif")}
                need = ("B04", NIR[short], "Fmask")
                if all(k in links for k in need):
                    t = timegm(time.strptime(e["time_start"][:19], "%Y-%m-%dT%H:%M:%S"))
                    out.append({"id": e["title"], "short": short, "t": t, "urls": [links[k] for k in need]})
            if len(entries) < 200:
                break
            page += 1
    return sorted(out, key=lambda g: g["t"])


def station_reading(store: Store, site: str, param: str, t: int) -> float | None:
    vals = [v for v in store.values("cdec", site, param, t - MATCH_S, t + MATCH_S) if v.value is not None]
    if not vals:
        return None
    return min(vals, key=lambda v: abs(v.t - t)).value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("out")
    ap.add_argument("--since", default="2026-07-01")
    ap.add_argument("--cloud", type=float, default=40.0)
    ap.add_argument("--tiles", default="T10SFH,T10SEH", help="MGRS tiles to read (the Delta and Suisun: 20 stations)")
    args = ap.parse_args(argv)
    store = Store(args.store)
    out = Path(args.out)
    (out / "tmp").mkdir(parents=True, exist_ok=True)
    cfg = tomllib.load(open(Path(args.region) / "overlays.toml", "rb"))
    stations = []
    for s in cfg.get("stations", []):
        for param in ("turbidity_fnu", "turbidity_ntu"):
            if store.latest("cdec", s["site"], param):
                stations.append({**s, "param": param})
                break
    box = (min(s["lon"] for s in stations) - 0.01, min(s["lat"] for s in stations) - 0.01,
           max(s["lon"] for s in stations) + 0.01, max(s["lat"] for s in stations) + 0.01)
    start = timegm(time.strptime(args.since, "%Y-%m-%d"))
    tiles = set(args.tiles.split(","))
    found = [g for g in passes(box, start, int(time.time()), args.cloud) if g["id"].split(".")[2] in tiles]
    print(f"{len(stations)} turbidity stations; {len(found)} HLS passes since {args.since} (cloud <= {args.cloud}%)", flush=True)
    token = TOKEN_FILE.read_text().strip()
    rows = []
    for k, g in enumerate(found, 1):
        try:
            bands = [read_tif(_download(u, token)) for u in g["urls"]]
        except Exception as e:  # noqa: BLE001 - one pass failing does not stop the check
            print(f"  {g['id']}: not read ({e})", file=sys.stderr)
            continue
        (red, where), (nir, _), (fmask, _) = bands
        kept = 0
        for s in stations:
            x, y = utm(s["lon"], s["lat"], where["zone"])
            c = int((float(x) - where["x0"]) // where["dx"])
            r = int((where["y0"] - float(y)) // where["dx"])
            if not (1 <= r < red.shape[0] - 1 and 1 <= c < red.shape[1] - 1):
                continue
            fm = fmask[r - 1:r + 2, c - 1:c + 2].astype(int)
            rr, nn = red[r - 1:r + 2, c - 1:c + 2], nir[r - 1:r + 2, c - 1:c + 2]
            if (fm & BAD).any() or not ((fm & WATER) > 0).all() or (rr <= -9999).any() or (nn <= -9999).any():
                continue
            rho_r, rho_n = float(np.median(rr)) * SCALE, float(np.median(nn)) * SCALE
            meas = station_reading(store, s["site"], s["param"], g["t"])
            if meas is None or rho_r <= 0:
                continue
            rows.append({"pass": g["id"], "sensor": g["short"], "t": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(g["t"])),
                         "site": s["site"], "unit": s["param"][-3:].upper(), "red": round(rho_r, 4), "nir": round(rho_n, 4),
                         "satellite": round(dogliotti(rho_r, rho_n), 2), "station": meas})
            kept += 1
        print(f"  {k}/{len(found)} {g['id']}: {kept} stations", flush=True)
    with open(out / "matchups.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pass", "sensor", "t", "site", "unit", "red", "nir", "satellite", "station"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    score(rows)
    return 0


def score(rows: list[dict]) -> None:
    ok = [r for r in rows if r["station"] > 0 and r["satellite"] > 0]
    if not ok:
        print("no matchups")
        return
    s, m = np.array([r["satellite"] for r in ok]), np.array([r["station"] for r in ok])
    from scipy.stats import spearmanr

    d = np.log10(s / m)
    print(f"matchups {len(ok)} at {len({r['site'] for r in ok})} stations on {len({r['pass'] for r in ok})} passes")
    print(f"station turbidity {np.median(m):.1f} median ({m.min():.1f}-{m.max():.1f}); satellite {np.median(s):.1f} ({s.min():.1f}-{s.max():.1f})")
    print(f"Spearman r {spearmanr(s, m)[0]:.2f}; satellite/station median ratio {10 ** np.median(d):.2f}; "
          f"log10 differences: mean {d.mean():+.2f}, MAD {np.median(np.abs(d - np.median(d))):.2f}; within a factor 2: {np.mean(np.abs(d) <= np.log10(2)):.0%}")
    for sensor in sorted({r["sensor"] for r in ok}):
        sub = [r for r in ok if r["sensor"] == sensor]
        ds = np.log10(np.array([r["satellite"] for r in sub]) / np.array([r["station"] for r in sub]))
        print(f"  {sensor}: {len(sub)} matchups, median ratio {10 ** np.median(ds):.2f}, Spearman "
              f"{spearmanr([r['satellite'] for r in sub], [r['station'] for r in sub])[0]:.2f}")


if __name__ == "__main__":
    sys.exit(main())
