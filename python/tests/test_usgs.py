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
