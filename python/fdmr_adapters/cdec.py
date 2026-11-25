"""California Data Exchange Center (CDEC, California Department of Water
Resources). A regional adapter: California stations only (specification 6).

Fetches sensor readings from CDEC's JSON service, converts them to SI units and
UTC, screens out readings no river could produce, and writes them to the store.

- Times: CDEC labels readings in Pacific Standard Time all year, with no
  summer shift. Checked 25 Nov 2026 against the USGS gauge at Bend Bridge in
  July, when the two would differ: flows line up at UTC = CDEC + 8 h.
- Screening: a reading outside the sensor's plausible range (a dropout reads
  0 °F) or a lone spike far from both its neighbours is dropped.
"""

from __future__ import annotations

from calendar import timegm
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from time import strptime
from urllib.parse import urlencode

from fdmr_store import Store, Value

from .http import HttpJsonClient, JsonClient

BASE = "https://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet"
SOURCE = "cdec"
PST_TO_UTC = 8 * 3600
CHUNK_DAYS = 90  # one request per 90 days keeps each answer small
FT = 0.3048
CFS = FT**3


@dataclass(frozen=True)
class Sensor:
    number: int
    unit: str  # stored unit
    convert: dict  # CDEC unit -> function to the stored unit
    plausible: tuple[float, float]  # in the stored unit
    max_spike: float  # a reading this far from both neighbours, the same way, is dropped


SENSORS = {
    "water_temperature": Sensor(
        25, "degC", {"DEG F": lambda f: (f - 32) * 5 / 9, "DEG C": lambda c: c}, (0.5, 35.0), 3.0
    ),
    "specific_conductance": Sensor(100, "uS/cm", {"uS/cm": lambda v: v, "US/CM": lambda v: v}, (1.0, 60_000.0), 500.0),
    # Stage as the station reports it, on its own datum: m.
    "gage_height": Sensor(1, "m", {"FEET": lambda f: f * FT}, (-15.0, 60.0), 1.0),
    # Flow in a channel: tidal channels run both ways, so negative is allowed.
    "discharge": Sensor(20, "m3/s", {"CFS": lambda q: q * CFS}, (-30_000.0, 30_000.0), 2_000.0),
    # Pumped out of the system (exports, diversions): m3/s.
    "pumping": Sensor(70, "m3/s", {"CFS": lambda q: q * CFS}, (0.0, 1_000.0), 1_000.0),
    "reservoir_outflow": Sensor(23, "m3/s", {"CFS": lambda q: q * CFS}, (0.0, 20_000.0), 20_000.0),
    "reservoir_inflow": Sensor(76, "m3/s", {"CFS": lambda q: q * CFS}, (-1_000.0, 20_000.0), 20_000.0),
}


def to_utc(date: str) -> int:
    """A CDEC time such as "2026-7-9 23:15" (Pacific Standard Time) as seconds since 1970 UTC."""
    return timegm(strptime(date.strip(), "%Y-%m-%d %H:%M")) + PST_TO_UTC


def screen(values: Sequence[Value], low: float, high: float, max_spike: float) -> list[Value]:
    """Drop readings outside [low, high], then lone spikes: a reading more than
    `max_spike` above both neighbours, or below both."""
    ok = [v for v in values if v.value is not None and low <= v.value <= high]
    out = []
    for i, v in enumerate(ok):
        if 0 < i < len(ok) - 1:
            a, b = v.value - ok[i - 1].value, v.value - ok[i + 1].value
            if (a > max_spike and b > max_spike) or (a < -max_spike and b < -max_spike):
                continue
        out.append(v)
    return out


class CdecAdapter:
    def __init__(self, store: Store, client: JsonClient | None = None) -> None:
        self.store = store
        self.client = client or HttpJsonClient()

    def fetch_values(self, station: str, parameter: str, start: int, end: int, duration: str = "H") -> int:
        """Fetch one sensor at one station for [start, end) (s since 1970), store it,
        and return how many readings were kept."""
        sensor = SENSORS[parameter]
        raw: dict[int, Value] = {}  # by time: chunks share their boundary day
        t = start
        while t < end:
            t1 = min(end, t + CHUNK_DAYS * 86_400)
            # CDEC reads Start and End as calendar days in its own time.
            day = lambda s: datetime.fromtimestamp(s - PST_TO_UTC, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
            q = urlencode({"Stations": station, "SensorNums": sensor.number, "dur_code": duration, "Start": day(t), "End": day(t1)})
            for r in self.client.get_json(f"{BASE}?{q}") or []:
                v, unit = r.get("value"), (r.get("units") or "").strip()
                if v is None or v == -9999:
                    continue
                if unit not in sensor.convert:
                    raise ValueError(f"CDEC {station} sensor {sensor.number}: unexpected unit {unit!r}")
                ts = to_utc(r["date"])
                if start <= ts < end:
                    raw[ts] = Value(ts, float(sensor.convert[unit](v)), (r.get("dataFlag") or "").strip() or None)
            t = t1
        kept = screen([raw[k] for k in sorted(raw)], *sensor.plausible, sensor.max_spike)
        return self.store.put_values(SOURCE, station, parameter, sensor.unit, kept)
