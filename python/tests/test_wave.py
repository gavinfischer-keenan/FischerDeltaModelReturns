import pytest

fdmr_py = pytest.importorskip("fdmr_py")

from fdmr_plugins.river_routing import wave  # noqa: E402

STEP = 900
HOUR = 3600


def stepped_release(levels, hold_steps):
    out = []
    for q in levels:
        out += [q] * hold_steps
    return out


def test_recovers_a_known_travel_time_law():
    # A release that steps between flows every 3 days, routed downstream with a
    # known law T = a·Q^b (faster at higher flow), with some smoothing.
    a, b = 20 * HOUR * 100**0.3, -0.3  # 20 h at 100 m3/s
    levels = [100, 160, 120, 250, 180, 400, 300, 140, 220, 90, 330, 200]
    up = stepped_release(levels, 3 * 24 * HOUR // STEP)
    down = fdmr_py.route(up, float(STEP), a, b, 2 * HOUR, 0.0)
    events = wave.measure_events(up, down, 0, STEP)
    assert len(events) >= 9
    f = wave.fit(events)
    assert f is not None
    assert f.b == pytest.approx(b, abs=0.08)
    assert f.travel_time(200) == pytest.approx(a * 200**b, rel=0.1)


def test_gaps_are_skipped_not_guessed():
    up = stepped_release([100, 200, 100, 200], 3 * 24 * HOUR // STEP)
    down = fdmr_py.route(up, float(STEP), 10 * HOUR, 0.0, 0.0, 0.0)
    down = list(down)
    first_change = 3 * 24 * HOUR // STEP
    for i in range(first_change + 10, first_change + 30):
        down[i] = None
    events = wave.measure_events(up, down, 0, STEP)
    assert all(abs(e.t - first_change * STEP) > HOUR for e in events)
    assert all(e.lag == pytest.approx(10 * HOUR, abs=STEP) for e in events)


def test_regular_resamples_onto_a_grid():
    pairs = [(0, 1.0), (900, 2.0), (2700, 4.0)]
    assert wave.regular(pairs, 0, 900, 4) == [1.0, 2.0, None, 4.0]
