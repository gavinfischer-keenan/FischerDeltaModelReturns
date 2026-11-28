//! The pools' water (flooded islands, forebays; #83): their surface, and
//! flow particles drifting over their mapped water.

#[allow(clippy::wildcard_imports)]
use super::*;

impl NetworkTracer {
    /// Each pool's water surface, m². With the channels' widths given, flow
    /// particles start evenly over all the network's water, pools included:
    /// a flooded island or a forebay shows its water like a channel does.
    pub fn set_pool_areas(&mut self, areas: &[f64]) {
        self.pool_area = areas.iter().map(|a| a.max(0.0)).collect();
    }

    /// Pool `q`'s mapped water as points (lon, lat) spread evenly over it, and
    /// the horizontal mixing, m²/s, its particles drift at. A pool is well
    /// mixed and its currents are not known (DSM2 holds a level and a volume),
    /// so a particle in it wanders, never onto land: for drawing only, and the
    /// speed it is shown with stays unknown.
    pub fn set_pool_water(&mut self, q: usize, lonlat: &[(f64, f64)], mixing: f64) {
        if self.pool_water.len() <= q {
            self.pool_water.resize_with(q + 1, || None);
        }
        self.pool_water[q] = PoolWater::new(lonlat, mixing);
    }

    /// Each flow particle's place on its pool's water, (lon, lat), or `NaN`s
    /// for one not in a pool with mapped water.
    #[must_use]
    pub fn particle_pool_positions(&self) -> Vec<(f64, f64)> {
        (0..self.flow.len())
            .map(
                |i| match (self.flow.places[i], self.pool_at.get(i).copied().flatten()) {
                    (Place::Pool(q), Some((p, x, y))) if p == q => self.pool_water[q]
                        .as_ref()
                        .map_or((f64::NAN, f64::NAN), |w| w.lonlat(x, y)),
                    _ => (f64::NAN, f64::NAN),
                },
            )
            .collect()
    }

    /// Move each pool particle over its pool's water by `dt` s; a particle new
    /// to a pool starts anywhere on it.
    pub(super) fn drift_in_pools(&mut self, dt: f64) {
        self.pool_at.resize(self.flow.len(), None);
        for i in 0..self.flow.len() {
            let Place::Pool(q) = self.flow.places[i] else {
                self.pool_at[i] = None;
                continue;
            };
            let Some(w) = self.pool_water.get(q).and_then(Option::as_ref) else {
                self.pool_at[i] = None;
                continue;
            };
            self.pool_at[i] = Some(match self.pool_at[i] {
                Some((p, x, y)) if p == q => {
                    let s = (2.0 * w.mixing * dt).sqrt();
                    let (nx, ny) = (
                        x + s * self.pool_rng.normal(),
                        y + s * self.pool_rng.normal(),
                    );
                    if w.on_water(nx, ny) {
                        (q, nx, ny)
                    } else {
                        (q, x, y)
                    }
                }
                _ => {
                    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
                    let k = ((self.pool_rng.uniform() * w.pts.len() as f64) as usize)
                        .min(w.pts.len() - 1);
                    (q, w.pts[k].0, w.pts[k].1)
                }
            });
        }
    }
}

/// A pool's mapped water, in its own flat metres about its first point.
pub(super) struct PoolWater {
    pub(super) lon0: f64,
    pub(super) lat0: f64,
    /// Metres per degree of longitude and of latitude there.
    pub(super) kx: f64,
    pub(super) ky: f64,
    pub(super) pts: Vec<(f64, f64)>,
    /// Points by grid cell, `cell` m square, to find those near a place.
    pub(super) grid: HashMap<(i64, i64), Vec<usize>>,
    pub(super) cell: f64,
    /// A place is on the water within this of one of the points, m.
    pub(super) reach: f64,
    pub(super) mixing: f64,
}

impl PoolWater {
    /// Metres per degree of latitude (mean Earth radius 6371 km).
    const M_PER_DEG: f64 = 111_195.0;

    pub(super) fn new(lonlat: &[(f64, f64)], mixing: f64) -> Option<Self> {
        let &(lon0, lat0) = lonlat.first()?;
        let ky = Self::M_PER_DEG;
        let kx = ky * lat0.to_radians().cos();
        let pts: Vec<(f64, f64)> = lonlat
            .iter()
            .map(|&(lon, lat)| ((lon - lon0) * kx, (lat - lat0) * ky))
            .collect();
        // The points' spacing: the mean distance to the nearest other point,
        // over up to 200 of them.
        let step = (pts.len() / 200).max(1);
        let near: Vec<f64> = pts
            .iter()
            .step_by(step)
            .map(|&(x, y)| {
                pts.iter()
                    .filter(|&&(a, b)| (a, b) != (x, y))
                    .map(|&(a, b)| (a - x).hypot(b - y))
                    .fold(f64::INFINITY, f64::min)
            })
            .filter(|d| d.is_finite())
            .collect();
        #[allow(clippy::cast_precision_loss)]
        let spacing = if near.is_empty() {
            50.0
        } else {
            near.iter().sum::<f64>() / near.len() as f64
        };
        let cell = spacing.max(1.0) * 1.5;
        let mut grid: HashMap<(i64, i64), Vec<usize>> = HashMap::new();
        for (i, &(x, y)) in pts.iter().enumerate() {
            #[allow(clippy::cast_possible_truncation)]
            grid.entry(((x / cell).floor() as i64, (y / cell).floor() as i64))
                .or_default()
                .push(i);
        }
        Some(Self {
            lon0,
            lat0,
            kx,
            ky,
            pts,
            grid,
            cell,
            reach: cell,
            mixing: mixing.max(0.0),
        })
    }

    /// Whether (x, y) is on the water: within `reach` of one of its points.
    pub(super) fn on_water(&self, x: f64, y: f64) -> bool {
        #[allow(clippy::cast_possible_truncation)]
        let (cx, cy) = (
            (x / self.cell).floor() as i64,
            (y / self.cell).floor() as i64,
        );
        (cx - 1..=cx + 1).any(|i| {
            (cy - 1..=cy + 1).any(|j| {
                self.grid.get(&(i, j)).is_some_and(|v| {
                    v.iter()
                        .any(|&k| (self.pts[k].0 - x).hypot(self.pts[k].1 - y) <= self.reach)
                })
            })
        })
    }

    pub(super) fn lonlat(&self, x: f64, y: f64) -> (f64, f64) {
        (self.lon0 + x / self.kx, self.lat0 + y / self.ky)
    }
}
