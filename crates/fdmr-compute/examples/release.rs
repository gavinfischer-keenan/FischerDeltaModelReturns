//! Run a release through a published cycle, from the command line.
//!
//!     cargo run --release --example release -- CYCLE_DIR X_M HOURS
//!
//! Reads CYCLE_DIR/geometry/river.json and every CYCLE_DIR/river/*.fdf, releases
//! 100 kg at chainage X_M at the start of the last full day, and reports where
//! the plume is after HOURS. A check on real published data, not part of the app.

use std::{env, fs};

use fdmr_compute::{Engine, Release};

fn main() {
    let args: Vec<String> = env::args().collect();
    let (dir, x, hours): (&str, f64, f64) = (&args[1], args[2].parse().unwrap(), args[3].parse().unwrap());
    let geo = fs::read_to_string(format!("{dir}/geometry/river.json")).unwrap();
    // A tiny reader for the elements array, to keep the engine free of a JSON dependency.
    let mut els = Vec::new();
    for part in geo.split("\"start_m\":").skip(1) {
        let start: f64 = part.split(',').next().unwrap().parse().unwrap();
        let end: f64 = part.split("\"end_m\":").nth(1).unwrap().split('}').next().unwrap().parse().unwrap();
        els.push((start, end));
    }
    let mut days: Vec<_> = fs::read_dir(format!("{dir}/river")).unwrap().map(|e| e.unwrap().path()).collect();
    days.sort();
    let mut engine = Engine::new(&els, 0.0, 1);
    for d in &days {
        engine.load_day(&fs::read(d).unwrap()).unwrap();
    }
    let name = days[days.len() - 2].file_stem().unwrap().to_string_lossy().to_string();
    let t0 = days_since_epoch(&name) * 86_400.0;
    engine = {
        let mut e = Engine::new(&els, t0, 1);
        for d in &days {
            e.load_day(&fs::read(d).unwrap()).unwrap();
        }
        e
    };
    engine.add_release(Release { x, start: t0, duration: 0.0, mass: 100.0, half_life: None, parcels: 20_000 });
    engine.step_to(t0 + hours * 3600.0);
    let (xs, _) = engine.parcel_snapshot();
    let n = xs.len() as f64;
    let mean = xs.iter().sum::<f64>() / n;
    let sd = (xs.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / n).sqrt();
    let peak = engine.concentration().iter().copied().fold(0.0, f64::max);
    let b = engine.mass_balance();
    println!("release at {x:.0} m on {name}; after {hours} h:");
    println!("  plume centre {:.1} km, spread (1 sd) {:.1} km, {} parcels in the water", mean / 1000.0, sd / 1000.0, xs.len());
    println!("  peak concentration {:.3} mg/L", peak * 1000.0);
    println!("  mass: released {:.1} kg, in water {:.1}, past the end {:.1}, decayed {:.1}", b.released, b.in_water, b.past_end, b.decayed);
}

fn days_since_epoch(ymd: &str) -> f64 {
    let p: Vec<i64> = ymd.split('-').map(|s| s.parse().unwrap()).collect();
    let (y, m, d) = (p[0], p[1], p[2]);
    // Days from civil date, Howard Hinnant's algorithm.
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    (era * 146_097 + doe - 719_468) as f64
}
