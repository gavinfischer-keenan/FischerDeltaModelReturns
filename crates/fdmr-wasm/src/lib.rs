//! The compute engine for the browser (specification section 4.2).
//!
//! Thin wrappers over `fdmr-compute`, exported to JavaScript. It runs inside a
//! web worker, the compute engine's background thread, never on the UI thread.
//! Results come back as typed arrays of numbers: chainage, concentration,
//! speed. Turning chainage into a place on the map is the UI's job.

use fdmr_compute::network::{NetRelease, NetworkTracer, Place, Topology};
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

    /// Parcels that passed the end since the last call, for the next reach:
    /// flat triples of (time, mass, half-life or `NaN`).
    pub fn take_outflow(&mut self) -> Vec<f64> {
        self.core
            .take_outflow()
            .into_iter()
            .flat_map(|(t, m, h)| [t, m, h.unwrap_or(f64::NAN)])
            .collect()
    }

    /// [released, in water, past the end, decayed], kg.
    #[must_use]
    pub fn mass_balance(&self) -> Vec<f64> {
        let b = self.core.mass_balance();
        vec![b.released, b.in_water, b.past_end, b.decayed]
    }
}

/// Parcels and flow particles moving through a channel network (a network reach).
///
/// Junctions and pools are indices from 0. Positions come back as an element
/// index and a fraction along it: channels first, then pools (index
/// `channels + pool`, fraction 0.5), for the UI to place on the map.
#[wasm_bindgen]
pub struct NetworkEngine {
    core: NetworkTracer,
}

#[wasm_bindgen]
impl NetworkEngine {
    /// The network: each channel's upstream and downstream junction and length
    /// (m), the junction count, the sea junctions, the pool count, and each
    /// link's pool and junction. `dispersion` in m²/s.
    #[wasm_bindgen(constructor)]
    #[must_use]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        up: &[u32],
        down: &[u32],
        length: &[f64],
        nodes: u32,
        sea: &[u32],
        pools: u32,
        link_pool: &[u32],
        link_node: &[u32],
        start_time: f64,
        seed: u32,
        dispersion: f64,
    ) -> Self {
        let ix = |v: &[u32]| v.iter().map(|&i| i as usize).collect::<Vec<_>>();
        let topo = Topology {
            up: ix(up),
            down: ix(down),
            length: length.to_vec(),
            nodes: nodes as usize,
            sea: ix(sea),
            pools: pools as usize,
            link_pool: ix(link_pool),
            link_node: ix(link_node),
        };
        Self {
            core: NetworkTracer::new(topo, start_time, u64::from(seed), dispersion),
        }
    }

    /// Load one day's channel file.
    ///
    /// # Errors
    /// If it does not match the network.
    pub fn load_channels(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .load_channels(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// Load one day's junction and pool file.
    ///
    /// # Errors
    /// If it does not match the network.
    pub fn load_nodes(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .load_nodes(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// Load one day's link file.
    ///
    /// # Errors
    /// If it does not match the network.
    pub fn load_links(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .load_links(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// Release into channel `channel` at `fraction` along it. `half_life` in s, or `NaN` for none.
    #[allow(clippy::too_many_arguments)]
    pub fn add_release(
        &mut self,
        channel: u32,
        fraction: f64,
        length: f64,
        start: f64,
        duration: f64,
        mass: f64,
        half_life: f64,
        parcels: u32,
    ) {
        self.core.add_release(NetRelease {
            at: Place::Channel(channel as usize, fraction.clamp(0.0, 1.0) * length),
            start,
            duration,
            mass,
            half_life: if half_life.is_finite() && half_life > 0.0 {
                Some(half_life)
            } else {
                None
            },
            parcels: parcels as usize,
        });
    }

    pub fn clear_releases(&mut self) {
        self.core.clear_releases();
    }

    pub fn seed_flow_particles(&mut self, n: usize) {
        self.core.seed_flow_particles(n);
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
    pub fn particle_element(&self) -> Vec<u32> {
        self.core.particles().0
    }

    #[must_use]
    pub fn particle_fraction(&self) -> Vec<f32> {
        self.core.particles().1
    }

    #[must_use]
    pub fn parcel_element(&self) -> Vec<u32> {
        self.core.parcels().0
    }

    #[must_use]
    pub fn parcel_fraction(&self) -> Vec<f32> {
        self.core.parcels().1
    }

    /// Concentration where each parcel is, kg/m³, in the order of the parcel positions.
    #[must_use]
    pub fn parcel_concentration(&self) -> Vec<f64> {
        self.core.parcel_concentration()
    }

    /// [released, in water, exported, diverted, out to sea, broken down], kg.
    #[must_use]
    pub fn mass_balance(&self) -> Vec<f64> {
        let b = self.core.balance();
        vec![
            b.released, b.in_water, b.exported, b.diverted, b.to_sea, b.decayed,
        ]
    }

    /// Each channel's mean velocity now, m/s, positive downstream.
    #[must_use]
    pub fn velocities(&self) -> Vec<f32> {
        self.core.velocities()
    }
}
