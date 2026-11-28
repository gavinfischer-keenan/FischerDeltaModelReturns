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
masks. Each kept tile goes in a tile cache (fdmr_store.tiles; the format
under EcostressAdapter): the water pixels' rows, columns and temperatures,
the tile's UTM origin, pixel size and zone, and the pass time.

The land is kept only as means over named places (`LandAsk`, taken from
the same passes as the water, each layer downloaded once): the land
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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
from fdmr_geo import utm
from fdmr_store.tiles import TileCache

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


@dataclass
class LandAsk:
    """The land's surface temperature as means over named places (`places`,
    each a `site`, its `points` and a `radius_m`), from the passes over `box`
    since `start`, stored in `store`: asked of `EcostressAdapter.fetch` with
    the water, so each pass's shared layers are downloaded once."""

    store: object
    places: list[dict]
    box: tuple[float, float, float, float]
    start: int


class EcostressAdapter:
    """Keeps each pass's water in a tile cache (fdmr_store.tiles): `<id>` holds
    the clear water pixels' `rows`, `cols` and `temperature` (degC), the
    grid's `shape`, its UTM origin `x0`, `y0`, pixel size `dx` and `zone`,
    and the pass time `t`. A pass with no clear water is marked `none`, and
    one whose land was taken, `land`."""

    def __init__(self, cache: Path, token: str | None = None, download=_download, client: JsonClient | None = None) -> None:
        self.tiles = TileCache(cache)
        self.token = token if token is not None else TOKEN_FILE.read_text(encoding="utf-8").strip()
        self.download = download
        self.client = client

    def fetch(self, box: tuple[float, float, float, float] | None, start: int, end: int, keep_s: int,
              per_run: int = PER_RUN, land: LandAsk | None = None) -> int:
        """Keep the water of the passes over `box` since `start` not already
        kept, newest first and at most `per_run` of them; with `land`, each
        place's mean land temperature from its passes not yet done, the same
        way. A pass wanted for both is downloaded once. What is older than
        `keep_s` is dropped. Returns how many water pixels and land values
        were kept."""
        self.tiles.prune(end - keep_s - 86_400, marks=("none", "land"))
        newest = lambda gs: sorted(gs, key=lambda g: -g["t"])[:per_run]  # noqa: E731
        water = newest([g for g in search(box, start, end, self.client)
                        if not (self.tiles.has(g["id"]) or self.tiles.has(g["id"], "none"))]) if box else []
        lands = newest([g for g in search(land.box, land.start, end, self.client)
                        if "LST" in g["urls"] and not self.tiles.has(g["id"], "land")]) if land else []
        water_ids, land_ids = {g["id"] for g in water}, {g["id"] for g in lands}
        kept = 0
        errors: list[str] = []
        for g in sorted({g["id"]: g for g in [*water, *lands]}.values(), key=lambda g: -g["t"]):
            got: dict[str, tuple[np.ndarray, dict]] = {}

            def layer(name: str, g=g, got=got) -> tuple[np.ndarray, dict]:
                """One of the pass's layers, downloaded once for both products."""
                if name not in got:
                    got[name] = read_tif(self.download(g["urls"][name], self.token))
                return got[name]

            # One tile's failure does not stop the rest; named, tried again next run (#91).
            if g["id"] in water_ids:
                try:
                    kept += self._keep_water(g, layer)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{g['id']}: {e}")
            if g["id"] in land_ids:
                try:
                    kept += self._keep_land(g, layer, land)
                    self.tiles.mark(g["id"], "land")  # done, whatever it held
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{g['id']} (land): {e}")
        if errors:
            raise http.PartialFetch(kept, errors)
        return kept

    def _keep_water(self, g: dict, layer) -> int:
        water, where = layer("water")
        cloud, _ = layer("cloud")
        if "WST" not in g["urls"] or not water_pixels(water, cloud, None, None).any():
            self.tiles.mark(g["id"], "none")
            return 0
        qc, _ = layer("QC")
        wst, _ = layer("WST")
        ok = water_pixels(water, cloud, qc, wst)
        rows, cols = np.nonzero(ok)
        self.tiles.put(g["id"], rows=rows.astype(np.uint16), cols=cols.astype(np.uint16),
                       temperature=(wst[ok] - KELVIN).astype(np.float32), shape=np.array(ok.shape), t=g["t"], **where)
        return int(ok.sum())

    def _keep_land(self, g: dict, layer, land: LandAsk) -> int:
        """Each place's mean land surface temperature in the pass, where at
        least MIN_LAND_PIXELS of its pixels are clear land."""
        from fdmr_store import Value

        water, where = layer("water")
        masks = place_masks(where, water.shape, land.places)
        if not masks:
            return 0
        cloud, _ = layer("cloud")
        if not (cloud == 0).any():
            return 0
        qc, _ = layer("QC")
        lst, _ = layer("LST")
        ok = land_pixels(water, cloud, qc, lst)
        n = 0
        for site, m in masks.items():
            sel = m & ok
            if sel.sum() >= MIN_LAND_PIXELS:
                n += land.store.put_values("ecostress", site, "land_surface_temperature", "degC",
                                           [Value(g["t"], float(lst[sel].mean()) - KELVIN, None)])
        return n


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
