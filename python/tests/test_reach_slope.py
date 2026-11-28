"""A reach's bed slope from NHDPlus (fdmr_runner.reach_slope)."""

import json

import pytest

from fdmr_runner import reach_slope


def test_the_slope_is_weighted_by_each_flowlines_length():
    s, km = reach_slope.weighted({1: (1e-3, 3.0), 2: (1e-4, 1.0)})
    assert km == 4.0 and s == pytest.approx((3e-3 + 1e-4) / 4)
    with pytest.raises(ValueError):
        reach_slope.weighted({})


def test_a_reach_takes_its_flowlines_down_from_its_top(tmp_path):
    (tmp_path / "r.json").write_text(json.dumps({"coords": [[-121.0, 38.0], [-121.1, 38.0]], "length_m": 5000.0}))
    (tmp_path / "region.toml").write_text('[[reaches]]\nid = "r"\nkind = "river-routing"\n')

    class Nldi:
        def point_position(self, lon, lat, name=""):
            return type("P", (), {"comid": 7})()

        def downstream_from(self, comid, distance_km):
            assert comid == 7 and distance_km == 5.0
            return [type("F", (), {"comid": c})() for c in (7, 8)]

    def get(url):
        assert "comid+IN+%287%2C8%29" in url or "comid%20IN%20(7,8)" in url or "IN+%287%2C8%29" in url
        return {"features": [{"properties": {"comid": 7, "slope": 2e-3, "lengthkm": 1.0, "gnis_name": "X"}},
                             {"properties": {"comid": 8, "slope": 1e-3, "lengthkm": 4.0, "gnis_name": "X"}}]}

    s = reach_slope.reach_slope(tmp_path, "r", nldi=Nldi(), get=get)
    assert s["bed_slope"] == pytest.approx((2e-3 + 4e-3) / 5) and s["flowlines"] == 2
