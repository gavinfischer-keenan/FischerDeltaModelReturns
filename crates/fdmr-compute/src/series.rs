//! Day files: a reach's published fields over time, read and interpolated
//! (docs/format.md). Shared by the network and mesh tracers.

use fdmr_format::{FieldFile, FormatError};

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
