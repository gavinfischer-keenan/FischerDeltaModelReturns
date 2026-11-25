import pytest

from fdmr_plugins.river_routing.centreline import chain, distance_m


def test_distance_of_one_degree_of_latitude():
    assert distance_m((0.0, 0.0), (0.0, 1.0)) == pytest.approx(111_195, rel=1e-4)


def test_chains_unordered_flowlines_from_the_start():
    a = (1, [(0.0, 0.0), (0.0, 0.01)])
    b = (2, [(0.0, 0.01), (0.0, 0.02), (0.0, 0.03)])
    c = (3, [(0.0, 0.03), (0.0, 0.04)])
    stray = (9, [(5.0, 5.0), (5.0, 5.01)])  # a side channel that does not join
    line = chain([c, stray, a, b], start_comid=1)
    assert line.coords == [(0.0, 0.0), (0.0, 0.01), (0.0, 0.02), (0.0, 0.03), (0.0, 0.04)]
    assert line.length == pytest.approx(4 * 1111.95, rel=1e-3)
    assert set(line.flowline_start) == {1, 2, 3}


def test_gauge_position_from_measure():
    line = chain([(1, [(0.0, 0.0), (0.0, 0.01)]), (2, [(0.0, 0.01), (0.0, 0.03)])], start_comid=1)
    # measure is percent from the downstream end: 100 is the upstream end
    assert line.position_of(2, 100.0) == pytest.approx(line.flowline_start[2])
    assert line.position_of(2, 0.0) == pytest.approx(line.length)
    assert line.position_of(2, 50.0) == pytest.approx(line.flowline_start[2] + 1111.95, rel=1e-3)


def test_point_at_interpolates():
    line = chain([(1, [(0.0, 0.0), (0.0, 0.02)])], start_comid=1)
    lon, lat = line.point_at(line.length / 2)
    assert lat == pytest.approx(0.01) and lon == pytest.approx(0.0)


def test_unknown_start_is_an_error():
    with pytest.raises(ValueError):
        chain([(1, [(0.0, 0.0), (0.0, 0.01)])], start_comid=7)


def test_nearest_finds_the_chainage_and_the_offset():
    from fdmr_plugins.river_routing.centreline import Centreline, distance_m, nearest

    coords = [(-122.0, 40.0), (-122.0, 40.1), (-121.9, 40.1)]
    ch = [0.0, distance_m(coords[0], coords[1])]
    ch.append(ch[1] + distance_m(coords[1], coords[2]))
    line = Centreline(coords=coords, chainage=ch)
    s, off = nearest(line, -121.999, 40.05)  # just east of the first leg, halfway up
    assert abs(s - ch[1] / 2) < 50
    assert 70 < off < 100  # 0.001 degrees of longitude at 40 N is about 85 m
