import json
import math
import tomllib
from pathlib import Path

import numpy as np
import pytest

from fdmr_adapters.lines import keyed
from fdmr_plugins import canal
from fdmr_runner import canals

REGION = Path(__file__).resolve().parents[2] / "regions" / "sf-bay-delta"


def east(m: float, lat: float = 38.0) -> float:
    """Degrees of longitude for m metres east at `lat`."""
    return m / (111_195 * math.cos(math.radians(lat)))


def test_sections_area_and_width():
    # A trapezoid: bottom 40 ft, 30 ft deep, sides 1.5:1 -> 2,550 ft2, 130 ft across.
    a, w = canal.section_shape({"bottom_ft": 40, "depth_ft": 30, "side": 1.5})
    assert a == pytest.approx(2550 * 0.3048**2) and w == pytest.approx(130 * 0.3048)
    # A full pipe, and one narrowing along its length.
    a, w = canal.section_shape({"diameter_in": 120})
    assert a == pytest.approx(math.pi * (120 * 0.0254) ** 2 / 4) and w == pytest.approx(3.048)
    assert canal.section_shape({"diameter_in": [72, 54]}, 0.5)[1] == pytest.approx(63 * 0.0254)


def test_a_canal_is_cut_into_short_channels_breaking_where_its_section_changes():
    line = [(-121.0, 38.0), (-121.0 + east(2500), 38.0)]
    c = {"section": [{"to_km": 1.0, "diameter_in": 120}, {"bottom_ft": 24, "depth_ft": 6.5, "side": 3}]}
    chs = canal.channels(c, line, most=1000)
    lengths = [c["length_m"] for c in chs]
    assert sum(lengths) == pytest.approx(2500, rel=1e-3) and max(lengths) <= 1000 + 1e-6
    assert chs[0]["length_m"] == pytest.approx(1000, rel=1e-3) and chs[0]["width_m"] == pytest.approx(3.048)
    assert all(c["width_m"] == pytest.approx((24 + 2 * 3 * 6.5) * 0.3048) for c in chs[1:])
    # Each channel's line meets the next.
    assert all(a["coords"][-1] == b["coords"][0] for a, b in zip(chs, chs[1:]))


def test_pieces_are_joined_from_start_to_end_whatever_their_order():
    a = [(0.0, 38.0), (east(500), 38.0)]
    b = [(east(1000), 38.0), (east(500) + east(10), 38.0)]  # drawn the other way, ending 10 m short
    spur = [(east(500), 38.0), (east(500), 38.01)]
    line = canals.walk([keyed(b), keyed(spur), keyed(a)])((0.0, 38.0), (east(1000), 38.0))
    assert line[0] == keyed(a)[0][1:] and line[-1] == keyed(b)[0][1:] and len(line) == 4
    # With a `via`, the line runs start to via to end, and says how far along the via is.
    c = {"start": (0.0, 38.0), "via": (east(500), 38.0), "end": (east(1000), 38.0)}
    line, via_m = canal.canal_line(c, canals.walk([keyed(b), keyed(spur), keyed(a)]))
    assert via_m == pytest.approx(500, rel=1e-3) and canal.length(line) == pytest.approx(1000, rel=1e-3)


def test_a_canal_is_fetched_once_per_source_file():
    calls = []
    fc = {"features": [{"properties": {"Conveyance": "Aqueduct"}, "geometry": {"type": "LineString", "coordinates": [[0, 38], [0.01, 38]]}},
                       {"properties": {"Conveyance": "Other"}, "geometry": {"type": "LineString", "coordinates": [[1, 38], [1.01, 38]]}}]}
    from fdmr_adapters import geojson

    real = geojson.download
    geojson.download = lambda url, get=None: calls.append(url) or fc
    try:
        files: dict = {}
        c = {"source": "file", "pieces": {"Conveyance": "Aqueduct"}}
        assert len(canals.fetch_pieces(c, {"file": "https://example.org/a.geojson"}, files)) == 1
        canals.fetch_pieces(c, {"file": "https://example.org/a.geojson"}, files)
    finally:
        geojson.download = real
    assert calls == ["https://example.org/a.geojson"]


def test_cut_at_keeps_the_first_metres():
    line = [(0.0, 38.0), (east(1000), 38.0)]
    assert canal.length(canal.cut_at(line, 400)) == pytest.approx(400, rel=1e-3)


def test_water_reaches_a_place_after_the_canal_upstream_of_it_has_filled():
    t = np.arange(0, 7200, 60.0)
    q = np.full(len(t), 10.0)
    left = canal.departures(t, q, np.array([3600.0]))  # 3,600 m3 of canal: 6 minutes at 10 m3/s
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
        assert canal.metres(line[0], c["start"]) < 50, c["id"]
    km = {k: v["length_m"] / 1000 for k, v in built["canals"].items()}
    assert km["swp"] == pytest.approx(107.3, abs=0.5)  # DWR Bulletin 132-16, Table 1-5
    assert km["cvp"] == pytest.approx(107.5, abs=0.1)  # mileposts 2.53 to 69.3
    assert km["north_bay"] == pytest.approx(27.6 * 1.609344, abs=0.5)  # Bulletin 132-16, Table 1-5
    geo, members, area = canal.geometry(cfg, built)
    assert [i["export"] for i in geo["inlets"]] == [c["export"] for c in cfg["canal"]]
    assert len(geo["exports"]) == len(cfg["canal"]) and sum(map(len, members)) == len(geo["elements"]) == len(area)
    # The aqueduct runs at 4 ft/s at its design flow of 10,000 cfs (Bulletin 200, Vol. II, p. 132).
    a = canal.section_shape(cfg["canal"][0]["section"][1])[0]
    assert 10_000 * 0.3048**3 / a / 0.3048 == pytest.approx(3.9, abs=0.1)


def _network(tmp_path: Path) -> Path:
    """A one-day network reach with an export for each of the region's canals,
    publishing ec, ssc and the bed's mud (#49)."""
    import fdmr_py

    cfg = tomllib.loads((REGION / "canals.toml").read_text(encoding="utf-8"))
    names = [c["export"] for c in cfg["canal"]]
    net = tmp_path / "net"
    (net / "geometry").mkdir(parents=True)
    (net / "delta").mkdir()
    nodes = [{"id": i} for i in range(len(names) + 1)]
    els = [{"id": i + 1, "up": 0, "down": i + 1} for i in range(len(names))]
    (net / "geometry" / "delta.json").write_text(json.dumps(
        {"nodes": nodes, "elements": els, "exports": [{"name": n, "node": i + 1} for i, n in enumerate(names)]}), encoding="utf-8")
    steps, step, t0, n_ch, m = 96, 900, 1_764_201_600, len(els), len(nodes)
    fdmr_py.write_field_file(str(net / "delta" / "2025-11-27.nodes.fdf"), "delta:nodes", 1, m, 1, steps, t0, step,
                             [("export", "m3/s", True, [50.0] * steps * m)])
    fdmr_py.write_field_file(str(net / "delta" / "2025-11-27.quality.fdf"), "delta:quality", 1, n_ch, 1, steps, t0, step, [
        ("ec", "uS/cm", True, [500.0] * steps * n_ch),
        ("ssc", "mg/L", True, [20.0] * steps * n_ch),
        ("bed_mud", "kg/m2", True, [3.0] * steps * n_ch)])
    (net / "reach.json").write_text(json.dumps({
        "id": "delta", "quality_fields": ["ec", "ssc", "bed_mud"], "bed_fields": ["bed_mud"],
        "time": {"start": "2025-11-27T00:00Z", "step_s": step, "days": ["2025-11-27"]}, "now": "2025-11-27T12:00Z",
        "certainty": [1.0] * steps}), encoding="utf-8")
    return net


def test_the_canals_carry_the_waters_own_fields_in_their_units_and_leave_the_bed(tmp_path):
    import fdmr_py

    pub = canals.publish(REGION, _network(tmp_path), tmp_path / "out")
    entry = json.loads((pub / "reach.json").read_text(encoding="utf-8"))
    assert entry["quality_fields"] == ["ec", "ssc"]
    # What the canals do not model is published with them (#72).
    assert [x["how"] for x in entry["assumptions"]] == [canal.ASSUMED]
    *_, fields = fdmr_py.read_field_file(str(pub / canals.REACH / "2025-11-27.quality.fdf"))
    assert {name: unit for name, unit, _ in fields} == {"ec": "uS/cm", "ssc": "mg/L"}
