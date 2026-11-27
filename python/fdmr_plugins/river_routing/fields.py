"""The river model's fields along a reach: what the river-routing maths gives
each element at each step, beyond its flow (moved here from the publisher,
issue #57: the runner orders the reaches and writes the files; the maths is
here, where it can be tested and swapped on its own, #10).

- the friction slope between gauges (segment_slopes), for the shear velocity;
- the channel's shape along the reach (fit_shapes, shape_at): each gauge's
  by the reach's registered channel maths, and between the gauges;
- Fischer's longitudinal dispersion (dispersion_field), the lateral mixing
  (lateral_field) and how far below a release Fischer's dispersion holds
  (element_fickian); each element's width (element_widths).

What the agencies' records hold is read by the runner and passed in, as two
small functions: a gauge's water surface, and a site's field measurements.
"""

from __future__ import annotations

import time

import fdmr_py

from . import channel

DAY = 86_400
MEASUREMENTS_YEARS = 10  # a gauge's shape is fitted to its field measurements of the last this many years (FDMR's choice)


DEFAULT_SLOPE = 3e-4  # a typical lowland river, used only where no gauge pair gives one


def segment_slopes(gauges, chainage, surface) -> list[float]:
    """The friction slope between each pair of consecutive main gauges, for the
    shear velocity u* = sqrt(g d S) that goes with the depth and velocity used.

    Where both gauges' channel is the region's sourced uniform-flow channel
    (gauge `channel`), their depth and velocity come from its slope, so u* takes
    that slope too (the mean of the two): a measured surface slope there would
    pair a weir pool's flat water with the open river's depth and speed.
    Elsewhere (channel shapes from field measurements), the water-surface slope:
    the drop in (datum + median gauge height) over the distance; a gauge
    reporting its water level against the region's datum itself
    (Rijkswaterstaat's, in m NAP) gives it directly. Missing or implausible
    slopes take a neighbour's, or a default. `surface(gauge)` gives a gauge's
    water-surface elevation (m, the region's datum) or None: the runner's
    reading of the agencies' records (fdmr_runner.publish)."""
    pairs = list(zip(gauges, gauges[1:]))
    sourced = [(a.channel["slope"] + b.channel["slope"]) / 2 if a.channel and b.channel else None for a, b in pairs]
    if all(s is not None for s in sourced):
        return sourced
    measured = _surface_slopes(gauges, chainage, surface)
    return [s if s is not None else m for s, m in zip(sourced, measured)]


def _surface_slopes(gauges, chainage, surface) -> list[float]:
    """Water-surface slope between each pair of consecutive main gauges (see segment_slopes)."""
    z = [surface(g) for g in gauges]
    s = []
    for (g1, z1), (g2, z2) in zip(zip(gauges, z), list(zip(gauges, z))[1:]):
        dist = chainage[g2.site] - chainage[g1.site]
        slope = (z1 - z2) / dist if z1 is not None and z2 is not None and dist > 0 else None
        s.append(slope if slope is not None and 1e-6 < slope < 0.05 else None)
    for i, v in enumerate(s):  # fill gaps from the nearest segment that has a slope
        if v is None:
            near = [x for x in sorted(range(len(s)), key=lambda j: abs(j - i)) if s[x] is not None]
            s[i] = s[near[0]] if near else DEFAULT_SLOPE
    return s


def shape_at(records, x: float):
    """(segment, channel shape) at chainage x: the shapes fitted at the gauges
    either side, taken between them; a reach with one gauge has its shape throughout."""
    recs = sorted(records, key=lambda r: r.chainage)
    if len(recs) == 1:
        return 0, recs[0].shape
    chain = [r.chainage for r in recs]
    k = max(0, min(len(chain) - 2, sum(1 for c in chain[1:-1] if c <= x)))
    span = chain[k + 1] - chain[k]
    f = 0.0 if span <= 0 else min(1.0, max(0.0, (x - chain[k]) / span))
    return k, channel.between(recs[k].shape, recs[k + 1].shape, f)


def dispersion_field(field, records, slopes):
    """Fischer's longitudinal dispersion for every element and step, m2/s
    (specification section 7.1): K = 0.011 U^2 W^2 / (d u*), u* = sqrt(g d S).
    A reach with no gauge pair to give a slope takes the default. Each
    element's steps are worked out together (#35), by the same maths."""
    import numpy as np

    flow, vel = np.asarray(field.flow, dtype=float), np.asarray(field.velocity, dtype=float)
    out = np.zeros((field.steps, len(field.elements)))
    for e, el in enumerate(field.elements):
        k, shape = shape_at(records, el.mid)
        slope = slopes[min(k, len(slopes) - 1)] if slopes else DEFAULT_SLOPE
        q = flow[:, e]
        d, w = shape.depth(q), shape.width(q)
        out[:, e] = fdmr_py.fischer_ks(vel[:, e].tolist(), w.tolist(), d.tolist(), fdmr_py.shear_velocities(d.tolist(), slope))
    return out


ELDER_LATERAL = 0.23  # lateral mixing eps = 0.23 d u*, Elder's, as Fischer 1966 adopts it (ref:fischer1966, eq. 23)


def lateral_field(field, records, slopes):
    """Lateral mixing for every element and step, m2/s: eps = 0.23 d u*
    (Fischer 1966, ref:fischer1966, section II-F), u* = sqrt(g d S), the same
    depth and slope as the dispersion coefficient. With the element's width it
    sets how fast a parcel wanders across the channel (the engine's shear
    spreading, issue #29)."""
    import numpy as np

    flow = np.asarray(field.flow, dtype=float)
    out = np.zeros((field.steps, len(field.elements)))
    for e, el in enumerate(field.elements):
        k, shape = shape_at(records, el.mid)
        slope = slopes[min(k, len(slopes) - 1)] if slopes else DEFAULT_SLOPE
        d = shape.depth(np.maximum(flow[:, e], 1e-3))
        out[:, e] = ELDER_LATERAL * d * np.asarray(fdmr_py.shear_velocities(d.tolist(), slope))
    return out


def element_fickian(field, records, slopes) -> list[float]:
    """How far below a release in each element Fischer's dispersion holds, m,
    at the element's median flow: U times six of Fischer's Lagrangian time
    scales, 1.8 U l^2 / (r u*) (Fischer 1966, ref:fischer1966, eq. 57: Taylor's
    theory holds only past t/T = 6). l is the half width, Fischer's length for a
    symmetric channel (an asymmetric one, its fastest water nearer one bank,
    takes longer, up to four times); r is the mean depth, as for K above."""
    out = []
    for e, el in enumerate(field.elements):
        k, shape = shape_at(records, el.mid)
        slope = slopes[min(k, len(slopes) - 1)] if slopes else DEFAULT_SLOPE
        i = sorted(range(field.steps), key=lambda i: field.flow[i][e])[field.steps // 2]
        q = max(field.flow[i][e], 1e-3)
        d, w, u = shape.depth(q), shape.width(q), abs(field.velocity[i][e])
        out.append(round(fdmr_py.fickian_distance(u, w / 2, d, fdmr_py.shear_velocity(d, slope))))
    return out


def element_widths(field, records) -> list[float]:
    """Each element's water-surface width, m, at its median flow over the
    published steps: the channel shape fitted at the gauges either side
    (USGS field measurements), taken between them as the routing does."""
    out = []
    for e, el in enumerate(field.elements):
        _, shape = shape_at(records, el.mid)
        q = sorted(field.flow[i][e] for i in range(field.steps))[field.steps // 2]
        out.append(round(shape.width(max(q, 1e-3)), 1))
    return out


POWER_LAW, WIDE_CHANNEL = "power-law-hydraulic-geometry", "wide-channel-uniform-flow"  # the registered channel maths


def channel_method(region, reach: str) -> str:
    """The channel maths the reach runs, as the register resolves its choice
    (fdmr_plugins.registry, #10): what runs is what index.json records (#64)."""
    from fdmr_plugins import registry

    chosen = next((r.get("maths") for r in region.reaches if r["id"] == reach), None)
    return registry.choices("river-routing", chosen)["channel"].name


def fit_shapes(gauges, chainage, measurements, method: str = POWER_LAW, now: float | None = None) -> dict:
    """Channel shape at each gauge, by the reach's registered channel maths:
    - power-law-hydraulic-geometry: from the gauge's USGS field measurements
      of the last ten years (for another agency's station, those of the USGS
      site at the same place, of any age if none are recent);
    - wide-channel-uniform-flow: the gauge's sourced main channel (its
      `channel` table in region.toml), where no measurements are published.
    A gauge with no shape of its own takes the nearest gauge's. A gauge whose
    `channel` table disagrees with the reach's choice stops the reach, rather
    than running other maths than the cycle records (#64). `measurements(site)`
    gives a site's field measurements (the runner's store)."""
    since = int(time.time() if now is None else now) - MEASUREMENTS_YEARS * 365 * DAY
    if method not in (POWER_LAW, WIDE_CHANNEL):
        raise ValueError(f"no channel shapes for maths {method!r}")
    if method == POWER_LAW and any(g.channel for g in gauges):
        raise ValueError(f"gauges {[g.site for g in gauges if g.channel]} give a `channel` table, but their reach runs "
                         f"{POWER_LAW}: choose {WIDE_CHANNEL} for the reach in region.toml `maths`")

    def fit(g):
        if method == WIDE_CHANNEL:
            if not g.channel:
                return None
            c = g.channel
            return channel.wide_channel(c["width_m"], c["slope"], chezy=c.get("chezy"), manning_n=c.get("manning_n"))
        if not g.place_site:
            return None
        ms = measurements(g.place_site)
        return channel.fit(ms, since=since) or (channel.fit(ms) if g.place_site != g.site else None)

    shapes = {g.site: fit(g) for g in gauges}
    if not any(shapes.values()):
        raise ValueError("no gauge has a `channel` table" if method == WIDE_CHANNEL else "no gauge has enough channel measurements")
    return {
        g.site: shapes[g.site] or min(
            ((abs(chainage[o.site] - chainage[g.site]), shapes[o.site]) for o in gauges if shapes[o.site]),
            key=lambda x: x[0],
        )[1]
        for g in gauges
    }
