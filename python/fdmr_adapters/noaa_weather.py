"""NOAA weather: airport observations and the National Weather Service forecast.
A national adapter (United States).

Three feeds, each stored in SI units under its own source:

- `ghcnh`: NOAA's Global Historical Climatology Network hourly archive, one
  file per station and year. It runs about four days behind (checked
  25 Nov 2026: the 2026 files ended on 21 Nov), so it covers the past.
  NOAA's older hourly archive (ISD, "global-hourly") stops in 2025.
- `nws`: the Weather Service's own station observations, about the last week,
  which cover the archive's last few days.
- `nws-forecast`: the Weather Service's gridded forecast for one 2.5 km grid
  cell, about 7 days ahead, each value tagged with the forecast's issue time.

Parameters: air_temperature and dew_point (degC), wind_speed (m/s, at 10 m),
cloud_cover (fraction of the sky, 0 to 1) and air_pressure (hPa; the forecast
has none). Airport sky reports reach about 3.7 km (12,000 ft) up, so higher
cloud is missed in the observations.

References: ref:nws_api, ref:ghcnh (references.toml).
"""

from __future__ import annotations

import csv
import io
from calendar import timegm
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from time import strptime
from urllib.parse import urlencode

from fdmr_store import Store, Value

from . import http
from .http import HttpJsonClient, JsonClient

GHCNH = "ghcnh"
NWS = "nws"
NWS_FORECAST = "nws-forecast"
GHCNH_URL = "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/access/by-year/{year}/psv/GHCNh_{station}_{year}.psv"
NWS_API = "https://api.weather.gov"

UNITS = {"air_temperature": "degC", "dew_point": "degC", "wind_speed": "m/s", "cloud_cover": "1", "air_pressure": "hPa"}
PLAUSIBLE = {
    "air_temperature": (-40.0, 55.0), "dew_point": (-50.0, 40.0), "wind_speed": (0.0, 60.0),
    "cloud_cover": (0.0, 1.0), "air_pressure": (850.0, 1090.0),
}
# Sky cover in eighths of the sky (oktas), as the Weather Service's layer amounts.
AMOUNT = {"SKC": 0.0, "CLR": 0.0, "FEW": 1.5 / 8, "SCT": 3.5 / 8, "BKN": 6 / 8, "OVC": 1.0, "VV": 1.0}


def _ok(parameter: str, v: float | None) -> bool:
    lo, hi = PLAUSIBLE[parameter]
    return v is not None and lo <= v <= hi


def _num(s: str | None) -> float | None:
    try:
        return float(s) if s not in (None, "") else None
    except ValueError:
        return None


def ghcnh_cloud(codes: Iterable[str]) -> float | None:
    """The sky covered, from GHCNh's sky cover codes such as "BKN:06" (oktas;
    09 is a sky hidden by fog): the most-covered layer, or None if none given."""
    best = None
    for c in codes:
        c = c.strip().rstrip(";")
        if ":" not in c:
            continue
        okta = _num(c.split(":", 1)[1].split()[0])
        if okta is None or okta > 9:
            continue
        f = 1.0 if okta == 9 else okta / 8
        best = f if best is None else max(best, f)
    return best


def parse_ghcnh(lines: Iterable[str], since: int = 0) -> dict[str, dict[int, float]]:
    """Every parameter's readings at or after `since`, from a GHCNh PSV file."""
    out: dict[str, dict[int, float]] = {p: {} for p in UNITS}
    rows = csv.reader(lines, delimiter="|")
    head = next(rows)
    col = {h: i for i, h in enumerate(head)}
    sky = [col[h] for h in head if h.startswith("sky_cover_summation_") and h[-1].isdigit()]
    sky += [col[h] for h in head if h.startswith("sky_cover_layer_") and h[-1].isdigit()]

    def get(r: list[str], name: str) -> float | None:
        i = col.get(name)
        return _num(r[i]) if i is not None and i < len(r) else None

    for r in rows:
        if len(r) < len(head) // 2:
            continue
        t = timegm(strptime(r[col["DATE"]], "%Y-%m-%dT%H:%M:%S"))
        if t < since:
            continue
        p = get(r, "station_level_pressure") or get(r, "altimeter") or get(r, "sea_level_pressure")
        vals = {
            "air_temperature": get(r, "temperature"),
            "dew_point": get(r, "dew_point_temperature"),
            "wind_speed": get(r, "wind_speed"),
            "air_pressure": p,
            "cloud_cover": ghcnh_cloud(r[i] for i in sky if i < len(r) and r[i]),
        }
        for k, v in vals.items():
            if _ok(k, v):
                out[k][t] = float(v)  # type: ignore[arg-type]
    return out


def nws_cloud(layers: list[dict]) -> float | None:
    """The sky covered, from a Weather Service observation's cloud layers."""
    fs = [AMOUNT[x["amount"]] for x in layers or [] if x.get("amount") in AMOUNT]
    return max(fs) if fs else None


def parse_nws_observations(features: list[dict]) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {p: {} for p in UNITS}
    for f in features:
        p = f["properties"]
        t = int(datetime.fromisoformat(p["timestamp"]).timestamp())
        val = lambda k: (p.get(k) or {}).get("value")  # noqa: E731
        wind = val("windSpeed")
        pres = val("barometricPressure")
        vals = {
            "air_temperature": val("temperature"),
            "dew_point": val("dewpoint"),
            "wind_speed": None if wind is None else wind / 3.6,
            "air_pressure": None if pres is None else pres / 100,
            "cloud_cover": nws_cloud(p.get("cloudLayers") or []),
        }
        for k, v in vals.items():
            if _ok(k, v):
                out[k][t] = float(v)
    return out


def _duration_h(iso: str) -> int:
    """Hours in an ISO 8601 duration such as "PT2H" or "P1DT6H"."""
    days, _, rest = iso[1:].partition("T") if "T" in iso else (iso[1:], "", "")
    h = int(days[:-1]) * 24 if days.endswith("D") else 0
    num = ""
    for ch in rest:
        if ch.isdigit():
            num += ch
        elif ch == "H":
            h += int(num)
            num = ""
        else:
            num = ""
    return max(h, 1)


def expand_hourly(values: list[dict]) -> Iterator[tuple[int, float]]:
    """A gridded forecast's (start/duration, value) entries, as hourly values."""
    for e in values:
        if e.get("value") is None:
            continue
        start, dur = e["validTime"].split("/")
        t0 = int(datetime.fromisoformat(start).timestamp())
        for k in range(_duration_h(dur)):
            yield t0 + k * 3600, float(e["value"])


def parse_nws_forecast(props: dict) -> tuple[str, dict[str, dict[int, float]]]:
    """(issue time, hourly values) from a gridpoint's raw forecast."""
    conv = {
        "temperature": ("air_temperature", lambda v: v),
        "dewpoint": ("dew_point", lambda v: v),
        "windSpeed": ("wind_speed", lambda v: v / 3.6),  # km/h
        "skyCover": ("cloud_cover", lambda v: v / 100),  # percent
    }
    out: dict[str, dict[int, float]] = {}
    for key, (param, f) in conv.items():
        unit = (props.get(key) or {}).get("uom", "")
        if key == "windSpeed" and not unit.endswith("km_h-1"):
            raise ValueError(f"unexpected wind unit {unit}")
        out[param] = {t: f(v) for t, v in expand_hourly((props.get(key) or {}).get("values", [])) if _ok(param, f(v))}
    return props.get("updateTime", ""), out


def _put(store: Store, source: str, site: str, data: dict[str, dict[int, float]], qualifier: str | None = None) -> int:
    return sum(
        store.put_values(source, site, p, UNITS[p], [Value(t, v, qualifier) for t, v in sorted(vs.items())])
        for p, vs in data.items() if vs
    )


def _get_text(url: str) -> Iterator[str]:
    yield from io.StringIO(http.get_text(url, timeout=300))


class NoaaWeatherAdapter:
    def __init__(self, store: Store, client: JsonClient | None = None, get_text=_get_text) -> None:
        self.store = store
        self.client = client or HttpJsonClient(headers={"Accept": "application/geo+json"})
        self.get_text = get_text

    def fetch_archive(self, station: str, since: int, until: int) -> int:
        """Store a station's GHCNh readings from `since`: one file per year."""
        n = 0
        for year in range(datetime.fromtimestamp(since, timezone.utc).year, datetime.fromtimestamp(until, timezone.utc).year + 1):
            data = parse_ghcnh(self.get_text(GHCNH_URL.format(station=station, year=year)), since)
            n += _put(self.store, GHCNH, station, data)
        return n

    def fetch_observations(self, station: str, since: int) -> int:
        """Store a Weather Service station's observations from `since` (it keeps about a week)."""
        start = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        page = self.client.get_json(f"{NWS_API}/stations/{station}/observations?{urlencode({'start': start})}")
        return _put(self.store, NWS, station, parse_nws_observations(page.get("features", [])))

    def fetch_forecast(self, office: str, x: int, y: int) -> tuple[str, int]:
        """Store the latest gridded forecast for one grid cell. Returns (issue, values stored)."""
        page = self.client.get_json(f"{NWS_API}/gridpoints/{office}/{x},{y}")
        issue, data = parse_nws_forecast(page["properties"])
        return issue, _put(self.store, NWS_FORECAST, f"{office}/{x},{y}", data, issue)
