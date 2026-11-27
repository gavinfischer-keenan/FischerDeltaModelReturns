"""NHD flowlines by name, joined and walked (fdmr_adapters.nhd)."""

from urllib.parse import parse_qs, urlparse

from fdmr_adapters import nhd


def fake(features):
    seen = {}

    def get(url):
        seen["query"] = parse_qs(urlparse(url).query)
        return {"features": features}

    return get, seen


def test_named_pieces_join_where_they_meet_and_walk_start_to_end():
    canal = {"geometry": {"type": "LineString", "coordinates": [[-121.64, 38.68], [-121.62, 38.62], [-121.61, 38.55]]}}
    drain = {"geometry": {"type": "MultiLineString", "coordinates": [[[-121.61, 38.55], [-121.60, 38.51], [-121.589, 38.475]]]}}
    get, seen = fake([canal, drain])
    pieces = nhd.flowlines(["Tule Canal", "Toe Drain"], (-121.75, 38.4, -121.55, 38.8), get=get)
    assert seen["query"]["where"] == ["gnis_name='Tule Canal' OR gnis_name='Toe Drain'"]
    line = nhd.line_between(pieces, (-121.6441, 38.6777), (-121.5887, 38.4749))
    assert line[0] == (-121.64, 38.68) and line[-1] == (-121.589, 38.475) and len(line) == 5


def test_a_name_with_a_quote_is_escaped():
    get, seen = fake([])
    nhd.flowlines(["O'Neill Slough"], (0, 0, 1, 1), get=get)
    assert seen["query"]["where"] == ["gnis_name='O''Neill Slough'"]
