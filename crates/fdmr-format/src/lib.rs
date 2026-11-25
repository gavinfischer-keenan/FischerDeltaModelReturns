//! Reader and writer for field files (`.fdf`) in the published flow-field
//! format, version 1. The format is described in `docs/format.md`.
//!
//! A field file holds one reach for one day: a header, a descriptor per field,
//! then each field as `steps x layers x elements` values, element index fastest.

use std::fmt;
use std::io::{self, Read, Write};

pub const MAGIC: &[u8; 4] = b"FDF1";
pub const VERSION_MAJOR: u16 = 1;
pub const VERSION_MINOR: u16 = 0;

/// Quantised value that means "no value".
pub const MISSING_I16: i16 = i16::MIN;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Encoding {
    F32,
    /// `value = offset + scale * q`
    QuantisedI16,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Field {
    pub name: String,
    pub unit: String,
    pub encoding: Encoding,
    /// `steps * layers * elements` values, element index fastest. `NaN` means no value.
    pub values: Vec<f32>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FieldFile {
    pub reach: String,
    /// 1, 2 or 3.
    pub dims: u8,
    pub elements: u32,
    /// Depth layers. Always present; 1 for 1D and 2D reaches.
    pub layers: u32,
    pub steps: u32,
    /// First step, seconds since 1970-01-01 UTC.
    pub t0: i64,
    pub step_s: u32,
    pub fields: Vec<Field>,
}

#[derive(Debug)]
pub enum FormatError {
    Io(io::Error),
    NotAFieldFile,
    /// The file's major version is one this reader does not know.
    UnsupportedVersion {
        major: u16,
        minor: u16,
    },
    Invalid(String),
}

impl fmt::Display for FormatError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(e) => write!(f, "i/o error: {e}"),
            Self::NotAFieldFile => write!(f, "not a field file (bad magic)"),
            Self::UnsupportedVersion { major, minor } => write!(
                f,
                "field file version {major}.{minor} is not supported; this reader knows version {VERSION_MAJOR}"
            ),
            Self::Invalid(s) => write!(f, "invalid field file: {s}"),
        }
    }
}

impl std::error::Error for FormatError {}

impl From<io::Error> for FormatError {
    fn from(e: io::Error) -> Self {
        Self::Io(e)
    }
}

impl FieldFile {
    /// Number of values each field must hold.
    #[must_use]
    pub fn values_per_field(&self) -> usize {
        self.steps as usize * self.layers as usize * self.elements as usize
    }

    /// The field with this name, if present.
    #[must_use]
    pub fn field(&self, name: &str) -> Option<&Field> {
        self.fields.iter().find(|f| f.name == name)
    }

    /// Index into a field's values.
    #[must_use]
    pub fn index(&self, step: u32, layer: u32, element: u32) -> usize {
        (step as usize * self.layers as usize + layer as usize) * self.elements as usize
            + element as usize
    }

    fn check(&self) -> Result<(), FormatError> {
        if !(1..=3).contains(&self.dims) {
            return Err(FormatError::Invalid(format!(
                "dims must be 1, 2 or 3, not {}",
                self.dims
            )));
        }
        if self.layers == 0 || self.elements == 0 {
            return Err(FormatError::Invalid(
                "layers and elements must be at least 1".into(),
            ));
        }
        if self.dims < 3 && self.layers != 1 {
            return Err(FormatError::Invalid(
                "1D and 2D reaches have exactly one layer".into(),
            ));
        }
        let n = self.values_per_field();
        for f in &self.fields {
            if f.values.len() != n {
                return Err(FormatError::Invalid(format!(
                    "field {} has {} values, expected {n}",
                    f.name,
                    f.values.len()
                )));
            }
        }
        Ok(())
    }

    /// Write the file.
    ///
    /// # Errors
    /// If the file is inconsistent, or on an i/o error.
    pub fn write_to<W: Write>(&self, w: &mut W) -> Result<(), FormatError> {
        self.check()?;
        w.write_all(MAGIC)?;
        w.write_all(&VERSION_MAJOR.to_le_bytes())?;
        w.write_all(&VERSION_MINOR.to_le_bytes())?;
        write_str(w, &self.reach)?;
        w.write_all(&[self.dims])?;
        w.write_all(&self.elements.to_le_bytes())?;
        w.write_all(&self.layers.to_le_bytes())?;
        w.write_all(&self.steps.to_le_bytes())?;
        w.write_all(&self.t0.to_le_bytes())?;
        w.write_all(&self.step_s.to_le_bytes())?;
        let nfields = u16::try_from(self.fields.len())
            .map_err(|_| FormatError::Invalid("too many fields".into()))?;
        w.write_all(&nfields.to_le_bytes())?;

        let quant: Vec<(f64, f64)> = self.fields.iter().map(quantisation).collect();
        for (f, &(scale, offset)) in self.fields.iter().zip(&quant) {
            write_str(w, &f.name)?;
            write_str(w, &f.unit)?;
            w.write_all(&[match f.encoding {
                Encoding::F32 => 0,
                Encoding::QuantisedI16 => 1,
            }])?;
            w.write_all(&scale.to_le_bytes())?;
            w.write_all(&offset.to_le_bytes())?;
        }
        for (f, &(scale, offset)) in self.fields.iter().zip(&quant) {
            match f.encoding {
                Encoding::F32 => {
                    for v in &f.values {
                        w.write_all(&v.to_le_bytes())?;
                    }
                }
                Encoding::QuantisedI16 => {
                    for &v in &f.values {
                        w.write_all(&quantise(v, scale, offset).to_le_bytes())?;
                    }
                }
            }
        }
        Ok(())
    }

    /// Read a file.
    ///
    /// # Errors
    /// If the bytes are not a version 1 field file, or on an i/o error.
    pub fn read_from<R: Read>(r: &mut R) -> Result<Self, FormatError> {
        let mut magic = [0u8; 4];
        r.read_exact(&mut magic)?;
        if &magic != MAGIC {
            return Err(FormatError::NotAFieldFile);
        }
        let major = read_u16(r)?;
        let minor = read_u16(r)?;
        if major != VERSION_MAJOR {
            return Err(FormatError::UnsupportedVersion { major, minor });
        }
        let reach = read_str(r)?;
        let dims = read_u8(r)?;
        let elements = read_u32(r)?;
        let layers = read_u32(r)?;
        let steps = read_u32(r)?;
        let t0 = i64::from_le_bytes(read_n::<8, _>(r)?);
        let step_s = read_u32(r)?;
        let nfields = read_u16(r)?;

        let mut descs = Vec::with_capacity(nfields as usize);
        for _ in 0..nfields {
            let name = read_str(r)?;
            let unit = read_str(r)?;
            let encoding = match read_u8(r)? {
                0 => Encoding::F32,
                1 => Encoding::QuantisedI16,
                e => return Err(FormatError::Invalid(format!("unknown encoding {e}"))),
            };
            let scale = f64::from_le_bytes(read_n::<8, _>(r)?);
            let offset = f64::from_le_bytes(read_n::<8, _>(r)?);
            descs.push((name, unit, encoding, scale, offset));
        }

        let n = steps as usize * layers as usize * elements as usize;
        let mut fields = Vec::with_capacity(descs.len());
        for (name, unit, encoding, scale, offset) in descs {
            let mut values = Vec::with_capacity(n);
            match encoding {
                Encoding::F32 => {
                    for _ in 0..n {
                        values.push(f32::from_le_bytes(read_n::<4, _>(r)?));
                    }
                }
                Encoding::QuantisedI16 => {
                    for _ in 0..n {
                        let q = i16::from_le_bytes(read_n::<2, _>(r)?);
                        values.push(if q == MISSING_I16 {
                            f32::NAN
                        } else {
                            (offset + scale * f64::from(q)) as f32
                        });
                    }
                }
            }
            fields.push(Field {
                name,
                unit,
                encoding,
                values,
            });
        }
        let file = Self {
            reach,
            dims,
            elements,
            layers,
            steps,
            t0,
            step_s,
            fields,
        };
        file.check()?;
        Ok(file)
    }
}

/// Scale and offset for a field. Quantised fields map their range onto
/// -32767..=32767, keeping -32768 for "no value".
fn quantisation(f: &Field) -> (f64, f64) {
    if f.encoding == Encoding::F32 {
        return (1.0, 0.0);
    }
    let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
    for &v in &f.values {
        if v.is_finite() {
            lo = lo.min(f64::from(v));
            hi = hi.max(f64::from(v));
        }
    }
    if !lo.is_finite() {
        return (1.0, 0.0);
    }
    let offset = f64::midpoint(lo, hi);
    let half = (hi - lo) / 2.0;
    let scale = if half > 0.0 { half / 32767.0 } else { 1.0 };
    (scale, offset)
}

fn quantise(v: f32, scale: f64, offset: f64) -> i16 {
    if !v.is_finite() {
        return MISSING_I16;
    }
    let q = ((f64::from(v) - offset) / scale).round();
    q.clamp(-32767.0, 32767.0) as i16
}

fn write_str<W: Write>(w: &mut W, s: &str) -> Result<(), FormatError> {
    let len = u16::try_from(s.len()).map_err(|_| FormatError::Invalid("string too long".into()))?;
    w.write_all(&len.to_le_bytes())?;
    w.write_all(s.as_bytes())?;
    Ok(())
}

fn read_n<const N: usize, R: Read>(r: &mut R) -> Result<[u8; N], FormatError> {
    let mut b = [0u8; N];
    r.read_exact(&mut b)?;
    Ok(b)
}
fn read_u8<R: Read>(r: &mut R) -> Result<u8, FormatError> {
    Ok(read_n::<1, _>(r)?[0])
}
fn read_u16<R: Read>(r: &mut R) -> Result<u16, FormatError> {
    Ok(u16::from_le_bytes(read_n::<2, _>(r)?))
}
fn read_u32<R: Read>(r: &mut R) -> Result<u32, FormatError> {
    Ok(u32::from_le_bytes(read_n::<4, _>(r)?))
}
fn read_str<R: Read>(r: &mut R) -> Result<String, FormatError> {
    let len = read_u16(r)? as usize;
    let mut b = vec![0u8; len];
    r.read_exact(&mut b)?;
    String::from_utf8(b).map_err(|_| FormatError::Invalid("string is not UTF-8".into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(encoding: Encoding) -> FieldFile {
        let (elements, steps) = (5u32, 4u32);
        let n = (elements * steps) as usize;
        FieldFile {
            reach: "river".into(),
            dims: 1,
            elements,
            layers: 1,
            steps,
            t0: 1_764_028_800,
            step_s: 900,
            fields: vec![
                Field {
                    name: "velocity".into(),
                    unit: "m/s".into(),
                    encoding,
                    values: (0..n).map(|i| (i as f32 * 0.37).sin() * 1.5).collect(),
                },
                Field {
                    name: "volume".into(),
                    unit: "m3".into(),
                    encoding: Encoding::F32,
                    values: (0..n).map(|i| 1.0e6 + i as f32).collect(),
                },
            ],
        }
    }

    fn round_trip(f: &FieldFile) -> FieldFile {
        let mut buf = Vec::new();
        f.write_to(&mut buf).unwrap();
        FieldFile::read_from(&mut buf.as_slice()).unwrap()
    }

    #[test]
    fn f32_round_trip_is_exact() {
        let f = sample(Encoding::F32);
        assert_eq!(round_trip(&f), f);
    }

    #[test]
    fn quantised_error_is_within_half_a_step() {
        let f = sample(Encoding::QuantisedI16);
        let g = round_trip(&f);
        let (a, b) = (&f.fields[0].values, &g.fields[0].values);
        let range = 3.0_f32; // the sample's values lie within -1.5..=1.5
        let half_step = range / 2.0 / 32767.0 / 2.0;
        for (x, y) in a.iter().zip(b) {
            assert!((x - y).abs() <= half_step * 1.01, "{x} vs {y}");
        }
        assert_eq!(g.fields[1], f.fields[1]);
    }

    #[test]
    fn missing_values_survive_quantisation() {
        let mut f = sample(Encoding::QuantisedI16);
        f.fields[0].values[3] = f32::NAN;
        let g = round_trip(&f);
        assert!(g.fields[0].values[3].is_nan());
        assert!(g.fields[0].values[2].is_finite());
    }

    #[test]
    fn constant_field_quantises_exactly() {
        let mut f = sample(Encoding::QuantisedI16);
        f.fields[0].values.iter_mut().for_each(|v| *v = 0.25);
        let g = round_trip(&f);
        assert!(g.fields[0].values.iter().all(|v| (v - 0.25).abs() < 1e-6));
    }

    #[test]
    fn unknown_major_version_is_refused() {
        let mut buf = Vec::new();
        sample(Encoding::F32).write_to(&mut buf).unwrap();
        buf[4..6].copy_from_slice(&2u16.to_le_bytes());
        match FieldFile::read_from(&mut buf.as_slice()) {
            Err(FormatError::UnsupportedVersion { major: 2, .. }) => {}
            other => panic!("expected a version error, got {other:?}"),
        }
    }

    #[test]
    fn wrong_magic_is_refused() {
        let mut buf = Vec::new();
        sample(Encoding::F32).write_to(&mut buf).unwrap();
        buf[0] = b'X';
        assert!(matches!(
            FieldFile::read_from(&mut buf.as_slice()),
            Err(FormatError::NotAFieldFile)
        ));
    }

    #[test]
    fn depth_axis_round_trips_for_3d() {
        let mut f = sample(Encoding::F32);
        f.dims = 3;
        f.layers = 3;
        f.elements = 2;
        let n = f.values_per_field();
        for fld in &mut f.fields {
            fld.values = (0..n).map(|i| i as f32).collect();
        }
        let g = round_trip(&f);
        assert_eq!(g.layers, 3);
        assert_eq!(
            g.field("velocity").unwrap().values[g.index(1, 2, 1)],
            g.index(1, 2, 1) as f32
        );
    }

    #[test]
    fn two_d_reach_with_layers_is_rejected() {
        let mut f = sample(Encoding::F32);
        f.layers = 2;
        let mut buf = Vec::new();
        assert!(matches!(f.write_to(&mut buf), Err(FormatError::Invalid(_))));
    }

    #[test]
    fn wrong_value_count_is_rejected() {
        let mut f = sample(Encoding::F32);
        f.fields[0].values.pop();
        let mut buf = Vec::new();
        assert!(matches!(f.write_to(&mut buf), Err(FormatError::Invalid(_))));
    }
}
