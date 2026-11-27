import numpy as np

from fdmr_adapters import ecostress, opera
from fdmr_runner import water_extent

# The settings as a region gives them (regions/sf-bay-delta/overlays.toml [water_extent]).
CFG = {"min_prior": 3, "new_below": 0.2, "min_patch_km2": 0.5}


def test_opera_classes():
    got = opera.classify(np.array([0, 1, 2, 252, 253, 255], np.uint8))
    assert got.tolist() == [0, 1, 1, 255, 255, 255]


def tile(t, water, x0, y0):
    return {"t": t, "water": water, "row0": 0, "col0": 0, "x0": x0, "y0": y0, "dx": 30.0, "zone": 10}


def test_new_water_is_water_where_there_was_none():
    box = (-121.60, 38.00, -121.55, 38.04)
    x0, y0, rows, cols = water_extent._grid(box, 10, 30.0)
    dry = np.zeros((rows, cols), np.uint8)
    flood = dry.copy()
    flood[40:80, 40:80] = 1  # 1.44 km2 of new water
    last_year = [tile(k * 86_400 * 6, dry, x0, y0) for k in range(4)]
    rec = water_extent.area_record([tile(400 * 86_400, dry, x0, y0), tile(406 * 86_400, flood, x0, y0)], {"name": "a", "box": box},
                                   CFG, baseline=last_year)
    last = rec["passes"][-1]
    assert last["new_water_km2"] == 1.44 and last["new_patches"][0]["km2"] == 1.44
    lo, la = last["new_patches"][0]["lon"], last["new_patches"][0]["lat"]
    assert box[0] < lo < box[2] and box[1] < la < box[3]
    assert all(p["new_water_km2"] == 0 for p in rec["passes"][:-1])


def test_water_always_there_is_not_new():
    box = (-121.60, 38.00, -121.55, 38.04)
    x0, y0, rows, cols = water_extent._grid(box, 10, 30.0)
    wet = np.ones((rows, cols), np.uint8)
    rec = water_extent.area_record([tile(400 * 86_400, wet, x0, y0)], {"name": "a", "box": box}, CFG,
                                   baseline=[tile(k * 86_400 * 6, wet, x0, y0) for k in range(4)])
    assert rec["passes"][-1]["new_water_km2"] == 0 and rec["passes"][-1]["water_km2"] > 0


def test_crop_covers_the_box():
    e, n = ecostress.utm(-121.5, 38.0, 10)
    where = {"x0": float(e) - 3000, "y0": float(n) + 3000, "dx": 30.0, "zone": 10}
    r0, r1, c0, c1 = opera.crop_rows_cols(where, (300, 300), (-121.51, 37.99, -121.49, 38.01))
    assert 0 < r0 < r1 < 300 and 0 < c0 < c1 < 300


def test_winter_flooding_seen_last_winter_is_not_new():
    box = (-121.60, 38.00, -121.55, 38.04)
    x0, y0, rows, cols = water_extent._grid(box, 10, 30.0)
    fields = np.zeros((rows, cols), np.uint8)
    fields[10:60, 10:60] = 1  # flooded every winter
    rec = water_extent.area_record([tile(400 * 86_400, fields, x0, y0)], {"name": "a", "box": box}, CFG,
                                   baseline=[tile(k * 86_400 * 6, fields, x0, y0) for k in range(4)])
    assert rec["passes"][-1]["new_water_km2"] == 0 and rec["passes"][-1]["baseline_passes"] == 4


def test_windows_keep_recent_and_the_same_season_before():
    w = water_extent.windows({"days": 60, "baseline_years": 2, "baseline_window_days": 30}, 1000 * 86_400)
    assert w[0] == (940 * 86_400, 1000 * 86_400) and w[1] == (605 * 86_400, 665 * 86_400) and len(w) == 3


def test_sudden_new_water_was_dry_on_the_last_pass():
    box = (-121.60, 38.00, -121.55, 38.04)
    x0, y0, rows, cols = water_extent._grid(box, 10, 30.0)
    dry = np.zeros((rows, cols), np.uint8)
    wet = dry.copy()
    wet[40:80, 40:80] = 1
    base = [tile(k * 86_400 * 6, dry, x0, y0) for k in range(4)]
    rec = water_extent.area_record([tile(400 * 86_400, dry, x0, y0), tile(406 * 86_400, wet, x0, y0), tile(412 * 86_400, wet, x0, y0)],
                                   {"name": "a", "box": box}, CFG, baseline=base)
    assert rec["passes"][1]["sudden_new_km2"] == 1.44 and rec["passes"][2]["sudden_new_km2"] == 0
    assert rec["passes"][2]["new_water_km2"] == 1.44  # still new against earlier seasons, no longer sudden

