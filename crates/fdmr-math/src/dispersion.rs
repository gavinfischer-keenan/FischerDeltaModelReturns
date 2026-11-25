//! Longitudinal dispersion in rivers, after Fischer.
//!
//! References: Fischer, List, Koh, Imberger and Brooks, *Mixing in Inland and
//! Coastal Waters* (1979), chapter 5; Fischer, *Longitudinal Dispersion in
//! Laboratory and Natural Streams* (Caltech, 1966).

use crate::G;

/// Shear velocity u* = √(g d S), m/s, for mean depth `depth` (m) and energy slope `slope`.
#[must_use]
pub fn shear_velocity(depth: f64, slope: f64) -> f64 {
    (G * depth * slope).sqrt()
}

/// Fischer's estimate of the longitudinal dispersion coefficient, m²/s:
/// K = 0.011 U² W² / (d u*).
///
/// `velocity` U (m/s), `width` W (m), `depth` d (m) and `shear_velocity` u* (m/s).
/// Good only to within a factor of several (specification section 7.1).
#[must_use]
pub fn fischer_k(velocity: f64, width: f64, depth: f64, shear_velocity: f64) -> f64 {
    debug_assert!(depth > 0.0 && shear_velocity > 0.0);
    0.011 * velocity * velocity * width * width / (depth * shear_velocity)
}

/// Dispersion coefficient from the change of temporal moments of a tracer
/// passing two stations (Fischer's change-of-moment method), m²/s:
///
/// K = U²/2 · (σ²₂ − σ²₁) / (t̄₂ − t̄₁)
///
/// `velocity` U (m/s); `mean_*` the centroid times (s) and `var_*` the temporal
/// variances (s²) of the passing signal at the upstream (1) and downstream (2) stations.
#[must_use]
pub fn change_of_moment_k(velocity: f64, mean_1: f64, var_1: f64, mean_2: f64, var_2: f64) -> f64 {
    debug_assert!(mean_2 > mean_1);
    0.5 * velocity * velocity * (var_2 - var_1) / (mean_2 - mean_1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shear_velocity_textbook() {
        // d = 2 m, S = 1e-4: u* = sqrt(9.81 * 2 * 1e-4) = 0.04429 m/s
        assert!((shear_velocity(2.0, 1e-4) - 0.044_294).abs() < 1e-5);
    }

    #[test]
    fn fischer_k_textbook() {
        // U = 0.5 m/s, W = 50 m, d = 2 m, u* = 0.05 m/s:
        // K = 0.011 * 0.25 * 2500 / (2 * 0.05) = 68.75 m²/s
        assert!((fischer_k(0.5, 50.0, 2.0, 0.05) - 68.75).abs() < 1e-9);
    }

    #[test]
    fn fischer_k_scales_as_u_squared_w_squared() {
        let k = fischer_k(0.5, 50.0, 2.0, 0.05);
        assert!((fischer_k(1.0, 50.0, 2.0, 0.05) / k - 4.0).abs() < 1e-12);
        assert!((fischer_k(0.5, 100.0, 2.0, 0.05) / k - 4.0).abs() < 1e-12);
    }

    #[test]
    fn change_of_moment_recovers_known_k() {
        // A slug in uniform flow: in time, the variance at distance x is 2Kx/U³,
        // and the centroid is at x/U. Between x1 and x2 the method gives K back.
        let (u, k) = (0.6_f64, 40.0_f64);
        let (x1, x2) = (5_000.0, 25_000.0);
        let var = |x: f64| 2.0 * k * x / u.powi(3);
        let got = change_of_moment_k(u, x1 / u, var(x1), x2 / u, var(x2));
        assert!((got - k).abs() < 1e-9);
    }
}
