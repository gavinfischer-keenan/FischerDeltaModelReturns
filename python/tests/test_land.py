import json

import numpy as np

from fdmr_adapters import ecostress
from fdmr_runner import land
from fdmr_store import Store, Value


def test_a_place_is_the_pixels_within_its_radius_of_its_points():
    e, n = ecostress.utm(-121.5, 38.5, 10)
    where = {"x0": float(e) - 35 - 700, "y0": float(n) + 35 + 700, "dx": 70.0, "zone": 10}  # the point at pixel (10, 10)'s centre
    got = ecostress.place_masks(where, (21, 21), [{"site": "p", "points": [(-121.5, 38.5)], "radius_m": 140.0},
                                                   {"site": "far", "points": [(-100.0, 38.5)], "radius_m": 140.0}])
    assert set(got) == {"p"}
    rows, cols = np.nonzero(got["p"])
    assert got["p"][10, 10] and got["p"][10, 12] and not got["p"][10, 13] and not got["p"][12, 12]
    assert abs(rows.mean() - 10) < 0.5 and abs(cols.mean() - 10) < 0.5


def test_land_pixels_leave_out_water_cloud_and_poor_quality():
    water = np.array([[0, 0, 1, 0]])
    cloud = np.array([[0, 0, 0, 1]])
    qc = np.array([[35520, 35522, 35520, 35520]])
    lst = np.full((1, 4), 290.0)
    assert ecostress.land_pixels(water, cloud, qc, lst, buffer=0).tolist() == [[True, False, False, False]]


def test_day_and_night_by_local_solar_hour():
    assert land.part_of_day(20 * 3600, -120.0) == "day"  # 20 UTC is 12 solar time at 120 W
    assert land.part_of_day(8 * 3600, -120.0) == "night"  # midnight
    assert land.part_of_day(14 * 3600, -120.0) is None  # dawn


def region(tmp_path):
    (tmp_path / "region.toml").write_text('''
reaches = [{ id = "r", kind = "river-routing" }, { id = "d", kind = "1d-tidal-network" }]
[river_temperature.land]
band_m = 1000.0
airport_m = 2000.0
days = 14
[[river_temperature.weather]]
name = "A"
lon = -121.0
lat = 38.0
[[river_temperature.weather]]
name = "B"
lon = -122.0
lat = 38.0
''', encoding="utf-8")
    coords = [(-121.1 - 0.001 * i, 38.0) for i in range(800)]  # 800 points about 88 m apart, westward from near A to near B
    (tmp_path / "r.json").write_text(json.dumps({"coords": coords, "chainage_m": [88.0 * i for i in range(800)]}), encoding="utf-8")
    return tmp_path


def test_places_split_each_river_by_the_zone_it_takes(tmp_path):
    pl = {p["site"]: p for p in land.places(region(tmp_path))}
    assert set(pl) == {"band:r:A", "band:r:B", "airport:A", "airport:B"}
    assert all(lo > -121.5 for lo, _ in pl["band:r:A"]["points"]) and all(lo <= -121.5 for lo, _ in pl["band:r:B"]["points"])
    assert len(pl["band:r:A"]["points"]) + len(pl["band:r:B"]["points"]) < 800  # thinned to about 200 m apart


def test_contrast_is_the_band_less_its_airport_at_the_same_pass(tmp_path):
    r = region(tmp_path)
    store = Store()
    now = 100 * 86_400
    day_pass = now - 86_400 + 20 * 3600  # solar noon at 121 W, near enough
    store.put_values("ecostress", "band:r:A", "land_surface_temperature", "degC", [Value(day_pass, 25.0, None), Value(day_pass - 86_400, 30.0, None)])
    store.put_values("ecostress", "airport:A", "land_surface_temperature", "degC", [Value(day_pass + 30, 22.0, None)])  # only the later pass
    got = land.contrast(r, store, now)
    assert got["stretches"] == [{"reach": "r", "zone": "A", "day": {"passes": 1, "band_mean": 25.0, "airport_mean": 22.0, "contrast": 3.0}}]
