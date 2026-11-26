from fdmr_adapters import openet
from fdmr_store import Store

DAY = 86_400


def test_daily_et_from_the_day_after_the_last_stored():
    asked = []

    def post(body, key):
        asked.append(body)
        return [{"time": "2026-10-01", "et": 3.5}, {"time": "2026-10-02", "et": None}, {"time": "2026-10-03", "et": 2.9}]

    store = Store()
    a = openet.OpenEtAdapter(store, key="k", post=post)
    area = {"site": "area:d", "box": [-121.9, 37.6, -121.2, 38.5]}
    assert a.fetch(area, 1790812800, 1791072000) == 2  # 1 to 4 Oct 2026
    assert asked[0]["geometry"][:4] == [-121.9, 37.6, -121.2, 37.6] and asked[0]["model"] == "Ensemble"
    a.fetch(area, 1790812800, 1791072000)
    assert asked[1]["date_range"][0] == "2026-10-04"


def test_samples_spread_evenly_and_stay_under_the_limit():
    from fdmr_runner import soil

    got = soil.openet_samples({"site": "area:d", "box": [-121.95, 37.65, -121.20, 38.55]})
    assert len(got) == 6 and got[0]["site"] == "area:d/s1"
    w, s, e, n = got[0]["box"]
    km2 = (e - w) * 111.32 * 0.788 * (n - s) * 110.57  # cos(38 N) = 0.788
    assert km2 * 247.1 < 50_000
