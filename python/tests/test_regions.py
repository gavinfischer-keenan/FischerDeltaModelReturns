from pathlib import Path

import pytest

from fdmr_runner.region import load_region

REGIONS = sorted((Path(__file__).resolve().parents[2] / "regions").glob("*/region.toml"))


@pytest.mark.parametrize("path", REGIONS, ids=lambda p: p.parent.name)
def test_region_package_is_well_formed(path):
    r = load_region(path)
    assert r.id == path.parent.name
    reach_ids = {x["id"] for x in r.reaches}
    for g in r.gauges:
        assert g.reach in reach_ids, f"gauge {g.site} names unknown reach {g.reach}"
        assert g.role in {"release", "main", "tributary"}
        if g.source == "usgs":
            assert g.site.isdigit()
        elif g.source == "cdec":  # placed by a USGS site beside it, or by its coordinates
            assert g.site.isalpha() and g.site.isupper()
            assert g.usgs_site or (g.lon is not None and g.lat is not None), g.site
        else:  # another country's agency: placed by its coordinates, its channel given (no USGS measurements)
            assert g.source == "rws" and g.site == g.site.lower(), g.site
            assert g.lon is not None and g.lat is not None, g.site
            if g.role in ("release", "main"):
                assert g.channel and g.channel["width_m"] > 0 and g.channel["slope"] > 0, g.site
                assert ("chezy" in g.channel) != ("manning_n" in g.channel), g.site
    for reach in reach_ids:
        gs = r.gauges_for(reach)
        if gs:
            # A reach starts at its release; one with no gauge below it is
            # routed from the release alone (such as the American below Nimbus).
            assert gs[0].role == "release", f"{reach}: the first gauge must be the release"
            assert sum(g.role == "release" for g in gs) == 1, reach


def test_both_regions_are_present():
    assert {p.parent.name for p in REGIONS} >= {"sf-bay-delta", "test-colorado"}


def test_an_unknown_setting_or_station_key_is_refused(tmp_path):
    """A misspelt or stale setting is an error, never silently ignored (#63)."""
    head = 'id = "t"\nname = "test"\ntimezone = "UTC"\n'
    (tmp_path / "region.toml").write_text(head + 'display_units = "us"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="display_units"):
        load_region(tmp_path)
    (tmp_path / "region.toml").write_text(head + '[[stations]]\nsource = "cdec"\nid = "KWK"\nname = "K"\nmeasures = ["ec"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown keys"):
        load_region(tmp_path)


def test_every_station_is_listed_the_same_way():
    for path in REGIONS:
        for s in load_region(path).stations:
            assert {"source", "site", "name", "quantities"} <= set(s), (path.parent.name, s)


def test_no_source_is_assumed_for_a_gauge_or_a_station(tmp_path):
    """#66: a gauge without a source takes the region's gauge_source, and
    without one the region is refused; a station likewise overlays.toml's."""
    import pytest

    from fdmr_runner import overlays
    from fdmr_runner.region import load_region

    (tmp_path / "region.toml").write_text('id = "x"\nname = "X"\ntimezone = "UTC"\n'
                                          '[[gauges]]\nreach = "r"\nsite = "1"\nname = "G"\nrole = "main"\n')
    with pytest.raises(ValueError, match="gauge_source"):
        load_region(tmp_path)
    (tmp_path / "region.toml").write_text('id = "x"\nname = "X"\ntimezone = "UTC"\ngauge_source = "usgs"\n'
                                          '[[gauges]]\nreach = "r"\nsite = "1"\nname = "G"\nrole = "main"\n')
    assert load_region(tmp_path).gauges[0].source == "usgs"
    with pytest.raises(ValueError, match="default_source"):
        overlays.station_source({}, {"site": "S"})
    assert overlays.station_source({"default_source": "cdec"}, {"site": "S"}) == "cdec"
    assert overlays.station_source({"default_source": "cdec"}, {"site": "S", "source": "ndbc"}) == "ndbc"
