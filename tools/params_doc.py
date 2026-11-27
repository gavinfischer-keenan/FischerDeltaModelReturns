"""Write docs/parameters.md: every number the model is set with, where it is
set, and what the file says about it.

Two places hold the model's numbers: each region's settings
(regions/<id>/*.toml) and the constants at the top of the model's own modules
(python/fdmr_runner, python/fdmr_plugins: names in capitals). For each number
this lists where it is set (file and line, the table it belongs to), its
value, the comment written beside or above it (where its source is given),
and the references it cites (ref:<key>, linked to references.md). Places,
names and station codes are left out: coordinates, ids, channel numbers.
The date a number was set is the file's history (git log -L), linked.

Usage:
  python tools/params_doc.py           write docs/parameters.md
  python tools/params_doc.py --check   fail if it is out of date
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NUMBER = re.compile(r"^[+-]?(\d[\d_]*\.?\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?)$")
# Keys that place or name things rather than set the model.
NOT_PARAMETERS = {
    "lon", "lat", "bbox", "start", "end", "mouth", "via", "dam", "node", "nodes", "channel", "channels",
    "distance", "site", "sites", "id", "usgs_site", "point", "element", "x", "y", "box", "maxzoom",
    "chan_no", "reservoir", "group", "number", "zone", "to_km", "past_via_km", "length_km", "year",
    "reference_year", "max_snap_m", "join_m", "depth_cm", "long",
}
REF = re.compile(r"\bref:([a-z][a-z0-9_]*)")
# Module constants that are units, not settings: their note says so.
UNITS = {"DAY": "unit: seconds in a day", "HOUR": "unit: seconds in an hour",
         "FT": "unit: metres in an international foot (exact)", "INCH": "unit: metres in an inch (exact)"}


def tracked(pattern: str) -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", pattern], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return [ROOT / p for p in sorted(out.split("\n")) if p]
    except (OSError, subprocess.CalledProcessError):
        return sorted(ROOT.glob(pattern))


def numbers(value: str) -> list[tuple[str, str]]:
    """(subkey, number) pairs in a TOML value: a number, an array of numbers,
    or an inline table's numeric entries."""
    v = value.strip()
    if NUMBER.match(v):
        return [("", v)]
    if v.startswith("[") and v.endswith("]"):
        items = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        if items and all(NUMBER.match(x) for x in items):
            return [("", "[" + ", ".join(items) + "]")]
        return []
    if v.startswith("{") and v.endswith("}"):
        out = []
        for part in re.split(r",(?![^\[]*\])", v[1:-1]):
            if "=" in part:
                k, x = (s.strip() for s in part.split("=", 1))
                if k not in NOT_PARAMETERS:
                    out += [(k + (("." + s) if s else ""), n) for s, n in numbers(x)]
        return out
    return []


def toml_rows(path: Path) -> list[dict]:
    """The numbers in a TOML file. A table's comment (the block just above its
    header) goes with the table (`table_note`), a key's (the block just above
    it, and its line's own) with the key. A key with no comment of its own
    takes, in turn: the note of the same key with the same value in an earlier
    entry of the same array of tables (a setting repeated per reach, noted
    once); or the comment of the keys just above it (no blank line between:
    one commented group). An entry of an array of tables after the first, with
    no comment of its own, points to the first entry's note."""
    rows, table, table_note, block, counts, group, first_notes = [], "", "", [], {}, "", {}
    repeated: dict[tuple[str, str, str], tuple[str, str]] = {}  # (array, key, value): (where noted, note)
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if line.startswith("#"):
            block.append(line.lstrip("#").strip())
            continue
        if not line:
            block, group = [], ""
            continue
        m = re.match(r"^\[\[([^\]]+)\]\]", line) or re.match(r"^\[([^\]]+)\]", line)
        if m:
            name = m.group(1).strip()
            table_note = " ".join(block)
            if line.startswith("[["):
                counts[name] = counts.get(name, 0) + 1
                if counts[name] == 1:
                    first_notes[name] = table_note
                elif not table_note and first_notes.get(name):
                    table_note = f"See the note on `{name}[1]`."
                name = f"{name}[{counts[name]}]"
            table, block, group = name, [], ""
            continue
        m = re.match(r"^([A-Za-z0-9_\-\"]+)\s*=\s*(.*)$", line)
        if not m:
            block = []
            continue
        key, rest = m.group(1).strip('"'), m.group(2)
        value, _, inline = rest.partition(" #")
        inline_table = value.strip().startswith("{")
        own = " ".join(block + ([inline.strip()] if inline.strip() else []))
        if block:
            group = " ".join(block)
        if key in NOT_PARAMETERS and not inline_table:
            block = []
            continue
        array = re.sub(r"\[\d+\]$", "", table)
        for sub, n in numbers(value):
            k = key + (("." + sub) if sub else "")
            if own and array != table:
                repeated.setdefault((array, k, n), (table, own))
            seen = repeated.get((array, k, n)) if array != table else None
            note = own or (f"As in `{seen[0]}`: {seen[1]}" if seen and seen[0] != table else group)
            rows.append({"file": path.relative_to(ROOT).as_posix(), "line": i, "table": table, "table_note": table_note,
                         "key": key + (("." + sub) if sub else ""), "value": n, "note": note})
        block = []
    return rows


def code_rows(path: Path) -> list[dict]:
    rows = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, raw in enumerate(lines, 1):
        m = re.match(r"^([A-Z][A-Z0-9_]+)\s*(?::\s*[\w\[\], |]+)?=\s*([^#]+?)\s*(?:#\s*(.*))?$", raw)
        if not m or not NUMBER.match(m.group(2).replace(" ", "")) and not re.match(r"^[\d.eE+\-*/ ()]+$", m.group(2)):
            continue
        above = []
        j = i - 2
        while j >= 0 and lines[j].strip().startswith("#"):
            above.insert(0, lines[j].strip().lstrip("#").strip())
            j -= 1
        note = " ".join(above[-4:] + ([m.group(3)] if m.group(3) else [])) or UNITS.get(m.group(1), "")
        rows.append({"file": path.relative_to(ROOT).as_posix(), "line": i, "table": "(module constant)",
                     "key": m.group(1), "value": m.group(2).strip(), "note": note})
    return rows


def linked(s: str) -> str:
    return REF.sub(r"[\1](references.md#\1)", s.replace("|", "\\|"))


def build() -> str:
    out = ["# Parameters", "",
           "Every number the model is set with: in each region's settings and among the model's own constants. "
           "Generated by `tools/params_doc.py` from the files themselves, "
           "so it lists what runs. The note is the file's own comment beside or above the number, where its source "
           "is given; `ref:` keys link to [references.md](references.md). When a number was set, and why it "
           "changed, is in the file's history (click its line, then *History*). Places, names and station codes "
           "are left out. Do not edit by hand.", ""]
    regions = sorted({p.parent for p in tracked("regions/*/*.toml")})
    for region in regions:
        out += [f"## Region {region.name}", ""]
        for f in [p for p in tracked(f"regions/{region.name}/*.toml")]:
            rows = toml_rows(f)
            if not rows:
                continue
            out += [f"### {f.name}", "", "| Where | Setting | Value | Note and source |", "| --- | --- | --- | --- |"]
            shown = None
            for r in rows:
                if r["table"] != shown:  # the table's own note, once
                    shown = r["table"]
                    if r["table_note"]:
                        out.append(f"| | **`{r['table']}`** | | {linked(r['table_note'])} |")
                where = f"[{r['line']}](../{r['file']}#L{r['line']})"
                setting = f"`{r['table']}`<br>`{r['key']}`" if r["table"] else f"`{r['key']}`"
                out.append(f"| {where} | {setting} | `{r['value']}` | {linked(r['note'])} |")
            out.append("")
    out += ["## The model's own constants", "", "| Where | Constant | Value | Note and source |", "| --- | --- | --- | --- |"]
    for f in tracked("python/fdmr_runner/*.py") + tracked("python/fdmr_plugins/**/*.py"):
        for r in code_rows(f):
            out.append(f"| [{r['file']}:{r['line']}](../{r['file']}#L{r['line']}) | `{r['key']}` | `{r['value']}` | {linked(r['note'])} |")
    out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    text, path = build(), ROOT / "docs" / "parameters.md"
    if "--check" in argv:
        ok = path.exists() and path.read_text(encoding="utf-8") == text
        print("docs/parameters.md", "ok" if ok else "is out of date: run python tools/params_doc.py")
        return 0 if ok else 1
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(ROOT)}: {text.count(chr(10) + '| [')} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
