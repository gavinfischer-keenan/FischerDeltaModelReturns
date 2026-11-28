"""A folder of kept raster tiles (#92): what a satellite adapter keeps of each
pass, and the one way the runner reads it back.

The format: one compressed NumPy archive per tile, `<id>.npz`, of named
arrays (each adapter documents its own; every tile has at least its pass time
`t`, UTC s), written whole or not at all. Beside them, empty marker files
`<id>.<mark>` say a tile was looked at and why nothing is kept (such as
`none`: the pass held no clear water), or that a further product was taken
from it (such as `land`), so it is not downloaded again.

The tile cache sits beside the store (the store's own folder); it holds what
is too large for the store's rows: grids, not series.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import numpy as np

SUFFIX = ".npz"


class TileCache:
    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)

    def exists(self) -> bool:
        return self.folder.exists()

    def _path(self, tile_id: str, mark: str | None = None) -> Path:
        return self.folder / (f"{tile_id}.{mark}" if mark else f"{tile_id}{SUFFIX}")

    def put(self, tile_id: str, **arrays) -> None:
        """Keep a tile's arrays, replacing any kept before, whole or not at all."""
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / f".{tile_id}.tmp{SUFFIX}"
        np.savez_compressed(tmp, **arrays)
        os.replace(tmp, self._path(tile_id))

    def has(self, tile_id: str, mark: str | None = None) -> bool:
        """Whether the tile is kept (or, with `mark`, so marked)."""
        return self._path(tile_id, mark).exists()

    def mark(self, tile_id: str, mark: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self._path(tile_id, mark).touch()

    def ids(self) -> list[str]:
        """Every kept tile's id, in order of its file's name."""
        if not self.folder.exists():
            return []
        return [p.name[: -len(SUFFIX)] for p in sorted(self.folder.glob(f"*{SUFFIX}")) if not p.name.startswith(".")]

    def get(self, tile_id: str) -> dict[str, np.ndarray]:
        """A kept tile's arrays, read whole."""
        with np.load(self._path(tile_id)) as z:
            return {k: z[k] for k in z.files}

    def time(self, tile_id: str) -> int:
        """A kept tile's pass time (its `t`), reading nothing else."""
        with np.load(self._path(tile_id)) as z:
            return int(z["t"])

    def items(self) -> Iterator[tuple[str, dict[str, np.ndarray]]]:
        """Every kept tile, in order of id."""
        for i in self.ids():
            yield i, self.get(i)

    def drop(self, tile_id: str) -> None:
        self._path(tile_id).unlink(missing_ok=True)

    def prune(self, older_than: float, marks: tuple[str, ...] = ()) -> None:
        """Drop the tiles, and the markers named, last written before `older_than` (UTC s)."""
        if not self.folder.exists():
            return
        suffixes = {SUFFIX, *(f".{m}" for m in marks)}
        for p in self.folder.iterdir():
            if p.suffix in suffixes and not p.name.startswith(".") and p.stat().st_mtime < older_than:
                p.unlink()

    def fingerprint(self) -> list[tuple[str, int, int]]:
        """Each kept tile's file name, size and time written: changes whenever a tile comes, goes or is rewritten."""
        return [(p.name, p.stat().st_size, int(p.stat().st_mtime)) for p in (self._path(i) for i in self.ids())]
