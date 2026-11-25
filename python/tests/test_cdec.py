import json
from calendar import timegm
from pathlib import Path

import pytest

from fdmr_adapters import cdec
from fdmr_store import Store, Value

PAGES = json.loads((Path(__file__).parent / "fixtures" / "cdec_bnd_temperature.json").read_text())


class Client:
    def __init__(self, pages):
        self.pages, self.urls = list(pages), []

    def get_json(self, url):
        self.urls.append(url)
        return self.pages.pop(0) if self.pages else []


def test_times_are_pacific_standard_all_year():
    # Midnight PST is 08:00 UTC, in winter and in summer alike.
    assert cdec.to_utc("2026-1-15 0:00") == timegm((2026, 1, 15, 8, 0, 0))
    assert cdec.to_utc("2026-7-15 0:00") == timegm((2026, 7, 15, 8, 0, 0))


def test_screen_drops_out_of_range_and_lone_spikes():
    vs = [Value(i, v) for i, v in enumerate([10.0, 10.1, 0.0, 10.2, 25.0, 10.3, 10.2])]
    kept = cdec.screen(vs, 0.5, 35.0, 3.0)
    assert [v.value for v in kept] == [10.0, 10.1, 10.2, 10.3, 10.2]
    # A real step is kept: it is far from only one neighbour.
    step = [Value(i, v) for i, v in enumerate([10.0, 10.0, 15.0, 15.0])]
    assert len(cdec.screen(step, 0.5, 35.0, 3.0)) == 4


def test_fetch_converts_screens_and_stores_once():
    store = Store()
    client = Client(PAGES)
    start, end = timegm((2026, 11, 20, 8, 0, 0)), timegm((2026, 11, 21, 8, 0, 0))
    n = cdec.CdecAdapter(store, client).fetch_values("BND", "water_temperature", start, end)
    vals = store.values("cdec", "BND", "water_temperature", 0, 2**62)
    assert n == len(vals) == 6  # 8 readings less the dropout and the spike; the -9999 is skipped
    first = PAGES[0][0]
    assert vals[0].t == start and vals[0].value == pytest.approx((first["value"] - 32) * 5 / 9)
    assert store.unit("cdec", "BND", "water_temperature") == "degC"
    assert "Stations=BND" in client.urls[0] and "SensorNums=25" in client.urls[0]


def test_unknown_unit_is_refused():
    page = [[{"date": "2026-11-20 0:00", "value": 50.0, "units": "KELVIN", "dataFlag": " "}]]
    with pytest.raises(ValueError):
        cdec.CdecAdapter(Store(), Client(page)).fetch_values("BND", "water_temperature", 0, 2**31)
