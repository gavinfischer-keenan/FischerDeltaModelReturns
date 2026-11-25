import pytest

from fdmr_runner.forecast import extend, on_grid


def test_on_grid_is_linear_between_hourly_samples():
    g = on_grid([(0, 10.0), (3600, 20.0)], 0, 900, 6)
    assert g[:5] == pytest.approx([10.0, 12.5, 15.0, 17.5, 20.0])
    assert g[5] is None  # past the last sample


def test_extend_starts_from_the_measured_value_and_closes_the_gap_over_a_day():
    measured = [100.0] * 5
    forecast = [90.0] * 20  # the forecast reads 10 low at now
    out, end = extend(measured, forecast, i_now=4, n=12, blend_steps=4)
    assert out[:5] == [100.0] * 5
    assert out[5] == pytest.approx(90 + 10 * 0.75)
    assert out[8] == pytest.approx(90.0)  # gap closed after blend_steps
    assert end == 11


def test_extend_holds_after_the_forecast_and_without_one():
    out, end = extend([5.0, 6.0, None], [6.0, 6.0, 6.0, 7.0], i_now=2, n=7, blend_steps=0)
    assert out[3] == 7.0 and out[6] == 7.0 and end == 3
    out, end = extend([5.0, 6.0, None], None, i_now=2, n=5, blend_steps=4)
    assert out[2] is None  # a measured gap stays a gap
    assert out[3:] == [6.0, 6.0] and end is None


def test_on_grid_leaves_long_gaps_empty():
    g = on_grid([(0, 1.0), (3600, 2.0), (6 * 3600, 7.0)], 0, 1800, 13, max_gap_s=2 * 3600)
    assert g[1] == pytest.approx(1.5)
    assert all(v is None for v in g[3:12])  # the five-hour gap is not filled
    assert g[12] == pytest.approx(7.0)


def test_fit_tide_recovers_shift_scale_and_offset():
    import math

    from fdmr_runner.forecast import fit_tide, shifted

    step = 900
    pred = [math.sin(2 * math.pi * i * step / 44_712) + 0.3 * math.sin(2 * math.pi * i * step / 86_164) for i in range(7 * 96)]
    obs = [None if v is None else 0.8 * v + 1.25 for v in shifted(pred, 2.0)]  # 30 minutes later, damped, raised
    shift, scale, offset = fit_tide(obs, pred, step)
    assert abs(shift - 2.0) < 0.1 and abs(scale - 0.8) < 0.01 and abs(offset - 1.25) < 0.01
