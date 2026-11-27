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
                       "Specification 7.1; lag-and-route as in standard hydrological routing texts (e.g. Chow, Maidment and Mays 1988, Applied Hydrology, ch. 8; ref:chow1988)",
                       "fdmr_plugins.river_routing.model, crates/fdmr-math routing.rs")],
        "channel": [Maths("power-law-hydraulic-geometry",
                          "Cross-section area and top width as power laws of flow, fitted to each gauge's recent USGS channel measurements",
                          "Leopold and Maddock 1953, USGS Professional Paper 252 (hydraulic geometry; ref:leopold1953)",
                          "fdmr_plugins.river_routing.channel"),
                    Maths("wide-channel-uniform-flow",
                          "Where no field measurements are published: a main channel of sourced width, water-surface slope and "
                          "roughness, depth from flow by uniform flow (Chezy or Manning, hydraulic radius as depth); width held, so "
                          "valid below bankfull",
                          "Chow 1959, Open-Channel Hydraulics, chapters 5-6 (ref:chow1959); each region's width, slope and roughness "
                          "with their own sources", "fdmr_plugins.river_routing.channel.wide_channel")],
        "travel-time": [Maths("measured-release-waves",
                              "Wave scale and water factor per segment, from release changes measured arriving at the next gauge, refitted nightly",
                              "Specification 9.1", "fdmr_plugins.river_routing.calibration, wave")],
        "temperature": [Maths("surface-heat-budget",
                              "The release's measured temperature carried down at the water's speed, with the surface heat budget "
                              "(sunlight, sky and water long-wave, evaporation, conduction) under each element's nearest airport's weather, "
                              "corrected toward temperature stations",
                              "Chapra 1997, Surface Water-Quality Modeling, Lecture 30 (ref:chapra1997); cloud term as QUAL2K (ref:chapra2008qual2k); "
                              "FAO-56 clear-sky sunlight (ref:allen1998fao56)",
                              "fdmr_plugins.river_routing.heat, fdmr_runner.publish river_temperature")],
        "dispersion": [Maths("fischer-longitudinal",
                             "Longitudinal dispersion coefficient from width, depth, velocity and shear velocity; "
                             "each element also publishes how far below a release it holds (fickian_m: six Lagrangian "
                             "time scales, Fischer 1966 eq. 57, ref:fischer1966)",
                             "Fischer, List, Koh, Imberger and Brooks 1979, Mixing in Inland and Coastal Waters, ch. 5 (ref:fischer1979)",
                             "crates/fdmr-math dispersion.rs")],
        "release": [Maths("held-or-rule",
                          "A dam's release ahead: the River Forecast Center's forecast where given; else following its release forecast "
                          "upstream; else the flood-control rule (storage against the Corps' top of conservation); else held",
                          "US Army Corps of Engineers daily Flood Control Requirements (ref:usace_fcr); NOAA CNRFC (ref:cnrfc)", "fdmr_runner.releases")],
    },
    "1d-tidal-network": {
        "flow": [Maths("dsm2-hydro", "One-dimensional unsteady flow in a channel network (St Venant equations, four-point implicit scheme)",
                       "California DWR, DSM2 HYDRO (v8.5.1; ref:dsm2), run unchanged on live inputs", "fdmr_plugins.dsm2.study, fdmr_runner.delta")],
        "salinity": [Maths("dsm2-qual-ec", "Advection-dispersion of conductance (EC) along the channels and through junctions and reservoirs",
                           "California DWR, DSM2 QUAL (ref:dsm2)", "fdmr_plugins.dsm2.study")],
        "temperature": [Maths("dsm2-qual-heat", "QUAL's surface heat budget for every channel under the region's weather",
                              "California DWR, DSM2 QUAL (heat; ref:dsm2)", "fdmr_plugins.dsm2.temperature")],
        "water-sources": [Maths("qual-fingerprint", "Each inflow group a conservative tracer: its share of the water everywhere",
                                "DWR's fingerprinting method, with DSM2 QUAL (ref:dsm2)", "fdmr_plugins.dsm2.fingerprint")],
        "sediment": [Maths("gtm-cohesive-settled-bed",
                           "Suspended mud carried on HYDRO's flows by DSM2's General Transport Model; deposition ws*c, erosion "
                           "M(tau_b/tau_ce - 1) (Partheniades) from a bed that holds only what has settled on it; bed shear from "
                           "Manning's n; inflow SSC from gauges, turbidity through published Delta relations",
                           "Achete, van der Wegen, Roelvink and Jaffe 2015 (set-up and calibrated values; ref:achete2015); Partheniades 1965 "
                           "(ref:partheniades1965); California DWR, DSM2 GTM v8.5.1 (ref:dsm2) with runner/dsm2/gtm-sediment.patch",
                           "fdmr_plugins.dsm2.sediment, fdmr_runner.delta ssc_series")],
        "bound-pollutant": [Maths("partition-settle-scour",
                                  "A release that sticks to sediment splits at release by its partition coefficient, Kd c / (1 + Kd c) bound "
                                  "(c the local suspended sediment); bound parcels settle at the mud's ws / depth and are scoured back up at the "
                                  "sediment run's erosion over its settled mass; no exchange after release",
                                  "Linear equilibrium partitioning as in Chapra 1997 (ref:chapra1997); settling and scour from the sediment run "
                                  "(ref:achete2015, runner/dsm2/gtm-sediment.patch)",
                                  "crates/fdmr-compute network.rs add_bound_release, step_to")],
    },
    "2d-field": {
        "flow": [Maths("noaa-fvcom-forecast", "NOAA's operational FVCOM forecast for the bay, depth-averaged and coarsened",
                       "NOAA CO-OPS operational forecast system (ref:noaa_ofs; FVCOM: Chen, Liu and Beardsley 2003, ref:chen2003fvcom)", "fdmr_plugins.fvcom")],
        "outflow-correction": [Maths("network-outflow-match", "The mesh's net flow across its join made to carry the network's",
                                     "Specification 7.4", "fdmr_plugins.fvcom.correct")],
        "extension": [Maths("tides-plus-outflow-fit", "Past NOAA's 48 hours: at each node a least-squares fit of tidal constituents plus river outflow",
                            "Harmonic analysis as in Pawlowicz, Beardsley and Lentz 2002 (T_TIDE; ref:pawlowicz2002), extended with an outflow term; "
                            "constituent speeds from Schureman 1958 (ref:schureman1958)",
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
                            "Specification 8.1; random-walk particle tracking (e.g. Fischer et al. 1979, ref:fischer1979); well-mixed drift term "
                            "(Dimou and Adams 1993, ref:dimou1993; Visser 1997, ref:visser1997)", "crates/fdmr-math parcel.rs, crates/fdmr-compute")],
        "salinity-from-conductance": [Maths("pss-78", "Practical salinity from specific conductance", "UNESCO 1981, the Practical Salinity Scale 1978 (ref:unesco1981)",
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
