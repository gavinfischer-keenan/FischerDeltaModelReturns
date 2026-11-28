"""Fetch's plan (#34, #76): each request on its host's queue, from where the
store's copy ends; the store shared between threads."""

import threading

from fdmr_runner.fetch import DAY, held, host_of, predictions_from, window
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


def test_a_fetch_starts_where_the_stores_copy_ends():
    start, now = 1_000 * DAY, 1_365 * DAY
    # Nothing held: from the start of the period.
    assert window(None, start) == start
    # Held from about the start: just after its end (or at it, or a day before it).
    cov = (start + DAY, now - 3600, 9)
    assert window(cov, start) == now - 3600 + 1
    assert window(cov, start, after=0) == now - 3600
    assert window(cov, start, after=-DAY) == now - 3600 - DAY
    # A copy that begins well after the start is filled from the start...
    late = (start + 3 * DAY, now - 3600, 9)
    assert window(late, start) == start
    # ...unless any copy will do, or it begins within the slack allowed.
    assert window(late, start, slack=None) == now - 3600 + 1
    assert window(late, start, slack=4 * DAY) == now - 3600 + 1
    # Never before the period's start.
    assert window((start - 9 * DAY, start - 5 * DAY, 9), start) == start


def test_a_series_is_held_when_it_reaches_back_and_is_fresh():
    start, now = 1_000 * DAY, 1_365 * DAY
    assert held((start, now - 60, 9), start, now - 1800)
    assert not held((start, now - 7200, 9), start, now - 1800)  # stale
    assert not held((start + 3 * DAY, now - 60, 9), start, now - 1800)  # does not reach back
    assert held((start + 3 * DAY, now - 60, 9), start, now - 1800, slack=None)
    assert not held(None, start, now)


def test_tide_predictions_are_kept_a_month_back_and_ahead():
    now = 1_365 * DAY
    kept = (now - 36 * DAY, now + 16 * DAY, 9)
    assert predictions_from(kept, now, 35 * DAY, 40 * DAY, 15 * DAY) is None
    # Running out ahead: from a day before the copy's end; none held, or not back far enough: from 40 days back.
    assert predictions_from((now - 36 * DAY, now + 2 * DAY, 9), now, 35 * DAY, 40 * DAY, 15 * DAY) == now + DAY
    assert predictions_from(None, now, 35 * DAY, 40 * DAY, 15 * DAY) == now - 40 * DAY
    assert predictions_from((now - 20 * DAY, now + 16 * DAY, 9), now, 35 * DAY, 40 * DAY, 15 * DAY) == now - 40 * DAY
