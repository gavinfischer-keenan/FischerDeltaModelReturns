import numpy as np
import pytest

from fdmr_runner import levels


def test_a_late_smaller_tide_on_another_datum():
    step = 900
    t = np.arange(4 * 96) * step
    measured = 1.0 * np.sin(2 * np.pi * t / 44_712) + 5.0  # a local datum, 5 m up
    model = 0.8 * np.sin(2 * np.pi * (t - 1800) / 44_712)  # 30 minutes late, 80% of the range
    got = levels.compare(model, measured, step)
    assert got["lag_min"] == 30 and got["range_ratio"] == pytest.approx(0.8, abs=0.02)
    assert got["mean_offset_m"] == pytest.approx(-5.0, abs=0.05) and 0.0 < got["rms_m"] < 1.0


def test_too_little_overlap_is_no_check():
    step = 900
    model = np.sin(np.arange(96) / 5.0)
    measured = np.full(96, np.nan)
    measured[:20] = 0.0
    assert levels.compare(model, measured, step) is None
