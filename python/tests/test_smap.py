import io
import json

import numpy as np
import pytest

from fdmr_adapters import smap
from fdmr_runner import soil
from fdmr_store import Store, Value

h5py = pytest.importorskip("h5py")


def nc4(**arrays) -> bytes:
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        for k, v in arrays.items():
            f.create_dataset(k, data=np.asarray(v, dtype=np.float32))
    return buf.getvalue()


def test_no_value_marks_become_nan():
    got = smap.read_nc4(nc4(**{"Geophysical_Data/sm_surface": [[0.2, -9999.0]]}))
    assert got["sm_surface"][0, 0] == pytest.approx(0.2) and np.isnan(got["sm_surface"][0, 1])


def test_cells_in_a_box_and_near_points():
    lat, lon = np.array([38.2, 38.1, 38.0]), np.array([-122.2, -122.1, -122.0])
    box = smap.members(lat, lon, {"box": [-122.15, 38.05, -121.95, 38.15]})
    assert box.tolist() == [[False, False, False], [False, True, True], [False, False, False]]
    near = smap.members(lat, lon, {"points": [(-122.2, 38.2)], "radius_m": 5_000.0})
    assert near.sum() == 1 and near[0, 0]


def test_fetch_reads_only_the_areas_cells_and_stores_each_period():
    lat = np.linspace(40.0, 36.0, smap.ROWS)  # not the real grid: a stand-in with the same shape
    lon = np.linspace(-125.0, -119.0, smap.COLS)
    asked = []

    class Client:
        def get_json(self, url):
            return {"feed": {"entry": [{"time_start": "2026-11-24T21:00:00Z", "time_end": "2026-11-25T00:00:00Z", "links": [{"href": "https://opendap.earthdata.example/g1"}]}]}}

    def get(url, token):
        asked.append(url)
        if "cell_lat" in url:
            return nc4(cell_lat=lat[:, None])
        if "cell_lon" in url:
            return nc4(cell_lon=lon[None, :])
        ce = __import__("urllib.parse").parse.unquote(url.split("dap4.ce=")[1])
        r0, r1 = [int(x) for x in ce.split("[")[1].rstrip("]").split(":1:")]
        c0, c1 = [int(x) for x in ce.split("[")[2].split("]")[0].split(":1:")]
        shape = (r1 - r0 + 1, c1 - c0 + 1)
        return nc4(**{f"Geophysical_Data/{v}": np.full(shape, k + 1.0) for k, v in enumerate(smap.FIELDS)})

    store = Store()
    a = smap.SmapAdapter(store, token="t", get=get, client=Client())
    n = a.fetch([{"site": "area:x", "box": [-122.0, 37.5, -121.5, 38.0]}], 0, 2_000_000_000)
    t = 1795554000 + 5400  # 21:00 to 24:00 UTC on 24 Nov 2026: its middle
    assert n == 4 and store.values("smap", "area:x", "soil_moisture_rootzone", t, t + 1)[0].value == 2.0
    assert a.fetch([{"site": "area:x", "box": [-122.0, 37.5, -121.5, 38.0]}], 0, 2_000_000_000) == 0  # already stored
    assert len([u for u in asked if "Geophysical_Data" in u]) == 1


def test_summary_gives_latest_and_the_weeks_change(tmp_path):
    (tmp_path / "region.toml").write_text('id = "t"\nname = "test"\ntimezone = "UTC"\nreaches = []\n[soil_moisture]\ncorridor_m = 10000.0\n[[soil_moisture.areas]]\nname = "d"\nbox = [0, 0, 1, 1]\n', encoding="utf-8")
    store = Store()
    now = 100 * 86_400
    t = now - 2 * 86_400
    store.put_values("smap", "area:d", "soil_moisture_rootzone", "m3/m3", [Value(t - 7 * 86_400, 0.20, None), Value(t, 0.25, None)])
    store.put_values("smap", "area:d", "soil_moisture_rootzone_percentile", "%", [Value(t, 64.0, None)])
    got = soil.summary(tmp_path, store, now)
    assert got["areas"] == [{"site": "area:d", "t": t, "rootzone": 0.25, "percentile": 64.0, "rootzone_change_7d": 0.05}]


def test_river_corridors_come_from_the_centrelines(tmp_path):
    (tmp_path / "region.toml").write_text('id = "t"\nname = "test"\ntimezone = "UTC"\nreaches = [{ id = "r", kind = "river-routing" }]\n[soil_moisture]\ncorridor_m = 5000.0\n', encoding="utf-8")
    (tmp_path / "r.json").write_text(json.dumps({"coords": [(-121.0 - 0.01 * i, 38.0) for i in range(50)], "chainage_m": [880.0 * i for i in range(50)]}), encoding="utf-8")
    (a,) = soil.areas(tmp_path)
    assert a["site"] == "river:r" and a["radius_m"] == 5000.0 and 15 <= len(a["points"]) <= 25
