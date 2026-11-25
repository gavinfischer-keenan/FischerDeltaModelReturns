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
