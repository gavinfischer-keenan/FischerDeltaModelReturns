"""Reading DSM2's text input files (.inp).

An input file is a list of blocks: a keyword line, a header line naming the
columns, rows, and END. The study's main file (hydro.inp, qual.inp) includes
others by path, and settings in config.inp's ENVVAR block fill in ${NAME}s.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

VAR = re.compile(r"\$\{([A-Za-z0-9_\-]+)\}")
# Blocks whose rows are settings, not included files.
SETTING_BLOCKS = {"SCALAR", "IO_FILE", "ENVVAR", "OUTPUT_TIME_SERIES_DUMMY"}


def strip_comment(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def blocks(text: str) -> list[tuple[str, list[str], list[list[str]]]]:
    """(keyword, header, rows) for each table in an input file. Rows are split
    like a shell would, so quoted fields with spaces stay whole."""
    out = []
    lines = [strip_comment(x) for x in text.splitlines()]
    lines = [x for x in lines if x.strip()]
    i = 0
    while i < len(lines):
        key = lines[i].strip()
        i += 1
        body = []
        while i < len(lines) and lines[i].strip().upper() != "END":
            body.append(lines[i])
            i += 1
        i += 1
        if key.upper() in ("CONFIGURATION", "GRID", "INITIAL_CONDITION", "HYDRO_TIME_SERIES", "QUAL_TIME_SERIES",
                           "OPERATION", "OUTPUT_TIME_SERIES", "GROUPS", "PARAMETER"):
            out.append((key.upper(), [], [[b.strip()] for b in body]))  # lists of included files
            continue
        if not body:
            out.append((key.upper(), [], []))
            continue
        header = body[0].split()
        rows = []
        for b in body[1:]:
            try:
                rows.append(shlex.split(b))
            except ValueError:
                rows.append(b.split())
        out.append((key.upper(), header, rows))
    return out


def envvars(text: str) -> dict[str, str]:
    """Settings in an ENVVAR block, as written (not yet expanded)."""
    env: dict[str, str] = {}
    for key, header, rows in blocks(text):
        if key == "ENVVAR":
            for r in rows:
                if len(r) >= 2:
                    env[r[0]] = r[1]
    return env


def expand(s: str, env: dict[str, str], depth: int = 0) -> str:
    if depth > 20:
        raise ValueError(f"settings refer to each other in a loop: {s}")
    out = VAR.sub(lambda m: env.get(m.group(1), m.group(0)), s)
    return out if out == s else expand(out, env, depth + 1)


def included(main: Path, env: dict[str, str]) -> list[Path]:
    """Every input file a study's main file includes, in order, resolved against
    the study folder and the settings."""
    out = []
    for key, _, rows in blocks(main.read_text(encoding="utf-8", errors="replace")):
        if key in ("CONFIGURATION",):
            continue
        if not rows or key in SETTING_BLOCKS:
            continue
        for r in rows:
            if len(r) == 1 and r[0].lower().endswith(".inp"):
                p = Path(expand(r[0], env))
                out.append(p if p.is_absolute() else (main.parent / p).resolve())
    return out


def series(text: str) -> list[tuple[str, str]]:
    """(FILE, PATH) of every time series an input file reads from a DSS file."""
    out = []
    for _, header, rows in blocks(text):
        up = [h.upper() for h in header]
        if "FILE" in up and "PATH" in up:
            fi, pi = up.index("FILE"), up.index("PATH")
            for r in rows:
                if len(r) > max(fi, pi) and r[pi].startswith("/"):
                    out.append((r[fi], r[pi]))
    return out


def set_envvars(text: str, values: dict[str, str]) -> str:
    """config.inp with the named ENVVAR settings replaced; others unchanged."""
    out, inside = [], False
    for line in text.splitlines():
        s = strip_comment(line).strip()
        if s.upper() == "ENVVAR":
            inside = True
        elif s.upper() == "END":
            inside = False
        elif inside and s:
            name = s.split()[0]
            if name in values:
                line = f"{name:24s} {values[name]}"
        out.append(line)
    return "\n".join(out) + "\n"
