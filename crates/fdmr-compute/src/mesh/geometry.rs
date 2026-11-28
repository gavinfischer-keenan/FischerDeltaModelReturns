//! The mesh itself (#83): its triangles and their sides, finding the
//! triangle holding a point, points and gradients within a triangle,
//! and following a straight move across triangles to where it ends or
//! leaves the mesh.

#[allow(clippy::wildcard_imports)]
use super::*;

impl MeshTracer {
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

    pub(super) fn bary(&self, t: usize, x: f64, y: f64) -> [f64; 3] {
        let [a, b, c] = self.topo.tri[t];
        let (xs, ys) = (&self.topo.x, &self.topo.y);
        let l0 = ((xs[b] - x) * (ys[c] - y) - (xs[c] - x) * (ys[b] - y)) / self.area2[t];
        let l1 = ((xs[c] - x) * (ys[a] - y) - (xs[a] - x) * (ys[c] - y)) / self.area2[t];
        [l0, l1, 1.0 - l0 - l1]
    }

    pub(super) fn inside(&self, t: usize, x: f64, y: f64) -> bool {
        self.bary(t, x, y).iter().all(|&l| l >= -1e-9)
    }

    /// Follow a straight move from (x, y) in triangle `t` by (dx, dy).
    pub(super) fn walk(
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

    pub(super) fn centroid(&self, t: usize) -> (f64, f64) {
        let n = self.topo.tri[t];
        (
            (self.topo.x[n[0]] + self.topo.x[n[1]] + self.topo.x[n[2]]) / 3.0,
            (self.topo.y[n[0]] + self.topo.y[n[1]] + self.topo.y[n[2]]) / 3.0,
        )
    }

    /// The point in triangle `t` with barycentric weights `w1`, `w2` for its
    /// second and third nodes (as `parcels()` gives them), m.
    #[must_use]
    pub fn point(&self, t: usize, w1: f64, w2: f64) -> (f64, f64) {
        let n = self.topo.tri[t];
        let w0 = 1.0 - w1 - w2;
        let (xs, ys) = (&self.topo.x, &self.topo.y);
        (
            w0 * xs[n[0]] + w1 * xs[n[1]] + w2 * xs[n[2]],
            w0 * ys[n[0]] + w1 * ys[n[1]] + w2 * ys[n[2]],
        )
    }
}

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

/// Edge `e` of a triangle runs from its node `e` to node `e + 1`.
pub(super) fn edge(t: &[usize; 3], e: usize) -> (usize, usize) {
    (t[e], t[(e + 1) % 3])
}

/// The gradient (∂/∂x, ∂/∂y) of a field linear over a triangle, from its
/// values `f` at the triangle's corners `n` (nodes at `xs`, `ys`), `area2`
/// twice the triangle's area.
pub(super) fn tri_gradient(
    xs: &[f64],
    ys: &[f64],
    n: [usize; 3],
    f: &[f64],
    area2: f64,
) -> (f64, f64) {
    let gx = (f[n[0]] * (ys[n[1]] - ys[n[2]])
        + f[n[1]] * (ys[n[2]] - ys[n[0]])
        + f[n[2]] * (ys[n[0]] - ys[n[1]]))
        / area2;
    let gy = (f[n[0]] * (xs[n[2]] - xs[n[1]])
        + f[n[1]] * (xs[n[0]] - xs[n[2]])
        + f[n[2]] * (xs[n[1]] - xs[n[0]]))
        / area2;
    (gx, gy)
}

/// A point anywhere in triangle `t`, evenly by area: two uniform draws,
/// folded back into the triangle where they fall in its mirror image.
pub(super) fn random_in_triangle(topo: &MeshTopology, t: usize, rng: &mut Rng) -> (f64, f64) {
    let (mut a, mut b) = (rng.uniform(), rng.uniform());
    if a + b > 1.0 {
        (a, b) = (1.0 - a, 1.0 - b);
    }
    let n = topo.tri[t];
    let (xs, ys) = (&topo.x, &topo.y);
    (
        xs[n[0]] + a * (xs[n[1]] - xs[n[0]]) + b * (xs[n[2]] - xs[n[0]]),
        ys[n[0]] + a * (ys[n[1]] - ys[n[0]]) + b * (ys[n[2]] - ys[n[0]]),
    )
}

pub(super) fn key(a: usize, b: usize) -> (usize, usize) {
    (a.min(b), a.max(b))
}

/// A bucket grid over the triangles' bounding boxes, for finding which holds a point.
pub(super) struct Grid {
    pub(super) x0: f64,
    pub(super) y0: f64,
    pub(super) cell: f64,
    pub(super) nx: usize,
    pub(super) ny: usize,
    pub(super) cells: Vec<Vec<usize>>,
}

impl Grid {
    pub(super) fn new(topo: &MeshTopology) -> Self {
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

    pub(super) fn near(&self, x: f64, y: f64) -> &[usize] {
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
