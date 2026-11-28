"""Where the water is, seen from orbit, and where it is new (issue #14:
levee breaches and flooding; #15: the bypasses; overlays.toml [water_extent];
fdmr_adapters.opera).

Each radar pass's water map (OPERA DSWx-S1, 30 m) is laid on one UTM grid per
area (a box): overlapping tiles fill each other's gaps. For each pass and
area: how much was seen, how much was water, and how much was **new** water:
water now where, in the same season of earlier years (`baseline_years`, within
`baseline_window_days` of the date; at least `min_prior` of those passes
seeing the pixel), it was water less than `new_below` of the time. Many fields
and islands are flooded on purpose every winter (rice straw, wildlife), so
water alone means nothing, and neither does water that was not there in a dry
autumn; water where the same season has not had any is the sign of a breach
or a flood. New water joined into patches of at least `min_patch_km2` is listed
with where it is. New water that was also dry on the previous pass is
**sudden** (`sudden_new_km2`, `sudden_patches`): new water that stays pass
after pass is more likely a lasting change, such as a restored wetland,
than a breach. A sign to look at, not a finding: the radar can mistake
some wet fields and smooth ground for water.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from fdmr_adapters.opera import NOT_SEEN
from fdmr_geo import lonlat_from_utm, utm
from fdmr_store.tiles import TileCache

PIXEL_KM2 = 0.03 * 0.03  # km2: one 30 m pixel of OPERA DSWx-S1 (ref:opera_dswx_s1)
MIN_SEEN = 0.2  # a pass counts only if it saw at least this share of the area (FDMR's choice)
MAX_PATCHES = 10  # the largest this many patches of new water are listed per pass (FDMR's choice)
PASS_S = 3600  # tiles within this of each other, s, are one radar pass (FDMR's choice: a pass takes minutes)


def _grid(box: tuple[float, float, float, float], zone: int, dx: float):
    """A UTM grid (zone, dx metres) over a box: its origin and shape, aligned to dx."""
    w, s, e, n = box
    xs, ys = utm(np.array([w, e, w, e]), np.array([s, s, n, n]), zone)
    x0 = np.floor(np.min(xs) / dx) * dx
    y0 = np.ceil(np.max(ys) / dx) * dx
    cols = int(np.ceil((np.max(xs) - x0) / dx))
    rows = int(np.ceil((y0 - np.min(ys)) / dx))
    return x0, y0, rows, cols


def _inside(box, zone, x0, y0, rows, cols, dx):
    """Which grid cells' centres are inside the box (longitude, latitude)."""
    xs = x0 + (np.arange(cols) + 0.5) * dx
    ys = y0 - (np.arange(rows) + 0.5) * dx
    ee, nn = np.meshgrid(xs, ys)
    lo, la = lonlat_from_utm(ee, nn, zone)
    w, s, e, n = box
    return (lo >= w) & (lo <= e) & (la >= s) & (la <= n)


def mosaics(tiles: list[dict], box, zone: int, dx: float) -> list[tuple[int, np.ndarray]]:
    """Each pass (tiles within an hour of each other) as one grid over the box:
    1 water, 0 not, 255 not seen."""
    x0, y0, rows, cols = _grid(box, zone, dx)
    passes: dict[int, list[dict]] = {}
    for tl in sorted(tiles, key=lambda t: t["t"]):
        key = next((k for k in passes if abs(k - tl["t"]) <= PASS_S), tl["t"])
        passes.setdefault(key, []).append(tl)
    out = []
    for t, group in sorted(passes.items()):
        g = np.full((rows, cols), NOT_SEEN, np.uint8)
        for tl in group:
            if tl["zone"] != zone or tl["dx"] != dx:
                continue
            tx0 = tl["x0"] + tl["col0"] * dx
            ty0 = tl["y0"] - tl["row0"] * dx
            dc, dr = int(round((tx0 - x0) / dx)), int(round((y0 - ty0) / dx))
            w = tl["water"]
            r0, c0 = max(0, dr), max(0, dc)
            r1, c1 = min(rows, dr + w.shape[0]), min(cols, dc + w.shape[1])
            if r0 >= r1 or c0 >= c1:
                continue
            src = w[r0 - dr:r1 - dr, c0 - dc:c1 - dc]
            dst = g[r0:r1, c0:c1]
            fill = (dst == NOT_SEEN) & (src != NOT_SEEN)
            dst[fill] = src[fill]
        out.append((t, g))
    return out


def area_record(tiles: list[dict], area: dict, cfg: dict, baseline: list[dict] | None = None) -> dict | None:
    from scipy.ndimage import label

    if not tiles:
        return None
    zone, dx = tiles[0]["zone"], tiles[0]["dx"]
    box = tuple(area["box"])
    x0, y0, rows, cols = _grid(box, zone, dx)
    inside = _inside(box, zone, x0, y0, rows, cols, dx)
    water_n = np.zeros((rows, cols), np.uint16)
    seen_n = np.zeros((rows, cols), np.uint16)
    # The same season in earlier years: how often each pixel was water then.
    for _, g in mosaics(baseline or [], box, zone, dx):
        seen = (g != NOT_SEEN) & inside
        if seen.sum() >= MIN_SEEN * inside.sum():
            water_n += ((g == 1) & inside).astype(np.uint16)
            seen_n += seen.astype(np.uint16)
    passes = []
    prev = None
    for t, g in mosaics(tiles, box, zone, dx):
        seen = (g != NOT_SEEN) & inside
        water = (g == 1) & inside
        if seen.sum() < MIN_SEEN * inside.sum():  # a pass that saw little of the area tells little
            continue
        freq = np.where(seen_n > 0, water_n / np.maximum(seen_n, 1), 1.0)
        new = water & (seen_n >= cfg["min_prior"]) & (freq < cfg["new_below"])
        rec = {"t": int(t), "seen_km2": round(float(seen.sum()) * PIXEL_KM2, 1), "water_km2": round(float(water.sum()) * PIXEL_KM2, 1),
               "new_water_km2": round(float(new.sum()) * PIXEL_KM2, 2), "baseline_passes": int(seen_n[inside].max())}
        def patches_of(mask):
            out = []
            if mask.any():
                lab, _ = label(mask, structure=np.ones((3, 3), bool))
                sizes = np.bincount(lab.ravel())
                for k in np.nonzero(sizes * PIXEL_KM2 >= cfg["min_patch_km2"])[0]:
                    if k == 0:
                        continue
                    rr, cc = np.nonzero(lab == k)
                    lo, la = lonlat_from_utm(x0 + (cc.mean() + 0.5) * dx, y0 - (rr.mean() + 0.5) * dx, zone)
                    out.append({"km2": round(float(sizes[k]) * PIXEL_KM2, 2), "lon": round(float(lo), 4), "lat": round(float(la), 4)})
            return sorted(out, key=lambda p: -p["km2"])[:MAX_PATCHES]

        patches = patches_of(new)
        if patches:
            rec["new_patches"] = patches
        if prev is not None:
            sudden = new & (prev == 0)
            rec["sudden_new_km2"] = round(float(sudden.sum()) * PIXEL_KM2, 2)
            sp = patches_of(sudden)
            if sp:
                rec["sudden_patches"] = sp
        prev = g
        passes.append(rec)
    return {"area": area["name"], "box": list(box), "passes": passes} if passes else None


def summary(cfg: dict, cache: Path, now: int) -> dict | None:
    we = cfg.get("water_extent")
    if not we or not cache.exists():
        return None
    recent_from = now - we["days"] * 86_400
    # The summary changes only when a tile comes or goes, or one moves from
    # the recent window into the baseline: kept beside the tiles until then (#35).
    tc = TileCache(cache)
    ids = tc.ids()
    key = hashlib.sha256(json.dumps([we, tc.fingerprint(), [tc.time(i) >= recent_from for i in ids]], sort_keys=True).encode()).hexdigest()
    kept = cache / "summary.json"
    if kept.exists():
        try:
            k = json.loads(kept.read_text(encoding="utf-8"))
            if k.get("key") == key:
                return k["summary"]
        except ValueError:
            pass
    tiles, baseline = [], []
    for _, z in tc.items():
        tl = {"t": int(z["t"]), "water": z["water"], "row0": int(z["row0"]), "col0": int(z["col0"]),
              "x0": float(z["x0"]), "y0": float(z["y0"]), "dx": float(z["dx"]), "zone": int(z["zone"])}
        (tiles if tl["t"] >= recent_from else baseline).append(tl)
    areas = [r for r in (area_record(tiles, a, we, baseline) for a in we.get("areas", [])) if r]
    out = {"source": "NASA JPL OPERA DSWx-S1 (Sentinel-1 radar, 30 m), PO.DAAC", "new_below": we["new_below"],
           "min_prior": we["min_prior"], "min_patch_km2": we["min_patch_km2"],
           "baseline_years": we["baseline_years"], "baseline_window_days": we["baseline_window_days"], "areas": areas} if areas else None
    tmp = kept.with_name(kept.name + ".tmp")
    tmp.write_text(json.dumps({"key": key, "summary": out}), encoding="utf-8")
    tmp.replace(kept)
    return out


def windows(cfg: dict, now: int) -> list[tuple[int, int]]:
    """The passes to keep: the last `days`, and the same season (`baseline_window_days`
    either side of the date) in each of the last `baseline_years` years."""
    days = cfg["days"] * 86_400
    w = cfg["baseline_window_days"] * 86_400
    out = [(now - days, now)]
    for y in range(1, cfg["baseline_years"] + 1):
        c = now - y * 365 * 86_400
        out.append((c - w, c + w))
    return out
