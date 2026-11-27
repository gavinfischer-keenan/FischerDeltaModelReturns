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
source, give the runner a dispatch for it, and name the standard problems it
passes (CHECKS below). A part with one entry has no alternative yet.

The standard problems (the test harness, #10): every registered maths names
the tests it must pass, as `path::test` (a Python test or a Rust test
function), such as pure advection, a dispersing pulse recovering its
dispersion coefficient, mass kept, a known textbook value; an external model
(DSM2, NOAA's forecast) names the checks against stations it is judged by in
every cycle, as `check: ...`. A replacement passes the same problems, then is
judged against today's choice on the nightly hindcasts (#9).

Candidates (issue #26): maths or settings that cannot be sourced yet are not
registered and never run live. They are listed in CANDIDATES, with what is
known and why each is not in, so the documentation shows them beside the
maths that runs. A registered alternative can be tried on a hindcast,
labelled and scored apart (`fdmr_runner.trial`); adopting anything stays a
change made by hand, with its source.
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
                          "Cross-section area and top width as power laws of flow, fitted to each gauge's recent USGS channel measurements; "
                          "a hinged power law (two regimes joined at a break flow) where the measurements show one by more than 10 in BIC",
                          "Leopold and Maddock 1953, USGS Professional Paper 252 (hydraulic geometry; ref:leopold1953); the hinge's "
                          "evidence threshold Kass and Raftery 1995 (ref:kassraftery1995), adopted on held-out measurements "
                          "(empirical, FDMR: tools/validation/channel_hinge)",
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
        "dispersion": [Maths("cross-channel-shear",
                             "Parcels spread by the mechanism behind the dispersion coefficient: each has a place across the channel, "
                             "moves downstream at the speed there (a parabolic profile, U (1 + c (1/3 - eta^2)), c set so that Taylor's "
                             "long-time K is Fischer's), and wanders across at Elder's lateral mixing 0.23 d u*, reflected at the banks; "
                             "far from a release it spreads at Fischer's K, near it as lopsided as the shear makes it",
                             "Fischer 1966, Longitudinal Dispersion in Laboratory and Natural Streams, sections II-F and III-F, eqs. 23, "
                             "40 and 57 (ref:fischer1966); Taylor's integral for the profile (FDMR, crates/fdmr-math dispersion.rs)",
                             "crates/fdmr-math dispersion.rs shear_profile_c, crates/fdmr-compute lib.rs, fdmr_runner.publish lateral_field"),
                       Maths("fischer-longitudinal",
                             "Longitudinal dispersion coefficient from width, depth, velocity and shear velocity; "
                             "each element also publishes how far below a release it holds (fickian_m: six Lagrangian "
                             "time scales, Fischer 1966 eq. 57, ref:fischer1966)",
                             "Fischer, List, Koh, Imberger and Brooks 1979, Mixing in Inland and Coastal Waters, ch. 5 (ref:fischer1979)",
                             "crates/fdmr-math dispersion.rs")],
        "dead-zone-storage": [Maths("groyne-field-storage",
                                    "Where the region gives a reach groyne fields: parcels are held in the water between the groynes, "
                                    "into it at a fixed exchange rate and out at that rate over the dead-zone ratio beta, so a cloud lags "
                                    "the water by beta; beta and the velocity head follow the discharge at the place's gauge, and the "
                                    "exchange is switched off below a velocity head of 0.01 m, fully on above 0.02 m. Does nothing elsewhere",
                                    "van Mazijk 1996, section 4.4, Figs 4.4.10-4.4.15, p. 217 (ref:vanmazijk1996); the exchange rate "
                                    "1e-5/s empirical, FDMR (fitted on one measured cloud, judged on another branch's: tools/validation/rhine_dye)",
                                    "fdmr_plugins.river_routing.storage, fdmr_runner.publish storage_field, crates/fdmr-compute lib.rs")],
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


# The standard problems each registered maths passes (module docstring).
CHECKS: dict[str, tuple[str, ...]] = {
    "gauge-blend-lag-route": (
        "python/tests/test_model.py::test_steady_flow_is_matched_everywhere",
        "python/tests/test_model.py::test_a_release_change_moves_downstream",
        "crates/fdmr-math/src/routing.rs::steady_flow_passes_unchanged",
        "crates/fdmr-math/src/routing.rs::pure_lag_delays_a_step_by_the_travel_time",
        "crates/fdmr-math/src/routing.rs::attenuation_smooths_but_conserves_volume",
    ),
    "power-law-hydraulic-geometry": (
        "crates/fdmr-math/src/geometry.rs::recovers_an_exact_power_law",
        "python/tests/test_channel.py::test_recovers_shape_and_velocity",
        "python/tests/test_channel.py::test_a_floodplain_is_fitted_with_a_hinge",
        "python/tests/test_channel.py::test_one_regime_stays_a_single_power_law",
    ),
    "wide-channel-uniform-flow": ("python/tests/test_channel.py::test_wide_channel_is_uniform_flow_in_a_fixed_width",),
    "measured-release-waves": (
        "python/tests/test_wave.py::test_recovers_a_known_travel_time_law",
        "python/tests/test_calibration.py::test_tracer_events_find_the_water_travel_time",
    ),
    "surface-heat-budget": (
        "python/tests/test_heat.py::test_with_no_heat_exchange_the_release_temperature_is_carried_down_at_the_water_speed",
        "python/tests/test_heat.py::test_water_far_warmer_than_still_dry_night_air_cools",
        "python/tests/test_heat.py::test_vapour_pressure_is_the_textbook_value",
        "check: river temperature against its stations, each cycle (check.html)",
    ),
    "cross-channel-shear": (
        "crates/fdmr-math/src/dispersion.rs::the_shear_profile_gives_fischers_k_by_taylors_integral",
        "crates/fdmr-compute/src/lib.rs::shear_and_lateral_mixing_spread_a_release_at_taylors_k",
    ),
    "fischer-longitudinal": (
        "crates/fdmr-math/src/dispersion.rs::fischer_k_textbook",
        "crates/fdmr-math/src/dispersion.rs::change_of_moment_recovers_known_k",
        "crates/fdmr-math/src/dispersion.rs::fickian_distance_fischer_example",
        "crates/fdmr-math/src/parcel.rs::moment_method_recovers_k_from_parcels",
    ),
    "groyne-field-storage": (
        "crates/fdmr-compute/src/lib.rs::dead_zone_storage_delays_a_cloud_by_beta",
        "python/tests/test_storage.py::test_the_switch_follows_the_velocity_head",
        "python/tests/test_storage.py::test_every_branchs_tables_are_sourced_and_sane",
        "check: every measured lag of the Rhine branches, 1986-1991 (tools/validation/rhine_dye/lag_rule.py)",
    ),
    "held-or-rule": (
        "python/tests/test_releases.py::test_flood_rule_raises_the_release_once_the_top_is_reached",
        "python/tests/test_releases.py::test_flood_rule_holds_while_there_is_room",
        "python/tests/test_releases.py::test_missing_inputs_hold_the_release_and_say_why",
    ),
    "dsm2-hydro": (
        "python/tests/test_dsm2.py::test_gate_states_from_tidal_flow_through_the_gate",
        "check: Delta flows and levels against USGS and CDEC stations, each cycle (check.html)",
    ),
    "dsm2-qual-ec": ("check: Delta conductance against stations, each cycle (check.html)",),
    "dsm2-qual-heat": (
        "python/tests/test_dsm2.py::test_temperature_study_gives_every_inflow_a_series",
        "python/tests/test_dsm2.py::test_wet_bulb_gives_back_the_dew_points_vapour_pressure",
        "check: Delta water temperature against stations, each cycle (check.html)",
    ),
    "qual-fingerprint": ("python/tests/test_dsm2.py::test_fingerprint_groups_take_inflows_by_name",),
    "gtm-cohesive-settled-bed": (
        "python/tests/test_sediment.py::test_stokes_diameter_matches_the_settling_velocity",
        "python/tests/test_sediment.py::test_study_assigns_every_inflow_and_names_its_class",
        "check: suspended sediment against turbidity stations, each Delta cycle (sediment_checks, check.html)",
    ),
    "partition-settle-scour": (
        "crates/fdmr-compute/src/network.rs::a_bound_release_splits_by_the_partition_coefficient",
        "crates/fdmr-compute/src/network.rs::bound_parcels_settle_at_ws_over_depth",
        "crates/fdmr-compute/src/network.rs::settling_and_scouring_at_equal_rates_leave_half_on_the_bed",
    ),
    "noaa-fvcom-forecast": (
        "python/tests/test_fvcom.py::test_a_coarse_mesh_covers_the_water_and_averages_exactly",
        "check: the Bay's currents, levels, salinity and temperature against stations, each cycle (check.html)",
    ),
    "network-outflow-match": ("python/tests/test_fvcom.py::test_the_outflow_correction_in_a_channel_is_q_over_the_cross_section",),
    "tides-plus-outflow-fit": (
        "python/tests/test_fvcom.py::test_the_fit_recovers_tides_and_drift_from_two_hourly_samples",
        "python/tests/test_fvcom.py::test_the_extension_joins_the_forecast_without_a_jump",
    ),
    "station-misfit-spread": ("python/tests/test_fvcom.py::test_misfits_spread_through_the_water_not_across_land",),
    "export-carried": ("python/tests/test_canals.py::test_sections_area_and_width",),
    "intake-lagged": ("python/tests/test_canals.py::test_water_reaches_a_place_after_the_canal_upstream_of_it_has_filled",),
    "advection-random-walk": (
        "crates/fdmr-math/src/parcel.rs::no_dispersion_means_pure_advection",
        "crates/fdmr-math/src/parcel.rs::moment_method_recovers_k_from_parcels",
        "crates/fdmr-compute/src/mesh.rs::uniform_flow_carries_a_parcel_exactly",
        "crates/fdmr-compute/src/mesh.rs::spreading_matches_diffusion_and_stays_even_over_a_sloping_bed",
        "crates/fdmr-compute/src/mesh.rs::mass_is_conserved_under_spreading",
        "crates/fdmr-compute/src/mesh.rs::an_even_start_stays_even_where_the_diffusivity_varies",
        "crates/fdmr-compute/src/lib.rs::mass_balances_with_decay_and_outflow",
    ),
    "pss-78": ("crates/fdmr-math/src/salinity.rs::delta_waters_read_sensibly",),
    "first-order": (
        "crates/fdmr-math/src/decay.rs::half_left_after_one_half_life",
        "crates/fdmr-math/src/decay.rs::steps_compose",
    ),
}


@dataclass(frozen=True)
class Candidate:
    kind: str
    part: str
    name: str
    known: str
    why_not: str
    issue: str


# Unsourced candidates, parked (issue #26). None of them runs.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate("1d-tidal-network", "sediment", "two-settling-classes",
              "Delta microflocs average 1.63 mm/s (Manning and Schoellhamer, 2015 conference abstract, preliminary); "
              "South Bay slow class 0.014 mm/s in autumn (Brand et al. 2015, Limnol. Oceanogr. 60:463)",
              "no published Delta source for the mass split between the classes; DWR's own two-class calibration "
              "(sand and fines, 2019) was tried and is worse model-wide", "#24"),
    Candidate("1d-tidal-network", "sediment", "wind-wave-resuspension",
              "fetch-limited waves (USACE Coastal Engineering Manual II-2); measured wave heights in the Delta's "
              "flooded islands (Lacy et al. 2023; Jones et al. 2008)",
              "the wave stress depends on a bed roughness z0 with no Delta value, and DSM2's flooded islands have no "
              "sediment bed in GTM", "#24"),
    Candidate("1d-tidal-network", "sediment", "depth-limited-bed",
              "critical stress rising as the bed erodes (Schoellhamer et al. 2017, ref:schoellhamer2017, p. 807)",
              "its slope comes from Franks Tract alone, r2 0.40", "#24"),
    Candidate("1d-tidal-network", "sediment-checks", "turbidity-floor",
              "USGS site fits give about 3.3 mg/L at 1 FNU; organic matter is 10-16% of suspended solids in "
              "October (Mathers et al. 2025)",
              "a derived number, and it changes how the checks score, not the model", "#24"),
    Candidate("river-routing", "lateral-mixing", "bend-mixing",
              "Fischer 1969 (WRR 5:496): eps_t/(d u*) = 25 (U/u*)^2 (d/R)^2; Baek and Lee 2023 (Water 15:3120) fit "
              "33 rivers' measured mixing on (U/u*)(d/R)",
              "raising the mixing in bends lowers the long-range spreading below Fischer's K; it needs a held-out "
              "test against measured dye clouds", "#27"),
    Candidate("fishing", "white-sturgeon", "salinity-bands",
              "FDMR's reading of Sellheim et al. 2022's habitat classes; CDFW's trammel-net survey agrees above "
              "16 psu, where salinity follows place",
              "moderate to weak evidence; too little survey salinity to choose new scores", "#25"),
    Candidate("fishing", "white-sturgeon", "run-cue-40-m3s",
              "FDMR's reading of Schaffter 1997 and Heublein et al. 2017",
              "not calibrated: the size and the day-over-day timescale are a reading", "#25"),
)
