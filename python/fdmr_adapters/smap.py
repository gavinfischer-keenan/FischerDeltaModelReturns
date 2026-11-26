"""NASA SMAP Level 4 soil moisture (collection SPL4SMGP, version 008, NASA
NSIDC DAAC): surface and root-zone soil moisture on the 9 km EASE-Grid 2.0,
every three hours, about two days behind, from SMAP's radiometer assimilated
into NASA's land model, so it has no gaps. A global adapter. Only the cells
over the areas asked for are read, through NASA's OPeNDAP service (Earthdata
login token, ~/.config/fdmr/earthdata-token).

Stored under source `smap`, site the area's name, at each three-hour
period's middle, as the mean over the area's land cells:

- `soil_moisture_surface`, `soil_moisture_rootzone`: water by volume in the
  top 5 cm and the root zone (top metre), m3/m3;
- `soil_wetness_rootzone`: the root zone's water as a fraction of what that
  soil can hold (0 to 1);
- `soil_moisture_rootzone_percentile`: the root zone's water against that
  cell's own climate, percent (SMAP's `sm_rootzone_pctl`): 90 is wetter than
  nine days in ten for the time of year there.
"""

from __future__ import annotations

import io
import time
import urllib.error
import urllib.parse
import urllib.request
from calendar import timegm
from pathlib import Path
from urllib.parse import urlencode

import numpy as np

from .areas import members as _members
from .http import USER_AGENT, HttpJsonClient, JsonClient

SOURCE = "smap"
CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
SHORT_NAME, VERSION = "SPL4SMGP", "008"
TOKEN_FILE = Path.home() / ".config" / "fdmr" / "earthdata-token"
FIELDS = {  # SMAP's name: (ours, unit)
    "sm_surface": ("soil_moisture_surface", "m3/m3"),
    "sm_rootzone": ("soil_moisture_rootzone", "m3/m3"),
    "sm_rootzone_wetness": ("soil_wetness_rootzone", "fraction"),
    "sm_rootzone_pctl": ("soil_moisture_rootzone_percentile", "%"),
}
ROWS, COLS = 1624, 3856  # the global 9 km grid
PER_RUN = 48  # three-hour periods read per fetch at most (six days)
FILL = -9000.0  # SMAP marks no value with -9999


def search(start: int, end: int, client: JsonClient | None = None) -> list[dict]:
    """Periods between two times: each one's middle `t` and its OPeNDAP `url`."""
    client = client or HttpJsonClient()
    stamp = lambda x: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(x))  # noqa: E731
    q = urlencode({"short_name": SHORT_NAME, "version": VERSION, "temporal": f"{stamp(start)},{stamp(end)}",
                   "page_size": 500, "sort_key": "start_date"})
    out = []
    for e in client.get_json(f"{CMR}?{q}")["feed"]["entry"]:
        url = next((lk["href"] for lk in e.get("links", []) if "opendap" in lk["href"]), None)
        if url:
            t0 = timegm(time.strptime(e["time_start"][:19], "%Y-%m-%dT%H:%M:%S"))
            t1 = timegm(time.strptime(e["time_end"][:19], "%Y-%m-%dT%H:%M:%S"))
            out.append({"t": (t0 + t1) // 2, "url": url})
    return out


def _get(url: str, token: str, sleep=time.sleep) -> bytes:
    for wait in (5, 20, None):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        req.add_unredirected_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or wait is None:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if wait is None:
                raise
        sleep(wait)
    raise AssertionError("unreachable")


def read_nc4(data: bytes) -> dict[str, np.ndarray]:
    """The variables of an OPeNDAP NetCDF-4 answer, by their last name; SMAP's
    no-value marks as NaN."""
    import h5py

    out: dict[str, np.ndarray] = {}
    with h5py.File(io.BytesIO(data), "r") as f:
        def take(name, obj):
            if isinstance(obj, h5py.Dataset):
                a = np.array(obj, dtype=float)
                a[a < FILL] = np.nan
                out[name.rsplit("/", 1)[-1]] = a
        f.visititems(take)
    return out


def members(lat: np.ndarray, lon: np.ndarray, area: dict) -> np.ndarray:
    """The cells (rows by lat, columns by lon) inside an area (fdmr_adapters.areas)."""
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    return _members(la, lo, area)


class SmapAdapter:
    def __init__(self, store, token: str | None = None, get=_get, client: JsonClient | None = None) -> None:
        self.store = store
        self.token = token if token is not None else TOKEN_FILE.read_text(encoding="utf-8").strip()
        self.get = get
        self.client = client
        self._axes: tuple[np.ndarray, np.ndarray] | None = None

    def dap(self, url: str, ce: str) -> dict[str, np.ndarray]:
        return read_nc4(self.get(f"{url}.dap.nc4?dap4.ce={urllib.parse.quote(ce, safe='')}", self.token))

    def axes(self, url: str) -> tuple[np.ndarray, np.ndarray]:
        """The grid's latitude by row and longitude by column (the same for
        every period: on this grid a row is one latitude, a column one longitude)."""
        if self._axes is None:
            lat = self.dap(url, f"/cell_lat[0:1:{ROWS - 1}][{COLS // 2}:1:{COLS // 2}]")["cell_lat"].ravel()
            lon = self.dap(url, f"/cell_lon[{ROWS // 2}:1:{ROWS // 2}][0:1:{COLS - 1}]")["cell_lon"].ravel()
            self._axes = (lat, lon)
        return self._axes

    def fetch(self, areas: list[dict], start: int, end: int, per_run: int = PER_RUN) -> int:
        """Each area's means for the periods since `start` not yet stored,
        oldest first, at most `per_run`; returns how many values were stored."""
        from fdmr_store import Value

        done = max((self.store.latest(SOURCE, a["site"], "soil_moisture_rootzone") or (0, 0))[0] for a in areas)
        todo = [g for g in search(start, end, self.client) if g["t"] > done][:per_run]
        if not todo:
            return 0
        lat, lon = self.axes(todo[0]["url"])
        pts = [p for a in areas for p in (a.get("points") or [(a["box"][0], a["box"][1]), (a["box"][2], a["box"][3])])]
        margin = 0.15
        rows = np.nonzero((lat >= min(p[1] for p in pts) - margin) & (lat <= max(p[1] for p in pts) + margin))[0]
        cols = np.nonzero((lon >= min(p[0] for p in pts) - margin) & (lon <= max(p[0] for p in pts) + margin))[0]
        r0, r1, c0, c1 = int(rows.min()), int(rows.max()), int(cols.min()), int(cols.max())
        masks = {a["site"]: members(lat[r0:r1 + 1], lon[c0:c1 + 1], a) for a in areas}
        ce = ";".join(f"/Geophysical_Data/{v}[{r0}:1:{r1}][{c0}:1:{c1}]" for v in FIELDS)
        n = 0
        for g in todo:
            try:
                got = self.dap(g["url"], ce)
            except Exception as e:  # one period's failure does not stop the rest; it is tried again next time
                print(f"smap {g['t']} FAILED {e}", flush=True)
                break  # later periods wait, so none is skipped over
            for site, m in masks.items():
                for v, (p, unit) in FIELDS.items():
                    vals = got[v][m]
                    vals = vals[np.isfinite(vals)]
                    if vals.size:
                        n += self.store.put_values(SOURCE, site, p, unit, [Value(g["t"], float(vals.mean()), None)])
        return n
