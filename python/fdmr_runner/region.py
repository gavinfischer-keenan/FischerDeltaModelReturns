"""Reading region packages (regions/<id>/region.toml). Region packages are data only."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Gauge:
    """A flow gauge on a river reach. Most are USGS's; a CDEC station
    (`source = "cdec"`, with its `parameter` and `duration`) serves where
    USGS has none live, such as a dam's release reported by its operator.
    `usgs_site` names the USGS site at the same place, if any: its position
    on the river and its field measurements (for the channel's shape) are
    used. A station with none gives `lon`, `lat`."""

    reach: str
    site: str
    name: str
    role: str  # "release", "main" or "tributary"
    forecast: dict | None = None  # {source, group, point}
    source: str = "usgs"
    parameter: str = "discharge"
    duration: str | None = None  # CDEC: "E" event, "H" hourly, "D" daily
    usgs_site: str | None = None
    lon: float | None = None
    lat: float | None = None
    release_rule: dict | None = None  # a release's forecast where no schedule is published (fdmr_runner.releases)

    @property
    def place_site(self) -> str | None:
        """The USGS site that places this gauge and gives its channel shape."""
        return self.site if self.source == "usgs" else self.usgs_site


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    timezone: str
    reaches: list[dict]
    gauges: list[Gauge]
    stations: list[dict] = field(default_factory=list)  # tracer stations: {reach, source, id, name, lat, lon, measures}
    handoffs: list[dict] = field(default_factory=list)  # {from, to, at, node?}: where one reach's water enters the next

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
        stations=d.get("stations", []),
        handoffs=d.get("handoffs", []),
    )
