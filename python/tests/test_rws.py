from fdmr_adapters import rws
from fdmr_store import Store


def meting(t, v, code="00", depth=0, method="NVT"):
    return {"Meetwaarde": {"Waarde_Numeriek": v}, "Tijdstip": t,
            "WaarnemingMetadata": {"Kwaliteitswaardecode": code, "Statuswaarde": "Ongecontroleerd", "Bemonsteringshoogte": str(depth)}}


REPLY = {"Succesvol": True, "WaarnemingenLijst": [
    {"AquoMetadata": {"WaardeBewerkingsMethode": {"Code": "NVT"}}, "MetingenLijst": [
        meting("2026-11-26T23:40:00.000+01:00", 2035.06),
        meting("2026-11-26T23:20:00.000+01:00", 2030.0),  # not in time order, as the API sends them
        meting("2026-11-26T23:30:00.000+01:00", 1.0e9, code="99"),  # a gap
        meting("2026-11-26T23:50:00.000+01:00", 2036.0, code="25"),
    ]},
    {"AquoMetadata": {"WaardeBewerkingsMethode": {"Code": "GEM24H"}}, "MetingenLijst": [meting("2026-11-26T00:00:00.000+01:00", 1990.0)]},
]}


def test_parse_keeps_raw_values_in_time_order_and_drops_gaps():
    got = rws.parse(REPLY, 1.0)
    assert [v.value for v in got] == [2030.0, 2035.06, 2036.0]
    assert got[0].t == 1795731600  # 23:20 at +01:00 is 22:20 UTC
    assert got[2].qualifier == "Ongecontroleerd:25"


def test_levels_in_cm_become_metres_and_depths_are_not_mixed():
    reply = {"WaarnemingenLijst": [{"AquoMetadata": {}, "MetingenLijst": [
        meting("2026-11-26T23:40:00+01:00", 112.0, depth=-250), meting("2026-11-26T23:40:00+01:00", 4091.0, depth=-450)]}]}
    assert [v.value for v in rws.parse(reply, 0.01, depth_cm=-250)] == [1.12]


def test_fetch_asks_for_the_station_quantity_and_period():
    seen = {}

    def post(url, body):
        seen.update(url=url, body=body)
        return REPLY

    s = Store()
    n = rws.RwsAdapter(s, post=post).fetch("lobith.bovenrijn.tolkamer", "discharge", 1795726800, 1795736400)
    assert n == 3 and seen["url"].endswith("/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen")
    meta = seen["body"]["AquoPlusWaarnemingMetadata"]["AquoMetadata"]
    assert meta["Grootheid"]["Code"] == "Q" and meta["ProcesType"] == "meting"
    assert seen["body"]["Periode"]["Begindatumtijd"] == "2026-11-26T22:00:00.000+01:00"
    assert s.unit("rws", "lobith.bovenrijn.tolkamer", "discharge") == "m3/s"
    rws.RwsAdapter(s, post=lambda u, b: None).fetch("x", "discharge", 0, 1, forecast=True)  # 204: nothing, no error
