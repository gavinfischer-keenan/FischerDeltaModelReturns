"""Which water the model covers (fdmr_runner.water_audit)."""

import json

import numpy as np

from fdmr_runner import water_audit


def cycle(tmp_path):
    """A published cycle: a mesh (two triangles over a 0.01-degree square at
    lon 0..0.01), a channel line at lat 0.05 (100 m wide), and a pool with water
    points at lon 0.1 and one with only a disc at lon 0.2."""
    (tmp_path / "geometry").mkdir()
    (tmp_path / "index.json").write_text(json.dumps({"reaches": [{"id": "bay"}, {"id": "net"}]}))
    (tmp_path / "geometry" / "bay.json").write_text(json.dumps({
        "dims": 2, "lon": [0, 0.01, 0.01, 0], "lat": [0, 0, 0.01, 0.01], "triangles": [0, 1, 2, 0, 2, 3]}))
    (tmp_path / "geometry" / "net.json").write_text(json.dumps({
        "dims": 1, "lines": [[[0.0, 0.05], [0.02, 0.05]]], "elements": [{"width_m": 100.0}],
        "nodes": [{"id": 1, "lon": 0.2, "lat": 0.0}],
        "pools": [{"id": "a", "area_m2": 1e4, "points": [[0.1, 0.0], [0.1005, 0.0]]},
                  {"id": "b", "area_m2": 31_416.0, "nodes": [1]}]}))
    return tmp_path


def test_a_point_is_covered_by_the_mesh_a_channel_or_a_pool(tmp_path):
    cov = water_audit.Coverage(cycle(tmp_path))
    pts = np.array([
        [0.005, 0.005],   # in the mesh
        [0.01, 0.0503],   # 33 m from the channel's line: inside its 50 m half-width
        [0.01, 0.0512],   # 133 m off: beyond 50 m + the 30 m slack
        [0.1002, 0.0],    # among the pool's water points
        [0.2, 0.0008],    # 89 m from the disc pool's centre: inside its 100 m radius
        [0.5, 0.5],       # nowhere
    ])
    assert cov.covered(pts).tolist() == [True, True, False, True, True, False]


def test_an_areas_samples_lie_inside_it_and_measure_it(tmp_path):
    sq = np.array([(0, 0), (0.01, 0), (0.01, 0.01), (0, 0.01), (0, 0)], dtype=float)
    pts, area = water_audit.sample([sq], np.random.default_rng(1), k=60)
    assert len(pts) == 60 and (pts >= 0).all() and (pts <= 0.01).all()
    side = 0.01 * water_audit.M_PER_DEG
    assert abs(area / side**2 - 1) < 0.02  # the square is all of its box


def test_the_report_lists_the_big_uncovered_areas(tmp_path):
    a = {"built": "2026-11-28", "cycle": "c", "bbox": [0, 0, 1, 1], "attribution": "OSM",
         "kinds": {"water": {"count": 2, "area_m2": 3e5, "covered_m2": 1e5, "small_count": 0, "small_area_m2": 0, "small_uncovered_m2": 0}},
         "uncovered": [{"osm": "way/1", "kind": "water", "name": "Tidal Canal", "tag": "canal", "area_m2": 2e5,
                        "covered": 0.1, "lon": 0.5, "lat": 0.5}]}
    text = water_audit.report(a, "x")
    assert "Tidal Canal" in text and "https://www.openstreetmap.org/way/1" in text and "| Open water | 2 |" in text
