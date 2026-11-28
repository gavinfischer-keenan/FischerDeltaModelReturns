"""Reading a finished DSM2 run's output (#78): the run's name, its output
files, and series at check stations, channels and reservoirs.

Each HDF5 series is read at the step and from the start its own file records
(a TIMESERIES dataset's `interval` and `start_time` attributes: HYDRO's
tidefile every 5 minutes, QUAL's and GTM's every 15, as the study sets them),
never at an assumed step. Flows come back in m3/s (DSM2 works in cfs),
conductance in uS/cm, temperature in degC, concentrations in mg/L. A time is
UTC seconds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .dss import Dss
from .fields import interval_s, tidefile_start
from .sediment import H5 as SEDIMENT_H5

TO_CFS = 1 / 0.3048**3  # cfs per m3/s (an international foot is 0.3048 m, exact)
QUAL_EC_H5 = "qual_ec.h5"  # QUAL's conductance, as HDF5 (study.with_hdf5_output)
FINGERPRINT_H5 = "qual_fp.h5"  # the source fingerprint (fingerprint.py)
TEMPERATURE_H5 = "qual_temp.h5"  # temperature (temperature.py)
TEMPERATURE_DSS = "qual_temp.dss"
# DWR's study writes its stations' series every 15 minutes (its output_*.inp).
STATION_INTERVAL = "15MIN"


@dataclass(frozen=True)
class Series:
    """A regular series: its first time (UTC s), its step (s) and its values."""

    t0: int
    step: int
    values: np.ndarray

    def times(self) -> np.ndarray:
        return self.t0 + self.step * np.arange(len(self.values))

    def sampled(self, first: int, n: int, step: int) -> list[float | None]:
        """n values every `step` s from `first`: None where the series has none."""
        if step % self.step:
            raise ValueError(f"a {step} s step is not a whole number of this series' {self.step} s steps")
        every, j0 = step // self.step, (first - self.t0) // self.step
        out: list[float | None] = []
        for k in range(n):
            j = j0 + k * every
            out.append(float(self.values[j]) if 0 <= j < len(self.values) else None)
        return out


def run_modifier(run_dir: Path) -> str:
    """The run's name (DSM2MODIFIER in its config.inp), which names its output files."""
    m = re.search(r"^\s*DSM2MODIFIER\s+(\S+)", (Path(run_dir) / "config.inp").read_text(), re.M)
    if not m:
        raise ValueError(f"no DSM2MODIFIER in {run_dir}/config.inp")
    return m.group(1)


def output_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "output"


def tidefile(run_dir: Path) -> Path:
    """HYDRO's tidefile: every channel's flow, stage and area."""
    return output_dir(run_dir) / f"{run_modifier(run_dir)}.h5"


def _series(ds, values: np.ndarray) -> Series:
    return Series(tidefile_start(ds.attrs["start_time"][0]), interval_s(ds.attrs["interval"][0]), values)


def channel_flow(run_dir: Path, channel: int) -> Series | None:
    """A channel's flow (m3/s, the mean of its two ends, positive from its
    upstream node to its downstream one), from HYDRO's tidefile; None if the
    tidefile or the channel is not there."""
    import h5py

    path = tidefile(run_dir)
    if not path.exists():
        return None
    with h5py.File(path, "r") as f:
        nums = list(f["hydro/geometry/channel_number"][:])
        if channel not in nums:
            return None
        ds = f["hydro/data/channel flow"]
        return _series(ds, ds[:, nums.index(channel), :].mean(axis=1) / TO_CFS)


def channel_concentration(h5_path: Path, constituent: str, channel: int) -> Series | None:
    """A constituent in a channel (the mean of its two ends), from QUAL's or
    GTM's HDF5 output; None if the file, constituent or channel is not there."""
    import h5py

    if not Path(h5_path).exists():
        return None
    with h5py.File(h5_path, "r") as f:
        consts = [x.decode().strip() for x in f["output/constituent_names"][:]]
        nums = list(f["output/channel_number"][:])
        if constituent not in consts or channel not in nums:
            return None
        ds = f["output/channel concentration"]
        return _series(ds, ds[:, consts.index(constituent), nums.index(channel), :].mean(axis=1))


def reservoir_concentration(h5_path: Path, constituent: str, reservoir: str) -> Series | None:
    """A constituent in one of DSM2's reservoirs, from QUAL's or GTM's HDF5
    output; None if the file, constituent or reservoir is not there."""
    import h5py

    if not Path(h5_path).exists():
        return None
    with h5py.File(h5_path, "r") as f:
        names = [x.decode().strip() for x in f["output/reservoir_names"][:]]
        consts = [x.decode().strip() for x in f["output/constituent_names"][:]]
        if reservoir not in names or constituent not in consts:
            return None
        ds = f["output/reservoir concentration"]
        return _series(ds, ds[:, consts.index(constituent), names.index(reservoir)])


def station_series(run_dir: Path, dsstool: str, output_a: str, kind: str, station: str, first: int, n: int) -> list[float | None]:
    """A check station's series as DWR's study writes it out (DSS), n values
    every 15 minutes from `first`: kind "flow" (HYDRO, m3/s), "ec" (QUAL,
    uS/cm) or "temp" (the temperature run, degC). `output_a` is the study's
    HYDRO A part (dsm2.toml output_a)."""
    mod = run_modifier(run_dir)
    out = output_dir(run_dir)
    qual_a = output_a.replace("HYDRO", "QUAL")
    if kind == "temp":
        f, path, scale = out / TEMPERATURE_DSS, f"/{qual_a}/{station}/TEMP//{STATION_INTERVAL}/{mod.upper()}+FROM-ALL/", 1.0
    elif kind == "ec":
        f, path, scale = out / f"{mod}_qual.dss", f"/{qual_a}/{station}/EC//{STATION_INTERVAL}/{mod.upper()}+FROM-ALL/", 1.0
    elif kind == "flow":
        f, path, scale = out / f"{mod}_hydro.dss", f"/{output_a}/{station}/FLOW//{STATION_INTERVAL}/{mod.upper()}/", 1 / TO_CFS
    else:
        raise ValueError(f"no station series of kind {kind!r}")
    vals, _, _ = Dss(dsstool).read(str(f), path, first, n)
    return [None if v is None else v * scale for v in vals]


def files(run_dir: Path) -> dict[str, Path]:
    """The run's output files the published reach is written from."""
    out = output_dir(run_dir)
    return {"tidefile": tidefile(run_dir), "ec": out / QUAL_EC_H5, "fingerprint": out / FINGERPRINT_H5,
            "temperature": out / TEMPERATURE_H5, "sediment": out / SEDIMENT_H5}
