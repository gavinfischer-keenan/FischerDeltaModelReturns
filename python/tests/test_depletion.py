"""Island water use following a live daily total, and the source it is read from."""

from calendar import timegm

import pytest

from fdmr_adapters import usbr_cvo
from fdmr_plugins.dsm2.study import adjust_depletion

# Rows as the November 2026 computation's text gives them (Reclamation CVO,
# doutdly.pdf, 25 Nov 2026): a dry day, a wet one, and a day still being filled in.
TEXT = """U.S. Bureau of Reclamation - Central Valley Operations Office
Delta Outflow Computation (values in c.f.s.)
Date NDCU NBA NDOI
11/01/26 7,152 170 9 282 2,109 2,011 2,044 9,722 1,700 457 952 84 5 88 1,575 1,610 6,447 7,161 6,447 14% 17% 15%
11/19/26 12,215 190 29 669 1,290 1,271 1,340 14,393 -15,188 3,491 2,344 79 8 88 5,993 5,956 23,588 12,889 8,397 40% 44% 56%
11/25/26 13,597 190 18 645 1,311 15,761
Total 260,694 4,670 446 14,398 34,559 34,077 35,871 314,767 (79,848) 56,308 42,300 1,884 197 2,038 102,332 90,975 276,522 231,229 174,238 - - -
"""


def test_parse_reads_complete_days_only():
    rows = usbr_cvo.parse(TEXT)
    day = lambda d: timegm((2026, 11, d, 0, 0, 0)) + 8 * 3600  # noqa: E731
    assert sorted(rows) == [day(1), day(19)]
    wet = rows[day(19)]
    assert wet["ndcu"] == -15188 and wet["ndoi"] == 23588 and wet["delta_inflow"] == 14393 and wet["delta_exports"] == 5993
    # The balance holds: NDOI = inflow - NDCU - exports (to rounding).
    for r in rows.values():
        assert r["delta_inflow"] - r["ndcu"] - r["delta_exports"] == pytest.approx(r["ndoi"], abs=2)
    with pytest.raises(ValueError):
        usbr_cvo.parse("some other page")


def test_adjust_depletion_meets_the_daily_total_and_keeps_the_pattern():
    # Two nodes: diversions, seepage and drainage over three days.
    series = {
        "/D/1/DIV-FLOW//1DAY/": [10.0, 10.0, 10.0], "/D/2/DIV-FLOW//1DAY/": [30.0, 30.0, 30.0],
        "/D/1/SEEP-FLOW//1DAY/": [2.0, 2.0, 2.0], "/D/2/SEEP-FLOW//1DAY/": [2.0, 2.0, 2.0],
        "/D/1/DRAIN-FLOW//1DAY/": [4.0, 4.0, 4.0], "/D/2/DRAIN-FLOW//1DAY/": [12.0, 12.0, None],
    }
    kinds = {p: p.split("/")[3] for p in series}
    use, ret = ("DIV-FLOW", "SEEP-FLOW"), ("DRAIN-FLOW",)
    # Reference net use: 44 - 16 = 28 on days 1 and 2; 44 - 4 = 40 on day 3 (one value missing).
    out, change = adjust_depletion(series, kinds, use, ret, [-12.0, 48.0, None])

    def net(i):
        return sum((1 if kinds[p] in use else -1) * (v[i] or 0.0) for p, v in out.items())

    # Wetter: 40 more returned, shared 1:3 as the nodes' drainage is.
    assert net(0) == pytest.approx(-12.0)
    assert out["/D/1/DRAIN-FLOW//1DAY/"][0] == pytest.approx(14.0) and out["/D/2/DRAIN-FLOW//1DAY/"][0] == pytest.approx(42.0)
    # Drier: 20 more taken, as diversions, shared 1:3.
    assert net(1) == pytest.approx(48.0)
    assert out["/D/1/DIV-FLOW//1DAY/"][1] == pytest.approx(15.0) and out["/D/2/DIV-FLOW//1DAY/"][1] == pytest.approx(45.0)
    # No target: unchanged, and a missing value stays missing.
    assert net(2) == pytest.approx(40.0) and out["/D/2/DRAIN-FLOW//1DAY/"][2] is None
    assert change == pytest.approx([-40.0, 20.0, 0.0])
    # Seepage is never touched.
    assert out["/D/1/SEEP-FLOW//1DAY/"] == [2.0, 2.0, 2.0]


def test_live_depletion_holds_the_last_dry_day_ahead():
    from fdmr_runner.delta import DAY, live_depletion
    from fdmr_store import Store, Value

    st = Store(":memory:")
    d0 = timegm((2026, 11, 20, 0, 0, 0)) + 8 * 3600
    st.put_values("usbr-cvo", "DOUT", "ndcu", "m3/s", [Value(d0, 46.7, None), Value(d0 + DAY, -430.0, None), Value(d0 + 2 * DAY, -60.0, None)])
    got = live_depletion(st, {"source": "usbr-cvo", "site": "DOUT", "parameter": "ndcu"}, d0 - 5 * DAY, d0 + 5 * DAY, d0 + 3 * DAY)
    assert got[d0 + DAY] == -430.0 and got[d0 + 2 * DAY] == -60.0
    # After the last report, the last day without rain.
    assert [got[d0 + k * DAY] for k in (3, 4, 5)] == [46.7, 46.7, 46.7]
    assert d0 - DAY not in got


def test_added_runoff_follows_the_rain_season_pattern_when_given():
    series = {"/D/1/DIV-FLOW//1DAY/": [10.0], "/D/2/DIV-FLOW//1DAY/": [10.0],
              "/D/1/DRAIN-FLOW//1DAY/": [9.0], "/D/2/DRAIN-FLOW//1DAY/": [1.0]}
    kinds = {p: p.split("/")[3] for p in series}
    # Irrigation drainage is all at node 1; the rain season's returns are even.
    out, _ = adjust_depletion(series, kinds, ("DIV-FLOW",), ("DRAIN-FLOW",), [0.0], "",
                              {"/D/1/DRAIN-FLOW//1DAY/": 5.0, "/D/2/DRAIN-FLOW//1DAY/": 5.0})
    assert out["/D/1/DRAIN-FLOW//1DAY/"][0] == pytest.approx(14.0) and out["/D/2/DRAIN-FLOW//1DAY/"][0] == pytest.approx(6.0)


def test_wetter_days_can_be_left_as_the_reference():
    series = {"/D/1/DIV-FLOW//1DAY/": [10.0, 10.0], "/D/1/DRAIN-FLOW//1DAY/": [2.0, 2.0]}
    kinds = {p: p.split("/")[3] for p in series}
    out, change = adjust_depletion(series, kinds, ("DIV-FLOW",), ("DRAIN-FLOW",), [-20.0, 12.0], wetter=False)
    # Day 1 is wetter than the reference's 8: kept. Day 2 drier: followed.
    assert out["/D/1/DRAIN-FLOW//1DAY/"][0] == 2.0 and change[0] == 0.0
    assert out["/D/1/DIV-FLOW//1DAY/"][1] == pytest.approx(14.0) and change[1] == pytest.approx(4.0)
