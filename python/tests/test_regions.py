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
        else:  # another agency's station: placed by a USGS site beside it, or by its coordinates
            assert g.source == "cdec" and g.site.isalpha() and g.site.isupper()
            assert g.usgs_site or (g.lon is not None and g.lat is not None), g.site
    for reach in reach_ids:
        gs = r.gauges_for(reach)
        if gs:
            # A reach starts at its release; one with no gauge below it is
            # routed from the release alone (such as the American below Nimbus).
            assert gs[0].role == "release", f"{reach}: the first gauge must be the release"
            assert sum(g.role == "release" for g in gs) == 1, reach


def test_both_regions_are_present():
    assert {p.parent.name for p in REGIONS} >= {"sf-bay-delta", "test-colorado"}
