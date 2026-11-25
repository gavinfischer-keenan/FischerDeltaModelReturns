//! The compute engine for the browser (specification section 4.2).
//!
//! Thin wrappers over `fdmr-compute`, exported to JavaScript. It runs inside a
//! web worker, the compute engine's background thread, never on the UI thread.
//! Results come back as typed arrays of numbers: chainage, concentration,
//! speed. Turning chainage into a place on the map is the UI's job.

use fdmr_compute::mesh::{MeshFate, MeshRelease, MeshTopology, MeshTracer};
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

    /// Load one day's water-quality file, to show its fields.
    ///
    /// # Errors
    /// If it does not match the network.
    pub fn load_quality(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .load_quality(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// A field's value where each flow particle is (`NaN` where there is none).
    #[must_use]
    pub fn particle_values(&self, name: &str) -> Vec<f32> {
        self.core.particle_values(name)
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

    /// Channels another reach tracks (by index): parcels about to enter one are handed on.
    pub fn set_handoff(&mut self, channels: &[u32]) {
        let c: Vec<usize> = channels.iter().map(|&i| i as usize).collect();
        self.core.set_handoff(&c);
    }

    /// Take in a parcel handed back from another reach at junction `node`, now.
    /// `half_life` in s, or `NaN` for none.
    pub fn take_parcel(&mut self, node: u32, mass: f64, half_life: f64) {
        self.core.take_parcel(
            node as usize,
            mass,
            if half_life.is_finite() && half_life > 0.0 {
                Some(half_life)
            } else {
                None
            },
        );
    }

    /// Parcels handed on since the last call: flat (junction, time, mass,
    /// half-life or `NaN`) for each.
    pub fn take_handed(&mut self) -> Vec<f64> {
        self.core
            .take_handed()
            .into_iter()
            .flat_map(|h| {
                #[allow(clippy::cast_precision_loss)]
                let node = h.node as f64;
                [node, h.t, h.mass, h.half_life.unwrap_or(f64::NAN)]
            })
            .collect()
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

    /// [released, in water, exported, diverted, out to sea, broken down, handed on], kg.
    #[must_use]
    pub fn mass_balance(&self) -> Vec<f64> {
        let b = self.core.balance();
        vec![
            b.released,
            b.in_water,
            b.exported,
            b.diverted,
            b.to_sea,
            b.decayed,
            b.handed_on,
        ]
    }

    /// Each channel's mean velocity now, m/s, positive downstream.
    #[must_use]
    pub fn velocities(&self) -> Vec<f32> {
        self.core.velocities()
    }
}

/// A 2D mesh reach (such as a bay), tracked by `fdmr_compute::mesh`.
///
/// Positions come back as a triangle index and two barycentric weights, for
/// its second and third nodes, for the UI to place on the map.
#[wasm_bindgen]
pub struct MeshEngine {
    core: MeshTracer,
}

#[wasm_bindgen]
impl MeshEngine {
    /// The mesh: node coordinates (m, planar) and still-water depths (m),
    /// counter-clockwise triangles as node triples (flat), open-sea edges and
    /// handoff edges as node pairs (flat), each handoff edge's id, and the
    /// horizontal diffusivity, m²/s.
    #[wasm_bindgen(constructor)]
    #[must_use]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        x: &[f64],
        y: &[f64],
        depth: &[f64],
        triangles: &[u32],
        open: &[u32],
        handoff: &[u32],
        handoff_id: &[u32],
        start_time: f64,
        seed: u32,
        diffusivity: f64,
    ) -> Self {
        let topo = MeshTopology {
            x: x.to_vec(),
            y: y.to_vec(),
            depth: depth.to_vec(),
            tri: triangles
                .as_chunks::<3>()
                .0
                .iter()
                .map(|t| t.map(|n| n as usize))
                .collect(),
            open: open
                .as_chunks::<2>()
                .0
                .iter()
                .map(|e| (e[0] as usize, e[1] as usize))
                .collect(),
            handoff: handoff
                .as_chunks::<2>()
                .0
                .iter()
                .zip(handoff_id)
                .map(|(e, &id)| (e[0] as usize, e[1] as usize, id))
                .collect(),
        };
        Self {
            core: MeshTracer::new(topo, start_time, u64::from(seed), diffusivity),
        }
    }

    /// Load one day's file.
    ///
    /// # Errors
    /// If it does not match the mesh.
    pub fn load_day(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .load_day(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// Release at a point given as a triangle and two barycentric weights.
    /// `half_life` in s, or `NaN` for none. False if the point is off the mesh.
    #[allow(clippy::too_many_arguments)]
    pub fn add_release(
        &mut self,
        triangle: u32,
        w1: f64,
        w2: f64,
        start: f64,
        duration: f64,
        mass: f64,
        half_life: f64,
        parcels: u32,
    ) -> bool {
        let (x, y) = self.core.point(triangle as usize, w1, w2);
        self.core.add_release(MeshRelease {
            x,
            y,
            start,
            duration,
            mass,
            half_life: if half_life.is_finite() && half_life > 0.0 {
                Some(half_life)
            } else {
                None
            },
            parcels: parcels as usize,
        })
    }

    /// Take in a parcel handed over from another reach at (x, y), m, released at
    /// `born` (s). `half_life` in s, or `NaN` for none. False if (x, y) is off the mesh.
    pub fn take_parcel(&mut self, x: f64, y: f64, mass: f64, born: f64, half_life: f64) -> bool {
        self.core.take_parcel(
            x,
            y,
            mass,
            born,
            if half_life.is_finite() && half_life > 0.0 {
                Some(half_life)
            } else {
                None
            },
        )
    }

    /// Parcels that crossed a handoff edge since the last call: flat
    /// (handoff id, x, y, time, mass, half-life or `NaN`) for each.
    pub fn take_handed(&mut self) -> Vec<f64> {
        self.core
            .take_handed()
            .into_iter()
            .flat_map(|(fate, mass, half_life)| match fate {
                MeshFate::Handoff { id, x, y, t } => {
                    vec![f64::from(id), x, y, t, mass, half_life.unwrap_or(f64::NAN)]
                }
                MeshFate::ToSea => Vec::new(),
            })
            .collect()
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
    pub fn particle_triangle(&self) -> Vec<u32> {
        self.core.particles().0
    }

    /// Two weights per particle (flat), as for positions above.
    #[must_use]
    pub fn particle_weights(&self) -> Vec<f32> {
        self.core.particles().1
    }

    #[must_use]
    pub fn particle_speed(&self) -> Vec<f32> {
        self.core.particle_speeds()
    }

    /// Load one day's water-quality file, to show its fields.
    ///
    /// # Errors
    /// If it does not match the mesh.
    pub fn load_quality(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .load_quality(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// A field's value where each flow particle is (`NaN` where there is none).
    #[must_use]
    pub fn particle_values(&self, name: &str) -> Vec<f32> {
        self.core.particle_values(name)
    }

    #[must_use]
    pub fn parcel_triangle(&self) -> Vec<u32> {
        self.core.parcels().0
    }

    #[must_use]
    pub fn parcel_weights(&self) -> Vec<f32> {
        self.core.parcels().1
    }

    /// Depth-averaged concentration where each parcel is, kg/m³.
    #[must_use]
    pub fn parcel_concentration(&self) -> Vec<f64> {
        self.core.parcel_concentration()
    }

    /// [released, in water, out to sea, handed on, broken down], kg.
    #[must_use]
    pub fn mass_balance(&self) -> Vec<f64> {
        let b = self.core.balance();
        vec![b.released, b.in_water, b.to_sea, b.handed_on, b.decayed]
    }
}
