//! A pulse through a published network reach, watched at chosen channels: the
//! tracer's side of checking its spreading against DSM2's QUAL
//! (`tools/pulse_check.py` runs both and compares).
//!
//! `cargo run --release --example delta_pulse -- TOPOLOGY REACH_DIR CHANNEL START DURATION HOURS DISPERSION WATCH...`
//!
//! Releases 1 kg over DURATION s from START (s since 1970) at the upstream end
//! of channel index CHANNEL, with DISPERSION m²/s, and prints every 15 minutes
//! for HOURS hours the time and the share of the release in each WATCH channel
//! index. TOPOLOGY is as for the `delta_release` example.

use std::{env, fs};

use fdmr_compute::network::{NetRelease, NetworkTracer, Place, Topology};

fn numbers<T: std::str::FromStr>(line: &str, skip: usize) -> Vec<T>
where
    T::Err: std::fmt::Debug,
{
    line.split_whitespace()
        .skip(skip)
        .map(|x| x.parse().unwrap())
        .collect()
}

fn topology(text: &str) -> Topology {
    let mut lines = text.lines();
    let mut count = |key: &str| -> (usize, Vec<String>) {
        let l = lines.next().unwrap();
        assert!(l.starts_with(key), "expected {key}, got {l}");
        let n: usize = l.split_whitespace().nth(1).unwrap().parse().unwrap();
        let body = (0..if key == "nodes" || key == "pools" {
            0
        } else {
            n
        })
            .map(|_| lines.next().unwrap().to_string())
            .collect();
        (n, body)
    };
    let (_, chans) = count("channels");
    let (nodes, _) = count("nodes");
    let sea_line = lines.next().unwrap().to_string();
    let mut lines2 = lines;
    let pools: usize = lines2
        .next()
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap()
        .parse()
        .unwrap();
    let nl: usize = lines2
        .next()
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap()
        .parse()
        .unwrap();
    let links: Vec<Vec<usize>> = (0..nl)
        .map(|_| numbers(lines2.next().unwrap(), 0))
        .collect();
    let mut t = Topology {
        up: Vec::new(),
        down: Vec::new(),
        length: Vec::new(),
        nodes,
        sea: numbers(&sea_line, 1),
        pools,
        link_pool: links.iter().map(|v| v[0]).collect(),
        link_node: links.iter().map(|v| v[1]).collect(),
    };
    for c in &chans {
        let v: Vec<f64> = numbers(c, 0);
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        {
            t.up.push(v[0] as usize);
            t.down.push(v[1] as usize);
        }
        t.length.push(v[2]);
    }
    t
}

// File names are our own, always lower case.
#[allow(clippy::case_sensitive_file_extension_comparisons)]
fn main() {
    let a: Vec<String> = env::args().collect();
    let topo = topology(&fs::read_to_string(&a[1]).unwrap());
    let channel: usize = a[3].parse().unwrap();
    let start: f64 = a[4].parse().unwrap();
    let duration: f64 = a[5].parse().unwrap();
    let hours: u32 = a[6].parse().unwrap();
    let dispersion: f64 = a[7].parse().unwrap();
    let watch: Vec<u32> = a[8..].iter().map(|x| x.parse().unwrap()).collect();

    let mut t = NetworkTracer::new(topo, start, 2027, dispersion);
    let mut files: Vec<_> = fs::read_dir(&a[2])
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
        } else if name.ends_with(".fdf") && !name.ends_with(".quality.fdf") {
            t.load_channels(&b).unwrap();
        }
    }
    let parcels = 50_000;
    t.add_release(NetRelease {
        at: Place::Channel(channel, 0.0),
        start,
        duration,
        mass: 1.0,
        half_life: None,
        parcels,
    });
    #[allow(clippy::cast_precision_loss)]
    let share = 1.0 / parcels as f64;
    for k in 1..=hours * 4 {
        let now = start + f64::from(k) * 900.0;
        t.step_to(now);
        let (el, _) = t.parcels();
        let row: Vec<String> = watch
            .iter()
            .map(|w| {
                #[allow(clippy::cast_precision_loss)]
                let n = el.iter().filter(|e| *e == w).count() as f64;
                format!("{:.6}", n * share)
            })
            .collect();
        println!("{now:.0} {}", row.join(" "));
    }
}
