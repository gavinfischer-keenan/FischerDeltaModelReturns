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
use fdmr_math::{decay, parcel, rng::Rng};

use crate::{series::Series, MAX_DT};

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

/// Bound parcels' rates per channel at one moment, 1/s.
struct SedNow {
    settle: Vec<f64>,
    resuspend: Vec<f64>,
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
    net: Option<[Series; 3]>,
    net_on: bool,
    dispersion: f64,
    time: f64,
    rng: Rng,
    parcels: Vec<Parcel>,
    particles: Vec<Place>,
    /// Each flow particle's age and life, s (`crate::PARTICLE_LIFE_S`).
    ages: Vec<f64>,
    lives: Vec<f64>,
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
            net: None,
            net_on: false,
            dispersion,
            time: start_time,
            rng: Rng::new(seed),
            parcels: Vec::new(),
            particles: Vec::new(),
            ages: Vec::new(),
            lives: Vec::new(),
            handed_particles: Vec::new(),
            balance: NetBalance::default(),
            handed_channel: Vec::new(),
            cut: Vec::new(),
            cuts: Vec::new(),
            widths: Vec::new(),
            min_seed_width: 0.0,
            outlet: Vec::new(),
            handed: Vec::new(),
            settling: None,
        }
    }

    /// Channels another reach tracks: a parcel about to enter one is passed on
    /// instead, at the junction it reached (`take_handed`). Their flow still
    /// carries water away from the junction, so the share leaving that way is
    /// the share passed on (specification 7.4: each parcel belongs to one
    /// reach at a time).
    pub fn set_handoff(&mut self, channels: &[usize]) {
        self.handed_channel = vec![false; self.topo.up.len()];
        for &c in channels {
            if c < self.handed_channel.len() {
                self.handed_channel[c] = true;
            }
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

    /// Exports that carry on into another reach (the export canals): each a
    /// junction, or a pool given as the number of junctions plus its index.
    /// What its pumps take is passed on through that outlet (`take_handed`,
    /// with the outlet's number, its index here) instead of leaving the
    /// model. Other exports still take things out of the model.
    pub fn set_outlets(&mut self, at: &[usize]) {
        self.outlet = vec![None; self.topo.nodes + self.topo.pools];
        for (k, &i) in at.iter().enumerate() {
            if let Some(o) = self.outlet.get_mut(i) {
                *o = Some(k);
            }
        }
    }

    /// What becomes of what the pumps take at junction or pool `i`.
    fn exported(&self, i: usize) -> Fate {
        match self.outlet.get(i).copied().flatten() {
            Some(k) => Fate::Outlet(k),
            None => Fate::Exported,
        }
    }

    /// Places where another reach takes over part way along a channel (the
    /// join's line crossing it): (channel, place along it from 0 at its
    /// upstream end to 1, whether the other reach's part is the downstream
    /// one). A parcel or flow particle carried past one toward the other
    /// reach is passed on there (`take_handed`, with the cut's number, its
    /// index here); one coming back enters there, moving out. The other
    /// reach's part draws and holds nothing here.
    pub fn set_cuts(&mut self, cuts: &[(usize, f64, bool)]) {
        self.cut = vec![None; self.topo.up.len()];
        self.cuts = Vec::new();
        for &(c, at, down) in cuts {
            if c < self.cut.len() {
                self.cut[c] = Some((at.clamp(0.0, 1.0), down, self.cuts.len()));
                self.cuts.push((c, at.clamp(0.0, 1.0), down));
            }
        }
    }

    /// The network's own part of a channel, m from its upstream end: (from, to).
    fn own_part(&self, c: usize) -> (f64, f64) {
        let len = self.topo.length[c];
        match self.cut.get(c).copied().flatten() {
            Some((at, true, _)) => (0.0, at * len),
            Some((at, false, _)) => (at * len, len),
            None => (0.0, len),
        }
    }

    /// Where something coming back through cut `k` enters: just inside the
    /// network's own part, at the cut.
    fn at_cut(&self, k: usize) -> Option<Place> {
        let &(c, at, down) = self.cuts.get(k)?;
        let len = self.topo.length[c];
        let s = at * len;
        let in_by = (0.001 * len).min(1.0);
        Some(Place::Channel(
            c,
            if down {
                (s - in_by).max(0.0)
            } else {
                (s + in_by).min(len)
            },
        ))
    }

    /// Take in a parcel coming back from another reach through cut `k`, now.
    pub fn take_parcel_at_cut(&mut self, k: usize, mass: f64, half_life: Option<f64>) {
        if let Some(place) = self.at_cut(k) {
            self.balance.released += mass;
            self.parcels.push(Parcel {
                place,
                mass,
                born: self.time - 1e-6,
                half_life,
                bound: false,
                on_bed: false,
            });
        }
    }

    /// Take in a flow particle from another reach through cut `k`, in place of
    /// the one furthest through its life.
    pub fn take_particle_at_cut(&mut self, k: usize) -> bool {
        let Some(place) = self.at_cut(k) else {
            return false;
        };
        let Some(i) = crate::oldest(&self.ages, &self.lives) else {
            return false;
        };
        self.particles[i] = place;
        self.ages[i] = 0.0;
        self.lives[i] = crate::particle_life(&mut self.rng);
        true
    }

    /// Parcels passed to another reach since the last call.
    pub fn take_handed(&mut self) -> Vec<Handed> {
        std::mem::take(&mut self.handed)
    }

    /// Take in a parcel handed back from another reach, arriving at junction
    /// `node` now. It goes into one of the network's own channels there, in
    /// proportion to the flow leaving the junction into each (or, if none is
    /// leaving that way, into any of them, evenly), at the junction's end.
    /// It counts as released at once, and moves from the next step.
    pub fn take_parcel(&mut self, node: usize, mass: f64, half_life: Option<f64>) {
        if let Some(place) = self.entry(node) {
            self.balance.released += mass;
            self.parcels.push(Parcel {
                place,
                mass,
                // Just before now: in the water now, and not counted again by the step.
                born: self.time - 1e-6,
                half_life,
                bound: false,
                on_bed: false,
            });
        }
    }

    /// Take in a flow particle arriving from another reach at junction `node`,
    /// as a parcel would come (`take_parcel`): it takes the place of the
    /// particle furthest through its life, so the number here stays the same.
    pub fn take_particle(&mut self, node: usize) -> bool {
        let Some(place) = self.entry(node) else {
            return false;
        };
        let Some(i) = crate::oldest(&self.ages, &self.lives) else {
            return false;
        };
        self.particles[i] = place;
        self.ages[i] = 0.0;
        self.lives[i] = crate::particle_life(&mut self.rng);
        true
    }

    /// Flow particles passed to another reach since the last call: the junction each reached.
    pub fn take_handed_particles(&mut self) -> Vec<Via> {
        std::mem::take(&mut self.handed_particles)
    }

    /// The water in the channels now, m³; 0 before the first day. With the
    /// particle count, the flow particles' density, which a join between
    /// reaches keeps even.
    #[must_use]
    pub fn water_volume(&self) -> f64 {
        self.particle_conditions(self.time)
            .map_or(0.0, |now| now.volume.iter().sum())
    }

    /// Where something arriving at junction `node` from another reach goes:
    /// one of the network's own channels there, in proportion to the flow
    /// leaving the junction into each (or, if none is leaving, any of them
    /// evenly), at the junction's end. None if the junction has none.
    fn entry(&mut self, node: usize) -> Option<Place> {
        let ours: Vec<(usize, End)> = self.at_node.get(node).map_or_else(Vec::new, |v| {
            v.iter()
                .copied()
                .filter(|&(c, _)| !self.handed_channel.get(c).copied().unwrap_or(false))
                .collect()
        });
        if ours.is_empty() {
            return None;
        }
        let now = self.conditions(self.time);
        let mut ways: Vec<(Place, f64)> = ours
            .iter()
            .map(|&(c, end)| {
                let x = if end == End::Up {
                    0.0
                } else {
                    self.topo.length[c]
                };
                let q = now.as_ref().map_or(0.0, |n| match end {
                    End::Up => n.q_up[c],
                    End::Down => -n.q_down[c],
                });
                (Place::Channel(c, x), q.max(0.0))
            })
            .collect();
        if ways.iter().all(|w| w.1 <= 0.0) {
            for w in &mut ways {
                w.1 = 1.0;
            }
        }
        let u = self.rng.uniform();
        pick(u, &ways)
    }

    /// Load one day's channel file (`flow_up`, `flow_down`, `area`).
    ///
    /// # Errors
    /// If the file is not a network channel file for this topology.
    pub fn load_channels(&mut self, bytes: &[u8]) -> Result<(), FormatError> {
        self.net = None;
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
        self.net = None;
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
        let (el, _) = self.particles();
        el.iter()
            .map(|&e| {
                per_channel
                    .as_ref()
                    .and_then(|v| v.get(e as usize))
                    .map_or(f32::NAN, |&x| {
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
        self.net = None;
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

    /// The sediment's settling velocity, m/s (the reach's `sediment` entry,
    /// from its sediment run), for bound releases.
    pub fn set_settling(&mut self, ws: f64) {
        self.settling = (ws.is_finite() && ws > 0.0).then_some(ws);
    }

    /// Add a release of a pollutant that sticks to suspended sediment, by its
    /// partition coefficient `kd`, L/kg. Where it is released, the share bound
    /// to the sediment is `kd c / (1 + kd c)`, `c` the suspended sediment there
    /// (the `ssc` field, mg/L, as kg/L), the linear equilibrium partition
    /// (Chapra 1997, ref:chapra1997); that share's parcels settle and are
    /// scoured back up with the mud (`step_to`), the rest is dissolved. With no
    /// sediment field, or released in a pool, everything is dissolved. The
    /// split is made once, at release; the pollutant does not move between
    /// water and sediment afterwards. Returns the bound share.
    pub fn add_bound_release(&mut self, r: NetRelease, kd: f64) -> f64 {
        let c = match r.at {
            Place::Channel(ch, _) => self
                .quality
                .at(r.start, "ssc")
                .and_then(|v| v.get(ch).copied())
                .filter(|x| x.is_finite() && *x > 0.0),
            _ => None,
        };
        let share = match c {
            Some(mg_l) if kd.is_finite() && kd > 0.0 => {
                let kc = kd * mg_l * 1e-6; // L/kg x kg/L
                kc / (1.0 + kc)
            }
            _ => 0.0,
        };
        self.push_release(&r, share);
        share
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

    /// Show the flow as net flow (`true`: the flow particles move on the flow
    /// with the tides filtered out) or moment by moment (`false`). Parcels
    /// always move with the real, instant flow. The filtered copies are built
    /// on first use and again after more days are loaded.
    pub fn set_net_flow(&mut self, on: bool) {
        self.net_on = on;
        if on && self.net.is_none() {
            self.net = Some([
                self.channels.tidal_mean(),
                self.nodes.tidal_mean(),
                self.links.tidal_mean(),
            ]);
        }
    }

    /// The conditions the parcels move in: the real flow at `t`.
    fn conditions(&self, t: f64) -> Option<Now> {
        self.conditions_in(t, &self.channels, &self.nodes, &self.links)
    }

    /// The conditions the flow particles move in: the real flow, or in
    /// net-flow mode the tidally filtered flow.
    fn particle_conditions(&self, t: f64) -> Option<Now> {
        match (&self.net, self.net_on) {
            (Some([c, n, l]), true) => self.conditions_in(t, c, n, l),
            _ => self.conditions(t),
        }
    }

    fn conditions_in(
        &self,
        t: f64,
        channels: &Series,
        nodes: &Series,
        links: &Series,
    ) -> Option<Now> {
        let (q_up, q_down, area) = (
            channels.at(t, "flow_up")?,
            channels.at(t, "flow_down")?,
            channels.at(t, "area")?,
        );
        let n = self.topo.nodes;
        let (export, withdrawal, volume) = match (
            nodes.at(t, "export"),
            nodes.at(t, "withdrawal"),
            nodes.at(t, "volume"),
        ) {
            (Some(e), Some(w), Some(v)) => (e, w, v),
            _ => (
                vec![0.0; n + self.topo.pools],
                vec![0.0; n + self.topo.pools],
                vec![f64::NAN; n + self.topo.pools],
            ),
        };
        let link_flow = links
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
            ways.push((Place::Gone(self.exported(n)), now.export[n]));
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
        match pick(u, &ways) {
            Some(Place::Channel(c, _)) if self.handed_channel.get(c).copied().unwrap_or(false) => {
                Some(Place::Gone(Fate::HandedOn(n)))
            }
            other => other,
        }
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
                        ways.push((PoolExit::Gone(self.exported(i)), now.export[i]));
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
                    if let Some((at, down, id)) = self.cut.get(c).copied().flatten() {
                        let s = at * len;
                        let past = |p: f64| if down { p >= s } else { p <= s };
                        if !past(x) && past(nx) {
                            // As at a junction, only the flow carries anything across:
                            // a crossing made by the random spreading step alone is
                            // turned back.
                            if past(x + u * left) {
                                return Place::Gone(Fate::Crossed(id));
                            }
                            return Place::Channel(c, (2.0 * s - nx).clamp(0.0, len));
                        }
                    }
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
            if self.net_on {
                if let Some(pnow) = self.particle_conditions(self.time + dt / 2.0) {
                    self.move_particles(dt, &pnow);
                }
            } else {
                self.move_particles(dt, &now);
            }
            self.time += dt;
        }
    }

    /// Each channel's rates for bound parcels at `t`, 1/s: settling, ws over
    /// the water's depth (its cross-section over its width), and scouring,
    /// the erosion rate over the settled mass (`erosion`, `bed_mud` from the
    /// sediment run). None without a settling velocity, widths or those fields.
    fn sediment_now(&self, t: f64, now: &Now) -> Option<SedNow> {
        let ws = self.settling?;
        if self.widths.len() != self.topo.length.len() {
            return None;
        }
        let bed = self.quality.at(t, "bed_mud")?;
        let ero = self.quality.at(t, "erosion")?;
        let settle = (0..self.topo.length.len())
            .map(|c| {
                let depth = now.volume[c] / self.topo.length[c].max(1.0) / self.widths[c].max(1.0);
                if depth > 0.01 {
                    ws / depth
                } else {
                    0.0
                }
            })
            .collect();
        let resuspend = bed
            .iter()
            .zip(&ero)
            .map(|(&m, &e)| {
                if m > 1e-9 && e.is_finite() && e > 0.0 {
                    e / m
                } else {
                    0.0
                }
            })
            .collect();
        Some(SedNow { settle, resuspend })
    }

    /// Move the flow particles `dt` s under the conditions at the current time,
    /// without advancing it (for "now": the water keeps moving, the clock does not).
    pub fn drift_frozen(&mut self, dt: f64) {
        let mut left = dt;
        while left > 0.0 {
            let step = left.min(MAX_DT);
            if let Some(now) = self.particle_conditions(self.time) {
                self.move_particles(step, &now);
            }
            left -= step;
        }
    }

    fn move_particles(&mut self, dt: f64, now: &Now) {
        for i in 0..self.particles.len() {
            self.ages[i] += dt;
            let p = self.advance(self.particles[i], dt, now, 0.0);
            // A particle that leaves, settles in a pool or has lived its life starts again elsewhere.
            let renew = match p {
                Place::Channel(..) => self.ages[i] > self.lives[i],
                Place::Gone(fate) => {
                    if let Some(via) = fate.via() {
                        self.handed_particles.push(via);
                    }
                    true
                }
                Place::Pool(_) => true,
            };
            if renew {
                self.particles[i] = self.random_place(now);
                self.ages[i] = 0.0;
                self.lives[i] = crate::particle_life(&mut self.rng);
            } else {
                self.particles[i] = p;
            }
        }
    }

    /// A place anywhere in the network's own water, evenly over its surface
    /// (`set_widths`), or else by volume: not in a channel another reach
    /// tracks, nor past a cut.
    fn random_place(&mut self, now: &Now) -> Place {
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
        let total: f64 = own.iter().sum();
        let mut r = self.rng.uniform() * total;
        for (c, v) in own.iter().enumerate() {
            if r < *v {
                let (a, b) = self.own_part(c);
                return Place::Channel(c, a + self.rng.uniform() * (b - a));
            }
            r -= v;
        }
        Place::Channel(0, 0.0)
    }

    /// Each channel's mean velocity now, m/s, positive downstream (net of the
    /// tides in net-flow mode, as the flow particles move); empty before the first day.
    #[must_use]
    pub fn velocities(&self) -> Vec<f32> {
        let Some(now) = self.particle_conditions(self.time) else {
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
        self.ages.clear();
        self.lives.clear();
        let Some(now) = self.particle_conditions(self.time) else {
            return;
        };
        for _ in 0..n {
            let p = self.random_place(&now);
            self.particles.push(p);
        }
        // Each part way through its life, so that they do not all start again at once.
        self.lives = (0..n)
            .map(|_| crate::particle_life(&mut self.rng))
            .collect();
        self.ages = self
            .lives
            .iter()
            .map(|l| l * self.rng.uniform())
            .collect::<Vec<_>>();
        self.handed_particles.clear();
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
            .ages
            .iter()
            .zip(&t.lives)
            .all(|(a, l)| *a >= 0.0 && a <= l));
        t.step_to(10.0 * 3600.0);
        // Ten hours on, every one has started again at least once (lives are 3 to 9 h).
        assert!(
            t.ages.iter().all(|a| *a < crate::PARTICLE_LIFE_S.1),
            "{:?}",
            t.ages.iter().copied().fold(0.0, f64::max)
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
        for p in &t.particles {
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

    #[test]
    fn a_flow_particle_from_another_reach_replaces_the_oldest() {
        let mut t = split();
        t.seed_flow_particles(20);
        let i = crate::oldest(&t.ages, &t.lives).unwrap();
        assert!(t.take_particle(0));
        assert_eq!(t.particles.len(), 20);
        assert_eq!(t.ages[i].to_bits(), 0.0_f64.to_bits());
        assert!(matches!(t.particles[i], Place::Channel(..)));
    }
}
