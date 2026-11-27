"""The bloom forecast's classes (fdmr_runner.blooms, #28)."""

import numpy as np
import pytest

pytest.importorskip("fdmr_py")

from fdmr_runner.blooms import classes  # noqa: E402


def test_temperature_gates_and_salinity_masks():
    t = np.array([15.0, 19.0, 22.0, 25.0, 27.0, 27.0, np.nan])
    s = np.array([0.2, 0.2, 0.2, 0.2, 0.2, 6.0, 0.2])
    assert classes(t, s, 19.0, 25.0, 5.0).tolist() == [0, 1, 1, 2, 2, 0, -1]
