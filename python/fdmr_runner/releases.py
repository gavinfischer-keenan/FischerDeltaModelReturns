"""A dam's release forecast where its operator publishes no schedule for the
gauge below it (issue #21; region.toml, a release gauge's `release_rule`).

Two ways, in this order:

- `release_forecast`: the River Forecast Center (ref:cnrfc) publishes a release forecast
  for a dam upstream of the gauge (a point ending in R, such as a reservoir's
  regulated outflow). The gauge's release follows its changes: today's
  measured release, plus the forecast less the forecast's value now.
- `reservoir` with `report` and `inflow`: the flood-control rule. From the
  reservoir's storage now (CDEC), its top of conservation (ref:usace_fcr; the US Army Corps
  of Engineers' daily report: the most it may hold, the rest kept for
  floods; held at the latest day's value) and its forecast inflow (the River
  Forecast Center), storage is stepped on with today's release. Once it would
  reach the top of conservation, the release must rise to at least the
  inflow, as the rule requires (no more water may be stored). The rise over
  today's release is added to the gauge below; until then the release is
  held. Encroached space is not evacuated faster than inflow: how fast an
  operator draws it down is set by its Water Control Manual and channel
  capacities, not modelled here.

Either way the gauge's release never falls below today's (the rule only ever
forces releases up), and how it was made is recorded for the cycle's
assumptions.
"""

from __future__ import annotations

import numpy as np

from fdmr_adapters import cnrfc
from fdmr_store import Store

from .forecast import on_grid

DAY = 86_400


REPORT_AGE_DAYS = 4  # the flood-control report and the release, daily, still used this old: a long weekend (FDMR's choice)
STORAGE_AGE_DAYS = 2  # the reservoir's storage, reported hourly or daily, still used this old (FDMR's choice)

def _latest(store: Store, src: str, site: str, p: str, t0: int, t1: int):
    vs = [v for v in store.values(src, site, p, t0, t1) if v.value is not None]
    return vs[-1] if vs else None


def _forecast_grid(store: Store, point: str, t0: int, step: int, n: int, now: int) -> np.ndarray | None:
    fv = store.values(cnrfc.SOURCE, point, "discharge", now - DAY, t0 + (n + 1) * step)
    if not fv:
        return None
    return np.array([np.nan if v is None else v for v in on_grid([(v.t, v.value) for v in fv], t0, step, n)], dtype=float)


def rule_forecast(store: Store, rule: dict, gauge_now: float, t0: int, step: int, n: int, i_now: int, now: int) -> tuple[list | None, dict]:
    """The gauge's release on the grid (None before now, the forecast after),
    and the record of how it was made."""
    out: list = [None] * n
    if "release_forecast" in rule:
        p = rule["release_forecast"]["point"]
        f = _forecast_grid(store, p, t0, step, n, now)
        if f is None or not np.isfinite(f[i_now:]).any():
            return None, {"how": f"held: the River Forecast Center's release forecast {p} has nothing ahead"}
        f_now = f[i_now] if np.isfinite(f[i_now]) else f[i_now:][np.isfinite(f[i_now:])][0]
        for i in range(i_now, n):
            if np.isfinite(f[i]):
                out[i] = max(gauge_now, gauge_now + (f[i] - f_now))
        rises = max((x for x in out[i_now:] if x is not None), default=gauge_now) - gauge_now
        return out, {"how": f"follows the River Forecast Center's release forecast {p} upstream (its changes added to today's release)",
                     "largest_rise_m3s": round(float(rises), 2)}
    site = rule["reservoir"]
    # Where the reservoir's figures come from: the rule says (region.toml), not the engine (#66).
    toc = _latest(store, rule["flood_pool_source"], site, "top_of_conservation", now - REPORT_AGE_DAYS * DAY, now + 1)
    storage = _latest(store, rule["reservoir_source"], site, "reservoir_storage", now - STORAGE_AGE_DAYS * DAY, now + 1)
    release = _latest(store, rule["reservoir_source"], site, "reservoir_outflow", now - REPORT_AGE_DAYS * DAY, now + 1)
    inflow = _forecast_grid(store, rule["inflow"]["point"], t0, step, n, now)
    if toc is None or storage is None or release is None or inflow is None:
        missing = [k for k, v in (("top of conservation", toc), ("storage", storage), ("release", release), ("inflow forecast", inflow)) if v is None]
        return None, {"how": f"held: no {', '.join(missing)} for the flood-control rule"}
    s = storage.value
    r0 = release.value
    space0 = toc.value - s
    least = space0
    reached_at = None
    for i in range(i_now, n):
        q_in = inflow[i] if np.isfinite(inflow[i]) else None
        if q_in is None:
            out[i] = None
            continue
        r = max(r0, q_in) if s >= toc.value else r0
        s = min(s + (q_in - r) * step, toc.value) if s >= toc.value else s + (q_in - r) * step
        least = min(least, toc.value - s)
        if s >= toc.value and reached_at is None:
            reached_at = int(t0 + i * step)
        out[i] = gauge_now + (r - r0)
    ends = max((i for i in range(i_now, n) if out[i] is not None), default=None)
    rec = {"how": ("flood-control rule: storage reaches its top of conservation, so the release rises to at least the inflow"
                   if reached_at else "flood-control rule: storage stays below its top of conservation over the inflow forecast, so the release is held"),
           "reservoir": site, "flood_space_m3": round(space0), "least_flood_space_m3": round(least),
           "inflow_forecast": rule["inflow"]["point"], "top_of_conservation_from": toc.t,
           **({"reaches_top_at": reached_at} if reached_at else {}),
           **({"forecast_ends": int(t0 + ends * step)} if ends is not None else {})}
    return out, rec
