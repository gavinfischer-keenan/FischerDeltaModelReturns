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

The land is kept only as means over named places (`fetch_land`): the land
surface temperature (layer LST, kept as degC) over the clear land within a
distance of a place's points (water, cloud within CLOUD_BUFFER pixels and
poor quality left out), stored under source `ecostress`, site the place's
name, parameter `land_surface_temperature`, at the pass time, when at least
MIN_LAND_PIXELS count. Land is a surface temperature too: on a sunny
afternoon tens of degrees above the air, on a clear night below it.

The temperature is the water's skin, its top fraction of a millimetre, at the
moment of the pass. The station's orbit drifts, so passes come at any hour.
The skin differs from a sensor's reading a metre or so down by a few tenths
of a degree, more on calm, sunny afternoons.

References: ref:ecostress_lst (references.toml).
"""

from __future__ import annotations

import io
import math
import time
from calendar import timegm
from pathlib import Path
from urllib.parse import urlencode

import numpy as np

from . import http
from .http import HttpJsonClient, JsonClient

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
SHORT_NAME, VERSION = "ECO_L2T_LSTE", "003"
LAYERS = ("water", "cloud", "QC", "WST", "LST")
NEEDED = ("water", "cloud", "QC")  # a pass without these is not used
MIN_LAND_PIXELS = 100  # about half a square kilometre of clear land
TOKEN_FILE = Path.home() / ".config" / "fdmr" / "earthdata-token"
CLOUD_BUFFER = 5  # pixels (350 m) kept clear of any flagged cloud
PER_RUN = 12  # new tiles downloaded per fetch at most, newest first, so a backlog never holds up a cycle
KELVIN = 273.15


def utm(lon, lat, zone: int) -> tuple[np.ndarray, np.ndarray]:
    """WGS84 longitude and latitude (degrees) to UTM easting and northing (m),
    northern hemisphere: the transverse Mercator projection by Krüger's series
    to the third order in n (Karney 2011, ref:karney2011, J. Geodesy 85:475, equations 7-11),
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


def lonlat_from_utm(east, north, zone: int) -> tuple[np.ndarray, np.ndarray]:
    """UTM easting and northing (m, northern hemisphere) back to WGS84
    longitude and latitude (degrees): the inverse of `utm`, by Krüger's series
    to the third order in n (Karney 2011, ref:karney2011, J. Geodesy 85:475, equations 15-21)."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    n = f / (2 - f)
    big_a = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    beta = (n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96, n ** 2 / 48 + n ** 3 / 15, 17 * n ** 3 / 480)
    delta = (2 * n - 2 * n ** 2 / 3 - 2 * n ** 3, 7 * n ** 2 / 3 - 8 * n ** 3 / 5, 56 * n ** 3 / 15)
    xi = np.asarray(north, dtype=float) / (k0 * big_a)
    eta = (np.asarray(east, dtype=float) - 500_000.0) / (k0 * big_a)
    xi_p = xi - sum(b * np.sin(2 * j * xi) * np.cosh(2 * j * eta) for j, b in enumerate(beta, 1))
    eta_p = eta - sum(b * np.cos(2 * j * xi) * np.sinh(2 * j * eta) for j, b in enumerate(beta, 1))
    chi = np.arcsin(np.sin(xi_p) / np.cosh(eta_p))
    lat = chi + sum(d * np.sin(2 * j * chi) for j, d in enumerate(delta, 1))
    lon = math.radians(zone * 6 - 183) + np.arctan2(np.sinh(eta_p), np.cos(xi_p))
    return np.degrees(lon), np.degrees(lat)


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
        if all(k in urls for k in NEEDED):
            out.append({"id": e["title"], "t": timegm(time.strptime(e["time_start"][:19], "%Y-%m-%dT%H:%M:%S")), "urls": urls})
    return out


RETRY_WAITS_S = (5, 20)  # waits before each retry of a download the server failed


def _download(url: str, token: str, sleep=time.sleep) -> bytes:
    """A file, with the token; a server error or a dropped connection is
    retried after each of RETRY_WAITS_S."""
    # The token is not passed on to the storage it redirects to (http.request's `bearer`).
    return http.request(url, bearer=token, timeout=300, sleep=sleep)


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
            if old.stat().st_mtime < end - keep_s - 86_400 and old.suffix in (".npz", ".none", ".land"):
                old.unlink()
        kept = 0
        todo = [g for g in search(box, start, end, self.client)
                if not any((self.cache / f"{g['id']}{s}").exists() for s in (".npz", ".none"))]
        for g in sorted(todo, key=lambda g: -g["t"])[:per_run]:
            try:
                kept += self._keep_water(g)
            except Exception as e:  # one tile's failure does not stop the rest; it is tried again next time
                print(f"ecostress {g['id']} FAILED {e}", flush=True)
        return kept

    def _keep_water(self, g: dict) -> int:
        water, where = read_tif(self.download(g["urls"]["water"], self.token))
        cloud, _ = read_tif(self.download(g["urls"]["cloud"], self.token))
        if "WST" not in g["urls"] or not water_pixels(water, cloud, None, None).any():
            (self.cache / f"{g['id']}.none").touch()
            return 0
        qc, _ = read_tif(self.download(g["urls"]["QC"], self.token))
        wst, _ = read_tif(self.download(g["urls"]["WST"], self.token))
        ok = water_pixels(water, cloud, qc, wst)
        rows, cols = np.nonzero(ok)
        np.savez_compressed(self.cache / f"{g['id']}.npz", rows=rows.astype(np.uint16), cols=cols.astype(np.uint16),
                            temperature=(wst[ok] - KELVIN).astype(np.float32), shape=np.array(ok.shape), t=g["t"], **where)
        return int(ok.sum())


class Tile:
    """A kept tile, to look up: the temperature at a point where the pixel and
    its eight neighbours are all kept water (no land or cloud mixed in)."""

    def __init__(self, path: Path) -> None:
        z = np.load(path)
        self.t, self.zone = int(z["t"]), int(z["zone"])
        self.x0, self.y0, self.dx = float(z["x0"]), float(z["y0"]), float(z["dx"])
        self.grid = np.full(tuple(z["shape"]), np.nan, dtype=np.float32)
        self.grid[z["rows"], z["cols"]] = z["temperature"]
        self._pure: np.ndarray | None = None

    @property
    def pure_mask(self) -> np.ndarray:
        """Pixels whose 3 x 3 pixels are all kept water (never on the tile's
        edge), worked out once per tile."""
        if self._pure is None:
            ok = np.isfinite(self.grid)
            m = np.zeros_like(ok)
            rows, cols = ok.shape
            inner = np.ones((rows - 2, cols - 2), dtype=bool) if rows > 2 and cols > 2 else np.zeros((0, 0), dtype=bool)
            for dr in (0, 1, 2):
                for dc in (0, 1, 2):
                    inner &= ok[dr:dr + rows - 2, dc:dc + cols - 2]
            if inner.size:
                m[1:-1, 1:-1] = inner
            self._pure = m
        return self._pure

    def pure(self, lon, lat) -> np.ndarray:
        """Each point's temperature (degC), NaN where its 3 x 3 pixels are not
        all kept water."""
        e, n = utm(lon, lat, self.zone)
        return self.pure_utm(np.atleast_1d(e), np.atleast_1d(n))

    def pure_utm(self, e: np.ndarray, n: np.ndarray) -> np.ndarray:
        """As `pure`, for points already in this tile's UTM zone (m)."""
        c = np.floor((e - self.x0) / self.dx).astype(int)
        r = np.floor((self.y0 - n) / self.dx).astype(int)
        out = np.full(c.shape, np.nan)
        inside = (r >= 1) & (c >= 1) & (r < self.grid.shape[0] - 1) & (c < self.grid.shape[1] - 1)
        ri, ci = r[inside], c[inside]
        ok = self.pure_mask[ri, ci]
        vals = np.full(ri.shape, np.nan)
        vals[ok] = self.grid[ri[ok], ci[ok]]
        out[inside] = vals
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


def place_masks(where: dict, shape: tuple[int, int], places: list[dict]) -> dict[str, np.ndarray]:
    """Each place's pixels in a tile: those within its `radius_m` of any of its
    `points` (lon, lat). Places missing the tile are left out."""
    from scipy.ndimage import distance_transform_edt

    out = {}
    for p in places:
        pts = np.asarray(p["points"], dtype=float)
        e, n = utm(pts[:, 0], pts[:, 1], where["zone"])
        c = np.floor((np.atleast_1d(e) - where["x0"]) / where["dx"]).astype(int)
        r = np.floor((where["y0"] - np.atleast_1d(n)) / where["dx"]).astype(int)
        reach = int(math.ceil(p["radius_m"] / where["dx"]))
        inside = (r >= -reach) & (c >= -reach) & (r < shape[0] + reach) & (c < shape[1] + reach)
        if not inside.any():
            continue
        r0, r1 = max(0, r[inside].min() - reach), min(shape[0], r[inside].max() + reach + 1)
        c0, c1 = max(0, c[inside].min() - reach), min(shape[1], c[inside].max() + reach + 1)
        if r0 >= r1 or c0 >= c1:
            continue
        line = np.ones((r1 - r0, c1 - c0), bool)  # False where a point falls, for the distance transform
        ok = (r >= r0) & (r < r1) & (c >= c0) & (c < c1)
        line[r[ok] - r0, c[ok] - c0] = False
        if line.all():  # the points pass just outside the tile: none of its pixels is near enough to tell
            continue
        mask = np.zeros(shape, bool)
        mask[r0:r1, c0:c1] = distance_transform_edt(line) * where["dx"] <= p["radius_m"]
        out[p["site"]] = mask
    return out


def land_pixels(water: np.ndarray, cloud: np.ndarray, qc: np.ndarray, lst: np.ndarray, buffer: int = CLOUD_BUFFER) -> np.ndarray:
    """Pixels whose land temperature is kept: not water, clear with no cloud
    within `buffer` pixels, quality best or nominal, with a temperature."""
    clear = water_pixels(np.ones_like(water), cloud, qc, lst, buffer)
    return clear & (water == 0)


def fetch_land(adapter: "EcostressAdapter", store, places: list[dict], box: tuple[float, float, float, float],
               start: int, end: int, per_run: int = PER_RUN) -> int:
    """For the passes since `start` not yet done, newest first and at most
    `per_run`, each place's mean land surface temperature; returns how many
    values were stored. A pass is marked done (`<id>.land`) whatever it held."""
    from fdmr_store import Value

    adapter.cache.mkdir(parents=True, exist_ok=True)
    todo = [g for g in search(box, start, end, adapter.client)
            if "LST" in g["urls"] and not (adapter.cache / f"{g['id']}.land").exists()]
    n = 0
    for g in sorted(todo, key=lambda g: -g["t"])[:per_run]:
        try:
            water, where = read_tif(adapter.download(g["urls"]["water"], adapter.token))
            masks = place_masks(where, water.shape, places)
            if masks:
                cloud, _ = read_tif(adapter.download(g["urls"]["cloud"], adapter.token))
                if (cloud == 0).any():
                    qc, _ = read_tif(adapter.download(g["urls"]["QC"], adapter.token))
                    lst, _ = read_tif(adapter.download(g["urls"]["LST"], adapter.token))
                    ok = land_pixels(water, cloud, qc, lst)
                    for site, m in masks.items():
                        sel = m & ok
                        if sel.sum() >= MIN_LAND_PIXELS:
                            n += store.put_values("ecostress", site, "land_surface_temperature", "degC",
                                                  [Value(g["t"], float(lst[sel].mean()) - KELVIN, None)])
        except Exception as e:  # one tile's failure does not stop the rest; it is tried again next time
            print(f"ecostress land {g['id']} FAILED {e}", flush=True)
            continue
        (adapter.cache / f"{g['id']}.land").touch()
    return n
