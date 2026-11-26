import math

import numpy as np
import pytest

from fdmr_plugins.river_routing import heat


def test_vapour_pressure_is_the_textbook_value():
    # Saturation vapour pressure at 20 degC is about 17.5 mmHg.
    assert float(heat.vapour_mmhg(np.array([20.0]))[0]) == pytest.approx(17.5, abs=0.1)


def test_no_sun_at_night_and_about_a_kilowatt_at_a_clear_summer_noon():
    # 38.5 N 121.5 W: solar noon near 20:00 UTC on 21 June 2026.
    from calendar import timegm

    noon = timegm((2026, 6, 21, 20, 6, 0))  # solar noon at 121.5 W is about 20:06 UTC
    night = noon - 12 * 3600
    rs = heat.sunlight(np.array([noon, night]), 38.5, -121.5, 10.0, np.array([0.0, 0.0]))
    assert 850 < rs[0] < 1000 and rs[1] == 0.0
    cloudy = heat.sunlight(np.array([noon]), 38.5, -121.5, 10.0, np.array([1.0]))
    assert cloudy[0] == pytest.approx(rs[0] * 0.35, rel=1e-6)


def test_water_far_warmer_than_still_dry_night_air_cools():
    f = heat.net_flux(np.array([25.0]), np.array([5.0]), np.array([0.0]), np.array([3.0]), np.array([0.0]))
    assert f[0] < -200


def test_with_no_heat_exchange_the_release_temperature_is_carried_down_at_the_water_speed():
    n, steps, step = 20, 200, 900.0
    u = np.full((steps, n), 0.5)
    w = {k: np.zeros((steps, n)) for k in ("air_temperature", "dew_point", "wind_speed", "sunlight")}
    # Air and water at the same temperature, saturated and still: next to no exchange.
    w["air_temperature"][:] = 10.0
    w["dew_point"][:] = 10.0
    boundary = np.full(steps, 10.0)
    boundary[50:] = 14.0
    out = heat.run([1000.0] * n, u, np.full((steps, n), 2.0), np.full((steps, n), 50.0), np.full((steps, n), 100.0),
                   step, boundary, np.full(n, 10.0), w)
    # The front reaches 10 km (element 9's middle, 9.5 km) after about 19,000 s = 21 steps.
    i = 50 + int(9_500 / 0.5 / step)
    assert out[i + 4, 9] > 12.0 and out[i - 6, 9] < 12.0


def test_correction_meets_the_stations_and_fades_after_their_last_reading():
    t = np.arange(0, 48 * 3600, 3600.0)
    model = np.zeros((len(t), 5))
    x_el = np.array([500.0, 1500.0, 2500.0, 3500.0, 4500.0])
    meas = np.full(len(t), np.nan)
    meas[:24] = 2.0
    out = heat.correct(model, x_el, t, [(1500.0, meas)])
    assert out[10, 1] == pytest.approx(2.0) and out[10, 4] == pytest.approx(2.0)
    assert out[23 + 24, 1] == pytest.approx(2.0 * math.exp(-1), rel=1e-6)


def test_cloud_adds_long_wave_from_the_sky():
    clear = heat.net_flux(np.array([10.0]), np.array([10.0]), np.array([5.0]), np.array([2.0]), np.array([0.0]), 0.0)
    overcast = heat.net_flux(np.array([10.0]), np.array([10.0]), np.array([5.0]), np.array([2.0]), np.array([0.0]), 1.0)
    assert 40 < overcast[0] - clear[0] < 70  # 17 % more of about 300 W/m2
