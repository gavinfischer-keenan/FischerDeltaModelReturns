import json
import math
import tomllib
from pathlib import Path

import numpy as np
import pytest

from fdmr_runner import canals

REGION = Path(__file__).resolve().parents[2] / "regions" / "sf-bay-delta"


def east(m: float, lat: float = 38.0) -> float:
    """Degrees of longitude for m metres east at `lat`."""
    return m / (111_195 * math.cos(math.radians(lat)))


def test_sections_area_and_width():
    # A trapezoid: bottom 40 ft, 30 ft deep, sides 1.5:1 -> 2,550 ft2, 130 ft across.
    a, w = canals.section_shape({"bottom_ft": 40, "depth_ft": 30, "side": 1.5})
    assert a == pytest.approx(2550 * 0.3048**2) and w == pytest.approx(130 * 0.3048)
    # A full pipe, and one narrowing along its length.
    a, w = canals.section_shape({"diameter_in": 120})
    assert a == pytest.approx(math.pi * (120 * 0.0254) ** 2 / 4) and w == pytest.approx(3.048)
    assert canals.section_shape({"diameter_in": [72, 54]}, 0.5)[1] == pytest.approx(63 * 0.0254)


def test_a_canal_is_cut_into_short_channels_breaking_where_its_section_changes():
    line = [(-121.0, 38.0), (-121.0 + east(2500), 38.0)]
    canal = {"section": [{"to_km": 1.0, "diameter_in": 120}, {"bottom_ft": 24, "depth_ft": 6.5, "side": 3}]}
    chs = canals.channels(canal, line, most=1000)
    lengths = [c["length_m"] for c in chs]
    assert sum(lengths) == pytest.approx(2500, rel=1e-3) and max(lengths) <= 1000 + 1e-6
    assert chs[0]["length_m"] == pytest.approx(1000, rel=1e-3) and chs[0]["width_m"] == pytest.approx(3.048)
    assert all(c["width_m"] == pytest.approx((24 + 2 * 3 * 6.5) * 0.3048) for c in chs[1:])
    # Each channel's line meets the next.
    assert all(a["coords"][-1] == b["coords"][0] for a, b in zip(chs, chs[1:]))


def test_pieces_are_stitched_from_start_to_end_whatever_their_order():
    a = [(0.0, 38.0), (east(500), 38.0)]
    b = [(east(1000), 38.0), (east(500), 38.0)]  # drawn the other way
    spur = [(east(500), 38.0), (east(500), 38.01)]
    line = canals.stitch([b, spur, a], (0.0, 38.0), (east(1000), 38.0))
    assert line[0] == a[0] and line[-1] == b[0] and len(line) == 3


def test_cut_at_keeps_the_first_metres():
    line = [(0.0, 38.0), (east(1000), 38.0)]
    assert canals.length(canals.cut_at(line, 400)) == pytest.approx(400, rel=1e-3)


def test_water_reaches_a_place_after_the_canal_upstream_of_it_has_filled():
    t = np.arange(0, 7200, 60.0)
    q = np.full(len(t), 10.0)
    left = canals.departures(t, q, np.array([3600.0]))  # 3,600 m3 of canal: 6 minutes at 10 m3/s
    assert left[-1, 0] == pytest.approx(t[-1] - 360, abs=1e-6)
    assert left[0, 0] == t[0]  # water there before the record began


def test_the_regions_canals_are_built_and_sourced():
    cfg = tomllib.loads((REGION / "canals.toml").read_text(encoding="utf-8"))
    built = json.loads((REGION / "canals.json").read_text(encoding="utf-8"))
    exports = tomllib.loads((REGION / "dsm2.toml").read_text(encoding="utf-8"))["exports"]
    for c in cfg["canal"]:
        assert c["export"] in exports, c["id"]
        line = built["canals"][c["id"]]["coords"]
        # Each begins where its water leaves the Delta model.
        assert canals.metres(line[0], c["start"]) < 50, c["id"]
    km = {k: v["length_m"] / 1000 for k, v in built["canals"].items()}
    assert km["swp"] == pytest.approx(107.3, abs=0.5)  # DWR Bulletin 132-16, Table 1-5
    assert km["cvp"] == pytest.approx(107.5, abs=0.1)  # mileposts 2.53 to 69.3
    assert km["north_bay"] == pytest.approx(27.6 * 1.609344, abs=0.5)  # Bulletin 132-16, Table 1-5
    geo, members = canals.geometry(cfg, built)
    assert [i["export"] for i in geo["inlets"]] == [c["export"] for c in cfg["canal"]]
    assert len(geo["exports"]) == len(cfg["canal"]) and sum(map(len, members)) == len(geo["elements"])
    # The aqueduct runs at 4 ft/s at its design flow of 10,000 cfs (Bulletin 200, Vol. II, p. 132).
    a = canals.section_shape(cfg["canal"][0]["section"][1])[0]
    assert 10_000 * 0.3048**3 / a / 0.3048 == pytest.approx(3.9, abs=0.1)
