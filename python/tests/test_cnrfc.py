from calendar import timegm
from pathlib import Path

import pytest

from fdmr_adapters import cnrfc
from fdmr_store import Store

ZIP = (Path(__file__).parent / "fixtures" / "cnrfc_2026112418_UpperSacramento.zip").read_bytes()
CFS = 0.3048**3


def test_latest_file_picks_the_newest_issue():
    html = "2026112412_UpperSacramento_csv_export.zip ... 2026112418_UpperSacramento_csv_export.zip 2026112418_american_csv_export.zip"
    assert cnrfc.latest_file("UpperSacramento", html) == "2026112418_UpperSacramento_csv_export.zip"
    assert cnrfc.latest_file("Tulare", html) is None


def test_parse_reads_flow_columns_only_in_si():
    issue, flows = cnrfc.parse(ZIP, ["BDBC1", "CLUC1", "KWKC1L", "BDBC1L", "NOPE"])
    assert issue == "2026112418"
    assert set(flows) == {"BDBC1", "CLUC1", "KWKC1L"}  # BDBC1L is SQIN, not flow at the point
    t = timegm((2026, 11, 24, 18, 0, 0))
    v = next(x for x in flows["BDBC1"] if x.t == t)
    # 5.61 thousand cfs at Bend Bridge, the same as the USGS gauge at that hour
    assert v.value == pytest.approx(5609.94 * CFS, rel=1e-3)
    assert v.qualifier == "2026112418"


def test_fetch_stores_the_latest_issue():
    pages = {
        cnrfc.INDEX: b"x 2026112418_UpperSacramento_csv_export.zip y",
        f"{cnrfc.BASE}/csv/2026112418_UpperSacramento_csv_export.zip": ZIP,
    }
    store = Store()
    issue, n = cnrfc.CnrfcAdapter(store, get=pages.__getitem__).fetch("UpperSacramento", ["BDBC1", "WLKC1"])
    assert issue == "2026112418" and n > 0
    assert store.unit("cnrfc", "WLKC1", "discharge") == "m3/s"
