import math

import pytest

from fdmr_plugins.river_routing import calibration as cal
from fdmr_plugins.river_routing.channel import ChannelShape

SHAPE = ChannelShape(area_a=4.0, area_b=0.7, width_a=20.0, width_b=0.2, area_r2=1, width_r2=1, n=20)
STEP = 900
STATIONS = [cal.Station(0.0, SHAPE), cal.Station(30_000.0, SHAPE), cal.Station(70_000.0, SHAPE)]


def test_water_travel_is_length_over_velocity_and_follows_the_factor():
    v = SHAPE.velocity(200.0)
    assert cal.water_travel(STATIONS, 0, 70_000, 200.0) == pytest.approx(70_000 / v)
    # Water twice as fast in the second segment only.
    t = cal.water_travel(STATIONS, 0, 70_000, 200.0, factors=[1.0, 2.0])
    assert t == pytest.approx(30_000 / v + 40_000 / (2 * v))
    assert cal.wave_travel(STATIONS, 0, 200.0) == pytest.approx(30_000 / v / 1.5)


def test_running_mean_removes_a_daily_cycle():
    per_day = 86_400 // STEP
    x = [10 + math.sin(2 * math.pi * i / per_day) for i in range(5 * per_day)]
    m = cal.running_mean(x, per_day)
    inside = [v for v in m if v is not None]
    assert len(inside) > 3 * per_day
    assert max(inside) - min(inside) < 0.02


def test_tracer_events_find_the_water_travel_time():
    per_day = 86_400 // STEP
    n = 40 * per_day
    lag_steps = 60  # 15 hours
    # Slow temperature swings a few days long, plus a daily cycle at both gauges.
    slow = [3 * math.sin(2 * math.pi * i / (3.3 * per_day)) + 2 * math.sin(2 * math.pi * i / (7.1 * per_day)) for i in range(n + lag_steps)]
    daily = [math.sin(2 * math.pi * i / per_day) for i in range(n)]
    up = [slow[i + lag_steps] + daily[i] for i in range(n)]
    down = [slow[i] + 0.5 * daily[i] for i in range(n)]
    flow = [200.0] * n
    ev = cal.tracer_events(up, down, flow, 0, STEP, lambda q: 12 * 3600.0)
    assert len(ev) >= 5
    assert all(abs(e.lag - lag_steps * STEP) < 1800 for e in ev)


def test_scale_needs_three_events_and_is_clamped():
    assert cal.scale([1.2, 1.3]) is None
    assert cal.scale([1.2, 1.3, 1.4]) == pytest.approx(1.3)
    assert cal.scale([9, 9, 9]) == cal.SCALE_LIMITS[1]
    # Events that disagree say nothing.
    assert cal.scale([0.5, 1.0, 1.9, 0.7, 1.6]) is None


def test_skill_and_blend():
    s = cal.skill([(3600 * 10, 3600 * 12), (3600 * 14, 3600 * 12)])
    assert s == {"n": 2, "mae_h": 2.0, "bias_h": 0.0}
    assert cal.skill([]) == {"n": 0}
    assert cal.blend(1.0, 2.0) == pytest.approx(1.3)
    assert cal.blend(None, 2.0) == 2.0 and cal.blend(1.5, None) == 1.5
