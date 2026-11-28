"""DSM2's output as published field files (fdmr_plugins.dsm2.fields, #61):
units, the 15-minute grid, Pacific Standard Time to UTC, and QUAL's EC."""

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")
fdmr_py = pytest.importorskip("fdmr_py")

from fdmr_plugins.dsm2 import fields  # noqa: E402

DAY = 86_400
T0 = 1_764_201_600  # 2025-11-27 00:00 UTC, which DSM2 writes as 2025-11-26 16:00 PST
PST_START = "2025-11-26 16:00:00"
N5 = DAY // 300 + 12  # a day of 5-minute records and an hour more
NCH = 2


def tidefile(path):
    t = np.arange(N5)
    with h5py.File(path, "w") as f:
        flow = np.zeros((N5, NCH, 2))
        flow[:, 0, :] = 100.0  # cfs, both ends
        flow[:, 1, 0], flow[:, 1, 1] = 50.0 + t, 60.0 + t  # rising by 1 cfs a record
        d = f.create_dataset("hydro/data/channel flow", data=flow)
        d.attrs["start_time"] = [PST_START.encode()]
        d.attrs["interval"] = [b"5min"]  # as HYDRO writes it (#78)
        f.create_dataset("hydro/data/channel avg area", data=np.full((N5, NCH), 1000.0))  # ft2
        f.create_dataset("hydro/data/channel stage", data=np.full((N5, NCH, 2), 10.0))  # ft of water above the bed
        f.create_dataset("hydro/geometry/channel_bottom", data=np.array([[-4.0, -6.0], [-2.0, -2.0]]))  # (2, channels), ft


def qualfile(path):
    nq = DAY // 900 + 4
    with h5py.File(path, "w") as f:
        f.create_dataset("output/constituent_names", data=np.array([b"ec"]))
        c = f.create_dataset("output/channel avg concentration", data=np.stack([np.full((nq, NCH), 800.0)], axis=1))
        c.attrs["start_time"] = [PST_START.encode()]
        c.attrs["interval"] = [b"15min"]
        f.create_dataset("output/reservoir concentration", data=np.zeros((nq, 1, 0)))


def test_hydro_and_qual_become_si_fields_on_utc_days(tmp_path):
    tidefile(tmp_path / "hydro.h5")
    qualfile(tmp_path / "qual_ec.h5")
    days = fields.write_days(tmp_path / "hydro.h5", "net", tmp_path / "out", T0, T0 + DAY, qual_file=tmp_path / "qual_ec.h5")
    assert days == ["2025-11-27"]
    reach, dims, n, layers, steps, d0, step, flds = fdmr_py.read_field_file(str(tmp_path / "out" / "2025-11-27.fdf"))
    assert (reach, n, steps, d0, step) == ("net", NCH, 96, T0, 900)
    got = {name: (unit, np.asarray(v).reshape(steps, n)) for name, unit, v in flds}
    assert {k: u for k, (u, _) in got.items()} == {"flow_up": "m3/s", "flow_down": "m3/s", "area": "m2", "level": "m"}
    cfs, ft = 0.3048**3, 0.3048
    assert got["flow_up"][1][:, 0] == pytest.approx(100 * cfs, rel=1e-6)
    # Every third 5-minute record: the k-th step is record 3k.
    assert got["flow_down"][1][:, 1] == pytest.approx((60.0 + 3 * np.arange(96)) * cfs, rel=1e-6)
    assert got["area"][1] == pytest.approx(1000 * ft**2, rel=1e-6)
    # The water surface: the bed plus the depth at each end, the mean of the two.
    assert got["level"][1][0] == pytest.approx([(10 - 3) * ft, (10 - 4) * ft], rel=1e-6)
    *_, q = fdmr_py.read_field_file(str(tmp_path / "out" / "2025-11-27.quality.fdf"))
    qual = {name: (unit, np.asarray(v)) for name, unit, v in q}
    assert qual["ec"][0] == "uS/cm" and qual["ec"][1] == pytest.approx(800.0)
    assert qual["salinity"][0] == "psu" and qual["salinity"][1] == pytest.approx(fdmr_py.practical_salinity([800.0])[0], rel=1e-6)


def test_the_tidefiles_start_time_is_pacific_standard_time():
    assert fields.tidefile_start(PST_START.encode()) == T0


def test_every_day_is_written_with_its_junctions_and_pools(tmp_path):
    """Two days through the junctions-and-pools part (#78): the tidefile's
    step is read once, and nothing in a day's loop may change it (a pool's
    heights did, so the second day failed, 28 Nov 2026)."""
    from types import SimpleNamespace

    n5 = 2 * DAY // 300 + 12
    with h5py.File(tmp_path / "hydro.h5", "w") as f:
        d = f.create_dataset("hydro/data/channel flow", data=np.full((n5, NCH, 2), 100.0))
        d.attrs["start_time"] = [PST_START.encode()]
        d.attrs["interval"] = [b"5min"]
        f.create_dataset("hydro/data/channel avg area", data=np.full((n5, NCH), 1000.0))
        f.create_dataset("hydro/data/channel stage", data=np.full((n5, NCH, 2), 10.0))
        f.create_dataset("hydro/geometry/channel_bottom", data=np.array([[-4.0, -6.0], [-2.0, -2.0]]))
        f.create_dataset("hydro/data/qext flow", data=np.full((n5, 1), -50.0))  # cfs taken out
        f.create_dataset("hydro/data/reservoir height", data=np.full((n5, 1), 3.0))  # ft
        f.create_dataset("hydro/data/reservoir flow", data=np.full((n5, 2), 20.0))
    extras = SimpleNamespace(pools=[{"id": "pool", "bottom_m": 0.0, "area_m2": 1e6}], external=[{"name": "pumps", "node": 2}])
    days = fields.write_days(tmp_path / "hydro.h5", "net", tmp_path / "out", T0, T0 + 2 * DAY,
                             node_ids=[1, 2, 3], extras=extras, exports={"pumps"})
    assert days == ["2025-11-27", "2025-11-28"]
    for day in days:
        *_, steps, _, _, flds = fdmr_py.read_field_file(str(tmp_path / "out" / f"{day}.nodes.fdf"))
        got = {name: np.asarray(v).reshape(steps, -1) for name, _, v in flds}
        assert steps == 96
        assert got["export"][:, 1] == pytest.approx(50 * 0.3048**3, rel=1e-6)  # at the pumps' junction
        assert got["volume"][:, 3] == pytest.approx(3 * 0.3048 * 1e6, rel=1e-6)  # the pool, after the junctions
