"""One rule for a river forecast, live or in a hindcast (forecast.cnrfc_forecast, #74)."""

from pathlib import Path

from fdmr_adapters import cnrfc
from fdmr_runner import forecast
from fdmr_store import Store

ZIP = (Path(__file__).parent / "fixtures" / "cnrfc_2026112418_UpperSacramento.zip").read_bytes()
FC = {"source": "cnrfc", "group": "UpperSacramento", "point": "BDBC1"}


def test_a_hindcast_takes_the_issue_in_force_then_and_nothing_later(tmp_path):
    store = Store(str(tmp_path / "fdmr.sqlite"))
    (tmp_path / "cnrfc-issues" / "UpperSacramento").mkdir(parents=True)
    (tmp_path / "cnrfc-issues" / "UpperSacramento" / "2026112418.zip").write_bytes(ZIP)
    issued = cnrfc.issue_time("2026112418")
    t0, step, n = issued, 900, 96
    got = forecast.cnrfc_forecast(store, FC, t0, step, n, issued + 3600, t0 + n * step, hindcast=True)
    assert got is not None and any(v is not None for v in got)
    # Before the issue was made, a hindcast cannot know it: held instead.
    assert forecast.cnrfc_forecast(store, FC, t0, step, n, issued - 3600, t0 + n * step, hindcast=True) is None
    # Live, the forecast fetched into the store (none here) is used, not the archive.
    assert forecast.cnrfc_forecast(store, FC, t0, step, n, issued + 3600, t0 + n * step, hindcast=False) is None


def test_the_hindcast_note_says_revisions_are_used_as_revised():
    assert "revised" in forecast.HINDCAST_NOTE and "in force" in forecast.HINDCAST_NOTE
