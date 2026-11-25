from calendar import timegm

import pytest

from fdmr_adapters import noaa_weather as nw
from fdmr_store import Store

HEAD = "STATION|DATE|temperature|dew_point_temperature|station_level_pressure|altimeter|wind_speed|sky_cover_summation_1|sky_cover_summation_2"


def test_archive_rows_give_si_values_and_the_most_covered_layer():
    lines = [
        HEAD,
        "USW1|2026-11-21T15:52:00|3.0|2.5||1021.0|1.5|SCT:04;|BKN:07;",
        "USW1|2026-11-21T16:52:00|4.0|||1020.5|0.0|VV:09;|",
        "USW1|2026-11-21T17:52:00|99.0||||||",  # no temperature that hot: dropped
    ]
    d = nw.parse_ghcnh(lines)
    t = timegm((2026, 11, 21, 15, 52, 0))
    assert d["air_temperature"] == {t: 3.0, t + 3600: 4.0}
    assert d["air_pressure"][t] == 1021.0  # the altimeter, where the station's own pressure is missing
    assert d["cloud_cover"][t] == pytest.approx(7 / 8) and d["cloud_cover"][t + 3600] == 1.0  # fog hides the sky
    assert t + 3600 not in d["dew_point"]
    assert nw.parse_ghcnh(lines, since=t + 1)["air_temperature"] == {t + 3600: 4.0}


def test_observations_convert_wind_and_pressure():
    f = {"properties": {
        "timestamp": "2026-11-25T16:55:00+00:00",
        "temperature": {"value": 11}, "dewpoint": {"value": 9}, "windSpeed": {"value": 12.96},
        "barometricPressure": {"value": 102302.8}, "cloudLayers": [{"amount": "SCT"}, {"amount": "BKN"}],
    }}
    d = nw.parse_nws_observations([f])
    t = timegm((2026, 11, 25, 16, 55, 0))
    assert d["wind_speed"][t] == pytest.approx(3.6) and d["air_pressure"][t] == pytest.approx(1023.028)
    assert d["cloud_cover"][t] == pytest.approx(0.75)


def test_gridded_forecast_spreads_each_value_over_its_hours():
    props = {
        "updateTime": "2026-11-25T10:07:49+00:00",
        "temperature": {"uom": "wmoUnit:degC", "values": [{"validTime": "2026-11-25T17:00:00+00:00/PT2H", "value": 12.0}]},
        "dewpoint": {"values": []},
        "windSpeed": {"uom": "wmoUnit:km_h-1", "values": [{"validTime": "2026-11-25T17:00:00+00:00/P1DT1H", "value": 36.0}]},
        "skyCover": {"values": [{"validTime": "2026-11-25T17:00:00+00:00/PT1H", "value": 40}]},
    }
    issue, d = nw.parse_nws_forecast(props)
    t = timegm((2026, 11, 25, 17, 0, 0))
    assert issue.startswith("2026-11-25T10")
    assert d["air_temperature"] == {t: 12.0, t + 3600: 12.0}
    assert len(d["wind_speed"]) == 25 and d["wind_speed"][t] == pytest.approx(10.0)
    assert d["cloud_cover"] == {t: 0.4}


def test_archive_fetches_a_file_per_year():
    store = Store()
    urls = []

    def get_text(url):
        urls.append(url)
        return [HEAD, "USW1|2026-01-01T00:52:00|5.0|1.0||1020.0|2.0|CLR:00;|"]

    a = nw.NoaaWeatherAdapter(store, client=None, get_text=get_text)
    n = a.fetch_archive("USW1", timegm((2025, 12, 20, 0, 0, 0)), timegm((2026, 1, 2, 0, 0, 0)))
    assert len(urls) == 2 and "2025" in urls[0] and "GHCNh_USW1_2026" in urls[1]
    assert n == 10  # 5 parameters, the same row in both years' answers
    assert store.values(nw.GHCNH, "USW1", "cloud_cover", 0, 2**62)[0].value == 0.0
