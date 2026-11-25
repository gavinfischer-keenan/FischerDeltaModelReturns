import pytest

pytest.importorskip("fdmr_py")

from fdmr_plugins.river_routing import channel  # noqa: E402
from fdmr_store import ChannelMeasurement  # noqa: E402


def synthetic(n=20, t0=0):
    out = []
    for i in range(n):
        q = 50.0 * 1.25**i
        out.append(ChannelMeasurement(t=t0 + i, flow=q, area=4.0 * q**0.7, width=20.0 * q**0.2, velocity=None))
    return out


def test_recovers_shape_and_velocity():
    s = channel.fit(synthetic())
    assert s.area_b == pytest.approx(0.7) and s.width_b == pytest.approx(0.2)
    q = 300.0
    assert s.velocity(q) == pytest.approx(q / (4.0 * q**0.7))
    assert s.depth(q) * s.width(q) == pytest.approx(s.area(q))


def test_old_and_incomplete_measurements_are_left_out():
    ms = synthetic(t0=1000) + [ChannelMeasurement(t=5, flow=100.0, area=9e9, width=1.0, velocity=None)]
    ms.append(ChannelMeasurement(t=2000, flow=100.0, area=None, width=30.0, velocity=None))
    s = channel.fit(ms, since=1000)
    assert s.n == 20 and s.area_b == pytest.approx(0.7)


def test_too_few_measurements_gives_none():
    assert channel.fit(synthetic(n=5)) is None


def test_between_runs_from_one_shape_to_the_other():
    up = channel.fit(synthetic())
    down = channel.ChannelShape(8.0, 0.6, 40.0, 0.25, 1.0, 1.0, 20)
    assert channel.between(up, down, 0.0).area(200) == pytest.approx(up.area(200))
    assert channel.between(up, down, 1.0).area(200) == pytest.approx(down.area(200))
    mid = channel.between(up, down, 0.5).area(200)
    assert min(up.area(200), down.area(200)) < mid < max(up.area(200), down.area(200))
