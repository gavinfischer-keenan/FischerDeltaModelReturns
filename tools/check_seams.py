"""Check the module seams (specification section 4.3).

Reads seams.toml and fails if:
  - a module depends on another module its `may_use` does not list;
  - a data-only module contains code;
  - an engine module names a place from any region's guard words
    (regions/*/region.toml, [guard] words), so nothing place-specific
    creeps into the engine (specification section 4.1).

Standard library only. Usage: python tools/check_seams.py [repo root]
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

CODE_SUFFIXES = {".rs", ".py", ".ts", ".js", ".svelte", ".c", ".f90", ".for", ".sh"}
SKIP_DIRS = {"target", "node_modules", "__pycache__", "dist", ".venv"}


def files(root: Path, suffixes: set[str]) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in suffixes and not SKIP_DIRS & set(p.parts):
            out.append(p)
    return out


def rust_deps(mod: dict, root: Path, by_crate: dict[str, str]) -> set[str]:
    cargo = tomllib.loads((root / mod["path"] / "Cargo.toml").read_text(encoding="utf-8"))
    names: set[str] = set()
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        names |= set(cargo.get(section, {}))
    return {by_crate[n] for n in names if n in by_crate}


def python_deps(mod: dict, root: Path, by_package: dict[str, str]) -> set[str]:
    found: set[str] = set()
    for f in files(root / mod["path"], {".py"}):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                tops = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                tops = [node.module.split(".")[0]]
            else:
                continue
            found |= {by_package[t] for t in tops if t in by_package}
    return found


TS_IMPORT = re.compile(r"""(?:import|export)[^'"]*?from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]""")


def ts_deps(mod: dict, root: Path, by_path: dict[str, str]) -> set[str]:
    found: set[str] = set()
    base = root / mod["path"]
    for f in files(base, {".ts", ".js", ".svelte"}):
        for m in TS_IMPORT.finditer(f.read_text(encoding="utf-8")):
            spec = m.group(1) or m.group(2)
            if not spec.startswith("."):
                continue
            target = (f.parent / spec).resolve()
            for path, name in by_path.items():
                if target.is_relative_to((root / path).resolve()):
                    found.add(name)
    return found


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    modules: dict[str, dict] = tomllib.loads((root / "seams.toml").read_text(encoding="utf-8"))["modules"]
    by_crate = {m["crate"]: n for n, m in modules.items() if m["kind"] == "rust"}
    by_package = {m["package"]: n for n, m in modules.items() if "package" in m}  # Python import names
    by_path = {m["path"]: n for n, m in modules.items()}

    guard: set[str] = set()
    for region in sorted((root / "regions").glob("*/region.toml")):
        guard |= set(tomllib.loads(region.read_text(encoding="utf-8")).get("guard", {}).get("words", []))
    guard_re = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(guard)) + r")\b", re.I) if guard else None

    # Data sources are named only through their adapters (#66): a string
    # literal of an adapter's SOURCE in other engine code is a problem, except
    # in the files seams.toml [source_names] allows (the fetch dispatcher).
    seams = tomllib.loads((root / "seams.toml").read_text(encoding="utf-8"))
    allowed = {root / f for f in seams.get("source_names", {}).get("allowed", [])}
    sources = sorted({m.group(1) for f in (root / "python" / "fdmr_adapters").glob("*.py")
                      for m in re.finditer(r'(?m)^SOURCE = "([a-z0-9-]+)"', f.read_text(encoding="utf-8"))})
    source_re = re.compile(r"""["'](""" + "|".join(re.escape(x) for x in sources) + r""")["']""") if sources else None

    problems: list[str] = []
    for name, mod in modules.items():
        path = root / mod["path"]
        if not path.exists():
            continue  # a module not written yet
        kind = mod["kind"]
        if kind == "data":
            code = files(path, CODE_SUFFIXES)
            problems += [f"{name}: data-only module contains code: {c.relative_to(root)}" for c in code]
            continue
        deps = {
            "rust": lambda: rust_deps(mod, root, by_crate),
            "python": lambda: python_deps(mod, root, by_package),
            "ts": lambda: ts_deps(mod, root, by_path),
        }[kind]() - {name}
        for bad in sorted(deps - set(mod["may_use"])):
            problems.append(f"{name}: depends on {bad}, which seams.toml does not allow")
        if mod.get("engine") and guard_re:
            for f in files(path, CODE_SUFFIXES):
                for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if m := guard_re.search(line):
                        problems.append(f"{name}: place-specific word '{m.group(1)}' in {f.relative_to(root)}:{i}")
        if mod.get("engine") and kind == "python" and name != "adapters" and source_re:
            for f in files(path, CODE_SUFFIXES):
                if f in allowed:
                    continue
                for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    if (m := source_re.search(line)) and not line.lstrip().startswith("#"):
                        problems.append(f"{name}: source '{m.group(1)}' named outside its adapter in {f.relative_to(root)}:{i}")

    for p in problems:
        print(p)
    checked = [n for n, m in modules.items() if (root / m["path"]).exists()]
    print(f"seams: {len(checked)} modules checked, {len(guard)} guard words, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
