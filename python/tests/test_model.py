import pytest

from fdmr_plugins.river_routing import model
from fdmr_plugins.river_routing.channel import ChannelShape

SHAPE = ChannelShape(area_a=4.0, area_b=0.7, width_a=20.0, width_b=0.2, area_r2=1, width_r2=1, n=20)
STEP = 900


def test_elements_cover_the_river():
    els = model.elements_for(10_400.0)
    assert len(els) == 10
    assert els[0].start == 0 and els[-1].end == pytest.approx(10_400.0)
    assert all(e.length == pytest.approx(1040.0) for e in els)


def test_steady_flow_is_matched_everywhere():
    g = [model.GaugeRecord(0.0, SHAPE, [200.0] * 50), model.GaugeRecord(20_000.0, SHAPE, [200.0] * 50)]
    f = model.build(20_000.0, g, 0, STEP)
    assert all(abs(q - 200.0) < 1e-9 for row in f.flow for q in row)
    u = 200.0 / SHAPE.area(200.0)
    assert all(abs(v - u) < 1e-9 for row in f.velocity for v in row)
    assert f.volume(0, 0) == pytest.approx(SHAPE.area(200.0) * 1000.0)


def test_a_release_change_moves_downstream():
    n = 400
    up = [100.0] * 100 + [300.0] * (n - 100)
    down = [100.0] * 140 + [300.0] * (n - 140)  # the step reaches the lower gauge 10 hours later
    g = [model.GaugeRecord(0.0, SHAPE, up), model.GaugeRecord(40_000.0, SHAPE, down)]
    f = model.build(40_000.0, g, 0, STEP)
    near, far = 2, len(f.elements) - 3
    first_high = lambda e: next(i for i in range(n) if f.flow[i][e] > 150)  # noqa: E731
    assert first_high(near) < first_high(far)


def test_gaps_are_held():
    assert model.filled([None, 2.0, None, 4.0, None]) == [2.0, 2.0, 2.0, 4.0, 4.0]
    with pytest.raises(ValueError):
        model.filled([None, None])
