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
    cross_correlation_peak(upstream, downstream, step, max_lag_steps).map(|(lag, _)| lag)
}

/// As [`lag_by_cross_correlation`], with the correlation at the peak (-1..1),
/// so a caller can reject a lag that rests on a weak match.
///
/// Every lag is scored on the same stretch of `upstream`, its first
/// `n - max_lag_steps` samples, against the matching stretch of `downstream`,
/// as a Pearson correlation. Scoring each lag over a different number of
/// samples would pull a broad peak toward zero lag.
#[must_use]
pub fn cross_correlation_peak(
    upstream: &[f64],
    downstream: &[f64],
    step: f64,
    max_lag_steps: usize,
) -> Option<(f64, f64)> {
    let n = upstream.len().min(downstream.len());
    let max_lag = max_lag_steps.min(n.saturating_sub(2));
    let m = n - max_lag;
    let pearson = |a: &[f64], b: &[f64]| {
        let (ma, mb) = (
            a.iter().sum::<f64>() / m as f64,
            b.iter().sum::<f64>() / m as f64,
        );
        let (mut sab, mut saa, mut sbb) = (0.0, 0.0, 0.0);
        for (x, y) in a.iter().zip(b) {
            sab += (x - ma) * (y - mb);
            saa += (x - ma) * (x - ma);
            sbb += (y - mb) * (y - mb);
        }
        if saa == 0.0 || sbb == 0.0 {
            None
        } else {
            Some(sab / (saa * sbb).sqrt())
        }
    };
    let a = &upstream[..m];
    let corr: Vec<f64> = (0..=max_lag)
        .map(|lag| pearson(a, &downstream[lag..lag + m]).unwrap_or(f64::NEG_INFINITY))
        .collect();
    let (best, peak) =
        corr.iter().enumerate().fold(
            (0, f64::NEG_INFINITY),
            |acc, (i, &c)| if c > acc.1 { (i, c) } else { acc },
        );
    if !peak.is_finite() {
        return None;
    }
    let mut lag = best as f64;
    if best > 0 && best < max_lag && corr[best - 1].is_finite() && corr[best + 1].is_finite() {
        let (y0, y1, y2) = (corr[best - 1], corr[best], corr[best + 1]);
        let denom = y0 - 2.0 * y1 + y2;
        if denom != 0.0 {
            lag += 0.5 * (y0 - y2) / denom;
        }
    }
    Some((lag * step, peak))
}

/// Running mean over `w` samples, centred: sample `i` averages
/// `x[i - w/2 ..= i - w/2 + w - 1]`. Within half a window of either end, where
/// a full window does not fit, the nearest full-window value is held: a mean
/// over a fragment of a tide is no mean. A series no longer than the window
/// is its own mean throughout. A window holding a missing value (NaN) has no
/// mean (NaN), and only that window: a gap never spreads beyond the windows
/// it falls in (#70).
#[must_use]
pub fn running_mean_held(x: &[f64], w: usize) -> Vec<f64> {
    let n = x.len();
    let w = w.max(1);
    if n <= w {
        let m = if n == 0 {
            0.0
        } else {
            x.iter().sum::<f64>() / n as f64
        };
        return vec![m; n];
    }
    let half = w / 2;
    let mut out = vec![0.0; n];
    // The window's sum over its finite values, and how many it holds that are not.
    let finite = |v: f64| if v.is_finite() { v } else { 0.0 };
    let mut sum: f64 = x[..w].iter().copied().map(finite).sum();
    let mut missing = x[..w].iter().filter(|v| !v.is_finite()).count();
    let mean = |sum: f64, missing: usize| {
        if missing > 0 {
            f64::NAN
        } else {
            sum / w as f64
        }
    };
    out[half] = mean(sum, missing);
    // Each next window gains the sample after it and loses its first.
    for (s, o) in out[half + 1..n - half].iter_mut().enumerate() {
        let (gain, lose) = (x[s + w], x[s]);
        sum += finite(gain) - finite(lose);
        missing = missing + usize::from(!gain.is_finite()) - usize::from(!lose.is_finite());
        *o = mean(sum, missing);
    }
    let (first, last) = (out[half], out[n - half - 1]);
    out[..half].fill(first);
    out[n - half..].fill(last);
    out
}

/// Godin's three running means, h (Godin 1972, ref:godin1972).
pub const GODIN_HOURS: [f64; 3] = [24.0, 24.0, 25.0];

/// Godin's windows in samples, for a series sampled `per_hour` times an hour.
fn godin_windows(per_hour: f64) -> [usize; 3] {
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    GODIN_HOURS.map(|h| (h * per_hour).round().max(1.0) as usize)
}

/// The subtidal part of a series sampled `per_hour` times an hour: Godin's
/// filter, running means over 24, 24 and 25 hours in turn, which takes out the
/// diurnal and semidiurnal tides far more cleanly than one long running mean
/// (Godin 1972, ref:godin1972, *The Analysis of Tides*, University of Toronto Press). The
/// ends are held as [`running_mean_held`] holds them: within [`godin_edge`]
/// samples of either end the value is not a full tidal mean.
#[must_use]
pub fn godin(x: &[f64], per_hour: f64) -> Vec<f64> {
    let mut m = x.to_vec();
    for w in godin_windows(per_hour) {
        m = running_mean_held(&m, w);
    }
    m
}

/// How many samples at each end of a series [`godin`] cannot fully average:
/// within this many of either end, one of its running means held its nearest
/// full-window value instead (36.5 h at any sampling rate, half of each
/// window, rounded up). A caller comparing or publishing tidal means marks
/// them missing (#71).
#[must_use]
pub fn godin_edge(per_hour: f64) -> usize {
    godin_windows(per_hour).iter().map(|w| w.div_ceil(2)).sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn beyond_the_edge_a_series_filters_as_if_it_went_on() {
        // A tidal mean away from the ends does not depend on where the record
        // stops: filter a stretch alone and inside a longer record, and they
        // agree everywhere beyond `godin_edge` of the stretch's ends.
        let long: Vec<f64> = (0..2000)
            .map(|i| {
                let t = f64::from(i) / 4.0;
                (t / 12.42 * std::f64::consts::TAU).sin() + 0.4 * (t / 23.93 * std::f64::consts::TAU).cos() + t / 300.0
            })
            .collect();
        for per_hour in [4.0, 12.0] {
            let (a, b) = (300, 1700);
            let whole = godin(&long, per_hour);
            let part = godin(&long[a..b], per_hour);
            let e = godin_edge(per_hour);
            assert_eq!(e, (36.5 * per_hour).round() as usize);
            for (k, v) in part.iter().enumerate().skip(e).take(b - a - 2 * e) {
                assert!((v - whole[a + k]).abs() < 1e-9, "per_hour {per_hour} k {k}");
            }
            // Inside the edge it is not the tidal mean (the held value differs).
            assert!((part[e / 2] - whole[a + e / 2]).abs() > 1e-6);
        }
    }

    #[test]
    fn a_gap_makes_only_its_own_windows_missing() {
        // Before #70 a NaN entered the running sum and every later value was NaN.
        let mut x: Vec<f64> = (0..200).map(|i| f64::from(i % 7)).collect();
        x[50] = f64::NAN;
        let m = running_mean_held(&x, 10);
        // Sample i averages x[i - 5 ..= i + 4]: the gap at 50 is in the windows of 46 to 55.
        assert!(m[46..56].iter().all(|v| v.is_nan()), "{:?}", &m[40..60]);
        assert!(m[..46].iter().all(|v| v.is_finite()) && m[56..].iter().all(|v| v.is_finite()));
        let clean: Vec<f64> = (0..200).map(|i| f64::from(i % 7)).collect();
        let c = running_mean_held(&clean, 10);
        assert!((m[100] - c[100]).abs() < 1e-12);
    }

    #[test]
    fn running_mean_matches_a_direct_sum_and_holds_the_ends() {
        let x: Vec<f64> = (0..40).map(|i| f64::from(i * i % 13)).collect();
        for w in [4, 5] {
            let m = running_mean_held(&x, w);
            let half = w / 2;
            for i in half..x.len() - half {
                let direct = x[i - half..i - half + w].iter().sum::<f64>() / w as f64;
                assert!((m[i] - direct).abs() < 1e-12, "w {w} i {i}");
            }
            let same = |a: f64, b: f64| a.to_bits() == b.to_bits();
            assert!(m[..half].iter().all(|&v| same(v, m[half])));
            assert!(m[x.len() - half..]
                .iter()
                .all(|&v| same(v, m[x.len() - half - 1])));
        }
        assert_eq!(running_mean_held(&[1.0, 3.0], 5), vec![2.0, 2.0]);
    }

    #[test]
    fn godin_takes_out_the_tides_and_keeps_the_mean_flow() {
        // 15-minute samples over 20 days: a steady 250 plus the four main
        // constituents (M2, S2, K1, O1, periods in hours), 1500 at their largest.
        let per_hour = 4.0;
        let tides = [
            (12.4206, 900.0),
            (12.0, 250.0),
            (23.9345, 200.0),
            (25.8193, 150.0),
        ];
        let x: Vec<f64> = (0..20 * 96)
            .map(|i| {
                let t = f64::from(i) / per_hour;
                250.0
                    + tides
                        .iter()
                        .map(|(p, a)| a * (std::f64::consts::TAU * t / p).sin())
                        .sum::<f64>()
            })
            .collect();
        let m = godin(&x, per_hour);
        // Away from the held ends, the tide is gone to within 0.5% of its range.
        let worst = m[4 * 96..16 * 96]
            .iter()
            .map(|v| (v - 250.0).abs())
            .fold(0.0, f64::max);
        assert!(worst < 7.5, "worst {worst}");
    }

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
    fn weak_match_has_a_low_peak() {
        let step = 900.0;
        let up = gaussian(400, step, 60_000.0, 5_000.0, 100.0);
        let down = gaussian(400, step, 91_000.0, 5_000.0, 100.0);
        let (_, strong) = cross_correlation_peak(&up, &down, step, 100).unwrap();
        // The same pulse against an unrelated wiggle.
        let noise: Vec<f64> = (0..400).map(|i| f64::from(i * 7919 % 97).sin()).collect();
        let (_, weak) = cross_correlation_peak(&up, &noise, step, 100).unwrap();
        assert!(strong > 0.95, "strong {strong}");
        assert!(weak < 0.5, "weak {weak}");
    }

    #[test]
    fn flat_series_has_no_lag() {
        assert!(lag_by_cross_correlation(&[1.0; 20], &[1.0; 20], 1.0, 5).is_none());
    }
}
