"""Where each variable lies in an FVCOM NetCDF file, to fetch only those bytes.

NOAA's field files for one forecast system are written the same way every
hour: same size, every variable at the same byte offset, uncompressed (checked
25 Nov 2026 for San Francisco Bay: 56,605,561 bytes each, the variables needed
in one 25.7 MB span). So the layout is read once from a whole file and reused:
each later file is fetched as one byte range and its variables cut out of it.
A file of another size is not trusted to share the layout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HOURLY = ("time", "zeta", "u", "v", "temp", "salinity")


@dataclass(frozen=True)
class Var:
    shape: tuple[int, ...]
    dtype: str
    chunk: tuple[int, ...] | None  # None: one contiguous block
    pieces: tuple[tuple[tuple[int, ...], int, int], ...]  # (chunk offset, byte offset, size)


@dataclass(frozen=True)
class Layout:
    file_size: int
    vars: dict[str, Var]

    def span(self, names=HOURLY) -> tuple[int, int]:
        """The byte range [start, end) holding every named variable."""
        lo = min(p[1] for n in names for p in self.vars[n].pieces)
        hi = max(p[1] + p[2] for n in names for p in self.vars[n].pieces)
        return lo, hi

    def decode(self, name: str, block: bytes, block_start: int) -> np.ndarray:
        """A variable's values, cut from a block of the file starting at block_start."""
        v = self.vars[name]
        out = np.empty(v.shape, dtype=v.dtype)
        for off, at, size in v.pieces:
            raw = np.frombuffer(block, dtype=v.dtype, count=size // np.dtype(v.dtype).itemsize, offset=at - block_start)
            if v.chunk is None:
                return raw.reshape(v.shape).copy()
            piece = raw.reshape(v.chunk)
            sl = tuple(slice(o, min(o + c, s)) for o, c, s in zip(off, v.chunk, v.shape))
            out[sl] = piece[tuple(slice(0, s.stop - s.start) for s in sl)]
        return out

    def to_json(self) -> str:
        return json.dumps({"file_size": self.file_size, "vars": {
            k: {"shape": v.shape, "dtype": v.dtype, "chunk": v.chunk, "pieces": v.pieces} for k, v in self.vars.items()}})

    @staticmethod
    def from_json(text: str) -> Layout:
        d = json.loads(text)
        return Layout(d["file_size"], {
            k: Var(tuple(v["shape"]), v["dtype"], tuple(v["chunk"]) if v["chunk"] else None,
                   tuple((tuple(p[0]), p[1], p[2]) for p in v["pieces"])) for k, v in d["vars"].items()})


def read_layout(path: Path, names=HOURLY) -> Layout:
    """The layout of the named variables in a whole local file."""
    import h5py

    out = {}
    with h5py.File(path, "r") as f:
        for n in names:
            d = f[n]
            if d.compression:
                raise ValueError(f"{n} is compressed: byte ranges cannot be cut from it")
            if d.chunks:
                infos = [d.id.get_chunk_info(i) for i in range(d.id.get_num_chunks())]
                pieces = tuple((tuple(int(x) for x in i.chunk_offset), int(i.byte_offset), int(i.size)) for i in infos)
            else:
                pieces = ((tuple(0 for _ in d.shape), int(d.id.get_offset()), int(d.id.get_storage_size())),)
            out[n] = Var(tuple(int(s) for s in d.shape), d.dtype.str, tuple(d.chunks) if d.chunks else None, pieces)
    return Layout(path.stat().st_size, out)
