"""The model runner's one lock (#86): whatever runs DSM2 or publishes takes
it, so two never run at once on a machine whose memory one Delta cycle nearly
fills (#36). The cycles take it in runner/schedule/cycle.sh (flock -w); the
experiments and trials take it here, by default. A run started by a cycle
(the nightly regime) already holds it, through the cycle, and must not take
it again: flock on a second open of the file would wait on itself.

The wait is limited: a run that cannot get the lock in time stops, rather
than queueing without end behind cycles, and says how long it waited.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

CYCLE_LOCK = Path(os.environ.get("FDMR_DATA", "/srv/fdmr-data")) / ".cycle.lock"
# The experiments' own lock (#39): one experiment at a time, never holding up
# a cycle. Memory allows it (#36: a Delta cycle peaks near 1.7 GB, one DSM2
# run near 0.75 GB, of 8 GB), and an experiment runs at low CPU priority
# (EXPERIMENT_NICE), so a cycle beside it always comes first.
EXPERIMENT_LOCK = Path(os.environ.get("FDMR_DATA", "/srv/fdmr-data")) / ".experiment.lock"
EXPERIMENT_NICE = 15
WAIT_S = 30 * 60  # half an hour: longer than a Delta cycle (about 15 min), shorter than an hour of hourly cycles queueing
POLL_S = 5.0


@contextmanager
def held(path: Path | str | None = None, wait_s: float = WAIT_S, what: str = "", sleep=time.sleep) -> Iterator[float]:
    """Hold the lock (the cycle's by default) for the `with` block; yields
    how long it waited, s. Raises TimeoutError after `wait_s`."""
    path = Path(path) if path else CYCLE_LOCK
    with open(path, "a") as f:
        asked = time.monotonic()
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - asked > wait_s:
                    raise TimeoutError(f"waited {wait_s:.0f} s for {path}{f' ({what})' if what else ''}") from None
                sleep(POLL_S)
        waited = time.monotonic() - asked
        print(f"lock: waited {waited:.0f} s for {path}{f' ({what})' if what else ''}", flush=True)
        try:
            yield waited
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
