"""A region's own clock (#67): its midnights and dates in its own time zone."""

from pathlib import Path

from fdmr_runner.region import load_region

ROOT = Path(__file__).resolve().parents[2] / "regions"


def test_midnight_and_date_are_the_regions_own():
    bay = load_region(ROOT / "sf-bay-delta")
    rhine = load_region(ROOT / "rhine-meuse")
    t = 1_795_824_000  # 2026-11-28 00:00 UTC
    assert bay.local_date(t) == "2026-11-27" and rhine.local_date(t) == "2026-11-28"
    assert bay.midnight(t) == t - 16 * 3600  # 00:00 PST on the 27th: 08:00 UTC
    assert rhine.midnight(t) == t - 3600  # 00:00 CET on the 28th, 23:00 UTC the day before
    assert rhine.midnight(t, 5) == rhine.midnight(t) - 5 * 86_400
