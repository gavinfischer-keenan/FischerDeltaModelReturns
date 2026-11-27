"""HEC-DSS time series through `dsstool` (runner/dsm2/dsstool.f90).

DSM2 reads and writes DSS files with times in Pacific Standard Time, all year.
Everything here takes and returns times in seconds since 1970 UTC and converts
at the edge. A DSS value is stamped at the end of its interval, and midnight is
written as 2400 of the day before, as DSS does itself.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

PST = 8 * 3600  # s: DSM2's clock is Pacific Standard Time, UTC-8 all year
MISSING = -901.0  # HEC-DSS's missing-value flag
INTERVALS = {"15MIN": 900, "1HOUR": 3600, "1DAY": 86_400}


def is_missing(v: float) -> bool:
    """DSS's missing markers are exactly -901 and -902. Real values can be far
    below -900, such as a flood tide running upstream."""
    return abs(v + 901.0) < 1e-3 or abs(v + 902.0) < 1e-3


def safe(v: float) -> float:
    """DSM2 takes a value of exactly 901 or 902, either sign, as DSS's missing
    marker; nudge such a value by a hundredth."""
    return v + 0.01 if abs(abs(v) - 901.0) < 1e-4 or abs(abs(v) - 902.0) < 1e-4 else v


def parts(path: str) -> list[str]:
    """A DSS path's six parts, A to F. A path written without its F part, such
    as /A/B/C//E/, gets an empty one."""
    p = path.strip()[1:-1].split("/") if path.strip().startswith("/") and path.strip().endswith("/") else []
    if len(p) == 5:
        p.append("")
    if len(p) != 6:
        raise ValueError(f"not a DSS path: {path!r}")
    return p


def interval(path: str) -> int:
    """The interval (s) a regular series' E part names."""
    e = parts(path)[4].upper()
    if e not in INTERVALS:
        raise ValueError(f"unsupported DSS interval {e!r} in {path}")
    return INTERVALS[e]


def label(t: int) -> tuple[str, str]:
    """DSS date and time labels for a UTC time, in Pacific Standard Time."""
    d = datetime.fromtimestamp(t - PST, timezone.utc)
    if d.hour == 0 and d.minute == 0:
        d -= timedelta(days=1)
        return d.strftime("%d%b%Y").upper(), "2400"
    return d.strftime("%d%b%Y").upper(), d.strftime("%H%M")


HEC_EPOCH = int(datetime(1899, 12, 31, tzinfo=timezone.utc).timestamp())  # HEC day 0


def hec_day(t: int) -> int:
    """HEC's day number (1 = 1 January 1900) of a UTC time's day in Pacific Standard Time."""
    return (t - PST - HEC_EPOCH) // 86_400


def is_irregular(path: str) -> bool:
    return parts(path)[4].upper().startswith("IR-")


class Dss:
    def __init__(self, tool: str) -> None:
        self.tool = tool

    def read(self, file: str, path: str, start: int, n: int) -> tuple[list[float | None], str, str]:
        """`n` values from the one stamped at UTC time `start`: (values, units, type).
        Missing values come back as None."""
        date, time = label(start)
        out = subprocess.run([self.tool, "read", file, path, date, time, str(n)], capture_output=True, text=True, check=True)
        lines = out.stdout.split("\n")
        units, kind, _, _ = (lines[0].split() + ["", "", "", ""])[:4]
        vals = [float(v) for v in lines[1:] if v.strip()]
        return [None if is_missing(v) else v for v in vals], units, kind

    def write(self, file: str, path: str, start: int, values: Sequence[float | None], units: str, kind: str) -> None:
        """Store values from the one stamped at UTC time `start`; None is written as missing."""
        date, time = label(start)
        text = "\n".join(str(MISSING if v is None else safe(v)) for v in values) + "\n"
        subprocess.run([self.tool, "write", file, path, date, time, units, kind], input=text, capture_output=True, text=True, check=True)

    def read_irregular(self, file: str, path: str, start: int, end: int) -> tuple[list[tuple[int, float]], str, str]:
        """An irregular series' (UTC time, value) pairs for the days from `start` to `end`."""
        out = subprocess.run([self.tool, "readir", file, path, str(hec_day(start)), str(hec_day(end))],
                             capture_output=True, text=True, check=True)
        lines = out.stdout.split("\n")
        units, kind, _, _, base = (lines[0].split() + ["", "", "", "", "0"])[:5]
        t0 = HEC_EPOCH + int(base) * 86_400 + PST
        pts = []
        for line in lines[1:]:
            if line.strip():
                m, v = line.split()
                if not is_missing(float(v)):
                    pts.append((t0 + int(m) * 60, float(v)))
        return pts, units, kind

    def write_irregular(self, file: str, path: str, points: Sequence[tuple[int, float]], units: str, kind: str) -> None:
        """Store (UTC time, value) pairs as an irregular series."""
        if not points:
            return
        base = hec_day(points[0][0])
        t0 = HEC_EPOCH + base * 86_400 + PST
        text = "\n".join(f"{(t - t0) // 60} {safe(v)}" for t, v in points) + "\n"
        subprocess.run([self.tool, "writeir", file, path, units, kind, str(base)], input=text, capture_output=True, text=True, check=True)
