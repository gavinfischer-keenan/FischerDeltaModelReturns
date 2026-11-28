"""NOAA's Snow Data Assimilation System (SNODAS, National Operational
Hydrologic Remote Sensing Center), daily through NSIDC: snow modelled with
the day's weather and corrected to satellite, airborne and ground
observations, on a 30-arc-second grid (about 1 km). A national adapter (US).
No login.

Stored under source `snodas`, site the area's name, as the mean over the
area's cells:

- `snowmelt_24h` (mm): melt leaving the bottom of the snowpack over the 24
  hours to 06:00 UTC (product 1044), stored at that time; SNODAS gives none
  where there is no snowpack, which is taken as nothing melted;
- `snow_water_equivalent` (mm): the snowpack's water at 06:00 UTC
  (product 1034).

Both are modelled, not measured: SNODAS is the National Weather Service's
own snow model, nudged to what is observed.

References: ref:snodas (references.toml).
"""

from __future__ import annotations

import gzip
import io
import tarfile
import time

import numpy as np

from . import http
from .areas import extent, members

SOURCE = "snodas"
URL = "https://noaadata.apps.nsidc.org/NOAA/G02158/masked/{y}/{m:02d}_{mon}/SNODAS_{y}{m:02d}{d:02d}.tar"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PRODUCTS = {"1044": ("snowmelt_24h", 100_000.0), "1034": ("snow_water_equivalent", 1_000.0)}  # code: (ours, raw units per metre)
NO_DATA = -9999


def _get(url: str) -> bytes:
    return http.request(url, timeout=300)


def header(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def read(tar_bytes: bytes) -> dict[str, tuple[np.ndarray, dict]]:
    """Each wanted product's grid (mm) and its header, from one day's tar."""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        names = tf.getnames()
        for code, (p, per_m) in PRODUCTS.items():
            dat = next((n for n in names if f"ssmv1{code}" in n and n.endswith(".dat.gz")), None)
            txt = next((n for n in names if f"ssmv1{code}" in n and n.endswith(".txt.gz")), None)
            if not dat or not txt:
                continue
            h = header(gzip.decompress(tf.extractfile(txt).read()).decode("ascii", "replace"))
            rows, cols = int(h["number of rows"]), int(h["number of columns"])
            raw = np.frombuffer(gzip.decompress(tf.extractfile(dat).read()), dtype=">i2").reshape(rows, cols).astype(float)
            raw[raw == NO_DATA] = np.nan
            out[p] = (raw / per_m * 1000.0, h)
    # SNODAS gives no melt where there is no snowpack: there, nothing melted.
    if "snowmelt_24h" in out and "snow_water_equivalent" in out:
        melt, h = out["snowmelt_24h"]
        swe = out["snow_water_equivalent"][0]
        out["snowmelt_24h"] = (np.where(np.isnan(melt) & (swe == 0), 0.0, melt), h)
    return out


def axes(h: dict[str, str]) -> tuple[np.ndarray, np.ndarray]:
    """Latitude by row and longitude by column, cell centres (the header's
    benchmark is the first cell's centre)."""
    rows, cols = int(h["number of rows"]), int(h["number of columns"])
    lat0, lon0 = float(h["benchmark y-axis coordinate"]), float(h["benchmark x-axis coordinate"])
    dy, dx = float(h["y-axis resolution"]), float(h["x-axis resolution"])
    return lat0 - dy * np.arange(rows), lon0 + dx * np.arange(cols)


class SnodasAdapter:
    def __init__(self, store, get=_get) -> None:
        self.store = store
        self.get = get

    def fetch(self, areas: list[dict], start: int, end: int) -> int:
        from fdmr_store import Value

        n = 0
        errors: list[str] = []
        day = start // 86_400 * 86_400
        while day <= end:
            t = day + 6 * 3600
            # Done only when every area has it, so a newly added area is filled in (#91).
            if all((self.store.latest(SOURCE, a["site"], "snow_water_equivalent") or (0, 0))[0] >= t for a in areas):
                day += 86_400
                continue
            y, m, d = time.gmtime(day)[:3]
            try:
                got = read(self.get(URL.format(y=y, m=m, d=d, mon=MONTHS[m - 1])))
            except Exception as e:  # noqa: BLE001
                if http.not_posted(e) and day >= end // 86_400 * 86_400 - 2 * 86_400:
                    print(f"snodas {y}-{m:02d}-{d:02d} not posted yet", flush=True)
                    break  # the newest days come in order: none later is posted either
                # Missing or failed: skipped, named, tried again next run, never holding back the days after (#91).
                errors.append(f"{y}-{m:02d}-{d:02d}: {e}")
                day += 86_400
                continue
            w, s, e, nn = extent(areas)
            for p, (grid, h) in got.items():
                lat, lon = axes(h)
                rows = np.nonzero((lat >= s) & (lat <= nn))[0]
                cols = np.nonzero((lon >= w) & (lon <= e))[0]
                if not rows.size or not cols.size:
                    continue
                sub = grid[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]  # only the areas' corner of the country
                la, lo = np.meshgrid(lat[rows[0]:rows[-1] + 1], lon[cols[0]:cols[-1] + 1], indexing="ij")
                for a in areas:
                    v = sub[members(la, lo, a)]
                    v = v[np.isfinite(v)]
                    if v.size:
                        n += self.store.put_values(SOURCE, a["site"], p, "mm", [Value(t, float(v.mean()), None)])
            day += 86_400
        if errors:
            raise http.PartialFetch(n, errors)
        return n
