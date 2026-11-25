//! The compute engine for the browser (specification section 4.2).
//!
//! Thin wrappers over `fdmr-compute`, exported to JavaScript. It runs inside a
//! web worker, the compute engine's background thread, never on the UI thread.
//! Results come back as typed arrays of numbers: chainage, concentration,
//! speed. Turning chainage into a place on the map is the UI's job.

use fdmr_compute::{Engine as Core, Release};
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub struct Engine {
    core: Core,
}

#[wasm_bindgen]
impl Engine {
    /// A 1D reach from element start and end chainages, m, upstream to downstream.
    #[wasm_bindgen(constructor)]
    #[must_use]
    pub fn new(starts: &[f64], ends: &[f64], start_time: f64, seed: u32) -> Self {
        let elements: Vec<(f64, f64)> = starts.iter().copied().zip(ends.iter().copied()).collect();
        Self {
            core: Core::new(&elements, start_time, u64::from(seed)),
        }
    }

    /// Load one day's field file, as fetched.
    ///
    /// # Errors
    /// If the bytes are not a field file for this reach.
    pub fn load_day(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .load_day(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    pub fn seed_flow_particles(&mut self, n: usize) {
        self.core.seed_flow_particles(n);
    }

    /// Add a release. `half_life` in s, or `NaN` for none. Returns its index.
    pub fn add_release(
        &mut self,
        x: f64,
        start: f64,
        duration: f64,
        mass: f64,
        half_life: f64,
        parcels: u32,
    ) -> usize {
        self.core.add_release(Release {
            x,
            start,
            duration,
            mass,
            half_life: if half_life.is_finite() && half_life > 0.0 {
                Some(half_life)
            } else {
                None
            },
            parcels,
        })
    }

    pub fn step_to(&mut self, t: f64) {
        self.core.step_to(t);
    }

    pub fn drift_frozen(&mut self, dt: f64) {
        self.core.drift_frozen(dt);
    }

    #[must_use]
    pub fn time(&self) -> f64 {
        self.core.time()
    }

    #[must_use]
    pub fn length(&self) -> f64 {
        self.core.length()
    }

    /// Flow particles' chainage, m.
    #[must_use]
    pub fn particle_x(&self) -> Vec<f64> {
        self.core.particle_snapshot().0
    }

    /// Flow particles' lateral position, -1..1.
    #[must_use]
    pub fn particle_lateral(&self) -> Vec<f32> {
        self.core.particle_snapshot().1
    }

    /// Flow particles' speed, m/s.
    #[must_use]
    pub fn particle_speed(&self) -> Vec<f64> {
        self.core.particle_snapshot().2
    }

    /// Parcels in the water: chainage, m.
    #[must_use]
    pub fn parcel_x(&self) -> Vec<f64> {
        self.core.parcel_snapshot().0
    }

    /// Parcels in the water: concentration where each is, kg/m³.
    #[must_use]
    pub fn parcel_concentration(&self) -> Vec<f64> {
        self.core.parcel_snapshot().1
    }

    /// Concentration in each element, kg/m³.
    #[must_use]
    pub fn concentration(&self) -> Vec<f64> {
        self.core.concentration()
    }

    /// [released, in water, past the end, decayed], kg.
    #[must_use]
    pub fn mass_balance(&self) -> Vec<f64> {
        let b = self.core.mass_balance();
        vec![b.released, b.in_water, b.past_end, b.decayed]
    }
}
