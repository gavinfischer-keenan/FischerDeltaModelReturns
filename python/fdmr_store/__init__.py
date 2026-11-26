"""The local data store (specification section 6.2).

Every fetched value is kept, with its source and the time it was fetched, so a
source outage degrades the forecast instead of stopping it. Values are stored
in SI units. Times are seconds since 1970-01-01 UTC.

The store depends on nothing else in the project; data adapters depend only on it.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    id        INTEGER PRIMARY KEY,
    source    TEXT NOT NULL,
    site      TEXT NOT NULL,
    parameter TEXT NOT NULL,
    unit      TEXT NOT NULL,
    UNIQUE (source, site, parameter)
);
CREATE TABLE IF NOT EXISTS obs (
    series_id  INTEGER NOT NULL REFERENCES series(id),
    t          INTEGER NOT NULL,
    value      REAL,
    qualifier  TEXT,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (series_id, t)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS channel_measurements (
    source     TEXT NOT NULL,
    site       TEXT NOT NULL,
    t          INTEGER NOT NULL,
    flow       REAL NOT NULL,   -- m3/s
    area       REAL,            -- m2
    width      REAL,            -- m
    velocity   REAL,            -- m/s
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (source, site, t)
);
"""


@dataclass(frozen=True)
class Value:
    t: int
    value: float | None
    qualifier: str | None = None


@dataclass(frozen=True)
class ChannelMeasurement:
    t: int
    flow: float
    area: float | None
    width: float | None
    velocity: float | None


class Store:
    """A local store of time series and channel measurements, in one SQLite file."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = Path(path)
        self._db = sqlite3.connect(str(path))
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _series_id(self, source: str, site: str, parameter: str, unit: str) -> int:
        row = self._db.execute(
            "SELECT id, unit FROM series WHERE source=? AND site=? AND parameter=?",
            (source, site, parameter),
        ).fetchone()
        if row:
            if row[1] != unit:
                raise ValueError(f"{source}/{site}/{parameter} is stored in {row[1]}, not {unit}")
            return row[0]
        cur = self._db.execute(
            "INSERT INTO series (source, site, parameter, unit) VALUES (?,?,?,?)",
            (source, site, parameter, unit),
        )
        return int(cur.lastrowid)

    def put_values(self, source: str, site: str, parameter: str, unit: str, values: Iterable[Value]) -> int:
        """Store values, replacing any already held for the same times (later revisions win).
        Returns how many were written."""
        sid = self._series_id(source, site, parameter, unit)
        now = int(time.time())
        rows = [(sid, v.t, v.value, v.qualifier, now) for v in values]
        with self._db:
            self._db.executemany(
                "INSERT INTO obs (series_id, t, value, qualifier, fetched_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT (series_id, t) DO UPDATE SET value=excluded.value, "
                "qualifier=excluded.qualifier, fetched_at=excluded.fetched_at",
                rows,
            )
        return len(rows)

    def values(self, source: str, site: str, parameter: str, start: int, end: int) -> list[Value]:
        """Values with start <= t < end, in time order."""
        return [
            Value(t, v, q)
            for t, v, q in self._db.execute(
                "SELECT o.t, o.value, o.qualifier FROM obs o JOIN series s ON s.id = o.series_id "
                "WHERE s.source=? AND s.site=? AND s.parameter=? AND o.t >= ? AND o.t < ? ORDER BY o.t",
                (source, site, parameter, start, end),
            )
        ]

    def unit(self, source: str, site: str, parameter: str) -> str | None:
        row = self._db.execute(
            "SELECT unit FROM series WHERE source=? AND site=? AND parameter=?", (source, site, parameter)
        ).fetchone()
        return row[0] if row else None

    def latest(self, source: str, site: str, parameter: str) -> tuple[int, int] | None:
        """(time of the latest value, when it was fetched), or None."""
        row = self._db.execute(
            "SELECT o.t, o.fetched_at FROM obs o JOIN series s ON s.id = o.series_id "
            "WHERE s.source=? AND s.site=? AND s.parameter=? ORDER BY o.t DESC LIMIT 1",
            (source, site, parameter),
        ).fetchone()
        return (row[0], row[1]) if row else None

    def coverage(self, source: str, site: str, parameter: str) -> tuple[int, int, int] | None:
        """(first time, last time, number of values) held for a series, or None."""
        row = self._db.execute(
            "SELECT MIN(o.t), MAX(o.t), COUNT(*) FROM obs o JOIN series s ON s.id = o.series_id "
            "WHERE s.source=? AND s.site=? AND s.parameter=?",
            (source, site, parameter),
        ).fetchone()
        return (row[0], row[1], row[2]) if row and row[2] else None

    def put_channel_measurements(self, source: str, site: str, ms: Iterable[ChannelMeasurement]) -> int:
        now = int(time.time())
        rows = [(source, site, m.t, m.flow, m.area, m.width, m.velocity, now) for m in ms]
        with self._db:
            self._db.executemany(
                "INSERT INTO channel_measurements VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT (source, site, t) DO UPDATE SET flow=excluded.flow, area=excluded.area, "
                "width=excluded.width, velocity=excluded.velocity, fetched_at=excluded.fetched_at",
                rows,
            )
        return len(rows)

    def channel_measurements(self, source: str, site: str) -> list[ChannelMeasurement]:
        return [
            ChannelMeasurement(*r)
            for r in self._db.execute(
                "SELECT t, flow, area, width, velocity FROM channel_measurements "
                "WHERE source=? AND site=? ORDER BY t",
                (source, site),
            )
        ]
