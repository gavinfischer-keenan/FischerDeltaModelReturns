"""Rijkswaterstaat's open water data (the Netherlands): the WaterWebservices
API, DDAPI 2.0. A national adapter, for any Dutch region (issue #23).

Every call is a JSON POST, no key. A station is named by its code (such as
"river.station"); a quantity by its Aquo codes: compartment,
grootheid (quantity) and, where needed, hoedanigheid (reference or form). Times
come with a fixed +01:00 offset (Central European winter time, all year); live
values are every 10 minutes, about half an hour behind. Observed values are
ProcesType "meting"; Rijkswaterstaat's own forecasts, "verwachting" (about 31
hours for rivers, 48 for tidal stations), are stored as their own parameter,
`<name>_forecast`. Read 26 Nov 2026 from the API's OpenAPI description and
live requests.

How values are read, and why:
- each station's series can come in several variants, including old ones and
  daily means: only values with no averaging applied (WaardeBewerkingsMethode
  "NVT") are kept, newest series first;
- a water-quality station may measure at several depths
  (Bemonsteringshoogte, cm): one depth is chosen per station (`depth_cm`),
  never mixed;
- quality code (Kwaliteitswaardecode) above 50 marks a value as unusable in
  Rijkswaterstaat's data (DONAR's practice; the API does not document its
  codes): such values are dropped. "00" and "25" are kept, with the code as
  the value's qualifier;
- the values are not sorted in time by the API; they are here;
- live conductivity labelled mS/m reads about 1000 times too low (checked 26
  Nov 2026 against chloride at a sea station), so conductivity is not
  read; chloride is.

References: ref:rws_ddapi (references.toml).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from fdmr_store import Store, Value

from .http import USER_AGENT

BASE = "https://ddapi20-waterwebservices.rijkswaterstaat.nl"
SOURCE = "rws"
MET = timezone(timedelta(hours=1))

# Standard name -> (Aquo metadata, SI unit, factor to SI)
QUANTITIES: dict[str, tuple[dict, str, float]] = {
    "discharge": ({"Compartiment": {"Code": "OW"}, "Grootheid": {"Code": "Q"}}, "m3/s", 1.0),
    "water_level": ({"Compartiment": {"Code": "OW"}, "Grootheid": {"Code": "WATHTE"}, "Hoedanigheid": {"Code": "NAP"}}, "m", 0.01),
    "water_temperature": ({"Compartiment": {"Code": "OW"}, "Grootheid": {"Code": "T"}}, "degC", 1.0),
    "chloride": ({"Compartiment": {"Code": "OW"}, "Grootheid": {"Code": "CONCTTE"}, "Hoedanigheid": {"Code": "Cl"}, "Parameter": {"Code": "Cl"}}, "mg/L", 1.0),
}
BAD_QUALITY = 50  # codes above this are gaps


def post(url: str, body: dict, timeout_s: float = 120.0) -> dict | None:
    """POST JSON; the parsed reply, or None for "204 No Content" (no values)."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        if r.status == 204:
            return None
        raw = r.read()
        return json.loads(raw) if raw else None


def stamp(t: int) -> str:
    return datetime.fromtimestamp(t, MET).strftime("%Y-%m-%dT%H:%M:%S.000+01:00")


def to_epoch(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def parse(reply: dict | None, factor: float, depth_cm: float | None = None) -> list[Value]:
    """The usable values of an OphalenWaarnemingen reply, in time order."""
    if not reply:
        return []
    out: dict[int, Value] = {}
    for w in reply.get("WaarnemingenLijst", []):
        meta = w.get("AquoMetadata", {})
        if (meta.get("WaardeBewerkingsMethode") or {}).get("Code", "NVT") != "NVT":
            continue  # a daily mean or other derived series
        for m in w.get("MetingenLijst", []):
            md = m.get("WaarnemingMetadata", {})
            code = str(md.get("Kwaliteitswaardecode", "00"))
            v = (m.get("Meetwaarde") or {}).get("Waarde_Numeriek")
            if v is None or (code.isdigit() and int(code) > BAD_QUALITY):
                continue
            if depth_cm is not None:
                try:
                    if float(md.get("Bemonsteringshoogte")) != depth_cm:
                        continue
                except (TypeError, ValueError):
                    continue
            t = to_epoch(m["Tijdstip"])
            out[t] = Value(t=t, value=float(v) * factor, qualifier=f"{md.get('Statuswaarde', '')}:{code}")
    return [out[t] for t in sorted(out)]


class RwsAdapter:
    def __init__(self, store: Store, post=post) -> None:
        self.store, self.post = store, post

    def fetch(self, site: str, quantity: str, start: int, end: int, forecast: bool = False, depth_cm: float | None = None) -> int:
        """One station's quantity over [start, end] into the store (observed,
        or Rijkswaterstaat's forecast as `<quantity>_forecast`). Returns how
        many values were stored."""
        meta, unit, factor = QUANTITIES[quantity]
        body = {"Locatie": {"Code": site},
                "AquoPlusWaarnemingMetadata": {"AquoMetadata": {**meta, "ProcesType": "verwachting" if forecast else "meting"}},
                "Periode": {"Begindatumtijd": stamp(start), "Einddatumtijd": stamp(end)}}
        values = parse(self.post(f"{BASE}/ONLINEWAARNEMINGENSERVICES/OphalenWaarnemingen", body), factor, depth_cm)
        return self.store.put_values(SOURCE, site, f"{quantity}_forecast" if forecast else quantity, unit, values)
