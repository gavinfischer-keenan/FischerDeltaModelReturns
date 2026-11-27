import math

import pytest

from fdmr_plugins.river_routing import form


def along(points):
    """point_at for a polyline of (lon, lat) at the equator (1 degree = 111.195 km both ways)."""
    seg = [math.hypot(b[0] - a[0], b[1] - a[1]) * 111_195 for a, b in zip(points, points[1:])]
    def at(s):
        for k, ((a, b), d) in enumerate(zip(zip(points, points[1:]), seg)):
            if s <= d or k == len(seg) - 1:
                f = min(1.0, s / d) if d else 0.0
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            s -= d
        return points[-1]
    return at, sum(seg)


def test_a_straight_element_does_not_turn():
    at, length = along([(0.0, 0.0), (0.01, 0.0)])
    f = form.element_form(at, 0.0, length)
    assert f["turn_deg"] == 0.0 and f["abs_turn_deg"] == 0.0 and f["sinuosity"] == pytest.approx(1.0, abs=1e-3)


def test_a_left_angle_turns_plus_90_and_winds():
    # East 1.1 km, then north 1.1 km: a 90 degree turn to the left.
    at, length = along([(0.0, 0.0), (0.01, 0.0), (0.01, 0.01)])
    f = form.element_form(at, 0.0, length, step_m=100.0)
    assert f["turn_deg"] == pytest.approx(90.0, abs=1.0)
    assert f["abs_turn_deg"] == pytest.approx(90.0, abs=1.0)
    assert f["sinuosity"] == pytest.approx(math.sqrt(2), abs=0.02)


def test_a_right_then_left_bend_nets_zero_but_bends_both_ways():
    at, length = along([(0.0, 0.0), (0.01, 0.0), (0.01, -0.01), (0.02, -0.01)])
    f = form.element_form(at, 0.0, length)
    assert f["turn_deg"] == pytest.approx(0.0, abs=1.0)
    assert f["abs_turn_deg"] == pytest.approx(180.0, abs=2.0)


def test_turn_wraps_across_west():
    assert form.turn(170.0, -170.0) == pytest.approx(20.0)
    assert form.turn(-170.0, 170.0) == pytest.approx(-20.0)
