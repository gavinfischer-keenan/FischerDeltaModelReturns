"""California DWR's daily reports on CDEC: reservoir storage and snowpack. A
state adapter (California).

- `RES`, "Water Storage": each reservoir's capacity and the historical
  average storage for the date (acre-feet). Stored under source `cdec`, the
  reservoir's site: `reservoir_capacity` and `reservoir_storage_average`
  (m3), at the report's day (00:00 Pacific Standard Time). The storage itself
  comes from the reservoir's own sensor (fdmr_adapters.cdec,
  `reservoir_storage`).
- `PAGE6`, "Snow Water Equivalents": each snow sensor's value as DWR has
  reviewed it ("r": revised; early in the season a raw snow pillow can read
  inches where DWR's reviewed value is nil), beside its April 1 average, by
  basin group. Stored per station (`snow_water_equivalent`,
  `snow_april1_average`, mm) and per basin group (site `snow-<group>`, such as
  `snow-feather-river`): `snow_water_equivalent`, the stations' mean (mm);
  `snow_percent_april1`, their total against their April 1 averages' total
  (%); `snow_stations`, how many reported.
- `DLYSWEQ`: DWR's own summary for its North, Central and South sections
  (site `snow-north` and so on): `snow_stations`, `snow_water_equivalent`
  (mm), `snow_percent_april1` and `snow_percent_normal` (% of normal for the
  date).

References: ref:cdec (references.toml).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from fdmr_store import Store, Value

from . import http

SOURCE = "cdec"
URL = "https://cdec.water.ca.gov/reportapp/javareports?name={name}"
AF = 1_233.48184  # m3
INCH = 25.4  # mm
PST = 8 * 3600


def _get(url: str) -> str:
    return http.get_text(url, timeout=90)


def rows(html: str) -> list[list[str]]:
    """A report's table rows as cell text."""
    out = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"\s+", " ", re.sub("<[^>]+>", "", c)).replace("&nbsp;", " ").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        if cells:
            out.append(cells)
    return out


def number(s: str) -> float | None:
    """'1,234', '0.0 r', '12%', '0.2"' as numbers; '---' and blanks as None."""
    m = re.match(r"^-?[\d,]*\.?\d+", s.strip())
    return float(m.group(0).replace(",", "")) if m else None


def slug(group: str) -> str:
    return "snow-" + re.sub(r"[^a-z0-9]+", "-", group.lower()).strip("-")


def parse_storage(html: str) -> list[dict]:
    """{site, name, capacity_af, average_af} per reservoir, each once."""
    out: dict[str, dict] = {}
    for c in rows(html):
        if len(c) >= 9 and re.fullmatch(r"[A-Z0-9]{3}", c[1]) and number(c[2]) is not None:
            out.setdefault(c[1], {"site": c[1], "name": c[0], "capacity_af": number(c[2]), "average_af": number(c[7])})
    return list(out.values())


def parse_snow(html: str) -> list[dict]:
    """{group, site, name, april1_in, today_in} per snow station."""
    group, out = None, []
    for c in rows(html):
        if len(c) == 2 and c[1] == "":
            group = c[0]
        elif group and len(c) >= 9 and re.fullmatch(r"[A-Z0-9]{3}", c[1]):
            out.append({"group": group, "site": c[1], "name": c[0], "april1_in": number(c[4]), "today_in": number(c[5])})
    return out


def parse_sections(html: str) -> dict[str, dict]:
    """DWR's section summaries: {NORTH: {stations, swe_in, percent_april1, percent_normal}, ...} for today."""
    keys = {"Number of Stations Reporting": "stations", "Average snow water equivalent": "swe_in",
            "Percent of April 1 Reporting": "percent_april1", "Percent of Normal for this date": "percent_normal"}
    out: dict[str, dict] = {}
    section = None
    for c in rows(html):
        if c[0] in ("NORTH", "CENTRAL", "SOUTH") or (len(c) > 1 and c[1] == "STATE"):
            section = "STATE" if len(c) > 1 and c[1] == "STATE" else c[0]
            c = c[1:] if section != "STATE" else c[2:]
        elif c[0] == "" and section:
            c = c[1:]
        else:
            continue
        if len(c) >= 2 and c[0] in keys and number(c[1]) is not None:
            out.setdefault(section, {}).setdefault(keys[c[0]], number(c[1]))
    return out


class CdecReports:
    def __init__(self, store: Store, get: Callable[[str], str] = _get) -> None:
        self.store = store
        self.get = get

    def day(self, now: int) -> int:
        """The report's day: 00:00 Pacific Standard Time, as UTC seconds."""
        return (now - PST) // 86_400 * 86_400 + PST

    def fetch_storage(self, sites: list[str], now: int) -> int:
        t = self.day(now)
        n = 0
        for r in parse_storage(self.get(URL.format(name="RES"))):
            if r["site"] not in sites:
                continue
            n += self.store.put_values(SOURCE, r["site"], "reservoir_capacity", "m3", [Value(t, r["capacity_af"] * AF, None)])
            if r["average_af"] is not None:
                n += self.store.put_values(SOURCE, r["site"], "reservoir_storage_average", "m3", [Value(t, r["average_af"] * AF, None)])
        return n

    def fetch_snow(self, now: int) -> int:
        t = self.day(now)
        n = 0
        groups: dict[str, list[dict]] = {}
        for s in parse_snow(self.get(URL.format(name="PAGE6"))):
            if s["today_in"] is None:
                continue
            n += self.store.put_values(SOURCE, s["site"], "snow_water_equivalent", "mm", [Value(t, s["today_in"] * INCH, None)])
            if s["april1_in"]:
                n += self.store.put_values(SOURCE, s["site"], "snow_april1_average", "mm", [Value(t, s["april1_in"] * INCH, None)])
            groups.setdefault(s["group"], []).append(s)
        for g, ss in groups.items():
            avg = [s for s in ss if s["april1_in"]]
            vals = {"snow_stations": ("count", float(len(ss))), "snow_water_equivalent": ("mm", sum(s["today_in"] for s in ss) / len(ss) * INCH)}
            if avg:
                vals["snow_percent_april1"] = ("%", 100.0 * sum(s["today_in"] for s in avg) / sum(s["april1_in"] for s in avg))
            for p, (unit, v) in vals.items():
                n += self.store.put_values(SOURCE, slug(g), p, unit, [Value(t, v, None)])
        for sec, v in parse_sections(self.get(URL.format(name="DLYSWEQ"))).items():
            units = {"stations": ("snow_stations", "count", 1.0), "swe_in": ("snow_water_equivalent", "mm", INCH),
                     "percent_april1": ("snow_percent_april1", "%", 1.0), "percent_normal": ("snow_percent_normal", "%", 1.0)}
            for k, x in v.items():
                p, unit, f = units[k]
                n += self.store.put_values(SOURCE, slug(sec), p, unit, [Value(t, x * f, None)])
        return n
