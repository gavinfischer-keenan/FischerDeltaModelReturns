"""Water temperature with QUAL (specification 7.5).

QUAL works out each channel's heat budget: sunshine (from the sun's height and
the cloud), the sky's and the water's own long-wave radiation, evaporation and
conduction to the air. It needs, for the whole Delta at once:

    cloud         fraction of the sky covered, 0 to 1
    dry_bulb      air temperature, degF
    wet_bulb      wet-bulb temperature, degF
    wind          wind speed, mph
    atm_pressure  air pressure, inches of mercury

(units from QUAL's own heat code, heat.f90 and calscsk.f90 in DSM2 8.5.1, read
25 Nov 2026), and the temperature of the water at every inflow, in degC.

DWR's temperature study uses a weather file its public release leaves out, so
the study here is written afresh: DWR's inflows (from its salinity study, as
fingerprint.py does), a temperature for each from the region's settings, and
live weather. Weather stations report a dew point, not a wet bulb; the wet bulb
is the one that gives, through QUAL's own vapour-pressure formulas, the
vapour pressure the dew point does, so QUAL sees the measured humidity.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import dss
from .fingerprint import inflows

MAIN = "qual_temp.inp"
DSS_FILE = "temp_live.dss"
F_PART = "FDMR-LIVE"
MPH = 3600 / 1609.344  # m/s to mph
INHG = 1 / 33.8639  # hPa to inches of mercury


def f_of(c: float) -> float:
    return c * 1.8 + 32


def vapour_pressure(t_f: float) -> float:
    """Saturation vapour pressure (inHg) at t_f (degF), QUAL's formula."""
    return 0.1001 * math.exp(0.03 * t_f) - 0.0837


def wet_bulb(t_f: float, dew_f: float, p_inhg: float) -> float:
    """The wet-bulb temperature (degF) that QUAL turns back into the vapour
    pressure of air at t_f with dew point dew_f, at pressure p_inhg."""
    e = vapour_pressure(min(dew_f, t_f))

    def air(wb: float) -> float:  # QUAL's vapour pressure of the air, from a wet bulb
        return vapour_pressure(wb) - 0.000367 * p_inhg * (t_f - wb) * (1.0 + (wb - 32.0) / 1571.0)

    lo, hi = min(dew_f, t_f) - 1.0, t_f
    for _ in range(50):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if air(mid) < e else (lo, mid)
    return (lo + hi) / 2


@dataclass(frozen=True)
class Climate:
    """The Delta's weather on a 15-minute grid from t0, SI units."""

    t0: int
    air_c: Sequence[float]
    dew_c: Sequence[float]
    wind_ms: Sequence[float]
    cloud: Sequence[float]
    pressure_hpa: Sequence[float]

    def qual(self) -> dict[str, tuple[str, list[float]]]:
        """QUAL's climate variables: name -> (DSS units, values)."""
        dry = [f_of(c) for c in self.air_c]
        p = [v * INHG for v in self.pressure_hpa]
        wet = [wet_bulb(t, f_of(d), pp) for t, d, pp in zip(dry, self.dew_c, p)]
        return {
            "dry_bulb": ("DEG F", dry),
            "wet_bulb": ("DEG F", wet),
            "wind": ("MPH", [v * MPH for v in self.wind_ms]),
            "cloud": ("FRACTION", [min(1.0, max(0.0, v)) for v in self.cloud]),
            "atm_pressure": ("IN HG", p),
        }


def climate_path(var: str) -> str:
    return f"/FDMR/DELTA/{var.upper().replace('_', '-')}//15MIN/{F_PART}/"


def water_path(name: str) -> str:
    """The series' DSS path. Drainage is daily: QUAL takes at most 100 inputs
    at 15 minutes, every inflow counting, and there are hundreds of drains."""
    return f"/FDMR/{name.upper()}/TEMP-WATER//{'1DAY' if name == 'drainage' else '15MIN'}/{F_PART}/"


def daily(values: Sequence[float], per_day: int = 96) -> list[float]:
    """A 15-minute series from a midnight, as daily means stamped at each day's
    end (DSS's way); the first stamp, that midnight, takes the first day's."""
    days = [sum(values[k * per_day : (k + 1) * per_day]) / per_day for k in range(len(values) // per_day)]
    return [days[0], *days] if days else []


def study_text(
    flows: Sequence[tuple[str, str, str]],
    assign: Mapping[str, Sequence[str]],
    scalars: Mapping[str, float],
    outputs: Sequence[tuple[str, int, str]],
) -> tuple[str, str, str, dict[str, int]]:
    """The temperature study's main file, inputs and outputs, and how many
    inflows each water series took. `assign` maps a series name to inflow names
    (regular expressions), in order: the first that matches wins, and "*"
    takes whatever no other matches. `outputs` are (name, channel, distance)
    station outputs."""
    f = f"${{STUDYDIR}}/{DSS_FILE}"
    rules = [(name, re.compile(p)) for name, pats in assign.items() for p in pats if p != "*"]
    rules += [(name, re.compile(".*")) for name, pats in assign.items() if "*" in pats]
    node_rows, res_rows, counts = [], [], {name: 0 for name in assign}
    for flow, kind, where in flows:
        series = next((name for name, rx in rules if rx.fullmatch(flow)), None)
        if series is None:
            raise ValueError(f"no water temperature for inflow {flow}")
        counts[series] += 1
        if kind == "node":
            node_rows.append(f"{flow:24s} {where:>8s}  temp  last  {f}  {water_path(series)}")
        else:
            res_rows.append(f"{flow:24s} {where:24s} temp  last  {f}  {water_path(series)}")
    ts = "INPUT_CLIMATE\nNAME            VARIABLE      FILLIN  FILE  PATH\n"
    ts += "".join(f"{v:15s} {v:13s} last    {f}  {climate_path(v)}\n" for v in ("cloud", "dry_bulb", "wet_bulb", "wind", "atm_pressure"))
    ts += "END\nNODE_CONCENTRATION\nNAME                     NODE_NO   VARIABLE  FILLIN  FILE  PATH\n" + "\n".join(node_rows) + "\nEND\n"
    if res_rows:
        ts += "RESERVOIR_CONCENTRATION\nNAME                     RES_NAME                 VARIABLE  FILLIN  FILE  PATH\n" + "\n".join(res_rows) + "\nEND\n"
    out = "OUTPUT_CHANNEL\nNAME          CHAN_NO  DISTANCE  VARIABLE  INTERVAL  PERIOD_OP  FILE\n"
    out += "".join(f"{n:12s} {c:8d}  {d:>8s}  temp      15MIN     inst       ${{DSM2OUTPUTDIR}}/qual_temp.dss\n" for n, c, d in outputs)
    out += "END\n"
    scalar = "".join(f"{k:16s} {v}\n" for k, v in scalars.items())
    main = f"""CONFIGURATION
config.inp
END
SCALAR
NAME             VALUE
checkdata        false
cont_bad         false
cont_missing     false
cont_question    false
cont_unchecked   true
dispersion       true
display_intvl    1day
printlevel       1
qual_time_step   15min
warn_missing     false
warn_question    false
warn_unchecked   false
run_start_date   ${{QUAL_START_DATE}}
run_end_date     ${{END_DATE}}
run_start_time   ${{START_TIME}}
run_end_time     ${{END_TIME}}
temp_dir         ${{TEMPDIR}}
binary_output    false
dss_direct       true
flush_output     20day
{scalar}END
IO_FILE
MODEL      TYPE     IO               INTERVAL FILE
qual       output   out              none     ${{DSM2OUTPUTDIR}}/qual_temp.qof
qual       hdf5     out              15min    ${{DSM2OUTPUTDIR}}/qual_temp.h5
END
TIDEFILE
START_DATE    END_DATE    FILE
runtime       length      ${{HYDROTIDEFILE}}
END
PARAMETER
${{DSM2INPUTDIR}}/scalar_qual_std.inp
END
QUAL_TIME_SERIES
temp_inputs.inp
END
OUTPUT_TIME_SERIES
temp_outputs.inp
END
"""
    return main, ts, out, counts


def write_study(
    run_dir: Path,
    qual_main: str,
    env: dict[str, str],
    tool: str,
    climate: Climate,
    water: Mapping[str, Sequence[float]],
    assign: Mapping[str, Sequence[str]],
    scalars: Mapping[str, float],
    outputs: Sequence[tuple[str, int, str]],
) -> dict[str, int]:
    """Write the temperature study and its inputs into a run folder, beside the
    salinity study whose inflows it uses. `water` holds each named series
    (degC, 15-minute, from climate.t0); "drainage", if assigned, is the air's
    daily mean temperature, a stand-in for water run off fields. QUAL starts
    every channel at the mean of the inflows' first day."""
    d = dss.Dss(tool)
    out = str(run_dir / DSS_FILE)
    for var, (units, vals) in climate.qual().items():
        d.write(out, climate_path(var), climate.t0, [dss.safe(v) for v in vals], units, "INST-VAL")
    series = dict(water)
    if "drainage" in assign:
        series["drainage"] = [max(0.5, v) for v in daily(climate.air_c)]
    for name in assign:
        if name not in series:
            raise ValueError(f"no series for water temperature {name}")
        d.write(out, water_path(name), climate.t0, [dss.safe(v) for v in series[name]], "DEG C", "INST-VAL")
    first = [v for s in water.values() for v in s[96:192]]
    init = {"init_conc": round(sum(first) / len(first), 1)} if first else {}
    main, ts, outs, counts = study_text(inflows(run_dir / qual_main, env), assign, {**init, **scalars}, outputs)
    (run_dir / MAIN).write_text(main, encoding="utf-8")
    (run_dir / "temp_inputs.inp").write_text(ts, encoding="utf-8")
    (run_dir / "temp_outputs.inp").write_text(outs, encoding="utf-8")
    return counts
