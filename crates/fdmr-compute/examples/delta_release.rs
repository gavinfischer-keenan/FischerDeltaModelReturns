//! Run a release through a published network reach, from the command line.
//!
//! `cargo run --release --example delta_release -- TOPOLOGY REACH_DIR CHANNEL START DAYS`
//!
//! TOPOLOGY is a plain text layout of the network (written from its geometry
//! by `tools/network_topology.py`, to keep the engine free of a JSON reader):
//!
//! ```text
//! channels <n>            then n lines: up down length_m
//! nodes <n>
//! sea <i> ...
//! pools <n>
//! links <n>               then n lines: pool node
//! ```
//!
//! Loads every day file in `REACH_DIR`, releases 100 kg at the upstream end of
//! channel index `CHANNEL` at `START` (s since 1970), and reports where the mass
//! has gone each day for `DAYS` days. A check on real published data, not part
//! of the app.

use std::{env, fs};

use fdmr_compute::network::{NetRelease, NetworkTracer, Place, Topology};

// File names are our own, always lower case.
#[allow(clippy::case_sensitive_file_extension_comparisons)]
fn main() {
    let a: Vec<String> = env::args().collect();
    let (topo_file, dir) = (&a[1], &a[2]);
    let channel: usize = a[3].parse().unwrap();
    let start: f64 = a[4].parse().unwrap();
    let days: u32 = a[5].parse().unwrap();

    let text = fs::read_to_string(topo_file).unwrap();
    let mut lines = text.lines();
    let count = |key: &str, lines: &mut std::str::Lines| -> usize {
        let l = lines.next().unwrap();
        assert!(l.starts_with(key), "expected {key}, got {l}");
        l.split_whitespace().nth(1).unwrap().parse().unwrap()
    };
    let n = count("channels", &mut lines);
    let (mut up, mut down, mut length) = (Vec::new(), Vec::new(), Vec::new());
    for _ in 0..n {
        let v: Vec<f64> = lines
            .next()
            .unwrap()
            .split_whitespace()
            .map(|x| x.parse().unwrap())
            .collect();
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        {
            up.push(v[0] as usize);
            down.push(v[1] as usize);
        }
        length.push(v[2]);
    }
    let nodes = count("nodes", &mut lines);
    let sea: Vec<usize> = lines
        .next()
        .unwrap()
        .split_whitespace()
        .skip(1)
        .map(|x| x.parse().unwrap())
        .collect();
    let pools = count("pools", &mut lines);
    let nl = count("links", &mut lines);
    let (mut link_pool, mut link_node) = (Vec::new(), Vec::new());
    for _ in 0..nl {
        let v: Vec<usize> = lines
            .next()
            .unwrap()
            .split_whitespace()
            .map(|x| x.parse().unwrap())
            .collect();
        link_pool.push(v[0]);
        link_node.push(v[1]);
    }
    let topo = Topology {
        up,
        down,
        length,
        nodes,
        sea,
        pools,
        link_pool,
        link_node,
    };
    let mut t = NetworkTracer::new(topo, start, 2027, 50.0);

    let mut files: Vec<_> = fs::read_dir(dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect();
    files.sort();
    for f in &files {
        let name = f.file_name().unwrap().to_string_lossy().to_string();
        let b = fs::read(f).unwrap();
        if name.ends_with(".nodes.fdf") {
            t.load_nodes(&b).unwrap();
        } else if name.ends_with(".links.fdf") {
            t.load_links(&b).unwrap();
        } else if name.ends_with(".fdf") {
            t.load_channels(&b).unwrap();
        }
    }
    t.add_release(NetRelease {
        at: Place::Channel(channel, 0.0),
        start,
        duration: 0.0,
        mass: 100.0,
        half_life: None,
        parcels: 20_000,
    });
    println!("day  in water  pumps  diversions  to sea   (kg)");
    for d in 1..=days {
        t.step_to(start + f64::from(d) * 86_400.0);
        let b = t.balance();
        println!(
            "{d:3}  {:8.1}  {:5.1}  {:10.1}  {:6.1}",
            b.in_water, b.exported, b.diverted, b.to_sea
        );
    }
}
