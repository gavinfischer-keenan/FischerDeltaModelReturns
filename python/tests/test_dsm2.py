from calendar import timegm

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


def test_gate_states_need_two_readings_to_change():
    from fdmr_runner.delta import gate_states

    H = 3600
    q = [0.5, -0.2, 40.0, 0.1, 38.0, 41.0, 39.0, 1.0, 0.3, 0.0]
    got = gate_states([(i * H, v) for i, v in enumerate(q)], 5.0)
    # The lone 40 at hour 2 is noise; open from hour 4; shut from hour 7.
    assert got == [(0, False), (4 * H, True), (7 * H, False)]


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
