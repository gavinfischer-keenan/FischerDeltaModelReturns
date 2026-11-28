"""Writing what a run publishes, written once (#77): the time stamps, the
folder a run publishes into, the link that points readers at the newest one,
pruning the older ones, and a reach's certainty settings.

Every publisher (the rivers' cycle, the Delta, the Bay, the canals) builds a
folder of its own, then points a link (`latest`, or the cycle's `current`) at
it in one rename, so a reader sees the old folder or the new one, never half
of one. Its certainty settings are its reach's in region.toml, with no
default in code: a reach that gives none is refused.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

TIME_FMT = "%Y-%m-%dT%H:%MZ"  # a time in index.json and reach.json
FOLDER_FMT = "%Y%m%dT%H%MZ"  # a published folder's name
DAY = 86_400


def utc(t: float, fmt: str = TIME_FMT) -> str:
    """UTC time t (s since 1970) as published."""
    return datetime.fromtimestamp(t, timezone.utc).strftime(fmt)


def folder_name(t: float) -> str:
    """A published folder's name for UTC time t."""
    return utc(t, FOLDER_FMT)


def swap_link(out_dir: Path, target: str, link: str = "latest") -> None:
    """Point out_dir/link at out_dir/target in one rename: a reader opening
    the link sees the old folder or the new one, never neither."""
    out_dir = Path(out_dir)
    tmp = out_dir / f".{link}.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, out_dir / link)


def prune_folders(out_dir: Path, keep: int, current: str) -> list[str]:
    """Remove all but the newest `keep` published folders (by name), never
    `current`, never a link. Returns what was removed."""
    out_dir = Path(out_dir)
    olds = sorted(p for p in out_dir.iterdir() if p.is_dir() and not p.is_symlink() and p.name != current)
    gone = olds[: max(0, len(olds) - (keep - 1))]
    for p in gone:
        shutil.rmtree(p)
    return [p.name for p in gone]


def certainty_for(reaches: list[dict], reach: str) -> tuple[float, float]:
    """A reach's certainty settings from region.toml: how long its forecast
    stays solid, s, and the floor certainty falls to (specification 8.2)."""
    rc = next((r for r in reaches if r["id"] == reach), None)
    c = (rc or {}).get("certainty")
    if not c or "solid_days" not in c or "floor" not in c:
        raise ValueError(f"reach {reach}: region.toml gives no certainty = {{ solid_days, floor }}")
    return float(c["solid_days"]) * DAY, float(c["floor"])
