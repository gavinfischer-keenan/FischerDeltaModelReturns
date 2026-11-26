"""The ocean's state at the model's edge (issue #18; overlays.toml
[ocean_state]): not a model of the ocean, a record of what it is doing.

- El Nino-Southern Oscillation (fdmr_adapters.cpc): the latest weekly sea-
  surface temperature anomaly in each Nino region of the tropical Pacific
  and its change over four weeks, and the latest Oceanic Nino Index. In an
  El Nino the eastern Pacific stands higher and warmer, and coastally trapped
  waves carry that up the coast; La Nina does the reverse.
- Sea level offshore (`sea_level`): satellite altimetry's sea level anomaly
  averaged over a box off the coast (fdmr_adapters.erddap, fetch_grid_mean),
  its latest day and 30-day mean.

With the tide residual (overlays.tide_residual, measured less predicted water
level at the coast's own gauges, its 30-day mean the slow part), these let a
miss be put down to the ocean or to the weather. A record for the validation
work (#9), not an input.
"""

from __future__ import annotations

from fdmr_store import Store

from fdmr_adapters.cpc import REGIONS
from fdmr_adapters.cpc import SOURCE as CPC

DAY = 86_400
ENSO_THRESHOLD = 0.5  # degC: CPC's El Nino (+) and La Nina (-) threshold for the Oceanic Nino Index


def _latest(store: Store, src: str, site: str, p: str, t0: int, t1: int):
    vs = [v for v in store.values(src, site, p, t0, t1) if v.value is not None]
    return vs[-1] if vs else None


def summary(cfg: dict, store: Store, now: int) -> dict | None:
    oc = cfg.get("ocean_state")
    if not oc:
        return None
    out: dict = {}
    if oc.get("enso"):
        weekly = {}
        for r in REGIONS:
            v = _latest(store, CPC, r, "sst_anomaly", now - 21 * DAY, now + 1)
            if v:
                before = _latest(store, CPC, r, "sst_anomaly", v.t - 31 * DAY, v.t - 27 * DAY)
                weekly[r] = {"week": v.t, "anomaly_c": v.value, **({"change_4w_c": round(v.value - before.value, 2)} if before else {})}
        oni = _latest(store, CPC, "oni", "sst_anomaly", now - 150 * DAY, now + 1)
        if weekly or oni:
            out["enso"] = {"source": "NOAA Climate Prediction Center (weekly Nino indices, 1991-2020 base; Oceanic Nino Index)",
                           "weekly": weekly, "threshold_c": ENSO_THRESHOLD,
                           **({"oni": {"season_middle": oni.t, "anomaly_c": oni.value}} if oni else {})}
    sl = oc.get("sea_level")
    if sl:
        from fdmr_adapters.erddap import source

        src = source(sl["server"])
        v = _latest(store, src, sl["site"], "sea_level_anomaly", now - 10 * DAY, now + 1)
        if v:
            month = [x.value for x in store.values(src, sl["site"], "sea_level_anomaly", v.t - 30 * DAY, v.t + 1) if x.value is not None]
            out["sea_level"] = {"source": f"{sl['server']} {sl['dataset']} {sl['variable']}", "box": sl["box"], "day": v.t,
                                "anomaly_m": round(v.value, 3), "mean_30d_m": round(sum(month) / len(month), 3), "days": len(month)}
    return out or None
