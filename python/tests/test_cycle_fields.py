"""Reading a reach's days back (#59): a missing day stays missing, in its place."""

import numpy as np
import pytest

fdmr_py = pytest.importorskip("fdmr_py")

from fdmr_runner import cycle_fields  # noqa: E402
from fdmr_runner.overlays import NetworkValues  # noqa: E402

DAY = 86_400
STEP = 900


def write_day(cdir, reach, day, d0, value, kind=".quality.fdf", name="ec"):
    (cdir / reach).mkdir(parents=True, exist_ok=True)
    steps, n = DAY // STEP, 2
    fdmr_py.write_field_file(str(cdir / reach / f"{day}{kind}"), reach, 1, n, 1, steps, d0, STEP,
                             [(name, "uS/cm", True, [float(value)] * steps * n)])


def test_a_missing_middle_day_is_nan_in_its_place_and_later_days_keep_their_times(tmp_path):
    t0 = 1_764_201_600  # a midnight, UTC
    write_day(tmp_path, "r", "2025-11-27", t0, 100)
    write_day(tmp_path, "r", "2025-11-29", t0 + 2 * DAY, 300)  # 28 November is missing
    t, step, got = cycle_fields.read_days(tmp_path, "r", ["2025-11-27", "2025-11-28", "2025-11-29"], ["ec"], "quality")
    assert (t, step) == (t0, STEP) and got["ec"].shape == (3 * DAY // STEP, 2)
    assert np.isnan(got["ec"][DAY // STEP]).all() and got["ec"][2 * DAY // STEP, 0] == 300
    v = cycle_fields.at_times(t, step, got["ec"], np.array([t0 + 3600, t0 + DAY + 3600, t0 + 2 * DAY + 3600]))
    assert v[0, 0] == 100 and np.isnan(v[1, 0]) and v[2, 0] == 300


def test_a_stations_model_value_after_a_missing_day_is_that_days(tmp_path):
    """NetworkValues used to join the days left end to end, a day off after a gap."""
    import json

    t0 = 1_764_201_600
    (tmp_path / "geometry").mkdir()
    (tmp_path / "geometry" / "r.json").write_text(json.dumps({"lines": [[[0, 0], [1, 0]], [[1, 0], [2, 0]]]}), encoding="utf-8")
    write_day(tmp_path, "r", "2025-11-27", t0, 100)
    write_day(tmp_path, "r", "2025-11-29", t0 + 2 * DAY, 300)
    nv = NetworkValues(tmp_path, {"id": "r", "time": {"days": ["2025-11-27", "2025-11-28", "2025-11-29"]}}, ["ec"])
    assert nv.at("ec", 0, t0 + 2 * DAY + 3600) == 300
    assert nv.at("ec", 0, t0 + DAY + 3600) is None
