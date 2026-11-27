"""Suspended sediment with DSM2's General Transport Model (issue #16; ref:dsm2).

GTM carries suspended sediment through the channels on HYDRO's flows, as QUAL
carries salt, and adds what the bed gives and takes:

    erosion     E = M (tau_b / tau_ce - 1)  where tau_b > tau_ce, else 0
                (Partheniades 1965, ref:partheniades1965), no more
                than the bed holds
    deposition  D = ws c                    at any bed shear stress
    bed shear   tau_b = rho g n^2 u^2 / R^(1/3)  (Manning), from each cell's
                velocity u, hydraulic radius R and DSM2's Manning's n

with one class of mud, set by its settling velocity ws, critical shear stress
for erosion tau_ce and erosion rate M, and a bed that starts bare and holds
only what has settled on it. That is the Delta model of Achete, van der Wegen,
Roelvink and Jaffe (2015, ref:achete2015), whose calibrated values the region gives
(dsm2.toml [sediment]). GTM as released sets these from a grain size and needs
a bed module DWR's public inputs do not set up; runner/dsm2/gtm-sediment.patch
adds the settings and the bare bed, and says what it changes.

Where sediment enters, its concentration (SSC, mg/L) comes from a gauge: USGS's
own continuous SSC where it has one (Benicia, for the sea end), otherwise the
gauge's turbidity through a published relation for its part of the Delta,
log10(SSC) = a log10(T) + b (Achete et al. 2015, section 4.5). Inflows no
gauge covers bring none; the region says which they are and why.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import dss
from .fingerprint import inflows

MAIN = "gtm_sed.inp"
DSS_FILE = "sed_live.dss"
OUT_DSS = "gtm_sed.dss"
H5 = "gtm_sed.h5"
F_PART = "FDMR-LIVE"
CLASS = "mud"  # the one sediment class


def ssc_from_turbidity(t: float, a: float, b: float) -> float:
    """SSC (mg/L) from turbidity by log10(SSC) = a log10(T) + b. Turbidity at
    or below zero (a sensor's floor) gives 0."""
    return 10 ** (a * math.log10(t) + b) if t > 0 else 0.0


def water_path(name: str) -> str:
    return f"/FDMR/{name.upper()}/SSC//15MIN/{F_PART}/"


def study_text(
    flows: Sequence[tuple[str, str, str]],
    assign: Mapping[str, Sequence[str]],
    scalars: Mapping[str, object],
    diameter_mm: float,
    outputs: Sequence[tuple[str, int, str]],
    constants: Mapping[str, float] | None = None,
) -> tuple[str, dict[str, int]]:
    """GTM's main file for the sediment run, and how many inflows each series
    took. `assign` maps a series name to inflow names (regular expressions), the
    first match winning; the series "none" brings no sediment, a series in
    `constants` brings that fixed SSC (mg/L), and "*" takes whatever no other
    pattern matches. `outputs` are (name, channel, distance ft)."""
    constants = dict(constants or {})
    f = f"${{STUDYDIR}}/{DSS_FILE}"
    rules = [(name, re.compile(p)) for name, pats in assign.items() for p in pats if p != "*"]
    rules += [(name, re.compile(".*")) for name, pats in assign.items() if "*" in pats]
    node_rows, res_rows, bc_rows, counts = [], [], [], {name: 0 for name in assign}
    for flow, kind, where in flows:
        series = next((name for name, rx in rules if rx.fullmatch(flow)), None)
        if series is None:
            raise ValueError(f"no sediment concentration for inflow {flow}")
        counts[series] += 1
        file, path = (("constant", "0") if series == "none" else ("constant", f"{constants[series]:g}") if series in constants
                      else (f, water_path(series)))
        if kind == "node":
            node_rows.append(f"{flow:24s} {where:>8s}  ssc   last  {file}  {path}")
        else:
            res_rows.append(f"{flow:24s} {where:24s} ssc   last  {file}  {path}")
        bc_rows.append(f"{flow:24s} {CLASS:8s} 100.0")
    scalar = "".join(f"{k:20s} {str(v).lower() if isinstance(v, bool) else v}\n" for k, v in scalars.items())
    ts = "NODE_CONCENTRATION\nNAME                     NODE_NO   VARIABLE  FILLIN  FILE  PATH\n" + "\n".join(node_rows) + "\nEND\n"
    if res_rows:
        ts += "RESERVOIR_CONCENTRATION\nNAME                     RES_NAME                 VARIABLE  FILLIN  FILE  PATH\n" + "\n".join(res_rows) + "\nEND\n"
    out = "OUTPUT_CHANNEL\nNAME          CHAN_NO  DISTANCE  VARIABLE  INTERVAL  PERIOD_OP  FILE\n"
    out += "".join(f"{n:12s} {c:8d}  {d:>8s}  ssc       15MIN     inst       ${{DSM2OUTPUTDIR}}/{OUT_DSS}\n" for n, c, d in outputs)
    out += "END\n"
    main = f"""CONFIGURATION
config.inp
END
SCALAR
NAME                 VALUE
checkdata            false
cont_bad             false
cont_missing         false
cont_question        false
cont_unchecked       true
dispersion           true
display_intvl        1day
init_conc            0.0
printlevel           1
warn_missing         false
warn_question        false
warn_unchecked       false
run_start_date       ${{QUAL_START_DATE}}
run_end_date         ${{END_DATE}}
run_start_time       ${{START_TIME}}
run_end_time         ${{END_TIME}}
temp_dir             ${{TEMPDIR}}
binary_output        false
dss_direct           true
flush_output         20day
run_pdaf             false
gtm_dx               1000.0
gtm_time_step        5min
hdf_out              channel
dss_out              cell
{scalar}END
IO_FILE
MODEL      TYPE     IO             INTERVAL FILE
gtm        output   out            none     ${{DSM2OUTPUTDIR}}/gtm_sed.qof
gtm        hdf5     out            15min    ${{DSM2OUTPUTDIR}}/{H5}
gtm        restart  out            1mon     ${{DSM2OUTPUTDIR}}/gtm_sed.qrf
END
TIDEFILE
START_DATE END_DATE FILE
runtime    length   ${{HYDROTIDEFILE}}
END
GROUP
NAME
all_channels
END
GROUP_MEMBER
GROUP_NAME     MEMBER_TYPE   PATTERN
all_channels   channel       list,1-500,501-999
END
GROUP_VARIABLE
GROUP_NAME     CONSTITUENT   VARIABLE      VALUE
all_channels   ssc           diameter      {diameter_mm}
END
SUSPENDED_SEDIMENT_TYPE
COMPOSITION
{CLASS}
END
SUSPENDED_SEDIMENT_BOUNDARY
NAME                     COMPOSITION PERCENT
{chr(10).join(bc_rows)}
END
{ts}{out}"""
    return main, counts


def write_study(
    run_dir: Path,
    qual_main: str,
    env: dict[str, str],
    tool: str,
    t0: int,
    water: Mapping[str, Sequence[float]],
    assign: Mapping[str, Sequence[str]],
    scalars: Mapping[str, object],
    diameter_mm: float,
    outputs: Sequence[tuple[str, int, str]],
    constants: Mapping[str, float] | None = None,
) -> dict[str, int]:
    """Write the sediment run's main file and its inflow series (SSC, mg/L, on
    the 15-minute grid from t0) into a run folder beside the salinity study,
    whose inflows it takes. Returns how many inflows each series took."""
    d = dss.Dss(tool)
    out = str(run_dir / DSS_FILE)
    for name in assign:
        if name == "none" or name in (constants or {}):
            continue
        if name not in water:
            raise ValueError(f"no series for sediment {name}")
        d.write(out, water_path(name), t0, [dss.safe(v) for v in water[name]], "MG/L", "INST-VAL")
    main, counts = study_text(inflows(run_dir / qual_main, env), assign, scalars, diameter_mm, outputs, constants)
    (run_dir / MAIN).write_text(main, encoding="utf-8")
    return counts
