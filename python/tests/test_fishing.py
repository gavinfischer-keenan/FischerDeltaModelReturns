import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import fdmr_py
from fdmr_runner import fishing

REGION = Path(__file__).resolve().parents[2] / "regions" / "sf-bay-delta"


def test_salinity_bands_follow_the_rules():
    bands = fishing.load(REGION)["white_sturgeon"]["salinity_bands"]
    s = np.array([0.3, 5.0, 12.0, 20.0, 30.0, np.nan])
    got = fishing.salinity_score(s, bands, migrating=False)
    assert got[:5].tolist() == [0.4, 1.0, 0.7, 0.4, 0.2] and np.isnan(got[5])
    assert fishing.salinity_score(s, bands, migrating=True)[0] == 0.7  # fresh water in the spawning run


def _cycle(tmp: Path, day: str, sal: list[float], temp: list[float], flows: list[float] | None = None) -> dict:
    t0 = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    (tmp / "delta").mkdir(parents=True, exist_ok=True)
    (tmp / "geometry").mkdir(exist_ok=True)
    steps, n = 4, len(sal)
    fdmr_py.write_field_file(str(tmp / "delta" / f"{day}.quality.fdf"), "delta:quality", 1, n, 1, steps, t0, 900,
                             [("salinity", "psu", True, [v for _ in range(steps) for v in sal]),
                              ("temperature", "degC", True, [v for _ in range(steps) for v in temp])])
    (tmp / "geometry" / "delta.json").write_text(json.dumps({"network": True, "lines": [[[-122.0 + i, 38.0], [-121.9 + i, 38.1]] for i in range(n)]}))
    return {"cycle": "c1", "now": f"{day}T00:00Z", "reaches": [{"id": "delta", "time": {"days": [day]}}]}


def test_white_sturgeon_scores_season_times_salinity(tmp_path):
    index = _cycle(tmp_path, "2026-03-10", [0.2, 6.0, 14.0], [12.0, 12.0, 25.0])
    fx = fishing.forecast(REGION, tmp_path, index)
    d = fx["species"]["white_sturgeon"]["days"][0]
    assert d["season"] == 1.0  # March: the Report Card's largest monthly share
    assert d["reaches"]["delta"] == [70, 100, 70]  # fresh water in the run scores 0.7
    assert d["outside_caught_temperature"]["delta"] == 1  # 25 degC: flagged, not scored down
    assert d["best_score"] == 100 and d["places_at_best"] == {"delta": 1}
    assert d["best"][0]["element"] == 1 and d["best"][0]["lon"] == -120.9  # the line's middle vertex


def test_closed_month_scores_zero(tmp_path):
    index = _cycle(tmp_path, "2026-08-10", [6.0], [15.0])
    d = fishing.forecast(REGION, tmp_path, index)["species"]["white_sturgeon"]["days"][0]
    assert d["closed"] and d["reaches"]["delta"] == [0]


def test_green_sturgeon_flags_its_observed_ranges(tmp_path):
    index = _cycle(tmp_path, "2026-06-10", [5.0, 12.0, 12.0], [15.0, 15.0, 23.0])
    d = fishing.forecast(REGION, tmp_path, index)["species"]["green_sturgeon"]["days"][0]
    assert d["in_season"] and d["reaches"]["delta"] == [1]  # salinity >= 8.8 and 11.9-21.9 degC
