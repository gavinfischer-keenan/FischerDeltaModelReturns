//! Flow and tracking through a channel network (docs/format.md, network
//! reaches; specification 7.2 and 8.1).
//!
//! A network is channels joined at junctions, with pools (flooded islands,
//! forebays) joined to junctions by links. Its published flow comes as three
//! sets of day files: channels (`flow_up`, `flow_down`, `area`), junctions
//! and pools (`inflow`, `export`, `withdrawal`, `volume`) and links (`flow`,
//! positive from the pool to its junction).
//!
//! A parcel in a channel moves with the local velocity, linear between the
//! channel's two ends, plus a random step for dispersion. At a junction it
//! leaves by one of the ways water is leaving: into a channel, into a pool,
//! to the pumps, to another diversion or, at the sea boundary, out to sea,
//! chosen in proportion to the flow each way. In a pool it is well mixed and
//! leaves at the rate water leaves. Flow particles do the same without
//! dispersion or mass, and start again somewhere else when they leave, so
//! the network always shows its water moving.

use fdmr_format::FormatError;
use std::collections::HashMap;

use fdmr_math::{decay, parcel, rng::Rng};

use crate::particles::{FlowParticles, NetFlow};
use crate::{series::Series, MAX_DT};

mod joins;
mod pools;
mod routing;
mod sediment;
use pools::PoolWater;

/// How the network is joined. Junctions and pools are indices from 0.
#[derive(Debug, Clone)]
pub struct Topology {
    pub up: Vec<usize>,
    pub down: Vec<usize>,
    pub length: Vec<f64>,
    pub nodes: usize,
    /// Junctions where the sea sets the level: water leaving there goes to sea.
    pub sea: Vec<usize>,
    pub pools: usize,
    pub link_pool: Vec<usize>,
    pub link_node: Vec<usize>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum End {
    Up,
    Down,
}

/// Where a parcel or particle is.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Place {
    /// In a channel, `x` m from its upstream junction.
    Channel(usize, f64),
    Pool(usize),
    /// Left the network.
    Gone(Fate),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Fate {
    Exported,
    Diverted,
    ToSea,
    /// Passed to another reach at this junction (see `set_handoff`).
    HandedOn(usize),
    /// Passed to another reach at this cut (see `set_cuts`).
    Crossed(usize),
    /// Pumped out at this outlet into another reach, such as a canal (see `set_outlets`).
    Outlet(usize),
}

/// Where something was passed to another reach.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Via {
    /// At this junction (`set_handoff`).
    Junction(usize),
    /// At this cut (`set_cuts`).
    Cut(usize),
    /// Through this outlet (`set_outlets`).
    Outlet(usize),
}

impl Fate {
    /// Where it went on to another reach, if it did.
    fn via(self) -> Option<Via> {
        match self {
            Fate::HandedOn(n) => Some(Via::Junction(n)),
            Fate::Crossed(k) => Some(Via::Cut(k)),
            Fate::Outlet(k) => Some(Via::Outlet(k)),
            Fate::Exported | Fate::Diverted | Fate::ToSea => None,
        }
    }
}

/// A release into the network.
#[derive(Debug, Clone, Copy)]
pub struct NetRelease {
    pub at: Place,
    pub start: f64,
    /// s; 0 releases everything at once.
    pub duration: f64,
    pub mass: f64,
    pub half_life: Option<f64>,
    pub parcels: usize,
}

#[derive(Debug, Clone, Copy)]
struct Parcel {
    place: Place,
    mass: f64,
    born: f64,
    half_life: Option<f64>,
    /// Bound to the suspended sediment (`add_bound_release`): it settles and
    /// is scoured back up with the mud, where the reach has a sediment run.
    bound: bool,
    /// Lying on the bed (a bound parcel that has settled).
    on_bed: bool,
}

/// [released, in the water, exported, diverted, out to sea, broken down,
/// handed on to another reach], kg.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct NetBalance {
    pub released: f64,
    pub in_water: f64,
    pub exported: f64,
    pub diverted: f64,
    pub to_sea: f64,
    pub decayed: f64,
    pub handed_on: f64,
    /// Bound to sediment and lying on the bed, kg (`add_bound_release`).
    pub on_bed: f64,
}

/// A parcel passed to another reach: where (`via`), at time `t` (s), with
/// its mass (kg) and half-life (s, if it breaks down).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Handed {
    pub via: Via,
    pub t: f64,
    pub mass: f64,
    pub half_life: Option<f64>,
}

/// Conditions at one moment, worked out once per step.
struct Now {
    u_up: Vec<f64>,
    u_down: Vec<f64>,
    q_up: Vec<f64>,
    q_down: Vec<f64>,
    volume: Vec<f64>,
    export: Vec<f64>,
    withdrawal: Vec<f64>,
    pool_volume: Vec<f64>,
    link_flow: Vec<f64>,
}

/// Parcels and flow particles moving through a network reach.
pub struct NetworkTracer {
    topo: Topology,
    /// For each junction: the channels meeting there, and which of their ends.
    at_node: Vec<Vec<(usize, End)>>,
    links_at_node: Vec<Vec<usize>>,
    links_of_pool: Vec<Vec<usize>>,
    is_sea: Vec<bool>,
    channels: Series,
    /// Water-quality fields per channel (`<day>.quality.fdf`), loaded to be shown.
    quality: Series,
    nodes: Series,
    links: Series,
    /// Tidally filtered copies of the channel, junction and link files, which
    /// the flow particles move on in net-flow mode; built by `set_net_flow`.
    net: NetFlow<[Series; 3]>,
    dispersion: f64,
    time: f64,
    rng: Rng,
    parcels: Vec<Parcel>,
    /// The flow particles: each one's place, age and life, s (`crate::PARTICLE_LIFE_S`).
    flow: FlowParticles<Place>,
    /// Flow particles passed to another reach: the junction each reached.
    handed_particles: Vec<Via>,
    balance: NetBalance,
    /// Channels another reach tracks (such as a bay's, where a mesh covers them).
    handed_channel: Vec<bool>,
    /// Per channel, where another reach takes over along it: (place along it,
    /// 0 to 1 from its upstream end; whether the other reach's part is
    /// downstream of that place; the cut's number).
    cut: Vec<Option<(f64, bool, usize)>>,
    /// The cuts by number: (channel, place along it, other reach downstream).
    cuts: Vec<(usize, f64, bool)>,
    /// Each channel's water-surface width, m, if given (`set_widths`): flow
    /// particles then start evenly over the water's surface, as on a mesh,
    /// and the water's depth (for settling) is its cross-section over it.
    widths: Vec<f64>,
    /// In where flow particles start, a channel counts as at least this wide,
    /// m (`set_min_seed_width`), so narrow channels still show their water
    /// moving. Particle placement only: never the physics (#50).
    min_seed_width: f64,
    /// Each pool's water surface, m², if given (`set_pool_areas`): where flow
    /// particles start evenly over the water's surface, pools get their share.
    pool_area: Vec<f64>,
    /// Each pool's mapped water, where given (`set_pool_water`): what a pool
    /// particle drifts over, for drawing.
    pool_water: Vec<Option<PoolWater>>,
    /// Where each flow particle in a pool is on its water: (pool, x, y in the
    /// pool's own metres), or None.
    pool_at: Vec<Option<(usize, f64, f64)>>,
    /// The pool drift's own random numbers, so it never changes a parcel's path.
    pool_rng: Rng,
    /// Per junction then pool, the outlet its exports go through to another
    /// reach, if any (`set_outlets`).
    outlet: Vec<Option<usize>>,
    handed: Vec<Handed>,
    /// The suspended sediment's settling velocity, m/s, where the reach has a
    /// sediment run (`set_settling`): bound parcels settle at it.
    settling: Option<f64>,
}

impl NetworkTracer {
    /// `dispersion`, m²/s, is the spreading coefficient for every channel.
    #[must_use]
    pub fn new(topo: Topology, start_time: f64, seed: u64, dispersion: f64) -> Self {
        let mut at_node = vec![Vec::new(); topo.nodes];
        for (c, (&u, &d)) in topo.up.iter().zip(&topo.down).enumerate() {
            at_node[u].push((c, End::Up));
            at_node[d].push((c, End::Down));
        }
        let mut links_at_node = vec![Vec::new(); topo.nodes];
        let mut links_of_pool = vec![Vec::new(); topo.pools];
        for (l, (&p, &n)) in topo.link_pool.iter().zip(&topo.link_node).enumerate() {
            links_at_node[n].push(l);
            links_of_pool[p].push(l);
        }
        let mut is_sea = vec![false; topo.nodes];
        for &n in &topo.sea {
            is_sea[n] = true;
        }
        Self {
            topo,
            at_node,
            links_at_node,
            links_of_pool,
            is_sea,
            channels: Series::default(),
            nodes: Series::default(),
            quality: Series::default(),
            links: Series::default(),
            net: NetFlow::default(),
            dispersion,
            time: start_time,
            rng: Rng::new(seed),
            parcels: Vec::new(),
            flow: FlowParticles::default(),
            handed_particles: Vec::new(),
            balance: NetBalance::default(),
            handed_channel: Vec::new(),
            cut: Vec::new(),
            cuts: Vec::new(),
            widths: Vec::new(),
            min_seed_width: 0.0,
            pool_area: Vec::new(),
            pool_water: Vec::new(),
            pool_at: Vec::new(),
            pool_rng: Rng::new(seed ^ 0x9E37_79B9_7F4A_7C15),
            outlet: Vec::new(),
            handed: Vec::new(),
            settling: None,
        }
    }

    /// Each channel's water-surface width, m, in channel order. Flow particles
    /// then start evenly over the water's surface (by width times length), as
    /// they do on a mesh, so that particles look as dense in a channel as in
    /// open water next to it. Without it they start by volume.
    pub fn set_widths(&mut self, widths: &[f64]) {
        self.widths = widths.to_vec();
    }

    /// Where flow particles start, count every channel as at least `w` m
    /// wide. Only where particles are placed: settling still uses the
    /// channels' own widths (#50).
    pub fn set_min_seed_width(&mut self, w: f64) {
        self.min_seed_width = w.max(0.0);
    }

    /// Load one day's channel file (`flow_up`, `flow_down`, `area`).
    ///
    /// # Errors
    /// If the file is not a network channel file for this topology.
    pub fn load_channels(&mut self, bytes: &[u8]) -> Result<(), FormatError> {
        self.net.invalidate();
        self.channels
            .load_day(bytes, &["flow_up", "flow_down", "area"])?;
        if self.channels.elements != self.topo.up.len() {
            return Err(FormatError::Invalid(
                "channel file does not match the network".into(),
            ));
        }
        Ok(())
    }

    /// Load one day's junction and pool file (`export`, `withdrawal`, `volume`).
    ///
    /// # Errors
    /// If the file is not a junction file for this topology.
    pub fn load_nodes(&mut self, bytes: &[u8]) -> Result<(), FormatError> {
        self.net.invalidate();
        self.nodes
            .load_day(bytes, &["export", "withdrawal", "volume"])?;
        if self.nodes.elements != self.topo.nodes + self.topo.pools {
            return Err(FormatError::Invalid(
                "junction file does not match the network".into(),
            ));
        }
        Ok(())
    }

    /// Load one day's water-quality file (`ec`, `salinity`, `temperature`,
    /// `source:<name>` per channel), to show them.
    ///
    /// # Errors
    /// If the file is not a channel file for this network.
    pub fn load_quality(&mut self, bytes: &[u8]) -> Result<(), FormatError> {
        self.quality.load_day(bytes, &[])?;
        if self.quality.elements != self.topo.up.len() {
            return Err(FormatError::Invalid(
                "quality file does not match the network".into(),
            ));
        }
        Ok(())
    }

    /// A field's value now in the channel where each flow particle is, in the
    /// order of `particles()`: a water-quality field, or one of the channel
    /// file's (such as `level`). `NaN` where there is none (in a pool, or a
    /// field not loaded).
    #[must_use]
    pub fn particle_values(&self, name: &str) -> Vec<f32> {
        let per_channel = if self.quality.has(name) {
            self.quality.at(self.time, name)
        } else if self.channels.has(name) {
            self.channels.at(self.time, name)
        } else {
            None
        };
        // A pool's own value, where the junction-and-pool file holds the field
        // (a pool's salinity, from QUAL); else none, and the UI draws it grey.
        let per_node = if self.nodes.has(name) {
            self.nodes.at(self.time, name)
        } else {
            None
        };
        let n = self.topo.up.len();
        let (el, _) = self.particles();
        el.iter()
            .map(|&e| {
                let e = e as usize;
                let v = if e < n {
                    per_channel.as_ref().and_then(|v| v.get(e))
                } else {
                    per_node
                        .as_ref()
                        .and_then(|v| v.get(self.topo.nodes + e - n))
                };
                v.map_or(f32::NAN, |&x| {
                    #[allow(clippy::cast_possible_truncation)]
                    let y = x as f32;
                    y
                })
            })
            .collect()
    }

    /// Load one day's link file (`flow`).
    ///
    /// # Errors
    /// If the file is not a link file for this topology.
    pub fn load_links(&mut self, bytes: &[u8]) -> Result<(), FormatError> {
        self.net.invalidate();
        self.links.load_day(bytes, &["flow"])?;
        if self.links.elements != self.topo.link_pool.len() {
            return Err(FormatError::Invalid(
                "link file does not match the network".into(),
            ));
        }
        Ok(())
    }

    #[must_use]
    pub fn time(&self) -> f64 {
        self.time
    }

    #[must_use]
    pub fn balance(&self) -> NetBalance {
        let mut b = self.balance;
        let held = |p: &&Parcel| p.born <= self.time && !matches!(p.place, Place::Gone(_));
        b.in_water = self
            .parcels
            .iter()
            .filter(held)
            .filter(|p| !p.on_bed)
            .map(|p| p.mass)
            .sum();
        b.on_bed = self
            .parcels
            .iter()
            .filter(held)
            .filter(|p| p.on_bed)
            .map(|p| p.mass)
            .sum();
        b
    }

    /// Add a release: its parcels start at its place, spread evenly over its duration.
    pub fn add_release(&mut self, r: NetRelease) {
        self.push_release(&r, 0.0);
    }

    fn push_release(&mut self, r: &NetRelease, bound_share: f64) {
        let n = r.parcels.max(1);
        // Bound parcels: the share of the parcels nearest the share of the mass
        // (at least one each way if both shares are real), each carrying its
        // kind's exact mass.
        #[allow(
            clippy::cast_possible_truncation,
            clippy::cast_sign_loss,
            clippy::cast_precision_loss
        )]
        let mut nb = (bound_share * n as f64).round() as usize;
        if bound_share > 0.0 && nb == 0 {
            nb = 1;
        }
        if bound_share < 1.0 && nb == n && n > 1 {
            nb = n - 1;
        }
        let nb = nb.min(n);
        #[allow(clippy::cast_precision_loss)]
        let (mb, md) = (
            if nb > 0 {
                r.mass * bound_share / nb as f64
            } else {
                0.0
            },
            if nb < n {
                r.mass * (1.0 - bound_share) / (n - nb) as f64
            } else {
                0.0
            },
        );
        for i in 0..n {
            #[allow(clippy::cast_precision_loss)]
            let born = r.start + r.duration * (i as f64 + 0.5) / n as f64;
            // Bound and dissolved parcels interleaved over the release's duration.
            #[allow(clippy::cast_precision_loss)]
            let bound = nb > 0 && ((i + 1) * nb / n) > (i * nb / n);
            self.parcels.push(Parcel {
                place: r.at,
                mass: if bound { mb } else { md },
                born,
                half_life: r.half_life,
                bound,
                on_bed: false,
            });
        }
    }

    pub fn clear_releases(&mut self) {
        self.parcels.clear();
        self.handed.clear();
        self.balance = NetBalance::default();
    }

    /// Run on to time `t`, in steps of at most a minute.
    pub fn step_to(&mut self, t: f64) {
        while self.time < t {
            let dt = (t - self.time).min(MAX_DT);
            let Some(now) = self.conditions(self.time + dt / 2.0) else {
                self.time += dt;
                continue;
            };
            let k = self.dispersion;
            let sed = self.sediment_now(self.time + dt / 2.0, &now);
            for i in 0..self.parcels.len() {
                let mut p = self.parcels[i];
                if p.born > self.time + dt || matches!(p.place, Place::Gone(_)) {
                    continue;
                }
                let step = (self.time + dt - p.born.max(self.time)).max(0.0);
                let keep = decay::remaining_fraction(step, p.half_life);
                self.balance.decayed += p.mass * (1.0 - keep);
                let mass = p.mass * keep;
                // A bound parcel settles at the sediment's rate, ws / depth, and
                // one on the bed is scoured back up at the bed's own rate,
                // erosion / settled mass (both from the sediment run); on the
                // bed it does not move.
                if let (true, Place::Channel(c, _), Some(s)) = (p.bound, p.place, sed.as_ref()) {
                    let rate = if p.on_bed {
                        s.resuspend[c]
                    } else {
                        s.settle[c]
                    };
                    if rate > 0.0 && self.rng.uniform() < 1.0 - (-rate * step).exp() {
                        p.on_bed = !p.on_bed;
                    }
                    if p.on_bed {
                        if p.born >= self.time && p.born < self.time + dt {
                            self.balance.released += p.mass;
                        }
                        self.parcels[i] = Parcel { mass, ..p };
                        continue;
                    }
                }
                let place = self.advance(p.place, step, &now, k);
                if let Place::Gone(fate) = place {
                    match fate {
                        Fate::Exported => self.balance.exported += mass,
                        Fate::Diverted => self.balance.diverted += mass,
                        Fate::ToSea => self.balance.to_sea += mass,
                        Fate::HandedOn(_) | Fate::Crossed(_) | Fate::Outlet(_) => {
                            self.balance.handed_on += mass;
                            if let Some(via) = fate.via() {
                                self.handed.push(Handed {
                                    via,
                                    t: self.time + dt,
                                    mass,
                                    half_life: p.half_life,
                                });
                            }
                        }
                    }
                }
                if p.born >= self.time && p.born < self.time + dt {
                    self.balance.released += p.mass;
                }
                self.parcels[i] = Parcel { place, mass, ..p };
            }
            if self.net.is_on() {
                if let Some(pnow) = self.particle_conditions(self.time + dt / 2.0) {
                    self.move_particles(dt, &pnow);
                }
            } else {
                self.move_particles(dt, &now);
            }
            self.time += dt;
        }
    }

    /// Move the flow particles `dt` s under the conditions at the current time,
    /// without advancing it (for "now": the water keeps moving, the clock does not).
    pub fn drift_frozen(&mut self, dt: f64) {
        crate::particles::in_steps(dt, |step| {
            if let Some(now) = self.particle_conditions(self.time) {
                self.move_particles(step, &now);
            }
        });
    }

    fn move_particles(&mut self, dt: f64, now: &Now) {
        for i in 0..self.flow.len() {
            let old = self.flow.age(i, dt);
            let p = self.advance(self.flow.places[i], dt, now, 0.0);
            // A particle that leaves or has lived its life starts again
            // elsewhere. In a pool it stays, well mixed, until the water
            // carries it out (`advance`) or its life ends.
            let renew = match p {
                Place::Channel(..) | Place::Pool(_) => old,
                Place::Gone(fate) => {
                    if let Some(via) = fate.via() {
                        self.handed_particles.push(via);
                    }
                    true
                }
            };
            if renew {
                let place = self.random_place(now);
                self.flow.renew(i, place, &mut self.rng);
            } else {
                self.flow.places[i] = p;
            }
        }
        self.drift_in_pools(dt);
    }

    /// A place anywhere in the network's own water, evenly over its surface
    /// (`set_widths`, and `set_pool_areas` for its pools), or else by volume:
    /// not in a channel another reach tracks, nor past a cut.
    fn random_place(&mut self, now: &Now) -> Place {
        let surface = !self.widths.is_empty();
        let pools: Vec<f64> = (0..self.topo.pools)
            .map(|q| {
                if surface {
                    self.pool_area.get(q).copied().unwrap_or(0.0)
                } else {
                    now.pool_volume.get(q).copied().unwrap_or(0.0).max(0.0)
                }
            })
            .collect();
        let own: Vec<f64> = (0..now.volume.len())
            .map(|c| {
                if self.handed_channel.get(c).copied().unwrap_or(false) {
                    return 0.0;
                }
                let (a, b) = self.own_part(c);
                match self.widths.get(c) {
                    Some(w) => w.max(self.min_seed_width).max(0.0) * (b - a),
                    None => now.volume[c] * (b - a) / self.topo.length[c].max(f64::EPSILON),
                }
            })
            .collect();
        let total: f64 = own.iter().sum::<f64>() + pools.iter().sum::<f64>();
        let mut r = self.rng.uniform() * total;
        for (c, v) in own.iter().enumerate() {
            if r < *v {
                let (a, b) = self.own_part(c);
                return Place::Channel(c, a + self.rng.uniform() * (b - a));
            }
            r -= v;
        }
        for (q, v) in pools.iter().enumerate() {
            if r < *v {
                return Place::Pool(q);
            }
            r -= v;
        }
        Place::Channel(0, 0.0)
    }

    /// Seed `n` flow particles through the network, more where there is more water.
    pub fn seed_flow_particles(&mut self, n: usize) {
        self.flow = FlowParticles::default();
        let Some(now) = self.particle_conditions(self.time) else {
            return;
        };
        let places = (0..n).map(|_| self.random_place(&now)).collect();
        self.flow.seed(places, &mut self.rng);
        self.handed_particles.clear();
        self.pool_at.clear();
        self.drift_in_pools(0.0);
    }

    /// Flow particles: channel index and position along it (0 at its upstream junction, 1 downstream).
    #[must_use]
    pub fn particles(&self) -> (Vec<u32>, Vec<f32>) {
        self.places(self.flow.places.iter().copied())
    }

    /// Parcels in the water: channel index (pools follow the channels: channels + pool) and position.
    #[must_use]
    pub fn parcels(&self) -> (Vec<u32>, Vec<f32>) {
        let live = self
            .parcels
            .iter()
            .filter(|p| p.born <= self.time)
            .map(|p| p.place);
        self.places(live)
    }

    fn places(&self, it: impl Iterator<Item = Place>) -> (Vec<u32>, Vec<f32>) {
        let (mut el, mut fr) = (Vec::new(), Vec::new());
        let n = self.topo.up.len();
        for p in it {
            #[allow(clippy::cast_possible_truncation)]
            match p {
                Place::Channel(c, x) => {
                    el.push(c as u32);
                    fr.push((x / self.topo.length[c]).clamp(0.0, 1.0) as f32);
                }
                Place::Pool(q) => {
                    el.push((n + q) as u32);
                    fr.push(0.5);
                }
                Place::Gone(_) => {}
            }
        }
        (el, fr)
    }

    /// Concentration where each parcel in the water is, kg/m³: the mass in its
    /// channel (or pool) over the water there, in the order of `parcels()`.
    #[must_use]
    pub fn parcel_concentration(&self) -> Vec<f64> {
        let Some(now) = self.conditions(self.time) else {
            return Vec::new();
        };
        let n = self.topo.up.len();
        let mut mass = vec![0.0; n + self.topo.pools];
        let live: Vec<&Parcel> = self
            .parcels
            .iter()
            .filter(|p| p.born <= self.time)
            .collect();
        for p in &live {
            match p.place {
                Place::Channel(c, _) => mass[c] += p.mass,
                Place::Pool(q) => mass[n + q] += p.mass,
                Place::Gone(_) => {}
            }
        }
        live.iter()
            .filter_map(|p| match p.place {
                Place::Channel(c, _) => Some(if now.volume[c] > 0.0 {
                    mass[c] / now.volume[c]
                } else {
                    0.0
                }),
                Place::Pool(q) => Some(if now.pool_volume[q] > 0.0 {
                    mass[n + q] / now.pool_volume[q]
                } else {
                    0.0
                }),
                Place::Gone(_) => None,
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use fdmr_format::{Encoding, Field, FieldFile};

    fn file(t0: i64, steps: u32, elements: u32, fields: Vec<(&str, Vec<f32>)>) -> Vec<u8> {
        let f = FieldFile {
            reach: "net".into(),
            dims: 1,
            elements,
            layers: 1,
            steps,
            t0,
            step_s: 900,
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

    /// Flow per channel, held for every step; area 10 everywhere.
    fn channels(steps: u32, q: &[f32]) -> Vec<u8> {
        let rep = |v: &[f32]| {
            v.iter()
                .copied()
                .cycle()
                .take(v.len() * steps as usize)
                .collect::<Vec<_>>()
        };
        file(
            0,
            steps,
            q.len() as u32,
            vec![
                ("flow_up", rep(q)),
                ("flow_down", rep(q)),
                ("area", rep(&vec![10.0; q.len()])),
            ],
        )
    }

    #[test]
    fn velocity_is_flow_over_area_and_days_join_across_midnight() {
        let topo = Topology {
            up: vec![0, 1],
            down: vec![1, 2],
            length: vec![100.0; 2],
            nodes: 3,
            sea: vec![],
            pools: 0,
            link_pool: vec![],
            link_node: vec![],
        };
        let mut t = NetworkTracer::new(topo, 1350.0, 1, 0.0);
        t.load_channels(&file(
            1800,
            1,
            2,
            vec![
                ("flow_up", vec![50.0; 2]),
                ("flow_down", vec![50.0; 2]),
                ("area", vec![10.0; 2]),
            ],
        ))
        .unwrap();
        t.load_channels(&file(
            0,
            2,
            2,
            vec![
                ("flow_up", vec![10.0, -20.0, 30.0, 30.0]),
                ("flow_down", vec![10.0, -20.0, 30.0, 30.0]),
                ("area", vec![10.0; 4]),
            ],
        ))
        .unwrap();
        // 1350 s is halfway from the first day's last step (900 s: 3 m/s) to the next day's first (5 m/s).
        let v = t.velocities();
        assert!((v[0] - 4.0).abs() < 1e-6, "{v:?}");
    }

    /// A junction where one channel (0) splits into two (1, 2), 3 to 1.
    fn split() -> NetworkTracer {
        let topo = Topology {
            up: vec![0, 1, 1],
            down: vec![1, 2, 3],
            length: vec![1000.0, 1000.0, 1000.0],
            nodes: 4,
            sea: vec![],
            pools: 0,
            link_pool: vec![],
            link_node: vec![],
        };
        let mut t = NetworkTracer::new(topo, 0.0, 7, 0.0);
        t.load_channels(&channels(400, &[40.0, 30.0, 10.0]))
            .unwrap();
        t
    }

    #[test]
    fn a_junction_shares_parcels_in_proportion_to_flow() {
        let mut t = split();
        t.add_release(NetRelease {
            at: Place::Channel(0, 0.0),
            start: 0.0,
            duration: 0.0,
            mass: 1.0,
            half_life: None,
            parcels: 4000,
        });
        t.step_to(600.0); // 4 m/s over 1 km: past the junction, not yet at the ends
        let mut n = [0usize; 3];
        for p in &t.parcels {
            if let Place::Channel(c, _) = p.place {
                n[c] += 1;
            }
        }
        assert_eq!(n[0], 0, "all have passed the junction");
        let share = n[1] as f64 / (n[1] + n[2]) as f64;
        assert!((share - 0.75).abs() < 0.03, "share {share}");
        assert!((t.balance().in_water - 1.0).abs() < 1e-9);
    }

    #[test]
    fn channels_another_reach_tracks_take_their_share_as_handed_on() {
        // As the split above, but channel 2 (a quarter of the flow) is tracked by
        // another reach: its share is passed on at the junction, with its mass.
        let mut t = split();
        t.set_handoff(&[2]);
        t.add_release(NetRelease {
            at: Place::Channel(0, 0.0),
            start: 0.0,
            duration: 0.0,
            mass: 1.0,
            half_life: None,
            parcels: 4000,
        });
        t.step_to(600.0);
        let b = t.balance();
        assert!((b.handed_on - 0.25).abs() < 0.03, "{b:?}");
        assert!((b.in_water + b.handed_on - 1.0).abs() < 1e-9);
        let handed = t.take_handed();
        let mass: f64 = handed.iter().map(|h| h.mass).sum();
        assert!((mass - b.handed_on).abs() < 1e-12);
        let junction = t.topo.down[0];
        assert!(handed
            .iter()
            .all(|h| h.via == Via::Junction(junction) && h.t > 0.0 && h.t <= 600.0));
        assert!(t.take_handed().is_empty(), "each parcel is handed on once");
        assert!(t
            .parcels
            .iter()
            .all(|p| !matches!(p.place, Place::Channel(2, _))));
    }

    #[test]
    fn a_parcel_handed_back_enters_the_networks_own_channel() {
        let mut t = split();
        t.set_handoff(&[2]);
        t.step_to(60.0);
        let junction = t.topo.down[0];
        t.take_parcel(junction, 0.5, None);
        assert_eq!(t.parcels.len(), 1);
        // Water leaves the junction only by channels 1 and 2; 2 is the other reach's.
        assert!(matches!(t.parcels[0].place, Place::Channel(1, x) if x == 0.0));
        let b = t.balance();
        assert!((b.released - 0.5).abs() < 1e-12 && (b.in_water - 0.5).abs() < 1e-12);
        t.step_to(120.0);
        assert!((t.balance().released - 0.5).abs() < 1e-12, "counted once");
    }

    #[test]
    fn exports_and_the_sea_take_parcels_and_the_mass_balance_holds() {
        // Channel 0 from junction 0 to junction 1, where pumps take a quarter and
        // the rest goes to sea.
        let topo = Topology {
            up: vec![0],
            down: vec![1],
            length: vec![500.0],
            nodes: 2,
            sea: vec![1],
            pools: 0,
            link_pool: vec![],
            link_node: vec![],
        };
        let mut t = NetworkTracer::new(topo, 0.0, 3, 0.0);
        t.load_channels(&channels(400, &[40.0])).unwrap();
        let rep = |v: Vec<f32>| {
            v.iter()
                .copied()
                .cycle()
                .take(v.len() * 400)
                .collect::<Vec<_>>()
        };
        t.load_nodes(&file(
            0,
            400,
            2,
            vec![
                ("export", rep(vec![0.0, 10.0])),
                ("withdrawal", rep(vec![0.0, 0.0])),
                ("volume", rep(vec![f32::NAN; 2])),
            ],
        ))
        .unwrap();
        t.add_release(NetRelease {
            at: Place::Channel(0, 0.0),
            start: 0.0,
            duration: 0.0,
            mass: 2.0,
            half_life: Some(3600.0),
            parcels: 4000,
        });
        t.step_to(1200.0);
        let b = t.balance();
        assert!(b.in_water < 1e-9, "all gone");
        assert!(
            (b.exported / (b.exported + b.to_sea) - 0.25).abs() < 0.03,
            "{b:?}"
        );
        assert!(
            (b.exported + b.to_sea + b.decayed - 2.0).abs() < 1e-9,
            "{b:?}"
        );
        assert!(b.decayed > 0.0);
    }

    #[test]
    fn a_pool_holds_water_and_lets_it_out_at_the_flow_rate() {
        // Junction 0 - channel 0 - junction 1, joined to pool 0 by link 0. The pool
        // (1e5 m³) is drained by pumps at 10 m³/s: half-emptied in about 1.9 h.
        let topo = Topology {
            up: vec![0],
            down: vec![1],
            length: vec![1000.0],
            nodes: 2,
            sea: vec![],
            pools: 1,
            link_pool: vec![0],
            link_node: vec![1],
        };
        let mut t = NetworkTracer::new(topo, 0.0, 11, 0.0);
        t.load_channels(&channels(800, &[0.0])).unwrap();
        let rep = |v: Vec<f32>| {
            v.iter()
                .copied()
                .cycle()
                .take(v.len() * 800)
                .collect::<Vec<_>>()
        };
        t.load_nodes(&file(
            0,
            800,
            3,
            vec![
                ("export", rep(vec![0.0, 0.0, 10.0])),
                ("withdrawal", rep(vec![0.0; 3])),
                ("volume", rep(vec![f32::NAN, f32::NAN, 1e5])),
            ],
        ))
        .unwrap();
        t.load_links(&file(0, 800, 1, vec![("flow", rep(vec![0.0]))]))
            .unwrap();
        t.add_release(NetRelease {
            at: Place::Pool(0),
            start: 0.0,
            duration: 0.0,
            mass: 1.0,
            half_life: None,
            parcels: 4000,
        });
        t.step_to(10_000.0 * std::f64::consts::LN_2); // the pool's half-emptying time
        let left = t.balance().in_water;
        assert!((left - 0.5).abs() < 0.04, "left {left}");
    }

    #[test]
    fn flow_particles_keep_their_number() {
        let mut t = split();
        t.seed_flow_particles(300);
        t.step_to(3600.0);
        assert_eq!(t.particles().0.len(), 300);
    }

    #[test]
    fn flow_particles_live_a_while_then_start_again() {
        let mut t = split();
        t.seed_flow_particles(300);
        // Seeded part way through their lives.
        assert!(t
            .flow
            .ages
            .iter()
            .zip(&t.flow.lives)
            .all(|(a, l)| *a >= 0.0 && a <= l));
        t.step_to(10.0 * 3600.0);
        // Ten hours on, every one has started again at least once (lives are 3 to 9 h).
        assert!(
            t.flow.ages.iter().all(|a| *a < crate::PARTICLE_LIFE_S.1),
            "{:?}",
            t.flow.ages.iter().copied().fold(0.0, f64::max)
        );
        assert_eq!(t.particles().0.len(), 300);
    }

    #[test]
    fn a_cut_passes_things_on_where_the_other_reach_begins() {
        // Channel 1 (1000 m) is the other reach's beyond its middle.
        let mut t = split();
        t.set_cuts(&[(1, 0.5, true)]);
        t.add_release(NetRelease {
            at: Place::Channel(0, 0.0),
            start: 0.0,
            duration: 0.0,
            mass: 1.0,
            half_life: None,
            parcels: 2000,
        });
        t.step_to(600.0);
        // Nothing in channel 1 past its cut; what went that way was passed on there.
        for p in &t.parcels {
            if let Place::Channel(1, x) = p.place {
                assert!(x < 500.0, "{x}");
            }
        }
        let handed = t.take_handed();
        assert!(!handed.is_empty() && handed.iter().all(|h| h.via == Via::Cut(0)));
        // Coming back: in at the cut, moving out of the other reach's part.
        t.take_parcel_at_cut(0, 1.0, None);
        assert!(
            matches!(t.parcels.last().unwrap().place, Place::Channel(1, x) if x < 500.0 && x > 498.0)
        );
        // Flow particles start only in the network's own water.
        t.seed_flow_particles(500);
        for p in &t.flow.places {
            if let Place::Channel(1, x) = *p {
                assert!(x <= 500.0);
            }
        }
        assert!(t.take_particle_at_cut(0));
    }

    #[test]
    fn an_export_with_an_outlet_passes_things_on_instead_of_removing_them() {
        // Channel 0 from junction 0 to junction 1, where pumps take a quarter
        // and carry it on into a canal (outlet 0); the rest goes on to sea.
        let mut t = NetworkTracer::new(
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
            0.0,
            3,
            0.0,
        );
        t.load_channels(&channels(400, &[40.0])).unwrap();
        let rep = |v: Vec<f32>| {
            v.iter()
                .copied()
                .cycle()
                .take(v.len() * 400)
                .collect::<Vec<_>>()
        };
        t.load_nodes(&file(
            0,
            400,
            2,
            vec![
                ("export", rep(vec![0.0, 10.0])),
                ("withdrawal", rep(vec![0.0, 0.0])),
                ("volume", rep(vec![f32::NAN; 2])),
            ],
        ))
        .unwrap();
        t.set_outlets(&[1]);
        t.add_release(NetRelease {
            at: Place::Channel(0, 0.0),
            start: 0.0,
            duration: 0.0,
            mass: 1.0,
            half_life: None,
            parcels: 4000,
        });
        t.seed_flow_particles(500);
        t.step_to(1200.0);
        let b = t.balance();
        assert_eq!(b.exported.to_bits(), 0.0_f64.to_bits(), "{b:?}");
        assert!((b.handed_on - 0.25).abs() < 0.03, "{b:?}");
        let handed = t.take_handed();
        assert!(!handed.is_empty() && handed.iter().all(|h| h.via == Via::Outlet(0)));
        assert!(t.take_handed_particles().contains(&Via::Outlet(0)));
    }

    #[test]
    fn with_widths_flow_particles_start_evenly_over_the_surface() {
        // Three channels of the same length; channel 1 nine times as wide as the others.
        let mut t = split();
        t.set_widths(&[10.0, 90.0, 10.0]);
        t.seed_flow_particles(4000);
        let (el, _) = t.particles();
        let n = |c: u32| el.iter().filter(|&&e| e == c).count() as f64;
        let per_m = |c: u32| n(c) / (t.topo.length[c as usize] * [10.0, 90.0, 10.0][c as usize]);
        assert!(
            (per_m(1) / per_m(0) - 1.0).abs() < 0.2,
            "{} {}",
            per_m(0),
            per_m(1)
        );
    }

    #[test]
    fn the_seeding_floor_spreads_particles_but_settling_keeps_the_true_width() {
        // A floor of 90 m makes the 10 m channels count as wide as the 90 m one
        // for where particles start (#50) ...
        let mut t = split();
        t.set_widths(&[10.0, 90.0, 10.0]);
        t.set_min_seed_width(90.0);
        t.seed_flow_particles(6000);
        let (el, _) = t.particles();
        let n = |c: u32| el.iter().filter(|&&e| e == c).count() as f64;
        let per_m = |c: u32| n(c) / t.topo.length[c as usize];
        assert!(
            (per_m(0) / per_m(1) - 1.0).abs() < 0.2,
            "{} {}",
            per_m(0),
            per_m(1)
        );
        // ... while the depth that settling divides by is still area over the true width.
        let mut s = with_sediment(10.0, 1.0, 0.0, 1e-4);
        s.set_min_seed_width(100.0);
        let now = s.conditions(100.0).unwrap();
        let sed = s.sediment_now(100.0, &now).unwrap();
        let depth = now.volume[0] / s.topo.length[0] / 10.0;
        assert!(
            (sed.settle[0] - 1e-4 / depth).abs() < 1e-12,
            "{} {}",
            sed.settle[0],
            1e-4 / depth
        );
    }

    /// The split network, 1 m deep everywhere (area 10, width 10), with
    /// suspended sediment, settled mud and its erosion rate held everywhere.
    fn with_sediment(ssc: f32, bed: f32, erosion: f32, ws: f64) -> NetworkTracer {
        let mut t = split();
        t.set_widths(&[10.0, 10.0, 10.0]);
        let steps = 400u32;
        let all = |v: f32| vec![v; 3 * steps as usize];
        t.load_quality(&file(
            0,
            steps,
            3,
            vec![
                ("ssc", all(ssc)),
                ("bed_mud", all(bed)),
                ("erosion", all(erosion)),
            ],
        ))
        .unwrap();
        t.set_settling(ws);
        t
    }

    fn bound_release(t: &mut NetworkTracer, kd: f64) -> f64 {
        t.add_bound_release(
            NetRelease {
                at: Place::Channel(0, 10.0),
                start: 0.0,
                duration: 0.0,
                mass: 10.0,
                half_life: None,
                parcels: 400,
            },
            kd,
        )
    }

    #[test]
    fn a_bound_release_splits_by_the_partition_coefficient() {
        // kd c = 1e4 L/kg x 100 mg/L (1e-4 kg/L) = 1: half bound.
        let mut t = with_sediment(100.0, 0.0, 0.0, 0.0);
        let share = bound_release(&mut t, 1e4);
        assert!((share - 0.5).abs() < 1e-12, "{share}");
        t.step_to(1.0);
        let b = t.balance();
        assert!((b.in_water + b.on_bed - 10.0).abs() < 1e-9, "{b:?}");
        // No settling velocity: nothing settles.
        t.step_to(3600.0);
        assert!(t.balance().on_bed.abs() < 1e-12);
    }

    #[test]
    fn bound_parcels_settle_at_ws_over_depth() {
        // 1 m deep, ws 1e-3 m/s: after an hour 1 - e^-3.6 of the bound mass is down.
        let mut t = with_sediment(1e6, 0.0, 0.0, 1e-3);
        let share = bound_release(&mut t, 1e6); // nearly all bound
        t.step_to(3600.0);
        let b = t.balance();
        let want = 10.0 * share * (1.0 - (-3.6f64).exp());
        assert!((b.on_bed - want).abs() < 0.4, "{} {want}", b.on_bed);
        assert!((b.in_water + b.on_bed - 10.0).abs() < 1e-9);
    }

    #[test]
    fn settling_and_scouring_at_equal_rates_leave_half_on_the_bed() {
        // settle ws/h = 1e-3/s; scour erosion/bed = 1e-3/1 = 1e-3/s: in balance, half down.
        let mut t = with_sediment(1e6, 1.0, 1e-3, 1e-3);
        bound_release(&mut t, 1e6);
        t.step_to(36_000.0);
        let b = t.balance();
        assert!(
            (b.on_bed / (b.on_bed + b.in_water) - 0.5).abs() < 0.08,
            "{b:?}"
        );
    }

    /// Channel 0 (junction 0 to 1, 1000 m) and a pool of `area` m² linked to
    /// junction 1, with the pool's EC in the junction-and-pool file.
    fn with_pool(area: f64, link_flow: f32) -> NetworkTracer {
        let mut t = NetworkTracer::new(
            Topology {
                up: vec![0],
                down: vec![1],
                length: vec![1000.0],
                nodes: 2,
                sea: vec![],
                pools: 1,
                link_pool: vec![0],
                link_node: vec![1],
            },
            0.0,
            9,
            0.0,
        );
        t.load_channels(&channels(400, &[0.0])).unwrap();
        let rep = |v: Vec<f32>| {
            v.iter()
                .copied()
                .cycle()
                .take(v.len() * 400)
                .collect::<Vec<_>>()
        };
        t.load_nodes(&file(
            0,
            400,
            3,
            vec![
                ("export", rep(vec![0.0; 3])),
                ("withdrawal", rep(vec![0.0; 3])),
                ("volume", rep(vec![f32::NAN, f32::NAN, 2.0e6])),
                ("ec", rep(vec![f32::NAN, f32::NAN, 750.0])),
            ],
        ))
        .unwrap();
        t.load_links(&file(0, 400, 1, vec![("flow", rep(vec![link_flow]))]))
            .unwrap();
        t.set_widths(&[100.0]);
        t.set_pool_areas(&[area]);
        t
    }

    #[test]
    fn flow_particles_live_in_pools_by_their_surface_and_take_the_pools_value() {
        // The channel's surface is 100 m x 1000 m = 0.1 km2; the pool's 0.3 km2:
        // three in four particles start in the pool.
        let mut t = with_pool(300_000.0, 0.0);
        t.seed_flow_particles(4000);
        let (el, _) = t.particles();
        let in_pool = el.iter().filter(|&&e| e == 1).count();
        assert!((in_pool as f64 / 4000.0 - 0.75).abs() < 0.03, "{in_pool}");
        // With no water leaving, they stay in the pool through a step.
        t.step_to(600.0);
        let (el2, _) = t.particles();
        assert!(el2.iter().filter(|&&e| e == 1).count() + 100 >= in_pool);
        // The pool's EC from the junction-and-pool file; none for a field only channels have.
        let ec = t.particle_values("ec");
        assert!(el2
            .iter()
            .zip(&ec)
            .all(|(&e, &v)| (e == 1) == ((v - 750.0).abs() < 1e-3)));
        let flow = t.particle_values("flow_up");
        assert!(el2.iter().zip(&flow).all(|(&e, v)| e != 1 || v.is_nan()));
    }

    #[test]
    fn pool_particles_drift_over_its_water_and_never_onto_land() {
        // The pool's water: a 1 km square of points 50 m apart, with a 400 m
        // square of land cut out of its middle.
        let mut water = Vec::new();
        for i in 0..20 {
            for j in 0..20 {
                if !(6..14).contains(&i) || !(6..14).contains(&j) {
                    water.push((
                        f64::from(i) * 50.0 / 111_195.0,
                        f64::from(j) * 50.0 / 111_195.0,
                    ));
                }
            }
        }
        let mut t = with_pool(1.0e6, 0.0);
        t.set_pool_water(0, &water, 5.0);
        t.seed_flow_particles(400);
        let start = t.particle_pool_positions();
        let (el, _) = t.particles();
        assert!(el
            .iter()
            .zip(&start)
            .all(|(&e, p)| (e == 1) == p.0.is_finite()));
        let parcels_before = t.rng.uniform();
        t.step_to(6.0 * 3600.0);
        let end = t.particle_pool_positions();
        let (el, _) = t.particles();
        let on_land = |(lon, lat): (f64, f64)| {
            let (x, y) = (lon * 111_195.0, lat * 111_195.0);
            (330.0..620.0).contains(&x) && (330.0..620.0).contains(&y)
        };
        let mut moved = 0;
        for (i, &e) in el.iter().enumerate() {
            if e == 1 {
                assert!(end[i].0.is_finite() && !on_land(end[i]), "{:?}", end[i]);
                if start[i].0.is_finite() && (end[i].0 - start[i].0).abs() > 1e-6 {
                    moved += 1;
                }
            }
        }
        assert!(moved > 100, "{moved}");
        // The drift draws its own random numbers: the parcels' are untouched.
        let mut u = with_pool(1.0e6, 0.0);
        u.seed_flow_particles(400);
        assert_eq!(u.rng.uniform().to_bits(), parcels_before.to_bits());
    }

    #[test]
    fn without_pool_areas_no_flow_particle_starts_in_a_pool() {
        let mut t = with_pool(300_000.0, 0.0);
        t.set_pool_areas(&[]);
        t.seed_flow_particles(500);
        assert!(t.particles().0.iter().all(|&e| e == 0));
    }

    #[test]
    fn a_flow_particle_from_another_reach_replaces_the_oldest() {
        let mut t = split();
        t.seed_flow_particles(20);
        let i = crate::oldest(&t.flow.ages, &t.flow.lives).unwrap();
        assert!(t.take_particle(0));
        assert_eq!(t.flow.len(), 20);
        assert_eq!(t.flow.ages[i].to_bits(), 0.0_f64.to_bits());
        assert!(matches!(t.flow.places[i], Place::Channel(..)));
    }
}
