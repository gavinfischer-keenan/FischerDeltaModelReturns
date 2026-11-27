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
