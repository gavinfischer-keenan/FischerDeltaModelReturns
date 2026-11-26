import sqlite3

import numpy as np
import pytest

from fdmr_runner import ledger
from fdmr_store import Store, Value

HOUR = 3600


def test_published_times_are_utc():
    assert ledger.stamp("1970-01-02T01:00Z") == 86_400 + HOUR


def test_values_between_steps_are_linear_and_none_outside():
    v = np.array([[0.0, 10.0], [4.0, 20.0]])
    got = ledger.at_times(1000, 900, v, np.array([1000, 1450, 1900, 3000]))
    assert got[1].tolist() == [2.0, 15.0] and np.isnan(got[3]).all()


def test_a_cycle_is_kept_only_when_the_last_is_six_hours_old(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    db.execute("INSERT INTO cycles VALUES (?,?,?)", (100 * HOUR, 0, ""))
    index = {"now": 100 * HOUR + 2 * HOUR}
    assert ledger.record(db, tmp_path, tmp_path, index, Store()) == 0  # too soon: nothing read


def test_scores_measured_less_predicted_by_lead_time():
    db = sqlite3.connect(":memory:")
    db.executescript(ledger.SCHEMA)
    db.execute("INSERT INTO points VALUES (1, 'river', 'usgs', 'G', 'discharge', 'flow', 3, 'Gauge')")
    c0 = 1000 * HOUR
    db.executemany("INSERT INTO predictions VALUES (?,?,?,?)", [
        (c0, 1, c0 + 1 * HOUR, 100.0),    # lead 1 h
        (c0, 1, c0 + 30 * HOUR, 110.0),   # lead 30 h
        (c0, 1, c0 + 31 * HOUR, 90.0),    # lead 31 h, no reading near it
        (c0, 1, c0 + 400 * HOUR, 50.0)])  # not yet come
    db.execute("INSERT INTO cycles VALUES (?,?,?)", (c0, 0, ""))
    store = Store()
    store.put_values("usgs", "G", "discharge", "m3/s", [Value(c0 + HOUR + 600, 104.0, None), Value(c0 + 30 * HOUR - 900, 105.0, None),
                                                       Value(c0 + 32 * HOUR, 70.0, None)])
    got = ledger.score(db, store, now=c0 + 40 * HOUR)
    assert got["cycles"] == 1
    assert [(g["lead"], g["pairs"], g["mean_misfit"]) for g in got["groups"]] == [("0-6 h", 1, 4.0), ("1-2 days", 1, -5.0)]
    assert got["points"][0]["leads"]["0-6 h"] == {"pairs": 1, "mean_misfit": 4.0, "rms_misfit": 4.0}


def test_lead_bands_cover_every_kept_lead():
    assert all(ledger._band(lead) for lead in ledger.LEADS_S)
    assert ledger._band(0) == "0-6 h" and ledger._band(15 * 24 * HOUR) == "5-15 days"
