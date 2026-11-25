"""Check the network tracer's spreading against DSM2's QUAL (build plan,
network tracer): one pulse, released at the Sacramento's inflow for an hour,
in both, watched at channels downstream.

    python tools/pulse_check.py RUN_DIR PUBLISHED_DIR START_ISO HOURS D[,D...] CHANNEL...

RUN_DIR is a finished DSM2 run folder with its fingerprint study (whose main
file the pulse study copies); PUBLISHED_DIR the reach it published. The pulse
goes into QUAL as a constituent of its own at the Sacramento's inflow; the
tracer runs it from the published files once for each spreading coefficient D
(m2/s), with the `delta_pulse` example built. For each watched DSM2 channel it
prints, for each model, the share of the release in the channel summed over
time (share-hours), when it is there on average and how spread out in time
(standard deviation), in hours from the release, and its peak share. A check
on the model runner, not part of the app.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from fdmr_plugins.dsm2 import dss, fingerprint, inp
from fdmr_plugins.dsm2.fields import tidefile_start

DSM2 = Path("/srv/fdmr-data/dsm2")
ROOT = Path(__file__).resolve().parents[1]
FT = 0.3048
STEP = 900
PULSE = "/FDMR/SAC/PULSE//15MIN/FDMR-LIVE/"


def qual_pulse(run: Path, geo: dict, t_rel: int, n: int, watch: list[int]) -> np.ndarray:
    """The pulse in QUAL: the share of the release in each watched channel at
    each 15-minute step after t_rel."""
    env = inp.envvars((run / "config.inp").read_text())
    sac = next(f for f in fingerprint.inflows(run / "qual_ec.inp", env) if f[0] == "sac")
    day = lambda s: int(datetime.strptime(s, "%d%b%Y").replace(tzinfo=timezone.utc).timestamp()) + dss.PST  # noqa: E731
    t0, t1 = day(env["START_DATE"]) - 86_400, day(env["END_DATE"]) + 2 * 86_400  # the whole run, a day either side
    k = (t_rel - t0) // STEP
    vals = [0.0] * ((t1 - t0) // STEP)
    for i in range(k + 1, k + 5):  # the hour after t_rel
        vals[i] = 1000.0
    dss.Dss(str(DSM2 / "bin" / "dsstool")).write(str(run / "pulse.dss"), PULSE, t0, vals, "UNIT", "INST-VAL")
    main = (run / fingerprint.MAIN).read_text()
    main = main.replace("qual_fp.", "qual_pulse.").replace("fp_conc.inp", "pulse_conc.inp").replace("fp_out.inp", "pulse_out.inp")
    (run / "qual_pulse.inp").write_text(main)
    (run / "pulse_conc.inp").write_text(
        "NODE_CONCENTRATION\nNAME  NODE_NO  VARIABLE  FILLIN  FILE  PATH\n"
        f"sac  {sac[2]}  pulse  last  ${{STUDYDIR}}/pulse.dss  {PULSE}\nEND\n"
    )
    (run / "pulse_out.inp").write_text(
        "OUTPUT_CHANNEL\nNAME  CHAN_NO  DISTANCE  VARIABLE  INTERVAL  PERIOD_OP  FILE\n"
        + "".join(f"p{c}  {c}  0  pulse  1DAY  ave  ${{DSM2OUTPUTDIR}}/qual_pulse.dss\n" for c in watch)
        + "END\n"
    )
    r = subprocess.run([str(DSM2 / "bin" / "qual"), "qual_pulse.inp"], cwd=run, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stdout[-2000:])
    length = {int(e["id"]): (e["end_m"] - e["start_m"]) / FT for e in geo["elements"]}  # ft
    modifier = (run / "config.inp").read_text().split("DSM2MODIFIER", 1)[1].split()[0]
    with h5py.File(run / "output" / "qual_pulse.h5", "r") as q, h5py.File(run / "output" / f"{modifier}.h5", "r") as h:
        c = q["output/channel avg concentration"]
        j = (t_rel - tidefile_start(c.attrs["start_time"][0])) // STEP
        nums = list(h["hydro/geometry/channel_number"][:])
        area, qext = h["hydro/data/channel avg area"], h["hydro/data/qext flow"]
        i5 = (t_rel - tidefile_start(area.attrs["start_time"][0])) // 300
        names = [x.decode().strip() for x in h["hydro/geometry/external_flow_names"][:]]
        q_in = float(qext[i5 : i5 + 12, names.index("sac")].mean())  # cfs, over the pulse's hour
        released = 1000.0 * q_in * 3600.0
        out = np.zeros((n, len(watch)))
        for w, ch in enumerate(watch):
            i = nums.index(ch)
            a = area[i5 : i5 + 3 * n : 3, i]  # ft2
            out[: len(a), w] = c[j : j + len(a), 0, i] * a * length[ch] / released
    return out


def tracer_pulse(published: Path, t_rel: int, hours: int, d: float, release_ix: int, watch_ix: list[int]) -> np.ndarray:
    topo = published.parent / "pulse_topology.txt"
    subprocess.run([sys.executable, str(ROOT / "tools" / "network_topology.py"), str(published / "geometry" / "delta.json"), str(topo)],
                   check=True, capture_output=True)
    exe = ROOT / "target" / "release" / "examples" / "delta_pulse"
    r = subprocess.run([str(exe), str(topo), str(published / "delta"), str(release_ix), str(t_rel), "3600", str(hours), str(d),
                        *map(str, watch_ix)], check=True, capture_output=True, text=True)
    return np.array([[float(x) for x in line.split()[1:]] for line in r.stdout.splitlines()])


def moments(m: np.ndarray) -> tuple[float, float, float, float]:
    """(share-hours, mean time h, spread h, peak share) of one channel's series."""
    t = (np.arange(len(m)) + 1) * STEP / 3600
    s = m.sum()
    if s <= 0:
        return 0.0, float("nan"), float("nan"), 0.0
    tbar = (t * m).sum() / s
    return s * STEP / 3600, tbar, float(np.sqrt(((t - tbar) ** 2 * m).sum() / s)), float(m.max())


def main() -> None:
    run, published = Path(sys.argv[1]), Path(sys.argv[2])
    t_rel = int(datetime.fromisoformat(sys.argv[3]).replace(tzinfo=timezone.utc).timestamp())
    hours = int(sys.argv[4])
    ds = [float(x) for x in sys.argv[5].split(",")]
    watch = [int(x) for x in sys.argv[6:]]
    geo = json.loads((published / "geometry" / "delta.json").read_text())
    ids = [int(e["id"]) for e in geo["elements"]]
    release_ix = next(i for i, e in enumerate(geo["elements"]) if e["up"] == 330)  # the Sacramento's inflow junction
    n = hours * 4
    rows = {"QUAL": qual_pulse(run, geo, t_rel, n, watch)}
    for d in ds:
        rows[f"tracer D={d:g}"] = tracer_pulse(published, t_rel, hours, d, release_ix, [ids.index(c) for c in watch])[:n]
    print(f"release: into DSM2 channel {ids[release_ix]} at {sys.argv[3]} UTC, over 1 h; watched {hours} h")
    print(f"{'channel':>8s} {'model':>16s} {'share-h':>8s} {'mean h':>7s} {'spread h':>8s} {'peak':>7s}")
    for w, ch in enumerate(watch):
        for name, m in rows.items():
            sh, tb, sd, pk = moments(m[:, w])
            print(f"{ch:8d} {name:>16s} {sh:8.3f} {tb:7.1f} {sd:8.1f} {pk:7.4f}")


if __name__ == "__main__":
    main()
