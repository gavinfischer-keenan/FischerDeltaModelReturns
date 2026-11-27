import tomllib
from pathlib import Path

from fdmr_plugins.river_routing import storage

ROOT = Path(__file__).resolve().parents[2]


def test_the_switch_follows_the_velocity_head():
    assert storage.switch(0.003) == 0.0  # the Lek's weir pools at low flow
    assert storage.switch(0.015) == 0.5
    assert storage.switch(0.06) == 1.0
    cfg = {"beta": [[900, 0.2], [3000, 0.3]], "velocity_head_m": [[900, 0.005], [3000, 0.05]]}
    assert storage.ratio(cfg, 900) == 0.0
    assert abs(storage.ratio(cfg, 3000) - 0.3) < 1e-12
    assert storage.ratio(cfg, 5000) == storage.ratio(cfg, 3000)  # held beyond the table


def test_every_branchs_tables_are_sourced_and_sane():
    found = 0
    for p in ROOT.glob("regions/*/region.toml"):
        text = p.read_text(encoding="utf-8")
        for r in tomllib.loads(text).get("reaches", []):
            g = r.get("groyne_fields")
            if not g:
                continue
            found += 1
            assert "ref:vanmazijk1996" in text
            for table, lo, hi in ((g["beta"], 0.0, 0.6), (g["velocity_head_m"], 0.0, 0.2)):
                qs = [q for q, _ in table]
                assert qs == sorted(qs) and len(set(qs)) == len(qs)
                assert all(lo <= v <= hi for _, v in table)
    assert found == 3
