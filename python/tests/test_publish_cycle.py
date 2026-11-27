"""A whole publish, end to end, on the plain test region (regions/test-colorado),
offline (issue #61): a store seeded with readings, `publish()` into a folder,
and every file read back. The safety net for changes to the publish path."""

import json
import math
import os
import time
from pathlib import Path

import fdmr_py
import pytest

from fdmr_runner import publish
from fdmr_store import ChannelMeasurement, Store, Value

REGION = Path(__file__).resolve().parents[2] / "regions" / "test-colorado"
STEP = 900
DAY = 86_400
GAUGES = {"09380000": 350.0, "09402500": 380.0}  # release and main, m3/s


def seeded(path: Path, now: int, days: int = 4) -> Store:
    """Readings every 15 minutes, a daily swing like a dam's, and channel
    measurements of a power-law channel (area 20 Q^0.6, width 8 Q^0.3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(path)
    t0 = (now - days * DAY) // STEP * STEP
    for site, q in GAUGES.items():
        vals = [Value(t, q * (1 + 0.2 * math.sin(2 * math.pi * (t % DAY) / DAY))) for t in range(t0, now + 1, STEP)]
        store.put_values("usgs", site, "discharge", "m3/s", vals)
        ms = [ChannelMeasurement(now - 365 * DAY + i * DAY, f, 20 * f**0.6, 8 * f**0.3, f / (20 * f**0.6))
              for i, f in enumerate(range(100, 1300, 100))]
        store.put_channel_measurements("usgs", site, ms)
    return store


@pytest.fixture
def cycle(tmp_path):
    now = int(time.time()) // STEP * STEP - 2 * STEP
    store = seeded(tmp_path / "store" / "fdmr.sqlite", now)
    out = tmp_path / "publish"
    cdir = publish.publish(REGION, store, out, days=2, datums={}, ahead=2)
    return now, out, cdir


def test_a_cycle_is_published_whole(cycle):
    now, out, cdir = cycle
    index = json.loads((cdir / "index.json").read_text(encoding="utf-8"))
    assert index["format"] == "fdmr-flow-field" and index["region"] == "test-colorado"
    assert index["now"] == publish.utc(now)
    assert os.readlink(out / "current") == cdir.name
    (river,) = index["reaches"]
    assert river["id"] == "river" and river["kind"] == "river-routing"
    assert river["maths"] and river["assumptions"]
    t0 = (now - 2 * DAY) // DAY * DAY
    n = (now + 2 * DAY - t0) // STEP + 1
    assert len(river["certainty"]) == n and river["certainty"][0] == 1

    geo = json.loads((cdir / "geometry" / "river.json").read_text(encoding="utf-8"))
    assert len(geo["elements"]) == river["elements"] == len(geo["lines"])

    steps = 0
    for day in index["time"]["days"]:
        reach, dims, n_el, layers, k, d0, step, fields = fdmr_py.read_field_file(str(cdir / "river" / f"{day}.fdf"))
        assert (reach, dims, n_el, layers, step) == ("river", 1, river["elements"], 1, STEP)
        got = {name: (unit, v) for name, unit, v in fields}
        assert set(got) == set(river["fields"])
        assert got["flow"][0] == "m3/s" and got["velocity"][0] == "m/s" and got["dispersion"][0] == "m2/s"
        assert len(got["flow"][1]) == k * n_el
        assert all(v > 0 for v in got["flow"][1]) and all(v > 0 for v in got["dispersion"][1])
        steps += k
    assert steps == n


def test_the_flow_follows_the_gauges(cycle):
    """At the main gauge, the published flow is the measured flow (m3/s), to rounding."""
    now, _, cdir = cycle
    index = json.loads((cdir / "index.json").read_text(encoding="utf-8"))
    geo = json.loads((cdir / "geometry" / "river.json").read_text(encoding="utf-8"))
    at = json.loads((REGION / "river.json").read_text(encoding="utf-8"))["gauges_m"]["09402500"]
    e = min(range(len(geo["elements"])), key=lambda i: abs((geo["elements"][i]["start_m"] + geo["elements"][i]["end_m"]) / 2 - at))
    day = publish.utc(now - DAY, "%Y-%m-%d")
    _, _, n_el, _, k, d0, step, fields = fdmr_py.read_field_file(str(cdir / "river" / f"{day}.fdf"))
    flow = dict((name, v) for name, _, v in fields)["flow"]
    t = d0 + (k // 2) * step
    measured = GAUGES["09402500"] * (1 + 0.2 * math.sin(2 * math.pi * (t % DAY) / DAY))
    assert flow[(k // 2) * n_el + e] == pytest.approx(measured, rel=0.05)


def test_the_same_now_published_again_never_writes_over_the_live_cycle(cycle, tmp_path):
    """A gauge that has not reported since: the second publish has the same "now",
    and goes to a folder of its own beside the live one (#53)."""
    now, out, first = cycle
    before = {f: f.stat().st_mtime_ns for f in first.rglob("*") if f.is_file()}
    store = Store(tmp_path / "store" / "fdmr.sqlite")
    second = publish.publish(REGION, store, out, days=2, datums={}, ahead=2)
    assert second.name == first.name + ".r2"
    assert os.readlink(out / "current") == second.name
    assert {f: f.stat().st_mtime_ns for f in first.rglob("*") if f.is_file()} == before
    assert not list(out.glob(".building-*"))


def test_a_cycle_says_what_failed_and_is_degraded(cycle, tmp_path):
    """A reach that should be linked in and is not, and a failed step before
    publish, are reported, and the cycle's status says so (#55)."""
    now, out, first = cycle
    assert json.loads((first / "index.json").read_text(encoding="utf-8"))["status"] == "ok"
    store_dir = tmp_path / "store"
    (store_dir / "cycle-steps-test-colorado.json").write_text(
        json.dumps([{"step": "fetch", "ok": True, "seconds": 3}, {"step": "dsm2", "ok": False, "seconds": 9}]), encoding="utf-8")
    cdir = publish.publish(REGION, Store(store_dir / "fdmr.sqlite"), out, days=2, datums={}, ahead=2,
                           extra_reaches=[tmp_path / "no-such-reach"])
    index = json.loads((cdir / "index.json").read_text(encoding="utf-8"))
    assert index["status"] == "degraded"
    steps = [p["step"] for p in index["problems"]]
    assert any("no-such-reach" in s for s in steps) and "dsm2" in steps
    assert index["cycle_steps"][1] == {"step": "dsm2", "ok": False, "seconds": 9}


def test_each_modes_last_run_is_kept_and_systemd_does_not_overwrite_a_fresh_record(tmp_path):
    from fdmr_runner import cycle_runs

    f = tmp_path / "cycle-runs.json"
    cycle_runs.record(f, "hourly", 1000, "0", now=1600)
    cycle_runs.record(f, "delta", 2000, "1", now=3000)
    runs = cycle_runs.record(f, "delta", 0, "unit-failed", now=3050)  # cycle.sh recorded it a moment ago
    assert runs["delta"]["status"] == "a step failed" and runs["hourly"]["status"] == "ok"
    runs = cycle_runs.record(f, "nightly", 0, "unit-failed", now=9000)  # nothing recorded: systemd ended it
    assert runs["nightly"]["status"] == "ended by systemd (timeout or crash)"
    assert json.loads(f.read_text(encoding="utf-8")) == runs


def test_the_channel_maths_that_runs_is_the_one_recorded(cycle, tmp_path):
    """The register's channel choice decides what runs (#64): each registered
    alternative runs when chosen, and a reach whose gauges disagree with its
    choice is refused rather than run with other maths than it records."""
    from fdmr_plugins.river_routing import channel
    from fdmr_runner.region import Gauge

    now, out, first = cycle
    river = json.loads((first / "index.json").read_text(encoding="utf-8"))["reaches"][0]
    assert river["maths"]["channel"]["name"] == publish.POWER_LAW
    store = Store(tmp_path / "store" / "fdmr.sqlite")
    gauges, chainage = publish.river_chain(REGION, "river")
    power = publish.fit_shapes(gauges, chainage, store, publish.POWER_LAW)
    assert all(isinstance(s, channel.ChannelShape) for s in power.values())

    wide = Gauge("river", "W", "W", "main", channel={"width_m": 100.0, "slope": 1e-4, "chezy": 40.0})
    shapes = publish.fit_shapes([gauges[0], wide], {gauges[0].site: 0.0, "W": 1000.0}, store, publish.WIDE_CHANNEL)
    expect = channel.wide_channel(100.0, 1e-4, chezy=40.0)
    assert shapes["W"] == expect and shapes[gauges[0].site] == expect  # no table of its own: the nearest gauge's
    with pytest.raises(ValueError, match="channel"):
        publish.fit_shapes([wide], {"W": 0.0}, store, publish.POWER_LAW)
    with pytest.raises(ValueError, match="channel"):  # the trial asks for maths these gauges cannot give
        publish.publish(REGION, store, out, days=2, datums={}, ahead=2,
                        trial={"name": "t", "maths": {"river": {"channel": publish.WIDE_CHANNEL}}})
