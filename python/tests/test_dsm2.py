import math
from calendar import timegm
from datetime import datetime, timezone

import pytest

from fdmr_plugins.dsm2 import dss, inp, study

CONFIG = """# a comment
ENVVAR
NAME                     VALUE
DSM2MODIFIER             hist_fc_mss      #Study name
VERSIONDATE              202312
HISTFLOWVERSION          DWR-DMS-${VERSIONDATE}
TSINPUTDIR               ../../timeseries
BNDRYINPUT               ${TSINPUTDIR}/hist.dss
START_DATE               01SEP2014
END
"""

BOUNDARY = """BOUNDARY_FLOW
NAME         NODE    SIGN  FILLIN    FILE           PATH
sac           330       1  last      ${BNDRYINPUT}  /FILL+CHAN/RSAC155/FLOW//15MIN/${HISTFLOWVERSION}/
vernalis       17       1  last      ${BNDRYINPUT}  /FILL+CHAN/RSAN112/FLOW//15MIN/${HISTFLOWVERSION}/
END
OPERATING_RULE
NAME     ACTION                                         TRIGGER
op       "SET gate_op(gate=x,device=y) TO ts(name=z)"   TRUE
END
SOURCE_FLOW
NAME        NODE    SIGN  FILLIN    FILE                   PATH
stockton      15       1  last      constant               0.0
END
"""


def test_dss_labels_are_pacific_standard_and_midnight_is_2400():
    assert dss.label(timegm((2026, 11, 1, 8, 0, 0))) == ("31OCT2026", "2400")
    assert dss.label(timegm((2026, 7, 1, 8, 15, 0))) == ("01JUL2026", "0015")
    assert dss.interval("/A/B/FLOW//15MIN/F/") == 900 and dss.interval("/A/B/X//1DAY/F/") == 86_400


def test_envvars_expand_and_rewrite():
    env = inp.envvars(CONFIG)
    assert env["DSM2MODIFIER"] == "hist_fc_mss"
    assert inp.expand("${BNDRYINPUT}", env) == "../../timeseries/hist.dss"
    assert inp.expand("/X/${HISTFLOWVERSION}/", env) == "/X/DWR-DMS-202312/"
    new = inp.envvars(inp.set_envvars(CONFIG, {"START_DATE": "01NOV2026", "BNDRYINPUT": "/run/b.dss"}))
    assert new["START_DATE"] == "01NOV2026" and new["BNDRYINPUT"] == "/run/b.dss"
    assert new["DSM2MODIFIER"] == "hist_fc_mss"


def test_series_finds_file_and_path_columns_only():
    assert inp.series(BOUNDARY) == [
        ("${BNDRYINPUT}", "/FILL+CHAN/RSAC155/FLOW//15MIN/${HISTFLOWVERSION}/"),
        ("${BNDRYINPUT}", "/FILL+CHAN/RSAN112/FLOW//15MIN/${HISTFLOWVERSION}/"),
    ]


def test_included_files_resolve_against_the_study(tmp_path):
    (tmp_path / "hydro.inp").write_text("CONFIGURATION\nconfig.inp\nEND\nSCALAR\nNAME VALUE\ntheta 0.6\nEND\n"
                                        "HYDRO_TIME_SERIES\n${DSM2INPUTDIR}/boundary.inp   #note\nEND\n")
    got = inp.included(tmp_path / "hydro.inp", {"DSM2INPUTDIR": str(tmp_path / "common")})
    assert got == [(tmp_path / "common" / "boundary.inp").resolve()]


def test_same_day_in_the_reference_year():
    t = timegm((2026, 11, 20, 8, 0, 0))  # midnight PST, 20 Nov 2026
    assert study.same_day_in(2024, t) == timegm((2024, 11, 20, 8, 0, 0))
    leap = timegm((2028, 2, 29, 20, 0, 0))
    assert study.same_day_in(2025, leap) == timegm((2025, 2, 28, 20, 0, 0))
    assert study.ae("/FILL+CHAN/rsac155/FLOW/01JAN2024/15MIN/DWR/") == "/FILL+CHAN/RSAC155/FLOW//15MIN/"
    assert study.ae("/FILL+CHAN/RSAC155/FLOW//15MIN/") == "/FILL+CHAN/RSAC155/FLOW//15MIN/"


def test_values_dsm2_would_take_as_missing_are_nudged():
    assert dss.safe(901.0) == 901.01 and dss.safe(-902.0) == -901.99
    assert dss.safe(900.0) == 900.0


def test_reference_days_keep_changes_within_the_day():
    H = 3600
    ref_day = timegm((2024, 11, 20, 8, 0, 0))  # midnight PST, 20 Nov 2024
    pts = [(ref_day - 5 * H, 0.0), (ref_day + 3 * H, 5.0), (ref_day + 9 * H, 0.0), (ref_day + 86_400 + 2 * H, 5.0)]
    t0 = timegm((2026, 11, 20, 8, 0, 0))
    out = study.reference_days(pts, 2024, t0, 2)
    assert out == [(t0, 0.0), (t0 + 3 * H, 5.0), (t0 + 9 * H, 0.0), (t0 + 86_400 + 2 * H, 5.0)]


def test_only_exact_markers_are_missing():
    assert dss.is_missing(-901.0) and dss.is_missing(-902.0)
    assert not dss.is_missing(-950.0) and not dss.is_missing(-900.5) and not dss.is_missing(901.0)


def test_gate_states_from_tidal_flow_through_the_gate():
    from fdmr_plugins.dsm2.gates import gate_states, states_by_rule

    H = 3600
    # Shut, leaking up to 16 m3/s with one 35 spike; then open, the tide
    # carrying 30 to 250 m3/s either way with two slack hours near zero; then
    # shut again. (Delta Cross Channel, autumn 2026: shut gates leaked up to
    # 16 m3/s hourly; open, daily means 60 to 120 m3/s.)
    q = [3.0, 16.0, 35.0, -4.0, 2.0, 1.0, 3.0, 2.0, 1.0, 4.0, 2.0, 1.0,
         120.0, 240.0, 90.0, 4.0, -2.0, -60.0, -30.0, 150.0, 200.0, 8.0, 3.0, 5.0, 2.0, 1.0]
    got = states_by_rule([(i * H, v) for i, v in enumerate(q)], {"open_above": 30.0, "max_gap_h": 8})
    # The spike, 10 hours before the next big reading, is noise. Open from the
    # first big reading (hour 12) to the last (hour 20); shut from hour 21.
    assert got == [(0, False), (12 * H, True), (21 * H, False)]
    # A run interrupted by more than the gap is two openings.
    got = gate_states([(i * H, v) for i, v in enumerate(q)], 30.0, max_gap_s=1 * H)
    assert got == [(0, False), (12 * H, True), (15 * H, False), (17 * H, True), (21 * H, False)]
    # Open from the start and still open at the end; shut throughout.
    assert gate_states([(0, 100.0), (H, 2.0), (2 * H, -80.0)], 30.0, 8 * H) == [(0, True)]
    assert gate_states([(0, 2.0), (H, 1.0)], 30.0, 8 * H) == [(0, False)]


def test_resample_to_hourly_and_daily():
    from fdmr_runner.delta import resample

    s = [float(i) for i in range(96 * 2 + 1)]  # two days and a step, 15-minute
    assert resample(s, 3600)[:3] == [0.0, 4.0, 8.0]
    d = resample(s, 86_400)
    assert d[0] == d[1] == sum(range(96)) / 96 and d[2] == sum(range(96, 192)) / 96


def test_qual_main_gains_hdf5_output_once():
    text = "IO_FILE\nMODEL TYPE IO INTERVAL FILE\nqual restart out 1mon x.qrf\nEND\n"
    out = study.with_hdf5_output(text, "${DSM2OUTPUTDIR}/q.h5")
    assert "qual       hdf5     out              15min    ${DSM2OUTPUTDIR}/q.h5" in out
    assert study.with_hdf5_output(out, "other.h5").count("hdf5") == 1


def test_aliases_of_replaced_files_are_followed():
    env = {"BNDRYINPUT": "../hist.dss", "QUALBNDINP": "${BNDRYINPUT}", "OTHER": "x.dss"}
    assert study.replaced_name("${QUALBNDINP}", env, ["BNDRYINPUT"]) == "BNDRYINPUT"
    assert study.replaced_name("${OTHER}", env, ["BNDRYINPUT"]) is None
    assert study.replaced_name("constant", env, ["BNDRYINPUT"]) is None


def test_irregular_settings_repeat_each_month():
    pst = dss.PST
    t0 = timegm((2026, 7, 27, 0, 0, 0)) + pst
    end = timegm((2026, 10, 15, 0, 0, 0)) + pst
    out = study.monthly([(t0, 5.0), (timegm((2026, 9, 10, 0, 0, 0)) + pst, 3.0)], end)
    months = [timegm((2026, m, 1, 0, 0, 0)) + pst for m in (8, 9, 10)]
    assert [t for t, _ in out] == [t0, months[0], months[1], timegm((2026, 9, 10, 0, 0, 0)) + pst, months[2]]
    assert [v for _, v in out] == [5.0, 5.0, 5.0, 3.0, 3.0]


def test_fingerprint_groups_take_inflows_by_name():
    from fdmr_plugins.dsm2 import fingerprint

    flows = [("sac", "node", "330"), ("yolo", "node", "316"), ("mtz", "node", "361"),
             ("dicu_drain_1", "node", "1"), ("dicu_drain_bbid", "reservoir", "clifton_court"), ("reg_san", "node", "335")]
    main, conc, out, counts = fingerprint.study_text({"sac": ["sac", "yolo"], "sea": ["mtz"], "ag": ["dicu_drain_.*"]}, flows)
    assert counts == {"sac": 2, "sea": 1, "ag": 2}
    assert "sac" in conc and "reg_san" not in conc
    assert "RESERVOIR_CONCENTRATION" in conc and "clifton_court" in conc
    # QUAL simulates only what an output asks for: one per group.
    assert out.count("fp_") == 3 and "qual_fp.h5" in main


def test_wet_bulb_gives_back_the_dew_points_vapour_pressure():
    from fdmr_plugins.dsm2 import temperature as tm

    assert tm.wet_bulb(68.0, 68.0, 29.92) == pytest.approx(68.0, abs=0.01)  # saturated air
    wb = tm.wet_bulb(86.0, 66.0, 29.92)  # 30 C at about 50% humidity
    assert 66.0 < wb < 86.0 and wb == pytest.approx(71.5, abs=1.5)
    e = 0.1001 * math.exp(0.03 * wb) - 0.0837 - 0.000367 * 29.92 * (86.0 - wb) * (1 + (wb - 32) / 1571)
    assert e == pytest.approx(tm.vapour_pressure(66.0), abs=1e-6)


def test_temperature_study_gives_every_inflow_a_series():
    from fdmr_plugins.dsm2 import temperature as tm

    flows = [("sac", "node", "330"), ("yolo", "node", "316"), ("dicu_drain_1", "node", "1"), ("potw_x", "node", "9"),
             ("res_drain", "reservoir", "clifton_court")]
    assign = {"sac": ["sac", "*"], "yolo": ["yolo"], "drainage": ["dicu_drain_.*", "res_drain"]}
    main, ts, out, counts = tm.study_text(flows, assign, {"lat": 38.05}, [("RVB", 430, "12521")])
    assert counts == {"sac": 2, "yolo": 1, "drainage": 2}
    assert "potw_x" in ts and "/FDMR/SAC/TEMP-WATER//15MIN/FDMR-LIVE/" in ts.split("potw_x", 1)[1].splitlines()[0]
    assert "RESERVOIR_CONCENTRATION" in ts and "INPUT_CLIMATE" in ts and "wet_bulb" in ts
    assert "lat              38.05" in main and "temp_outputs.inp" in main
    assert "RVB" in out and "430" in out
    with pytest.raises(ValueError):
        tm.study_text([("odd", "node", "2")], {"sac": ["sac"]}, {}, [])


def test_climate_in_quals_units():
    from fdmr_plugins.dsm2 import temperature as tm

    c = tm.Climate(0, [20.0], [10.0], [10.0], [1.2], [1013.25]).qual()
    assert c["dry_bulb"][1][0] == pytest.approx(68.0) and c["wind"][1][0] == pytest.approx(22.369, abs=0.01)
    assert c["atm_pressure"][1][0] == pytest.approx(29.92, abs=0.01) and c["cloud"][1][0] == 1.0


def test_drainage_is_a_daily_series():
    from fdmr_plugins.dsm2 import temperature as tm

    assert "/1DAY/" in tm.water_path("drainage") and "/15MIN/" in tm.water_path("sac")
    assert tm.daily([1.0] * 96 + [3.0] * 96) == [1.0, 1.0, 3.0]


def test_an_irregular_series_blocks_by_decade_or_year():
    from fdmr_plugins.dsm2 import dss

    t = lambda y, m, d: int(datetime(y, m, d, tzinfo=timezone.utc).timestamp()) + dss.PST  # noqa: E731
    p = "/HIST+GATE/FALSE_R/INSTALL//IR-DECADE/DWR-BDO_NAVD/"
    assert dss.irregular_blocks(p, t(2020, 6, 30), t(2022, 1, 2)) == ["/HIST+GATE/FALSE_R/INSTALL/01JAN2020/IR-DECADE/DWR-BDO_NAVD/"]
    assert len(dss.irregular_blocks(p, t(2017, 11, 30), t(2020, 2, 1))) == 2
    assert dss.irregular_blocks("/A/B/C//IR-YEAR/F/", t(2018, 3, 1), t(2019, 1, 5))[-1] == "/A/B/C/01JAN2019/IR-YEAR/F/"


def test_reservoir_out_ratio_changes_only_the_named_reservoirs_outflow():
    from fdmr_plugins.dsm2.study import with_reservoir_ratio

    text = """RESERVOIR
NAME AREA BOT_ELEV
franks_tract 141.786 -7.754
END
RESERVOIR_CONNECTION
RES_NAME               NODE    COEF_IN    COEF_OUT
franks_tract            103       3000        3000
franks_tract            216       1500        1500   # a comment
liberty                 322       7500        5625
END
"""
    out, changed = with_reservoir_ratio(text, {"franks_tract": 0.75})
    assert [(c["node"], c["coef_out"]) for c in changed] == [(103, 2250.0), (216, 1125.0)]
    rows = [r.split("#")[0].split() for r in out.splitlines()]
    assert ["franks_tract", "103", "3000", "2250"] in rows
    assert ["liberty", "322", "7500", "5625"] in rows  # untouched
    import pytest

    with pytest.raises(ValueError):
        with_reservoir_ratio(text, {"mildred": 0.75})


def test_side_by_side_gives_each_run_its_own_temp_folder_and_reports_each(tmp_path):
    """#37: runs after HYDRO at once, each with a temp folder of its own; a
    failure is returned for the caller to judge, not raised over the others."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for m in ("a.inp", "b.inp"):
        (run_dir / m).write_text("SCALAR\nNAME VALUE\ntemp_dir         ${TEMPDIR}\nEND\n")
    ok = tmp_path / "ok.sh"
    ok.write_text("#!/bin/sh\nexit 0\n")
    ok.chmod(0o755)
    bad = tmp_path / "bad.sh"
    bad.write_text("#!/bin/sh\nexit 3\n")
    bad.chmod(0o755)
    got = study.side_by_side([(str(ok), "a.inp", False), (str(bad), "b.inp", False)], run_dir, str(tmp_path / "qt"))
    assert isinstance(got["a.inp"], float) and isinstance(got["b.inp"], RuntimeError)
    temps = {(run_dir / m).read_text().split("temp_dir")[1].split()[0] for m in ("a.inp", "b.inp")}
    assert len(temps) == 2 and all(t.startswith(str(tmp_path / "qt")) for t in temps)
