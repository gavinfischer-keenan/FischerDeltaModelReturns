import numpy as np
import pytest

import fdmr_py
from fdmr_runner import isohaline


def straight_net():
    """Five channels in a row eastward along 38 N, 0.01 degrees each, and a
    side channel off the third junction that the axis must not take."""
    nodes = [{"id": k + 1, "lon": -122.0 + 0.01 * k, "lat": 38.0} for k in range(6)]
    nodes.append({"id": 7, "lon": -121.98, "lat": 38.03})
    elements, lines = [], []
    for k in range(5):
        a, b = nodes[k], nodes[k + 1]
        elements.append({"id": 10 + k, "up": a["id"], "down": b["id"], "start_m": 0.0, "end_m": 877.0})
        lines.append([[a["lon"], a["lat"]], [b["lon"], b["lat"]]])
    elements.append({"id": 99, "up": 3, "down": 7, "start_m": 0.0, "end_m": 3300.0})
    lines.append([[nodes[2]["lon"], 38.0], [-121.98, 38.03]])
    return {"nodes": nodes, "elements": elements, "lines": lines}


ANCHORS = [{"site": "A", "lon": -122.0, "lat": 38.0, "km": 50}, {"site": "B", "lon": -121.95, "lat": 38.0, "km": 60}]


def test_chain_follows_the_axis_not_the_side_channel():
    ax = isohaline.Axis(straight_net(), [dict(a) for a in ANCHORS])
    assert [i for i, _ in ax.links] == [0, 1, 2, 3, 4]
    # Anchors map to their km; channel midpoints spread evenly between.
    assert ax.km(ax.d_anchor).tolist() == pytest.approx([50, 60])
    assert ax.mid_km.tolist() == pytest.approx([51, 53, 55, 57, 59], abs=1e-6)


def test_crossing_walks_up_from_the_sea():
    km = np.array([51.0, 53.0, 55.0, 57.0])
    assert isohaline.crossing(km, np.array([5000.0, 3000.0, 2000.0, 1000.0]), 2640.0) == (pytest.approx(53 + 2 * 360 / 1000), "")
    assert isohaline.crossing(km, np.array([2000.0, 1000.0, 500.0, 100.0]), 2640.0) == (None, "seaward")
    assert isohaline.crossing(km, np.array([9000.0, 8000.0, 7000.0, 6000.0]), 2640.0) == (None, "landward")
    assert isohaline.crossing(km, np.full(4, np.nan), 2640.0) == (None, "missing")


def test_series_is_tidally_averaged_and_hourly(tmp_path):
    ax = isohaline.Axis(straight_net(), [dict(a) for a in ANCHORS])
    # Three days at 15 minutes: a conductance falling up the axis, 1000 per
    # channel, with a strong M2 tide on top that the filter must remove.
    t0, step, steps = 1_794_355_200, 900, 96
    base = np.array([5000.0, 4000.0, 3000.0, 2000.0, 1000.0, 0.0])
    files = []
    for d in range(3):
        rows = []
        for i in range(steps):
            h = (d * steps + i) / 4
            rows.append(base + 800.0 * np.sin(2 * np.pi * h / 12.4206))
        p = tmp_path / f"day{d}.quality.fdf"
        fdmr_py.write_field_file(str(p), "net:quality", 1, 6, 1, steps, t0 + d * 86_400, step,
                                 [("ec", "uS/cm", True, np.array(rows).ravel().tolist())])
        files.append(p)
    s = isohaline.series(ax, files, "ec", 2640.0)
    assert s["t0"] == t0 and s["step_s"] == 3600 and len(s["km"]) == 72
    # The axis's channels hold 5000, 4000, 3000, 2000 and 1000 at 51 to 59 km:
    # 2640 lies between 3000 (55 km) and 2000 (57 km), 36% of the way.
    mid = s["km"][36]
    assert mid == pytest.approx(55 + 2 * 0.36, abs=0.05)
