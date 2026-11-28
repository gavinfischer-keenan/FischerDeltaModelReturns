"""Pools' mapped water as points (fdmr_runner.pool_water)."""

import math

from fdmr_runner import pool_water


def square(x0, y0, d):
    return [(x0, y0), (x0 + d, y0), (x0 + d, y0 + d), (x0, y0 + d), (x0, y0)]


def test_points_fill_the_water_and_leave_out_its_islands():
    # A 0.02-degree square of water at the equator (about 2.2 km a side) with
    # an island in its middle; the island's ring is just another set of edges.
    lake, island = square(0.0, 0.0, 0.02), square(0.008, 0.008, 0.004)
    pts, area = pool_water.points([lake, island], spacing_m=50.0, max_points=10_000)
    side = 0.02 * pool_water.M_PER_DEG
    hole = 0.004 * pool_water.M_PER_DEG
    assert math.isclose(area, side**2 - hole**2, rel_tol=0.03)
    assert not any(0.008 < x < 0.012 and 0.008 < y < 0.012 for x, y in pts)
    assert all(0 < x < 0.02 and 0 < y < 0.02 for x, y in pts)


def test_a_relations_loose_pieces_bound_the_same_area_as_its_rings():
    # A multipolygon's outer ring mapped as two ways, unjoined: odd crossings still hold.
    ring = square(0.0, 0.0, 0.01)
    a, b = ring[:3], ring[2:]
    assert pool_water.inside(0.005, 0.005, [a, b]) and not pool_water.inside(0.015, 0.005, [a, b])


def test_many_points_are_thinned_evenly_to_the_limit():
    pts, _ = pool_water.points([square(0.0, 0.0, 0.02)], spacing_m=20.0, max_points=500)
    assert len(pts) == 500
    xs = sorted(x for x, _ in pts)
    assert xs[0] < 0.002 and xs[-1] > 0.018  # spread over the whole lake, not one corner
