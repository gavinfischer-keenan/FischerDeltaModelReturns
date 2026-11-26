//! Hydraulic geometry: how a channel's cross-section changes with flow.
//!
//! Fitted from field measurements of flow, area and width at a gauge
//! (specification section 9.1), as power laws y = a·Qᵇ, the classic
//! at-a-station hydraulic geometry of Leopold and Maddock (ref:leopold1953).

/// y = a·Qᵇ
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PowerLaw {
    pub a: f64,
    pub b: f64,
}

impl PowerLaw {
    #[must_use]
    pub fn eval(&self, q: f64) -> f64 {
        self.a * q.powf(self.b)
    }
}

/// A power-law fit and how well it fits.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Fit {
    pub law: PowerLaw,
    /// Coefficient of determination in log space.
    pub r2: f64,
    pub n: usize,
}

/// Least-squares fit of y = a·Qᵇ in log space. Pairs with a non-positive value are skipped.
/// Returns `None` with fewer than two usable pairs, or if every Q is the same.
#[must_use]
pub fn fit_power_law(q: &[f64], y: &[f64]) -> Option<Fit> {
    let pts: Vec<(f64, f64)> = q
        .iter()
        .zip(y)
        .filter(|(q, y)| **q > 0.0 && **y > 0.0)
        .map(|(q, y)| (q.ln(), y.ln()))
        .collect();
    let n = pts.len();
    if n < 2 {
        return None;
    }
    let nf = n as f64;
    let mx = pts.iter().map(|p| p.0).sum::<f64>() / nf;
    let my = pts.iter().map(|p| p.1).sum::<f64>() / nf;
    let sxx: f64 = pts.iter().map(|p| (p.0 - mx).powi(2)).sum();
    if sxx == 0.0 {
        return None;
    }
    let sxy: f64 = pts.iter().map(|p| (p.0 - mx) * (p.1 - my)).sum();
    let b = sxy / sxx;
    let ln_a = my - b * mx;
    let ss_res: f64 = pts.iter().map(|p| (p.1 - (ln_a + b * p.0)).powi(2)).sum();
    let ss_tot: f64 = pts.iter().map(|p| (p.1 - my).powi(2)).sum();
    let r2 = if ss_tot > 0.0 {
        1.0 - ss_res / ss_tot
    } else {
        1.0
    };
    Some(Fit {
        law: PowerLaw { a: ln_a.exp(), b },
        r2,
        n,
    })
}

/// A cross-section's shape as a function of flow.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct HydraulicGeometry {
    /// Cross-section area, m², against flow, m³/s.
    pub area: PowerLaw,
    /// Top width, m, against flow, m³/s.
    pub width: PowerLaw,
}

impl HydraulicGeometry {
    /// Fit from field measurements: flow (m³/s), area (m²) and width (m).
    #[must_use]
    pub fn fit(flow: &[f64], area: &[f64], width: &[f64]) -> Option<(Self, Fit, Fit)> {
        let fa = fit_power_law(flow, area)?;
        let fw = fit_power_law(flow, width)?;
        Some((
            Self {
                area: fa.law,
                width: fw.law,
            },
            fa,
            fw,
        ))
    }

    #[must_use]
    pub fn area(&self, q: f64) -> f64 {
        self.area.eval(q)
    }
    #[must_use]
    pub fn width(&self, q: f64) -> f64 {
        self.width.eval(q)
    }
    /// Mean depth = area / width, m.
    #[must_use]
    pub fn depth(&self, q: f64) -> f64 {
        self.area(q) / self.width(q)
    }
    /// Mean water velocity = flow / area, m/s: the speed a pollutant travels at
    /// (specification section 7.1), not the speed of a flood wave.
    #[must_use]
    pub fn velocity(&self, q: f64) -> f64 {
        q / self.area(q)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recovers_an_exact_power_law() {
        let q: Vec<f64> = (1..=20).map(|i| f64::from(i) * 50.0).collect();
        let y: Vec<f64> = q.iter().map(|q| 3.2 * q.powf(0.62)).collect();
        let f = fit_power_law(&q, &y).unwrap();
        assert!((f.law.a - 3.2).abs() < 1e-9 && (f.law.b - 0.62).abs() < 1e-12);
        assert!((f.r2 - 1.0).abs() < 1e-12);
    }

    #[test]
    fn skips_bad_pairs_and_refuses_too_few() {
        assert!(fit_power_law(&[0.0, 100.0], &[1.0, 2.0]).is_none());
        assert!(fit_power_law(&[100.0, 100.0], &[1.0, 2.0]).is_none());
        let f = fit_power_law(&[-1.0, 10.0, 100.0], &[5.0, 2.0, 4.0]).unwrap();
        assert_eq!(f.n, 2);
    }

    #[test]
    fn velocity_depth_and_width_are_consistent() {
        // Exponents that obey continuity: width b + depth f + velocity m = 1.
        let g = HydraulicGeometry {
            area: PowerLaw { a: 2.0, b: 0.7 },
            width: PowerLaw { a: 8.0, b: 0.25 },
        };
        let q = 300.0;
        let (a, w, d, u) = (g.area(q), g.width(q), g.depth(q), g.velocity(q));
        assert!((w * d - a).abs() < 1e-9 * a);
        assert!((u * a - q).abs() < 1e-9 * q);
        // Velocity grows with flow here, as exponent 1 - 0.7 = 0.3.
        assert!(g.velocity(600.0) > u);
    }
}
