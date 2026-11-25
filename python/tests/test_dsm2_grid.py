import json

import pytest

from fdmr_plugins.dsm2 import grid
from fdmr_runner.publish import add_reach


def test_utm_round_trip_and_central_meridian():
    x, y = grid.lonlat_to_utm(-123.0, 38.0, 10)
    assert x == pytest.approx(500_000.0, abs=1e-6)
    for lon, lat in [(-121.5, 38.05), (-122.4, 37.8), (-120.9, 39.0)]:
        x, y = grid.lonlat_to_utm(lon, lat, 10)
        back = grid.utm_to_lonlat(x, y, 10)
        assert back[0] == pytest.approx(lon, abs=1e-9) and back[1] == pytest.approx(lat, abs=1e-9)


def test_utm_scale_is_right():
    # One degree of latitude near 38 N is about 111 km.
    _, y0 = grid.lonlat_to_utm(-123.0, 37.5, 10)
    _, y1 = grid.lonlat_to_utm(-123.0, 38.5, 10)
    assert 110_900 < (y1 - y0) / 0.9996 < 111_100


def test_lines_run_from_the_upstream_junction_and_meet_both():
    line = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    # Drawn the other way round, and stopping short of both junctions.
    assert grid.orient(line, (2.1, 0.0), (-0.1, 0.0)) == [(2.1, 0.0), (2.0, 0.0), (1.0, 0.0), (0.0, 0.0), (-0.1, 0.0)]
    assert grid.orient(line, (0.0, 0.0), (2.0, 0.0)) == line


def test_geometry_lists_elements_nodes_and_lines():
    ch = [grid.Channel(1, 10, 11, 1000.0, [(0.0, 0.0), (0.01, 0.0)]), grid.Channel(2, 11, 12, 500.0, [(0.01, 0.0), (0.02, 0.0)])]
    g = grid.geometry("net", ch, {10: (0.0, 0.0), 11: (0.01, 0.0), 12: (0.02, 0.0)})
    assert g["network"] and g["elements"][1] == {"id": 2, "up": 11, "down": 12, "start_m": 0.0, "end_m": 500.0}
    assert [n["id"] for n in g["nodes"]] == [10, 11, 12] and len(g["lines"]) == 2


def test_add_reach_links_files_and_extends_the_index(tmp_path):
    src = tmp_path / "run"
    (src / "net").mkdir(parents=True)
    (src / "geometry").mkdir()
    (src / "net" / "2026-11-25.fdf").write_bytes(b"FDF1")
    (src / "geometry" / "net.json").write_text("{}")
    (src / "reach.json").write_text(json.dumps({"id": "net", "time": {"start": "x"}}))
    cdir = tmp_path / "cycle"
    cdir.mkdir()
    index = {"format_version": [1, 0], "reaches": [{"id": "river"}]}
    add_reach(cdir, src, index)
    assert (cdir / "net" / "2026-11-25.fdf").read_bytes() == b"FDF1" and not (cdir / "reach.json").exists()
    assert [r["id"] for r in index["reaches"]] == ["river", "net"] and index["format_version"] == [1, 1]
