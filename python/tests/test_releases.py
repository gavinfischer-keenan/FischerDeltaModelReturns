import pytest

from fdmr_adapters import usace_spk
from fdmr_runner import releases
from fdmr_store import Store, Value

STEP = 900
HOUR = 3600

REPORT = """<pre> Corps of Engineers Flood Control Requirements for California Reservoirs
 Data Ending 2400 hours   25 NOV 2026
 Shasta:        4,552,100 3,358,657 2,280,307  50     -1,078,350(  0)  89950 CFS
 New Hogan:       317,100   163,100   150,826  48        -12,274(  0)  4.51      ---
 Folsom:          966,823                                                 Forecasted Volumes****
  25NOV2026 18              566,823   375,383  39       -191,440(  0)        2,740;    5,475;    8,201;   15,325
  26NOV2026 12              566,823   375,053  39       -191,770(  0)        2,525;    5,251;    8,238;   23,995
   Pardee:        203,795                -NR-
</pre>"""


def test_corps_report_rows_and_folsoms_latest_line():
    t, rows = usace_spk.parse(REPORT)
    assert t == 1795651200 + 8 * 3600  # 24:00 PST on 25 Nov 2026 is 08:00 UTC on the 26th
    assert rows["New Hogan"] == {"gross": 317100, "toc": 163100, "storage": 150826, "above": -12274}
    assert rows["Folsom"]["toc"] == 566823 and rows["Folsom"]["storage"] == 375053
    assert "Pardee" not in rows


def setup(store, now, storage, toc, release, inflow_m3s, points="IN"):
    store.put_values("cdec", "R", "reservoir_storage", "m3", [Value(now - HOUR, storage, None)])
    store.put_values("cdec", "R", "reservoir_outflow", "m3/s", [Value(now - HOUR, release, None)])
    store.put_values("usace-spk", "R", "top_of_conservation", "m3", [Value(now - HOUR, toc, None)])
    store.put_values("cnrfc", points, "discharge", "m3/s", [Value(now + k * HOUR, inflow_m3s, None) for k in range(-2, 49)])


def test_flood_rule_raises_the_release_once_the_top_is_reached():
    store = Store()
    t0, n = 0, 4 * 48
    now = t0 + 4 * STEP
    i_now = 4
    # 100 m3/s in, 20 out: 80 m3/s fills 1.0e6 m3 of space in about 3.5 hours.
    setup(store, now, storage=9.0e6, toc=10.0e6, release=20.0, inflow_m3s=100.0)
    rule = {"reservoir_source": "cdec", "flood_pool_source": "usace-spk", "reservoir": "R", "report": "R", "inflow": {"group": "g", "point": "IN"}}
    out, rec = releases.rule_forecast(store, rule, 25.0, t0, STEP, n, i_now, now)
    assert "reaches_top_at" in rec and rec["flood_space_m3"] == 1_000_000
    assert out[i_now] == 25.0 and out[-1] == pytest.approx(105.0)  # the gauge's 25 plus the dam's rise from 20 to 100


def test_flood_rule_holds_while_there_is_room():
    store = Store()
    t0, n = 0, 4 * 48
    now = t0 + 4 * STEP
    setup(store, now, storage=1.0e6, toc=100.0e6, release=20.0, inflow_m3s=30.0)
    rule = {"reservoir_source": "cdec", "flood_pool_source": "usace-spk", "reservoir": "R", "report": "R", "inflow": {"group": "g", "point": "IN"}}
    out, rec = releases.rule_forecast(store, rule, 25.0, t0, STEP, n, 4, now)
    assert "reaches_top_at" not in rec and set(x for x in out[4:] if x is not None) == {25.0}


def test_release_forecast_upstream_moves_the_gauge():
    store = Store()
    t0, n = 0, 4 * 48
    now = t0 + 4 * STEP
    store.put_values("cnrfc", "UPR", "discharge", "m3/s", [Value(now + k * HOUR, 10.0 if k < 10 else 40.0, None) for k in range(-2, 49)])
    out, rec = releases.rule_forecast(store, {"release_forecast": {"group": "g", "point": "UPR"}}, 25.0, t0, STEP, n, 4, now)
    assert out[4] == 25.0 and out[-1] == 55.0 and rec["largest_rise_m3s"] == 30.0


def test_missing_inputs_hold_the_release_and_say_why():
    out, rec = releases.rule_forecast(Store(), {"reservoir_source": "cdec", "flood_pool_source": "usace-spk", "reservoir": "R", "report": "R", "inflow": {"group": "g", "point": "IN"}}, 25.0, 0, STEP, 10, 2, 2 * STEP)
    assert out is None and rec["how"].startswith("held: no top of conservation")
