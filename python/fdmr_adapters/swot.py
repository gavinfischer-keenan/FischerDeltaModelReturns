"""NASA/CNES SWOT satellite river observations through PO.DAAC's Hydrocron.
A global adapter.

Each SWORD river reach (about 10 km of river) is seen every few days to
weeks. Stored under source `swot`, site the reach id: width (m) and wse (water
surface elevation, m), from passes Hydrocron flags good or suspect (reach_q
0 or 1); degraded and bad ones are dropped. SWOT resolves rivers wider than
about 100 m; narrower ones are noisy.
"""

from __future__ import annotations

from calendar import timegm
from time import strptime
from urllib.parse import urlencode

from fdmr_store import Store, Value

from .http import HttpJsonClient, JsonClient

SOURCE = "swot"
URL = "https://soto.podaac.earthdatacloud.nasa.gov/hydrocron/v1/timeseries"
FIELDS = ("width", "wse")


def parse(csv_text: str) -> dict[str, list[Value]]:
    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    head = lines[0].split(",")
    col = {n: i for i, n in enumerate(head)}
    out: dict[str, list[Value]] = {f: [] for f in FIELDS}
    for ln in lines[1:]:
        r = ln.split(",")
        if r[col["time_str"]] in ("", "no_data") or int(float(r[col["reach_q"]])) > 1:
            continue
        t = timegm(strptime(r[col["time_str"]][:19], "%Y-%m-%dT%H:%M:%S"))
        for f in FIELDS:
            v = float(r[col[f]])
            if v > -1e9:  # fill: -999999999999
                out[f].append(Value(t, v, None))
    return out


class SwotAdapter:
    def __init__(self, store: Store, client: JsonClient | None = None) -> None:
        self.store = store
        self.client = client or HttpJsonClient()

    def fetch(self, reach_id: str, start_iso: str, end_iso: str) -> int:
        q = urlencode({"feature": "Reach", "feature_id": reach_id, "start_time": start_iso, "end_time": end_iso,
                       "output": "csv", "fields": "reach_id,time_str,wse,width,reach_q"})
        page = self.client.get_json(f"{URL}?{q}")
        data = parse(page["results"]["csv"])
        units = {"width": "m", "wse": "m"}
        return sum(self.store.put_values(SOURCE, reach_id, f, units[f], vs) for f, vs in data.items() if vs)
