import gzip
import io
import tarfile

import numpy as np
import pytest

from fdmr_adapters import areas, nws_precip, snodas
from fdmr_runner import soil
from fdmr_store import Store, Value

DAY = 86_400


def test_the_grids_south_west_cell_is_where_hrap_puts_it():
    lon, lat = nws_precip.lonlat(np.array(-1902530.86073866), np.array(-7617164.94569394))
    assert float(lat) == pytest.approx(23.117, abs=0.004) and float(lon) == pytest.approx(-119.023, abs=0.002)  # a fifteenth of a cell


def test_cells_in_an_area():
    la, lo = np.meshgrid([38.2, 38.1], [-122.2, -122.1], indexing="ij")
    assert areas.members(la, lo, {"box": [-122.15, 38.05, -122.0, 38.15]}).tolist() == [[False, False], [False, True]]


def snodas_tar(melt_raw: int, swe_raw: int) -> bytes:
    hdr = ("Number of columns: 3\nNumber of rows: 2\nBenchmark x-axis coordinate: -122.0\nBenchmark y-axis coordinate: 38.0\n"
           "X-axis resolution: 0.01\nY-axis resolution: 0.01\n")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for code, raw in (("1044", melt_raw), ("1034", swe_raw)):
            grid = np.full((2, 3), raw, dtype=">i2")
            grid[0, 0] = -9999
            for name, data in ((f"us_ssmv1{code}x.dat.gz", gzip.compress(grid.tobytes())), (f"us_ssmv1{code}x.txt.gz", gzip.compress(hdr.encode()))):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_snodas_units_and_no_data():
    got = snodas.read(snodas_tar(melt_raw=250, swe_raw=40))
    melt, h = got["snowmelt_24h"]
    assert np.isnan(melt[0, 0]) and melt[1, 2] == pytest.approx(2.5)  # 250 / 100000 m = 2.5 mm
    assert got["snow_water_equivalent"][0][1, 1] == pytest.approx(40.0)  # 40 / 1000 m = 40 mm
    lat, lon = snodas.axes(h)
    assert lat.tolist() == pytest.approx([38.0, 37.99]) and lon.tolist() == pytest.approx([-122.0, -121.99, -121.98])


def test_no_melt_where_there_is_no_snow():
    got = snodas.read(snodas_tar(melt_raw=-9999, swe_raw=0))
    melt = got["snowmelt_24h"][0]
    assert np.isnan(melt[0, 0]) and melt[1, 1] == 0.0  # the no-data corner stays unknown


def test_snodas_fetch_stores_an_area_mean_once():
    store = Store()
    a = snodas.SnodasAdapter(store, get=lambda url: snodas_tar(100, 20))
    day = 20_000 * DAY
    assert a.fetch([{"site": "area:x", "box": [-122.1, 37.9, -121.9, 38.1]}], day, day) == 2
    assert store.values("snodas", "area:x", "snowmelt_24h", day, day + DAY)[0].value == pytest.approx(1.0)
    assert a.fetch([{"site": "area:x", "box": [-122.1, 37.9, -121.9, 38.1]}], day, day) == 0


def test_water_in_sums_the_week_against_normal():
    store = Store()
    now = 100 * DAY
    t = now - DAY + 12 * 3600
    store.put_values("nws-precip", "area:x", "precipitation_24h", "mm", [Value(t - k * DAY, 2.0, None) for k in range(7)])
    store.put_values("nws-precip", "area:x", "precipitation_normal_24h", "mm", [Value(t - k * DAY, 1.0, None) for k in range(7)])
    got = soil.water_in(store, "area:x", now)
    assert got["rain_24h_mm"] == 2.0 and got["rain_7d_mm"] == 14.0 and got["rain_7d_percent_normal"] == 200
