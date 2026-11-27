import math

import pytest

from fdmr_runner.geometry import along, build_from_line

REGION = """
id = "t"
name = "test"
timezone = "UTC"
[[reaches]]
id = "r"
name = "a river"
kind = "river-routing"
mouth = [5.30, 52.0]
[[gauges]]
reach = "r"
site = "up"
name = "upstream"
role = "release"
source = "rws"
lon = 5.10
lat = 52.001
[[gauges]]
reach = "r"
site = "down"
name = "downstream"
role = "main"
source = "rws"
lon = 5.20
lat = 51.999
"""


def test_a_line_east_along_a_parallel(tmp_path):
    (tmp_path / "region.toml").write_text(REGION)
    coords = [(5.0 + 0.01 * i, 52.0) for i in range(41)]  # 5.0 to 5.4 E
    g = build_from_line(tmp_path, "r", coords, "test line")
    km = 0.01 * math.cos(math.radians(52.0)) * 111.195  # one step
    assert g["gauges_m"]["up"] == 0.0  # the reach starts at its release gauge
    assert g["gauges_m"]["down"] == pytest.approx(10 * km * 1000, abs=1)
    assert g["length_m"] == pytest.approx(20 * km * 1000, abs=1) and g["model_length_m"] == g["length_m"]  # ends at its mouth
    assert g["coords"][0] == [5.1, 52.0] and g["source"] == "test line"


def test_a_gauge_far_from_the_line_is_refused(tmp_path):
    (tmp_path / "region.toml").write_text(REGION.replace("lat = 51.999", "lat = 51.99"))  # about 1.1 km off
    with pytest.raises(ValueError, match="gauge down is .* m from the line"):
        build_from_line(tmp_path, "r", [(5.0 + 0.01 * i, 52.0) for i in range(41)], "test line")


def test_along_is_distance_on_the_ground():
    assert along([(0.0, 0.0), (0.0, 1.0)])[-1] == pytest.approx(111_195, rel=1e-9)
