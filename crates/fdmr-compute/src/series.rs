//! Day files: a reach's published fields over time, read and interpolated
//! (docs/format.md). Shared by the network and mesh tracers.

use fdmr_format::{FieldFile, FormatError};
use fdmr_math::signal::godin;

/// A reach's day files of one kind (such as a network's channels, or a mesh's nodes).
#[derive(Debug, Default)]
pub(crate) struct Series {
    pub(crate) elements: usize,
    days: Vec<FieldFile>,
}

impl Series {
    pub(crate) fn load_day(&mut self, bytes: &[u8], needs: &[&str]) -> Result<(), FormatError> {
        let f = FieldFile::read_from(&mut &bytes[..])?;
        for name in needs {
            if f.field(name).is_none() {
                return Err(FormatError::Invalid(format!(
                    "day file has no {name} field"
                )));
            }
        }
        if !self.days.is_empty() && f.elements as usize != self.elements {
            return Err(FormatError::Invalid(
                "day files disagree about the number of elements".into(),
            ));
        }
        self.elements = f.elements as usize;
        let at = self.days.partition_point(|d| d.t0 < f.t0);
        self.days.insert(at, f);
        Ok(())
    }

    /// The day and step at or before `t`, and how far to the next step (0..1).
    fn locate(&self, t: f64) -> Option<(usize, usize, f64)> {
        #[allow(clippy::cast_precision_loss)]
        let d = self.days.iter().rposition(|d| d.t0 as f64 <= t)?;
        let day = &self.days[d];
        #[allow(clippy::cast_precision_loss)]
        let pos = (t - day.t0 as f64) / f64::from(day.step_s);
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let i = pos.floor() as usize;
        if i >= day.steps as usize {
            return Some((d, day.steps as usize - 1, 0.0));
        }
        Some((d, i, pos - pos.floor()))
    }

    /// Step `i` of day `d`, or the next day's first step when `i` is one past the end.
    fn row(&self, d: usize, i: usize, name: &str) -> &[f32] {
        let (day, i) = if i >= self.days[d].steps as usize && d + 1 < self.days.len() {
            (&self.days[d + 1], 0)
        } else {
            (&self.days[d], i.min(self.days[d].steps as usize - 1))
        };
        let v = &day.field(name).expect("checked when loaded").values;
        &v[i * self.elements..(i + 1) * self.elements]
    }

    /// The time of the last step held, s; `None` with no days.
    pub(crate) fn end(&self) -> Option<f64> {
        let d = self.days.last()?;
        #[allow(clippy::cast_precision_loss)]
        Some(d.t0 as f64 + f64::from(d.step_s) * f64::from(d.steps.saturating_sub(1)))
    }

    /// A copy with every field tidally filtered (Godin's filter, ref:godin1972), element by
    /// element, across all the days held taken as one series: the flow with
    /// the tides taken out, for showing net flow (specification 3.5). An
    /// element with a missing value anywhere is copied unfiltered.
    pub(crate) fn tidal_mean(&self) -> Series {
        let mut out = Series {
            elements: self.elements,
            days: self.days.clone(),
        };
        let Some(first) = self.days.first() else {
            return out;
        };
        let per_hour = 3600.0 / f64::from(first.step_s);
        let cols = self.elements * first.layers as usize;
        let total: usize = self.days.iter().map(|d| d.steps as usize).sum();
        let mut x = vec![0.0; total];
        for (k, field) in first.fields.iter().enumerate() {
            if self
                .days
                .iter()
                .any(|d| d.fields.get(k).is_none_or(|f| f.name != field.name))
            {
                continue; // days that do not line up are left as they are
            }
            for c in 0..cols {
                let mut s = 0;
                for d in &self.days {
                    for i in 0..d.steps as usize {
                        x[s] = f64::from(d.fields[k].values[i * cols + c]);
                        s += 1;
                    }
                }
                if x.iter().any(|v| !v.is_finite()) {
                    continue;
                }
                let m = godin(&x, per_hour);
                let mut s = 0;
                for d in &mut out.days {
                    for i in 0..d.steps as usize {
                        #[allow(clippy::cast_possible_truncation)]
                        let v = m[s] as f32;
                        d.fields[k].values[i * cols + c] = v;
                        s += 1;
                    }
                }
            }
        }
        out
    }

    /// Whether the days hold a field of this name.
    pub(crate) fn has(&self, name: &str) -> bool {
        self.days.first().is_some_and(|d| d.field(name).is_some())
    }

    /// A field at `t` for every element, linear between steps; `None` before the
    /// first day. A value missing at either step (NaN) stays missing.
    pub(crate) fn at(&self, t: f64, name: &str) -> Option<Vec<f64>> {
        let (d, i, f) = self.locate(t)?;
        let (a, b) = (self.row(d, i, name), self.row(d, i + 1, name));
        Some(
            a.iter()
                .zip(b)
                .map(|(x, y)| f64::from(*x) * (1.0 - f) + f64::from(*y) * f)
                .collect(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use fdmr_format::{Encoding, Field};

    #[test]
    fn tidal_mean_filters_across_the_days_as_one_series() {
        // Two elements, five days of 15-minute steps, in five day files.
        let per_day = 96usize;
        let x = |i: usize, e: usize| {
            #[allow(clippy::cast_precision_loss)]
            let h = i as f64 / 4.0;
            #[allow(clippy::cast_precision_loss)]
            let base = 100.0 * (e + 1) as f64;
            base + 40.0 * (std::f64::consts::TAU * h / 12.4206).sin()
        };
        let mut s = Series::default();
        for d in 0..5 {
            let values: Vec<f32> = (0..per_day)
                .flat_map(|i| (0..2).map(move |e| x(d * per_day + i, e) as f32))
                .collect();
            let f = FieldFile {
                reach: "test".into(),
                dims: 1,
                elements: 2,
                layers: 1,
                steps: 96,
                t0: i64::try_from(d * 86_400).unwrap(),
                step_s: 900,
                fields: vec![Field {
                    name: "flow".into(),
                    unit: "m3/s".into(),
                    encoding: Encoding::F32,
                    values,
                }],
            };
            let mut b = Vec::new();
            f.write_to(&mut b).unwrap();
            s.load_day(&b, &["flow"]).unwrap();
        }
        let m = s.tidal_mean();
        for e in 0..2 {
            let whole: Vec<f64> = (0..5 * per_day)
                .map(|i| f64::from(x(i, e) as f32))
                .collect();
            let want = godin(&whole, 4.0);
            for i in [0, 150, 240, 300, 479] {
                let got = m.at(i as f64 * 900.0, "flow").unwrap()[e];
                assert!(
                    (got - want[i]).abs() < 1e-3,
                    "e {e} i {i}: {got} vs {}",
                    want[i]
                );
            }
        }
        // The middle day has the tide gone.
        let mid = m.at(2.5 * 86_400.0, "flow").unwrap();
        assert!(
            (mid[0] - 100.0).abs() < 0.5 && (mid[1] - 200.0).abs() < 0.5,
            "{mid:?}"
        );
    }
}
