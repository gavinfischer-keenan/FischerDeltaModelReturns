import pytest

from fdmr_runner import delta


def test_flat_unless_a_shape_is_given():
    assert delta.day_weights(None) == [1.0] * 24


def test_a_shape_keeps_the_days_total():
    by_day = [0.0] * 6 + [2.0] * 12 + [0.0] * 6  # released only from 06:00 to 18:00
    w = delta.day_weights(by_day)
    assert sum(w) == pytest.approx(24.0) and w[0] == 0.0 and w[12] == pytest.approx(2.0)


def test_a_bad_shape_is_refused():
    with pytest.raises(ValueError):
        delta.day_weights([1.0] * 23)
    with pytest.raises(ValueError):
        delta.day_weights([-1.0] + [1.0] * 23)
