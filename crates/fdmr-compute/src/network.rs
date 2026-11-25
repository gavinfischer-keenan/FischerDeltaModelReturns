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

use fdmr_format::{FieldFile, FormatError};
use fdmr_math::{decay, parcel, rng::Rng};

use crate::MAX_DT;

/// Day files of one kind (channels, junctions or links).
#[derive(Debug, Default)]
struct Series {
    elements: usize,
    days: Vec<FieldFile>,
}

impl Series {
    fn load_day(&mut self, bytes: &[u8], needs: &[&str]) -> Result<(), FormatError> {
        let f = FieldFile::read_from(&mut &bytes[..])?;
        for name in needs {
            if f.field(name).is_none() {
                return Err(FormatError::Invalid(format!(
                    "network day file has no {name} field"
                )));
            }
        }
        if !self.days.is_empty() && f.elements as usize != self.elements {
            return Err(FormatError::Invalid(
                "day files disagree about the number of elements".into(),
            ));
        }
        self.elements = f.elements as usize;
        let at = self.days.partition_point(|d| d.t0 < f.t0);
        self.days.insert(at, f);
        Ok(())
    }

    /// The day and step at or before `t`, and how far to the next step (0..1).
    fn locate(&self, t: f64) -> Option<(usize, usize, f64)> {
        #[allow(clippy::cast_precision_loss)]
        let d = self.days.iter().rposition(|d| d.t0 as f64 <= t)?;
        let day = &self.days[d];
        #[allow(clippy::cast_precision_loss)]
        let pos = (t - day.t0 as f64) / f64::from(day.step_s);
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let i = pos.floor() as usize;
        if i >= day.steps as usize {
            return Some((d, day.steps as usize - 1, 0.0));
        }
        Some((d, i, pos - pos.floor()))
    }

    /// Step `i` of day `d`, or the next day's first step when `i` is one past the end.
    fn row(&self, d: usize, i: usize, name: &str) -> &[f32] {
        let (day, i) = if i >= self.days[d].steps as usize && d + 1 < self.days.len() {
            (&self.days[d + 1], 0)
        } else {
            (&self.days[d], i.min(self.days[d].steps as usize - 1))
        };
        let v = &day.field(name).expect("checked when loaded").values;
        &v[i * self.elements..(i + 1) * self.elements]
    }

    /// A field at `t` for every element, linear between steps; `None` before the
    /// first day. A value missing at either step (NaN) stays missing.
    fn at(&self, t: f64, name: &str) -> Option<Vec<f64>> {
        let (d, i, f) = self.locate(t)?;
        let (a, b) = (self.row(d, i, name), self.row(d, i + 1, name));
        Some(
            a.iter()
                .zip(b)
                .map(|(x, y)| f64::from(*x) * (1.0 - f) + f64::from(*y) * f)
                .collect(),
        )
    }
}

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
}

/// [released, in the water, exported, diverted, out to sea, broken down], kg.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct NetBalance {
    pub released: f64,
    pub in_water: f64,
    pub exported: f64,
    pub diverted: f64,
    pub to_sea: f64,
    pub decayed: f64,
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
    nodes: Series,
    links: Series,
    dispersion: f64,
    time: f64,
    rng: Rng,
    parcels: Vec<Parcel>,
    particles: Vec<Place>,
    balance: NetBalance,
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
            links: Series::default(),
            dispersion,
            time: start_time,
            rng: Rng::new(seed),
            parcels: Vec::new(),
            particles: Vec::new(),
            balance: NetBalance::default(),
        }
    }

    /// Load one day's channel file (`flow_up`, `flow_down`, `area`).
    ///
    /// # Errors
    /// If the file is not a network channel file for this topology.
    pub fn load_channels(&mut self, bytes: &[u8]) -> Result<(), FormatError> {
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
        self.nodes
            .load_day(bytes, &["export", "withdrawal", "volume"])?;
        if self.nodes.elements != self.topo.nodes + self.topo.pools {
            return Err(FormatError::Invalid(
                "junction file does not match the network".into(),
            ));
        }
        Ok(())
    }

    /// Load one day's link file (`flow`).
    ///
    /// # Errors
    /// If the file is not a link file for this topology.
    pub fn load_links(&mut self, bytes: &[u8]) -> Result<(), FormatError> {
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
        b.in_water = self
            .parcels
            .iter()
            .filter(|p| p.born <= self.time && !matches!(p.place, Place::Gone(_)))
            .map(|p| p.mass)
            .sum();
        b
    }

    /// Add a release: its parcels start at its place, spread evenly over its duration.
    pub fn add_release(&mut self, r: NetRelease) {
        let n = r.parcels.max(1);
        #[allow(clippy::cast_precision_loss)]
        let each = r.mass / n as f64;
        for i in 0..n {
            #[allow(clippy::cast_precision_loss)]
            let born = r.start + r.duration * (i as f64 + 0.5) / n as f64;
            self.parcels.push(Parcel {
                place: r.at,
                mass: each,
                born,
                half_life: r.half_life,
            });
        }
    }

    pub fn clear_releases(&mut self) {
        self.parcels.clear();
        self.balance = NetBalance::default();
    }

    fn conditions(&self, t: f64) -> Option<Now> {
        let (q_up, q_down, area) = (
            self.channels.at(t, "flow_up")?,
            self.channels.at(t, "flow_down")?,
            self.channels.at(t, "area")?,
        );
        let n = self.topo.nodes;
        let (export, withdrawal, volume) = match (
            self.nodes.at(t, "export"),
            self.nodes.at(t, "withdrawal"),
            self.nodes.at(t, "volume"),
        ) {
            (Some(e), Some(w), Some(v)) => (e, w, v),
            _ => (
                vec![0.0; n + self.topo.pools],
                vec![0.0; n + self.topo.pools],
                vec![f64::NAN; n + self.topo.pools],
            ),
        };
        let link_flow = self
            .links
            .at(t, "flow")
            .unwrap_or_else(|| vec![0.0; self.topo.link_pool.len()]);
        let u = |q: &Vec<f64>| -> Vec<f64> {
            q.iter()
                .zip(&area)
                .map(|(q, a)| if *a > 0.0 { q / a } else { 0.0 })
                .collect()
        };
        Some(Now {
            u_up: u(&q_up),
            u_down: u(&q_down),
            volume: area
                .iter()
                .zip(&self.topo.length)
                .map(|(a, l)| a * l)
                .collect(),
            q_up,
            q_down,
            export,
            withdrawal,
            pool_volume: volume[n..]
                .iter()
                .map(|v| if v.is_finite() { *v } else { 0.0 })
                .collect(),
            link_flow,
        })
    }

    /// Where something arriving at junction `n` goes next, chosen in proportion
    /// to the flow leaving each way, using `u`, a uniform random number in
    /// [0, 1). `None` if no water is leaving.
    fn route(&self, now: &Now, n: usize, u: f64) -> Option<Place> {
        let mut ways: Vec<(Place, f64)> = Vec::new();
        let mut into_node = 0.0;
        for &(c, end) in &self.at_node[n] {
            // Flow leaving the junction into the channel.
            let q = match end {
                End::Up => now.q_up[c],
                End::Down => -now.q_down[c],
            };
            if q > 0.0 {
                let x = match end {
                    End::Up => 0.0,
                    End::Down => self.topo.length[c],
                };
                ways.push((Place::Channel(c, x), q));
            } else {
                into_node -= q;
            }
        }
        for &l in &self.links_at_node[n] {
            let into_pool = -now.link_flow[l];
            if into_pool > 0.0 {
                ways.push((Place::Pool(self.topo.link_pool[l]), into_pool));
            } else {
                into_node -= into_pool;
            }
        }
        if now.export[n] > 0.0 {
            ways.push((Place::Gone(Fate::Exported), now.export[n]));
        }
        if now.withdrawal[n] > 0.0 {
            ways.push((Place::Gone(Fate::Diverted), now.withdrawal[n]));
        }
        if self.is_sea[n] {
            // At the sea boundary, whatever arrives and is not carried on goes out.
            let leaving: f64 = ways.iter().map(|w| w.1).sum();
            let out = into_node - leaving;
            if out > 0.0 {
                ways.push((Place::Gone(Fate::ToSea), out));
            }
        }
        pick(u, &ways)
    }

    /// Move one thing for `dt` s under `now`. `k` is its dispersion (0 for flow particles).
    fn advance(&mut self, mut place: Place, dt: f64, now: &Now, k: f64) -> Place {
        let mut left = dt;
        // A few hops at most per step: channels are far longer than a step's travel.
        for _ in 0..8 {
            match place {
                Place::Gone(_) => return place,
                Place::Pool(p) => {
                    let mut ways: Vec<(PoolExit, f64)> = Vec::new();
                    for &l in &self.links_of_pool[p] {
                        if now.link_flow[l] > 0.0 {
                            ways.push((PoolExit::Link(self.topo.link_node[l]), now.link_flow[l]));
                        }
                    }
                    let i = self.topo.nodes + p;
                    if now.export[i] > 0.0 {
                        ways.push((PoolExit::Gone(Fate::Exported), now.export[i]));
                    }
                    if now.withdrawal[i] > 0.0 {
                        ways.push((PoolExit::Gone(Fate::Diverted), now.withdrawal[i]));
                    }
                    let out: f64 = ways.iter().map(|w| w.1).sum();
                    let v = now.pool_volume[p];
                    // Well mixed: in `left` s, a share out x left / volume of the pool's water leaves.
                    if out <= 0.0 || v <= 0.0 || self.rng.uniform() >= (out * left / v).min(1.0) {
                        return place;
                    }
                    let (u1, u2) = (self.rng.uniform(), self.rng.uniform());
                    match pick(u1, &ways) {
                        // Leaving by a link: arrive at its junction and carry on from there.
                        Some(PoolExit::Link(node)) => match self.route(now, node, u2) {
                            Some(Place::Pool(q)) if q == p => return place,
                            Some(next) => {
                                place = next;
                                left = 0.0;
                            }
                            None => return place,
                        },
                        Some(PoolExit::Gone(fate)) => return Place::Gone(fate),
                        None => return place,
                    }
                }
                Place::Channel(c, x) => {
                    if left <= 0.0 {
                        return place;
                    }
                    let len = self.topo.length[c];
                    let f = (x / len).clamp(0.0, 1.0);
                    let u = now.u_up[c] * (1.0 - f) + now.u_down[c] * f;
                    let nx = parcel::step_along_channel(x, u, k, left, &mut self.rng);
                    if (0.0..=len).contains(&nx) {
                        return Place::Channel(c, nx);
                    }
                    // Only the flow carries anything through a junction. A crossing
                    // made by the random spreading step alone is turned back into
                    // the channel: otherwise a parcel jittering about a junction
                    // would be offered its diversions again and again, far beyond
                    // their share of the flow (seen on real data, 25 Nov 2026).
                    if (0.0..=len).contains(&(x + u * left)) {
                        let back = if nx < 0.0 { -nx } else { 2.0 * len - nx };
                        return Place::Channel(c, back.clamp(0.0, len));
                    }
                    // Reached a junction: the time left is the share of the move beyond it.
                    let (node, over) = if nx < 0.0 {
                        (self.topo.up[c], -nx)
                    } else {
                        (self.topo.down[c], nx - len)
                    };
                    let moved = (nx - x).abs().max(f64::EPSILON);
                    left *= (over / moved).clamp(0.0, 1.0);
                    let u = self.rng.uniform();
                    match self.route(now, node, u) {
                        Some(next) => place = next,
                        // Nothing leaving: wait at the end of the channel.
                        None => return Place::Channel(c, if nx < 0.0 { 0.0 } else { len }),
                    }
                }
            }
        }
        place
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
            for i in 0..self.parcels.len() {
                let p = self.parcels[i];
                if p.born > self.time + dt || matches!(p.place, Place::Gone(_)) {
                    continue;
                }
                let step = (self.time + dt - p.born.max(self.time)).max(0.0);
                let keep = decay::remaining_fraction(step, p.half_life);
                self.balance.decayed += p.mass * (1.0 - keep);
                let mass = p.mass * keep;
                let place = self.advance(p.place, step, &now, k);
                if let Place::Gone(fate) = place {
                    match fate {
                        Fate::Exported => self.balance.exported += mass,
                        Fate::Diverted => self.balance.diverted += mass,
                        Fate::ToSea => self.balance.to_sea += mass,
                    }
                }
                if p.born >= self.time && p.born < self.time + dt {
                    self.balance.released += p.mass;
                }
                self.parcels[i] = Parcel { place, mass, ..p };
            }
            self.move_particles(dt, &now);
            self.time += dt;
        }
    }

    /// Move the flow particles `dt` s under the conditions at the current time,
    /// without advancing it (for "now": the water keeps moving, the clock does not).
    pub fn drift_frozen(&mut self, dt: f64) {
        let mut left = dt;
        while left > 0.0 {
            let step = left.min(MAX_DT);
            if let Some(now) = self.conditions(self.time) {
                self.move_particles(step, &now);
            }
            left -= step;
        }
    }

    fn move_particles(&mut self, dt: f64, now: &Now) {
        for i in 0..self.particles.len() {
            let p = self.advance(self.particles[i], dt, now, 0.0);
            self.particles[i] = match p {
                Place::Channel(..) => p,
                // A particle that leaves, or settles in a pool, starts again elsewhere.
                _ => self.random_place(now),
            };
        }
    }

    fn random_place(&mut self, now: &Now) -> Place {
        let total: f64 = now.volume.iter().sum();
        let mut r = self.rng.uniform() * total;
        for (c, v) in now.volume.iter().enumerate() {
            if r < *v {
                return Place::Channel(c, self.rng.uniform() * self.topo.length[c]);
            }
            r -= v;
        }
        Place::Channel(0, 0.0)
    }

    /// Each channel's mean velocity now, m/s, positive downstream; empty before the first day.
    #[must_use]
    pub fn velocities(&self) -> Vec<f32> {
        let Some(now) = self.conditions(self.time) else {
            return Vec::new();
        };
        #[allow(clippy::cast_possible_truncation)]
        now.u_up
            .iter()
            .zip(&now.u_down)
            .map(|(a, b)| f64::midpoint(*a, *b) as f32)
            .collect()
    }

    /// Seed `n` flow particles through the network, more where there is more water.
    pub fn seed_flow_particles(&mut self, n: usize) {
        self.particles.clear();
        let Some(now) = self.conditions(self.time) else {
            return;
        };
        for _ in 0..n {
            let p = self.random_place(&now);
            self.particles.push(p);
        }
    }

    /// Flow particles: channel index and position along it (0 at its upstream junction, 1 downstream).
    #[must_use]
    pub fn particles(&self) -> (Vec<u32>, Vec<f32>) {
        self.places(self.particles.iter().copied())
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

/// How something leaves a pool.
#[derive(Debug, Clone, Copy)]
enum PoolExit {
    /// Through a link, to this junction.
    Link(usize),
    Gone(Fate),
}

/// One of the ways, in proportion to their weights, using `u` in [0, 1).
fn pick<T: Copy>(u: f64, ways: &[(T, f64)]) -> Option<T> {
    let total: f64 = ways.iter().map(|w| w.1).sum();
    if total <= 0.0 {
        return None;
    }
    let mut r = u * total;
    for (p, w) in ways {
        if r < *w {
            return Some(*p);
        }
        r -= w;
    }
    ways.last().map(|w| w.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use fdmr_format::{Encoding, Field};

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
}
