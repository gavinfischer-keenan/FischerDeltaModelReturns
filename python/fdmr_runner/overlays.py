"""What is drawn over the water (specification 3.5, 5.3), for one published
cycle: `overlays.json` beside the cycle's index.

- Isohalines (such as X2): where a tidally averaged field crosses a threshold
  along an axis, hourly over the reach's whole time axis (fdmr_runner.isohaline),
  with the same position worked out each day from the stations' measurements.
- Stations: each station's latest reading of everything it measures, and,
  where it stands on a network reach that computes the same thing, the
  model's value at the same moment beside it.
- Structures: dams, pumping plants and gates, with their latest readings.
- Landmarks: named places on the water, each tied to the nearest piece of
  the model (a reach and an element), for arrival times.
- Satellite checks (`satellite_temperature`): the water-surface temperature a
  satellite saw against the stations' readings and against every model's
  temperature at the moment of each pass.

The region's `overlays.toml` says what each one is. Nothing here draws.
"""

from __future__ import annotations

import calendar
import json
import math
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fdmr_adapters import cdec
from fdmr_store import Store

from . import delta, isohaline

LATEST_S = 26 * 3600  # a reading older than this is not "latest"
# Days are the source's own: its offset from UTC, s (a source not listed keeps UTC days).
DAY_OFFSET = {cdec.SOURCE: cdec.PST_TO_UTC}

# What a station overlay shows, in this order, and the network field (if any)
# that the model computes for it.
SHOWN = {
    "water_temperature": "temperature",
    "salinity": "salinity",
    "specific_conductance": "ec",
    "specific_conductance_bottom": None,
    "dissolved_oxygen": None,
    "turbidity_ntu": None,
    "turbidity_fnu": None,
    "discharge": None,
    "air_temperature": None,
    "wind_speed": None,
}
CHECK_DAYS = 14  # each station's readings against the model over this many days
MODEL_NEAR_KM = 1.0  # a station further than this from every channel gets no model value


def load(region_dir: Path) -> dict | None:
    p = region_dir / "overlays.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


class NetworkValues:
    """A reach's water-quality fields per element over its days, to look up:
    a network's channels, a river's elements, or (`points`) a mesh's nodes."""

    def __init__(self, cdir: Path, entry: dict, fields: list[str]) -> None:
        """With no `fields`, the geometry only: for finding places, not values."""
        import fdmr_py

        self.reach = entry["id"]
        self.geo = json.loads((cdir / "geometry" / f"{entry['id']}.json").read_text(encoding="utf-8"))
        if "lines" not in self.geo:  # a mesh: each node is a point
            self.geo["lines"] = [[[x, y]] for x, y in zip(self.geo["lon"], self.geo["lat"])]
        data: dict[str, list[np.ndarray]] = {f: [] for f in fields}
        self.t0 = self.step = None
        for day in entry["time"]["days"] if fields else []:
            path = cdir / entry["id"] / f"{day}.quality.fdf"
            if not path.exists():
                continue
            _, _, n, _, steps, t0, step, flds = fdmr_py.read_field_file(str(path))
            if self.t0 is None:
                self.t0, self.step = t0, step
            got = {name: v for name, _, v in flds}
            for f in fields:
                data[f].append(np.asarray(got[f], dtype=float).reshape(steps, n) if f in got else np.full((steps, n), np.nan))
        self.data = {f: np.concatenate(v) for f, v in data.items() if v}

    skip: frozenset = frozenset()  # channels another reach covers (a mesh's), not this one's to answer for

    def channel_near(self, lon: float, lat: float) -> tuple[int, float]:
        """The channel nearest a point, and how far it is, km."""
        c = math.cos(math.radians(lat))
        best = (math.inf, -1)
        for k, line in enumerate(self.geo["lines"]):
            if k in self.skip:
                continue
            a = np.asarray(line)
            d = float(np.min(((a[:, 0] - lon) * c) ** 2 + (a[:, 1] - lat) ** 2))
            best = min(best, (d, k))
        return best[1], math.sqrt(best[0]) * isohaline.KM_PER_DEG_LAT

    def at(self, field: str, channel: int, t: int) -> float | None:
        """The field in a channel at time t, linear between steps; None outside."""
        if field not in self.data or self.t0 is None:
            return None
        pos = (t - self.t0) / self.step
        v = self.data[field][:, channel]
        if pos < 0 or pos > len(v) - 1:
            return None
        i = int(pos)
        f = pos - i
        x = v[i] if f == 0 else v[i] * (1 - f) + v[min(i + 1, len(v) - 1)] * f
        return None if not np.isfinite(x) else float(x)


def measured_crossings(store: Store, source: str, sites: dict[str, float], parameter: str, threshold: float,
                       start: int, end: int) -> list[dict]:
    """Each whole day's (in the source's own time) crossing of the
    threshold from the stations' daily means, walking up the axis: the method
    of DWR's CX2, over whichever stations are given (`sites`: site -> km)."""
    order = sorted(sites, key=sites.get)
    off = DAY_OFFSET.get(source, 0)
    out = []
    for d in range((start - off) // 86_400 * 86_400 + 86_400 + off, end - 86_400 + 1, 86_400):
        means = []
        for s in order:
            vs = [v.value for v in store.values(source, s, parameter, d, d + 86_400) if v.value is not None]
            means.append(float(np.mean(vs)) if len(vs) >= 18 else math.nan)  # three quarters of the hours
        km, why = isohaline.crossing(np.array([sites[s] for s in order]), np.array(means), threshold)
        out.append({"day": datetime.fromtimestamp(d - off, timezone.utc).strftime("%Y-%m-%d"),
                    "km": None if km is None else round(km, 2), **({"outside": why} if why else {})})
    return out


def isohalines(cfg: dict, cdir: Path, index: dict, store: Store, now: int) -> list[dict]:
    out = []
    reaches = {r["id"]: r for r in index["reaches"]}
    for h in cfg.get("isohalines", []):
        entry = reaches.get(h["reach"])
        if entry is None:
            continue
        net = json.loads((cdir / "geometry" / f"{h['reach']}.json").read_text(encoding="utf-8"))
        axis = isohaline.Axis(net, [dict(a) for a in h["anchors"]])
        days = [cdir / h["reach"] / f"{d}.quality.fdf" for d in entry["time"]["days"]]
        s = isohaline.series(axis, [p for p in days if p.exists()], h["field"], h["threshold"])
        # The axis, thinned to every ~200 m, for drawing the line across it.
        keep = [0]
        for k in range(1, len(axis.lonlat)):
            if axis.line_km[k] - axis.line_km[keep[-1]] >= 0.2:
                keep.append(k)
        line = [[round(axis.lonlat[k][0], 5), round(axis.lonlat[k][1], 5), round(float(axis.line_km[k]), 2)] for k in keep]
        entry_out = {
            "id": h["id"], "reach": h["reach"], "field": h["field"], "threshold": h["threshold"],
            "anchors": [{k: a[k] for k in ("site", "name", "km")} for a in axis.anchors],
            "axis": line, **s,
        }
        chk = h.get("check")
        if chk:
            sites = {a["site"]: float(a["km"]) for a in axis.anchors}
            for site in chk.get("also", []):
                st = next((x for x in cfg.get("stations", []) if x["site"] == site), None)
                if st:
                    sites[site] = round(axis_km(axis, st["lon"], st["lat"]), 2)
            start = int(s["t0"]) if s["t0"] is not None else now - 14 * 86_400
            measured = measured_crossings(store, chk["source"], sites, chk["parameter"], h["threshold"], start, min(now, start + len(s["km"]) * s["step_s"]))
            # The model's daily mean over the same days, beside each.
            km = np.array([np.nan if v is None else v for v in s["km"]])
            tt = s["t0"] + s["step_s"] * np.arange(len(km))
            for m in measured:
                d0 = calendar.timegm(datetime.strptime(m["day"], "%Y-%m-%d").timetuple()) + DAY_OFFSET.get(chk["source"], 0)
                sel = (tt >= d0) & (tt < d0 + 86_400)
                ok = sel & np.isfinite(km)
                m["model_km"] = round(float(km[ok].mean()), 2) if ok.sum() >= 18 else None
            pairs = [(m["model_km"], m["km"]) for m in measured if m.get("model_km") is not None and m.get("km") is not None]
            entry_out["check"] = {
                "source": chk["source"], "parameter": chk["parameter"], "sites": sites, "days": measured,
                "mean_difference_km": round(float(np.mean([a - b for a, b in pairs])), 2) if pairs else None,
                "largest_difference_km": round(float(np.max([abs(a - b) for a, b in pairs])), 2) if pairs else None,
            }
        out.append(entry_out)
    return out


def axis_km(axis: isohaline.Axis, lon: float, lat: float) -> float:
    """The km along an axis of the point on it nearest (lon, lat)."""
    lat0 = sum(a["lat"] for a in axis.anchors) / len(axis.anchors)
    p = np.array(isohaline._xy(lon, lat, lat0))
    xy = np.array([isohaline._xy(lo, la, lat0) for lo, la in axis.lonlat])
    return float(axis.line_km[int(np.argmin(np.hypot(*(xy - p).T)))])


def model_lookups(cdir: Path, index: dict, load: bool = True) -> list[NetworkValues]:
    """Every reach that computes water quality, to look its values up (or,
    with `load` False, only to find places on it)."""
    lookups = []
    for r in index["reaches"]:
        fields = sorted({f for f in SHOWN.values() if f and f in r.get("quality_fields", [])})
        if fields:  # a reach without its own time block (a river) runs on the cycle's
            lk = NetworkValues(cdir, {**r, "time": r.get("time", index["time"])}, fields if load else [])
            lk.skip = frozenset(c for h in index.get("handoffs", []) if h.get("from") == r["id"] for c in h.get("owned", []))
            lookups.append(lk)
    return lookups


def stations(cfg: dict, cdir: Path, index: dict, store: Store, now: int, lookups: list[NetworkValues] | None = None) -> tuple[list[dict], dict]:
    """Each station's latest readings, with the value of whichever model covers
    it beside (a network's channel, a river's element or a mesh's node within
    MODEL_NEAR_KM), and `checks`: its readings over the last CHECK_DAYS days
    against that model's at the same moments (mean and RMS of measured less
    modelled)."""
    lookups = model_lookups(cdir, index) if lookups is None else lookups
    units: dict[str, str] = {}
    out = []
    for st in cfg.get("stations", []):
        readings = {}
        near = None
        for lk in lookups:
            c, d = lk.channel_near(st["lon"], st["lat"])
            if d <= MODEL_NEAR_KM and (near is None or d < near[2]):
                near = (lk, c, d)
        for p, field in SHOWN.items():
            vs = [v for v in store.values(st.get("source", "cdec"), st["site"], p, now - LATEST_S, now + 1) if v.value is not None]
            if not vs:
                continue
            v = vs[-1]
            units[p] = store.unit(st.get("source", "cdec"), st["site"], p) or ""
            r = {"v": round(v.value, 3), "t": v.t}
            if field and near:
                m = near[0].at(field, near[1], v.t)
                if m is not None:
                    r["model"] = round(m, 3)
            readings[p] = r
        checks = {}
        for p, field in SHOWN.items():
            if not (field and near):
                continue
            pairs = []
            for v in store.values(st.get("source", "cdec"), st["site"], p, now - CHECK_DAYS * 86_400, now + 1):
                m = near[0].at(field, near[1], v.t) if v.value is not None else None
                if m is not None:
                    pairs.append(v.value - m)
            if len(pairs) >= 24:
                d = np.array(pairs)
                checks[p] = {"readings": len(d), "mean_misfit": round(float(d.mean()), 3),
                             "rms_misfit": round(float(np.sqrt((d ** 2).mean())), 3)}
        out.append({"site": st["site"], "name": st["name"], "lon": st["lon"], "lat": st["lat"],
                    "source": st.get("source", "cdec"), "readings": readings,
                    **({"model_reach": near[0].reach, "model_km": round(near[2], 2)} if near else {}),
                    **({"checks": checks} if checks else {})})
    return out, units


def _summary(d: list[float]) -> dict:
    a = np.array(d)
    return {"pairs": int(a.size), "mean_difference": round(float(a.mean()), 2), "rms_difference": round(float(np.sqrt((a ** 2).mean())), 2)}


def satellite_temperature(cfg: dict, cache: Path, lookups: list[NetworkValues], store: Store, now: int, tiles=None) -> dict | None:
    """The water-surface temperature seen from orbit (overlays.toml
    [satellite_temperature]; fdmr_adapters.ecostress) over the last `days`:

    - against the stations: at each pass, each station's reading nearest in
      time (within `reading_s`) against the satellite at the nearest pixel
      within two pixels whose neighbours are all water, so the satellite's own
      error is on record;
    - against the models: at each pass inside a reach's time axis, each
      channel, element or node whose points fall on such pixels, the mean of
      those pixels against the model's temperature then, per reach.

    Differences are the satellite less the station or the model. The
    satellite sees the water's skin; the stations and the models, the water
    below it."""
    sc = cfg.get("satellite_temperature")
    if not sc or (tiles is None and not cache.exists()):
        return None
    from fdmr_adapters import ecostress

    tiles = tiles if tiles is not None else [ecostress.Tile(p) for p in sorted(cache.glob("*.npz"))]
    tiles = [t for t in tiles if now - sc.get("days", 14) * 86_400 <= t.t <= now]
    if not tiles:
        return None
    reading_s = sc.get("reading_s", 1800)
    per_station: dict[str, list[float]] = {}
    for st in cfg.get("stations", []):
        src = st.get("source", "cdec")
        for tl in tiles:
            vs = [v for v in store.values(src, st["site"], "water_temperature", tl.t - reading_s, tl.t + reading_s) if v.value is not None]
            if not vs:
                continue
            s = tl.near(st["lon"], st["lat"])
            if s is not None:
                v = min(vs, key=lambda v: abs(v.t - tl.t))
                per_station.setdefault(st["site"], []).append(s - v.value)
    per_reach: dict[str, dict] = {}
    for lk in lookups:
        seen: dict[tuple[int, int], float] = {}
        for tl in tiles:
            for k, line in enumerate(lk.geo["lines"]):
                if k in lk.skip or (tl.t, k) in seen:
                    continue
                m = lk.at("temperature", k, tl.t)
                if m is None:
                    continue
                a = np.asarray(line, dtype=float)
                s = tl.pure(a[:, 0], a[:, 1])
                if np.isfinite(s).any():
                    seen[(tl.t, k)] = float(np.nanmean(s)) - m
        if seen:
            per_reach[lk.reach] = {"places": len({k for _, k in seen}), **_summary(list(seen.values()))}
    out = {"source": sc.get("source", "ECOSTRESS"), "passes": len({t.t for t in tiles}),
           "newest": datetime.fromtimestamp(max(t.t for t in tiles), timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
    if per_station:
        out["stations"] = {"stations": len(per_station), **_summary([x for d in per_station.values() for x in d]),
                           "per_station": [{"site": s, **_summary(d)} for s, d in sorted(per_station.items())]}
    if per_reach:
        out["models"] = per_reach
    return out


def gate_state(store: Store, source: str, site: str, rule: dict, now: int) -> dict | None:
    """Open or closed now, from the flow through the gate over the last three
    days, by the rule the Delta model uses (fdmr_runner.delta.gate_states, with
    the gate's own settings from dsm2.toml)."""
    vals = [(v.t, v.value) for v in store.values(source, site, rule["parameter"], now - 3 * 86_400, now + 1) if v.value is not None]
    states = delta.gate_states(vals, rule["open_above"], int(rule.get("max_gap_h", 8) * 3600))
    if not states:
        return None
    # The first entry is where the three days began; any later one is a change.
    return {"open": states[-1][1], "t": vals[-1][0], **({"changed_at": states[-1][0]} if len(states) > 1 else {})}


def structures(cfg: dict, store: Store, now: int, units: dict, gates: list[dict] | None = None) -> list[dict]:
    """Each dam, pumping plant and gate with its latest readings, and a gate's
    state. A gate's `state = { live_gate = <site> }` takes its rule from the
    Delta model's own (`gates`, dsm2.toml [[live_gate]]), so the map and the
    model never disagree; without it, no state is claimed."""
    out = []
    for s in cfg.get("structures", []):
        src = s.get("source", cdec.SOURCE)
        # A daily value is posted the day after; allow it three days.
        max_age = LATEST_S if s.get("duration", "H") != "D" else 3 * 86_400
        readings = {}
        for p in s.get("show", []):
            age = 3 * 86_400 if p in s.get("daily", []) else max_age
            vs = [v for v in store.values(src, s["site"], p, now - age, now + 1) if v.value is not None]
            if vs:
                units[p] = store.unit(src, s["site"], p) or ""
                readings[p] = {"v": round(vs[-1].value, 3), "t": vs[-1].t}
        entry = {k: s[k] for k in ("id", "kind", "name", "site", "lon", "lat")}
        entry["readings"] = readings
        entry["daily"] = s.get("duration", "H") == "D"
        if "state" in s:
            rule = next((g for g in gates or [] if g["site"] == s["state"]["live_gate"]), None)
            st = gate_state(store, src, s["site"], rule, now) if rule else None
            if st:
                entry["state"] = st
        if "model_note" in s:
            entry["model_note"] = s["model_note"]
        if s.get("storage_report"):
            st = storage(store, src, s["site"], readings.get("reservoir_storage", {}).get("v"), now)
            if st:
                entry["storage"] = st
        if s.get("snow"):
            entry["snow"] = [g for g in (snowpack(store, src, grp, now) for grp in s["snow"]) if g]
        out.append(entry)
    return out


def _latest(store: Store, src: str, site: str, p: str, t0: int, t1: int) -> float | None:
    vs = [v for v in store.values(src, site, p, t0, t1) if v.value is not None]
    return vs[-1].value if vs else None


def storage(store: Store, src: str, site: str, held: float | None, now: int) -> dict | None:
    """A reservoir's storage against its capacity and the historical average
    for the date (DWR's daily reservoir report, fdmr_adapters.cdec_reports)."""
    cap = _latest(store, src, site, "reservoir_capacity", now - 3 * 86_400, now + 1)
    avg = _latest(store, src, site, "reservoir_storage_average", now - 3 * 86_400, now + 1)
    if cap is None:
        return None
    out = {"capacity_m3": round(cap), **({"average_m3": round(avg)} if avg else {})}
    if held is not None:
        out["percent_full"] = round(100 * held / cap, 1)
        if avg:
            out["percent_of_average"] = round(100 * held / avg, 1)
    return out


def snowpack(store: Store, src: str, group: str, now: int) -> dict | None:
    """A snow basin group's latest summary (DWR's reviewed values,
    fdmr_adapters.cdec_reports) and its change over the last week."""
    from fdmr_adapters.cdec_reports import slug

    site = slug(group)
    swe = _latest(store, src, site, "snow_water_equivalent", now - 3 * 86_400, now + 1)
    if swe is None:
        return None
    week = _latest(store, src, site, "snow_water_equivalent", now - 10 * 86_400, now - 7 * 86_400 + 1)
    pct = _latest(store, src, site, "snow_percent_april1", now - 3 * 86_400, now + 1)
    n = _latest(store, src, site, "snow_stations", now - 3 * 86_400, now + 1)
    return {"group": group, "site": site, "stations": int(n or 0), "swe_mm": round(swe, 1),
            **({"percent_april1": round(pct, 1)} if pct is not None else {}),
            **({"change_7d_mm": round(swe - week, 1)} if week is not None else {})}


def _segment_km(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    """Distance from p to the segment a-b, all in km on a local plane."""
    d = b - a
    f = 0.0 if not d.any() else float(np.clip(np.dot(p - a, d) / np.dot(d, d), 0.0, 1.0))
    return float(np.hypot(*(p - (a + f * d))))


def nearest_element(geo: dict, lon: float, lat: float) -> tuple[int, float]:
    """The element of a reach's geometry nearest a point, and how far, km: the
    nearest line for a 1D reach (lines per element), the nearest node for a 2D
    mesh (lon/lat per node)."""
    def xy(lo, la):
        return np.array(isohaline._xy(lo, la, lat))
    p = xy(lon, lat)
    if "lines" in geo:
        best = (math.inf, -1)
        for k, line in enumerate(geo["lines"]):
            pts = [xy(lo, la) for lo, la in line]
            d = min(_segment_km(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1)) if len(pts) > 1 else float(np.hypot(*(p - pts[0])))
            best = min(best, (d, k))
        return best[1], best[0]
    lo, la = np.asarray(geo["lon"]), np.asarray(geo["lat"])
    c = math.cos(math.radians(lat))
    d = np.hypot((lo - lon) * c, la - lat) * isohaline.KM_PER_DEG_LAT
    k = int(np.argmin(d))
    return k, float(d[k])


def landmarks(cfg: dict, cdir: Path, index: dict) -> list[dict]:
    """Each landmark where it stands, and the nearest element of any reach
    within MODEL_NEAR_KM (`model`: reach, element index, km away); one with
    none is drawn but has no model place."""
    named = {s["site"]: s for s in cfg.get("stations", [])} | {s["id"]: s for s in cfg.get("structures", [])}
    geos = {}
    for r in index["reaches"]:
        p = cdir / "geometry" / f"{r['id']}.json"
        if p.exists():
            geos[r["id"]] = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for lm in cfg.get("landmarks", []):
        src = named[lm["at"]] if "at" in lm else lm
        lon, lat = float(src["lon"]), float(src["lat"])
        entry = {"id": lm["id"], "name": lm["name"], "lon": lon, "lat": lat}
        best = None
        for rid, geo in geos.items():
            k, d = nearest_element(geo, lon, lat)
            if d <= MODEL_NEAR_KM and (best is None or d < best[2]):
                best = (rid, k, d)
        if best:
            entry["model"] = {"reach": best[0], "element": best[1], "km": round(best[2], 3)}
        out.append(entry)
    return out


def build(region_dir: Path, cdir: Path, index: dict, store: Store, now: int) -> dict | None:
    cfg = load(region_dir)
    if cfg is None:
        return None
    lookups = model_lookups(cdir, index)
    st, units = stations(cfg, cdir, index, store, now, lookups)
    sat = None
    if cfg.get("satellite_temperature") and store.path.name != ":memory:":
        try:  # a check; the overlays are published whatever it says
            sat = satellite_temperature(cfg, store.path.parent / cfg["satellite_temperature"]["cache"], lookups, store, now)
        except Exception as e:
            print(f"satellite temperature check FAILED {e}", flush=True)
    return {"generated": now, "isohalines": isohalines(cfg, cdir, index, store, now), "stations": st,
            **({"satellite_temperature": sat} if sat else {}),
            "structures": structures(cfg, store, now, units, (delta.load(region_dir) or {}).get("live_gate", [])),
            "landmarks": landmarks(cfg, cdir, index), "units": units}


def fetch_groups(cfg: dict) -> list[tuple[str, list[str], list[str]]]:
    """What to ask CDEC for the overlays: (duration, sites, parameters) groups,
    one request each, stations and structures alike."""
    groups: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for s in cfg.get("stations", []):
        if s.get("source", cdec.SOURCE) == cdec.SOURCE:
            groups.setdefault((s.get("duration", "H"), tuple(p for p in SHOWN if p in cdec.SENSORS)), []).append(s["site"])
    for s in cfg.get("structures", []):
        if s.get("source", cdec.SOURCE) == cdec.SOURCE:
            daily = set(s.get("daily", []))  # series this one reports daily only
            for dur, ps in ((s.get("duration", "H"), tuple(p for p in s.get("show", []) if p not in daily)),
                            ("D", tuple(p for p in s.get("show", []) if p in daily))):
                if ps:
                    groups.setdefault((dur, ps), []).append(s["site"])
    return [(d, sites, list(ps)) for (d, ps), sites in sorted(groups.items())]
