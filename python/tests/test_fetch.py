"""Fetch's plan (#34): each request on its host's queue; the store shared between threads."""

import threading

from fdmr_runner.fetch import host_of
from fdmr_store import Store, Value


def test_every_label_goes_to_its_host():
    assert host_of("11447650 discharge") == "usgs"
    assert host_of("11447650 channel_measurements") == "usgs"
    assert host_of("cdec 12 stations (H) from 2026-11-27T00:00:00Z") == "cdec.water.ca.gov"
    assert host_of("cdec snow reports") == "cdec.water.ca.gov"
    assert host_of("noaa 9414290 water_level") == "tidesandcurrents.noaa.gov"
    assert host_of("nws KSAC observations") == "api.weather.gov"
    assert host_of("nws precipitation") == "water.noaa.gov"
    assert host_of("ghcnh USW00023232") == "ncei.noaa.gov"
    assert host_of("cnrfc HourlyNumeric") == "cnrfc.noaa.gov"
    assert host_of("sea level anomaly") == host_of("erddap 46013") == "erddap"
    assert host_of("rws lobith discharge") == "rijkswaterstaat"
    assert host_of("something new") == "other"


def test_the_store_can_be_shared_between_threads():
    store = Store()
    errors = []

    def write(site: str) -> None:
        try:
            for k in range(50):
                store.put_values("x", site, "flow", "m3/s", [Value(k, float(k))])
                store.coverage("x", site, "flow")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=write, args=(f"s{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert all(store.coverage("x", f"s{i}", "flow") == (0, 49, 50) for i in range(4))
