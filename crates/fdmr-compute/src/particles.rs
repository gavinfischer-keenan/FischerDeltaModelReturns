//! What the network and mesh tracers' flow particles share (#83): each
//! particle's place, its age and its life; seeding them part way through
//! their lives; one arriving from another reach taking the place of the one
//! furthest through its life; and the tidally filtered fields the particles
//! move on in net-flow mode. A place is the tracer's own (a channel and a
//! distance along it, or a triangle and a point in it).

use fdmr_math::rng::Rng;

use crate::{oldest, particle_life, MAX_DT};

/// Flow particles: where each is, how long it has lived, and how long it lives.
#[derive(Debug, Clone)]
pub(crate) struct FlowParticles<P> {
    pub places: Vec<P>,
    pub ages: Vec<f64>,
    pub lives: Vec<f64>,
}

impl<P> Default for FlowParticles<P> {
    fn default() -> Self {
        Self {
            places: Vec::new(),
            ages: Vec::new(),
            lives: Vec::new(),
        }
    }
}

impl<P: Copy> FlowParticles<P> {
    pub fn len(&self) -> usize {
        self.places.len()
    }

    /// Start afresh at `places`, each part way through its life so that they
    /// do not all start again at once: every life drawn, then every age.
    pub fn seed(&mut self, places: Vec<P>, rng: &mut Rng) {
        let n = places.len();
        self.places = places;
        self.lives = (0..n).map(|_| particle_life(rng)).collect();
        self.ages = self.lives.iter().map(|l| l * rng.uniform()).collect();
    }

    /// Particle `i` starts again at `place`, with a new life.
    pub fn renew(&mut self, i: usize, place: P, rng: &mut Rng) {
        self.places[i] = place;
        self.ages[i] = 0.0;
        self.lives[i] = particle_life(rng);
    }

    /// Particle `i` a further `dt` s on; whether it has outlived its life.
    pub fn age(&mut self, i: usize, dt: f64) -> bool {
        self.ages[i] += dt;
        self.ages[i] > self.lives[i]
    }

    /// One arriving from another reach takes the place of the particle
    /// furthest through its life, so the number stays the same: its place
    /// from `place` (drawn only once the particle to give up is known).
    /// False when there are none.
    pub fn take_in(&mut self, rng: &mut Rng, place: impl FnOnce(&mut Rng) -> P) -> bool {
        let Some(i) = oldest(&self.ages, &self.lives) else {
            return false;
        };
        let p = place(rng);
        self.renew(i, p, rng);
        true
    }
}

/// The fields the flow particles move on in net-flow mode: the tidally
/// filtered copy, built on first use, dropped when more days come in.
#[derive(Debug)]
pub(crate) struct NetFlow<T> {
    on: bool,
    filtered: Option<T>,
}

impl<T> Default for NetFlow<T> {
    fn default() -> Self {
        Self {
            on: false,
            filtered: None,
        }
    }
}

impl<T> NetFlow<T> {
    /// Net flow on (`true`) or off; the filtered copy built by `build` if it is wanted and not yet there.
    pub fn set(&mut self, on: bool, build: impl FnOnce() -> T) {
        self.on = on;
        if on && self.filtered.is_none() {
            self.filtered = Some(build());
        }
    }

    /// The filtered fields, when net flow is on.
    pub fn active(&self) -> Option<&T> {
        if self.on {
            self.filtered.as_ref()
        } else {
            None
        }
    }

    /// Whether net flow is on.
    pub fn is_on(&self) -> bool {
        self.on
    }

    /// Drop the filtered copy (more days have come in); rebuilt when next wanted.
    pub fn invalidate(&mut self) {
        self.filtered = None;
    }
}

/// `dt` s in steps of at most `MAX_DT`, for moving the particles with time
/// standing still ("Now" mode).
pub(crate) fn in_steps(dt: f64, mut step: impl FnMut(f64)) {
    let mut left = dt;
    while left > 0.0 {
        let s = left.min(MAX_DT);
        step(s);
        left -= s;
    }
}
