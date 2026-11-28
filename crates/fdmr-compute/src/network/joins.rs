//! Where the network meets other reaches (#83): channels another reach tracks,
//! cuts where it begins, outlets that pass on what the pumps take, and
//! parcels and flow particles handed in and out.

#[allow(clippy::wildcard_imports)]
use super::*;

impl NetworkTracer {
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
    pub(super) fn exported(&self, i: usize) -> Fate {
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
    pub(super) fn own_part(&self, c: usize) -> (f64, f64) {
        let len = self.topo.length[c];
        match self.cut.get(c).copied().flatten() {
            Some((at, true, _)) => (0.0, at * len),
            Some((at, false, _)) => (at * len, len),
            None => (0.0, len),
        }
    }

    /// Where something coming back through cut `k` enters: just inside the
    /// network's own part, at the cut.
    pub(super) fn at_cut(&self, k: usize) -> Option<Place> {
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
        self.flow.take_in(&mut self.rng, |_| place)
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
        self.flow.take_in(&mut self.rng, |_| place)
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
    pub(super) fn entry(&mut self, node: usize) -> Option<Place> {
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
        super::routing::pick(u, &ways)
    }
}
