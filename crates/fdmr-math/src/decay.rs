//! First-order decay by half-life (specification section 3.3).

/// Fraction of mass left after `dt` seconds, for a half-life in seconds.
/// `None` means the pollutant does not break down.
#[must_use]
pub fn remaining_fraction(dt: f64, half_life: Option<f64>) -> f64 {
    match half_life {
        Some(h) if h > 0.0 => (-std::f64::consts::LN_2 * dt / h).exp(),
        _ => 1.0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn half_left_after_one_half_life() {
        assert!((remaining_fraction(3_600.0, Some(3_600.0)) - 0.5).abs() < 1e-12);
        assert!((remaining_fraction(7_200.0, Some(3_600.0)) - 0.25).abs() < 1e-12);
    }

    #[test]
    fn steps_compose() {
        let h = Some(5_000.0);
        let whole = remaining_fraction(900.0, h);
        let parts = remaining_fraction(300.0, h).powi(3);
        assert!((whole - parts).abs() < 1e-12);
    }

    #[test]
    fn no_half_life_means_no_decay() {
        assert!((remaining_fraction(1.0e9, None) - 1.0).abs() < f64::EPSILON);
    }
}
