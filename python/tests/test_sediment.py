import math

import pytest

from fdmr_plugins.dsm2 import sediment


def test_relation_is_the_papers_log_log_line():
    # log10(SSC) = 0.85 log10(T) + 0.35: T = 10 gives 10^1.2
    assert sediment.ssc_from_turbidity(10.0, 0.85, 0.35) == pytest.approx(10 ** 1.2)
    assert sediment.ssc_from_turbidity(1.0, 1.16, 0.27) == pytest.approx(10 ** 0.27)
    assert sediment.ssc_from_turbidity(0.0, 0.85, 0.35) == 0.0  # a sensor's floor brings none


def test_stokes_diameter_matches_the_settling_velocity():
    # dsm2.toml's diameter is the Stokes diameter of ws = 0.25 mm/s in 10 degC water
    nu, ws, s, g = 1.307e-6, 0.25e-3, 2.65, 9.81  # GTM's own constants (sediment_variables.f90)
    d_mm = math.sqrt(18 * nu * ws / ((s - 1) * g)) * 1000
    assert d_mm == pytest.approx(0.019, abs=0.0005)


FLOWS = [("sac", "node", "330"), ("vernalis", "node", "17"), ("mtz", "node", "361"),
         ("dicu_drain_1", "node", "1"), ("dicu_div_bbid", "reservoir", "clifton_court")]


def test_study_assigns_every_inflow_and_names_its_class():
    main, counts = sediment.study_text(
        FLOWS, {"sac": ["sac"], "sjr": ["vernalis"], "sea": ["mtz"], "none": ["*"]},
        {"ero_coef": 0.0001, "sed_deposit_always": True}, 0.019, [("RVB", 430, "8399")])
    assert counts == {"sac": 1, "sjr": 1, "sea": 1, "none": 2}
    assert "/FDMR/SAC/SSC//15MIN/FDMR-LIVE/" in main
    assert "dicu_drain_1                    1  ssc   last  constant  0" in main
    assert "RESERVOIR_CONCENTRATION" in main and "clifton_court" in main
    assert "sed_deposit_always   true" in main and "ero_coef             0.0001" in main
    assert "all_channels   ssc           diameter      0.019" in main
    # every inflow is a boundary of the one class, 100% of its SSC
    for name, _, _ in FLOWS:
        assert f"{name:24s} mud      100.0" in main
    assert "RVB               430      8399  ssc" in main


def test_an_inflow_nothing_assigns_is_refused():
    with pytest.raises(ValueError, match="no sediment concentration for inflow vernalis"):
        sediment.study_text(FLOWS, {"sac": ["sac"], "sea": ["mtz"], "none": ["dicu_.*"]}, {}, 0.019, [])
