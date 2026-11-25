from calendar import timegm

import numpy as np
import pytest

from fdmr_adapters import noaa_ofs

h5py = pytest.importorskip("h5py")
pytest.importorskip("scipy")

from fdmr_plugins.fvcom import mesh  # noqa: E402
from fdmr_plugins.fvcom.layout import read_layout  # noqa: E402

CYCLES = [3, 9, 15, 21]
DAY = timegm((2026, 11, 24, 0, 0, 0))


def test_each_hour_comes_from_one_file():
    latest = DAY + 15 * 3600
    assert noaa_ofs.source_of(DAY + 15 * 3600, CYCLES, latest) == (DAY + 15 * 3600, "n", 6)
    assert noaa_ofs.source_of(DAY + 10 * 3600, CYCLES, latest) == (DAY + 15 * 3600, "n", 1)
    assert noaa_ofs.source_of(DAY + 18 * 3600, CYCLES, latest) == (latest, "f", 3)
    # Late in the day: the next day's first cycle.
    assert noaa_ofs.source_of(DAY - 2 * 3600, CYCLES, latest) == (DAY + 3 * 3600, "n", 1)
    url = noaa_ofs.file_url("sfbofs", DAY + 3 * 3600, "f", 7)
    assert url.endswith("sfbofs/netcdf/2026/11/24/sfbofs.t03z.20261124.fields.f007.nc")


def test_variables_are_cut_exactly_from_a_byte_range(tmp_path):
    p = tmp_path / "f.nc"
    rng = np.random.default_rng(1)
    u = rng.normal(size=(1, 4, 10)).astype("<f4")
    zeta = rng.normal(size=(1, 7)).astype("<f4")
    with h5py.File(p, "w") as f:
        f.create_dataset("time", data=np.array([4.3e8]))
        f.create_dataset("zeta", data=zeta)
        f.create_dataset("u", data=u, chunks=(1, 3, 4))  # chunks that do not divide the shape
    layout = read_layout(p, ("time", "zeta", "u"))
    lo, hi = layout.span(("time", "zeta", "u"))
    block = p.read_bytes()[lo:hi]
    assert np.array_equal(layout.decode("u", block, lo), u)
    assert np.array_equal(layout.decode("zeta", block, lo), zeta)
    assert layout.decode("time", block, lo)[0] == 4.3e8
    from fdmr_plugins.fvcom.layout import Layout

    assert Layout.from_json(layout.to_json()) == layout


def grid_mesh(nx: int = 30, ny: int = 12, d: float = 100.0) -> mesh.Fine:
    xs, ys = np.meshgrid(np.arange(nx) * d, np.arange(ny) * d)
    x, y = xs.ravel(), ys.ravel()
    tri = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b, c, e = j * nx + i, j * nx + i + 1, (j + 1) * nx + i + 1, (j + 1) * nx + i
            tri += [[a, b, c], [a, c, e]]
    tri = np.array(tri)
    return mesh.Fine(x=x, y=y, lon=-122 + x / 1e5, lat=38 + y / 1e5, tri=tri, depth=5 + y / 100, node_area=np.full(len(x), d * d))


def test_a_coarse_mesh_covers_the_water_and_averages_exactly():
    fine = grid_mesh()
    c = mesh.coarsen(fine, np.ones(len(fine.tri), bool), k=2.0, min_m=150.0, max_m=1000.0)
    assert 20 < len(c.x) < len(fine.x)
    a = mesh.tri_area(c.x, c.y, c.tri)
    assert a.sum() > 0.9 * mesh.tri_area(fine.x, fine.y, fine.tri).sum()
    # Counter-clockwise, every one.
    cross = (c.x[c.tri[:, 1]] - c.x[c.tri[:, 0]]) * (c.y[c.tri[:, 2]] - c.y[c.tri[:, 0]]) - (c.x[c.tri[:, 2]] - c.x[c.tri[:, 0]]) * (c.y[c.tri[:, 1]] - c.y[c.tri[:, 0]])
    assert (cross > 0).all()
    # Means of a constant are that constant; every coarse node gets a value.
    assert np.allclose(mesh.node_mean(c, np.full(len(fine.x), 2.5)), 2.5)
    assert np.allclose(mesh.element_mean(c, np.full(len(fine.tri), -0.4)), -0.4)
    assert not np.isnan(c.depth).any()
    # Depth follows the fine depth (5 m plus 1 m per 100 m north).
    assert np.allclose(c.depth, 5 + c.y / 100, atol=1.5)


def test_boundary_edges_are_classified_by_the_region():
    fine = grid_mesh()
    c = mesh.coarsen(fine, np.ones(len(fine.tri), bool), k=2.0, min_m=150.0, max_m=1000.0)
    lon0, lon1 = c.lon.min(), c.lon.max()
    edges = mesh.classify(c, {"west_of": lon0 + 1e-6, "deeper_than_m": 0.0}, [{"id": "up", "east_of": lon1 - 1e-6}])
    assert edges["open"] and edges["up"]
    assert all(c.lon[a] <= lon0 + 1e-6 and c.lon[b] <= lon0 + 1e-6 for a, b in edges["open"])
    assert all(c.lon[a] >= lon1 - 1e-6 for e in edges["up"] for a in e)
