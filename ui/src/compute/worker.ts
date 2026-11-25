// The compute host: runs the compute engine (WebAssembly) in a web worker, the
// engine's background thread (specification section 4.2). It fetches the
// published field files itself and answers the UI's requests with snapshots.
// No drawing happens here.

import init, { Engine } from "./pkg/fdmr_wasm.js";
import type { Frame, Reply, Request, Words } from "../compute-interface/messages";

interface Index {
  time: { start: string; step_s: number; days: string[] };
  now: string;
  reaches: { id: string; certainty: number[] }[];
}
interface Geometry {
  elements: { start_m: number; end_m: number }[];
}
interface PendingRelease {
  x: number;
  start: number;
  duration: number;
  mass: number;
  halfLife: number | null;
}

const PARCELS_PER_RELEASE = 20_000;
let engine: Engine | null = null;
let days: Uint8Array[] = [];
let starts = new Float64Array();
let ends = new Float64Array();
let particles = 0;
let releases: PendingRelease[] = [];
let certainty: number[] = [];
let start = 0;
let step = 900;
let now = 0;

const post = (reply: Reply, transfer: Transferable[] = []) =>
  (self as unknown as Worker).postMessage(reply, transfer);
const epoch = (iso: string) => Date.parse(iso.endsWith("Z") ? iso : iso + "Z") / 1000;

async function open(cycle: string, reach: string, n: number): Promise<void> {
  await init();
  const [index, geometry] = (await Promise.all([
    fetch(`${cycle}/index.json`).then((r) => r.json()),
    fetch(`${cycle}/geometry/${reach}.json`).then((r) => r.json()),
  ])) as [Index, Geometry];
  starts = Float64Array.from(geometry.elements.map((e) => e.start_m));
  ends = Float64Array.from(geometry.elements.map((e) => e.end_m));
  days = await Promise.all(
    index.time.days.map((d) =>
      fetch(`${cycle}/${reach}/${d}.fdf`)
        .then((r) => r.arrayBuffer())
        .then((b) => new Uint8Array(b)),
    ),
  );
  particles = n;
  now = epoch(index.now);
  start = epoch(index.time.start);
  step = index.time.step_s;
  certainty = index.reaches.find((x) => x.id === reach)?.certainty ?? [1];
  build(now);
  const end = start + (certainty.length - 1) * step; // the last published step
  post({ type: "ready", start, end, now, length: engine!.length(), certainty, step });
  frame();
}

/** A fresh engine at time t, with every release replayed up to t. */
function build(t: number): void {
  engine?.free();
  const first = Math.min(t, ...releases.map((r) => r.start));
  const e = new Engine(starts, ends, first, 2026);
  for (const d of days) e.load_day(d);
  for (const r of releases) e.add_release(r.x, r.start, r.duration, r.mass, r.halfLife ?? NaN, PARCELS_PER_RELEASE);
  e.step_to(t);
  e.seed_flow_particles(particles);
  engine = e;
}

/** Certainty at time t. One reach, and certainty never rises with time, so a
 * parcel's lowest certainty so far is the reach's certainty now (spec 8.2). */
function certaintyAt(t: number): number {
  const i = Math.max(0, Math.min(certainty.length - 1, Math.round((t - start) / step)));
  return certainty[i] ?? 1;
}

/** The three tiers of section 3.4, as in fdmr-math's certainty::words. */
function words(c: number, t: number): Words {
  if (t <= now) return "measured";
  return c >= 0.5 ? "forecast" : "rough_guide";
}

function frame(): void {
  if (!engine) return;
  const t = engine.time();
  const c = certaintyAt(t);
  const f: Frame = {
    type: "frame",
    t,
    particleX: engine.particle_x(),
    particleLateral: engine.particle_lateral(),
    particleSpeed: engine.particle_speed(),
    parcelX: engine.parcel_x(),
    parcelConcentration: engine.parcel_concentration(),
    massBalance: engine.mass_balance(),
    certainty: c,
    words: words(c, t),
  };
  post(f, [f.particleX.buffer, f.particleLateral.buffer, f.particleSpeed.buffer, f.parcelX.buffer, f.parcelConcentration.buffer]);
}

self.addEventListener("message", async (e: MessageEvent<Request>) => {
  const r = e.data;
  try {
    switch (r.type) {
      case "open":
        await open(r.cycle, r.reach, r.particles);
        return;
      case "seek":
        build(r.t);
        break;
      case "advance":
        if (engine && r.to < engine.time()) build(r.to);
        else engine?.step_to(r.to);
        break;
      case "drift":
        engine?.drift_frozen(r.dt);
        break;
      case "release":
        releases.push({ x: r.x, start: r.start, duration: r.duration, mass: r.mass, halfLife: r.halfLife });
        build(engine ? engine.time() : r.start);
        break;
      case "clear":
        releases = [];
        build(engine ? engine.time() : 0);
        break;
    }
    frame();
  } catch (err) {
    post({ type: "error", message: String(err) });
  }
});
