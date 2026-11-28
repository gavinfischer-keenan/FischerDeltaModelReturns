"""Run DSM2 for the Delta on live inputs, through the forecast (specification 7.2, 8.2).

    python -m fdmr_runner.delta REGION_DIR STORE_FILE WORK_DIR [--days 28] [--ahead 14] [--as-of 2026-11-22T00:00]

Reads REGION_DIR/dsm2.toml and builds each live input from the store on a
15-minute grid up to now, then carries it on:

- a tide (forecast source noaa-tides): NOAA's predictions, fitted to the
  gauge's last week for timing, size and level;
- a river (forecast source cnrfc): the River Forecast Center's forecast,
  joined to the measured flow over the first day, as the river model does;
- anything else, such as pumping: held at its last value.

Tidal averaging, where an input asks for it, comes after, over the joined series. Gates are held
in their last state. The run covers `--days` before today's midnight (Pacific
Standard Time) and `--ahead` after it.

`--as-of` pretends it is an earlier time: nothing measured after it is used
(though a value measured before it is used as revised since: the store keeps
the latest version), and a river forecast is the River Forecast Center's
issue that was in force then, where it was archived, else held: the rivers'
rule (forecast.cnrfc_forecast, #74). DSM2's forecast is then checked against
what the stations measured.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from fdmr_adapters import cnrfc, ndbc, noaa_tides
from fdmr_adapters.noaa_tides import PARAMETER as TIDE
from fdmr_plugins.dsm2 import dss, gates, output, study
from fdmr_plugins.river_routing.calibration import running_mean
from fdmr_plugins.river_routing.model import filled
from fdmr_store import Store

from .forecast import extend, fit_tide, on_grid, repeat_last_day, shifted
from . import DATA, writer

STEP = 900  # s: the published time step, 15 minutes (docs/format.md)
DAY = 86_400
TO_DSS = {"CFS": 1 / 0.3048**3, "FEET": 1 / 0.3048, "UMHOS/CM": 1.0}  # from the store's units
DSM2_ROOT = DATA / "dsm2"  # DWR's DSM2, built by runner/dsm2/build_dsm2.sh
BLEND = 96  # forecasts join the measured series over a day


def load(region_dir: Path) -> dict | None:
    p = region_dir / "dsm2.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def feeds(region_dir: Path) -> list[dict]:
    """Every series the Delta's DSM2 run reads or is checked against, for fetching."""
    cfg = load(region_dir) or {}
    return [
        *cfg.get("live", []), *cfg.get("live_gate", []), *cfg.get("checks", []), *cfg.get("ec_checks", []), *cfg.get("net_checks", []),
        *cfg.get("temperature", {}).get("water", []), *cfg.get("temp_checks", []),
        *cfg.get("sediment", {}).get("water", []), *cfg.get("sediment_checks", []),
        *[{**f, "parameter": f["mean_with"]} for f in cfg.get("sediment", {}).get("water", []) if f.get("mean_with")],
    ]


def forecasts(region_dir: Path) -> list[dict]:
    """The forecast of every live input that has one, for fetching."""
    return [f["forecast"] for f in (load(region_dir) or {}).get("live", []) if "forecast" in f]


def grid(store: Store, f: dict, t0: int, n: int, cutoff: int | None = None) -> list[float | None]:
    """A series on the 15-minute grid from t0, as measured up to `cutoff`."""
    end = t0 + (n + 1) * STEP if cutoff is None else min(cutoff + 1, t0 + (n + 1) * STEP)
    vals = [v for v in store.values(f["source"], f["site"], f["parameter"], t0 - 3 * DAY, end) if v.value is not None]
    g0 = t0 - 3 * DAY
    m = n + 3 * 96
    if f.get("duration") == "D":
        # A daily figure holds through its day, as in DWR's own inputs, unless
        # the input gives its day's shape (`daily_shape`, see day_weights).
        weights = day_weights(f.get("daily_shape"))
        full: list[float | None] = [None] * m
        for v in vals:
            first = max(0, (v.t - g0 + STEP - 1) // STEP)
            last = (min(v.t + DAY, end) - g0 + STEP - 1) // STEP
            for i in range(first, min(m, last)):
                hour = ((g0 + i * STEP - dss.PST) % DAY) // 3600  # Pacific Standard Time
                full[i] = v.value * weights[hour]
    else:
        # Others are bridged across gaps of up to 6 hours.
        full = on_grid([(v.t, v.value) for v in vals], g0, STEP, m, max_gap_s=6 * 3600)
    return full[3 * 96 :]


def day_weights(shape: list[float] | None) -> list[float]:
    """How a daily figure is spread over its day: 24 hourly weights (Pacific
    Standard Time, from midnight), scaled to average 1 so the day's total is
    kept. None is flat. A shape must come with its source in the region's
    config: an operator's schedule, or a record showing it (such as a dam
    that releases only by day). None of this region's inputs has one."""
    if shape is None:
        return [1.0] * 24
    if len(shape) != 24 or min(shape) < 0 or sum(shape) <= 0:
        raise ValueError("daily_shape needs 24 hourly weights, none negative")
    mean = sum(shape) / 24
    return [w / mean for w in shape]


def carried_on(store: Store, f: dict, t0: int, n: int, now: int, use_river_forecasts: bool,
               hindcast: bool = False) -> tuple[list[float], str]:
    """A live input from t0 for n steps: measured up to now, then its forecast or
    held. Returns the series (SI units) and how it was carried on."""
    g = grid(store, f, t0, n, cutoff=now)
    known = [i for i, v in enumerate(g) if v is not None and i <= (now - t0) // STEP]
    if not known:
        raise ValueError(f"no data for {f['source']} {f['site']} {f['parameter']}")
    i_now = known[-1]
    fc = f.get("forecast")
    how = "held"
    if fc and fc["source"] == noaa_tides.SOURCE:
        pv = store.values(noaa_tides.SOURCE, fc["station"], TIDE, t0 - DAY, t0 + (n + 1) * STEP)
        pred = on_grid([(v.t, v.value) for v in pv], t0, STEP, n)
        week = max(0, i_now - 7 * 96)
        fit = fit_tide(g[week : i_now + 1], pred[week : i_now + 1], STEP)
        if fit is None:
            raise ValueError(f"cannot fit tide predictions {fc['station']} to {f['site']}")
        shift, scale, offset = fit
        tide = [None if v is None else scale * v + offset for v in shifted(pred, shift)]
        g, _ = extend(g, tide, i_now, n, BLEND)
        how = f"tide {fc['station']}: {shift * STEP / 60:+.0f} min, x{scale:.3f}, {offset:+.3f} m"
    elif fc and fc["source"] == cnrfc.SOURCE and use_river_forecasts:
        # Live, the latest; a hindcast, the issue in force then if kept: the rivers' rule (#74).
        from .forecast import cnrfc_forecast

        fcg = cnrfc_forecast(store, fc, t0, STEP, n, now, t0 + n * STEP, hindcast)
        if fcg is not None:
            g, last = extend(g, fcg, i_now, n, BLEND)
            how = f"{'archived ' if hindcast else ''}forecast {fc['point']} to {dss.label(t0 + last * STEP)[0] if last else 'now'}"
    if how == "held" and f.get("hold") == "mean_25h":
        # A tidal signal held at one moment would freeze half a tide: hold its
        # mean over the last 25 hours instead.
        last = [v for v in g[max(0, i_now - 99) : i_now + 1] if v is not None]
        g = g[: i_now + 1] + [sum(last) / len(last)] * (n - i_now - 1)
        how = "held at its 25 h mean"
    elif how == "held":
        g = g[: i_now + 1] + [None] * (n - i_now - 1)
    if f.get("average_h"):
        g = running_mean(g, int(f["average_h"] * 3600 / STEP))
    return filled(g), how


def ssc_series(store: Store, f: dict, t0: int, n: int, now: int) -> tuple[list[float], str]:
    """A sediment inflow's SSC (mg/L) from t0 for n steps (dsm2.toml
    [[sediment.water]]): the gauge's SSC, or the mean of two of its sensors
    (`mean_with`), or its turbidity through the region's relation, carried on
    as any live input is."""
    from fdmr_plugins.dsm2.sediment import ssc_from_turbidity

    s, how = carried_on(store, f, t0, n, now, use_river_forecasts=False)
    if f.get("mean_with"):
        s2, _ = carried_on(store, {**f, "parameter": f["mean_with"]}, t0, n, now, use_river_forecasts=False)
        s = [(a + b) / 2 for a, b in zip(s, s2)]
        how += f"; the mean of {f['parameter']} and {f['mean_with']}"
    r = f.get("relation")
    if r:
        s = [ssc_from_turbidity(v, r["a"], r["b"]) for v in s]
        how += f"; SSC = 10^{r['b']} x turbidity^{r['a']} ({r['area']} relation, Achete et al. 2015, ref:achete2015)"
    return s, how


WEATHER = ("air_temperature", "dew_point", "wind_speed", "cloud_cover", "air_pressure")


def weather(store: Store, tcfg: dict, t0: int, n: int, now: int, use_forecast: bool) -> tuple[dict[str, list[float]], dict[str, str]]:
    """The Delta's weather on the 15-minute grid from t0: the mean of the
    region's weather stations up to now (NOAA's archive and the Weather
    Service's recent observations of each), then the Weather Service's forecast,
    joined over a day, then its last day over again. Air pressure has no
    forecast: held at its 25 h mean. Returns each parameter's series and how
    it was carried on."""
    from fdmr_adapters import noaa_weather as nw

    fc = tcfg.get("forecast") if use_forecast else None
    i_cut = (now - t0) // STEP
    out, how = {}, {}
    for p in WEATHER:
        per = []
        for s in tcfg["stations"]:
            pts = {}
            for src, key in ((nw.GHCNH, "ghcnh"), (nw.NWS, "nws"), (ndbc.SOURCE, ndbc.SOURCE)):  # an NDBC weather station too
                if key in s:
                    pts.update({v.t: v.value for v in store.values(src, s[key], p, t0 - DAY, now + 1) if v.value is not None})
            per.append(on_grid(sorted(pts.items()), t0, STEP, i_cut + 1, max_gap_s=6 * 3600))
        g: list[float | None] = []
        for i in range(i_cut + 1):
            vs = [s[i] for s in per if s[i] is not None]
            g.append(sum(vs) / len(vs) if vs else None)
        known = [i for i, v in enumerate(g) if v is not None]
        if not known:
            raise ValueError(f"no weather data for {p}")
        i_now = known[-1]
        g += [None] * (n - len(g))
        last = None
        if fc:
            fv = store.values(nw.NWS_FORECAST, f"{fc['office']}/{fc['x']},{fc['y']}", p, t0, t0 + (n + 1) * STEP)
            if fv:
                g, last = extend(g, on_grid([(v.t, v.value) for v in fv], t0, STEP, n), i_now, n, BLEND, floor=None)
        if last is not None:
            g = repeat_last_day(g, last, 96)
            how[p] = f"forecast to {dss.label(t0 + last * STEP)[0]}, then its last day again"
        else:
            day = [v for v in g[max(0, i_now - 99) : i_now + 1] if v is not None]
            g = g[: i_now + 1] + [sum(day) / len(day)] * (n - i_now - 1)
            how[p] = "held at its 25 h mean"
        out[p] = filled(g)
    return out, how


def resample(series: list[float], step: int) -> list[float]:
    """A 15-minute series from t0 at a DSS path's interval: every 4th value for
    hourly, and for daily the mean of each day, stamped at its end (DSS's way),
    the first stamp taking the first day's mean."""
    if step == STEP:
        return series
    if step == 3600:
        return series[::4]
    per = step // STEP
    days = [sum(series[k * per : (k + 1) * per]) / len(series[k * per : (k + 1) * per]) for k in range(len(series) // per)]
    return [days[0], *days] if days else []


def gate_setting(store: Store, g: dict, t0: int, now: int) -> study.LiveSetting:
    """A gate's position from its measured flow up to now, held after."""
    vals = [(v.t, v.value) for v in store.values(g["source"], g["site"], g["parameter"], t0 - 2 * DAY, now + 1) if v.value is not None]
    states = gates.states_by_rule(vals, g)
    if not states:
        raise ValueError(f"no data for gate feed {g['source']} {g['site']}")
    before = [s for t, s in states if t <= t0]
    first = before[-1] if before else states[0][1]
    pts = [(t0, g["open"] if first else g["closed"])]
    for t, s in states:
        if t > t0 and (g["open"] if s else g["closed"]) != pts[-1][1]:
            pts.append((t, g["open"] if s else g["closed"]))
    return study.LiveSetting(g["path"], g.get("units", "UNSPECIF"), pts)


TO_DSS_M3S_CFS = 0.3048**3  # m3/s per cfs


def live_depletion(store: Store, dcfg: dict, start: int, end: int, now: int) -> dict[int, float]:
    """Each day's island water use (m3/s, by day's start) from the live
    source: as reported for the days it covers; on later days, the last day
    reported without rain (a value at or above zero, the depletion alone), as
    rain is not forecast here. Days before the record keep the reference."""
    vals = [(v.t, v.value) for v in store.values(dcfg["source"], dcfg["site"], dcfg["parameter"], start, now + 1) if v.value is not None]
    days = {t: v for t, v in vals}
    if not vals:
        return days
    dry = next((v for t, v in reversed(vals) if v >= 0), None)
    if dry is not None:
        t = vals[-1][0] + DAY
        while t <= end:
            days[t] = dry
            t += DAY
    return days


def pst_midnight(t: int) -> int:
    return (t - dss.PST) // DAY * DAY + dss.PST


def check(store: Store, run_dir: Path, cfg: dict, c: dict, start: int, end: int, kind: str = "flow") -> dict:
    """DSM2 at a check station against the measured value over [start, end):
    means, the moment-to-moment and the tidally averaged (25 h) differences.
    Flow (m3/s) from HYDRO; "ec" (uS/cm) or "temp" (degC) from QUAL. A flow
    check that names a `channel` reads that channel's flow (the mean of its two
    ends) from HYDRO's tidefile, so any channel can be checked, not only those
    DWR's study writes out."""
    n = (end - start) // STEP
    first = start + STEP
    if kind == "flow" and "channel" in c:
        q = output.channel_flow(run_dir, c["channel"])
        if q is None:
            raise ValueError(f"channel {c['channel']} is not in the tidefile")
        return _compare(c, q.sampled(first, n, STEP), grid(store, c, first, n))
    if kind == "ssc":
        # GTM's SSC, the mean of the channel's two ends (gtm_sed.h5), against the
        # station's turbidity through its part of the Delta's relation (dsm2.toml).
        from fdmr_plugins.dsm2.sediment import ssc_from_turbidity

        m = output.channel_concentration(output.files(run_dir)["sediment"], "ssc", c["channel"])
        if m is None:
            raise ValueError(f"no SSC for channel {c['channel']}")
        r = c["relation"]
        meas = [None if v is None else ssc_from_turbidity(v, r["a"], r["b"]) for v in grid(store, c, first, n)]
        return _compare(c, m.sampled(first, n, STEP), meas)
    model = output.station_series(run_dir, str(DSM2_ROOT / "bin" / "dsstool"), cfg["output_a"], kind, c["output"], first, n)
    return _compare(c, model, grid(store, c, first, n))


def net_check(store: Store, run_dir: Path, c: dict, start: int, end: int) -> dict:
    """DSM2's net flow in a channel against a gauge's tidally filtered flow,
    day by day over [start, end) (whole days, PST): DSM2's flow through the
    channel (the mean of its two ends, from HYDRO's tidefile), filtered by
    Godin's filter (ref:godin1972), and the gauge's own tidally filtered flow, each as daily
    means. A measured day needs 18 hours of values (one daily value, for a
    gauge that publishes only those)."""
    import numpy as np

    from fdmr_plugins.fvcom.correct import tidal_mean

    s = output.channel_flow(run_dir, c["channel"])
    if s is None:
        raise ValueError(f"channel {c['channel']} is not in the tidefile")
    tq, per_hour = s.times(), 3600 // s.step
    qf = tidal_mean(s.values, float(per_hour), edges="missing")  # no held ends in a compared mean (#71)
    days = range(start, end - DAY + 1, DAY)
    rows = []
    for d in days:
        sel = (tq >= d) & (tq < d + DAY)
        ok = sel & np.isfinite(qf)
        model = float(qf[ok].mean()) if ok.sum() >= per_hour * 18 else None
        vs = [v for v in store.values(c["source"], c["site"], c["parameter"], d, d + DAY - 1) if v.value is not None]
        if c.get("duration") == "D":
            meas = vs[0].value if vs else None
        else:
            meas = float(np.mean([v.value for v in vs])) if len({v.t // 3600 for v in vs}) >= 18 else None
        if model is not None and meas is not None:
            rows.append((model, meas))
    if not rows:
        return {"name": c["name"], "site": c["site"], "days": 0}
    m, g = np.array(rows).T
    return {
        "name": c["name"], "site": c["site"], "channel": c["channel"], "days": len(rows),
        "measured_mean": round(float(g.mean()), 1), "model_mean": round(float(m.mean()), 1),
        "mean_difference": round(float((m - g).mean()), 1), "daily_mae": round(float(np.abs(m - g).mean()), 1),
    }


def _compare(c: dict, model: list[float | None], meas: list[float | None]) -> dict:
    w = 100  # 25 hours
    tm, tmeas = running_mean(model, w), running_mean(meas, w)
    both = [(a, b) for a, b in zip(model, meas) if a is not None and b is not None]
    tide = [(a, b) for a, b in zip(tm, tmeas) if a is not None and b is not None]
    if not both:
        return {"name": c["name"], "n": 0}
    return {
        "name": c["name"], "hours": round(len(both) / 4),
        "measured_mean": round(mean(b for _, b in both), 1),
        "model_mean": round(mean(a for a, _ in both), 1),
        "instant_mae": round(mean(abs(a - b) for a, b in both), 1),
        "tidal_mae": round(mean(abs(a - b) for a, b in tide), 1) if tide else None,
        "measured_range": round(max(b for _, b in both) - min(b for _, b in both), 1),
    }


def publish_run(run_dir: Path, cfg: dict, region_dir: Path, now: int, end: int, out_root: Path, keep: int = 3,
                net_checks: list[dict] | None = None, assumptions: list[dict] | None = None,
                sediment_checks: list[dict] | None = None, problems: list[dict] | None = None) -> Path:
    """Publish a finished run as a network reach (docs/format.md): geometry,
    daily field files from 14 days before now to the end of the forecast, and
    the reach's entry for index.json. Points out_root/latest at it."""
    from fdmr_plugins.dsm2 import fields, grid

    from .region import load_region

    out_files = output.files(run_dir)
    tidefile = out_files["tidefile"]
    channels, nodes = grid.load(str(DSM2_ROOT / "inputs" / cfg["grid"]), grid.read_channels(tidefile))
    reach = cfg["reach"]  # the network reach's id, from dsm2.toml (#68)
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%MZ")
    pub = out_root / stamp
    if pub.exists():
        # Never rewrite a published file in place: hourly cycles hard-link these
        # files, and a browser may be reading one. Fresh files, fresh folder.
        import shutil

        shutil.rmtree(pub)
    (pub / "geometry").mkdir(parents=True)
    extras = grid.read_extras(tidefile)
    exports = cfg.get("exports", {})
    # Points over each pool's mapped water (fdmr_runner.pool_water), where the region has them.
    pw = region_dir / "pool_water.json"
    water = {k: v["points"] for k, v in json.loads(pw.read_text(encoding="utf-8"))["pools"].items()} if pw.exists() else None
    geo = grid.add_extras(grid.geometry(reach, channels, nodes, widths=grid.read_widths(tidefile)), extras, nodes, exports,
                          centres=cfg.get("pool_centres"), water=water)
    (pub / "geometry" / f"{reach}.json").write_text(json.dumps(geo, separators=(",", ":")), encoding="utf-8")
    start = (now - 14 * DAY) // DAY * DAY
    qual_file, fp_file, temp_file, sed_file = (out_files[k] for k in ("ec", "fingerprint", "temperature", "sediment"))
    days = fields.write_days(tidefile, reach, pub / reach, start, end,
                             node_ids=[n["id"] for n in geo["nodes"]], extras=extras, exports=set(exports),
                             qual_file=qual_file if qual_file.exists() else None,
                             sources_file=fp_file if fp_file.exists() else None,
                             temp_file=temp_file if temp_file.exists() else None,
                             sediment_file=sed_file if sed_file.exists() else None)
    sources = [f"source:{g}" for g in cfg.get("fingerprint", {})] if fp_file.exists() else []
    solid, floor = writer.certainty_for(load_region(region_dir).reaches, reach)  # region.toml only (#77)
    import fdmr_py

    utc = writer.utc
    steps = (end - start) // STEP
    entry = {
        "id": reach, "kind": "1d-tidal-network", "dims": 1, "network": True,
        "elements": len(channels), "layers": 1, "fields": fields.FIELDS,
        # In <day>.quality.fdf, loaded only to show them (docs/format.md).
        "quality_fields": (["ec", "salinity"] if qual_file.exists() else []) + (["temperature"] if temp_file.exists() else [])
        + (["ssc"] + (["bed_mud", "erosion"] if sed_file.with_name("gtm_sed_bed.bin").exists() else []) if sed_file.exists() else [])
        + sources,
        # Each water source's name and colour, the region's (dsm2.toml [fingerprint_shown], #89).
        **({"source_groups": [{"id": g, **cfg.get("fingerprint_shown", {}).get(g, {"label": g})} for g in cfg.get("fingerprint", {})]}
           if sources else {}),
        # Of those, the ones describing the bed rather than the water: not carried on with it (#49).
        **({"bed_fields": ["bed_mud", "erosion"]} if sed_file.exists() and sed_file.with_name("gtm_sed_bed.bin").exists() else {}),
        # The sediment's settling velocity, for pollutants that ride on it (#16).
        **({"sediment": {"settling_velocity_m_s": cfg["sediment"]["scalars"]["sed_settling"]}}
           if sed_file.exists() and cfg.get("sediment") else {}),
        "time": {"start": utc(start), "step_s": STEP, "days": days},
        "now": utc(now),
        "certainty": [round(fdmr_py.certainty_at(start + i * STEP - now, solid, end - now, floor), 4) for i in range(steps)],
        "flow_source": f"dsm2 run {run_dir.name}",
        # Net flow against the gauges' own tidally filtered flow, the last 14 days (net_check).
        **({"net_checks": net_checks} if net_checks else {}),
        # GTM's SSC against station turbidity through the relations, the last 14 days (#16).
        **({"sediment_checks": sediment_checks} if sediment_checks else {}),
        # How each input was carried past its last reading (#22).
        **({"assumptions": assumptions} if assumptions else {}),
        # What failed in this run (#55): the cycle that links it in reports it.
        **({"problems": problems} if problems else {}),
    }
    (pub / "reach.json").write_text(json.dumps(entry, separators=(",", ":")), encoding="utf-8")
    writer.swap_link(out_root, stamp)
    writer.prune_folders(out_root, keep, stamp)  # keep the newest few
    return pub


def temp_dir() -> Path:
    """DSM2's temporary files: on disk beside its install, not in /tmp, which
    is memory on the runner (#36); a short path, which QUAL needs."""
    d = DSM2_ROOT.parent / "qt"
    d.mkdir(exist_ok=True)
    return d


def prune_runs(work: Path, keep: int = 2) -> None:
    """Keep the newest few run folders: each holds HYDRO's full output, hundreds of MB."""
    import shutil

    runs = sorted((p for p in work.iterdir() if p.is_dir() and p.name[:2].isdigit()), key=lambda p: p.stat().st_mtime)
    for p in runs[: max(0, len(runs) - keep)]:
        shutil.rmtree(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region")
    ap.add_argument("store")
    ap.add_argument("work")
    ap.add_argument("--days", type=int, help="days before today: warm-up plus the past window (default: days_back in dsm2.toml)")
    ap.add_argument("--ahead", type=int, default=14, help="days of forecast")
    ap.add_argument("--as-of", help="pretend it is this UTC time, to check the forecast against what happened")
    ap.add_argument("--publish", help="publish the run as a reach into this folder (its `latest` link)")
    args = ap.parse_args(argv)
    region_dir = Path(args.region)
    cfg = load(region_dir)
    if cfg is None:
        print("no dsm2.toml in this region")
        return 1
    store = Store(args.store)
    hindcast = args.as_of is not None
    now = int(datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc).timestamp()) if hindcast else int(time.time())
    today = pst_midnight(now)
    if args.days is None:
        args.days = cfg.get("days_back", 28)
    start, end = today - args.days * DAY, today + args.ahead * DAY
    t0 = start - DAY
    n = (end + DAY - t0) // STEP + 1
    live: list[study.LiveSeries | study.LiveSetting] = []
    assumed: list[dict] = []  # how each input was carried past its last reading (index.json `assumptions`)
    problems: list[dict] = []  # what failed in this run, published with it (#55)

    def last_reading(f: dict) -> int | None:
        x = store.latest(f["source"], f["site"], f["parameter"])
        return min(x[0], now) if x else None

    for f in cfg["live"]:
        series, how = carried_on(store, f, t0, n, now, use_river_forecasts=True, hindcast=hindcast)
        values = resample([v * TO_DSS[f["units"]] for v in series], dss.interval(f["path"]))
        live.append(study.LiveSeries(f["path"], f["units"], t0, values))
        print(f"{f['path']:44s} {f['source']} {f['site']:9s} {how}")
        assumed.append({"input": f"{f['source']} {f['site']} {f['parameter']}", "how": how, "last_reading": last_reading(f),
                        **({"daily_held_flat": True} if f.get("duration") == "D" and not f.get("daily_shape") else {})})
    for g in cfg.get("live_gate", []):
        assumed.append({"input": f"gate {g.get('name', g['site'])}", "how": "held in its last state", "last_reading": last_reading(g)})
    live += [gate_setting(store, g, t0, now) for g in cfg.get("live_gate", [])]
    live += [study.LiveSetting(f["path"], f.get("units", "UNSPECIF"), [(t0, f["value"])]) for f in cfg.get("fixed", [])]
    root = DSM2_ROOT
    dcfg = cfg.get("depletion")
    depletion = None
    if dcfg:
        days = live_depletion(store, dcfg, t0 - DAY, end + 2 * DAY, now)
        depletion = study.Depletion(dcfg["file"], tuple(dcfg["use"]), tuple(dcfg["returns"]),
                                    {t: v / TO_DSS_M3S_CFS for t, v in days.items()}, dcfg.get("add_use", ""),
                                    dcfg.get("wetter", True), tuple(dcfg.get("rain_months", ())))
    settings = study.RunSettings(
        study=root / "inputs" / cfg["inputs"] / cfg["study"],
        replace=cfg["replace"], version=cfg["version"], version_settings=cfg["version_settings"],
        reference_year=cfg["reference_year"], tool=str(root / "bin" / "dsstool"), hydro=str(root / "bin" / "hydro"),
        qual=str(root / "bin" / "qual"), mains=cfg.get("mains", ["hydro.inp"]), depletion=depletion,
        temp_dir=str(temp_dir()),
    )
    run_dir = Path(args.work) / (datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%MZ") + ("-asof" if hindcast else ""))
    summary = study.build(settings, run_dir, start, end, live)
    print(f"run folder {run_dir}: {len(summary['live'])} live inputs, {summary['reference_year']} from {cfg['reference_year']}")
    dp = summary.get("depletion") or {}
    assumed.append({"input": "island water use", "how": (f"DWR's averages, {dp['series']} series moved with {dcfg['source']} {dcfg['parameter']}"
                                                         if dp.get("series") else "DWR's averages")})
    if dp.get("series"):
        ch = [c * TO_DSS_M3S_CFS for c in dp["change"] if c != 0.0]
        print(f"island water use: {dp['series']} series follow {dcfg['source']} {dcfg['parameter']} on {dp['days']} days"
              + (f"; net use changed by {mean(ch):+.1f} m3/s on average ({min(ch):+.1f} to {max(ch):+.1f})" if ch else ""), flush=True)
    secs = study.run_hydro(settings, run_dir)
    print(f"hydro: {args.days + args.ahead} days in {secs:.0f} s")
    # After HYDRO, every QUAL run and GTM only read its tidefile and write
    # files of their own: each study is written first, then they run side by
    # side (#37). jobs: (program, main, unlimited stack, label, required).
    jobs: list[tuple[str, str, bool, str, bool]] = [(settings.qual, main, False, f"qual ({main})", True) for main in settings.mains[1:]]
    if cfg.get("fingerprint") and len(settings.mains) > 1:
        from fdmr_plugins.dsm2 import fingerprint

        counts = fingerprint.write_study(run_dir, settings.mains[1], summary["env"], cfg["fingerprint"])
        jobs.append((settings.qual, fingerprint.MAIN, False,
                     f"qual (water sources: {', '.join(f'{g} {n} inflows' for g, n in counts.items())})", True))
    tcfg = cfg.get("temperature")
    if tcfg and len(settings.mains) > 1:
        from fdmr_plugins.dsm2 import temperature

        wx, how = weather(store, tcfg, t0, n, now, use_forecast=not hindcast)
        for p, h in how.items():
            print(f"weather {p:28s} {h}")
            assumed.append({"input": f"weather {p}", "how": h})
        climate = temperature.Climate(t0, wx["air_temperature"], wx["dew_point"], wx["wind_speed"], wx["cloud_cover"], wx["air_pressure"])
        water = {}
        for f in tcfg["water"]:
            water[f["name"]], h = carried_on(store, f, t0, n, now, use_river_forecasts=False)
            print(f"water temperature {f['name']:10s} {f['source']} {f['site']:9s} {h}")
            assumed.append({"input": f"inflow temperature {f['name']} ({f['source']} {f['site']})", "how": h, "last_reading": last_reading(f)})
        outputs = [(c["output"], c["channel"], str(c["distance"])) for c in cfg.get("temp_checks", [])]
        counts = temperature.write_study(run_dir, settings.mains[1], summary["env"], settings.tool, climate, water,
                                         tcfg["inflows"], tcfg.get("scalars", {}), outputs)
        jobs.append((settings.qual, temperature.MAIN, False,
                     f"qual (temperature: {', '.join(f'{g} {k} inflows' for g, k in counts.items())})", True))
    scfg = cfg.get("sediment")
    gtm = root / "bin" / "gtm"
    if scfg and len(settings.mains) > 1 and gtm.exists():
        from fdmr_plugins.dsm2 import sediment

        water = {}
        for f in scfg["water"]:
            try:
                water[f["name"]], h = ssc_series(store, f, t0, n, now)
            except ValueError as e:  # a gauge with nothing: that inflow brings none, and says so
                water[f["name"]], h = [0.0] * n, f"no data ({e}); taken as 0 mg/L"
            print(f"sediment {f['name']:10s} {f['source']} {f['site']:9s} {h}")
            assumed.append({"input": f"inflow sediment {f['name']} ({f['source']} {f['site']})", "how": h, "last_reading": last_reading(f)})
        outputs = [(c["output"], c["channel"], str(c["distance"])) for c in cfg.get("sediment_checks", [])]
        counts = sediment.write_study(run_dir, settings.mains[1], summary["env"], settings.tool, t0, water,
                                      scfg["inflows"], scfg["scalars"], scfg["diameter_mm"], outputs, scfg.get("constants"))
        for name, v in (scfg.get("constants") or {}).items():
            if counts.get(name):
                assumed.append({"input": f"inflow sediment, {name}", "how": f"{counts[name]} inflows at a fixed {v:g} mg/L (dsm2.toml [sediment.constants])"})
        if counts.get("none"):
            assumed.append({"input": "inflow sediment, other inflows",
                            "how": f"{counts['none']} inflows (eastside streams, treatment plants and any others) bring no sediment: no live gauge"})
        # Not required: a failed sediment run leaves the rest of the run as it is.
        jobs.append((str(gtm), sediment.MAIN, True, f"gtm (sediment: {', '.join(f'{g} {k} inflows' for g, k in counts.items())})", False))
    t_side = time.time()
    done = study.side_by_side([(p, m, big) for p, m, big, _, _ in jobs], run_dir, settings.temp_dir)
    for _, main, _, label, required in jobs:
        r = done[main]
        if isinstance(r, Exception):
            print(f"{label} FAILED: {r}", flush=True)
            if required:
                raise r
            problems.append({"step": "GTM sediment run", "error": str(r)[:300]})
        else:
            print(f"{label}: {r:.0f} s")
    print(f"after hydro, side by side: {time.time() - t_side:.0f} s")
    kinds = [(c, "flow") for c in cfg.get("checks", [])]
    if len(settings.mains) > 1:
        kinds += [(c, "ec") for c in cfg.get("ec_checks", [])]
        kinds += [(c, "temp") for c in cfg.get("temp_checks", []) if tcfg]
        kinds += [(c, "ssc") for c in cfg.get("sediment_checks", []) if output.files(run_dir)["sediment"].exists()]
    sediment_checks = []
    for c, kind in kinds:
        try:  # one check's failure, any part of it, does not stop the run
            r = check(store, run_dir, cfg, c, start + 7 * DAY, now // STEP * STEP, kind)
            print("past    ", json.dumps(r))
            if kind == "ssc":
                r14 = check(store, run_dir, cfg, c, today - 14 * DAY, now // STEP * STEP, kind)
                sediment_checks.append({**r14, "site": f"{c['source']} {c['site']}", "channel": c["channel"],
                                        "relation": c["relation"]["area"], "unit": "mg/L"})
            if hindcast:
                print("forecast", json.dumps(check(store, run_dir, cfg, c, now // STEP * STEP, min(end, int(time.time())) // STEP * STEP, kind)))
        except Exception as e:
            print(f"check {c['name']} ({kind}) FAILED {e}", flush=True)
            problems.append({"step": f"check {c['name']} ({kind})", "error": str(e)[:300]})
    nets = []
    for c in cfg.get("net_checks", []):
        try:
            nets.append(net_check(store, run_dir, c, today - 14 * DAY, today))
        except Exception as e:  # one check's failure does not stop the run
            print(f"net check {c['name']} FAILED {e}", flush=True)
            problems.append({"step": f"net check {c['name']}", "error": str(e)[:300]})
    for r in nets:
        print("net     ", json.dumps(r))
    store.close()
    if args.publish:  # a hindcast publishes too, wherever it is told (fdmr_runner.nightly)
        pub = publish_run(run_dir, cfg, region_dir, now // STEP * STEP, end, Path(args.publish), net_checks=nets, assumptions=assumed,
                          sediment_checks=sediment_checks, problems=problems)
        size = sum(p.stat().st_size for p in pub.rglob("*") if p.is_file())
        print(f"published {pub} ({size / 1e6:.1f} MB)")
        prune_runs(Path(args.work))
    return 0


if __name__ == "__main__":
    sys.exit(main())
