//! Tracking on a 2D triangular mesh (docs/format.md, 2D reaches;
//! specification 7.3 and 8.1).
//!
//! A mesh reach publishes, at its nodes, depth-averaged velocity `u`, `v`
//! (m/s, east and north) and water level `level` (m, on the same datum as the
//! still-water `depth`). Within a triangle every field is linear (barycentric
//! interpolation); between published steps, linear in time.
//!
//! A parcel moves by the random-walk form of the depth-averaged
//! advection-diffusion equation for a constant horizontal diffusivity K:
//!
//! dX = (u + K ∇H / H) dt + √(2K) dW
//!
//! where H is the total water depth. The K ∇H / H term is what keeps parcels
//! from collecting in shallow water, where a plain random walk would leave
//! them (the well-mixed condition: Dimou and Adams 1993, Estuarine, Coastal and
//! Shelf Science 37:99-110; Visser 1997, Marine Ecology Progress Series
//! 158:275-281). The flow part is taken with a midpoint (second-order
//! Runge-Kutta) step.
//!
//! A move is followed in a straight line through the triangles it crosses.
//! A land edge reflects it; an open-sea edge takes the parcel out of the
//! system; a handoff edge passes it to another reach, with where it crossed.
//! Where the water is too shallow to move (a drying mudflat) a parcel waits.

use fdmr_format::FormatError;
use fdmr_math::{decay, rng::Rng};

use crate::{series::Series, MAX_DT};

/// Water shallower than this, m, does not move parcels (dry or drying).
pub const MIN_DEPTH: f64 = 0.1;

/// What lies across one edge of a triangle.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Tri(usize),
    Land,
    Sea,
    /// Passes to another reach: the handoff's id.
    Handoff(u32),
}

/// A mesh and its boundaries. Coordinates are planar, m.
#[derive(Debug, Clone)]
pub struct MeshTopology {
    pub x: Vec<f64>,
    pub y: Vec<f64>,
    /// Still-water depth at each node, m (positive down).
    pub depth: Vec<f64>,
    /// Counter-clockwise node triples.
    pub tri: Vec<[usize; 3]>,
    /// Boundary edges open to the sea, as node pairs (either order).
    pub open: Vec<(usize, usize)>,
    /// Boundary edges handed to another reach: node pair and handoff id.
    pub handoff: Vec<(usize, usize, u32)>,
}

/// Where a parcel is: in a triangle at a point, or gone.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum MeshPlace {
    In(usize, f64, f64),
    Gone(MeshFate),
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum MeshFate {
    ToSea,
    /// Crossed a handoff edge, at (x, y) on it, at time t.
    Handoff {
        id: u32,
        x: f64,
        y: f64,
        t: f64,
    },
}

#[derive(Debug, Clone, Copy)]
pub struct MeshRelease {
    pub x: f64,
    pub y: f64,
    pub start: f64,
    /// s; 0 releases everything at once.
    pub duration: f64,
    pub mass: f64,
    pub half_life: Option<f64>,
    pub parcels: usize,
}

#[derive(Debug, Clone, Copy)]
struct Parcel {
    place: MeshPlace,
    mass: f64,
    born: f64,
    half_life: Option<f64>,
    /// Counted in the balance's `released` yet.
    counted: bool,
}

/// [released, in the water, out to sea, handed on, broken down], kg.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct MeshBalance {
    pub released: f64,
    pub in_water: f64,
    pub to_sea: f64,
    pub handed_on: f64,
    pub decayed: f64,
}

/// Fields at the nodes at one moment.
struct Now {
    u: Vec<f64>,
    v: Vec<f64>,
    /// Total depth, m.
    h: Vec<f64>,
}

/// The flow at a point: velocity, total depth and its gradient.
struct Sample {
    u: f64,
    v: f64,
    h: f64,
    hx: f64,
    hy: f64,
}

/// Parcels and flow particles moving on a mesh reach.
pub struct MeshTracer {
    topo: MeshTopology,
    sides: Vec<[Side; 3]>,
    /// Twice each triangle's area, m².
    area2: Vec<f64>,
    grid: Grid,
    fields: Series,
    diffusivity: f64,
    time: f64,
    rng: Rng,
    parcels: Vec<Parcel>,
    particles: Vec<MeshPlace>,
    balance: MeshBalance,
    handed: Vec<(MeshFate, f64)>,
}

/// Edge `e` of a triangle runs from its node `e` to node `e + 1`.
fn edge(t: &[usize; 3], e: usize) -> (usize, usize) {
    (t[e], t[(e + 1) % 3])
}

fn key(a: usize, b: usize) -> (usize, usize) {
    (a.min(b), a.max(b))
}

impl MeshTracer {
    /// `diffusivity`, m²/s, is the horizontal diffusivity K everywhere.
    ///
    /// # Panics
    /// If a triangle is not counter-clockwise (or is degenerate).
    #[must_use]
    pub fn new(topo: MeshTopology, start_time: f64, seed: u64, diffusivity: f64) -> Self {
        use std::collections::HashMap;
        let mut owner: HashMap<(usize, usize), (usize, usize)> = HashMap::new();
        let mut sides = vec![[Side::Land; 3]; topo.tri.len()];
        for (i, t) in topo.tri.iter().enumerate() {
            for e in 0..3 {
                let (a, b) = edge(t, e);
                if let Some((j, f)) = owner.remove(&key(a, b)) {
                    sides[i][e] = Side::Tri(j);
                    sides[j][f] = Side::Tri(i);
                } else {
                    owner.insert(key(a, b), (i, e));
                }
            }
        }
        for &(a, b) in &topo.open {
            if let Some(&(i, e)) = owner.get(&key(a, b)) {
                sides[i][e] = Side::Sea;
            }
        }
        for &(a, b, id) in &topo.handoff {
            if let Some(&(i, e)) = owner.get(&key(a, b)) {
                sides[i][e] = Side::Handoff(id);
            }
        }
        let area2: Vec<f64> = topo
            .tri
            .iter()
            .map(|t| {
                let a2 = (topo.x[t[1]] - topo.x[t[0]]) * (topo.y[t[2]] - topo.y[t[0]])
                    - (topo.x[t[2]] - topo.x[t[0]]) * (topo.y[t[1]] - topo.y[t[0]]);
                assert!(a2 > 0.0, "triangles must be counter-clockwise");
                a2
            })
            .collect();
        let grid = Grid::new(&topo);
        Self {
            topo,
            sides,
            area2,
            grid,
            fields: Series::default(),
            diffusivity,
            time: start_time,
            rng: Rng::new(seed),
            parcels: Vec::new(),
            particles: Vec::new(),
            balance: MeshBalance::default(),
            handed: Vec::new(),
        }
    }

    /// Load one day's file (`u`, `v`, `level` at the nodes).
    ///
    /// # Errors
    /// If the file is not a field file for this mesh.
    pub fn load_day(&mut self, bytes: &[u8]) -> Result<(), FormatError> {
        self.fields.load_day(bytes, &["u", "v", "level"])?;
        if self.fields.elements != self.topo.x.len() {
            return Err(FormatError::Invalid(
                "day file does not match the mesh".into(),
            ));
        }
        Ok(())
    }

    #[must_use]
    pub fn time(&self) -> f64 {
        self.time
    }

    #[must_use]
    pub fn triangles(&self) -> usize {
        self.topo.tri.len()
    }

    /// The triangle holding a point, if any.
    #[must_use]
    pub fn locate(&self, x: f64, y: f64) -> Option<usize> {
        self.grid
            .near(x, y)
            .iter()
            .copied()
            .find(|&t| self.inside(t, x, y))
    }

    fn bary(&self, t: usize, x: f64, y: f64) -> [f64; 3] {
        let [a, b, c] = self.topo.tri[t];
        let (xs, ys) = (&self.topo.x, &self.topo.y);
        let l0 = ((xs[b] - x) * (ys[c] - y) - (xs[c] - x) * (ys[b] - y)) / self.area2[t];
        let l1 = ((xs[c] - x) * (ys[a] - y) - (xs[a] - x) * (ys[c] - y)) / self.area2[t];
        [l0, l1, 1.0 - l0 - l1]
    }

    fn inside(&self, t: usize, x: f64, y: f64) -> bool {
        self.bary(t, x, y).iter().all(|&l| l >= -1e-9)
    }

    #[must_use]
    pub fn balance(&self) -> MeshBalance {
        let mut b = self.balance;
        b.in_water = self
            .parcels
            .iter()
            .filter(|p| p.born <= self.time && matches!(p.place, MeshPlace::In(..)))
            .map(|p| p.mass)
            .sum();
        b
    }

    /// Add a release at a point. Returns false (and adds nothing) if the point is not on the mesh.
    pub fn add_release(&mut self, r: MeshRelease) -> bool {
        let Some(t) = self.locate(r.x, r.y) else {
            return false;
        };
        let n = r.parcels.max(1);
        #[allow(clippy::cast_precision_loss)]
        let each = r.mass / n as f64;
        for i in 0..n {
            #[allow(clippy::cast_precision_loss)]
            let born = r.start + r.duration * (i as f64 + 0.5) / n as f64;
            self.parcels.push(Parcel {
                place: MeshPlace::In(t, r.x, r.y),
                mass: each,
                born,
                half_life: r.half_life,
                counted: false,
            });
        }
        true
    }

    /// Take in one parcel handed over from another reach, at a point on this mesh.
    /// Returns false if the point is not on the mesh.
    pub fn take_parcel(
        &mut self,
        x: f64,
        y: f64,
        mass: f64,
        born: f64,
        half_life: Option<f64>,
    ) -> bool {
        let Some(t) = self.locate(x, y) else {
            return false;
        };
        self.balance.released += mass;
        self.parcels.push(Parcel {
            place: MeshPlace::In(t, x, y),
            mass,
            // It moves from now on, whenever it was first released.
            born: born.min(self.time),
            half_life,
            counted: true,
        });
        true
    }

    /// Parcels that crossed a handoff edge since the last call, with their mass.
    pub fn take_handed(&mut self) -> Vec<(MeshFate, f64)> {
        std::mem::take(&mut self.handed)
    }

    pub fn clear_releases(&mut self) {
        self.parcels.clear();
        self.handed.clear();
        self.balance = MeshBalance::default();
    }

    fn conditions(&self, t: f64) -> Option<Now> {
        let u = self.fields.at(t, "u")?;
        let v = self.fields.at(t, "v")?;
        let level = self.fields.at(t, "level")?;
        let h = self
            .topo
            .depth
            .iter()
            .zip(&level)
            .map(|(d, l)| if l.is_nan() { *d } else { d + l })
            .collect();
        let nan0 = |w: Vec<f64>| {
            w.into_iter()
                .map(|x| if x.is_nan() { 0.0 } else { x })
                .collect()
        };
        Some(Now {
            u: nan0(u),
            v: nan0(v),
            h,
        })
    }

    fn sample(&self, now: &Now, t: usize, x: f64, y: f64) -> Sample {
        let l = self.bary(t, x, y);
        let n = self.topo.tri[t];
        let at = |f: &[f64]| l[0] * f[n[0]] + l[1] * f[n[1]] + l[2] * f[n[2]];
        let h = at(&now.h);
        // The gradient of the linear depth over the triangle.
        let (xs, ys, hs) = (&self.topo.x, &self.topo.y, &now.h);
        let hx = (hs[n[0]] * (ys[n[1]] - ys[n[2]])
            + hs[n[1]] * (ys[n[2]] - ys[n[0]])
            + hs[n[2]] * (ys[n[0]] - ys[n[1]]))
            / self.area2[t];
        let hy = (hs[n[0]] * (xs[n[2]] - xs[n[1]])
            + hs[n[1]] * (xs[n[0]] - xs[n[2]])
            + hs[n[2]] * (xs[n[1]] - xs[n[0]]))
            / self.area2[t];
        if h < MIN_DEPTH {
            return Sample {
                u: 0.0,
                v: 0.0,
                h,
                hx: 0.0,
                hy: 0.0,
            };
        }
        Sample {
            u: at(&now.u),
            v: at(&now.v),
            h,
            hx,
            hy,
        }
    }

    /// Follow a straight move from (x, y) in triangle `t` by (dx, dy).
    fn walk(
        &self,
        mut t: usize,
        mut x: f64,
        mut y: f64,
        mut dx: f64,
        mut dy: f64,
        time: f64,
    ) -> MeshPlace {
        let mut bounces = 0;
        for _ in 0..10_000 {
            let (tx, ty) = (x + dx, y + dy);
            if self.inside(t, tx, ty) {
                return MeshPlace::In(t, tx, ty);
            }
            // The edge the move leaves by: the first one the path crosses.
            let tri = self.topo.tri[t];
            let mut best: Option<(usize, f64)> = None;
            for e in 0..3 {
                let (a, b) = edge(&tri, e);
                let (ax, ay, bx, by) = (
                    self.topo.x[a],
                    self.topo.y[a],
                    self.topo.x[b],
                    self.topo.y[b],
                );
                // Is the target outside this edge (right of a→b)?
                let out = (bx - ax) * (ty - ay) - (by - ay) * (tx - ax);
                if out >= 0.0 {
                    continue;
                }
                // Where the path (x, y) + s (dx, dy) meets the edge's line.
                let den = (bx - ax) * dy - (by - ay) * dx;
                if den.abs() < 1e-30 {
                    continue;
                }
                let s = ((bx - ax) * (ay - y) - (by - ay) * (ax - x)) / den;
                let s = s.clamp(0.0, 1.0);
                if best.is_none_or(|(_, bs)| s < bs) {
                    best = Some((e, s));
                }
            }
            let Some((e, s)) = best else {
                return MeshPlace::In(t, x, y);
            };
            let (cx, cy) = (x + dx * s, y + dy * s);
            let (rx, ry) = (dx * (1.0 - s), dy * (1.0 - s));
            match self.sides[t][e] {
                Side::Tri(n) => {
                    // Step just inside the next triangle.
                    t = n;
                    x = cx + rx * 1e-9;
                    y = cy + ry * 1e-9;
                    dx = rx * (1.0 - 1e-9);
                    dy = ry * (1.0 - 1e-9);
                }
                Side::Sea => return MeshPlace::Gone(MeshFate::ToSea),
                Side::Handoff(id) => {
                    return MeshPlace::Gone(MeshFate::Handoff {
                        id,
                        x: cx,
                        y: cy,
                        t: time,
                    })
                }
                Side::Land => {
                    bounces += 1;
                    let (a, b) = edge(&tri, e);
                    let (ex, ey) = (
                        self.topo.x[b] - self.topo.x[a],
                        self.topo.y[b] - self.topo.y[a],
                    );
                    let len2 = ex * ex + ey * ey;
                    // Mirror what is left of the move in the edge.
                    let along = (rx * ex + ry * ey) / len2;
                    let (px, py) = (along * ex, along * ey);
                    dx = 2.0 * px - rx;
                    dy = 2.0 * py - ry;
                    // Back off the edge a hair, toward the triangle's inside.
                    let c = self.centroid(t);
                    x = cx + (c.0 - cx) * 1e-6;
                    y = cy + (c.1 - cy) * 1e-6;
                    if bounces > 8 {
                        return MeshPlace::In(t, x, y);
                    }
                }
            }
        }
        MeshPlace::In(t, x, y)
    }

    fn centroid(&self, t: usize) -> (f64, f64) {
        let n = self.topo.tri[t];
        (
            (self.topo.x[n[0]] + self.topo.x[n[1]] + self.topo.x[n[2]]) / 3.0,
            (self.topo.y[n[0]] + self.topo.y[n[1]] + self.topo.y[n[2]]) / 3.0,
        )
    }

    /// One step of `dt` s from `place`: midpoint flow step, the K∇H/H drift and
    /// the random step (none when `k` is 0).
    fn advance(&mut self, place: MeshPlace, dt: f64, now: &Now, k: f64, time: f64) -> MeshPlace {
        let MeshPlace::In(t, x, y) = place else {
            return place;
        };
        let s1 = self.sample(now, t, x, y);
        if s1.h < MIN_DEPTH {
            return place;
        }
        let half = self.walk(t, x, y, s1.u * dt / 2.0, s1.v * dt / 2.0, time);
        let (u, v) = match half {
            MeshPlace::In(t2, x2, y2) => {
                let s2 = self.sample(now, t2, x2, y2);
                (s2.u, s2.v)
            }
            MeshPlace::Gone(_) => (s1.u, s1.v),
        };
        let (mut dx, mut dy) = (u * dt, v * dt);
        if k > 0.0 {
            let sd = (2.0 * k * dt).sqrt();
            dx += k * s1.hx / s1.h * dt + sd * self.rng.normal();
            dy += k * s1.hy / s1.h * dt + sd * self.rng.normal();
        }
        self.walk(t, x, y, dx, dy, time)
    }

    /// Run on to time `t`, in steps of at most a minute.
    pub fn step_to(&mut self, t: f64) {
        while self.time < t {
            let dt = (t - self.time).min(MAX_DT);
            let Some(now) = self.conditions(self.time + dt / 2.0) else {
                self.time += dt;
                continue;
            };
            let k = self.diffusivity;
            for i in 0..self.parcels.len() {
                let p = self.parcels[i];
                if p.born > self.time + dt || !matches!(p.place, MeshPlace::In(..)) {
                    continue;
                }
                let step = (self.time + dt - p.born.max(self.time)).max(0.0);
                let keep = decay::remaining_fraction(step, p.half_life);
                self.balance.decayed += p.mass * (1.0 - keep);
                let mass = p.mass * keep;
                let counted = p.counted || {
                    self.balance.released += p.mass;
                    true
                };
                let place = self.advance(p.place, step, &now, k, self.time + dt);
                match place {
                    MeshPlace::Gone(MeshFate::ToSea) => self.balance.to_sea += mass,
                    MeshPlace::Gone(fate @ MeshFate::Handoff { .. }) => {
                        self.balance.handed_on += mass;
                        self.handed.push((fate, mass));
                    }
                    MeshPlace::In(..) => {}
                }
                self.parcels[i] = Parcel {
                    place,
                    mass,
                    counted,
                    ..p
                };
            }
            self.move_particles(dt, &now);
            self.time += dt;
        }
    }

    /// Move the flow particles `dt` s at the current time without advancing it.
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
        let time = self.time;
        for i in 0..self.particles.len() {
            let p = self.advance(self.particles[i], dt, now, 0.0, time);
            self.particles[i] = match p {
                MeshPlace::In(..) => p,
                MeshPlace::Gone(_) => self.random_place(),
            };
        }
    }

    /// A point anywhere on the mesh, evenly by area.
    fn random_place(&mut self) -> MeshPlace {
        let total: f64 = self.area2.iter().sum();
        let mut r = self.rng.uniform() * total;
        let mut t = self.area2.len() - 1;
        for (i, a) in self.area2.iter().enumerate() {
            if r < *a {
                t = i;
                break;
            }
            r -= a;
        }
        let (mut a, mut b) = (self.rng.uniform(), self.rng.uniform());
        if a + b > 1.0 {
            (a, b) = (1.0 - a, 1.0 - b);
        }
        let n = self.topo.tri[t];
        let (xs, ys) = (&self.topo.x, &self.topo.y);
        MeshPlace::In(
            t,
            xs[n[0]] + a * (xs[n[1]] - xs[n[0]]) + b * (xs[n[2]] - xs[n[0]]),
            ys[n[0]] + a * (ys[n[1]] - ys[n[0]]) + b * (ys[n[2]] - ys[n[0]]),
        )
    }

    pub fn seed_flow_particles(&mut self, n: usize) {
        self.particles = (0..n).map(|_| self.random_place()).collect();
    }

    fn places(&self, it: impl Iterator<Item = MeshPlace>) -> (Vec<u32>, Vec<f32>) {
        let (mut tri, mut ab) = (Vec::new(), Vec::new());
        for p in it {
            if let MeshPlace::In(t, x, y) = p {
                let l = self.bary(t, x, y);
                #[allow(clippy::cast_possible_truncation)]
                {
                    tri.push(t as u32);
                    ab.push(l[1] as f32);
                    ab.push(l[2] as f32);
                }
            }
        }
        (tri, ab)
    }

    /// Flow particles: each one's triangle, and its place in it as two
    /// barycentric weights (for the triangle's second and third nodes).
    #[must_use]
    pub fn particles(&self) -> (Vec<u32>, Vec<f32>) {
        self.places(self.particles.iter().copied())
    }

    /// Parcels in the water, as for `particles`.
    #[must_use]
    pub fn parcels(&self) -> (Vec<u32>, Vec<f32>) {
        self.places(
            self.parcels
                .iter()
                .filter(|p| p.born <= self.time)
                .map(|p| p.place),
        )
    }

    /// Depth-averaged concentration where each parcel is, kg/m³: the mass in its
    /// triangle over the water there, in the order of `parcels()`.
    #[must_use]
    pub fn parcel_concentration(&self) -> Vec<f64> {
        let Some(now) = self.conditions(self.time) else {
            return Vec::new();
        };
        let mut mass = vec![0.0; self.topo.tri.len()];
        let live: Vec<&Parcel> = self
            .parcels
            .iter()
            .filter(|p| p.born <= self.time)
            .collect();
        for p in &live {
            if let MeshPlace::In(t, ..) = p.place {
                mass[t] += p.mass;
            }
        }
        live.iter()
            .filter_map(|p| match p.place {
                MeshPlace::In(t, ..) => {
                    let n = self.topo.tri[t];
                    let h = (now.h[n[0]] + now.h[n[1]] + now.h[n[2]]) / 3.0;
                    let vol = self.area2[t] / 2.0 * h.max(MIN_DEPTH);
                    Some(mass[t] / vol)
                }
                MeshPlace::Gone(_) => None,
            })
            .collect()
    }

    /// Speed at every node at the current time, m/s (for colouring the water).
    #[must_use]
    pub fn speeds(&self) -> Vec<f32> {
        self.conditions(self.time).map_or_else(Vec::new, |now| {
            #[allow(clippy::cast_possible_truncation)]
            now.u
                .iter()
                .zip(&now.v)
                .map(|(u, v)| u.hypot(*v) as f32)
                .collect()
        })
    }
}

/// A bucket grid over the triangles' bounding boxes, for finding which holds a point.
struct Grid {
    x0: f64,
    y0: f64,
    cell: f64,
    nx: usize,
    ny: usize,
    cells: Vec<Vec<usize>>,
}

impl Grid {
    fn new(topo: &MeshTopology) -> Self {
        let (mut x0, mut y0, mut x1, mut y1) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
        for (&x, &y) in topo.x.iter().zip(&topo.y) {
            x0 = x0.min(x);
            y0 = y0.min(y);
            x1 = x1.max(x);
            y1 = y1.max(y);
        }
        #[allow(clippy::cast_precision_loss)]
        let cell = ((x1 - x0) * (y1 - y0) / topo.tri.len().max(1) as f64)
            .sqrt()
            .max(1.0)
            * 2.0;
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let (nx, ny) = (
            ((x1 - x0) / cell) as usize + 1,
            ((y1 - y0) / cell) as usize + 1,
        );
        let mut cells = vec![Vec::new(); nx * ny];
        for (i, t) in topo.tri.iter().enumerate() {
            let xs = t.map(|n| topo.x[n]);
            let ys = t.map(|n| topo.y[n]);
            #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
            let range = |lo: f64, hi: f64, o: f64, m: usize| {
                (((lo - o) / cell) as usize).min(m - 1)..=(((hi - o) / cell) as usize).min(m - 1)
            };
            for cx in range(
                xs.iter().copied().fold(f64::MAX, f64::min),
                xs.iter().copied().fold(f64::MIN, f64::max),
                x0,
                nx,
            ) {
                for cy in range(
                    ys.iter().copied().fold(f64::MAX, f64::min),
                    ys.iter().copied().fold(f64::MIN, f64::max),
                    y0,
                    ny,
                ) {
                    cells[cy * nx + cx].push(i);
                }
            }
        }
        Self {
            x0,
            y0,
            cell,
            nx,
            ny,
            cells,
        }
    }

    fn near(&self, x: f64, y: f64) -> &[usize] {
        if x < self.x0 || y < self.y0 {
            return &[];
        }
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let (cx, cy) = (
            ((x - self.x0) / self.cell) as usize,
            ((y - self.y0) / self.cell) as usize,
        );
        if cx >= self.nx || cy >= self.ny {
            return &[];
        }
        &self.cells[cy * self.nx + cx]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use fdmr_format::{Encoding, Field, FieldFile};

    /// A channel 1000 m long and 200 m wide, of 2 x 10 squares cut in triangles:
    /// land along its sides, the sea at x = 1000 and a handoff at x = 0.
    fn channel(depth: f64) -> MeshTopology {
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
        let open = (0..ny - 1)
            .map(|j| (id(nx - 1, j), id(nx - 1, j + 1)))
            .collect();
        let handoff = (0..ny - 1).map(|j| (id(0, j), id(0, j + 1), 7)).collect();
        MeshTopology {
            depth: vec![depth; x.len()],
            x,
            y,
            tri,
            open,
            handoff,
        }
    }

    fn day(n: usize, u: f32, v: f32, steps: u32) -> Vec<u8> {
        let f = |name: &str, val: f32| Field {
            name: name.into(),
            unit: "m/s".into(),
            encoding: Encoding::F32,
            values: vec![val; n * steps as usize],
        };
        let file = FieldFile {
            reach: "test".into(),
            dims: 2,
            elements: u32::try_from(n).unwrap(),
            layers: 1,
            steps,
            t0: 0,
            step_s: 3600,
            fields: vec![f("u", u), f("v", v), f("level", 0.0)],
        };
        let mut b = Vec::new();
        file.write_to(&mut b).unwrap();
        b
    }

    fn tracer(u: f32, v: f32, k: f64) -> MeshTracer {
        let topo = channel(5.0);
        let n = topo.x.len();
        let mut t = MeshTracer::new(topo, 0.0, 1, k);
        t.load_day(&day(n, u, v, 25)).unwrap();
        t
    }

    #[test]
    fn locates_points() {
        let t = tracer(0.0, 0.0, 0.0);
        assert!(t.locate(150.0, 50.0).is_some());
        assert!(t.locate(150.0, 250.0).is_none());
        assert!(t.locate(-5.0, 50.0).is_none());
    }

    #[test]
    fn uniform_flow_carries_a_parcel_exactly() {
        let mut t = tracer(0.1, 0.0, 0.0);
        assert!(t.add_release(MeshRelease {
            x: 100.0,
            y: 50.0,
            start: 0.0,
            duration: 0.0,
            mass: 1.0,
            half_life: None,
            parcels: 1
        }));
        t.step_to(3000.0); // 300 m at 0.1 m/s, across three triangle edges
        let MeshPlace::In(_, x, y) = t.parcels[0].place else {
            panic!("left the mesh")
        };
        // Day files hold f32: 0.1 m/s is stored as 0.100000001, 4.5 µm over 3000 s.
        assert!(
            (x - 400.0).abs() < 1e-5 && (y - 50.0).abs() < 1e-9,
            "{x} {y}"
        );
    }

    #[test]
    fn a_wall_reflects_and_the_sea_takes_parcels_out() {
        // Across the channel at 0.1 m/s: it bounces off the sides, never leaving.
        let mut t = tracer(0.0, 0.1, 0.0);
        t.add_release(MeshRelease {
            x: 500.0,
            y: 100.0,
            start: 0.0,
            duration: 0.0,
            mass: 2.0,
            half_life: None,
            parcels: 4,
        });
        t.step_to(20_000.0);
        assert!((t.balance().in_water - 2.0).abs() < 1e-12);
        // Along it to the sea.
        let mut t = tracer(0.1, 0.0, 0.0);
        t.add_release(MeshRelease {
            x: 900.0,
            y: 100.0,
            start: 0.0,
            duration: 0.0,
            mass: 2.0,
            half_life: None,
            parcels: 4,
        });
        t.step_to(2_000.0);
        let b = t.balance();
        assert!((b.to_sea - 2.0).abs() < 1e-12 && b.in_water.abs() < 1e-12);
    }

    #[test]
    fn handoff_edges_pass_parcels_on_with_where_they_crossed() {
        let mut t = tracer(-0.1, 0.0, 0.0);
        t.add_release(MeshRelease {
            x: 50.0,
            y: 120.0,
            start: 0.0,
            duration: 0.0,
            mass: 1.0,
            half_life: None,
            parcels: 1,
        });
        t.step_to(1_000.0);
        let handed = t.take_handed();
        assert_eq!(handed.len(), 1);
        let (MeshFate::Handoff { id, x, y, .. }, m) = handed[0] else {
            panic!()
        };
        assert_eq!(id, 7);
        assert!(x.abs() < 1e-6 && (y - 120.0).abs() < 1e-6 && (m - 1.0).abs() < 1e-12);
        assert!((t.balance().handed_on - 1.0).abs() < 1e-12);
    }

    #[test]
    fn mass_is_conserved_under_spreading() {
        let mut t = tracer(0.05, 0.0, 5.0);
        t.add_release(MeshRelease {
            x: 500.0,
            y: 100.0,
            start: 0.0,
            duration: 3600.0,
            mass: 10.0,
            half_life: None,
            parcels: 2000,
        });
        t.step_to(30_000.0);
        let b = t.balance();
        let total = b.in_water + b.to_sea + b.handed_on;
        assert!((total - 10.0).abs() < 1e-9, "{b:?}");
        assert!((b.released - 10.0).abs() < 1e-9);
        assert!(b.to_sea > 0.0 && b.in_water > 0.0);
    }

    #[test]
    fn spreading_matches_diffusion_and_stays_even_over_a_sloping_bed() {
        // Still water over a bed sloping from 2 m to 12 m across the channel:
        // without the K∇H/H term parcels would collect in the shallows. With it,
        // an even start stays even (by volume): the share in the deeper half
        // stays at its volume share.
        let mut topo = channel(5.0);
        for (i, y) in topo.y.iter().enumerate() {
            topo.depth[i] = 2.0 + 10.0 * y / 200.0;
        }
        let n = topo.x.len();
        let mut t = MeshTracer::new(topo, 0.0, 3, 2.0);
        t.load_day(&day(n, 0.0, 0.0, 25)).unwrap();
        // Parcels placed by volume: proportional to depth across the channel.
        let mut rng = Rng::new(9);
        for _ in 0..20_000 {
            let (x, y) = loop {
                let y = rng.uniform() * 200.0;
                if rng.uniform() * 12.0 < 2.0 + 10.0 * y / 200.0 {
                    break (200.0 + rng.uniform() * 600.0, y);
                }
            };
            t.add_release(MeshRelease {
                x,
                y,
                start: 0.0,
                duration: 0.0,
                mass: 1.0,
                half_life: None,
                parcels: 1,
            });
        }
        let deep_share = |t: &MeshTracer| {
            let ys: Vec<f64> = t
                .parcels
                .iter()
                .filter_map(|p| match p.place {
                    MeshPlace::In(_, _, y) => Some(y),
                    MeshPlace::Gone(_) => None,
                })
                .collect();
            #[allow(clippy::cast_precision_loss)]
            let s = ys.iter().filter(|&&y| y > 100.0).count() as f64 / ys.len() as f64;
            s
        };
        // Volume share of the deeper half: mean depth 9.5 against 4.5.
        let expected = 9.5 / 14.0;
        assert!((deep_share(&t) - expected).abs() < 0.02);
        t.step_to(20_000.0);
        assert!(
            (deep_share(&t) - expected).abs() < 0.02,
            "deep share {}",
            deep_share(&t)
        );
    }
}
