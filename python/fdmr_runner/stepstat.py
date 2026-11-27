"""Run one step of a cycle and record how it went (#55, #36).

    python -m fdmr_runner.stepstat STEPS_FILE NAME -- COMMAND...

Runs COMMAND, then appends to STEPS_FILE (a JSON list being written by
runner/schedule/cycle.sh, without its closing bracket) one entry:
{"step", "ok", "seconds", "peak_mb"}. `peak_mb` is the largest resident
memory of any one process the step ran (the step's own, or a program it
started such as DSM2's HYDRO), from the operating system's account of its
finished children: what a step needs of the container's memory. Exits with
the command's own status.
"""

from __future__ import annotations

import json
import resource
import subprocess
import sys
import time
from pathlib import Path


def run(steps_file: Path, name: str, cmd: list[str]) -> int:
    t0 = time.time()
    code = subprocess.call(cmd)
    peak_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss  # Linux: kilobytes
    entry = {"step": name, "ok": code == 0, "seconds": round(time.time() - t0), "peak_mb": round(peak_kb / 1024)}
    first = not steps_file.exists() or steps_file.read_text(encoding="utf-8").strip() in ("", "[")
    with steps_file.open("a", encoding="utf-8") as f:
        f.write(("" if first else ",") + json.dumps(entry, separators=(",", ":")))
    return code


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    if len(a) < 4 or a[2] != "--":
        print(__doc__)
        return 2
    return run(Path(a[0]), a[1], a[3:])


if __name__ == "__main__":
    sys.exit(main())
