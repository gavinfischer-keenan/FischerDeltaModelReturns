"""Reading region packages (regions/<id>/region.toml). Region packages are data only.

`load_region` is the one reader of region.toml (#63): every section the code
uses is a field of `Region`, and a key it does not know is refused, so a
misspelt or a stale setting cannot be silently ignored. A region's other
files (overlays.toml, dsm2.toml, bay.toml, canals.toml, fishing.toml) have
their own modules' readers.

Every region lists its measuring stations the same way, `[[stations]]`:
`source` (the agency: an adapter's name), `site`, `name` and `quantities`
(what it measures); optionally `reach`, `lon` and `lat` (where it is, on a
river reach), `duration` (CDEC's: "H" hourly, "E" event, "D" daily),
`forecasts` (the agency's own forecasts of those quantities) and `depth_cm`
with `depth_for` (the sensor's depth, for the quantities it applies to).
"""

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
    # Where no channel measurements are published: {width_m, slope, chezy or
    # manning_n, source}, a wide channel (fdmr_plugins.river_routing.channel.wide_channel).
    channel: dict | None = None

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
    stations: list[dict] = field(default_factory=list)  # measuring stations (module docstring)
    handoffs: list[dict] = field(default_factory=list)  # {from, to, at, node?}: where one reach's water enters the next
    river_temperature: dict = field(default_factory=dict)  # the rivers' heat model: weather zones, the land from orbit
    river_swot: list[dict] = field(default_factory=list)  # SWOT's river reaches, checked against the rivers' widths
    soil_moisture: dict | None = None  # where rain runs off into the water: areas, OpenET samples
    basemap: dict | None = None  # the map's own basemap extract: bbox, maxzoom
    guard: dict = field(default_factory=dict)  # words engine code may not contain (tools/check_seams.py)

    def gauges_for(self, reach: str) -> list[Gauge]:
        return [g for g in self.gauges if g.reach == reach]

    def local_date(self, t: int) -> str:
        """The region's own date at UTC time t (its `timezone`), YYYY-MM-DD (#67)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return datetime.fromtimestamp(t, ZoneInfo(self.timezone)).strftime("%Y-%m-%d")

    def midnight(self, t: int, days_ago: int = 0) -> int:
        """Midnight, in the region's own time zone, starting the region's day
        `days_ago` days before the one holding UTC time t (#67)."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(self.timezone)
        d = datetime.fromtimestamp(t, tz).date() - timedelta(days=days_ago)
        return int(datetime(d.year, d.month, d.day, tzinfo=tz).timestamp())


SECTIONS = {"id", "name", "timezone", "reaches", "gauges", "stations", "handoffs", "river_temperature",
            "river_swot", "soil_moisture", "basemap", "guard"}
STATION_KEYS = {"source", "site", "name", "quantities", "reach", "lon", "lat", "duration", "forecasts",
                "depth_cm", "depth_for"}


def load_region(path: str | Path) -> Region:
    p = Path(path)
    if p.is_dir():
        p = p / "region.toml"
    d = tomllib.loads(p.read_text(encoding="utf-8"))
    unknown = set(d) - SECTIONS
    if unknown:
        raise ValueError(f"{p}: unknown settings {sorted(unknown)}")
    for s in d.get("stations", []):
        odd = set(s) - STATION_KEYS
        if odd or not {"source", "site", "name", "quantities"} <= set(s):
            raise ValueError(f"{p}: station {s.get('site', s)}: unknown keys {sorted(odd)}" if odd
                             else f"{p}: station {s.get('site', s)} needs source, site, name and quantities")
    return Region(
        id=d["id"],
        name=d["name"],
        timezone=d["timezone"],
        reaches=d.get("reaches", []),
        gauges=[Gauge(**g) for g in d.get("gauges", [])],
        stations=d.get("stations", []),
        handoffs=d.get("handoffs", []),
        river_temperature=d.get("river_temperature", {}),
        river_swot=d.get("river_swot", []),
        soil_moisture=d.get("soil_moisture"),
        basemap=d.get("basemap"),
        guard=d.get("guard", {}),
    )
