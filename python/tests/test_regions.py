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
        assert g.site.isdigit()
    for reach in reach_ids:
        gs = r.gauges_for(reach)
        if gs:
            assert gs[0].role == "release", f"{reach}: the first gauge must be the release"
            assert any(g.role == "main" for g in gs)


def test_both_regions_are_present():
    assert {p.parent.name for p in REGIONS} >= {"sf-bay-delta", "test-colorado"}
