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


def test_the_fit_recovers_tides_and_drift_from_two_hourly_samples():
    from fdmr_plugins.fvcom import extend

    t = np.arange(0, 365 * 86_400, 7200) + 1_760_000_000
    q = 300 + 200 * np.sin(2 * np.pi * (t - t[0]) / (365 * 86_400))  # a season of outflow
    h = (t - extend.EPOCH) / 3600
    w = lambda n: np.radians(extend.SPEEDS[n]) * h  # noqa: E731
    truth = 0.1 + 0.0004 * q + 0.8 * np.cos(w("M2") - 1.0) + 0.3 * np.cos(w("K1") + 0.5) + 0.05 * np.cos(w("M4"))
    y = np.column_stack([truth, -truth]) + np.random.default_rng(3).normal(0, 0.02, (len(t), 2))
    f = extend.fit(t, q, {"u": y})
    assert f.skill["u"].min() > 0.99
    # The drift per m3/s, and the forecast two weeks on.
    assert f.coef["u"][1, 0] / extend.Q_SCALE == pytest.approx(0.0004, rel=0.02)
    t2 = t[-1] + np.arange(1, 14 * 12) * 7200
    q2 = np.full(len(t2), 250.0)
    h2 = (t2 - extend.EPOCH) / 3600
    w2 = lambda n: np.radians(extend.SPEEDS[n]) * h2  # noqa: E731
    want = 0.1 + 0.0004 * 250 + 0.8 * np.cos(w2("M2") - 1.0) + 0.3 * np.cos(w2("K1") + 0.5) + 0.05 * np.cos(w2("M4"))
    got = f.predict(t2, q2)["u"][:, 0]
    assert np.abs(got - want).max() < 0.01


def test_the_extension_joins_the_forecast_without_a_jump():
    from fdmr_plugins.fvcom import extend

    pred = {"u": np.full((10, 2), 1.0)}
    out = extend.join({"u": np.array([1.5, 0.5])}, {"u": np.array([1.0, 1.0])}, pred, np.arange(10) * 1.0, blend_h=6)
    assert out["u"][0].tolist() == [1.5, 0.5] and out["u"][6].tolist() == [1.0, 1.0] and out["u"][9].tolist() == [1.0, 1.0]
    assert out["u"][3].tolist() == pytest.approx([1.25, 0.75])


def test_a_saved_fit_predicts_the_same(tmp_path):
    from fdmr_plugins.fvcom import extend

    t = np.arange(0, 60 * 86_400, 7200) + 1_760_000_000
    f = extend.fit(t, np.full(len(t), 100.0) + (t % 86_400) / 1000, {"u": np.cos(t / 9000.0)[:, None], "level": np.sin(t / 7000.0)[:, None]})
    f.save(tmp_path / "fit.npz")
    g = extend.Fit.load(tmp_path / "fit.npz")
    assert g.names == f.names and g.t1 == f.t1
    tt, qq = t[:5] + 1e6, np.full(5, 120.0)
    assert np.allclose(g.predict(tt, qq)["u"], f.predict(tt, qq)["u"])


def test_the_recent_offset_is_the_fits_mean_miss():
    from fdmr_plugins.fvcom import extend

    t = np.arange(0, 60 * 86_400, 7200) + 1_760_000_000
    h = (t - extend.EPOCH) / 3600
    y = 0.5 * np.cos(np.radians(extend.SPEEDS["M2"]) * h)
    f = extend.fit(t, np.zeros(len(t)), {"level": y[:, None]})
    recent = t[-36:]
    obs = y[-36:, None] + 0.3  # the sea has risen 0.3 m since
    off = extend.anomaly(f, recent, np.zeros(36), {"level": obs})
    assert off["level"][0] == pytest.approx(0.3, abs=0.01)


def test_the_outflow_correction_in_a_channel_is_q_over_the_cross_section():
    from fdmr_plugins.fvcom import correct

    fine = grid_mesh(nx=40, ny=6, d=50.0)  # 1950 m long, 250 m wide; depth 5 m + 1 m per 100 m north
    fine.depth = np.full(len(fine.x), 4.0)
    c = mesh.Coarse(fine_node=np.arange(len(fine.x)), lon=fine.lon, lat=fine.lat, x=fine.x, y=fine.y, tri=fine.tri.copy(),
                    depth=fine.depth, el_owner=np.zeros(0, int), node_owner=np.zeros(0, int), el_area=np.zeros(0), node_area=np.zeros(0))
    nx = 40
    join = [[j * nx + nx - 1, (j + 1) * nx + nx - 1] for j in range(5)]  # the east end: water comes in here
    sea = [[j * nx, (j + 1) * nx] for j in range(5)]  # the west end: out to sea
    u, v = correct.unit_outflow(c, join, sea)
    inner = (fine.x > 200) & (fine.x < 1700)
    # 1 m3/s through 250 m x 4 m, westward.
    assert np.allclose(u[inner], -1 / (250 * 4), rtol=0.02)
    assert np.abs(v[inner]).max() < 1e-6


def test_a_running_mean_holds_its_last_full_window_at_the_ends():
    from fdmr_plugins.fvcom.correct import running_mean

    x = np.r_[np.full(50, 1.0), np.full(5, 100.0)]  # a spike in the last few samples
    m = running_mean(x, 20)
    assert m[0] == pytest.approx(1.0) and m[-1] == m[-11]  # held from the last full window
    assert running_mean(np.arange(5.0), 20).tolist() == [2.0] * 5


def test_the_tidal_mean_removes_the_tides():
    from fdmr_plugins.fvcom import extend
    from fdmr_plugins.fvcom.correct import tidal_mean

    t = np.arange(0, 30 * 86_400, 3600)
    h = t / 3600
    tide = 7000 * np.cos(np.radians(extend.SPEEDS["M2"]) * h) + 3000 * np.cos(np.radians(extend.SPEEDS["K1"]) * h)
    net = 250 + 100 * np.sin(2 * np.pi * t / (14.77 * 86_400))  # spring-neap filling and draining
    m = tidal_mean(net + tide, 1.0)
    inner = slice(80, -80)
    assert np.abs(m[inner] - net[inner]).max() < 25


def test_misfits_spread_through_the_water_not_across_land():
    from fdmr_plugins.fvcom import correct

    # A U of water: two arms joined at the east end, land between them.
    fine = grid_mesh(nx=20, ny=11, d=100.0)
    keep = ~((fine.x[fine.tri].mean(axis=1) < 1500) & (np.abs(fine.y[fine.tri].mean(axis=1) - 500) < 300))
    tri = fine.tri[keep]
    c = mesh.Coarse(fine_node=np.arange(len(fine.x)), lon=fine.lon, lat=fine.lat, x=fine.x, y=fine.y, tri=tri,
                    depth=np.full(len(fine.x), 4.0), el_owner=np.zeros(0, int), node_owner=np.zeros(0, int), el_area=np.zeros(0), node_area=np.zeros(0))
    south, north = 0, 10 * 20  # the west ends of the two arms, 1000 m apart across the land
    d = correct.water_distances(c, [south, north])
    # Through the water the way round is far longer than the 1000 m across.
    assert d[0, north] > 2500 and d[0, south] == 0
    corr = correct.spread(d, np.array([2.0, -1.0]))
    # Each arm's west end follows its own station.
    # (the station round the U still counts a little: 5% here)
    assert corr[south] == pytest.approx(2.0, abs=0.15) and corr[north] == pytest.approx(-1.0, abs=0.15)
    # A station with no misfit that day is left out.
    assert np.allclose(correct.spread(d, np.array([2.0, np.nan]))[[south, north]], 2.0)


def test_the_flow_across_a_meshs_edges_is_velocity_times_length_times_depth():
    """fdmr_plugins.fvcom.flux (#58): 0.5 m/s westward through the 250 m west end,
    4 m deep, is 500 m3/s out of the mesh; the sum over the edges is the old
    edge-by-edge loop's, exactly."""
    from fdmr_plugins.fvcom import flux

    fine = grid_mesh(nx=40, ny=6, d=50.0)
    fine.depth = np.full(len(fine.x), 4.0)
    c = mesh.Coarse(fine_node=np.arange(len(fine.x)), lon=fine.lon, lat=fine.lat, x=fine.x, y=fine.y, tri=fine.tri.copy(),
                    depth=fine.depth, el_owner=np.zeros(0, int), node_owner=np.zeros(0, int), el_area=np.zeros(0), node_area=np.zeros(0))
    nx = 40
    west = [[j * nx, (j + 1) * nx] for j in range(5)]
    n = len(fine.x)
    rng = np.random.default_rng(3)
    hours = {0: {"u": np.full(n, -0.5), "v": np.zeros(n), "level": np.zeros(n)},
             3600: {"u": rng.normal(size=n), "v": rng.normal(size=n), "level": rng.normal(scale=0.3, size=n)}}
    ts, q = flux.mesh_outflow(c, west, hours)
    assert list(ts) == [0, 3600] and q[0] == pytest.approx(0.5 * 250 * 4)
    en = flux.edge_normals(c, west)
    old = 0.0
    d = hours[3600]
    for a, b, nxx, nyy in en:  # the loop bay.mesh_outflow used to run
        a, b = int(a), int(b)
        u, v = (d["u"][a] + d["u"][b]) / 2, (d["v"][a] + d["v"][b]) / 2
        h = (c.depth[a] + d["level"][a] + c.depth[b] + d["level"][b]) / 2
        old += (u * nxx + v * nyy) * max(h, 0.0)
    assert q[1] == old


def test_a_tidal_mean_compared_as_one_has_no_held_ends():
    # #71: within 36.5 h of either end no full tidal window fits; there the
    # values are missing, not the nearest full-window value held.
    import fdmr_py

    from fdmr_plugins.fvcom.correct import tidal_mean

    t = np.arange(24 * 4 * 10) / 4.0  # ten days at four an hour
    x = 50 + 20 * np.sin(2 * np.pi * t / 12.42) + t / 10
    held, missing = tidal_mean(x, 4.0), tidal_mean(x, 4.0, edges="missing")
    e = fdmr_py.godin_edge(4.0)
    assert e == 146  # 36.5 hours
    assert np.isnan(missing[:e]).all() and np.isnan(missing[-e:]).all()
    assert np.isfinite(missing[e:-e]).all()
    assert np.array_equal(missing[e:-e], held[e:-e])
    with pytest.raises(ValueError):
        tidal_mean(x, 4.0, edges="trimmed")
