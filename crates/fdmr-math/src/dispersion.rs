//! Longitudinal dispersion in rivers, after Fischer.
//!
//! References: Fischer, List, Koh, Imberger and Brooks, *Mixing in Inland and
//! Coastal Waters* (Academic Press, 1979; ref:fischer1979), chapter 5; Fischer,
//! *Longitudinal Dispersion in Laboratory and Natural Streams* (Caltech, 1966;
//! ref:fischer1966).

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

/// How far below a release Fischer's (Taylor's) one-dimensional dispersion
/// holds, m: x = U · 6T, with T = 0.30 l² / (r u*) the Lagrangian time scale
/// (Fischer 1966, ref:fischer1966, eq. 57). Taylor's theory "should be applied
/// only in the diffusive period", t/T > 6 (the same, summary item 5); nearer,
/// the cloud is still being sheared out and more lopsided than a dispersion
/// coefficient gives.
///
/// `velocity` U (m/s); `length` l (m), the surface distance from the thread of
/// maximum velocity to the furthest bank, the half width for a symmetric
/// channel; `hydraulic_radius` r (m); `shear_velocity` u* (m/s).
#[must_use]
pub fn fickian_distance(
    velocity: f64,
    length: f64,
    hydraulic_radius: f64,
    shear_velocity: f64,
) -> f64 {
    debug_assert!(hydraulic_radius > 0.0 && shear_velocity > 0.0);
    6.0 * 0.30 * velocity * length * length / (hydraulic_radius * shear_velocity)
}

/// Elder's lateral mixing coefficient as Fischer adopts it for rivers,
/// ε = 0.23 d u* (Fischer 1966, ref:fischer1966, section II-F, eq. 23).
pub const ELDER_LATERAL: f64 = 0.23;

/// The strength c of the cross-channel velocity profile
/// u = U (1 + c (1/3 − η²)), η from −1 at one bank to 1 at the other (its
/// mean is U, its mean square deviation c² 4/45 U²).
///
/// With lateral mixing ε, Taylor's long-time dispersion for this profile is
/// K = (1/(W ε)) ∫ q² dy, q(y) = ∫₀ʸ u′ dy′, which comes to
/// K = c² U² W² / (472.5 ε). c is chosen so that K is Fischer's
/// 0.011 U² W² / (d u*) when ε = 0.23 d u*: c² = 0.011 × 472.5 × 0.23, so
/// c ≈ 1.093. Water at the banks then moves at 0.27 U and mid-channel at
/// 1.36 U, and near a release the cloud is as skewed as that shear makes it,
/// the same mechanism (Fischer 1966, eqs. 40 and 57) at every distance.
#[must_use]
pub fn shear_profile_c() -> f64 {
    (0.011 * 472.5 * ELDER_LATERAL).sqrt()
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
    fn fickian_distance_fischer_example() {
        // Fischer 1966 p. 215: a river a mile wide and 25 ft deep, u* = 0.1 ft/s,
        // has a convective period of "the first 1200 hours". With l the half
        // width, 6T = 1.8 * 2640^2 / (25 * 0.1) s = 1394 h, as approximate as his.
        let ft = 0.3048;
        let six_t = fickian_distance(1.0, 2640.0 * ft, 25.0 * ft, 0.1 * ft) / 3600.0;
        assert!((six_t - 1393.9).abs() < 0.1);
        assert!((six_t / 1200.0 - 1.0).abs() < 0.2);
    }

    #[test]
    fn the_shear_profile_gives_fischers_k_by_taylors_integral() {
        // K = (1/(W ε)) ∫ q² dy by quadrature, for the profile at c, against
        // Fischer's 0.011 U² W² / (d u*) with ε = 0.23 d u*.
        let (u, w, d, ustar) = (0.8_f64, 60.0_f64, 2.5_f64, 0.07_f64);
        let eps = ELDER_LATERAL * d * ustar;
        let c = shear_profile_c();
        let n = 20_000;
        let dy = w / f64::from(n);
        let (mut q, mut integral) = (0.0, 0.0);
        for i in 0..n {
            let eta = 2.0 * (f64::from(i) + 0.5) * dy / w - 1.0;
            q += u * c * (1.0 / 3.0 - eta * eta) * dy;
            integral += q * q * dy;
        }
        let k = integral / (w * eps);
        let fischer = fischer_k(u, w, d, ustar);
        assert!((k / fischer - 1.0).abs() < 1e-3, "{k} against {fischer}");
        // the banks still move downstream
        assert!(1.0 - c * 2.0 / 3.0 > 0.2);
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
