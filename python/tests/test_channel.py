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


def test_wide_channel_is_uniform_flow_in_a_fixed_width():
    import math

    from fdmr_plugins.river_routing.channel import wide_channel

    s = wide_channel(260.0, 1e-4, chezy=45.0)
    d = (1500 / (45.0 * 260.0 * math.sqrt(1e-4))) ** (2 / 3)  # Q = C W d^1.5 S^0.5
    assert math.isclose(s.depth(1500.0), d, rel_tol=1e-9) and math.isclose(s.width(1500.0), 260.0)
    m = wide_channel(100.0, 2e-4, manning_n=0.03)
    dm = (300 * 0.03 / (100.0 * math.sqrt(2e-4))) ** 0.6  # Q = (1/n) W d^(5/3) S^0.5
    assert math.isclose(m.depth(300.0), dm, rel_tol=1e-9)
    assert math.isclose(m.velocity(300.0), 300.0 / (100.0 * dm), rel_tol=1e-9)


def floodplain(n=40, qb=300.0, noise=0.03):
    """A channel 40 m wide that spills onto a floodplain above `qb`: width
    grows fast above it. Small deterministic scatter, as real measurements."""
    out = []
    for i in range(n):
        q = 10.0 * 1.15**i
        a = 3.0 * q**0.6 if q <= qb else 3.0 * qb**0.6 * (q / qb) ** 1.3
        w = 40.0 * q**0.1 if q <= qb else 40.0 * qb**0.1 * (q / qb) ** 1.8
        e = noise * (1 if i % 2 else -1)
        out.append(ChannelMeasurement(t=i, flow=q, area=a * (1 + e), width=w * (1 - e), velocity=None))
    return out


def test_a_floodplain_is_fitted_with_a_hinge():
    s = channel.fit(floodplain())
    assert s.q_break > 0
    assert 200 < s.q_break < 450
    assert s.area_b == pytest.approx(0.6, abs=0.1) and s.area_b2 == pytest.approx(1.3, abs=0.15)
    assert s.width_b2 > 1.2
    # continuous at the break, and the floodplain flow is slow, not extrapolated fast
    qb = s.q_break
    assert s.area(qb * 0.9999) == pytest.approx(s.area(qb * 1.0001), rel=1e-3)
    assert s.velocity(3000.0) < channel.fit(floodplain()[:15]).velocity(3000.0)


def test_one_regime_stays_a_single_power_law():
    assert channel.fit(synthetic(n=30)).q_break == 0.0


def test_between_a_hinged_and_a_plain_shape_blends_log_area():
    up = channel.fit(floodplain())
    down = channel.ChannelShape(8.0, 0.6, 40.0, 0.25, 1.0, 1.0, 20)
    mid = channel.between(up, down, 0.5)
    q = 1000.0
    assert mid.area(q) == pytest.approx((up.area(q) * down.area(q)) ** 0.5)
    assert channel.between(up, down, 0.0) is up and channel.between(up, down, 1.0) is down
