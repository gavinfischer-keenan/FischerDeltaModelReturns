//! Python bindings to the math library (specification section 4.2).
//!
//! Thin wrappers only: every calculation lives in `fdmr-math`, so the model
//! runner (Python) and the compute engine (Rust, in the browser) use the same
//! code. Arguments and results are plain numbers and lists.

#![allow(clippy::needless_pass_by_value)] // PyO3 hands over owned lists

use fdmr_math::{decay, dispersion, geometry, routing, signal};
use pyo3::prelude::*;

/// Shear velocity √(g d S), m/s.
#[pyfunction]
fn shear_velocity(depth: f64, slope: f64) -> f64 {
    dispersion::shear_velocity(depth, slope)
}

/// Fischer's longitudinal dispersion estimate, m²/s.
#[pyfunction]
fn fischer_k(velocity: f64, width: f64, depth: f64, shear_velocity: f64) -> f64 {
    dispersion::fischer_k(velocity, width, depth, shear_velocity)
}

/// Dispersion from the change of temporal moments between two stations, m²/s.
#[pyfunction]
fn change_of_moment_k(velocity: f64, mean_1: f64, var_1: f64, mean_2: f64, var_2: f64) -> f64 {
    dispersion::change_of_moment_k(velocity, mean_1, var_1, mean_2, var_2)
}

/// Fit y = a·Qᵇ. Returns (a, b, r², n) or None.
#[pyfunction]
fn fit_power_law(q: Vec<f64>, y: Vec<f64>) -> Option<(f64, f64, f64, usize)> {
    geometry::fit_power_law(&q, &y).map(|f| (f.law.a, f.law.b, f.r2, f.n))
}

/// Centroid time (s) and variance (s²) of a sampled signal, or None.
#[pyfunction]
fn temporal_moments(values: Vec<f64>, t0: f64, step: f64) -> Option<(f64, f64)> {
    signal::temporal_moments(&values, t0, step)
}

/// Lag (s) by which `downstream` follows `upstream`, or None.
#[pyfunction]
fn lag_by_cross_correlation(
    upstream: Vec<f64>,
    downstream: Vec<f64>,
    step: f64,
    max_lag_steps: usize,
) -> Option<f64> {
    signal::lag_by_cross_correlation(&upstream, &downstream, step, max_lag_steps)
}

/// Route an inflow series down one segment (variable lag-and-route).
#[pyfunction]
fn route(
    inflow: Vec<f64>,
    step: f64,
    travel_a: f64,
    travel_b: f64,
    atten_a: f64,
    atten_b: f64,
) -> Vec<f64> {
    let seg = routing::SegmentRouting {
        travel_a,
        travel_b,
        atten_a,
        atten_b,
    };
    routing::route(&inflow, step, &seg)
}

/// Fraction of mass left after `dt` s for a half-life in s (None: no decay).
#[pyfunction]
#[pyo3(signature = (dt, half_life=None))]
fn remaining_fraction(dt: f64, half_life: Option<f64>) -> f64 {
    decay::remaining_fraction(dt, half_life)
}

#[pymodule]
fn fdmr_py(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(shear_velocity, m)?)?;
    m.add_function(wrap_pyfunction!(fischer_k, m)?)?;
    m.add_function(wrap_pyfunction!(change_of_moment_k, m)?)?;
    m.add_function(wrap_pyfunction!(fit_power_law, m)?)?;
    m.add_function(wrap_pyfunction!(temporal_moments, m)?)?;
    m.add_function(wrap_pyfunction!(lag_by_cross_correlation, m)?)?;
    m.add_function(wrap_pyfunction!(route, m)?)?;
    m.add_function(wrap_pyfunction!(remaining_fraction, m)?)?;
    Ok(())
}
