import json
from pathlib import Path

import pytest

from fdmr_adapters.usgs import CFS, FT, UsgsAdapter
from fdmr_store import Store

FIXTURES = Path(__file__).parent / "fixtures"


class RecordedClient:
    """Serves recorded API pages in order, and remembers the URLs asked for."""

    def __init__(self, name: str) -> None:
        self.pages = json.loads((FIXTURES / name).read_text())
        self.urls: list[str] = []

    def get_json(self, url: str):
        self.urls.append(url)
        return self.pages[len(self.urls) - 1]


def test_values_follow_pages_and_convert_to_si():
    store = Store()
    client = RecordedClient("usgs_continuous_keswick.json")
    n = UsgsAdapter(store, client).fetch_values("11370500", "discharge", "P1D")
    assert n == 4 and len(client.urls) == 2  # two pages, the second from the "next" link
    vals = store.values("usgs", "11370500", "discharge", 0, 2**62)
    assert [v.t for v in vals] == sorted(v.t for v in vals)
    assert vals[0].value == pytest.approx(3240 * CFS)  # 91.75 m3/s
    assert vals[0].qualifier == "Provisional"
    assert store.unit("usgs", "11370500", "discharge") == "m3/s"
    assert "parameter_code=00060" in client.urls[0] and "USGS-11370500" in client.urls[0]


def test_channel_measurements_convert_to_si_and_agree_with_q_over_a():
    store = Store()
    UsgsAdapter(store, RecordedClient("usgs_channel_keswick.json")).fetch_channel_measurements("11370500")
    ms = store.channel_measurements("usgs", "11370500")
    assert len(ms) == 2
    m = ms[-1]  # 24 Nov 2026: 3130 cfs, 201 ft wide, 2840 ft2, 1.10 ft/s
    assert m.flow == pytest.approx(3130 * CFS)
    assert m.width == pytest.approx(201 * FT)
    assert m.area == pytest.approx(2840 * FT**2)
    assert m.velocity == pytest.approx(1.10 * FT)
    assert m.flow / m.area == pytest.approx(m.velocity, rel=0.01)  # the measurement is self-consistent


def test_unknown_unit_is_refused():
    class OddUnit:
        def get_json(self, url):
            return {"features": [{"properties": {"time": "2026-11-24T04:00:00+00:00", "value": "1", "unit_of_measure": "gal/min"}}], "links": []}

    with pytest.raises(ValueError):
        UsgsAdapter(Store(), OddUnit()).fetch_values("1", "discharge", "P1D")


@pytest.mark.live
def test_live_keswick_last_day():
    store = Store()
    adapter = UsgsAdapter(store)
    assert adapter.fetch_values("11370500", "discharge", "P1D") > 50
    assert adapter.fetch_channel_measurements("11370500") > 100


def test_available_lists_only_continuous_standard_parameters():
    # Freeport records much more (dissolved oxygen, nitrate...), and daily-mean
    # series; only continuous values of the standard parameters count (turbidity
    # among them since 26 Nov 2026, for the sediment work, issue #16).
    have = UsgsAdapter(Store(), RecordedClient("usgs_series_freeport.json")).available("11447650", active_within_days=3650)
    assert have == {"discharge", "gage_height", "water_temperature", "specific_conductance", "turbidity_fnu"}


class TwoSensors:
    """A bay station: upper and lower sensors for one parameter, values interleaved."""

    def get_json(self, url: str):
        if "time-series-metadata" in url:
            return {"features": [
                {"properties": {"id": "U", "statistic_id": "00011", "sublocation_identifier": "UPPER - 30 FT FROM BED"}},
                {"properties": {"id": "L", "statistic_id": "00011", "sublocation_identifier": "LOWER - 5 FT FROM BED"}},
            ], "links": []}
        rows = [("2026-11-24T00:00:00+00:00", "41400", "U"), ("2026-11-24T00:00:00+00:00", "42300", "L"),
                ("2026-11-24T00:15:00+00:00", "41000", "U"), ("2026-11-24T00:15:00+00:00", "42000", "L")]
        return {"features": [{"properties": {"time": t, "value": v, "time_series_id": i, "unit_of_measure": "uS/cm"}} for t, v, i in rows], "links": []}


def test_a_station_with_two_sensors_is_read_one_sensor_at_a_time():
    store = Store()
    a = UsgsAdapter(store, TwoSensors())
    with pytest.raises(ValueError, match="more than one sensor"):
        a.fetch_values("375607122264701", "specific_conductance", "P1D")
    assert a.fetch_values("375607122264701", "specific_conductance", "P1D", sensor="upper") == 2
    assert a.fetch_values("375607122264701", "specific_conductance", "P1D", sensor="lower") == 2
    up = [v.value for v in store.values("usgs", "375607122264701", "specific_conductance_upper", 0, 2**62)]
    low = [v.value for v in store.values("usgs", "375607122264701", "specific_conductance_lower", 0, 2**62)]
    assert up == [41400, 41000] and low == [42300, 42000]



class MainAndProject(TwoSensors):
    """A river gauge: its own sensor (no sublocation) and a project's, both reporting."""

    def get_json(self, url: str):
        if "time-series-metadata" in url:
            return {"features": [
                {"properties": {"id": "M", "statistic_id": "00011", "sublocation_identifier": None}},
                {"properties": {"id": "P", "statistic_id": "00011", "sublocation_identifier": "BGC PROJECT"}},
            ], "links": []}
        rows = [("2026-11-24T00:00:00+00:00", "11.0", "M"), ("2026-11-24T00:00:00+00:00", "11.1", "P")]
        return {"features": [{"properties": {"time": t, "value": v, "time_series_id": i, "unit_of_measure": "degC"}} for t, v, i in rows], "links": []}


def test_a_gauge_with_a_project_sensor_keeps_its_own():
    store = Store()
    assert UsgsAdapter(store, MainAndProject()).fetch_values("11447650", "water_temperature", "P1D") == 1
    assert [v.value for v in store.values("usgs", "11447650", "water_temperature", 0, 2**62)] == [11.0]
