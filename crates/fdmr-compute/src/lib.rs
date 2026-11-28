//! The compute engine (specification section 4.2).
//!
//! Moves pollutant parcels and flow particles through a region's published
//! flow fields, and works out concentrations. It knows nothing about the
//! screen: it is handed field-file bytes and answers in places (a chainage, a
//! channel, a triangle), element indices and numbers. The UI draws.
//!
//! Each kind of reach has its tracer: a river (`river::RiverTracer`, 1D), a
//! channel network (`network::NetworkTracer`) and a 2D mesh
//! (`mesh::MeshTracer`); a region joins them (`region::Region`). Parcels move
//! with the flow plus a random step for dispersion (specification 8.1,
//! `fdmr_math::parcel`); flow particles move with the flow only and carry no
//! mass: they show the water (section 8.4). Every tracer reads its day files
//! through `series::Series`: linear between steps and across midnight, the
//! last value held after a day's end until the next day's file begins.

use fdmr_math::rng::Rng;

pub mod mesh;
pub mod network;
mod particles;
pub mod region;
pub mod river;
mod series;

pub use river::{EngineError, MassBalance, Release, RiverTracer};

/// Largest time step the engine takes, s.
pub const MAX_DT: f64 = 60.0;

/// How long a flow particle lives, s (model time): a random while in this
/// range, then it starts again somewhere else in its reach. Without it,
/// particles gather where the currents converge, along shores and on drying
/// shoals, and the open water empties; with it the density stays even
/// whatever the currents do. Three to nine hours: long enough to show a tide
/// carrying the water, short enough to keep the water filled.
pub const PARTICLE_LIFE_S: (f64, f64) = (3.0 * 3600.0, 9.0 * 3600.0);

/// A new flow particle's life (`PARTICLE_LIFE_S`).
pub(crate) fn particle_life(rng: &mut Rng) -> f64 {
    PARTICLE_LIFE_S.0 + rng.uniform() * (PARTICLE_LIFE_S.1 - PARTICLE_LIFE_S.0)
}

/// The particle to give up for one arriving from another reach: the one
/// furthest through its life.
pub(crate) fn oldest(ages: &[f64], lives: &[f64]) -> Option<usize> {
    (0..ages.len()).max_by(|&a, &b| (ages[a] / lives[a]).total_cmp(&(ages[b] / lives[b])))
}
