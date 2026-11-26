"""Which maths runs in which kind of water (issue #10): the register.

Every model kind (a reach's `kind` in region.toml) is made of parts: how its
flow is found, how heat and salt move, how particles are carried, and so on.
For each part this register names the maths that does it: what it is, where
it comes from, and the code that runs it. A region may choose, per reach, a
different registered implementation of a part (region.toml, the reach's
`maths = { part = "name" }`); an unknown choice is refused, never swapped
silently. Each cycle records the maths every reach ran (index.json, a reach's
`maths`), and the documentation (#20) is written from this register, so the
two cannot drift apart.

To add maths: write it, register it here under its kind and part with its
source, and give the runner a dispatch for it. A part with one entry has no
alternative yet.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Maths:
    name: str
    what: str
    source: str
    code: str


REGISTER: dict[str, dict[str, list[Maths]]] = {
    "river-routing": {
        "flow": [Maths("gauge-blend-lag-route",
                       "Flow between two gauges as a blend of their records, each shifted by the flood wave's travel time; "
                       "beyond the last gauge, variable-parameter lag-and-route (a delay set by flow, then a linear reservoir)",
                       "Specification 7.1; lag-and-route as in standard hydrological routing texts (e.g. Chow, Maidment and Mays 1988, Applied Hydrology, ch. 8)",
                       "fdmr_plugins.river_routing.model, crates/fdmr-math routing.rs")],
        "channel": [Maths("power-law-hydraulic-geometry",
                          "Cross-section area and top width as power laws of flow, fitted to each gauge's recent USGS channel measurements",
                          "Leopold and Maddock 1953, USGS Professional Paper 252 (hydraulic geometry)",
                          "fdmr_plugins.river_routing.channel")],
        "travel-time": [Maths("measured-release-waves",
                              "Wave scale and water factor per segment, from release changes measured arriving at the next gauge, refitted nightly",
                              "Specification 9.1", "fdmr_plugins.river_routing.calibration, wave")],
        "temperature": [Maths("surface-heat-budget",
                              "The release's measured temperature carried down at the water's speed, with the surface heat budget "
                              "(sunlight, sky and water long-wave, evaporation, conduction) under each element's nearest airport's weather, "
                              "corrected toward temperature stations",
                              "Chapra 1997, Surface Water-Quality Modeling, Lecture 30; cloud term as QUAL2K; FAO-56 clear-sky sunlight",
                              "fdmr_plugins.river_routing.heat, fdmr_runner.publish river_temperature")],
        "dispersion": [Maths("fischer-longitudinal",
                             "Longitudinal dispersion coefficient from width, depth, velocity and shear velocity",
                             "Fischer, List, Koh, Imberger and Brooks 1979, Mixing in Inland and Coastal Waters, ch. 5",
                             "crates/fdmr-math dispersion.rs")],
        "release": [Maths("held-or-rule",
                          "A dam's release ahead: the River Forecast Center's forecast where given; else following its release forecast "
                          "upstream; else the flood-control rule (storage against the Corps' top of conservation); else held",
                          "US Army Corps of Engineers daily Flood Control Requirements; NOAA CNRFC", "fdmr_runner.releases")],
    },
    "1d-tidal-network": {
        "flow": [Maths("dsm2-hydro", "One-dimensional unsteady flow in a channel network (St Venant equations, four-point implicit scheme)",
                       "California DWR, DSM2 HYDRO (v8.5.1), run unchanged on live inputs", "fdmr_plugins.dsm2.study, fdmr_runner.delta")],
        "salinity": [Maths("dsm2-qual-ec", "Advection-dispersion of conductance (EC) along the channels and through junctions and reservoirs",
                           "California DWR, DSM2 QUAL", "fdmr_plugins.dsm2.study")],
        "temperature": [Maths("dsm2-qual-heat", "QUAL's surface heat budget for every channel under the region's weather",
                              "California DWR, DSM2 QUAL (heat)", "fdmr_plugins.dsm2.temperature")],
        "water-sources": [Maths("qual-fingerprint", "Each inflow group a conservative tracer: its share of the water everywhere",
                                "DWR's fingerprinting method, with DSM2 QUAL", "fdmr_plugins.dsm2.fingerprint")],
        "sediment": [Maths("dsm2-gtm-sediment", "Suspended sediment with deposition and erosion of a bed (cohesive and non-cohesive), in DSM2's General Transport Model",
                           "California DWR, DSM2 GTM (v8.5.1 sources): sediment, sediment_bed modules", "not yet run (issue #16)")],
    },
    "2d-field": {
        "flow": [Maths("noaa-fvcom-forecast", "NOAA's operational FVCOM forecast for the bay, depth-averaged and coarsened",
                       "NOAA CO-OPS operational forecast system (FVCOM: Chen, Liu and Beardsley 2003)", "fdmr_plugins.fvcom")],
        "outflow-correction": [Maths("network-outflow-match", "The mesh's net flow across its join made to carry the network's",
                                     "Specification 7.4", "fdmr_plugins.fvcom.correct")],
        "extension": [Maths("tides-plus-outflow-fit", "Past NOAA's 48 hours: at each node a least-squares fit of tidal constituents plus river outflow",
                            "Harmonic analysis as in Pawlowicz, Beardsley and Lentz 2002 (T_TIDE), extended with an outflow term",
                            "fdmr_plugins.fvcom.extend")],
        "quality-correction": [Maths("station-misfit-spread", "Each station's daily misfit spread along the water by inverse distance squared; left-out checks",
                                     "Specification 7.5", "fdmr_runner.bay correct_quality, fdmr_plugins.fvcom.correct")],
    },
    "canal-network": {
        "flow": [Maths("export-carried", "The network's own export at the intake, the same all along the canal", "Specification 7.4", "fdmr_runner.canals")],
        "quality": [Maths("intake-lagged", "The intake's water quality carried down at the water's travel time", "Specification 7.4", "fdmr_runner.canals")],
    },
    "all": {
        "particles": [Maths("advection-random-walk", "Parcels carried by the local velocity plus a random step for dispersion",
                            "Specification 8.1; random-walk particle tracking (e.g. Fischer et al. 1979)", "crates/fdmr-math parcel.rs, crates/fdmr-compute")],
        "salinity-from-conductance": [Maths("pss-78", "Practical salinity from specific conductance", "UNESCO 1981, the Practical Salinity Scale 1978",
                                            "crates/fdmr-math salinity.rs")],
        "decay": [Maths("first-order", "First-order decay by half-life", "Specification 3.3", "crates/fdmr-math decay.rs")],
    },
}


def choices(kind: str, chosen: dict | None = None) -> dict[str, Maths]:
    """The maths a reach of this kind runs, part by part: the first registered
    for each part unless the reach chose another. An unknown kind, part or
    name is an error."""
    if kind not in REGISTER:
        raise ValueError(f"no maths registered for kind {kind!r}")
    parts = {**REGISTER[kind], **REGISTER["all"]}
    out = {p: impls[0] for p, impls in parts.items()}
    for part, name in (chosen or {}).items():
        if part not in parts:
            raise ValueError(f"kind {kind!r} has no part {part!r}")
        match = [m for m in parts[part] if m.name == name]
        if not match:
            raise ValueError(f"no maths {name!r} registered for {kind!r} {part!r}: {[m.name for m in parts[part]]}")
        out[part] = match[0]
    return out


def record(kind: str, chosen: dict | None = None) -> dict:
    """The maths a reach ran, for its index.json entry: part -> {name, source}."""
    return {p: {"name": m.name, "source": m.source} for p, m in choices(kind, chosen).items()}
