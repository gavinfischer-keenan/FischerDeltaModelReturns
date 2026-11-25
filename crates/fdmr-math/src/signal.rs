//! Signals passing gauges: the lag and change of shape between two stations
//! (specification section 9.1).

/// Temporal moments of a signal: centroid time (s) and variance (s²).
///
/// `values` sampled every `step` seconds from time `t0`. The baseline, the
/// series minimum, is removed first, so a pulse on a steady background works.
/// Returns `None` if the signal has no pulse.
#[must_use]
pub fn temporal_moments(values: &[f64], t0: f64, step: f64) -> Option<(f64, f64)> {
    let base = values.iter().copied().fold(f64::INFINITY, f64::min);
    let (mut m0, mut m1) = (0.0, 0.0);
    for (i, v) in values.iter().enumerate() {
        let w = v - base;
        m0 += w;
        m1 += w * (t0 + step * i as f64);
    }
    if m0 <= 0.0 {
        return None;
    }
    let mean = m1 / m0;
    let var = values
        .iter()
        .enumerate()
        .map(|(i, v)| (v - base) * (t0 + step * i as f64 - mean).powi(2))
        .sum::<f64>()
        / m0;
    Some((mean, var))
}

/// The lag (s) by which `downstream` follows `upstream`, from the peak of their
/// cross-correlation, refined between samples by a parabola through the peak.
///
/// Both series are sampled every `step` seconds over the same times. Lags from
/// 0 to `max_lag_steps` are searched. Returns `None` if either series is flat.
#[must_use]
pub fn lag_by_cross_correlation(
    upstream: &[f64],
    downstream: &[f64],
    step: f64,
    max_lag_steps: usize,
) -> Option<f64> {
    let n = upstream.len().min(downstream.len());
    let demean = |s: &[f64]| {
        let m = s[..n].iter().sum::<f64>() / n as f64;
        s[..n].iter().map(|v| v - m).collect::<Vec<f64>>()
    };
    let (a, b) = (demean(upstream), demean(downstream));
    let norm = (a.iter().map(|v| v * v).sum::<f64>() * b.iter().map(|v| v * v).sum::<f64>()).sqrt();
    if norm == 0.0 {
        return None;
    }
    let max_lag = max_lag_steps.min(n.saturating_sub(1));
    let corr: Vec<f64> = (0..=max_lag)
        .map(|lag| (0..n - lag).map(|i| a[i] * b[i + lag]).sum::<f64>() / norm)
        .collect();
    let (best, _) =
        corr.iter().enumerate().fold(
            (0, f64::NEG_INFINITY),
            |acc, (i, &c)| if c > acc.1 { (i, c) } else { acc },
        );
    let mut lag = best as f64;
    if best > 0 && best < max_lag {
        let (y0, y1, y2) = (corr[best - 1], corr[best], corr[best + 1]);
        let denom = y0 - 2.0 * y1 + y2;
        if denom != 0.0 {
            lag += 0.5 * (y0 - y2) / denom;
        }
    }
    Some(lag * step)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gaussian(n: usize, step: f64, centre: f64, sd: f64, base: f64) -> Vec<f64> {
        (0..n)
            .map(|i| base + (-0.5 * ((i as f64 * step - centre) / sd).powi(2)).exp())
            .collect()
    }

    #[test]
    fn moments_of_a_gaussian_pulse() {
        let (step, centre, sd) = (60.0, 30_000.0, 2_400.0);
        let s = gaussian(1200, step, centre, sd, 7.5);
        let (m, v) = temporal_moments(&s, 0.0, step).unwrap();
        assert!((m - centre).abs() < 1.0, "centroid {m}");
        assert!((v.sqrt() - sd).abs() / sd < 0.005, "sd {}", v.sqrt());
    }

    #[test]
    fn flat_signal_has_no_moments() {
        assert!(temporal_moments(&[3.0; 50], 0.0, 1.0).is_none());
    }

    #[test]
    fn finds_a_lag_between_samples() {
        let step = 900.0; // 15-minute gauge data
        let up = gaussian(400, step, 60_000.0, 5_000.0, 100.0);
        let down = gaussian(400, step, 60_000.0 + 31_000.0, 5_000.0, 100.0);
        let lag = lag_by_cross_correlation(&up, &down, step, 100).unwrap();
        assert!((lag - 31_000.0).abs() < 150.0, "lag {lag}");
    }

    #[test]
    fn flat_series_has_no_lag() {
        assert!(lag_by_cross_correlation(&[1.0; 20], &[1.0; 20], 1.0, 5).is_none());
    }
}
