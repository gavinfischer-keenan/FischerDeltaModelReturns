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
    db.execute("INSERT INTO cycles (cycle, recorded, made) VALUES (?,?,?)", (100 * HOUR, 0, ""))
    index = {"now": 100 * HOUR + 2 * HOUR}
    assert ledger.record(db, tmp_path, tmp_path, index, Store()) == 0  # too soon: nothing read


def test_scores_measured_less_predicted_by_lead_time():
    db = sqlite3.connect(":memory:")
    db.executescript(ledger.SCHEMA)
    db.execute("INSERT INTO points VALUES (1, 'river', 'usgs', 'G', 'discharge', 'flow', 3, 'Gauge')")
    c0 = 1000 * HOUR
    db.executemany("INSERT INTO predictions (cycle, point, t, value) VALUES (?,?,?,?)", [
        (c0, 1, c0 + 1 * HOUR, 100.0),    # lead 1 h
        (c0, 1, c0 + 30 * HOUR, 110.0),   # lead 30 h
        (c0, 1, c0 + 31 * HOUR, 90.0),    # lead 31 h, no reading near it
        (c0, 1, c0 + 400 * HOUR, 50.0)])  # not yet come
    db.execute("INSERT INTO cycles (cycle, recorded, made) VALUES (?,?,?)", (c0, 0, ""))
    store = Store()
    store.put_values("usgs", "G", "discharge", "m3/s", [Value(c0 - 600, 100.0, None), Value(c0 + HOUR + 600, 104.0, None),
                                                       Value(c0 + 30 * HOUR - 900, 105.0, None), Value(c0 + 32 * HOUR, 70.0, None)])
    got = ledger.score(db, store, now=c0 + 40 * HOUR)
    assert got["cycles"] == 1 and got["cycles_by_kind"] == {"live": 1}
    assert [(g["kind"], g["lead"], g["pairs"], g["mean_misfit"]) for g in got["groups"]] == [("live", "0-6 h", 1, 4.0), ("live", "1-2 days", 1, -5.0)]
    # Persistence held the 100 read just before the cycle: missed by 4 and 5.
    assert got["points"][0]["leads"]["live 0-6 h"] == {"pairs": 1, "mean_misfit": 4.0, "rms_misfit": 4.0, "persistence_pairs": 1, "rms_persistence": 4.0, "skill_vs_persistence": 0.0}
    assert got["groups"][1]["skill_vs_persistence"] == 0.0


def test_lead_bands_cover_every_kept_lead():
    assert all(ledger._band(lead) for lead in ledger.LEADS_S)
    assert ledger._band(0) == "0-6 h" and ledger._band(15 * 24 * HOUR) == "5-15 days"


def test_archived_forecast_in_force_at_a_time(tmp_path):
    from fdmr_adapters import cnrfc

    d = tmp_path / "G"
    d.mkdir()
    for issue in ("2026112106", "2026112112", "2026112118"):
        (d / f"{issue}.zip").write_bytes(issue.encode())
    t = cnrfc.issue_time("2026112112") + 3600
    assert cnrfc.archived_issue(tmp_path, "G", t) == b"2026112112"
    assert cnrfc.archived_issue(tmp_path, "G", cnrfc.issue_time("2026112106") - 1) is None


def test_a_hindcast_never_replaces_the_live_cycle_at_the_same_time():
    """Both are kept and scored apart (#54)."""
    db = sqlite3.connect(":memory:")
    db.executescript(ledger.SCHEMA)
    db.execute("INSERT INTO points VALUES (1, 'river', 'usgs', 'G', 'discharge', 'flow', 3, 'Gauge')")
    c0 = 1000 * HOUR
    for kind, v in (("live", 100.0), ("hindcast", 96.0)):
        db.execute("INSERT OR REPLACE INTO cycles (cycle, kind, recorded, made) VALUES (?,?,?,?)", (c0, kind, 0, ""))
        db.execute("INSERT OR REPLACE INTO predictions (cycle, kind, point, t, value) VALUES (?,?,?,?,?)", (c0, kind, 1, c0 + HOUR, v))
    store = Store()
    store.put_values("usgs", "G", "discharge", "m3/s", [Value(c0, 100.0, None), Value(c0 + HOUR, 104.0, None)])
    got = ledger.score(db, store, now=c0 + 2 * HOUR)
    assert got["cycles_by_kind"] == {"hindcast": 1, "live": 1}
    assert {g["kind"]: g["mean_misfit"] for g in got["groups"]} == {"live": 4.0, "hindcast": 8.0}


def test_a_ledger_from_before_is_migrated_whole(tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(str(path))
    old.executescript("""
        CREATE TABLE cycles (cycle INTEGER PRIMARY KEY, recorded INTEGER NOT NULL, made TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'live');
        CREATE TABLE points (id INTEGER PRIMARY KEY, reach TEXT NOT NULL, source TEXT NOT NULL, site TEXT NOT NULL,
            parameter TEXT NOT NULL, field TEXT NOT NULL, element INTEGER NOT NULL, name TEXT,
            UNIQUE (reach, source, site, parameter, field, element));
        CREATE TABLE predictions (cycle INTEGER NOT NULL, point INTEGER NOT NULL, t INTEGER NOT NULL, value REAL NOT NULL,
            PRIMARY KEY (cycle, point, t)) WITHOUT ROWID;
        INSERT INTO cycles VALUES (10, 0, '', 'live'), (20, 0, '', 'hindcast');
        INSERT INTO predictions VALUES (10, 1, 11, 1.0), (20, 1, 21, 2.0);
    """)
    old.commit()
    old.close()
    db = ledger.connect(path)
    assert sorted(db.execute("SELECT cycle, kind, point, t, value FROM predictions")) == [(10, "live", 1, 11, 1.0), (20, "hindcast", 1, 21, 2.0)]
    assert sorted(db.execute("SELECT cycle, kind FROM cycles")) == [(10, "live"), (20, "hindcast")]
    db.execute("INSERT INTO cycles (cycle, kind, recorded, made) VALUES (10, 'hindcast', 0, '')")  # same time, other kind: allowed
    db.commit()
    db.close()
    assert ledger.connect(path).execute("SELECT COUNT(*) FROM cycles").fetchone()[0] == 3  # connecting again changes nothing
