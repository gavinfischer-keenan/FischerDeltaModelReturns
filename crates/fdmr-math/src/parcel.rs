//! Parcel movement: carried by the flow plus a random step for dispersion
//! (specification section 8.1).
//!
//! Horizontal movement and vertical mixing are kept apart (specification
//! section 4.1). Version 1 needs only the horizontal part, along a channel;
//! a vertical-mixing step can be added later on its own.

use crate::rng::Rng;

/// One time step along a channel: x + U·dt + √(2·K·dt)·N(0,1).
///
/// `x` position along the channel (m), `velocity` U (m/s, positive downstream),
/// `k` longitudinal dispersion coefficient (m²/s), `dt` time step (s).
/// Over many parcels this gives the advection–dispersion equation's solution:
/// the cloud's centre moves at U and its variance grows as 2·K·t.
pub fn step_along_channel(x: f64, velocity: f64, k: f64, dt: f64, rng: &mut Rng) -> f64 {
    x + velocity * dt + (2.0 * k * dt).sqrt() * rng.normal()
}

/// The drift a random walk needs where K or the channel's cross-section A
/// changes along it: (1/A)·∂(A·K)/∂x, m/s, added to the flow's velocity.
///
/// The cross-sectionally averaged advection–dispersion equation is
/// ∂(A c)/∂t + ∂(Q c)/∂x = ∂/∂x (A K ∂c/∂x). Parcels are the mass A c per
/// metre, and the random walk that solves it is
/// dX = (U + (1/A) ∂(A K)/∂x) dt + √(2K) dW. Without the drift, parcels
/// collect where K or A is small and a well-mixed tracer un-mixes: the
/// well-mixed condition (Dimou and Adams 1993, ref:dimou1993; Visser 1997,
/// ref:visser1997), in its one-dimensional form, as the mesh's ∇(K H)/H is
/// in two (crates/fdmr-compute mesh.rs).
///
/// `a`, `ak`: A and A·K at x; `dak_dx`: the slope of A·K there.
#[must_use]
pub fn well_mixed_drift(a: f64, dak_dx: f64) -> f64 {
    if a > 0.0 {
        dak_dx / a
    } else {
        0.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dispersion::change_of_moment_k;

    fn mean_var(xs: &[f64]) -> (f64, f64) {
        let n = xs.len() as f64;
        let m = xs.iter().sum::<f64>() / n;
        (m, xs.iter().map(|x| (x - m).powi(2)).sum::<f64>() / n)
    }

    #[test]
    fn cloud_moves_at_u_and_spreads_as_2kt() {
        let (u, k, dt, steps) = (0.5, 50.0, 60.0, 600); // 10 hours
        let mut rng = Rng::new(2026);
        let mut xs = vec![0.0_f64; 40_000];
        for _ in 0..steps {
            for x in &mut xs {
                *x = step_along_channel(*x, u, k, dt, &mut rng);
            }
        }
        let t = dt * f64::from(steps);
        let (m, v) = mean_var(&xs);
        assert!(
            (m - u * t).abs() / (u * t) < 0.005,
            "centre {m} vs {}",
            u * t
        );
        assert!(
            (v - 2.0 * k * t).abs() / (2.0 * k * t) < 0.03,
            "variance {v} vs {}",
            2.0 * k * t
        );
    }

    #[test]
    fn moment_method_recovers_k_from_parcels() {
        // Parcels released at x = 0 pass stations at 3 km and 12 km. Their
        // arrival times' moments, through Fischer's change-of-moment method,
        // should give back the K that moved them. This ties the parcel step and
        // the river calibration (specification section 9.1) together.
        let (u, k, dt) = (0.4, 30.0, 20.0);
        let (s1, s2) = (3_000.0, 12_000.0);
        let mut rng = Rng::new(99);
        let (mut t1, mut t2) = (Vec::new(), Vec::new());
        for _ in 0..20_000 {
            let (mut x, mut t) = (0.0, 0.0);
            let mut hit1 = false;
            while x < s2 {
                let nx = step_along_channel(x, u, k, dt, &mut rng);
                t += dt;
                if !hit1 && nx >= s1 {
                    t1.push(t);
                    hit1 = true;
                }
                x = nx;
            }
            t2.push(t);
        }
        let ((m1, v1), (m2, v2)) = (mean_var(&t1), mean_var(&t2));
        let got = change_of_moment_k(u, m1, v1, m2, v2);
        assert!((got - k).abs() / k < 0.1, "K from moments {got} vs {k}");
    }

    #[test]
    fn no_dispersion_means_pure_advection() {
        let mut rng = Rng::new(3);
        assert!((step_along_channel(10.0, 1.5, 0.0, 4.0, &mut rng) - 16.0).abs() < 1e-12);
    }
}
