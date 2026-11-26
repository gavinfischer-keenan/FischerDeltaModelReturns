from calendar import timegm

import pytest

from fdmr_adapters import cpc, erddap
from fdmr_store import Store

WEEKLY = """ Weekly SST data starts week centered on 2Sept1981

                Nino1+2      Nino3        Nino34        Nino4
 Week          SST SSTA     SST SSTA     SST SSTA     SST SSTA
 11NOV2026     27.0 5.1     29.5 4.4     29.9 3.1     29.9 1.3
 18NOV2026     27.5 5.7     29.8 4.7     30.1 3.3     30.0 1.4
"""

ONI = """ SEAS  YR   TOTAL   ANOM
  DJF 1950  24.72  -1.53
  ASO 2026  29.23   2.46
"""


def test_weekly_nino_anomalies_by_region():
    got = cpc.parse_weekly(WEEKLY)
    assert [v.value for v in got["nino34"]] == [3.1, 3.3] and got["nino12"][-1].value == 5.7
    assert got["nino34"][-1].t == timegm((2026, 11, 18, 0, 0, 0))


def test_oni_season_at_its_middle_month():
    got = cpc.parse_oni(ONI)
    assert got[-1].value == 2.46 and got[-1].t == timegm((2026, 9, 15, 0, 0, 0))
    assert got[0].t == timegm((1950, 1, 15, 0, 0, 0))


def test_grid_mean_over_a_box_per_time():
    csv = ("time,latitude,longitude,sla\nUTC,degrees_north,degrees_east,m\n"
           "2026-11-24T00:00:00Z,37.375,-123.625,0.10\n2026-11-24T00:00:00Z,37.375,-123.375,0.20\n"
           "2026-11-24T00:00:00Z,37.625,-123.625,NaN\n2026-11-25T00:00:00Z,37.375,-123.625,0.30\n")
    store = Store()
    a = erddap.ErddapAdapter(store, "https://example.org/erddap", get_text=lambda url: csv)
    assert a.fetch_grid_mean("ds", "sla", (-123.75, 37.25, -122.75, 38.25), "2026-11-20T00:00:00Z", "box", "sea_level_anomaly", "m") == 2
    vs = store.values("erddap:example.org", "box", "sea_level_anomaly", 0, 2_000_000_000)
    assert [round(v.value, 3) for v in vs] == [0.15, 0.3]


def test_tide_residual_slow_part():
    from fdmr_adapters import noaa_tides
    from fdmr_runner import overlays
    from fdmr_store import Value

    hour = 3600
    now = 2000 * hour
    store = Store()
    t0 = now - 30 * 24 * hour
    store.put_values("noaa-tides", "S", noaa_tides.PARAMETER, "m", [Value(t0 + k * 900, 1.0, None) for k in range(4 * 30 * 24 + 8)])
    store.put_values("noaa-tides", "S", "water_level", "m", [Value(t0 + k * hour, 1.3 if t0 + k * hour < now - 2 * hour else 1.6, None) for k in range(30 * 24)])
    (r,) = overlays.tide_residual({"stations": [{"source": "noaa-tides", "site": "S", "name": "S", "products": ["water_level"]}]}, store, now)
    assert r["mean_30d_m"] == pytest.approx(0.3, abs=0.01) and r["latest_less_30d_m"] == pytest.approx(0.3, abs=0.01)
