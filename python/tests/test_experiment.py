from pathlib import Path

import pytest

from fdmr_runner import experiment as ex

REGION = Path(__file__).resolve().parents[2] / "regions" / "sf-bay-delta"
D = ex.DAY


def test_an_override_holds_its_value_then_gives_back_the_original():
    pts = [(0, 1.0), (10 * D, 0.0), (20 * D, 1.0), (40 * D, 0.0)]
    out = ex.overridden(pts, 5.0, 15 * D, 30 * D)
    assert out == [(0, 1.0), (10 * D, 0.0), (15 * D, 5.0), (30 * D, 1.0), (40 * D, 0.0)]
    assert ex.overridden(pts, 0.0, 5 * D, None) == [(0, 1.0), (5 * D, 0.0)]


def test_a_run_gets_the_value_in_force_at_its_start():
    pts = [(0, 1.0), (10 * D, 0.0), (20 * D, 1.0)]
    assert ex.in_run(pts, 12 * D, 30 * D) == [(12 * D, 0.0), (20 * D, 1.0)]


def test_daily_means_need_eighteen_hours():
    first = ex.midnight("2021-07-01") + ex.STEP
    vals = [1.0] * 96 + [2.0] * 71 + [None] * 25
    d = ex.daily(vals, first)
    assert d == {"2021-07-01": 1.0}


def test_net_flow_takes_out_the_tide():
    import math

    q = [100.0 + 500.0 * math.sin(2 * math.pi * i / (12.42 * 4)) for i in range(96 * 10)]
    sub = [v for v in ex.subtidal(q) if v is not None]
    assert sub and max(abs(v - 100.0) for v in sub) < 10.0


def test_the_score_says_which_scenario_is_closer_and_the_effect():
    days = [f"2021-07-{k:02d}" for k in range(1, 31)]
    exp = {"name": "x", "title": "t", "what": "w", "run_from": "2021-01-01", "judge_from": "2021-07-01",
           "judge_to": "2021-08-01", "effect_from": "2021-07-01", "effect_to": "2021-08-01",
           "scenario": [{"name": "as-built", "label": "a"}, {"name": "none", "label": "b", "without": True}],
           "station": [{"name": "BET", "place": "p", "quantity": "ec"}]}
    series = {"as-built": {"BET/ec": {d: 900.0 for d in days}}, "none": {"BET/ec": {d: 1300.0 for d in days}}}
    measured = {("BET", "ec"): {d: 880.0 for d in days}}
    r = ex.score(exp, series, measured, {})
    st = r["stations"][0]
    assert st["closer"] == "as-built"
    assert st["effect"]["mean"] == 400.0
    assert st["scenarios"]["as-built"]["in_place"]["mean_difference"] == 20.0
    assert st["monthly"]["measured"] == {"2021-07": 880.0}


def test_every_experiment_is_complete_and_has_its_measurements():
    exps = ex.load(REGION)
    assert {e["name"] for e in exps} >= {"false-river-barrier-2021", "suisun-gates-2018"}
    for e in exps:
        assert e["run_from"] < e["judge_from"] <= e["effect_from"] < e["effect_to"] <= e["judge_to"]
        assert sum(1 for s in e["scenario"] if s.get("without")) == 1
        for s in e["scenario"]:
            for o in s.get("override", []):
                assert o["path"].startswith("/HIST+GATE/") and o["path"].count("/") == 7
        measured = ex.read_table(REGION / "experiments" / e["name"] / "measured.csv", "quantity")
        for st in e["station"]:
            days = [d for d in measured.get((st["name"], st["quantity"]), {}) if e["judge_from"] <= d < e["judge_to"]]
            assert len(days) >= 60, (e["name"], st["name"], st["quantity"], len(days))


def test_a_reservoirs_ec_is_read_from_quals_hdf5_output(tmp_path):
    """A station inside one of DSM2's reservoirs is set against the reservoir's water (#42)."""
    h5py = pytest.importorskip("h5py")
    import numpy as np

    from fdmr_plugins.dsm2 import output

    path = tmp_path / "qual_ec.h5"
    t0 = 1_764_201_600  # 2025-11-27 00:00 UTC: 2025-11-26 16:00 PST
    with h5py.File(path, "w") as f:
        f.create_dataset("output/reservoir_names", data=np.array([b"clifton_court", b"franks_tract"]))
        f.create_dataset("output/constituent_names", data=np.array([b"ec"]))
        c = np.zeros((10, 1, 2), dtype=np.float32)
        c[:, 0, 1] = np.arange(10) * 10.0
        d = f.create_dataset("output/reservoir concentration", data=c)
        d.attrs["start_time"] = [b"2025-11-26 16:00:00"]
        d.attrs["interval"] = [b"15min"]
    r = output.reservoir_concentration(path, "ec", "franks_tract")
    assert r.step == 900 and r.t0 == t0
    assert r.sampled(t0 + 2 * 900, 4, 900) == [20.0, 30.0, 40.0, 50.0]
    assert r.sampled(t0 - 900, 2, 900) == [None, 0.0]
    assert output.reservoir_concentration(path, "ec", "no_such") is None


def test_a_channels_flow_is_read_at_the_tidefiles_own_step(tmp_path):
    """Where DWR's output files name no station, a channel's flow (#41), read
    at the step the tidefile records (#78)."""
    h5py = pytest.importorskip("h5py")
    import numpy as np

    from fdmr_plugins.dsm2 import output

    (tmp_path / "output").mkdir()
    lines = ["ENVVARS", "NAME VALUE", "DSM2MODIFIER   hist   #Study name", "X ${DSM2MODIFIER}_qual", "END"]
    (tmp_path / "config.inp").write_text("".join(x + chr(10) for x in lines))
    assert output.run_modifier(tmp_path) == "hist"
    t0 = 1_764_201_600  # 2025-11-27 00:00 UTC: 2025-11-26 16:00 PST
    with h5py.File(tmp_path / "output" / "hist.h5", "w") as f:
        f.create_dataset("hydro/geometry/channel_number", data=np.array([118, 119]))
        q = np.zeros((40, 2, 2))
        q[:, 1, 0], q[:, 1, 1] = np.arange(40) * 10.0, np.arange(40) * 10.0 + 20.0  # cfs at the two ends
        d = f.create_dataset("hydro/data/channel flow", data=q)
        d.attrs["start_time"] = [b"2025-11-26 16:00:00"]
        d.attrs["interval"] = [b"5min"]
    s = output.channel_flow(tmp_path, 119)
    cfs = 0.3048**3
    assert s.step == 300
    # records 3, 6, 9: every 15 minutes
    assert s.sampled(t0 + 900, 3, 900) == pytest.approx([(30 + 10) * cfs, (60 + 10) * cfs, (90 + 10) * cfs])
    assert s.sampled(t0 - 900, 2, 900)[0] is None
    assert output.channel_flow(tmp_path, 999) is None
    with pytest.raises(ValueError):
        s.sampled(t0, 2, 400)  # not a whole number of the file's steps


def test_a_series_step_is_read_from_its_interval():
    from fdmr_plugins.dsm2 import output

    assert [output.interval_s(x) for x in (b"5min", "15min", b"1hour", "1day")] == [300, 900, 3600, 86_400]
    with pytest.raises(ValueError):
        output.interval_s("1mon")
