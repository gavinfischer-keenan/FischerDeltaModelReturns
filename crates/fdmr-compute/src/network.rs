//! Flow through a channel network (docs/format.md, network reaches; specification 7.2).
//!
//! Holds a network reach's day files and answers what the water is doing in
//! each channel at any moment. The first use is drawing: each channel's mean
//! velocity, for colouring the map. The network tracer builds on it.

use fdmr_format::{FieldFile, FormatError};

/// A network reach's published flow, day by day.
#[derive(Debug, Default)]
pub struct NetworkFlow {
    elements: usize,
    days: Vec<FieldFile>,
}

impl NetworkFlow {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Load one day's field file. It must hold `flow_up`, `flow_down` and `area`.
    ///
    /// # Errors
    /// If the bytes are not a field file, lack those fields, or disagree with the
    /// days already loaded about the number of channels.
    pub fn load_day(&mut self, bytes: &[u8]) -> Result<(), FormatError> {
        let f = FieldFile::read_from(&mut &bytes[..])?;
        for name in ["flow_up", "flow_down", "area"] {
            if f.field(name).is_none() {
                return Err(FormatError::Invalid(format!(
                    "network day file has no {name} field"
                )));
            }
        }
        if !self.days.is_empty() && f.elements as usize != self.elements {
            return Err(FormatError::Invalid(
                "day files disagree about the number of channels".into(),
            ));
        }
        self.elements = f.elements as usize;
        let at = self.days.partition_point(|d| d.t0 < f.t0);
        self.days.insert(at, f);
        Ok(())
    }

    #[must_use]
    pub fn elements(&self) -> usize {
        self.elements
    }

    /// The day and step at or before `t`, and how far to the next step (0..1).
    fn locate(&self, t: f64) -> Option<(usize, usize, f64)> {
        #[allow(clippy::cast_precision_loss)]
        let d = self.days.iter().rposition(|d| d.t0 as f64 <= t)?;
        let day = &self.days[d];
        let pos = (t - day.t0 as f64) / f64::from(day.step_s);
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let i = pos.floor() as usize;
        if i >= day.steps as usize {
            // Past this day's last step: hold it, unless the next day carries on.
            return Some((d, day.steps as usize - 1, 0.0));
        }
        Some((d, i, pos - pos.floor()))
    }

    /// One field's value for every channel at step `i` of day `d`, or of the
    /// following day's first step when `i` is one past the end.
    fn row(&self, d: usize, i: usize, name: &str) -> &[f32] {
        let (day, i) = if i >= self.days[d].steps as usize && d + 1 < self.days.len() {
            (&self.days[d + 1], 0)
        } else {
            (&self.days[d], i.min(self.days[d].steps as usize - 1))
        };
        let v = &day.field(name).expect("checked when loaded").values;
        &v[i * self.elements..(i + 1) * self.elements]
    }

    /// Each channel's mean velocity at `t` (s since 1970), m/s, positive from
    /// its upstream junction to its downstream one: the mean of the flows at its
    /// two ends over its mean area, linear in time between steps. `None` before
    /// the first day.
    #[must_use]
    pub fn velocities(&self, t: f64) -> Option<Vec<f32>> {
        let (d, i, f) = self.locate(t)?;
        let v = |i: usize| -> Vec<f64> {
            let (up, down, area) = (
                self.row(d, i, "flow_up"),
                self.row(d, i, "flow_down"),
                self.row(d, i, "area"),
            );
            (0..self.elements)
                .map(|k| {
                    let a = f64::from(area[k]);
                    if a > 0.0 {
                        f64::midpoint(f64::from(up[k]), f64::from(down[k])) / a
                    } else {
                        0.0
                    }
                })
                .collect()
        };
        let (a, b) = (v(i), v(i + 1));
        #[allow(clippy::cast_possible_truncation)]
        Some(
            a.iter()
                .zip(&b)
                .map(|(x, y)| (x * (1.0 - f) + y * f) as f32)
                .collect(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use fdmr_format::{Encoding, Field};

    fn day(t0: i64, steps: u32, flow: &[f32]) -> Vec<u8> {
        // Two channels; flow per step per channel, area 10 everywhere.
        let n = 2;
        let f = FieldFile {
            reach: "net".into(),
            dims: 1,
            elements: n,
            layers: 1,
            steps,
            t0,
            step_s: 900,
            fields: vec![
                Field {
                    name: "flow_up".into(),
                    unit: "m3/s".into(),
                    encoding: Encoding::F32,
                    values: flow.to_vec(),
                },
                Field {
                    name: "flow_down".into(),
                    unit: "m3/s".into(),
                    encoding: Encoding::F32,
                    values: flow.to_vec(),
                },
                Field {
                    name: "area".into(),
                    unit: "m2".into(),
                    encoding: Encoding::F32,
                    values: vec![10.0; flow.len()],
                },
            ],
        };
        let mut b = Vec::new();
        f.write_to(&mut b).unwrap();
        b
    }

    #[test]
    fn velocity_is_flow_over_area_and_linear_in_time() {
        let mut net = NetworkFlow::new();
        net.load_day(&day(0, 2, &[10.0, -20.0, 30.0, -40.0]))
            .unwrap();
        assert_eq!(net.elements(), 2);
        let v = net.velocities(450.0).unwrap(); // halfway between the two steps
        assert!((v[0] - 2.0).abs() < 1e-6 && (v[1] + 3.0).abs() < 1e-6);
        assert!(net.velocities(-1.0).is_none());
    }

    #[test]
    fn days_join_across_midnight_in_any_load_order() {
        let mut net = NetworkFlow::new();
        net.load_day(&day(1800, 1, &[50.0, 50.0])).unwrap();
        net.load_day(&day(0, 2, &[10.0, 10.0, 30.0, 30.0])).unwrap();
        // 1350 s is halfway from the first day's last step (900) to the next day's first (1800).
        let v = net.velocities(1350.0).unwrap();
        assert!((v[0] - 4.0).abs() < 1e-6, "{v:?}");
    }

    #[test]
    fn a_day_without_flow_is_refused() {
        let f = FieldFile {
            reach: "x".into(),
            dims: 1,
            elements: 1,
            layers: 1,
            steps: 1,
            t0: 0,
            step_s: 900,
            fields: vec![],
        };
        let mut b = Vec::new();
        f.write_to(&mut b).unwrap();
        assert!(NetworkFlow::new().load_day(&b).is_err());
    }
}
