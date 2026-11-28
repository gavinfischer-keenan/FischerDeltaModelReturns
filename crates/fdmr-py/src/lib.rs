//! Python bindings to the math library and the published format
//! (specification section 4.2).
//!
//! Thin wrappers only: every calculation lives in `fdmr-math` and every byte
//! of the format in `fdmr-format`, so the model runner (Python) and the compute
//! engine (Rust, in the browser) share the same code. Arguments and results are
//! plain numbers, strings and lists.

#![allow(clippy::needless_pass_by_value)] // PyO3 hands over owned lists

use std::fs::File;
use std::io::{BufReader, BufWriter};

use fdmr_format::{Encoding, Field, FieldFile};
use fdmr_math::{certainty, decay, dispersion, geometry, routing, salinity, signal};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// One field to write: (name, unit, quantised, values).
type FieldSpec = (String, String, bool, Vec<f32>);

/// Write a field file (`docs/format.md`). Values are `steps x layers x elements`,
/// element index fastest; `NaN` means no value.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn write_field_file(
    path: String,
    reach: String,
    dims: u8,
    elements: u32,
    layers: u32,
    steps: u32,
    t0: i64,
    step_s: u32,
    fields: Vec<FieldSpec>,
) -> PyResult<()> {
    let file = FieldFile {
        reach,
        dims,
        elements,
        layers,
        steps,
        t0,
        step_s,
        fields: fields
            .into_iter()
            .map(|(name, unit, quantised, values)| Field {
                name,
                unit,
                encoding: if quantised {
                    Encoding::QuantisedI16
                } else {
                    Encoding::F32
                },
                values,
            })
            .collect(),
    };
    let mut w = BufWriter::new(File::create(&path)?);
    file.write_to(&mut w)
        .map_err(|e| PyValueError::new_err(e.to_string()))
}

/// Read a field file. Returns `(reach, dims, elements, layers, steps, t0, step_s,
/// [(name, unit, values)])`.
#[pyfunction]
#[allow(clippy::type_complexity)]
fn read_field_file(
    path: String,
) -> PyResult<(
    String,
    u8,
    u32,
    u32,
    u32,
    i64,
    u32,
    Vec<(String, String, Vec<f32>)>,
)> {
    let mut r = BufReader::new(File::open(&path)?);
    let f = FieldFile::read_from(&mut r).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let fields = f
        .fields
        .into_iter()
        .map(|x| (x.name, x.unit, x.values))
        .collect();
    Ok((
        f.reach, f.dims, f.elements, f.layers, f.steps, f.t0, f.step_s, fields,
    ))
}

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

/// Godin's tidal filter (`fdmr_math::signal::godin`, ref:godin1972): the subtidal
/// part of a series sampled `per_hour` times an hour; a NaN makes only the
/// windows it falls in missing.
#[pyfunction]
fn godin(x: Vec<f64>, per_hour: f64) -> Vec<f64> {
    fdmr_math::signal::godin(&x, per_hour)
}

/// `shear_velocity` for every depth at once (one slope), m/s.
#[pyfunction]
fn shear_velocities(depths: Vec<f64>, slope: f64) -> Vec<f64> {
    depths
        .iter()
        .map(|&d| dispersion::shear_velocity(d, slope))
        .collect()
}

/// `fischer_k` for every step at once, m²/s: the four lists the same length.
#[pyfunction]
fn fischer_ks(
    velocity: Vec<f64>,
    width: Vec<f64>,
    depth: Vec<f64>,
    shear_velocity: Vec<f64>,
) -> PyResult<Vec<f64>> {
    let n = velocity.len();
    if width.len() != n || depth.len() != n || shear_velocity.len() != n {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "velocity, width, depth and shear_velocity must be the same length",
        ));
    }
    Ok((0..n)
        .map(|i| dispersion::fischer_k(velocity[i], width[i], depth[i], shear_velocity[i]))
        .collect())
}

/// How far below a release Fischer's dispersion holds, m (Fischer 1966, eq. 57, t/T > 6).
#[pyfunction]
fn fickian_distance(velocity: f64, length: f64, hydraulic_radius: f64, shear_velocity: f64) -> f64 {
    dispersion::fickian_distance(velocity, length, hydraulic_radius, shear_velocity)
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

/// (lag s, correlation at the peak) by which `downstream` follows `upstream`, or None.
#[pyfunction]
fn cross_correlation_peak(
    upstream: Vec<f64>,
    downstream: Vec<f64>,
    step: f64,
    max_lag_steps: usize,
) -> Option<(f64, f64)> {
    signal::cross_correlation_peak(&upstream, &downstream, step, max_lag_steps)
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

/// Certainty of a reach's flow `t` s after now (negative for the past): 1 to the
/// end of the solid forecast, then falling linearly to `floor` at `forecast_end`.
#[pyfunction]
fn certainty_at(t: f64, solid_until: f64, forecast_end: f64, floor: f64) -> f64 {
    certainty::ReachCertainty {
        solid_until,
        forecast_end,
        floor,
    }
    .at(t)
}

/// Practical salinity (psu) for each specific conductance (µS/cm at 25 °C).
#[pyfunction]
fn practical_salinity(specific_conductance: Vec<f64>) -> Vec<f64> {
    specific_conductance
        .into_iter()
        .map(salinity::practical_salinity)
        .collect()
}

#[pymodule]
fn fdmr_py(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(shear_velocity, m)?)?;
    m.add_function(wrap_pyfunction!(fischer_k, m)?)?;
    m.add_function(wrap_pyfunction!(shear_velocities, m)?)?;
    m.add_function(wrap_pyfunction!(fischer_ks, m)?)?;
    m.add_function(wrap_pyfunction!(godin, m)?)?;
    // Elder's lateral mixing coefficient, as the engine uses it: written once (#70).
    m.add("ELDER_LATERAL", dispersion::ELDER_LATERAL)?;
    m.add_function(wrap_pyfunction!(fickian_distance, m)?)?;
    m.add_function(wrap_pyfunction!(change_of_moment_k, m)?)?;
    m.add_function(wrap_pyfunction!(fit_power_law, m)?)?;
    m.add_function(wrap_pyfunction!(temporal_moments, m)?)?;
    m.add_function(wrap_pyfunction!(lag_by_cross_correlation, m)?)?;
    m.add_function(wrap_pyfunction!(cross_correlation_peak, m)?)?;
    m.add_function(wrap_pyfunction!(route, m)?)?;
    m.add_function(wrap_pyfunction!(remaining_fraction, m)?)?;
    m.add_function(wrap_pyfunction!(certainty_at, m)?)?;
    m.add_function(wrap_pyfunction!(practical_salinity, m)?)?;
    m.add_function(wrap_pyfunction!(write_field_file, m)?)?;
    m.add_function(wrap_pyfunction!(read_field_file, m)?)?;
    Ok(())
}
