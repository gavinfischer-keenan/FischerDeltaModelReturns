from calendar import timegm

import pytest

from fdmr_adapters.noaa_tides import NoaaTidesAdapter
from fdmr_store import Store


class Client:
    def __init__(self, pages):
        self.pages, self.urls = list(pages), []

    def get_json(self, url):
        self.urls.append(url)
        return self.pages.pop(0)


def test_predictions_are_stored_in_metres_and_utc():
    page = {"predictions": [{"t": "2026-11-25 00:00", "v": "0.727"}, {"t": "2026-11-25 00:15", "v": "0.625"}]}
    store = Store()
    client = Client([page])
    t0 = timegm((2026, 11, 25, 0, 0, 0))
    assert NoaaTidesAdapter(store, client).fetch_predictions("9415102", t0, t0 + 86_400) == 2
    v = store.values("noaa-tides", "9415102", "tide_prediction", 0, 2**62)
    assert v[0].t == t0 and v[0].value == pytest.approx(0.727) and v[1].t == t0 + 900
    assert "station=9415102" in client.urls[0] and "time_zone=gmt" in client.urls[0] and "interval=15" in client.urls[0]


def test_long_spans_come_a_month_at_a_time():
    store = Store()
    client = Client([{"predictions": []}, {"predictions": []}])
    t0 = timegm((2026, 11, 1, 0, 0, 0))
    NoaaTidesAdapter(store, client).fetch_predictions("9415102", t0, t0 + 45 * 86_400)
    assert len(client.urls) == 2 and "begin_date=20261201" in client.urls[1]


def test_an_error_answer_is_raised():
    with pytest.raises(ValueError):
        NoaaTidesAdapter(Store(), Client([{"error": {"message": "No data was found"}}])).fetch_predictions("1", 0, 10)
