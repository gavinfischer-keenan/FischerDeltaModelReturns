from fdmr_adapters import osm


def test_shortest_path_along_joined_ways_ignores_direction_and_side_branches():
    # A main river of three ways (one mapped backwards) and a side arm off node 3.
    segs = [
        [(1, 5.00, 52.0), (2, 5.01, 52.0), (3, 5.02, 52.0)],
        [(5, 5.04, 52.0), (4, 5.03, 52.0), (3, 5.02, 52.0)],  # drawn the other way
        [(3, 5.02, 52.0), (9, 5.02, 52.1)],  # a side arm
    ]
    line = osm.line_between(segs, (4.999, 52.0), (5.041, 52.0))
    assert line == [(5.00, 52.0), (5.01, 52.0), (5.02, 52.0), (5.03, 52.0), (5.04, 52.0)]


def test_ways_asks_for_named_rivers_in_the_box():
    seen = {}

    def get(ql):
        seen["ql"] = ql
        return {"elements": [{"type": "way", "nodes": [1, 2], "geometry": [{"lon": 5.0, "lat": 52.0}, {"lon": 5.1, "lat": 52.0}]}]}

    got = osm.ways(["Waal", "Boven-Rijn"], (5.0, 51.7, 6.2, 52.0), get=get)
    assert got == [[(1, 5.0, 52.0), (2, 5.1, 52.0)]]
    assert '"name"~"^(Waal|Boven-Rijn)$"' in seen["ql"] and "(51.7,5.0,52.0,6.2)" in seen["ql"]
    assert 'way(r.r:"main_stream")' in seen["ql"]  # the main stream of the river's relation first
