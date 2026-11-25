from fdmr_runner.publish import corrections
from fdmr_runner.region import Gauge

G = [Gauge("river", s, s, "main") for s in ("A", "B", "C")]


def test_corrections_follow_the_segments_and_default_to_one():
    cal = {"reaches": [{"reach": "river", "segments": [
        {"up": "A", "down": "B", "wave_scale": 0.7, "water_factor": 1.1},
        {"up": "X", "down": "Y", "wave_scale": 2.0, "water_factor": 2.0},
    ]}]}
    assert corrections(cal, "river", G) == ([0.7, 1.0], [1.1, 1.0])
    assert corrections(None, "river", G) == ([1.0, 1.0], [1.0, 1.0])
