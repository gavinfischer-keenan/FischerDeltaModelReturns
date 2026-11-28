from fdmr_runner import overlays
from fdmr_store import Store, Value

DAY = 86_400


def test_daily_series_are_asked_for_apart():
    cfg = {"default_source": "cdec", "structures": [{"site": "DNP", "duration": "H", "show": ["reservoir_storage", "reservoir_inflow"], "daily": ["reservoir_inflow"]}]}
    assert overlays.fetch_groups(cfg) == [("D", ["DNP"], ["reservoir_inflow"]), ("H", ["DNP"], ["reservoir_storage"])]


def test_a_dam_shows_how_full_it_is_and_its_basins_snow():
    store = Store()
    now = 100 * DAY
    store.put_values("cdec", "ORO", "reservoir_storage", "m3", [Value(now - 3600, 400.0, None)])
    store.put_values("cdec", "ORO", "reservoir_capacity", "m3", [Value(now - DAY, 1000.0, None)])
    store.put_values("cdec", "ORO", "reservoir_storage_average", "m3", [Value(now - DAY, 500.0, None)])
    site = "snow-feather-river"
    store.put_values("cdec", site, "snow_water_equivalent", "mm", [Value(now - 8 * DAY, 10.0, None), Value(now - DAY, 60.0, None)])
    store.put_values("cdec", site, "snow_percent_april1", "%", [Value(now - DAY, 7.5, None)])
    store.put_values("cdec", site, "snow_stations", "count", [Value(now - DAY, 9.0, None)])
    cfg = {"default_source": "cdec", "structures": [{"id": "oroville", "kind": "dam", "name": "Oroville", "site": "ORO", "lon": 0, "lat": 0, "duration": "H",
                           "show": ["reservoir_storage"], "storage_report": True, "snow": ["FEATHER RIVER"]}]}
    s = overlays.structures(cfg, store, now, {})[0]
    assert s["storage"] == {"capacity_m3": 1000, "average_m3": 500, "percent_full": 40.0, "percent_of_average": 80.0}
    assert s["snow"] == [{"group": "FEATHER RIVER", "site": site, "stations": 9, "swe_mm": 60.0, "percent_april1": 7.5, "change_7d_mm": 50.0}]
