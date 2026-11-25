//! Certainty about the flow (specification section 8.2).
//!
//! Inside a reach's solid forecast, certainty is 1. Past it, certainty falls
//! steadily to a floor at the end of the forecast. A parcel keeps the lowest
//! certainty it has passed through.

/// One reach's certainty settings. Times are seconds after "now"; the past is
/// built from measurements and always has certainty 1.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ReachCertainty {
    /// End of the solid forecast, s after now.
    pub solid_until: f64,
    /// End of the forecast, s after now.
    pub forecast_end: f64,
    /// Certainty at the end of the forecast.
    pub floor: f64,
}

impl ReachCertainty {
    /// Certainty at `t` seconds after now (negative for the past).
    #[must_use]
    pub fn at(&self, t: f64) -> f64 {
        if t <= self.solid_until {
            return 1.0;
        }
        let span = self.forecast_end - self.solid_until;
        if span <= 0.0 || t >= self.forecast_end {
            return self.floor;
        }
        1.0 - (1.0 - self.floor) * (t - self.solid_until) / span
    }
}

/// A parcel's certainty after it passes through flow of certainty `here`.
#[must_use]
pub fn carry(parcel: f64, here: f64) -> f64 {
    parcel.min(here)
}

/// Certainty as words for read-off answers (specification section 3.4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Words {
    Measured,
    Forecast,
    RoughGuide,
}

/// `in_past` for times before now, which are built from measured conditions.
#[must_use]
pub fn words(certainty: f64, in_past: bool) -> Words {
    if in_past {
        Words::Measured
    } else if certainty >= 0.5 {
        Words::Forecast
    } else {
        Words::RoughGuide
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DAY: f64 = 86_400.0;

    fn river() -> ReachCertainty {
        ReachCertainty {
            solid_until: 5.0 * DAY,
            forecast_end: 14.0 * DAY,
            floor: 0.2,
        }
    }

    #[test]
    fn solid_then_linear_to_floor() {
        let r = river();
        assert!((r.at(-3.0 * DAY) - 1.0).abs() < f64::EPSILON);
        assert!((r.at(5.0 * DAY) - 1.0).abs() < f64::EPSILON);
        assert!((r.at(9.5 * DAY) - 0.6).abs() < 1e-12);
        assert!((r.at(14.0 * DAY) - 0.2).abs() < 1e-12);
        assert!((r.at(20.0 * DAY) - 0.2).abs() < 1e-12);
    }

    #[test]
    fn parcel_keeps_its_lowest_certainty() {
        let mut c = 1.0;
        for here in [1.0, 0.7, 1.0, 0.9] {
            c = carry(c, here);
        }
        assert!((c - 0.7).abs() < f64::EPSILON);
    }

    #[test]
    fn words_follow_the_three_tiers() {
        assert_eq!(words(1.0, true), Words::Measured);
        assert_eq!(words(1.0, false), Words::Forecast);
        assert_eq!(words(0.5, false), Words::Forecast);
        assert_eq!(words(0.49, false), Words::RoughGuide);
    }
}
