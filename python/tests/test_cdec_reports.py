import pytest

from fdmr_adapters import cdec_reports as cr
from fdmr_store import Store

RES = """<table><tr><td>Reservoir Name</td><td>StaID</td><td>Capacity(AF)</td><td>Elevation(FT)</td><td>Storage(AF)</td>
<td>Storage Change(AF)</td><td>% of Capacity</td><td>Average Storage(AF)</td><td>% of Average</td><td>Outflow(CFS)</td></tr>
<tr><td>FEATHER RIVER</td></tr>
<tr><td>OROVILLE</td><td>ORO</td><td>3,424,753</td><td>733.48</td><td>1,451,653</td><td>1,283</td><td>42</td><td>1,765,986</td><td>82</td><td>2,048</td></tr>
<tr><td>CAMANCHE</td><td>CMN</td><td>417,120</td><td>---</td><td>---</td><td>---</td><td>---</td><td>245,194</td><td>---</td><td>---</td></tr></table>"""

PAGE6 = """<table><tr><td>Station</td><td>ID</td><td>Coop. Agency</td><td>Elev (FT)</td><td>Apr 1 Avg (IN)</td><td>Today (IN)</td>
<td>Percent Apr 1</td><td>24 Hrs Ago (IN)</td><td>1 Week Ago (IN)</td></tr>
<tr><td>FEATHER RIVER</td><td></td></tr>
<tr><td>Kettle Rock</td><td>KTL</td><td>DWR-BECK</td><td>7,300</td><td>22.4</td><td>2.0 r</td><td>9%</td><td>0.0 r</td><td>0.0 r</td></tr>
<tr><td>Rattlesnake</td><td>RTL</td><td>DWR/O&amp;M</td><td>6,210</td><td>16.3</td><td>---</td><td>---</td><td>---</td><td>---</td></tr>
<tr><td>Bucks Lake</td><td>BKL</td><td>DWR/O&amp;M</td><td>5,873</td><td>17.6</td><td>6.0</td><td>34%</td><td>0.0</td><td>0.0</td></tr></table>"""

SECTIONS = """<table><tr><td>NORTH</td><td>Number of Stations Reporting</td><td>34</td><td>34</td></tr>
<tr><td></td><td>Average snow water equivalent</td><td>0.2"</td><td>0.2"</td></tr>
<tr><td></td><td>Percent of April 1 Reporting</td><td>1%</td><td>0%</td></tr>
<tr><td></td><td>Percent of Normal for this date</td><td>12%</td><td>0%</td></tr>
<tr><td>Statewide Summary</td><td>STATE</td><td>Number of Stations Reporting</td><td>114</td><td>114</td></tr></table>"""


def test_numbers_as_dwr_writes_them():
    assert [cr.number(s) for s in ("1,234", "0.0 r", "12%", '0.2"', "---", "")] == [1234.0, 0.0, 12.0, 0.2, None, None]


def test_reservoir_report_gives_capacity_and_the_average_for_the_date():
    got = {r["site"]: r for r in cr.parse_storage(RES)}
    assert got["ORO"]["capacity_af"] == 3_424_753 and got["ORO"]["average_af"] == 1_765_986
    assert got["CMN"]["capacity_af"] == 417_120


def test_snow_report_keeps_reviewed_values_and_skips_stations_not_reporting():
    got = cr.parse_snow(PAGE6)
    assert [(s["site"], s["today_in"]) for s in got] == [("KTL", 2.0), ("RTL", None), ("BKL", 6.0)]
    assert {s["group"] for s in got} == {"FEATHER RIVER"}


def test_section_summary():
    got = cr.parse_sections(SECTIONS)
    assert got["NORTH"] == {"stations": 34, "swe_in": 0.2, "percent_april1": 1, "percent_normal": 12}
    assert got["STATE"] == {"stations": 114}


def test_basin_group_is_its_stations_total_against_their_april_1_total():
    store = Store()
    pages = {"RES": RES, "PAGE6": PAGE6, "DLYSWEQ": SECTIONS}
    reports = cr.CdecReports(store, get=lambda url: pages[url.rsplit("=", 1)[1]])
    now = 1_800_000_000
    reports.fetch_snow(now)
    reports.fetch_storage(["ORO"], now)
    t = reports.day(now)
    v = lambda site, p: store.values("cdec", site, p, t, t + 1)[0].value  # noqa: E731
    assert v("snow-feather-river", "snow_stations") == 2  # Rattlesnake did not report
    assert v("snow-feather-river", "snow_water_equivalent") == pytest.approx(4.0 * 25.4)
    assert v("snow-feather-river", "snow_percent_april1") == pytest.approx(100 * 8.0 / 40.0)
    assert v("snow-north", "snow_percent_normal") == 12
    assert v("ORO", "reservoir_capacity") == pytest.approx(3_424_753 * cr.AF)
    assert store.values("cdec", "CMN", "reservoir_capacity", t, t + 1) == []  # only the reservoirs asked for


def test_report_day_is_pacific_standard_midnight():
    assert cr.CdecReports(Store()).day(86_400 + 7 * 3600) == 8 * 3600  # 23:00 PST the day before
