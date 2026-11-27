"""Each cycle mode's last run, kept beside the published cycles (#55).

    python -m fdmr_runner.cycle_runs FILE MODE STARTED STATUS

FILE is a small JSON file, {mode: {"started", "finished", "status"}}, one
entry per mode (hourly, delta, nightly), each replaced by that mode's latest
run. STATUS is the cycle's exit status (0: every step worked; anything else:
a step failed), or `unit-failed` when systemd ended the cycle itself (its
OnFailure unit: a timeout or a crash), for check.html to show. Written whole
and renamed into place, so a reader never sees half of it.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def record(path: Path, mode: str, started: int, status: str, now: int | None = None) -> dict:
    """Set `mode`'s last run in the file at `path`; returns the whole record."""
    runs = {}
    if path.exists():
        try:
            runs = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            runs = {}
    t = int(time.time() if now is None else now)
    if status == "unit-failed" and runs.get(mode, {}).get("finished", 0) >= t - 120:
        return runs  # cycle.sh itself has just recorded how it ended: a failed step, not systemd
    ok = status == "0"
    runs[mode] = {"started": int(started) or None, "finished": t,
                  "status": "ok" if ok else ("ended by systemd (timeout or crash)" if status == "unit-failed" else "a step failed")}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(runs, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    return runs


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    if len(a) != 4:
        print(__doc__)
        return 2
    record(Path(a[0]), a[1], int(a[2]) if a[2].isdigit() else 0, a[3])
    return 0


if __name__ == "__main__":
    sys.exit(main())
