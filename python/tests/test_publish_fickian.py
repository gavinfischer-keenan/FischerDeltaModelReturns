"""Each element's distance below a release before Fischer's dispersion holds."""

import math
from types import SimpleNamespace

import pytest

fdmr_py = pytest.importorskip("fdmr_py")

from fdmr_runner.publish import element_fickian  # noqa: E402


class Shape:
    def depth(self, q):
        return 2.0

    def width(self, q):
        return 100.0


def test_six_lagrangian_time_scales_at_the_median_flow():
    els = [SimpleNamespace(mid=500.0), SimpleNamespace(mid=1500.0)]
    # Three steps; the median flow's step has velocity 0.5 m/s in both elements.
    field = SimpleNamespace(elements=els, steps=3,
                            flow=[[10.0, 10.0], [50.0, 50.0], [30.0, 30.0]],
                            velocity=[[0.1, 0.1], [0.9, 0.9], [0.5, 0.5]])
    records = [SimpleNamespace(chainage=0.0, shape=Shape())]
    got = element_fickian(field, records, [1e-4])
    u_star = math.sqrt(9.81 * 2.0 * 1e-4)
    want = 1.8 * 0.5 * 50.0**2 / (2.0 * u_star)  # l = half of the 100 m width
    assert got == [round(want), round(want)]


def test_sourced_channels_take_their_own_slope_for_shear_velocity():
    """A weir pool's flat surface must not pair with the open river's uniform-flow depth."""
    from fdmr_runner.publish import segment_slopes

    a = SimpleNamespace(site="a", source="rws", channel={"slope": 1.2e-4})
    b = SimpleNamespace(site="b", source="rws", channel={"slope": 1.0e-4})
    c = SimpleNamespace(site="c", source="rws", channel={"slope": 1.0e-4})
    # The store is never read: every segment's gauges have a sourced channel.
    got = segment_slopes([a, b, c], {"a": 0.0, "b": 8000.0, "c": 60000.0}, None, {}, 0, 0)
    assert got == [pytest.approx(1.1e-4), pytest.approx(1.0e-4)]
