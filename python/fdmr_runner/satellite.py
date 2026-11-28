"""The water's surface temperature seen from orbit (#92): the ECOSTRESS
passes fdmr_adapters.ecostress keeps in its tile cache, read back and looked
up at points (overlays.satellite_temperature). A tile holds only the clear
water pixels; a point counts only where its pixel and the eight round it are
all such water, so no land or cloud is mixed into its value.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from fdmr_geo import utm
from fdmr_store.tiles import TileCache


def water_tiles(folder: Path) -> list["WaterTile"]:
    """Every kept pass in the folder's tile cache, in order of id."""
    return [WaterTile(z) for _, z in TileCache(folder).items()]


class WaterTile:
    """A kept tile, to look up: the temperature at a point where the pixel and
    its eight neighbours are all kept water (no land or cloud mixed in)."""

    def __init__(self, z: dict) -> None:
        self.t, self.zone = int(z["t"]), int(z["zone"])
        self.x0, self.y0, self.dx = float(z["x0"]), float(z["y0"]), float(z["dx"])
        self.grid = np.full(tuple(z["shape"]), np.nan, dtype=np.float32)
        self.grid[z["rows"], z["cols"]] = z["temperature"]
        self._pure: np.ndarray | None = None

    @property
    def pure_mask(self) -> np.ndarray:
        """Pixels whose 3 x 3 pixels are all kept water (never on the tile's
        edge), worked out once per tile."""
        if self._pure is None:
            ok = np.isfinite(self.grid)
            m = np.zeros_like(ok)
            rows, cols = ok.shape
            inner = np.ones((rows - 2, cols - 2), dtype=bool) if rows > 2 and cols > 2 else np.zeros((0, 0), dtype=bool)
            for dr in (0, 1, 2):
                for dc in (0, 1, 2):
                    inner &= ok[dr:dr + rows - 2, dc:dc + cols - 2]
            if inner.size:
                m[1:-1, 1:-1] = inner
            self._pure = m
        return self._pure

    def pure(self, lon, lat) -> np.ndarray:
        """Each point's temperature (degC), NaN where its 3 x 3 pixels are not
        all kept water."""
        e, n = utm(lon, lat, self.zone)
        return self.pure_utm(np.atleast_1d(e), np.atleast_1d(n))

    def pure_utm(self, e: np.ndarray, n: np.ndarray) -> np.ndarray:
        """As `pure`, for points already in this tile's UTM zone (m)."""
        c = np.floor((e - self.x0) / self.dx).astype(int)
        r = np.floor((self.y0 - n) / self.dx).astype(int)
        out = np.full(c.shape, np.nan)
        inside = (r >= 1) & (c >= 1) & (r < self.grid.shape[0] - 1) & (c < self.grid.shape[1] - 1)
        ri, ci = r[inside], c[inside]
        ok = self.pure_mask[ri, ci]
        vals = np.full(ri.shape, np.nan)
        vals[ok] = self.grid[ri[ok], ci[ok]]
        out[inside] = vals
        return out

    def near(self, lon: float, lat: float, reach: int = 2) -> float | None:
        """The temperature at the nearest pixel, within `reach` pixels of the
        point, whose 3 x 3 pixels are all kept water: for a sensor on a bank or
        a pier."""
        e, n = utm(lon, lat, self.zone)
        c0, r0 = int((float(e) - self.x0) // self.dx), int((self.y0 - float(n)) // self.dx)
        best = None
        for dr in range(-reach, reach + 1):
            for dc in range(-reach, reach + 1):
                r, c = r0 + dr, c0 + dc
                if not (1 <= r < self.grid.shape[0] - 1 and 1 <= c < self.grid.shape[1] - 1):
                    continue
                if np.isfinite(self.grid[r - 1:r + 2, c - 1:c + 2]).all() and (best is None or dr * dr + dc * dc < best[0]):
                    best = (dr * dr + dc * dc, float(self.grid[r, c]))
        return None if best is None else best[1]
