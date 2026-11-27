//! The compute engine (specification section 4.2).
//!
//! Moves pollutant parcels and flow particles through a published 1D flow
//! field, and works out concentrations. It knows nothing about the screen: it
//! is handed field-file bytes and answers in distances along the reach
//! (chainage, m), element indices and numbers. The UI draws.
//!
//! Parcels move with the local velocity plus a random step for dispersion
//! (specification section 8.1, `fdmr_math::parcel`). Flow particles move with
//! the velocity only and carry no mass: they show the water (section 8.4).
//!
//! Where the reach publishes its lateral mixing (`lateral_mixing`) and its
//! widths are given (`set_widths`), parcels spread by the mechanism behind the
//! dispersion coefficient instead (issue #29): each has a place across the
//! channel, η from −1 to 1, moves downstream at the speed there,
//! U (1 + c (1/3 − η²)) (`fdmr_math::dispersion::shear_profile_c`), and
//! wanders across at the lateral mixing ε, reflected at the banks. Far from a
//! release this spreads at Fischer's K; near it, the cloud is as drawn out and
//! lopsided as the shear makes it (Fischer 1966, ref:fischer1966). A release
//! starts spread evenly across the channel, Fischer's plane source.

use fdmr_format::{FieldFile, FormatError};
use fdmr_math::{decay, dispersion, parcel, rng::Rng};

pub mod mesh;
pub mod network;
mod series;

/// Largest time step the engine takes, s.
pub const MAX_DT: f64 = 60.0;

/// How long a flow particle lives, s (model time): a random while in this
/// range, then it starts again somewhere else in its reach. Without it,
/// particles gather where the currents converge, along shores and on drying
/// shoals, and the open water empties; with it the density stays even
/// whatever the currents do. Three to nine hours: long enough to show a tide
/// carrying the water, short enough to keep the water filled.
pub const PARTICLE_LIFE_S: (f64, f64) = (3.0 * 3600.0, 9.0 * 3600.0);

/// A new flow particle's life (`PARTICLE_LIFE_S`).
pub(crate) fn particle_life(rng: &mut Rng) -> f64 {
    PARTICLE_LIFE_S.0 + rng.uniform() * (PARTICLE_LIFE_S.1 - PARTICLE_LIFE_S.0)
}

/// The particle to give up for one arriving from another reach: the one
/// furthest through its life.
pub(crate) fn oldest(ages: &[f64], lives: &[f64]) -> Option<usize> {
    (0..ages.len()).max_by(|&a, &b| (ages[a] / lives[a]).total_cmp(&(ages[b] / lives[b])))
}

/// One day of a reach's published flow field.
struct Day {
    t0: f64,
    step: f64,
    steps: usize,
    velocity: Vec<f32>,
    volume: Vec<f32>,
    dispersion: Vec<f32>,
    /// Lateral mixing ε, m²/s, where published.
    lateral: Option<Vec<f32>>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Release {
    /// Chainage of the release point, m.
    pub x: f64,
    /// Start, s since 1970.
    pub start: f64,
    /// 0 for all at once.
    pub duration: f64,
    /// Total mass released, kg.
    pub mass: f64,
    /// Half-life, s; None for no breakdown.
    pub half_life: Option<f64>,
    /// Parcels to carry it.
    pub parcels: u32,
}

#[derive(Debug, Clone, Copy)]
struct Parcel {
    x: f64,
    /// Across the channel, −1 to 1 (used where the reach has lateral mixing).
    lat: f64,
    mass: f64,
    release: usize,
    in_water: bool,
}

#[derive(Debug, Clone, Copy)]
struct FlowParticle {
    x: f64,
    /// Across the channel, -1..1, for drawing only.
    lateral: f32,
}

/// Where the released mass is (specification section 8.1).
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct MassBalance {
    pub released: f64,
    pub in_water: f64,
    pub past_end: f64,
    pub decayed: f64,
}

#[derive(Debug)]
pub enum EngineError {
    Format(FormatError),
    WrongReach(String),
    MissingField(&'static str),
}

impl std::fmt::Display for EngineError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Format(e) => write!(f, "{e}"),
            Self::WrongReach(s) => write!(f, "field file does not match this reach: {s}"),
            Self::MissingField(n) => write!(f, "field file has no {n} field"),
        }
    }
}

impl std::error::Error for EngineError {}

pub struct Engine {
    starts: Vec<f64>,
    ends: Vec<f64>,
    days: Vec<Day>,
    time: f64,
    rng: Rng,
    releases: Vec<(Release, u32)>, // release, parcels emitted so far
    parcels: Vec<Parcel>,
    particles: Vec<FlowParticle>,
    balance: MassBalance,
    /// Parcels that passed the end since last taken: (time, mass, half-life),
    /// for handing on to the next reach (specification 7.4).
    outflow: Vec<(f64, f64, Option<f64>)>,
    /// Water-quality fields per element (`<day>.quality.fdf`), loaded to be shown.
    quality: series::Series,
    /// Each element's water-surface width, m (`set_widths`).
    widths: Option<Vec<f64>>,
}

impl Engine {
    /// A reach of elements given by their start and end chainage, m, upstream to downstream.
    #[must_use]
    pub fn new(elements: &[(f64, f64)], start_time: f64, seed: u64) -> Self {
        Self {
            starts: elements.iter().map(|e| e.0).collect(),
            ends: elements.iter().map(|e| e.1).collect(),
            days: Vec::new(),
            time: start_time,
            rng: Rng::new(seed),
            releases: Vec::new(),
            parcels: Vec::new(),
            particles: Vec::new(),
            balance: MassBalance::default(),
            outflow: Vec::new(),
            quality: series::Series::default(),
            widths: None,
        }
    }

    /// Each element's water-surface width, m (the reach's geometry `width_m`):
    /// with the published lateral mixing, parcels spread by the cross-channel
    /// shear (module docs).
    ///
    /// # Panics
    /// If there is not one width per element.
    pub fn set_widths(&mut self, widths: &[f64]) {
        assert_eq!(widths.len(), self.elements(), "one width per element");
        self.widths = Some(widths.iter().map(|w| w.max(1.0)).collect());
    }

    /// Load one day's water-quality file (such as `temperature`, per element),
    /// to show it.
    ///
    /// # Errors
    /// If the file is not a quality file for this reach.
    pub fn load_quality(&mut self, bytes: &[u8]) -> Result<(), EngineError> {
        self.quality
            .load_day(bytes, &[])
            .map_err(EngineError::Format)?;
        if self.quality.elements != self.elements() {
            return Err(EngineError::WrongReach(format!(
                "quality file has {} elements, expected {}",
                self.quality.elements,
                self.elements()
            )));
        }
        Ok(())
    }

    /// A water-quality field's value now where each flow particle is, in the
    /// order of `particle_snapshot`; `NaN` where there is none.
    #[must_use]
    pub fn particle_values(&self, name: &str) -> Vec<f32> {
        let per = self.quality.at(self.time, name);
        self.particles
            .iter()
            .map(|p| {
                per.as_ref()
                    .and_then(|v| v.get(self.element(p.x)))
                    .map_or(f32::NAN, |&x| {
                        #[allow(clippy::cast_possible_truncation)]
                        let y = x as f32;
                        y
                    })
            })
            .collect()
    }

    #[must_use]
    pub fn length(&self) -> f64 {
        self.ends.last().copied().unwrap_or(0.0)
    }

    #[must_use]
    pub fn time(&self) -> f64 {
        self.time
    }

    #[must_use]
    pub fn elements(&self) -> usize {
        self.starts.len()
    }

    /// Load one day's field file (bytes as published).
    ///
    /// # Errors
    /// If the bytes are not a field file for this reach, or lack velocity, volume or dispersion.
    pub fn load_day(&mut self, bytes: &[u8]) -> Result<(), EngineError> {
        let f = FieldFile::read_from(&mut &bytes[..]).map_err(EngineError::Format)?;
        if f.dims != 1 || f.elements as usize != self.elements() {
            return Err(EngineError::WrongReach(format!(
                "{} elements in {}D, expected {} in 1D",
                f.elements,
                f.dims,
                self.elements()
            )));
        }
        let take = |name: &'static str| {
            f.field(name)
                .map(|x| x.values.clone())
                .ok_or(EngineError::MissingField(name))
        };
        let day = Day {
            t0: f.t0 as f64,
            step: f64::from(f.step_s),
            steps: f.steps as usize,
            velocity: take("velocity")?,
            volume: take("volume")?,
            dispersion: take("dispersion")?,
            lateral: f.field("lateral_mixing").map(|x| x.values.clone()),
        };
        let at = self.days.partition_point(|d| d.t0 < day.t0);
        self.days.insert(at, day);
        Ok(())
    }

    /// Element containing chainage x (clamped to the reach).
    fn element(&self, x: f64) -> usize {
        self.starts
            .partition_point(|&s| s <= x)
            .saturating_sub(1)
            .min(self.elements() - 1)
    }

    /// (day, step index, fraction to the next sample) for time t, held at the ends.
    /// After a day's last step, the next sample is the next day's first step.
    fn locate(&self, t: f64) -> Option<(usize, usize, f64)> {
        let d = self.days.partition_point(|d| d.t0 <= t).saturating_sub(1);
        let day = self.days.get(d)?;
        let last = (day.steps - 1) as f64;
        let x = ((t - day.t0) / day.step).max(0.0);
        if x < last {
            let i = x.floor() as usize;
            return Some((d, i, x - i as f64));
        }
        let last_t = day.t0 + last * day.step;
        let f = match self.days.get(d + 1) {
            Some(next) if next.t0 > last_t => ((t - last_t) / (next.t0 - last_t)).clamp(0.0, 1.0),
            _ => 0.0,
        };
        Some((d, day.steps - 1, f))
    }

    /// A field value for element e at time t, linear in time (across midnight too).
    fn value(&self, pick: fn(&Day) -> &[f32], e: usize, t: f64) -> f64 {
        let Some((d, i, f)) = self.locate(t) else {
            return 0.0;
        };
        let day = &self.days[d];
        let n = self.elements();
        let a = f64::from(pick(day)[i * n + e]);
        let b = if i + 1 < day.steps {
            f64::from(pick(day)[(i + 1) * n + e])
        } else if let Some(next) = self.days.get(d + 1) {
            f64::from(pick(next)[e])
        } else {
            a
        };
        a + f * (b - a)
    }

    #[must_use]
    pub fn velocity(&self, x: f64, t: f64) -> f64 {
        self.value(|d| &d.velocity, self.element(x), t)
    }

    #[must_use]
    pub fn dispersion(&self, x: f64, t: f64) -> f64 {
        self.value(|d| &d.dispersion, self.element(x), t)
    }

    /// The lateral mixing ε at x and t, m²/s, where every day holds it.
    fn lateral_mixing(&self, x: f64, t: f64) -> Option<f64> {
        if self.days.is_empty() || self.days.iter().any(|d| d.lateral.is_none()) {
            return None;
        }
        Some(self.value(|d| d.lateral.as_deref().unwrap_or(&[]), self.element(x), t))
    }

    /// Add a release. Returns its index.
    pub fn add_release(&mut self, r: Release) -> usize {
        self.releases.push((r, 0));
        self.releases.len() - 1
    }

    /// Spread `n` flow particles evenly along the reach, at random positions.
    pub fn seed_flow_particles(&mut self, n: usize) {
        let len = self.length();
        self.particles = (0..n)
            .map(|_| FlowParticle {
                x: self.rng.uniform() * len,
                lateral: (self.rng.uniform() * 2.0 - 1.0) as f32,
            })
            .collect();
    }

    fn emit(&mut self, t: f64) {
        for idx in 0..self.releases.len() {
            let (r, emitted) = self.releases[idx];
            let due = if t < r.start {
                0
            } else if r.duration <= 0.0 {
                r.parcels
            } else {
                (f64::from(r.parcels) * ((t - r.start) / r.duration).min(1.0)).floor() as u32
            };
            let each = r.mass / f64::from(r.parcels.max(1));
            for _ in emitted..due {
                let lat = self.rng.uniform() * 2.0 - 1.0;
                self.parcels.push(Parcel {
                    x: r.x,
                    lat,
                    mass: each,
                    release: idx,
                    in_water: true,
                });
                self.balance.released += each;
                self.balance.in_water += each;
            }
            self.releases[idx].1 = due.max(emitted);
        }
    }

    /// Advance to time `t`, moving parcels and flow particles through the field.
    pub fn step_to(&mut self, t: f64) {
        let len = self.length();
        while self.time < t {
            let dt = (t - self.time).min(MAX_DT);
            let now = self.time;
            self.emit(now);
            for i in 0..self.parcels.len() {
                let p = self.parcels[i];
                if !p.in_water {
                    continue;
                }
                let u = self.velocity(p.x, now);
                let mut lat = p.lat;
                let mut x = match (self.lateral_mixing(p.x, now), &self.widths) {
                    (Some(eps), Some(widths)) => {
                        // Downstream at the speed across the channel where it is,
                        // then across by the lateral mixing, reflected at the banks.
                        let w = widths[self.element(p.x)];
                        let c = dispersion::shear_profile_c();
                        let x = p.x + u * (1.0 + c * (1.0 / 3.0 - lat * lat)) * dt;
                        lat += 2.0 / w * (2.0 * eps.max(0.0) * dt).sqrt() * self.rng.normal();
                        while !(-1.0..=1.0).contains(&lat) {
                            lat = if lat > 1.0 { 2.0 - lat } else { -2.0 - lat };
                        }
                        x
                    }
                    _ => parcel::step_along_channel(
                        p.x,
                        u,
                        self.dispersion(p.x, now),
                        dt,
                        &mut self.rng,
                    ),
                };
                if x < 0.0 {
                    x = -x; // the top of the reach reflects
                }
                let half_life = self.releases[p.release].0.half_life;
                let kept = p.mass * decay::remaining_fraction(dt, half_life);
                self.balance.decayed += p.mass - kept;
                self.balance.in_water -= p.mass - kept;
                let q = &mut self.parcels[i];
                q.mass = kept;
                q.x = x;
                q.lat = lat;
                if x >= len {
                    q.in_water = false;
                    self.balance.in_water -= kept;
                    self.balance.past_end += kept;
                    // When it reached the end: the share of the step's move needed to get there.
                    let frac = if x > p.x {
                        ((len - p.x) / (x - p.x)).clamp(0.0, 1.0)
                    } else {
                        1.0
                    };
                    self.outflow.push((now + dt * frac, kept, half_life));
                }
            }
            for i in 0..self.particles.len() {
                let fp = self.particles[i];
                let mut x = fp.x + self.velocity(fp.x, now) * dt;
                if x >= len {
                    x -= len; // re-enter at the top, so density stays steady
                } else if x < 0.0 {
                    x += len;
                }
                self.particles[i].x = x;
            }
            self.time += dt;
        }
        self.emit(self.time);
    }

    /// Move flow particles over the field frozen at the current time, for "Now"
    /// mode (specification section 3.4): the motion is shown, time stands still.
    pub fn drift_frozen(&mut self, dt: f64) {
        let (len, now) = (self.length(), self.time);
        for i in 0..self.particles.len() {
            let fp = self.particles[i];
            let x = (fp.x + self.velocity(fp.x, now) * dt).rem_euclid(len);
            self.particles[i].x = x;
        }
    }

    #[must_use]
    pub fn mass_balance(&self) -> MassBalance {
        self.balance
    }

    /// Parcels that passed the end of the reach since the last call: (time,
    /// mass, half-life), in time order, for the next reach downstream.
    pub fn take_outflow(&mut self) -> Vec<(f64, f64, Option<f64>)> {
        let mut out = std::mem::take(&mut self.outflow);
        out.sort_by(|a, b| a.0.total_cmp(&b.0));
        out
    }

    /// Concentration in each element now, kg/m³: parcel mass over water volume.
    #[must_use]
    pub fn concentration(&self) -> Vec<f64> {
        let mut mass = vec![0.0; self.elements()];
        for p in self.parcels.iter().filter(|p| p.in_water) {
            mass[self.element(p.x)] += p.mass;
        }
        mass.iter()
            .enumerate()
            .map(|(e, m)| {
                let v = self.value(|d| &d.volume, e, self.time);
                if v > 0.0 {
                    m / v
                } else {
                    0.0
                }
            })
            .collect()
    }

    /// Parcels in the water: chainage, m, and the concentration where each is, kg/m³.
    #[must_use]
    pub fn parcel_snapshot(&self) -> (Vec<f64>, Vec<f64>) {
        let c = self.concentration();
        self.parcels
            .iter()
            .filter(|p| p.in_water)
            .map(|p| (p.x, c[self.element(p.x)]))
            .unzip()
    }

    /// Flow particles: chainage, m; lateral position, -1..1; speed, m/s.
    #[must_use]
    pub fn particle_snapshot(&self) -> (Vec<f64>, Vec<f32>, Vec<f64>) {
        let now = self.time;
        let mut xs = Vec::with_capacity(self.particles.len());
        let mut lat = Vec::with_capacity(self.particles.len());
        let mut speed = Vec::with_capacity(self.particles.len());
        for p in &self.particles {
            xs.push(p.x);
            lat.push(p.lateral);
            speed.push(self.velocity(p.x, now));
        }
        (xs, lat, speed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use fdmr_format::{Encoding, Field};

    const STEP: u32 = 900;

    /// A uniform reach: `n` elements of 1 km, velocity u, dispersion k, area 100 m².
    fn uniform_day(n: u32, t0: i64, u: f32, k: f32) -> Vec<u8> {
        let steps = 96;
        let len = (n * steps) as usize;
        let f = |name: &str, unit: &str, v: f32| Field {
            name: name.into(),
            unit: unit.into(),
            encoding: Encoding::F32,
            values: vec![v; len],
        };
        let file = FieldFile {
            reach: "river".into(),
            dims: 1,
            elements: n,
            layers: 1,
            steps,
            t0,
            step_s: STEP,
            fields: vec![
                f("velocity", "m/s", u),
                f("volume", "m3", 100_000.0),
                f("dispersion", "m2/s", k),
            ],
        };
        let mut b = Vec::new();
        file.write_to(&mut b).unwrap();
        b
    }

    fn engine(n: u32, u: f32, k: f32, days: i64) -> Engine {
        let els: Vec<(f64, f64)> = (0..n)
            .map(|i| (f64::from(i) * 1000.0, f64::from(i + 1) * 1000.0))
            .collect();
        let mut e = Engine::new(&els, 0.0, 7);
        for d in 0..days {
            e.load_day(&uniform_day(n, d * 86_400, u, k)).unwrap();
        }
        e
    }

    /// A uniform reach as `uniform_day`, with lateral mixing `eps` published too.
    fn shear_day(n: u32, t0: i64, u: f32, eps: f32) -> Vec<u8> {
        let mut file = FieldFile::read_from(&mut &uniform_day(n, t0, u, 1.0)[..]).unwrap();
        let len = file.fields[0].values.len();
        file.fields.push(Field {
            name: "lateral_mixing".into(),
            unit: "m2/s".into(),
            encoding: Encoding::F32,
            values: vec![eps; len],
        });
        let mut b = Vec::new();
        file.write_to(&mut b).unwrap();
        b
    }

    fn moments(xs: &[f64]) -> (f64, f64, f64) {
        #[allow(clippy::cast_precision_loss)]
        let n = xs.len() as f64;
        let mean = xs.iter().sum::<f64>() / n;
        let var = xs.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / n;
        let skew = xs.iter().map(|x| (x - mean).powi(3)).sum::<f64>() / n / var.powf(1.5);
        (mean, var, skew)
    }

    #[test]
    fn shear_and_lateral_mixing_spread_a_release_at_taylors_k() {
        // 50 m wide, U 0.5 m/s, ε 0.05 m²/s: K = c² U² W² / (472.5 ε) = 31.6 m²/s
        // once the cloud has mixed across (several W²/ε), and before that the
        // slow water near the banks leaves a tail behind (negative skew).
        let (n, u, w, eps) = (200u32, 0.5_f64, 50.0_f64, 0.05_f64);
        let els: Vec<(f64, f64)> = (0..n)
            .map(|i| (f64::from(i) * 1000.0, f64::from(i + 1) * 1000.0))
            .collect();
        let mut e = Engine::new(&els, 0.0, 13);
        #[allow(clippy::cast_possible_truncation)]
        for d in 0..3 {
            e.load_day(&shear_day(n, d * 86_400, u as f32, eps as f32))
                .unwrap();
        }
        e.set_widths(&vec![w; n as usize]);
        e.add_release(Release {
            x: 1_000.0,
            start: 0.0,
            duration: 0.0,
            mass: 1000.0,
            half_life: None,
            parcels: 20_000,
        });
        e.step_to(2_000.0);
        let (_, _, early_skew) = moments(&e.parcel_snapshot().0);
        assert!(early_skew < -0.3, "early skew {early_skew}");
        e.step_to(60_000.0);
        let (_, v1, _) = moments(&e.parcel_snapshot().0);
        e.step_to(180_000.0);
        let (mean, v2, late_skew) = moments(&e.parcel_snapshot().0);
        let k = (v2 - v1) / (2.0 * 120_000.0);
        let c = dispersion::shear_profile_c();
        let taylor = c * c * u * u * w * w / (472.5 * eps);
        assert!((k / taylor - 1.0).abs() < 0.1, "K {k} against {taylor}");
        assert!(
            late_skew.abs() < early_skew.abs() / 2.0,
            "late skew {late_skew}"
        );
        assert!(
            (mean - (1_000.0 + u * 180_000.0)).abs() < 300.0,
            "centre {mean}"
        );
    }

    #[test]
    fn plume_moves_at_u_and_spreads_as_2kt() {
        let mut e = engine(400, 0.5, 40.0, 2);
        e.add_release(Release {
            x: 10_000.0,
            start: 0.0,
            duration: 0.0,
            mass: 1000.0,
            half_life: None,
            parcels: 20_000,
        });
        let t = 36_000.0; // 10 h
        e.step_to(t);
        let (xs, _) = e.parcel_snapshot();
        let n = xs.len() as f64;
        let mean = xs.iter().sum::<f64>() / n;
        let var = xs.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / n;
        assert!((mean - (10_000.0 + 0.5 * t)).abs() < 150.0, "centre {mean}");
        assert!(
            (var - 2.0 * 40.0 * t).abs() / (2.0 * 40.0 * t) < 0.05,
            "variance {var}"
        );
    }

    #[test]
    fn mass_balances_with_decay_and_outflow() {
        let mut e = engine(20, 1.0, 20.0, 2);
        e.add_release(Release {
            x: 2_000.0,
            start: 0.0,
            duration: 3_600.0,
            mass: 50.0,
            half_life: Some(7_200.0),
            parcels: 2_000,
        });
        e.step_to(40_000.0); // long enough for much of it to leave the 20 km reach
        let b = e.mass_balance();
        assert!((b.released - 50.0).abs() < 1e-9);
        assert!((b.in_water + b.past_end + b.decayed - b.released).abs() < 1e-9);
        assert!(b.past_end > 0.0 && b.decayed > 0.0);
        // What passed the end is handed on whole, in time order.
        let out = e.take_outflow();
        assert!((out.iter().map(|o| o.1).sum::<f64>() - b.past_end).abs() < 1e-9);
        assert!(out.windows(2).all(|w| w[0].0 <= w[1].0));
        assert_eq!(e.take_outflow().len(), 0);
    }

    #[test]
    fn continuous_release_emits_over_its_duration() {
        let mut e = engine(50, 0.5, 10.0, 1);
        e.add_release(Release {
            x: 1_000.0,
            start: 3_600.0,
            duration: 7_200.0,
            mass: 10.0,
            half_life: None,
            parcels: 100,
        });
        e.step_to(3_000.0);
        assert_eq!(e.parcel_snapshot().0.len(), 0);
        e.step_to(3_600.0 + 3_600.0);
        assert_eq!(e.parcel_snapshot().0.len(), 50);
        e.step_to(20_000.0);
        assert!((e.mass_balance().released - 10.0).abs() < 1e-9);
    }

    #[test]
    fn concentration_is_mass_over_volume() {
        let mut e = engine(10, 0.0, 0.0, 1);
        e.add_release(Release {
            x: 4_500.0,
            start: 0.0,
            duration: 0.0,
            mass: 7.0,
            half_life: None,
            parcels: 10,
        });
        e.step_to(60.0);
        let c = e.concentration();
        assert!((c[4] - 7.0 / 100_000.0).abs() < 1e-12);
        assert!(c.iter().enumerate().all(|(i, &v)| i == 4 || v == 0.0));
    }

    #[test]
    fn flow_particles_keep_a_steady_count_and_move_downstream() {
        let mut e = engine(10, 0.5, 0.0, 1);
        e.seed_flow_particles(500);
        let (before, _, _) = e.particle_snapshot();
        e.step_to(600.0);
        let (after, _, speed) = e.particle_snapshot();
        assert_eq!(after.len(), 500);
        let moved = before
            .iter()
            .zip(&after)
            .filter(|(a, b)| (**b - **a - 300.0).abs() < 1e-6)
            .count();
        assert!(
            moved > 450,
            "most particles move 300 m downstream; some wrap to the top"
        );
        assert!(speed.iter().all(|&s| (s - 0.5).abs() < 1e-9));
    }

    #[test]
    fn now_mode_moves_particles_without_moving_time() {
        let mut e = engine(10, 0.5, 0.0, 1);
        e.seed_flow_particles(10);
        e.step_to(1_000.0);
        e.drift_frozen(120.0);
        assert!((e.time() - 1_000.0).abs() < f64::EPSILON);
    }

    #[test]
    fn velocity_is_linear_in_time_across_midnight() {
        let els: Vec<(f64, f64)> = (0..5)
            .map(|i| (f64::from(i) * 1000.0, f64::from(i + 1) * 1000.0))
            .collect();
        let mut e = Engine::new(&els, 0.0, 1);
        e.load_day(&uniform_day(5, 0, 1.0, 0.0)).unwrap();
        e.load_day(&uniform_day(5, 86_400, 2.0, 0.0)).unwrap();
        let last_step = 95.0 * f64::from(STEP);
        let mid = f64::midpoint(last_step, 86_400.0);
        assert!((e.velocity(100.0, mid) - 1.5).abs() < 1e-9);
    }

    #[test]
    fn wrong_reach_is_refused() {
        let mut e = engine(10, 0.5, 0.0, 0);
        assert!(matches!(
            e.load_day(&uniform_day(11, 0, 0.5, 0.0)),
            Err(EngineError::WrongReach(_))
        ));
    }
}
