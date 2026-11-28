"""NASA JPL's OPERA surface-water maps (DSWx), 30 m on the Sentinel-2 (MGRS)
tiles in UTM, through NASA PO.DAAC: DSWx-S1 from Sentinel-1's radar, which
sees through cloud, every few days. A global adapter. Finding passes is open
(NASA's CMR); downloading needs the Earthdata login token
(~/.config/fdmr/earthdata-token).

Of each pass only its water classification (layer B01_WTR: 0 not water,
1 open water, 2 partial surface water; 252 and above snow, cloud, ocean mask
or no data) over the boxes asked for is kept, a tile per pass in a tile cache
(fdmr_store.tiles): `water` (1 where open or partial water, 0 not, 255 where
nothing could be seen), the tile's UTM origin, pixel size and zone, the crop's
offset (`row0`, `col0`), the MGRS `tile` and the pass time `t`. When a pass was processed more than once, only the
latest processing is used.

References: ref:opera_dswx_s1 (references.toml).
"""

from __future__ import annotations

import time
from calendar import timegm
from pathlib import Path
from urllib.parse import urlencode

import numpy as np

from fdmr_geo import utm
from fdmr_store.tiles import TileCache

from .ecostress import TOKEN_FILE, _download, read_tif
from . import http
from .http import HttpJsonClient, JsonClient

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
S1 = "OPERA_L3_DSWX-S1_V1"
NOT_SEEN = 255
PER_RUN = 24  # tiles downloaded per fetch at most, newest first


def search(short_name: str, box: tuple[float, float, float, float], start: int, end: int, client: JsonClient | None = None) -> list[dict]:
    """Passes over a box: {id, tile, t (acquisition), url of B01_WTR}, the latest
    processing of each tile and acquisition."""
    client = client or HttpJsonClient()
    stamp = lambda x: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(x))  # noqa: E731
    q = urlencode({"short_name": short_name, "bounding_box": ",".join(str(v) for v in box),
                   "temporal": f"{stamp(start)},{stamp(end)}", "page_size": 500, "sort_key": "-start_date"})
    best: dict[tuple[str, int], dict] = {}
    for e in client.get_json(f"{CMR}?{q}")["feed"]["entry"]:
        url = next((lk["href"] for lk in e.get("links", []) if lk["href"].startswith("https") and lk["href"].endswith("_B01_WTR.tif")), None)
        parts = e["title"].split("_")  # OPERA_L3_DSWx-S1_T10SEG_<acquired>_<processed>_...
        if not url or len(parts) < 6:
            continue
        tile, acquired, processed = parts[3], parts[4], parts[5]
        t = timegm(time.strptime(acquired, "%Y%m%dT%H%M%SZ"))
        key = (tile, t)
        if key not in best or processed > best[key]["processed"]:
            best[key] = {"id": f"{tile}_{acquired}", "tile": tile, "t": t, "url": url, "processed": processed}
    return sorted(best.values(), key=lambda g: -g["t"])


def crop_rows_cols(where: dict, shape: tuple[int, int], box: tuple[float, float, float, float]) -> tuple[int, int, int, int] | None:
    """The tile's rows and columns covering a box (west, south, east, north)."""
    w, s, e, n = box
    lons = np.array([w, e, w, e])
    lats = np.array([s, s, n, n])
    x, y = utm(lons, lats, where["zone"])
    c = (np.asarray(x) - where["x0"]) / where["dx"]
    r = (where["y0"] - np.asarray(y)) / where["dx"]
    r0, r1 = max(0, int(np.floor(r.min()))), min(shape[0], int(np.ceil(r.max())) + 1)
    c0, c1 = max(0, int(np.floor(c.min()))), min(shape[1], int(np.ceil(c.max())) + 1)
    return None if r0 >= r1 or c0 >= c1 else (r0, r1, c0, c1)


def classify(wtr: np.ndarray) -> np.ndarray:
    """OPERA's classes to 1 water, 0 not, NOT_SEEN where it could not see."""
    out = np.full(wtr.shape, NOT_SEEN, np.uint8)
    out[wtr == 0] = 0
    out[(wtr == 1) | (wtr == 2)] = 1
    return out


class OperaAdapter:
    def __init__(self, cache: Path, token: str | None = None, download=_download, client: JsonClient | None = None) -> None:
        self.tiles = TileCache(cache)
        self.token = token if token is not None else TOKEN_FILE.read_text(encoding="utf-8").strip()
        self.download = download
        self.client = client

    def fetch(self, box: tuple[float, float, float, float], windows: list[tuple[int, int]], per_run: int = PER_RUN,
              short_name: str = S1) -> int:
        """Keep the water over the box from the passes in any of `windows`
        ((start, end) pairs) not yet kept, newest first, at most `per_run`;
        drop kept passes outside every window. Returns how many tiles were kept."""
        inside = lambda t: any(a <= t <= b for a, b in windows)  # noqa: E731
        for old in self.tiles.ids():
            try:
                t_old = self.tiles.time(old)
            except Exception:
                t_old = -1
            if not inside(t_old):
                self.tiles.drop(old)
        todo = [g for a, b in windows for g in search(short_name, box, a, b, self.client)]
        todo = sorted({g["id"]: g for g in todo}.values(), key=lambda g: -g["t"])
        kept = 0
        errors: list[str] = []
        for g in [g for g in todo if not self.tiles.has(g["id"])][:per_run]:
            try:
                wtr, where = read_tif(self.download(g["url"], self.token))
            except Exception as e:  # noqa: BLE001  one tile's failure does not stop the rest; named, tried again next run (#91)
                errors.append(f"{g['id']}: {e}")
                continue
            rc = crop_rows_cols(where, wtr.shape, box)
            if rc is None:
                continue
            r0, r1, c0, c1 = rc
            self.tiles.put(g["id"], water=classify(wtr[r0:r1, c0:c1]), t=g["t"], tile=g["tile"], row0=r0, col0=c0, **where)
            kept += 1
        if errors:
            raise http.PartialFetch(kept, errors)
        return kept
