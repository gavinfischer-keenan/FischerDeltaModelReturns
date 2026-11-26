import pytest

from fdmr_adapters import ndbc, noaa_tides
from fdmr_store import Store, Value

HOUR = 3600


class Client:
    def __init__(self, page):
        self.page, self.urls = page, []

    def get_json(self, url):
        self.urls.append(url)
        return self.page


def test_wind_gives_speed_direction_and_gust_and_skips_blanks():
    page = {"data": [{"t": "2026-11-26 00:00", "s": "5.1", "d": "270.00", "g": "7.2"},
                     {"t": "2026-11-26 00:06", "s": "", "d": "", "g": ""}]}
    store = Store()
    c = Client(page)
    n = noaa_tides.NoaaTidesAdapter(store, c).fetch_observed("9414290", "wind", 1795651200, 1795651200)
    assert n == 3
    t = 1795651200
    assert [store.values("noaa-tides", "9414290", p, t, t + 1)[0].value for p in ("wind_speed", "wind_direction", "wind_gust")] == [5.1, 270.0, 7.2]
    assert "product=wind" in c.urls[0] and "datum" not in c.urls[0]


def test_water_level_is_asked_for_above_mllw():
    c = Client({"data": [{"t": "2026-11-26 00:00", "v": "1.234"}]})
    noaa_tides.NoaaTidesAdapter(Store(), c).fetch_observed("9414290", "water_level", 1795651200, 1795651200)
    assert "datum=MLLW" in c.urls[0]


def test_an_error_page_is_an_error():
    with pytest.raises(ValueError):
        noaa_tides.NoaaTidesAdapter(Store(), Client({"error": {"message": "No data was found"}})).fetch_observed("1", "wind", 0, 0)


def test_tide_residual_is_measured_less_predicted_hourly():
    from fdmr_runner import overlays

    store = Store()
    now = 1000 * HOUR
    t0 = now - 30 * HOUR
    store.put_values("noaa-tides", "S", noaa_tides.PARAMETER, "m", [Value(t0 + k * 900, 1.0, None) for k in range(4 * 31)])
    # Measured 0.2 m above the prediction, then a 0.5 m surge in the last hour.
    obs = [Value(t0 + k * 360, 1.2 if t0 + k * 360 < now - HOUR else 1.5, None) for k in range(10 * 30)]
    store.put_values("noaa-tides", "S", "water_level", "m", obs)
    cfg = {"stations": [{"source": "noaa-tides", "site": "S", "name": "S", "products": ["water_level"]},
                        {"source": "noaa-tides", "site": "T", "name": "T"}]}
    (r,) = overlays.tide_residual(cfg, store, now)
    assert r["latest_m"] == pytest.approx(0.5) and r["highest_m"] == pytest.approx(0.5) and r["lowest_m"] == pytest.approx(0.2)
    assert r["hours"] == 30 and 0.2 < r["mean_m"] < 0.25


def test_buoy_standard_file_gives_wind_direction_and_gust():
    lines = ["#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE",
             "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft",
             "2026 11 26 08 30 310  8.0 10.0   1.2    14   7.9 270 1018.2  13.1  14.2  10.0   MM   MM    MM"]
    got = ndbc.parse(lines, ndbc.MET_COLUMNS)
    assert got["wind_direction"][0].value == 310 and got["wind_gust"][0].value == 10.0 and got["air_pressure"][0].value == 1018.2
