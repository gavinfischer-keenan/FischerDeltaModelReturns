//! A release on a published mesh reach, from the command line: a check of the
//! 2D tracer on real data, not part of the app.
//!
//! `cargo run --release --example bay_release -- MESH_TXT REACH_DIR X Y START HOURS DIFFUSIVITY`
//!
//! `MESH_TXT` is the mesh as plain text (written from its geometry by
//! `tools/mesh_text.py`): `nodes n` then n lines `x y depth`; `triangles n` then
//! n lines `a b c`; `open n` then n lines `a b`; `handoff n` then n lines
//! `a b id`. Releases 100 kg at (X, Y) (m, the mesh's projection) at START
//! (s since 1970) and prints, every 6 hours for HOURS hours, where the mass is
//! and how far the parcels in the water are, on average, from the release.

use std::{env, fs};

use fdmr_compute::mesh::{MeshRelease, MeshTopology, MeshTracer};

// File names are our own, always lower case.
#[allow(clippy::case_sensitive_file_extension_comparisons)]
fn main() {
    let a: Vec<String> = env::args().collect();
    let text = fs::read_to_string(&a[1]).unwrap();
    let mut lines = text.lines();
    let mut section = |key: &str| -> Vec<Vec<f64>> {
        let head = lines.next().unwrap();
        assert!(head.starts_with(key), "expected {key}, got {head}");
        let n: usize = head.split_whitespace().nth(1).unwrap().parse().unwrap();
        (0..n)
            .map(|_| {
                lines
                    .next()
                    .unwrap()
                    .split_whitespace()
                    .map(|x| x.parse().unwrap())
                    .collect()
            })
            .collect()
    };
    let nodes = section("nodes");
    let tris = section("triangles");
    let open = section("open");
    let handoff = section("handoff");
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    let ix = |v: f64| v as usize;
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    let topo = MeshTopology {
        x: nodes.iter().map(|n| n[0]).collect(),
        y: nodes.iter().map(|n| n[1]).collect(),
        depth: nodes.iter().map(|n| n[2]).collect(),
        tri: tris
            .iter()
            .map(|t| [ix(t[0]), ix(t[1]), ix(t[2])])
            .collect(),
        open: open.iter().map(|e| (ix(e[0]), ix(e[1]))).collect(),
        handoff: handoff
            .iter()
            .map(|e| (ix(e[0]), ix(e[1]), e[2] as u32))
            .collect(),
    };
    let (x, y): (f64, f64) = (a[3].parse().unwrap(), a[4].parse().unwrap());
    let start: f64 = a[5].parse().unwrap();
    let hours: u32 = a[6].parse().unwrap();
    let k: f64 = a[7].parse().unwrap();
    let mut t = MeshTracer::new(topo, start, 2028, k);
    let mut files: Vec<_> = fs::read_dir(&a[2])
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| {
            let n = p.file_name().unwrap().to_string_lossy().to_string();
            n.ends_with(".fdf") && !n.ends_with(".quality.fdf")
        })
        .collect();
    files.sort();
    for f in &files {
        t.load_day(&fs::read(f).unwrap()).unwrap();
    }
    assert!(
        t.add_release(MeshRelease {
            x,
            y,
            start,
            duration: 0.0,
            mass: 100.0,
            half_life: None,
            parcels: 5_000,
        }),
        "the release point is not on the mesh"
    );
    println!("hour  in water  to sea  handed on   (kg)");
    for h in (6..=hours).step_by(6) {
        t.step_to(start + f64::from(h) * 3600.0);
        let b = t.balance();
        println!(
            "{h:4}  {:8.1}  {:6.1}  {:9.1}",
            b.in_water, b.to_sea, b.handed_on
        );
    }
}
