"""Water-source fingerprinting with QUAL (specification 7.5).

What share of the water in each channel came from each source: each river,
the sea, farm drainage and so on. Each source group becomes a
conservative tracer, held at 100 where its water enters and 0 everywhere else,
so its concentration anywhere is that source's share in percent. DWR calls
this fingerprinting.

The inflows and where they enter are taken from DWR's own salinity inputs (the
NODE_CONCENTRATION and RESERVOIR_CONCENTRATION tables), so every inflow DWR
gives a salinity to can be put in a group by name. QUAL only simulates a
constituent that some output asks for, and matches a concentration to an
inflow by the inflow's own name (both found 25 Nov 2026), so the study asks
for one station output per tracer and uses DWR's names.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import inp

MAIN = "qual_fp.inp"


def inflows(qual_main: Path, env: dict[str, str]) -> list[tuple[str, str, str]]:
    """(name, "node" or "reservoir", where) of every inflow DWR's QUAL study gives a salinity to."""
    out: dict[str, tuple[str, str, str]] = {}
    for f in inp.included(qual_main, env):
        for key, header, rows in inp.blocks(f.read_text(encoding="utf-8", errors="replace")):
            up = [h.upper() for h in header]
            if key == "NODE_CONCENTRATION" and "NODE_NO" in up:
                i, j = up.index("NAME"), up.index("NODE_NO")
                for r in rows:
                    if len(r) > j:
                        out.setdefault(r[i], (r[i], "node", r[j]))
            elif key == "RESERVOIR_CONCENTRATION" and "RES_NAME" in up:
                i, j = up.index("NAME"), up.index("RES_NAME")
                for r in rows:
                    if len(r) > j:
                        out.setdefault(r[i], (r[i], "reservoir", r[j]))
    return list(out.values())


def study_text(groups: Mapping[str, Sequence[str]], flows: Sequence[tuple[str, str, str]]) -> tuple[str, str, str, dict[str, int]]:
    """The fingerprint study's main file, concentrations and outputs, and how
    many inflows each group took."""
    node_rows, res_rows, counts = [], [], {}
    for group, patterns in groups.items():
        rx = [re.compile(p) for p in patterns]
        counts[group] = 0
        for name, kind, where in flows:
            if any(r.fullmatch(name) for r in rx):
                counts[group] += 1
                if kind == "node":
                    node_rows.append(f"{name:24s} {where:>8s}  {group:12s} last    constant  100")
                else:
                    res_rows.append(f"{name:24s} {where:24s} {group:12s} last    constant  100")
    conc = "NODE_CONCENTRATION\nNAME                     NODE_NO   VARIABLE     FILLIN  FILE      PATH\n" + "\n".join(node_rows) + "\nEND\n"
    if res_rows:
        conc += "RESERVOIR_CONCENTRATION\nNAME                     RES_NAME                 VARIABLE     FILLIN  FILE      PATH\n" + "\n".join(res_rows) + "\nEND\n"
    out = "OUTPUT_CHANNEL\nNAME          CHAN_NO  DISTANCE  VARIABLE      INTERVAL  PERIOD_OP  FILE\n"
    out += "".join(f"fp_{g:10s}       1         0  {g:12s}  1DAY      ave        ${{DSM2OUTPUTDIR}}/qual_fp.dss\n" for g in groups)
    out += "END\n"
    main = """CONFIGURATION
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
init_conc        0.0
printlevel       1
qual_time_step   15min
warn_missing     false
warn_question    false
warn_unchecked   false
run_start_date   ${QUAL_START_DATE}
run_end_date     ${END_DATE}
run_start_time   ${START_TIME}
run_end_time     ${END_TIME}
temp_dir         ${TEMPDIR}
binary_output    false
dss_direct       true
flush_output     20day
END
IO_FILE
MODEL      TYPE     IO               INTERVAL FILE
qual       output   out              none     ${DSM2OUTPUTDIR}/qual_fp.qof
qual       hdf5     out              15min    ${DSM2OUTPUTDIR}/qual_fp.h5
END
TIDEFILE
START_DATE    END_DATE    FILE
runtime       length      ${HYDROTIDEFILE}
END
PARAMETER
${DSM2INPUTDIR}/scalar_qual_std.inp
END
QUAL_TIME_SERIES
fp_conc.inp
END
OUTPUT_TIME_SERIES
fp_out.inp
END
"""
    return main, conc, out, counts


def write_study(run_dir: Path, qual_main: str, env: dict[str, str], groups: Mapping[str, Sequence[str]]) -> dict[str, int]:
    """Write the fingerprint study into a run folder, beside the salinity study
    whose inflows it uses. Returns how many inflows each group took."""
    main, conc, out, counts = study_text(groups, inflows(run_dir / qual_main, env))
    (run_dir / MAIN).write_text(main, encoding="utf-8")
    (run_dir / "fp_conc.inp").write_text(conc, encoding="utf-8")
    (run_dir / "fp_out.inp").write_text(out, encoding="utf-8")
    return counts
