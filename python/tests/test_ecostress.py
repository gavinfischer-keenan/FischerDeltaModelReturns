import fdmr_geo
import numpy as np
import pytest

from fdmr_adapters import ecostress
from fdmr_runner.satellite import WaterTile
from fdmr_store import Store, Value
from fdmr_store.tiles import TileCache


def test_utm_matches_standard_values():
    # On the central meridian the northing is 0.9996 times the meridian arc:
    # 4,984,944.378 m from the equator to 45 degrees on WGS84.
    e, n = fdmr_geo.utm(-123.0, 45.0, 10)
    assert float(e) == pytest.approx(500_000, abs=1e-6) and float(n) == pytest.approx(0.9996 * 4_984_944.378, abs=0.01)
    # On the equator at the zone's edge, 3 degrees out: easting 833,978.56 m.
    e, n = fdmr_geo.utm(-120.0, 0.0, 10)
    assert float(e) == pytest.approx(833_978.56, abs=0.01) and abs(float(n)) < 1e-6


def test_quality_flags_keep_best_and_nominal_clear_water():
    water = np.array([1, 1, 1, 1, 0])
    cloud = np.array([0, 0, 0, 1, 0])
    qc = np.array([35520, 35521, 35522, 35520, 35520])  # bits 0-1: best, nominal, cloud, best
    wst = np.array([290.0, 291.0, 292.0, 293.0, 294.0])
    assert ecostress.water_pixels(water, cloud, qc, wst, buffer=0).tolist() == [True, True, False, False, False]


def test_water_beside_cloud_is_not_kept():
    water = np.ones((1, 9), int)
    cloud = np.zeros((1, 9), int)
    cloud[0, 0] = 1
    kept = ecostress.water_pixels(water, cloud, None, None, buffer=2)
    assert kept[0].tolist() == [False, False, False, True, True, True, True, True, True]


def make_tile(tmp_path, values: np.ndarray, t: int, x0: float, y0: float, name="a"):
    ok = np.isfinite(values)
    rows, cols = np.nonzero(ok)
    cache = TileCache(tmp_path)
    cache.put(name, rows=rows.astype(np.uint16), cols=cols.astype(np.uint16), temperature=values[ok].astype(np.float32),
              shape=np.array(values.shape), t=t, x0=x0, y0=y0, dx=70.0, zone=10)
    return WaterTile(cache.get(name))


def lonlat_of(tile, row, col):
    """A point at a pixel's centre, found by searching (the inverse of utm)."""
    from scipy.optimize import minimize

    target = (tile.x0 + (col + 0.5) * tile.dx, tile.y0 - (row + 0.5) * tile.dx)
    f = lambda p: sum((np.array(fdmr_geo.utm(p[0], p[1], 10)) - target) ** 2)
    return minimize(f, [-122.0, 38.0], method="Nelder-Mead", options={"xatol": 1e-9, "fatol": 1e-6}).x


def test_only_pixels_with_water_all_round_count(tmp_path):
    e, n = fdmr_geo.utm(-121.7, 38.05, 10)
    v = np.full((7, 7), 15.0)
    v[0, :] = np.nan  # land along the top row
    tile = make_tile(tmp_path, v, 0, float(e) - 210, float(n) + 210)
    lon, lat = lonlat_of(tile, 3, 3)
    assert tile.pure([lon], [lat])[0] == pytest.approx(15.0)
    lon1, lat1 = lonlat_of(tile, 1, 3)  # a neighbour is land
    assert np.isnan(tile.pure([lon1], [lat1])[0])
    assert tile.near(lon1, lat1) == pytest.approx(15.0)  # the bank sensor's nearest open-water pixel


class Lookup:
    reach = "net"
    skip = frozenset()

    def __init__(self, lines, model):
        self.geo = {"lines": lines}
        self.model = model

    def at(self, field, k, t):
        return self.model if field == "temperature" else None


def test_check_scores_satellite_against_stations_and_models(tmp_path):
    e, n = fdmr_geo.utm(-121.7, 38.05, 10)
    t = 1_800_000_000
    tile = make_tile(tmp_path, np.full((9, 9), 15.0), t, float(e) - 300, float(n) + 300)
    lon, lat = lonlat_of(tile, 4, 4)
    store = Store()
    store.put_values("cdec", "AAA", "water_temperature", "degC", [Value(t - 600, 14.5, None), Value(t + 3600, 9.0, None)])
    cfg = {"default_source": "cdec", "satellite_temperature": {"days": 14}, "stations": [{"site": "AAA", "lon": lon, "lat": lat}]}
    got = ecostress_check = __import__("fdmr_runner.overlays", fromlist=["x"]).satellite_temperature(
        cfg, tmp_path, [Lookup([[[lon, lat]], [[-100.0, 10.0]]], 14.0)], store, t + 60, tiles=[tile])
    assert got["passes"] == 1
    assert got["stations"]["mean_difference"] == pytest.approx(0.5)
    assert got["models"]["net"] == {"places": 1, "pairs": 1, "mean_difference": 1.0, "rms_difference": 1.0}
    assert ecostress_check["stations"]["per_station"][0]["site"] == "AAA"


def test_geotiff_tags_give_the_tile_position(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    import io

    buf = io.BytesIO()
    keys = (1, 1, 0, 2, 1024, 0, 1, 1, 3072, 0, 1, 32610)
    tifffile.imwrite(buf, np.zeros((4, 4), np.uint8), extratags=[
        (33550, "d", 3, (70.0, 70.0, 0.0)), (33922, "d", 6, (0.0, 0.0, 0.0, 600000.0, 4300020.0, 0.0)),
        (34735, "H", len(keys), keys)])
    a, where = ecostress.read_tif(buf.getvalue())
    assert a.shape == (4, 4) and where == {"x0": 600000.0, "y0": 4300020.0, "dx": 70.0, "zone": 10}


def test_a_server_error_is_retried_and_a_refusal_is_not(monkeypatch):
    import urllib.error
    import urllib.request

    calls = []

    def fake(req, timeout):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"ok"

        return R()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert ecostress._download("https://x/y.tif", "t", sleep=lambda s: None) == b"ok" and len(calls) == 3

    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(urllib.error.HTTPError):
        ecostress._download("https://x/y.tif", "t", sleep=lambda s: None)


def test_utm_back_to_longitude_and_latitude():
    lons = np.array([-121.5, -122.9, -120.1])
    lats = np.array([38.1, 37.3, 39.9])
    e, n = fdmr_geo.utm(lons, lats, 10)
    lo, la = fdmr_geo.lonlat_from_utm(e, n, 10)
    assert np.allclose(lo, lons, atol=1e-7) and np.allclose(la, lats, atol=1e-7)


def test_a_pass_wanted_for_water_and_land_is_downloaded_once(tmp_path):
    """#92: the water and the land come from the same passes; each layer of a
    pass is downloaded once for both, and each product is marked done."""
    import io

    tifffile = pytest.importorskip("tifffile")
    e, n = fdmr_geo.utm(-121.5, 38.5, 10)
    x0, y0 = float(e) - 70 * 10, float(n) + 70 * 10

    def tif(a):
        buf = io.BytesIO()
        keys = (1, 1, 0, 2, 1024, 0, 1, 1, 3072, 0, 1, 32610)  # UTM zone 10N (EPSG 32610), as the test above
        tifffile.imwrite(buf, a, extratags=[(33550, "d", 3, (70.0, 70.0, 0.0)), (33922, "d", 6, (0.0, 0.0, 0.0, x0, y0, 0.0)),
                                            (34735, "H", len(keys), keys)])
        return buf.getvalue()

    water = np.zeros((21, 21), np.uint8)
    water[:, :10] = 1  # water on the west half, land on the east
    layers = {"water": tif(water), "cloud": tif(np.zeros((21, 21), np.uint8)), "QC": tif(np.zeros((21, 21), np.uint16)),
              "WST": tif(np.full((21, 21), 290.0, np.float32)), "LST": tif(np.full((21, 21), 300.0, np.float32))}
    got: list[str] = []

    def download(url, token):
        got.append(url)
        return layers[url]

    g = {"id": "pass1", "t": 1_800_000_000, "urls": {k: k for k in layers}}
    ecostress_search = ecostress.search
    ecostress.search = lambda box, start, end, client=None: [g]
    try:
        store = Store()
        a = ecostress.EcostressAdapter(tmp_path, token="t", download=download)
        place = {"site": "p", "points": [(-121.5 + 0.004, 38.5)], "radius_m": 300.0}
        n_kept = a.fetch((-122, 38, -121, 39), 0, g["t"] + 60, 86_400 * 14, land=ecostress.LandAsk(store, [place], (-122, 38, -121, 39), 0))
    finally:
        ecostress.search = ecostress_search
    assert sorted(got) == ["LST", "QC", "WST", "cloud", "water"]  # each once
    assert a.tiles.has("pass1") and a.tiles.has("pass1", "land")
    assert n_kept > 0
