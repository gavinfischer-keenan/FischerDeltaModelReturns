//! How water and what it carries move through the network (#83): the
//! conditions at a moment, the way out of a junction or pool, and a
//! parcel's or particle's move along a channel.

#[allow(clippy::wildcard_imports)]
use super::*;

impl NetworkTracer {
    /// Show the flow as net flow (`true`: the flow particles move on the flow
    /// with the tides filtered out) or moment by moment (`false`). Parcels
    /// always move with the real, instant flow. The filtered copies are built
    /// on first use and again after more days are loaded.
    pub fn set_net_flow(&mut self, on: bool) {
        let (c, n, l) = (&self.channels, &self.nodes, &self.links);
        self.net
            .set(on, || [c.tidal_mean(), n.tidal_mean(), l.tidal_mean()]);
    }

    /// The conditions the parcels move in: the real flow at `t`.
    pub(super) fn conditions(&self, t: f64) -> Option<Now> {
        self.conditions_in(t, &self.channels, &self.nodes, &self.links)
    }

    /// The conditions the flow particles move in: the real flow, or in
    /// net-flow mode the tidally filtered flow.
    pub(super) fn particle_conditions(&self, t: f64) -> Option<Now> {
        match self.net.active() {
            Some([c, n, l]) => self.conditions_in(t, c, n, l),
            None => self.conditions(t),
        }
    }

    pub(super) fn conditions_in(
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
    pub(super) fn route(&self, now: &Now, n: usize, u: f64) -> Option<Place> {
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
    pub(super) fn advance(&mut self, mut place: Place, dt: f64, now: &Now, k: f64) -> Place {
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
}

/// How something leaves a pool.
#[derive(Debug, Clone, Copy)]
pub(super) enum PoolExit {
    /// Through a link, to this junction.
    Link(usize),
    Gone(Fate),
}

/// One of the ways, in proportion to their weights, using `u` in [0, 1).
pub(super) fn pick<T: Copy>(u: f64, ways: &[(T, f64)]) -> Option<T> {
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
