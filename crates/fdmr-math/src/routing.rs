//! Flood-wave routing along a river segment (specification section 7.1).
//!
//! A change in flow travels down a segment as a wave: it arrives after a wave
//! travel time and is smoothed on the way. Both depend on flow. This is
//! variable-parameter lag-and-route: the inflow is delayed by the travel time
//! for the current flow, then passed through a linear reservoir whose time
//! constant sets the smoothing.
//!
//! This is the wave, not the water. The water, and anything dissolved in it,
//! travels more slowly, at Q/A (see `geometry::HydraulicGeometry::velocity`).

/// A segment's routing parameters as functions of flow: y = a·Qᵇ, in seconds.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SegmentRouting {
    /// Wave travel time, s, against flow, m³/s.
    pub travel_a: f64,
    pub travel_b: f64,
    /// Attenuation time constant, s, against flow, m³/s. 0 means no smoothing.
    pub atten_a: f64,
    pub atten_b: f64,
}

impl SegmentRouting {
    #[must_use]
    pub fn travel_time(&self, q: f64) -> f64 {
        self.travel_a * q.max(1e-6).powf(self.travel_b)
    }
    #[must_use]
    pub fn attenuation(&self, q: f64) -> f64 {
        self.atten_a * q.max(1e-6).powf(self.atten_b)
    }
}

/// Route an inflow series down one segment.
///
/// `inflow` sampled every `step` seconds. Before the first sample the inflow is
/// taken as steady at its first value, so a run starts in balance. Returns the
/// outflow at the same times.
#[must_use]
pub fn route(inflow: &[f64], step: f64, seg: &SegmentRouting) -> Vec<f64> {
    let Some(&first) = inflow.first() else {
        return Vec::new();
    };
    let at = |t: f64| -> f64 {
        // inflow at time t (s from the first sample), linear between samples
        if t <= 0.0 {
            return first;
        }
        let x = t / step;
        let i = x.floor() as usize;
        if i + 1 >= inflow.len() {
            return inflow[inflow.len() - 1];
        }
        let f = x - i as f64;
        inflow[i] * (1.0 - f) + inflow[i + 1] * f
    };
    let mut out = Vec::with_capacity(inflow.len());
    let mut o = first;
    let mut prev_lagged = first;
    for (i, &q_in) in inflow.iter().enumerate() {
        let t = i as f64 * step;
        // The travel time for the flow now in the segment; outflow is a fair proxy.
        let lagged = at(t - seg.travel_time(o.max(q_in).max(f64::MIN_POSITIVE)));
        let k = seg.attenuation(o.max(f64::MIN_POSITIVE));
        o = if k <= 0.0 || i == 0 {
            lagged
        } else {
            // Linear reservoir dO/dt = (I - O)/k, trapezoidal in I, implicit in O.
            let c = step / (k + step / 2.0);
            o + c * (f64::midpoint(lagged, prev_lagged) - o)
        };
        prev_lagged = lagged;
        out.push(o);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const HOUR: f64 = 3_600.0;

    fn seg(travel_h: f64, atten_h: f64) -> SegmentRouting {
        SegmentRouting {
            travel_a: travel_h * HOUR,
            travel_b: 0.0,
            atten_a: atten_h * HOUR,
            atten_b: 0.0,
        }
    }

    #[test]
    fn steady_flow_passes_unchanged() {
        let out = route(&[250.0; 200], 900.0, &seg(6.0, 3.0));
        assert!(out.iter().all(|q| (q - 250.0).abs() < 1e-9));
    }

    #[test]
    fn pure_lag_delays_a_step_by_the_travel_time() {
        let step = 900.0;
        let inflow: Vec<f64> = (0..200)
            .map(|i| if i < 40 { 100.0 } else { 300.0 })
            .collect();
        let out = route(&inflow, step, &seg(5.0, 0.0)); // 5 h = 20 steps
        assert!((out[59] - 100.0).abs() < 1e-9);
        assert!((out[60] - 300.0).abs() < 1e-9);
    }

    #[test]
    fn attenuation_smooths_but_conserves_volume() {
        let step = 900.0;
        let mut inflow = vec![100.0; 600];
        for q in &mut inflow[100..140] {
            *q = 500.0; // a 10-hour pulse
        }
        let out = route(&inflow, step, &seg(4.0, 6.0));
        let peak_in = inflow.iter().copied().fold(0.0, f64::max);
        let peak_out = out.iter().copied().fold(0.0, f64::max);
        assert!(
            peak_out < peak_in * 0.9,
            "peak {peak_out} should be attenuated"
        );
        let excess = |s: &[f64]| s.iter().map(|q| q - 100.0).sum::<f64>();
        assert!(
            (excess(&out) - excess(&inflow)).abs() / excess(&inflow) < 0.01,
            "volume conserved"
        );
    }

    #[test]
    fn higher_flow_travels_faster_when_b_is_negative() {
        let r = SegmentRouting {
            travel_a: 40.0 * HOUR * 100f64.powf(0.3),
            travel_b: -0.3,
            atten_a: 0.0,
            atten_b: 0.0,
        };
        assert!(r.travel_time(400.0) < r.travel_time(100.0));
        assert!((r.travel_time(100.0) - 40.0 * HOUR).abs() < 1e-6);
    }

    #[test]
    fn empty_inflow_routes_to_nothing() {
        assert_eq!(route(&[], 900.0, &seg(1.0, 1.0)), Vec::<f64>::new());
    }
}
