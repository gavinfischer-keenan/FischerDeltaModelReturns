//! The math library (specification section 4.1).
//!
//! Pure calculation, with nothing about any place in it: no station numbers,
//! channel numbers, names or coordinates. SI units throughout. Each component
//! is tested against textbook cases.

pub mod certainty;
pub mod decay;
pub mod dispersion;
pub mod geometry;
pub mod parcel;
pub mod rng;
pub mod routing;
pub mod signal;

/// Gravitational acceleration, m/s².
pub const G: f64 = 9.81;
