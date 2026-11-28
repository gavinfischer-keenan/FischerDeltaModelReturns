//! The compute engine for the browser (specification section 4.2).
//!
//! Thin wrappers over `fdmr-compute`, exported to JavaScript. It runs inside a
//! web worker, the compute engine's background thread, never on the UI thread.
//! Results come back as typed arrays of numbers: chainage, concentration,
//! speed. Turning chainage into a place on the map is the UI's job.

use fdmr_compute::mesh::{MeshRelease, MeshTopology, MeshTracer};
use fdmr_compute::network::{NetRelease, NetworkTracer, Place, Topology};
use fdmr_compute::region::{Entry, Region as RegionCore, RiverEnd, Shared};
use fdmr_compute::{Engine as Core, Release};
use std::cell::RefCell;
use std::rc::Rc;
use wasm_bindgen::prelude::*;

fn shared<T>(v: T) -> Shared<T> {
    Rc::new(RefCell::new(v))
}

#[wasm_bindgen]
pub struct Engine {
    core: Shared<Core>,
}

#[wasm_bindgen]
impl Engine {
    /// A 1D reach from element start and end chainages, m, upstream to downstream.
    #[wasm_bindgen(constructor)]
    #[must_use]
    pub fn new(starts: &[f64], ends: &[f64], start_time: f64, seed: u32) -> Self {
        let elements: Vec<(f64, f64)> = starts.iter().copied().zip(ends.iter().copied()).collect();
        Self {
            core: shared(Core::new(&elements, start_time, u64::from(seed))),
        }
    }

    /// Each element's water-surface width, m (the geometry's `width_m`): with
    /// a published `lateral_mixing`, parcels spread by the cross-channel shear.
    ///
    /// # Errors
    /// If there is not one width per element.
    pub fn set_widths(&mut self, widths: &[f64]) -> Result<(), JsError> {
        if widths.len() != self.core.borrow_mut().elements() {
            return Err(JsError::new("one width per element"));
        }
        self.core.borrow_mut().set_widths(widths);
        Ok(())
    }

    /// Load one day's field file, as fetched.
    ///
    /// # Errors
    /// If the bytes are not a field file for this reach.
    pub fn load_day(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .borrow_mut()
            .load_day(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    pub fn seed_flow_particles(&mut self, n: usize) {
        self.core.borrow_mut().seed_flow_particles(n);
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
        self.core.borrow_mut().add_release(Release {
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
        self.core.borrow_mut().step_to(t);
    }

    pub fn drift_frozen(&mut self, dt: f64) {
        self.core.borrow_mut().drift_frozen(dt);
    }

    #[must_use]
    pub fn time(&self) -> f64 {
        self.core.borrow().time()
    }

    #[must_use]
    pub fn length(&self) -> f64 {
        self.core.borrow().length()
    }

    /// Flow particles' chainage, m.
    #[must_use]
    pub fn particle_x(&self) -> Vec<f64> {
        self.core.borrow().particle_snapshot().0
    }

    /// Flow particles' lateral position, -1..1.
    #[must_use]
    pub fn particle_lateral(&self) -> Vec<f32> {
        self.core.borrow().particle_snapshot().1
    }

    /// Flow particles' speed, m/s.
    #[must_use]
    pub fn particle_speed(&self) -> Vec<f64> {
        self.core.borrow().particle_snapshot().2
    }

    /// Load one day's water-quality file (such as the river's temperature).
    ///
    /// # Errors
    /// If it does not match the reach.
    pub fn load_quality(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .borrow_mut()
            .load_quality(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// A field's value where each flow particle is (`NaN` where there is none).
    #[must_use]
    pub fn particle_values(&self, name: &str) -> Vec<f32> {
        self.core.borrow().particle_values(name)
    }

    /// Parcels in the water: chainage, m.
    #[must_use]
    pub fn parcel_x(&self) -> Vec<f64> {
        self.core.borrow().parcel_snapshot().0
    }

    /// Parcels in the water: concentration where each is, kg/m³.
    #[must_use]
    pub fn parcel_concentration(&self) -> Vec<f64> {
        self.core.borrow().parcel_snapshot().1
    }

    /// Concentration in each element, kg/m³.
    #[must_use]
    pub fn concentration(&self) -> Vec<f64> {
        self.core.borrow().concentration()
    }

    /// [released, in water, past the end, decayed], kg.
    #[must_use]
    pub fn mass_balance(&self) -> Vec<f64> {
        let b = self.core.borrow().mass_balance();
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
    core: Shared<NetworkTracer>,
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
            core: shared(NetworkTracer::new(
                topo,
                start_time,
                u64::from(seed),
                dispersion,
            )),
        }
    }

    /// Load one day's channel file.
    ///
    /// # Errors
    /// If it does not match the network.
    pub fn load_channels(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .borrow_mut()
            .load_channels(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// Load one day's junction and pool file.
    ///
    /// # Errors
    /// If it does not match the network.
    pub fn load_nodes(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .borrow_mut()
            .load_nodes(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// Load one day's water-quality file, to show its fields.
    ///
    /// # Errors
    /// If it does not match the network.
    pub fn load_quality(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .borrow_mut()
            .load_quality(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// A field's value where each flow particle is (`NaN` where there is none).
    #[must_use]
    pub fn particle_values(&self, name: &str) -> Vec<f32> {
        self.core.borrow().particle_values(name)
    }

    /// Load one day's link file.
    ///
    /// # Errors
    /// If it does not match the network.
    pub fn load_links(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .borrow_mut()
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
        self.core.borrow_mut().add_release(NetRelease {
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

    /// Release a pollutant that sticks to suspended sediment, by its
    /// partition coefficient `kd` (L/kg): the share bound where it is released
    /// settles and is scoured back up with the mud (fdmr-compute
    /// `add_bound_release`). Returns that share. Load the quality days first.
    #[allow(clippy::too_many_arguments)]
    pub fn add_bound_release(
        &mut self,
        channel: u32,
        fraction: f64,
        length: f64,
        start: f64,
        duration: f64,
        mass: f64,
        half_life: f64,
        parcels: u32,
        kd: f64,
    ) -> f64 {
        self.core.borrow_mut().add_bound_release(
            NetRelease {
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
            },
            kd,
        )
    }

    /// The sediment's settling velocity, m/s (the reach's `sediment` entry), for bound releases.
    pub fn set_settling(&mut self, ws: f64) {
        self.core.borrow_mut().set_settling(ws);
    }

    pub fn clear_releases(&mut self) {
        self.core.borrow_mut().clear_releases();
    }

    /// Channels another reach tracks (by index): parcels about to enter one are handed on.
    pub fn set_handoff(&mut self, channels: &[u32]) {
        let c: Vec<usize> = channels.iter().map(|&i| i as usize).collect();
        self.core.borrow_mut().set_handoff(&c);
    }

    /// Each channel's water-surface width, m: flow particles then start evenly
    /// over the water's surface rather than by volume, and settling divides
    /// by the water's depth over it.
    pub fn set_widths(&mut self, widths: &[f64]) {
        self.core.borrow_mut().set_widths(widths);
    }

    /// Where flow particles start, count every channel as at least `w` m
    /// wide (placement only, never the physics).
    pub fn set_min_seed_width(&mut self, w: f64) {
        self.core.borrow_mut().set_min_seed_width(w);
    }

    /// Each pool's water surface, m² (the geometry's pools' `area_m2`): flow
    /// particles then start over the pools too, by their share of the surface.
    pub fn set_pool_areas(&mut self, areas: &[f64]) {
        self.core.borrow_mut().set_pool_areas(areas);
    }

    /// Pool `q`'s mapped water as points (flat lon, lat pairs) and the
    /// horizontal mixing its particles drift at, m²/s: they wander over it,
    /// for drawing.
    pub fn set_pool_water(&mut self, q: u32, lonlat: &[f64], mixing: f64) {
        let pts: Vec<(f64, f64)> = lonlat
            .as_chunks::<2>()
            .0
            .iter()
            .map(|p| (p[0], p[1]))
            .collect();
        self.core
            .borrow_mut()
            .set_pool_water(q as usize, &pts, mixing);
    }

    /// Each flow particle's place on its pool's water, flat (lon, lat), `NaN`
    /// for one not in a pool with mapped water.
    #[must_use]
    pub fn particle_pool_positions(&self) -> Vec<f64> {
        self.core
            .borrow()
            .particle_pool_positions()
            .into_iter()
            .flat_map(|(a, b)| [a, b])
            .collect()
    }

    /// Where another reach takes over part way along channels: for each cut,
    /// its channel, its place along it (0 to 1 from the upstream end) and
    /// whether the other reach's part is downstream (1) or upstream (0).
    pub fn set_cuts(&mut self, channels: &[u32], at: &[f64], down: &[u8]) {
        let cuts: Vec<(usize, f64, bool)> = channels
            .iter()
            .zip(at)
            .zip(down)
            .map(|((&c, &a), &d)| (c as usize, a, d != 0))
            .collect();
        self.core.borrow_mut().set_cuts(&cuts);
    }

    /// The water in the channels now, m³.
    #[must_use]
    pub fn water_volume(&self) -> f64 {
        self.core.borrow().water_volume()
    }

    /// Exports that carry on into another reach: each a junction index, or a
    /// pool as the number of junctions plus its index. Their outlets are
    /// numbered in this order.
    pub fn set_outlets(&mut self, at: &[u32]) {
        let at: Vec<usize> = at.iter().map(|&i| i as usize).collect();
        self.core.borrow_mut().set_outlets(&at);
    }

    pub fn seed_flow_particles(&mut self, n: usize) {
        self.core.borrow_mut().seed_flow_particles(n);
    }

    pub fn step_to(&mut self, t: f64) {
        self.core.borrow_mut().step_to(t);
    }

    pub fn drift_frozen(&mut self, dt: f64) {
        self.core.borrow_mut().drift_frozen(dt);
    }

    /// Show the flow as net flow (tides filtered out) or moment by moment.
    /// Only the flow particles change; parcels keep the real flow.
    pub fn set_net_flow(&mut self, on: bool) {
        self.core.borrow_mut().set_net_flow(on);
    }

    #[must_use]
    pub fn time(&self) -> f64 {
        self.core.borrow().time()
    }

    #[must_use]
    pub fn particle_element(&self) -> Vec<u32> {
        self.core.borrow().particles().0
    }

    #[must_use]
    pub fn particle_fraction(&self) -> Vec<f32> {
        self.core.borrow().particles().1
    }

    #[must_use]
    pub fn parcel_element(&self) -> Vec<u32> {
        self.core.borrow().parcels().0
    }

    #[must_use]
    pub fn parcel_fraction(&self) -> Vec<f32> {
        self.core.borrow().parcels().1
    }

    /// Concentration where each parcel is, kg/m³, in the order of the parcel positions.
    #[must_use]
    pub fn parcel_concentration(&self) -> Vec<f64> {
        self.core.borrow().parcel_concentration()
    }

    /// [released, in water, exported, diverted, out to sea, broken down, handed on], kg.
    #[must_use]
    pub fn mass_balance(&self) -> Vec<f64> {
        let b = self.core.borrow().balance();
        vec![
            b.released,
            b.in_water,
            b.exported,
            b.diverted,
            b.to_sea,
            b.decayed,
            b.handed_on,
            b.on_bed,
        ]
    }

    /// Each channel's mean velocity now, m/s, positive downstream.
    #[must_use]
    pub fn velocities(&self) -> Vec<f32> {
        self.core.borrow().velocities()
    }
}

/// A 2D mesh reach (such as a bay), tracked by `fdmr_compute::mesh`.
///
/// Positions come back as a triangle index and two barycentric weights, for
/// its second and third nodes, for the UI to place on the map.
#[wasm_bindgen]
pub struct MeshEngine {
    core: Shared<MeshTracer>,
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
            core: shared(MeshTracer::new(
                topo,
                start_time,
                u64::from(seed),
                diffusivity,
            )),
        }
    }

    /// Load one day's file.
    ///
    /// # Errors
    /// If it does not match the mesh.
    pub fn load_day(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .borrow_mut()
            .load_day(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// The horizontal diffusivity at each node, m²/s (the reach's geometry
    /// `diffusivity_m2s`), in place of the one value given when made.
    ///
    /// # Errors
    /// If there is not one value per node.
    pub fn set_node_diffusivity(&mut self, k: &[f64]) -> Result<(), JsError> {
        if k.len() != self.core.borrow_mut().node_count() {
            return Err(JsError::new("one diffusivity per node"));
        }
        self.core.borrow_mut().set_node_diffusivity(k.to_vec());
        Ok(())
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
        let (x, y) = self.core.borrow_mut().point(triangle as usize, w1, w2);
        self.core.borrow_mut().add_release(MeshRelease {
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

    /// The water on the mesh now, m³.
    #[must_use]
    pub fn water_volume(&self) -> f64 {
        self.core.borrow().water_volume()
    }

    pub fn clear_releases(&mut self) {
        self.core.borrow_mut().clear_releases();
    }

    pub fn seed_flow_particles(&mut self, n: usize) {
        self.core.borrow_mut().seed_flow_particles(n);
    }

    pub fn step_to(&mut self, t: f64) {
        self.core.borrow_mut().step_to(t);
    }

    pub fn drift_frozen(&mut self, dt: f64) {
        self.core.borrow_mut().drift_frozen(dt);
    }

    /// Show the flow as net flow (tides filtered out) or moment by moment.
    /// Only the flow particles change; parcels keep the real flow.
    pub fn set_net_flow(&mut self, on: bool) {
        self.core.borrow_mut().set_net_flow(on);
    }

    #[must_use]
    pub fn time(&self) -> f64 {
        self.core.borrow().time()
    }

    #[must_use]
    pub fn particle_triangle(&self) -> Vec<u32> {
        self.core.borrow().particles().0
    }

    /// Two weights per particle (flat), as for positions above.
    #[must_use]
    pub fn particle_weights(&self) -> Vec<f32> {
        self.core.borrow().particles().1
    }

    #[must_use]
    pub fn particle_speed(&self) -> Vec<f32> {
        self.core.borrow().particle_speeds()
    }

    /// Load one day's water-quality file, to show its fields.
    ///
    /// # Errors
    /// If it does not match the mesh.
    pub fn load_quality(&mut self, bytes: &[u8]) -> Result<(), JsError> {
        self.core
            .borrow_mut()
            .load_quality(bytes)
            .map_err(|e| JsError::new(&e.to_string()))
    }

    /// A field's value where each flow particle is (`NaN` where there is none).
    #[must_use]
    pub fn particle_values(&self, name: &str) -> Vec<f32> {
        self.core.borrow().particle_values(name)
    }

    #[must_use]
    pub fn parcel_triangle(&self) -> Vec<u32> {
        self.core.borrow().parcels().0
    }

    #[must_use]
    pub fn parcel_weights(&self) -> Vec<f32> {
        self.core.borrow().parcels().1
    }

    /// Depth-averaged concentration where each parcel is, kg/m³.
    #[must_use]
    pub fn parcel_concentration(&self) -> Vec<f64> {
        self.core.borrow().parcel_concentration()
    }

    /// [released, in water, out to sea, handed on, broken down], kg.
    #[must_use]
    pub fn mass_balance(&self) -> Vec<f64> {
        let b = self.core.borrow().balance();
        vec![b.released, b.in_water, b.to_sea, b.handed_on, b.decayed]
    }
}

/// A region's reaches run together (`fdmr_compute::region`): the region steps
/// them, passes parcels and flow particles across every join, and keeps one
/// mass balance for them all. The joins are given as plain numbers: each reach
/// by the index its `add_` call returned.
#[wasm_bindgen]
pub struct Region {
    core: RegionCore,
}

#[wasm_bindgen]
impl Region {
    #[wasm_bindgen(constructor)]
    #[must_use]
    pub fn new() -> Self {
        Self {
            core: RegionCore::new(),
        }
    }

    /// Add a river reach, upstream ones first; returns its index.
    #[allow(clippy::cast_possible_truncation)]
    pub fn add_river(&mut self, e: &Engine) -> u32 {
        self.core.add_river(e.core.clone()) as u32
    }

    /// Add a network (a canal if `canal`); returns its index.
    #[allow(clippy::cast_possible_truncation)]
    pub fn add_network(&mut self, n: &NetworkEngine, canal: bool) -> u32 {
        self.core.add_network(n.core.clone(), canal) as u32
    }

    /// Add a mesh; returns its index.
    #[allow(clippy::cast_possible_truncation)]
    pub fn add_mesh(&mut self, m: &MeshEngine) -> u32 {
        self.core.add_mesh(m.core.clone()) as u32
    }

    /// What passes the end of river `from` enters river `to`, `at_m` m along it.
    pub fn river_to_river(&mut self, from: u32, to: u32, at_m: f64) {
        self.core.set_river_end(
            from as usize,
            RiverEnd::River {
                to: to as usize,
                at_m,
            },
        );
    }

    /// What passes the end of river `from` enters network `to` at the head of `channel`.
    pub fn river_to_network(&mut self, from: u32, to: u32, channel: u32) {
        self.core.set_river_end(
            from as usize,
            RiverEnd::Network {
                to: to as usize,
                channel: channel as usize,
            },
        );
    }

    /// River `from` hands on to a reach that is not here: what passes its end is lost at the join.
    pub fn river_lost(&mut self, from: u32) {
        self.core.set_river_end(from as usize, RiverEnd::Lost);
    }

    /// Network `net`'s outlet `k` (its `set_outlets` order) feeds network `to` at junction `node`.
    pub fn outlet(&mut self, net: u32, k: u32, to: u32, node: u32) {
        self.core
            .set_outlet(net as usize, k as usize, to as usize, node as usize);
    }

    /// What network `net` passes on at junction `node` enters mesh `mesh` at (x, y), m.
    pub fn junction_out(&mut self, net: u32, node: u32, mesh: u32, x: f64, y: f64) {
        self.core
            .set_junction_out(net as usize, node as usize, mesh as usize, x, y);
    }

    /// What network `net` passes on through cut `k` enters mesh `mesh` at (x, y), m.
    pub fn cut_out(&mut self, net: u32, k: u32, mesh: u32, x: f64, y: f64) {
        self.core
            .set_cut_out(net as usize, k as usize, mesh as usize, x, y);
    }

    /// Mesh `mesh`'s handoff edge `edge` (its id) passes to network `net`.
    pub fn edge(&mut self, mesh: u32, edge: u32, net: u32) {
        self.core.set_edge(mesh as usize, edge, net as usize);
    }

    /// A crossing of the join between network `net` and mesh `mesh` at (x, y)
    /// on the mesh, back into `net` at junction `index` (`cut` false) or
    /// through cut `index` (`cut` true). What crosses back enters at the nearest.
    pub fn crossing_back(&mut self, mesh: u32, net: u32, cut: bool, index: u32, x: f64, y: f64) {
        let at = if cut {
            Entry::Cut(index as usize)
        } else {
            Entry::Junction(index as usize)
        };
        self.core
            .add_crossing_back(mesh as usize, net as usize, at, x, y);
    }

    /// Run every reach on to `t` together.
    pub fn step_to(&mut self, t: f64) {
        self.core.step_to(t);
    }

    /// Drift every reach's flow particles `dt` s with time held, passing them across the joins.
    pub fn drift_frozen(&mut self, dt: f64) {
        self.core.drift_frozen(dt);
    }

    #[must_use]
    pub fn time(&self) -> f64 {
        self.core.time()
    }

    /// Every reach's mass together, kg: [released, in water, past the end of
    /// a river that hands on to nothing, broken down, exported, diverted, to
    /// sea from a network, to sea from a mesh, in a canal, carried on down a
    /// canal, on the bed, lost at a join].
    #[must_use]
    pub fn mass_balance(&self) -> Vec<f64> {
        let b = self.core.balance();
        vec![
            b.released,
            b.in_water,
            b.past_end,
            b.decayed,
            b.exported,
            b.diverted,
            b.to_sea,
            b.mesh_to_sea,
            b.canal_in_water,
            b.canal_exported,
            b.on_bed,
            b.lost_at_join,
        ]
    }
}

impl Default for Region {
    fn default() -> Self {
        Self::new()
    }
}
