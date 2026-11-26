//! Salinity from electrical conductivity (specification 7.5).
//!
//! Conductivity is how the Delta measures salt; the map shows salinity. This
//! is the Practical Salinity Scale of 1978 (PSS-78; UNESCO 1981, ref:unesco1981), applied to
//! specific conductance, which is conductivity already corrected to 25 °C.
//! The scale is defined for 2 to 42; below 2, as in most of the Delta, it is
//! used as it stands, which reads a little low but keeps fresh water near 0.

/// Conductivity of standard seawater (salinity 35) at 25 °C, µS/cm.
pub const SEAWATER_SC: f64 = 53_087.0;

const A: [f64; 6] = [0.0080, -0.1692, 25.3851, 14.0941, -7.0261, 2.7081];
const B: [f64; 6] = [0.0005, -0.0056, -0.0066, -0.0375, 0.0636, -0.0144];
const K: f64 = 0.0162;

/// Practical salinity (psu) from specific conductance (µS/cm at 25 °C).
#[must_use]
pub fn practical_salinity(specific_conductance: f64) -> f64 {
    if !specific_conductance.is_finite() || specific_conductance <= 0.0 {
        return 0.0;
    }
    let r = (specific_conductance / SEAWATER_SC).sqrt();
    let t = 25.0 - 15.0;
    let mut s = 0.0;
    let mut dt = 0.0;
    let mut p = 1.0; // r to the power i
    for i in 0..6 {
        s += A[i] * p;
        dt += B[i] * p;
        p *= r;
    }
    (s + t / (1.0 + K * t) * dt).max(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seawater_reads_35() {
        assert!((practical_salinity(SEAWATER_SC) - 35.0).abs() < 1e-3);
    }

    #[test]
    fn delta_waters_read_sensibly() {
        // Fresh river water, about 100 µS/cm: a few hundredths.
        let fresh = practical_salinity(100.0);
        assert!(fresh > 0.0 && fresh < 0.1, "{fresh}");
        // A brackish 10,000 µS/cm, roughly the western Delta at high tide: about 5.7.
        let brackish = practical_salinity(10_000.0);
        assert!((brackish - 5.7).abs() < 0.2, "{brackish}");
        // More conductive water is saltier.
        assert!(practical_salinity(20_000.0) > brackish);
        assert!(practical_salinity(f64::NAN) == 0.0 && practical_salinity(-5.0) == 0.0);
    }
}
