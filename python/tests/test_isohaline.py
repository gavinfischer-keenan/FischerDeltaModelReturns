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
    # Five days at 15 minutes: a conductance falling up the axis, 1000 per
    # channel, with a strong M2 tide on top that the filter must remove.
    t0, step, steps = 1_794_355_200, 900, 96
    base = np.array([5000.0, 4000.0, 3000.0, 2000.0, 1000.0, 0.0])
    files = []
    for d in range(5):
        rows = []
        for i in range(steps):
            h = (d * steps + i) / 4
            rows.append(base + 800.0 * np.sin(2 * np.pi * h / 12.4206))
        p = tmp_path / f"day{d}.quality.fdf"
        fdmr_py.write_field_file(str(p), "net:quality", 1, 6, 1, steps, t0 + d * 86_400, step,
                                 [("ec", "uS/cm", True, np.array(rows).ravel().tolist())])
        files.append(p)
    s = isohaline.series(ax, files, "ec", 2640.0)
    assert s["t0"] == t0 and s["step_s"] == 3600 and len(s["km"]) == 120
    # The axis's channels hold 5000, 4000, 3000, 2000 and 1000 at 51 to 59 km:
    # 2640 lies between 3000 (55 km) and 2000 (57 km), 36% of the way.
    mid = s["km"][60]
    assert mid == pytest.approx(55 + 2 * 0.36, abs=0.05)
    # Within 36.5 hours of the record's ends no full tidal window fits: no position (#71).
    assert all(k is None for k in s["km"][:36]) and all(k is None for k in s["km"][-36:])
    assert all(k is not None for k in s["km"][37:83])


def test_a_missing_day_leaves_its_hours_missing_and_the_days_after_in_place(tmp_path):
    """No gap is closed up, and no hour is given an unaveraged value (#59, #71)."""
    ax = isohaline.Axis(straight_net(), [dict(a) for a in ANCHORS])
    t0, step, steps = 1_794_355_200, 900, 96
    base = np.array([5000.0, 4000.0, 3000.0, 2000.0, 1000.0, 0.0])
    files = []
    for d in (0, 1, 2, 4, 5, 6, 7, 8):  # day 3 is missing
        p = tmp_path / f"day{d}.quality.fdf"
        fdmr_py.write_field_file(str(p), "net:quality", 1, 6, 1, steps, t0 + d * 86_400, step,
                                 [("ec", "uS/cm", True, np.tile(base, steps).tolist())])
        files.append(p)
    s = isohaline.series(ax, files, "ec", 2640.0)
    assert s["t0"] == t0 and len(s["km"]) == 9 * 24
    # The missing day, and the 36.5 hours either side where a full tidal
    # window would reach into it: no position, never an unaveraged one (#71).
    assert all(k is None for k in s["km"][3 * 24 - 36:4 * 24 + 36])
    assert s["km"][6 * 24 + 6] == pytest.approx(55 + 2 * 0.36, abs=0.05)  # beyond them, where it was
    # Before it, every hour is within 36.5 hours of the record's start or of the gap.
    assert all(k is None for k in s["km"][:3 * 24])


def test_gate_state_and_structure_readings():
    from fdmr_runner import overlays
    from fdmr_store import Store, Value

    store = Store()
    now = 1_794_400_000
    # The gates shut in the last day: tidal flow through them, then a leak.
    flows = [(now - 20 * 3600 + h * 3600, (120.0 if h % 2 else -60.0) if h < 10 else 0.4) for h in range(20)]
    store.put_values("cdec", "DLC", "discharge", "m3/s", [Value(t, q) for t, q in flows])
    store.put_values("cdec", "HRO", "pumping", "m3/s", [Value(now - 30 * 3600, 146.3)])
    cfg = {"default_source": "cdec", "structures": [
        {"id": "dcc", "kind": "gate", "name": "Gates", "site": "DLC", "lon": 0.0, "lat": 0.0, "duration": "H",
         "show": ["discharge"], "state": {"live_gate": "DLC"}},
        {"id": "banks", "kind": "pumps", "name": "Pumps", "site": "HRO", "lon": 0.0, "lat": 0.0, "duration": "D", "show": ["pumping"]},
    ]}
    units: dict = {}
    gates = [{"site": "DLC", "parameter": "discharge", "open_above": 30.0, "max_gap_h": 8}]
    got = {s["id"]: s for s in overlays.structures(cfg, store, now, units, gates)}
    assert got["dcc"]["state"]["open"] is False
    assert got["dcc"]["state"]["changed_at"] == flows[10][0]
    assert got["dcc"]["readings"]["discharge"]["v"] == 0.4
    # A daily value a day and more old is still the latest.
    assert got["banks"]["readings"]["pumping"]["v"] == 146.3 and got["banks"]["daily"]
    assert units == {"discharge": "m3/s", "pumping": "m3/s"}
    groups = overlays.fetch_groups(cfg)
    assert groups == [("D", ["HRO"], ["pumping"]), ("H", ["DLC"], ["discharge"])]
    # Without the model's rule, no state is claimed.
    assert "state" not in overlays.structures(cfg, store, now, {})[0]


def test_landmarks_take_their_place_and_the_nearest_model_element(tmp_path):
    import json

    from fdmr_runner import overlays

    (tmp_path / "geometry").mkdir()
    (tmp_path / "geometry" / "net.json").write_text(json.dumps(straight_net()))
    # A 2D mesh of three nodes to the west.
    (tmp_path / "geometry" / "mesh.json").write_text(json.dumps({"lon": [-122.2, -122.1, -122.05], "lat": [38.0, 38.0, 38.005]}))
    index = {"reaches": [{"id": "net"}, {"id": "mesh"}]}
    cfg = {
        "default_source": "cdec",
        "stations": [{"site": "S1", "name": "Station", "lon": -121.985, "lat": 38.001}],
        "structures": [{"id": "pumps", "name": "Pumps", "lon": -121.98, "lat": 38.0295}],
        "landmarks": [
            {"id": "a", "name": "On the network", "at": "S1"},
            {"id": "b", "name": "At the pumps", "at": "pumps"},
            {"id": "c", "name": "In the mesh", "lon": -122.049, "lat": 38.004},
            {"id": "d", "name": "Far from both", "lon": -121.5, "lat": 38.5},
        ],
    }
    got = {lm["id"]: lm for lm in overlays.landmarks(cfg, tmp_path, index)}
    # S1 sits 0.11 km north of the second channel (between its ends, not at one).
    assert got["a"]["lon"] == -121.985 and got["a"]["model"]["reach"] == "net" and got["a"]["model"]["element"] == 1
    assert got["a"]["model"]["km"] == pytest.approx(0.111, abs=0.002)
    # At the end of the side channel.
    assert got["b"]["model"]["element"] == 5
    assert got["c"]["model"] == {"reach": "mesh", "element": 2, "km": pytest.approx(0.142, abs=0.002)}
    assert "model" not in got["d"]


def test_outflow_is_compared_day_by_day_and_split_dry_and_wet():
    from fdmr_runner.bay import compare_days

    model = {1: 100.0, 2: 200.0, 3: 300.0, 4: float("nan")}
    measured = {1: 110.0, 2: 260.0, 3: 290.0, 4: 50.0, 5: 70.0}
    from fdmr_runner import bay

    old = bay.MIN_SPLIT_DAYS
    bay.MIN_SPLIT_DAYS = 1
    try:
        got = compare_days(model, measured, {1: True, 2: False, 3: True})
    finally:
        bay.MIN_SPLIT_DAYS = old
    assert got["all"] == {"days": 3, "model_mean": 200.0, "measured_mean": 220.0, "difference": -20.0}
    assert got["dry"] == {"days": 2, "model_mean": 200.0, "measured_mean": 200.0, "difference": 0.0}
    assert got["wet"]["days"] == 1 and got["wet"]["difference"] == -60.0
    # Too few days to split: only the count.
    assert compare_days(model, measured, {1: True, 2: False, 3: True})["dry"] == {"days": 2}
