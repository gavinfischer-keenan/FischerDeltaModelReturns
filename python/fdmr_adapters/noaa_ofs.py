"""NOAA Operational Forecast Systems (OFS) on AWS: hourly field files of its
coastal and bay models. A national adapter.

Files live in NOAA's open bucket, one folder per day:

    <ofs>/netcdf/YYYY/MM/DD/<ofs>.tHHz.YYYYMMDD.fields.<n|f>NNN.nc

Each cycle (HH) has nowcasts n000-n006, the six hours up to the cycle, and
forecasts f000-f048, the 48 hours after it (San Francisco Bay, checked
25 Nov 2026: cycles at 03, 09, 15 and 21 UTC; the archive reaches back to
September 2024). Every hour of the past is a nowcast of the first cycle at or
after it; hours after the latest cycle come from its forecast.

Files are fetched as byte ranges (only the variables wanted), so the bytes
never pass through the store: this adapter hands them to the FVCOM plug-in.

References: ref:noaa_ofs (references.toml).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .http import USER_AGENT

BASE = "https://noaa-nos-ofs-pds.s3.amazonaws.com"
HOUR = 3600


def file_url(ofs: str, cycle: int, kind: str, n: int) -> str:
    """The URL of one field file: cycle time (s since 1970 UTC), kind "n" or "f", hour n."""
    d = datetime.fromtimestamp(cycle, timezone.utc)
    return f"{BASE}/{ofs}/netcdf/{d:%Y/%m/%d}/{ofs}.t{d:%H}z.{d:%Y%m%d}.fields.{kind}{n:03d}.nc"


def source_of(t: int, cycles: list[int], latest: int) -> tuple[int, str, int]:
    """Which file holds hour t: (cycle, kind, n). The past from the first cycle at
    or after t (a nowcast); after the latest cycle, its forecast."""
    if t > latest:
        return latest, "f", (t - latest) // HOUR
    day = t // 86_400 * 86_400
    for c in sorted(day + h * HOUR for h in cycles) + [day + 86_400 + h * HOUR for h in sorted(cycles)]:
        if c >= t:
            return c, "n", 6 - (c - t) // HOUR
    raise ValueError("no cycle")


def _request(url: str, method: str = "GET", byte_range: tuple[int, int] | None = None, retries: int = 4):
    headers = {"User-Agent": USER_AGENT}
    if byte_range:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1] - 1}"
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=300)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(5 * 2**attempt)
    raise RuntimeError(f"giving up on {url}: {last}")


def size(url: str) -> int | None:
    """A file's size in bytes, or None if it is not there (yet)."""
    try:
        with _request(url, "HEAD") as r:
            return int(r.headers["Content-Length"])
    except urllib.error.HTTPError:
        return None


def fetch_range(url: str, start: int, end: int) -> bytes:
    """Bytes [start, end) of a file."""
    with _request(url, byte_range=(start, end)) as r:
        if r.status != 206:
            raise ValueError(f"{url}: no byte range served (status {r.status})")
        data = r.read()
    if len(data) != end - start:
        raise ValueError(f"{url}: {len(data)} bytes, expected {end - start}")
    return data


def download(url: str, path: Path) -> None:
    tmp = path.with_suffix(".part")
    with _request(url) as r, open(tmp, "wb") as out:
        while chunk := r.read(1 << 20):
            out.write(chunk)
    tmp.replace(path)


def latest_cycle(ofs: str, cycles: list[int], now: int, last_hour: int = 48) -> int | None:
    """The latest cycle whose whole forecast is out (its last file listed)."""
    day = now // 86_400 * 86_400
    candidates = sorted((d + h * HOUR for d in (day - 86_400, day) for h in cycles), reverse=True)
    for c in candidates:
        if c <= now and size(file_url(ofs, c, "f", last_hour)):
            return c
    return None
