"""NASA's ECOSTRESS radiometer on the International Space Station: surface
temperature at 70 m, tiled on the Sentinel-2 (MGRS) grid in UTM, through NASA
LP DAAC (collection ECO_L2T_LSTE, version 003). A global adapter. Finding
passes is open (NASA's CMR catalogue); downloading needs a NASA Earthdata
login token, kept in ~/.config/fdmr/earthdata-token.

Of each tile only the water is kept: the water-surface temperature (layer
WST, kelvin, kept as degC) where the product's own water mask says water, its
cloud mask says clear, its quality flags (QC bits 0-1) say best or nominal,
and no cloud lies within CLOUD_BUFFER pixels. That last guard is ours: checked
26 Nov 2026 against the stations, pixels beside flagged cloud read up to 6
degC cold (thin cloud and fog at the edges the mask misses). A tile with no clear water is not downloaded past its two small
masks. Each kept tile is a small `.npz` in the cache folder: the water
pixels' rows, columns and temperatures, the tile's UTM origin, pixel size and
zone, and the pass time.

The temperature is the water's skin, its top fraction of a millimetre, at the
moment of the pass. The station's orbit drifts, so passes come at any hour.
The skin differs from a sensor's reading a metre or so down by a few tenths
of a degree, more on calm, sunny afternoons.
"""

from __future__ import annotations

import io
import math
import time
import urllib.request
from calendar import timegm
from pathlib import Path
from urllib.parse import urlencode

import numpy as np

from .http import USER_AGENT, HttpJsonClient, JsonClient

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
SHORT_NAME, VERSION = "ECO_L2T_LSTE", "003"
LAYERS = ("water", "cloud", "QC", "WST")
TOKEN_FILE = Path.home() / ".config" / "fdmr" / "earthdata-token"
CLOUD_BUFFER = 5  # pixels (350 m) kept clear of any flagged cloud
PER_RUN = 12  # new tiles downloaded per fetch at most, newest first, so a backlog never holds up a cycle
KELVIN = 273.15


def utm(lon, lat, zone: int) -> tuple[np.ndarray, np.ndarray]:
    """WGS84 longitude and latitude (degrees) to UTM easting and northing (m),
    northern hemisphere: the transverse Mercator projection by Krüger's series
    to the third order in n (Karney 2011, J. Geodesy 85:475, equations 7-11),
    good to well under a metre within the zone."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    n = f / (2 - f)
    big_a = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    alpha = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16, 13 * n ** 2 / 48 - 3 * n ** 3 / 5, 61 * n ** 3 / 240)
    e = math.sqrt(f * (2 - f))
    phi = np.radians(np.asarray(lat, dtype=float))
    lam = np.radians(np.asarray(lon, dtype=float)) - math.radians(zone * 6 - 183)
    t = np.sinh(np.arctanh(np.sin(phi)) - e * np.arctanh(e * np.sin(phi)))
    xi = np.arctan2(t, np.cos(lam))
    eta = np.arctanh(np.sin(lam) / np.sqrt(1 + t * t))
    east = eta + sum(al * np.cos(2 * j * xi) * np.sinh(2 * j * eta) for j, al in enumerate(alpha, 1))
    north = xi + sum(al * np.sin(2 * j * xi) * np.cosh(2 * j * eta) for j, al in enumerate(alpha, 1))
    return 500_000.0 + k0 * big_a * east, k0 * big_a * north


def read_tif(data: bytes) -> tuple[np.ndarray, dict]:
    """A tile's raster and where it lies: UTM `x0`, `y0` (its top-left corner),
    pixel size `dx` (m) and `zone`, from its GeoTIFF tags."""
    import tifffile

    with tifffile.TiffFile(io.BytesIO(data)) as tf:
        page = tf.pages[0]
        tags = page.tags
        tie = tags["ModelTiepointTag"].value
        scale = tags["ModelPixelScaleTag"].value
        keys = tags["GeoKeyDirectoryTag"].value
        geo = {keys[i]: keys[i + 3] for i in range(4, len(keys), 4)}
        epsg = geo.get(3072)  # ProjectedCSTypeGeoKey: 326zz is WGS84 UTM zone zz north
        if not epsg or not 32601 <= epsg <= 32660:
            raise ValueError(f"not a northern UTM tile (EPSG {epsg})")
        return page.asarray(), {"x0": float(tie[3]), "y0": float(tie[4]), "dx": float(scale[0]), "zone": epsg - 32600}


def water_pixels(water: np.ndarray, cloud: np.ndarray, qc: np.ndarray | None, wst: np.ndarray | None,
                 buffer: int = CLOUD_BUFFER) -> np.ndarray:
    """Pixels kept: water, clear with no cloud within `buffer` pixels, quality
    best or nominal, with a temperature."""
    near_cloud = cloud == 1
    if buffer and near_cloud.any():
        from scipy.ndimage import binary_dilation

        near_cloud = binary_dilation(near_cloud, np.ones((2 * buffer + 1, 2 * buffer + 1), bool))
    ok = (water == 1) & (cloud == 0) & ~near_cloud
    if qc is not None:
        ok &= (qc & 3) <= 1
    if wst is not None:
        ok &= np.isfinite(wst)
    return ok


def search(box: tuple[float, float, float, float], start: int, end: int, client: JsonClient | None = None) -> list[dict]:
    """Passes over a box (west, south, east, north) between two times: each
    tile's `id`, pass time `t` (s) and its layers' download `urls`."""
    client = client or HttpJsonClient()
    stamp = lambda x: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(x))
    q = urlencode({"short_name": SHORT_NAME, "version": VERSION, "bounding_box": ",".join(str(v) for v in box),
                   "temporal": f"{stamp(start)},{stamp(end)}", "page_size": 500, "sort_key": "start_date"})
    out = []
    for e in client.get_json(f"{CMR}?{q}")["feed"]["entry"]:
        urls = {}
        for link in e.get("links", []):
            h = link["href"]
            for layer in LAYERS:
                if h.startswith("https") and h.endswith(f"_{layer}.tif"):
                    urls[layer] = h
        if len(urls) == len(LAYERS):
            out.append({"id": e["title"], "t": timegm(time.strptime(e["time_start"][:19], "%Y-%m-%dT%H:%M:%S")), "urls": urls})
    return out


def _download(url: str, token: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    req.add_unredirected_header("Authorization", f"Bearer {token}")  # not passed on to the storage it redirects to
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


class EcostressAdapter:
    def __init__(self, cache: Path, token: str | None = None, download=_download, client: JsonClient | None = None) -> None:
        self.cache = Path(cache)
        self.token = token if token is not None else TOKEN_FILE.read_text(encoding="utf-8").strip()
        self.download = download
        self.client = client

    def fetch(self, box: tuple[float, float, float, float], start: int, end: int, keep_s: int, per_run: int = PER_RUN) -> int:
        """Keep the water of the passes over the box since `start` not already
        kept, newest first and at most `per_run` of them, and drop what is
        older than `keep_s`; returns how many water pixels were kept."""
        self.cache.mkdir(parents=True, exist_ok=True)
        for old in self.cache.glob("*"):
            if old.stat().st_mtime < end - keep_s - 86_400 and old.suffix in (".npz", ".none"):
                old.unlink()
        kept = 0
        todo = [g for g in search(box, start, end, self.client)
                if not any((self.cache / f"{g['id']}{s}").exists() for s in (".npz", ".none"))]
        for g in sorted(todo, key=lambda g: -g["t"])[:per_run]:
            water, where = read_tif(self.download(g["urls"]["water"], self.token))
            cloud, _ = read_tif(self.download(g["urls"]["cloud"], self.token))
            if not water_pixels(water, cloud, None, None).any():
                (self.cache / f"{g['id']}.none").touch()
                continue
            qc, _ = read_tif(self.download(g["urls"]["QC"], self.token))
            wst, _ = read_tif(self.download(g["urls"]["WST"], self.token))
            ok = water_pixels(water, cloud, qc, wst)
            rows, cols = np.nonzero(ok)
            np.savez_compressed(self.cache / f"{g['id']}.npz", rows=rows.astype(np.uint16), cols=cols.astype(np.uint16),
                                temperature=(wst[ok] - KELVIN).astype(np.float32), shape=np.array(ok.shape), t=g["t"], **where)
            kept += int(ok.sum())
        return kept


class Tile:
    """A kept tile, to look up: the temperature at a point where the pixel and
    its eight neighbours are all kept water (no land or cloud mixed in)."""

    def __init__(self, path: Path) -> None:
        z = np.load(path)
        self.t, self.zone = int(z["t"]), int(z["zone"])
        self.x0, self.y0, self.dx = float(z["x0"]), float(z["y0"]), float(z["dx"])
        self.grid = np.full(tuple(z["shape"]), np.nan, dtype=np.float32)
        self.grid[z["rows"], z["cols"]] = z["temperature"]

    def pure(self, lon, lat) -> np.ndarray:
        """Each point's temperature (degC), NaN where its 3 x 3 pixels are not
        all kept water."""
        e, n = utm(lon, lat, self.zone)
        c = np.floor((np.atleast_1d(e) - self.x0) / self.dx).astype(int)
        r = np.floor((self.y0 - np.atleast_1d(n)) / self.dx).astype(int)
        out = np.full(c.shape, np.nan)
        inside = (r >= 1) & (c >= 1) & (r < self.grid.shape[0] - 1) & (c < self.grid.shape[1] - 1)
        for i in np.nonzero(inside)[0]:
            w = self.grid[r[i] - 1:r[i] + 2, c[i] - 1:c[i] + 2]
            if np.isfinite(w).all():
                out[i] = float(w[1, 1])
        return out

    def near(self, lon: float, lat: float, reach: int = 2) -> float | None:
        """The temperature at the nearest pixel, within `reach` pixels of the
        point, whose 3 x 3 pixels are all kept water: for a sensor on a bank or
        a pier."""
        e, n = utm(lon, lat, self.zone)
        c0, r0 = int((float(e) - self.x0) // self.dx), int((self.y0 - float(n)) // self.dx)
        best = None
        for dr in range(-reach, reach + 1):
            for dc in range(-reach, reach + 1):
                r, c = r0 + dr, c0 + dc
                if not (1 <= r < self.grid.shape[0] - 1 and 1 <= c < self.grid.shape[1] - 1):
                    continue
                if np.isfinite(self.grid[r - 1:r + 2, c - 1:c + 2]).all() and (best is None or dr * dr + dc * dc < best[0]):
                    best = (dr * dr + dc * dc, float(self.grid[r, c]))
        return None if best is None else best[1]
