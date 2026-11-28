//! Parcel movement: carried by the flow plus a random step for dispersion
//! (specification section 8.1).
//!
//! Horizontal movement and vertical mixing are kept apart (specification
//! section 4.1); only the horizontal part is modelled. Along a channel, a
//! parcel moves by Fischer's K alone (`step_along_channel`, with the
//! well-mixed drift), or by the shear across the channel and lateral mixing
//! (`step_shear`), and may rest in dead-zone storage (`dead_zone_switch`). On
//! a mesh it takes a two-dimensional random step (`step_2d`). The tracers in
//! crates/fdmr-compute call these; the maths lives here (#81).

use crate::dispersion::DEAD_ZONE_EXCHANGE;
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
/// in two (crates/fdmr-compute mesh/).
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

/// One step along a channel by the shear across it (#5): downstream at the
/// speed where the parcel is across the channel, U (1 + c (1/3 − η²)) with η
/// its place across, −1 to 1, and `c` the profile's strength
/// (`dispersion::shear_profile_c`); then across by the lateral mixing
/// coefficient ε, m²/s, over the channel's width, m, reflected at the banks.
/// Over many parcels the cloud spreads at Taylor's K = c² U² W² / (472.5 ε)
/// once it has mixed across, as `dispersion::shear_profile_c` sets out.
/// Returns the new chainage, m, and place across.
#[must_use]
#[allow(clippy::too_many_arguments)]
pub fn step_shear(
    x: f64,
    lat: f64,
    u: f64,
    c: f64,
    eps: f64,
    width: f64,
    dt: f64,
    rng: &mut Rng,
) -> (f64, f64) {
    let nx = x + u * (1.0 + c * (1.0 / 3.0 - lat * lat)) * dt;
    let mut lat = lat;
    lat += 2.0 / width * (2.0 * eps.max(0.0) * dt).sqrt() * rng.normal();
    while !(-1.0..=1.0).contains(&lat) {
        lat = if lat > 1.0 { 2.0 - lat } else { -2.0 - lat };
    }
    (nx, lat)
}

/// Whether a parcel is in dead-zone storage (a groyne field) after a step of
/// `dt`, s, where the reach's storage ratio is β > 0: a parcel in the flow is
/// taken in at the rate `DEAD_ZONE_EXCHANGE`, a stored one let out at that rate
/// over β, so in the long run a share β/(1 + β) is stored and a cloud is
/// delayed by β (van Mazijk 1996, ref:vanmazijk1996, section 4.4). One uniform
/// draw. A parcel let out takes a new place across the channel (the caller's
/// next draw).
#[must_use]
pub fn dead_zone_switch(stored: bool, beta: f64, dt: f64, rng: &mut Rng) -> bool {
    let rate = if stored {
        DEAD_ZONE_EXCHANGE / beta
    } else {
        DEAD_ZONE_EXCHANGE
    };
    if rng.uniform() < 1.0 - (-rate * dt).exp() {
        !stored
    } else {
        stored
    }
}

/// The random part of a step on a mesh, m in x and in y: √(2 K dt) N(0,1) in
/// each, with the drift that keeps a well-mixed tracer mixed where K or the
/// depth H changes, ∇K + K ∇H / H (Dimou and Adams 1993, ref:dimou1993;
/// Visser 1997, ref:visser1997). `k` K, m²/s; `kx`, `ky` its gradient; `h`
/// the depth, m, and `hx`, `hy` its gradient. Two normal draws, x first.
#[must_use]
#[allow(clippy::too_many_arguments, clippy::similar_names)]
pub fn step_2d(
    k: f64,
    kx: f64,
    ky: f64,
    h: f64,
    hx: f64,
    hy: f64,
    dt: f64,
    rng: &mut Rng,
) -> (f64, f64) {
    let sd = (2.0 * k * dt).sqrt();
    let dx = (kx + k * hx / h) * dt + sd * rng.normal();
    let dy = (ky + k * hy / h) * dt + sd * rng.normal();
    (dx, dy)
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

    #[test]
    fn shear_and_lateral_mixing_spread_at_taylors_k() {
        // 50 m wide, U 0.5 m/s, ε 0.05 m²/s: once mixed across (several
        // W²/ε), the cloud's variance grows at 2 K with Taylor's K =
        // c² U² W² / (472.5 ε); before that, the slow water by the banks
        // leaves a tail behind (negative skew).
        let (u, w, eps, dt) = (0.5, 50.0, 0.05, 60.0);
        let c = crate::dispersion::shear_profile_c();
        let mut rng = Rng::new(13);
        let mut ps: Vec<(f64, f64)> = (0..20_000)
            .map(|_| (0.0, rng.uniform() * 2.0 - 1.0))
            .collect();
        let run = |ps: &mut Vec<(f64, f64)>, steps: u32, rng: &mut Rng| {
            for _ in 0..steps {
                for p in ps.iter_mut() {
                    *p = step_shear(p.0, p.1, u, c, eps, w, dt, rng);
                }
            }
        };
        let xs = |ps: &Vec<(f64, f64)>| ps.iter().map(|p| p.0).collect::<Vec<_>>();
        run(&mut ps, 1_000, &mut rng); // 60,000 s
        let (_, v1) = mean_var(&xs(&ps));
        run(&mut ps, 2_000, &mut rng); // to 180,000 s
        let (m, v2) = mean_var(&xs(&ps));
        let k = (v2 - v1) / (2.0 * 120_000.0);
        let taylor = c * c * u * u * w * w / (472.5 * eps);
        assert!((k / taylor - 1.0).abs() < 0.1, "K {k} against {taylor}");
        assert!((m - u * 180_000.0).abs() < 300.0, "centre {m}");
        assert!(
            ps.iter().all(|p| (-1.0..=1.0).contains(&p.1)),
            "every parcel within the banks"
        );
    }

    #[test]
    fn dead_zone_storage_delays_a_cloud_by_beta() {
        // Every parcel starts in the flow; the share stored tends to β/(1+β)
        // over τ = β / (a (1+β)), and the cloud's centre moves
        // U (t/(1+β) + β τ/(1+β) (1 − e^(−t/τ))): at U/(1+β) in the long run.
        let (u, beta, dt, steps) = (0.5, 0.25, 60.0, 6_000u32);
        let mut rng = Rng::new(21);
        let mut ps = vec![(0.0_f64, false); 10_000];
        for _ in 0..steps {
            for p in &mut ps {
                p.1 = dead_zone_switch(p.1, beta, dt, &mut rng);
                if !p.1 {
                    p.0 += u * dt;
                }
            }
        }
        let t = dt * f64::from(steps);
        let a = DEAD_ZONE_EXCHANGE;
        let tau = beta / (a * (1.0 + beta));
        let expected =
            u * (t / (1.0 + beta) + beta * tau / (1.0 + beta) * (1.0 - (-t / tau).exp()));
        let (m, _) = mean_var(&ps.iter().map(|p| p.0).collect::<Vec<_>>());
        assert!(
            (m / expected - 1.0).abs() < 0.01,
            "centre {m} against {expected}"
        );
        #[allow(clippy::cast_precision_loss)]
        let stored = ps.iter().filter(|p| p.1).count() as f64 / ps.len() as f64;
        let share = beta / (1.0 + beta) * (1.0 - (-t / tau).exp());
        assert!(
            (stored - share).abs() < 0.02,
            "stored share {stored} against {share}"
        );
    }

    #[test]
    fn a_2d_step_spreads_as_2kt_and_drifts_up_the_gradients() {
        let (k, dt) = (2.0, 30.0);
        let mut rng = Rng::new(7);
        let steps: Vec<(f64, f64)> = (0..40_000)
            .map(|_| step_2d(k, 0.0, 0.0, 5.0, 0.0, 0.0, dt, &mut rng))
            .collect();
        let (mx, vx) = mean_var(&steps.iter().map(|s| s.0).collect::<Vec<_>>());
        let (_, vy) = mean_var(&steps.iter().map(|s| s.1).collect::<Vec<_>>());
        assert!(
            mx.abs() < 0.1
                && (vx / (2.0 * k * dt) - 1.0).abs() < 0.03
                && (vy / (2.0 * k * dt) - 1.0).abs() < 0.03
        );
        // No randomness without K; the drift alone, ∇K + K ∇H / H, times dt.
        let (dx, dy) = step_2d(0.0, 0.1, -0.2, 5.0, 0.0, 0.0, dt, &mut rng);
        assert!((dx - 3.0).abs() < 1e-12 && (dy + 6.0).abs() < 1e-12);
    }
}
