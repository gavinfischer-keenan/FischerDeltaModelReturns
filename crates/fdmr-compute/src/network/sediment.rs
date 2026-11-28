//! Parcels bound to sediment (#83): their share of a release, and the
//! rates at which they settle onto the bed and are scoured off it.

#[allow(clippy::wildcard_imports)]
use super::*;

impl NetworkTracer {
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

    /// Each channel's rates for bound parcels at `t`, 1/s: settling, ws over
    /// the water's depth (its cross-section over its width), and scouring,
    /// the erosion rate over the settled mass (`erosion`, `bed_mud` from the
    /// sediment run). None without a settling velocity, widths or those fields.
    pub(super) fn sediment_now(&self, t: f64, now: &Now) -> Option<SedNow> {
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
}

/// Bound parcels' rates per channel at one moment, 1/s.
pub(super) struct SedNow {
    pub(super) settle: Vec<f64>,
    pub(super) resuspend: Vec<f64>,
}
