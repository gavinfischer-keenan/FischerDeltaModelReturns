import pytest

from fdmr_store import ChannelMeasurement, Store, Value


@pytest.fixture
def store():
    s = Store()
    yield s
    s.close()


def test_values_round_trip_in_time_order(store):
    store.put_values("usgs", "1", "discharge", "m3/s", [Value(200, 2.0), Value(100, 1.0, "Approved")])
    assert store.values("usgs", "1", "discharge", 0, 1000) == [Value(100, 1.0, "Approved"), Value(200, 2.0)]


def test_range_is_half_open(store):
    store.put_values("usgs", "1", "discharge", "m3/s", [Value(t, float(t)) for t in (100, 200, 300)])
    assert [v.t for v in store.values("usgs", "1", "discharge", 100, 300)] == [100, 200]


def test_later_revision_replaces_earlier(store):
    store.put_values("usgs", "1", "discharge", "m3/s", [Value(100, 1.0, "Provisional")])
    store.put_values("usgs", "1", "discharge", "m3/s", [Value(100, 1.5, "Approved")])
    assert store.values("usgs", "1", "discharge", 0, 1000) == [Value(100, 1.5, "Approved")]


def test_unit_cannot_change(store):
    store.put_values("usgs", "1", "discharge", "m3/s", [Value(100, 1.0)])
    with pytest.raises(ValueError):
        store.put_values("usgs", "1", "discharge", "ft3/s", [Value(200, 1.0)])


def test_missing_values_are_kept_as_none(store):
    store.put_values("usgs", "1", "gage_height", "m", [Value(100, None, "Eqp")])
    assert store.values("usgs", "1", "gage_height", 0, 1000) == [Value(100, None, "Eqp")]


def test_latest_reports_time_and_fetch_time(store):
    assert store.latest("usgs", "1", "discharge") is None
    store.put_values("usgs", "1", "discharge", "m3/s", [Value(100, 1.0), Value(400, 4.0)])
    t, fetched = store.latest("usgs", "1", "discharge")
    assert t == 400 and fetched > 1_700_000_000


def test_series_are_kept_apart(store):
    store.put_values("usgs", "1", "discharge", "m3/s", [Value(100, 1.0)])
    store.put_values("usgs", "2", "discharge", "m3/s", [Value(100, 9.0)])
    store.put_values("cdec", "1", "discharge", "m3/s", [Value(100, 5.0)])
    assert store.values("usgs", "1", "discharge", 0, 1000) == [Value(100, 1.0)]


def test_channel_measurements_round_trip(store):
    m = ChannelMeasurement(t=100, flow=88.6, area=263.8, width=61.3, velocity=0.336)
    store.put_channel_measurements("usgs", "1", [m])
    store.put_channel_measurements("usgs", "1", [m])  # re-fetching does not duplicate
    assert store.channel_measurements("usgs", "1") == [m]


def test_store_persists_to_a_file(tmp_path):
    path = tmp_path / "store.sqlite"
    s = Store(path)
    s.put_values("usgs", "1", "discharge", "m3/s", [Value(100, 1.0)])
    s.close()
    s = Store(path)
    assert s.values("usgs", "1", "discharge", 0, 1000) == [Value(100, 1.0)]
    s.close()
