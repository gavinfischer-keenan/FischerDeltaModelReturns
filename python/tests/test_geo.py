"""Distances on the ground (fdmr_geo)."""

import math

import pytest

import fdmr_geo as geo


def test_a_degree_is_the_mean_earth_radius_arc():
    assert geo.M_PER_DEG == pytest.approx(2 * math.pi * 6_371_008.8 / 360, rel=1e-6)
    assert geo.distance_m((0.0, 0.0), (0.0, 1.0)) == pytest.approx(111_195.0)
    # A degree of longitude at 60 degrees north is half of one at the equator.
    assert geo.distance_m((10.0, 60.0), (11.0, 60.0)) == pytest.approx(111_195.0 / 2, rel=1e-9)


def test_the_nearest_point_is_on_a_segment_not_a_vertex():
    line = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]
    i, f, off, along = geo.nearest_on_line(line, (0.013, 0.001))
    assert i == 1 and f == pytest.approx(0.3) and off == pytest.approx(111.195, rel=1e-6)
    assert along == pytest.approx(0.013 * 111_195, rel=1e-9)
    # Past the end: the end, and its distance.
    i, f, off, along = geo.nearest_on_line(line, (0.03, 0.0))
    assert (i, f) == (1, 1.0) and off == pytest.approx(0.01 * 111_195, rel=1e-9)


def test_many_distances_at_once_match_one_at_a_time():
    np = pytest.importorskip("numpy")
    lon, lat = np.array([0.0, 0.5, -1.0]), np.array([38.0, 38.2, 37.9])
    d = geo.distances_m(lon, lat, (0.1, 38.0))
    assert d.tolist() == pytest.approx([geo.distance_m((0.1, 38.0), (x, y)) for x, y in zip(lon, lat)], rel=1e-12)
