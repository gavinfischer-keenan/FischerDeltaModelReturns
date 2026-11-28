//! A region's reaches run together, passing parcels and flow particles across
//! every join, with one mass balance for them all (issue #60).
//!
//! A region is river reaches (`Engine`), channel networks (`NetworkTracer`)
//! and meshes (`MeshTracer`). Each runs on its own; the region steps them
//! together, `EXCHANGE_S` at a time, and after each step hands on what
//! crossed a join:
//!
//! - what passed the end of a river reach is released, one parcel each, where
//!   that river joins the next: part way along another river reach, or at the
//!   head of a network's channel;
//! - what a network pumped out through an outlet enters the network the
//!   outlet feeds (a canal) at its junction;
//! - what left a network for a mesh, at a junction or a cut, enters the mesh
//!   at that crossing's place on the mesh;
//! - what crossed a mesh's handoff edge enters the network that edge hands
//!   to, at the crossing of that join nearest where it crossed.
//!
//! The joins are given as plain data (which reach, which junction, cut or
//! outlet, and where); the region knows nothing of ids or files. Mass handed
//! to a join with nowhere to go is kept on the books as lost at the join,
//! never dropped. Mass passed from one reach to another is counted as released
//! once, in the reach it was first released into.
//!
//! The reaches are shared (`Shared`): the region steps them, and whoever made
//! them (the browser's worker) still reads them to draw.

use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use crate::mesh::{MeshFate, MeshTracer};
use crate::network::{NetRelease, NetworkTracer, Place, Via};
use crate::Engine;

/// A reach the region steps and its maker reads.
pub type Shared<T> = Rc<RefCell<T>>;

/// Reaches pass parcels to each other this often, s of model time. A parcel
/// handed back waits at most this long at the join.
pub const EXCHANGE_S: f64 = 300.0;

/// Where what passes the end of a river reach goes.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum RiverEnd {
    /// Nowhere: it stays counted as past the end of the reach.
    Open,
    /// Into river reach `to`, `at_m` m along it.
    River { to: usize, at_m: f64 },
    /// Into network `to`, at the head of channel `channel`.
    Network { to: usize, channel: usize },
    /// The river hands on to a reach that is not here: lost at the join.
    Lost,
}

/// Where something enters a network from a mesh.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Entry {
    /// At this junction.
    Junction(usize),
    /// Through this cut (`NetworkTracer::set_cuts`).
    Cut(usize),
}

/// A place on a mesh where a network's join crosses into it.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Crossing {
    mesh: usize,
    x: f64,
    y: f64,
}

struct NetworkReach {
    tracer: Shared<NetworkTracer>,
    /// A canal: what is in it and carried on down it is shown apart.
    canal: bool,
    /// Each outlet's network and junction (`set_outlets` order); None: lost.
    outlets: Vec<Option<(usize, usize)>>,
    /// Junctions where it passes things to a mesh, and where they enter.
    junctions: HashMap<usize, Crossing>,
    /// Each cut's place on the mesh it passes to (`set_cuts` order).
    cuts: Vec<Option<Crossing>>,
}

struct MeshReach {
    tracer: Shared<MeshTracer>,
    /// The network each handoff edge (by id) passes to.
    edges: HashMap<u32, usize>,
    /// For each network, its crossings into this mesh, in order: what crosses
    /// back goes in at the nearest (the first of equals).
    back: HashMap<usize, Vec<(Entry, f64, f64)>>,
}

/// Every reach's mass together, kg (specification section 8.1).
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct RegionBalance {
    /// Released into the region (mass passed between reaches counted once).
    pub released: f64,
    /// In the water of the rivers, networks (not canals) and meshes.
    pub in_water: f64,
    /// Past the end of a river reach that hands on to nothing.
    pub past_end: f64,
    pub decayed: f64,
    /// Exported from a network (not a canal) and not carried on.
    pub exported: f64,
    pub diverted: f64,
    /// Out to sea from a network.
    pub to_sea: f64,
    /// Out to sea from a mesh.
    pub mesh_to_sea: f64,
    /// In a canal's water.
    pub canal_in_water: f64,
    /// Carried on down a canal, beyond the map.
    pub canal_exported: f64,
    /// Bound to sediment, settled on the bed.
    pub on_bed: f64,
    /// Handed to a join with nowhere to go.
    pub lost_at_join: f64,
}

impl RegionBalance {
    /// Everything that has happened to the released mass: equal to `released`.
    #[must_use]
    pub fn accounted(&self) -> f64 {
        self.in_water
            + self.past_end
            + self.decayed
            + self.exported
            + self.diverted
            + self.to_sea
            + self.mesh_to_sea
            + self.canal_in_water
            + self.canal_exported
            + self.on_bed
            + self.lost_at_join
    }
}

fn half_life(h: Option<f64>) -> Option<f64> {
    h.filter(|v| v.is_finite() && *v > 0.0)
}

/// The reaches of a region, run together.
#[derive(Default)]
pub struct Region {
    rivers: Vec<(Shared<Engine>, RiverEnd)>,
    networks: Vec<NetworkReach>,
    meshes: Vec<MeshReach>,
    /// Mass passed into another reach, kg: counted there as released, so taken
    /// off the total once.
    handed_in: f64,
    lost_at_join: f64,
}

impl Region {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Add a river reach; returns its index. Add them upstream first: each is
    /// stepped, and hands on what passed its end, in the order added.
    pub fn add_river(&mut self, e: Shared<Engine>) -> usize {
        self.rivers.push((e, RiverEnd::Open));
        self.rivers.len() - 1
    }

    /// Add a network (a canal if `canal`); returns its index.
    pub fn add_network(&mut self, n: Shared<NetworkTracer>, canal: bool) -> usize {
        self.networks.push(NetworkReach {
            tracer: n,
            canal,
            outlets: Vec::new(),
            junctions: HashMap::new(),
            cuts: Vec::new(),
        });
        self.networks.len() - 1
    }

    /// Add a mesh; returns its index.
    pub fn add_mesh(&mut self, m: Shared<MeshTracer>) -> usize {
        self.meshes.push(MeshReach {
            tracer: m,
            edges: HashMap::new(),
            back: HashMap::new(),
        });
        self.meshes.len() - 1
    }

    /// Where what passes the end of river `from` goes.
    pub fn set_river_end(&mut self, from: usize, end: RiverEnd) {
        self.rivers[from].1 = end;
    }

    /// Network `net`'s outlet `k` (its `set_outlets` order) feeds network `to` at junction `node`.
    pub fn set_outlet(&mut self, net: usize, k: usize, to: usize, node: usize) {
        let o = &mut self.networks[net].outlets;
        if o.len() <= k {
            o.resize(k + 1, None);
        }
        o[k] = Some((to, node));
    }

    /// What network `net` passes on at junction `node` enters mesh `mesh` at
    /// (x, y), m. The first mesh given for a junction keeps it.
    pub fn set_junction_out(&mut self, net: usize, node: usize, mesh: usize, x: f64, y: f64) {
        self.networks[net]
            .junctions
            .entry(node)
            .or_insert(Crossing { mesh, x, y });
    }

    /// What network `net` passes on through cut `k` enters mesh `mesh` at (x, y), m.
    pub fn set_cut_out(&mut self, net: usize, k: usize, mesh: usize, x: f64, y: f64) {
        let c = &mut self.networks[net].cuts;
        if c.len() <= k {
            c.resize(k + 1, None);
        }
        c[k] = Some(Crossing { mesh, x, y });
    }

    /// Mesh `mesh`'s handoff edge `edge` (its id) passes to network `net`.
    pub fn set_edge(&mut self, mesh: usize, edge: u32, net: usize) {
        self.meshes[mesh].edges.insert(edge, net);
    }

    /// A crossing of the join between network `net` and mesh `mesh`, at (x, y)
    /// on the mesh: what crosses back from the mesh nearest it enters `net` there.
    pub fn add_crossing_back(&mut self, mesh: usize, net: usize, at: Entry, x: f64, y: f64) {
        self.meshes[mesh]
            .back
            .entry(net)
            .or_default()
            .push((at, x, y));
    }

    /// The region's time, s: the earliest of its reaches' (they move together).
    #[must_use]
    pub fn time(&self) -> f64 {
        let r = self.rivers.iter().map(|(e, _)| e.borrow().time());
        let n = self.networks.iter().map(|n| n.tracer.borrow().time());
        let m = self.meshes.iter().map(|m| m.tracer.borrow().time());
        r.chain(n).chain(m).fold(f64::INFINITY, f64::min)
    }

    /// Run every reach on to `to` together, passing parcels across every join.
    pub fn step_to(&mut self, to: f64) {
        let mut now = self.time();
        while now < to {
            let t = to.min(now + EXCHANGE_S);
            // Rivers first, upstream ones first, each into what it joins.
            for i in 0..self.rivers.len() {
                let out = {
                    let mut e = self.rivers[i].0.borrow_mut();
                    e.step_to(t);
                    e.take_outflow()
                };
                self.hand_out(i, &out);
            }
            // Networks on, then into the canals and meshes they feed.
            for i in 0..self.networks.len() {
                let handed = {
                    let mut n = self.networks[i].tracer.borrow_mut();
                    n.step_to(t);
                    n.take_handed()
                };
                for h in handed {
                    self.receive_from_network(i, h.via, h.t, h.mass, h.half_life);
                }
            }
            // Meshes on, then back into the networks at the nearest crossing.
            for i in 0..self.meshes.len() {
                let handed = {
                    let mut m = self.meshes[i].tracer.borrow_mut();
                    m.step_to(t);
                    m.take_handed()
                };
                for (fate, mass, hl) in handed {
                    if let MeshFate::Handoff { id, x, y, .. } = fate {
                        self.receive_from_mesh(i, id, x, y, mass, hl);
                    }
                }
            }
            self.exchange_particles();
            let later = self.time();
            if later <= now {
                break; // a reach that cannot go on would hold the rest forever
            }
            now = later;
        }
    }

    /// Drift every reach's flow particles `dt` s on the flow of the moment,
    /// time held (the page's still view), passing particles across the joins.
    pub fn drift_frozen(&mut self, dt: f64) {
        for (e, _) in &self.rivers {
            e.borrow_mut().drift_frozen(dt);
        }
        for n in &self.networks {
            n.tracer.borrow_mut().drift_frozen(dt);
        }
        for m in &self.meshes {
            m.tracer.borrow_mut().drift_frozen(dt);
        }
        self.exchange_particles();
    }

    /// Hand what passed the end of river `from` on to where it joins.
    fn hand_out(&mut self, from: usize, out: &[(f64, f64, Option<f64>)]) {
        let end = self.rivers[from].1;
        for &(t, mass, hl) in out {
            match end {
                RiverEnd::Open => {}
                RiverEnd::Lost => self.lost_at_join += mass,
                RiverEnd::River { to, at_m } => {
                    // A parcel of its own, never a release looped over every step (#84).
                    self.rivers[to]
                        .0
                        .borrow_mut()
                        .take_parcel(at_m, t, mass, half_life(hl));
                    self.handed_in += mass;
                }
                RiverEnd::Network { to, channel } => {
                    self.networks[to]
                        .tracer
                        .borrow_mut()
                        .add_release(NetRelease {
                            at: Place::Channel(channel, 0.0),
                            start: t,
                            duration: 0.0,
                            mass,
                            half_life: half_life(hl),
                            parcels: 1,
                        });
                    self.handed_in += mass;
                }
            }
        }
    }

    /// Where something network `net` passed on at `via` enters a mesh.
    fn crossing_out(&self, net: usize, via: Via) -> Option<Crossing> {
        let n = &self.networks[net];
        match via {
            Via::Junction(j) => n.junctions.get(&j).copied(),
            Via::Cut(k) => n.cuts.get(k).copied().flatten(),
            Via::Outlet(_) => None,
        }
    }

    fn receive_from_network(&mut self, net: usize, via: Via, t: f64, mass: f64, hl: Option<f64>) {
        if let Via::Outlet(k) = via {
            match self.networks[net].outlets.get(k).copied().flatten() {
                Some((to, node)) => {
                    self.networks[to]
                        .tracer
                        .borrow_mut()
                        .take_parcel(node, mass, hl);
                    self.handed_in += mass;
                }
                None => self.lost_at_join += mass,
            }
            return;
        }
        let taken = self.crossing_out(net, via).is_some_and(|c| {
            self.meshes[c.mesh]
                .tracer
                .borrow_mut()
                .take_parcel(c.x, c.y, mass, t, hl)
        });
        if taken {
            self.handed_in += mass;
        } else {
            self.lost_at_join += mass;
        }
    }

    /// The network, and where in it, for something that left mesh `mesh` by
    /// handoff edge `edge` at (x, y): the nearest crossing of that join.
    fn crossing_in(&self, mesh: usize, edge: u32, x: f64, y: f64) -> Option<(usize, Entry)> {
        let m = &self.meshes[mesh];
        let net = *m.edges.get(&edge)?;
        let mut best = None;
        let mut d = f64::INFINITY;
        for &(at, cx, cy) in m.back.get(&net)? {
            let dd = (cx - x).powi(2) + (cy - y).powi(2);
            if dd < d {
                d = dd;
                best = Some((net, at));
            }
        }
        best
    }

    fn receive_from_mesh(
        &mut self,
        mesh: usize,
        edge: u32,
        x: f64,
        y: f64,
        mass: f64,
        hl: Option<f64>,
    ) {
        let Some((net, at)) = self.crossing_in(mesh, edge, x, y) else {
            self.lost_at_join += mass;
            return;
        };
        let mut n = self.networks[net].tracer.borrow_mut();
        match at {
            Entry::Cut(k) => n.take_parcel_at_cut(k, mass, hl),
            Entry::Junction(j) => n.take_parcel(j, mass, hl),
        }
        self.handed_in += mass;
    }

    /// Flow particles that crossed a join carry on in the other reach, one for
    /// one, through the same crossing as water does (each reach gives up its
    /// oldest to take one in, so its number stays the same): what leaves the
    /// Delta carries on as a plume in the Bay, and the other way.
    fn exchange_particles(&mut self) {
        for i in 0..self.networks.len() {
            let handed = self.networks[i].tracer.borrow_mut().take_handed_particles();
            for via in handed {
                if let Via::Outlet(k) = via {
                    if let Some((to, node)) = self.networks[i].outlets.get(k).copied().flatten() {
                        self.networks[to].tracer.borrow_mut().take_particle(node);
                    }
                } else if let Some(c) = self.crossing_out(i, via) {
                    self.meshes[c.mesh]
                        .tracer
                        .borrow_mut()
                        .take_particle(c.x, c.y);
                }
            }
        }
        for i in 0..self.meshes.len() {
            let handed = self.meshes[i].tracer.borrow_mut().take_handed_particles();
            for (edge, x, y) in handed {
                let Some((net, at)) = self.crossing_in(i, edge, x, y) else {
                    continue;
                };
                let mut n = self.networks[net].tracer.borrow_mut();
                match at {
                    Entry::Cut(k) => n.take_particle_at_cut(k),
                    Entry::Junction(j) => n.take_particle(j),
                };
            }
        }
    }

    /// Every reach's mass balance together.
    #[must_use]
    pub fn balance(&self) -> RegionBalance {
        let mut b = RegionBalance {
            released: -self.handed_in,
            lost_at_join: self.lost_at_join,
            ..RegionBalance::default()
        };
        for (e, end) in &self.rivers {
            let r = e.borrow().mass_balance();
            b.released += r.released;
            b.in_water += r.in_water;
            // Handed on (or lost at the join), it is counted there instead.
            if *end == RiverEnd::Open {
                b.past_end += r.past_end;
            }
            b.decayed += r.decayed;
        }
        for n in &self.networks {
            let r = n.tracer.borrow().balance();
            b.released += r.released;
            b.decayed += r.decayed;
            b.on_bed += r.on_bed;
            if n.canal {
                b.canal_in_water += r.in_water;
                b.canal_exported += r.exported;
            } else {
                b.in_water += r.in_water;
                b.exported += r.exported;
            }
            b.diverted += r.diverted;
            b.to_sea += r.to_sea;
        }
        for m in &self.meshes {
            let r = m.tracer.borrow().balance();
            b.released += r.released;
            b.in_water += r.in_water;
            b.decayed += r.decayed;
            b.mesh_to_sea += r.to_sea;
        }
        b
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mesh::{MeshRelease, MeshTopology};
    use crate::network::Topology;
    use crate::Release;
    use fdmr_format::{Encoding, Field, FieldFile};

    fn shared<T>(v: T) -> Shared<T> {
        Rc::new(RefCell::new(v))
    }

    fn file(
        reach: &str,
        dims: u8,
        step_s: u32,
        steps: u32,
        elements: u32,
        fields: Vec<(&str, Vec<f32>)>,
    ) -> Vec<u8> {
        let f = FieldFile {
            reach: reach.into(),
            dims,
            elements,
            layers: 1,
            steps,
            t0: 0,
            step_s,
            fields: fields
                .into_iter()
                .map(|(n, v)| Field {
                    name: n.into(),
                    unit: String::new(),
                    encoding: Encoding::F32,
                    values: v,
                })
                .collect(),
        };
        let mut b = Vec::new();
        f.write_to(&mut b).unwrap();
        b
    }

    /// Each value held for every step.
    fn held(v: &[f32], steps: u32) -> Vec<f32> {
        v.iter()
            .copied()
            .cycle()
            .take(v.len() * steps as usize)
            .collect()
    }

    /// A river of five 1 km elements at 1 m/s.
    fn river() -> Engine {
        let els: Vec<(f64, f64)> = (0..5)
            .map(|i| (f64::from(i) * 1000.0, f64::from(i + 1) * 1000.0))
            .collect();
        let mut e = Engine::new(&els, 0.0, 11);
        let n = 5 * 96;
        e.load_day(&file(
            "river",
            1,
            900,
            96,
            5,
            vec![
                ("velocity", vec![1.0; n]),
                ("volume", vec![100_000.0; n]),
                ("dispersion", vec![5.0; n]),
            ],
        ))
        .unwrap();
        e
    }

    /// Network flows `q` per channel, area 10, over 400 steps of 900 s.
    fn network(topo: Topology, q: &[f32], seed: u64) -> NetworkTracer {
        let mut t = NetworkTracer::new(topo, 0.0, seed, 0.0);
        let len = u32::try_from(q.len()).unwrap();
        t.load_channels(&file(
            "net",
            1,
            900,
            400,
            len,
            vec![
                ("flow_up", held(q, 400)),
                ("flow_down", held(q, 400)),
                ("area", held(&vec![10.0; q.len()], 400)),
            ],
        ))
        .unwrap();
        t
    }

    /// The Delta: channel 0 (junction 0 to 1) splits into channel 1 (to
    /// junction 2, pumped out there to a canal) and channel 2 (to junction 3,
    /// tracked by the Bay).
    fn delta() -> NetworkTracer {
        let mut t = network(
            Topology {
                up: vec![0, 1, 1],
                down: vec![1, 2, 3],
                length: vec![1000.0; 3],
                nodes: 4,
                sea: vec![],
                pools: 0,
                link_pool: vec![],
                link_node: vec![],
            },
            &[40.0, 30.0, 10.0],
            5,
        );
        t.load_nodes(&file(
            "net",
            1,
            900,
            400,
            4,
            vec![
                ("export", held(&[0.0, 0.0, 30.0, 0.0], 400)),
                ("withdrawal", held(&[0.0; 4], 400)),
                ("volume", held(&[f32::NAN; 4], 400)),
            ],
        ))
        .unwrap();
        t.set_handoff(&[2]);
        t.set_outlets(&[2]);
        t
    }

    /// A canal: one channel to the sea.
    fn canal() -> NetworkTracer {
        network(
            Topology {
                up: vec![0],
                down: vec![1],
                length: vec![1000.0],
                nodes: 2,
                sea: vec![1],
                pools: 0,
                link_pool: vec![],
                link_node: vec![],
            },
            &[30.0],
            6,
        )
    }

    /// A bay: a channel 1000 m long and 200 m wide in triangles, land along
    /// its sides, the sea at x = 1000 and a handoff edge (id 7) at x = 0,
    /// with a uniform current u.
    fn bay(u: f32) -> MeshTracer {
        let (nx, ny) = (11usize, 3usize);
        let (mut x, mut y) = (Vec::new(), Vec::new());
        for j in 0..ny {
            for i in 0..nx {
                #[allow(clippy::cast_precision_loss)]
                {
                    x.push(i as f64 * 100.0);
                    y.push(j as f64 * 100.0);
                }
            }
        }
        let id = |i: usize, j: usize| j * nx + i;
        let mut tri = Vec::new();
        for j in 0..ny - 1 {
            for i in 0..nx - 1 {
                tri.push([id(i, j), id(i + 1, j), id(i + 1, j + 1)]);
                tri.push([id(i, j), id(i + 1, j + 1), id(i, j + 1)]);
            }
        }
        let n = x.len();
        let topo = MeshTopology {
            depth: vec![5.0; n],
            x,
            y,
            tri,
            open: (0..ny - 1)
                .map(|j| (id(nx - 1, j), id(nx - 1, j + 1)))
                .collect(),
            handoff: (0..ny - 1).map(|j| (id(0, j), id(0, j + 1), 7)).collect(),
        };
        let mut m = MeshTracer::new(topo, 0.0, 3, 0.0);
        let steps = 25;
        let len = n * steps as usize;
        m.load_day(&file(
            "bay",
            2,
            3600,
            steps,
            u32::try_from(n).unwrap(),
            vec![
                ("u", vec![u; len]),
                ("v", vec![0.0; len]),
                ("level", vec![0.0; len]),
            ],
        ))
        .unwrap();
        m
    }

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    #[test]
    fn mass_is_conserved_from_the_river_through_the_delta_to_the_bay_and_the_canal() {
        let r = shared(river());
        r.borrow_mut().add_release(Release {
            x: 500.0,
            start: 0.0,
            duration: 600.0,
            mass: 1.0,
            half_life: Some(50_000.0),
            parcels: 400,
        });
        let (d, c, b) = (shared(delta()), shared(canal()), shared(bay(0.1)));
        let mut region = Region::new();
        let ri = region.add_river(r.clone());
        let di = region.add_network(d.clone(), false);
        let ci = region.add_network(c.clone(), true);
        let bi = region.add_mesh(b.clone());
        region.set_river_end(ri, RiverEnd::Network { to: di, channel: 0 });
        region.set_outlet(di, 0, ci, 0);
        region.set_junction_out(di, 1, bi, 1.0, 100.0);
        region.set_edge(bi, 7, di);
        region.add_crossing_back(bi, di, Entry::Junction(1), 1.0, 100.0);
        for (t, check) in [(3_000.0, false), (9_000.0, false), (40_000.0, true)] {
            region.step_to(t);
            let bal = region.balance();
            assert!(close(bal.released, 1.0), "{bal:?}");
            assert!(close(bal.accounted(), bal.released), "{t}: {bal:?}");
            assert!(bal.lost_at_join.abs() < 1e-12, "{bal:?}");
            if check {
                // It got everywhere: out of the Bay to sea, and down the canal to sea.
                assert!(
                    bal.mesh_to_sea > 0.05 && bal.to_sea > 0.3 && bal.decayed > 0.0,
                    "{bal:?}"
                );
                assert!(close(
                    r.borrow().mass_balance().past_end,
                    d.borrow().balance().released
                ));
            }
        }
        assert!(close(region.time(), 40_000.0));
        // The reaches are the maker's to read as well.
        assert!(c.borrow().balance().released > 0.0 && b.borrow().balance().released > 0.0);
    }

    #[test]
    fn what_the_bay_hands_back_enters_the_delta_at_the_nearest_crossing() {
        let (d, b) = (shared(delta()), shared(bay(-0.1)));
        b.borrow_mut().add_release(MeshRelease {
            x: 500.0,
            y: 100.0,
            start: 0.0,
            duration: 0.0,
            mass: 2.0,
            half_life: None,
            parcels: 50,
        });
        let mut region = Region::new();
        let di = region.add_network(d.clone(), false);
        let bi = region.add_mesh(b.clone());
        region.set_edge(bi, 7, di);
        // Two crossings; everything crosses at y = 0..200, nearer junction 1's.
        region.add_crossing_back(bi, di, Entry::Junction(3), 0.0, 5_000.0);
        region.add_crossing_back(bi, di, Entry::Junction(1), 0.0, 100.0);
        region.step_to(8_000.0);
        let bal = region.balance();
        assert!(
            close(bal.released, 2.0) && close(bal.accounted(), 2.0),
            "{bal:?}"
        );
        let back = d.borrow().balance();
        assert!(
            close(back.released, b.borrow().balance().handed_on) && back.released > 1.9,
            "{back:?}"
        );
        // In at junction 1, it goes on down channel 1 (channel 2 is the Bay's)
        // and is pumped out at junction 2, with no canal loaded: lost at that join.
        assert!(
            bal.lost_at_join > 0.0 && close(bal.lost_at_join, back.handed_on),
            "{bal:?}"
        );
    }

    #[test]
    fn a_join_with_nowhere_to_go_keeps_its_mass_as_lost_and_counts_it_once() {
        let r = shared(river());
        r.borrow_mut().add_release(Release {
            x: 500.0,
            start: 0.0,
            duration: 0.0,
            mass: 1.0,
            half_life: None,
            parcels: 100,
        });
        let mut region = Region::new();
        let ri = region.add_river(r.clone());
        region.set_river_end(ri, RiverEnd::Lost);
        region.step_to(20_000.0);
        let bal = region.balance();
        assert!(
            close(bal.lost_at_join, 1.0) && bal.past_end.abs() < 1e-12,
            "{bal:?}"
        );
        assert!(close(bal.accounted(), bal.released), "{bal:?}");
    }

    #[test]
    fn one_river_hands_on_part_way_along_another() {
        let (trib, main) = (shared(river()), shared(river()));
        trib.borrow_mut().add_release(Release {
            x: 0.0,
            start: 0.0,
            duration: 0.0,
            mass: 1.0,
            half_life: None,
            parcels: 100,
        });
        let mut region = Region::new();
        let ti = region.add_river(trib.clone());
        let mi = region.add_river(main.clone());
        region.set_river_end(
            ti,
            RiverEnd::River {
                to: mi,
                at_m: 2_500.0,
            },
        );
        region.step_to(6_000.0);
        let bal = region.balance();
        assert!(
            close(bal.released, 1.0) && close(bal.accounted(), 1.0),
            "{bal:?}"
        );
        let m = main.borrow().mass_balance();
        assert!(close(m.released, trib.borrow().mass_balance().past_end) && m.released > 0.5);
        // Released 2.5 km along, at 1 m/s nothing has reached the main river's end yet.
        assert!(m.past_end < 1e-12, "{m:?}");
        let _ = mi;
    }
}
