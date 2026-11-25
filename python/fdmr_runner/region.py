"""Reading region packages (regions/<id>/region.toml). Region packages are data only."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Gauge:
    reach: str
    site: str
    name: str
    role: str  # "release", "main" or "tributary"


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    timezone: str
    reaches: list[dict]
    gauges: list[Gauge]

    def gauges_for(self, reach: str) -> list[Gauge]:
        return [g for g in self.gauges if g.reach == reach]


def load_region(path: str | Path) -> Region:
    p = Path(path)
    if p.is_dir():
        p = p / "region.toml"
    d = tomllib.loads(p.read_text(encoding="utf-8"))
    return Region(
        id=d["id"],
        name=d["name"],
        timezone=d["timezone"],
        reaches=d.get("reaches", []),
        gauges=[Gauge(**g) for g in d.get("gauges", [])],
    )
