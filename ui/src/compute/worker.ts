// The compute host: runs the compute engine (WebAssembly) in a web worker, the
// engine's background thread (specification section 4.2). It fetches the
// published field files itself and answers the UI's requests with snapshots.
// No drawing happens here.
//
// A region has a river reach (a line) and may have network reaches (a tidal
// delta) and mesh reaches (a bay). Each has its own engine; they run in step,
// a few minutes of model time at a time, and pass parcels between them where
// the index joins them (specification 7.4): from the river's end into a
// network at a junction; between a network and a mesh both ways, where the
// mesh covers some of the network's channels (index.json `handoffs`). Each
// parcel belongs to one reach at a time, with its mass, so the total balances.

import init, { Engine, MeshEngine, NetworkEngine } from "./pkg/fdmr_wasm.js";
import type { Frame, Layer, MeshFrame, NetworkFrame, Reply, Request, Words } from "../compute-interface/messages";

interface ReachEntry {
  id: string;
  certainty: number[];
  network?: boolean;
  dims?: number;
  time?: { days: string[] };
  quality_fields?: string[];
}
interface Junction {
  node: number;
  element: number;
  fraction: number;
  x: number;
  y: number;
}
interface Handoff {
  from: string;
  to: string;
  node?: number;
  /** The water-source group the giving reach's water belongs to. */
  source?: string;
  owned?: number[];
  junctions?: Junction[];
}
interface Index {
  time: { start: string; step_s: number; days: string[] };
  now: string;
  reaches: ReachEntry[];
  handoffs?: Handoff[];
}
interface LineGeometry {
  elements: { start_m: number; end_m: number }[];
}
interface NetworkGeometry {
  elements: { id: number; up: number; down: number; end_m: number }[];
  nodes: { id: number; kind?: string }[];
  pools?: { id: string }[];
  links?: { pool: string; node: number }[];
}
interface MeshGeometry {
  x: number[];
  y: number[];
  depth_m: number[];
  triangles: [number, number, number][];
  edges: Record<string, [number, number][]>;
}
interface MeshRelease {
  triangle: number;
  w1: number;
  w2: number;
  start: number;
  duration: number;
  mass: number;
  halfLife: number | null;
  parcels: number;
}
interface RiverRelease {
  x: number;
  start: number;
  duration: number;
  mass: number;
  halfLife: number | null;
}
interface NetRelease {
  element: number;
  fraction: number;
  start: number;
  duration: number;
  mass: number;
  halfLife: number | null;
  parcels: number;
}

const PARCELS_PER_RELEASE = 20_000;
const NETWORK_PARTICLES = 6_000;
// Spreading in the network's channels, m²/s. Checked 25 Nov 2026 against DSM2's
// own transport (QUAL) with a pulse released at the network's main inflow
// (tools/pulse_check.py; build plan, network tracer): at 50 the tracer matches
// QUAL's arrival, spread and peak at five channels across the network; 10 and
// 200 are clearly worse, 30 and 80 about as good.
const NETWORK_DISPERSION = 50;
const MESH_PARTICLES = 5_000;
// Horizontal diffusivity on a mesh reach, m²/s: a placeholder, to be checked as
// the network's spreading was. It stands for mixing the depth-averaged mesh
// does not resolve: eddies smaller than its triangles (about 0.1 to 2 m²/s at
// 0.1 to 2.5 km by Okubo's 1971 diffusion diagram) and the spreading the
// averaged-out vertical shear would cause (Elder's 5.93 H u*, about 3 m²/s for
// 10 m of water and u* = 0.05 m/s, along the flow).
const MESH_DIFFUSIVITY = 2;
// Reaches pass parcels to each other this often, s of model time. A parcel
// handed back waits at most this long at the join.
const EXCHANGE_S = 300;

let engine: Engine | null = null;
let days: Uint8Array[] = [];
let starts = new Float64Array();
let ends = new Float64Array();
let particles = 0;
let riverReleases: RiverRelease[] = [];
let certainty: number[] = [];
let start = 0;
let step = 900;
let now = 0;
let index: Index | null = null;
let cycleUrl = "";
let layer: Layer = "plain";
/** Water sources the networks carry shares of (from `source:<name>` fields). */
let sources: string[] = [];
let riverId = "river";
/** Mass passed into another reach since the engines were built, kg: counted
 * there as released, so taken off the total once. */
let handedIn = 0;

interface Net {
  id: string;
  topo: {
    up: Uint32Array;
    down: Uint32Array;
    length: Float64Array;
    nodes: number;
    sea: Uint32Array;
    pools: number;
    linkPool: Uint32Array;
    linkNode: Uint32Array;
  };
  channels: Uint8Array[];
  nodeDays: Uint8Array[];
  linkDays: Uint8Array[];
  /** Channel where parcels from the river enter, if this network takes them. */
  entry: number | null;
  releases: NetRelease[];
  engine: NetworkEngine | null;
  days: string[];
  /** Water-quality day files, fetched when a layer first needs them. */
  quality: Uint8Array[] | null;
}
const nets: Net[] = [];

interface Mesh {
  id: string;
  x: Float64Array;
  y: Float64Array;
  depth: Float64Array;
  triangles: Uint32Array;
  open: Uint32Array;
  handoff: Uint32Array;
  handoffId: Uint32Array;
  /** The reach each handoff id leads to. */
  handoffTo: string[];
  days: Uint8Array[];
  releases: MeshRelease[];
  engine: MeshEngine | null;
  dayNames: string[];
  quality: Uint8Array[] | null;
}
const meshes: Mesh[] = [];

const post = (reply: Reply, transfer: Transferable[] = []) =>
  (self as unknown as Worker).postMessage(reply, transfer);
const epoch = (iso: string) => Date.parse(iso.endsWith("Z") ? iso : iso + "Z") / 1000;
const bytes = async (url: string): Promise<Uint8Array | null> => {
  const r = await fetch(url);
  return r.ok ? new Uint8Array(await r.arrayBuffer()) : null;
};
const present = (list: (Uint8Array | null)[]): Uint8Array[] => list.filter((b): b is Uint8Array => b !== null);
const hl = (h: number | null) => h ?? NaN;

async function open(cycle: string, reach: string, n: number): Promise<void> {
  await init();
  const [ix, geometry] = (await Promise.all([
    fetch(`${cycle}/index.json`).then((r) => r.json()),
    fetch(`${cycle}/geometry/${reach}.json`).then((r) => r.json()),
  ])) as [Index, LineGeometry];
  index = ix;
  riverId = reach;
  cycleUrl = cycle;
  sources = [...new Set(ix.reaches.flatMap((r) => (r.quality_fields ?? []).filter((f) => f.startsWith("source:")).map((f) => f.slice(7))))];
  starts = Float64Array.from(geometry.elements.map((e) => e.start_m));
  ends = Float64Array.from(geometry.elements.map((e) => e.end_m));
  days = present(await Promise.all(ix.time.days.map((d) => bytes(`${cycle}/${reach}/${d}.fdf`))));
  particles = n;
  now = epoch(ix.now);
  start = epoch(ix.time.start);
  step = ix.time.step_s;
  certainty = ix.reaches.find((x) => x.id === reach)?.certainty ?? [1];
  build(now);
  const end = start + (certainty.length - 1) * step; // the last published step
  const netReaches = ix.reaches.filter((r) => r.network);
  const meshReaches = ix.reaches.filter((r) => r.dims === 2);
  post({ type: "ready", start, end, now, length: engine!.length(), certainty, step, networks: netReaches.map((r) => r.id), meshes: meshReaches.map((r) => r.id) });
  frame();
  // Network and mesh reaches load in the background: the river is ready to play first.
  for (const r of netReaches) void loadNetwork(cycle, r.id, r.time?.days ?? ix.time.days);
  for (const r of meshReaches) void loadMesh(cycle, r.id, r.time?.days ?? ix.time.days);
}

async function loadNetwork(cycle: string, id: string, netDays: string[]): Promise<void> {
  const g = (await fetch(`${cycle}/geometry/${id}.json`).then((r) => r.json())) as NetworkGeometry;
  const nodeIndex = new Map(g.nodes.map((n, i) => [n.id, i]));
  const poolIndex = new Map((g.pools ?? []).map((p, i) => [p.id, i]));
  const ix = (k: number) => nodeIndex.get(k) ?? 0;
  const [channels, nodeDays, linkDays] = await Promise.all(
    ["", ".nodes", ".links"].map((suffix) =>
      Promise.all(netDays.map((d) => bytes(`${cycle}/${id}/${d}${suffix}.fdf`))).then(present),
    ),
  );
  const entryNode = index?.handoffs?.find((h) => h.from === riverId && h.to === id)?.node;
  const entry = entryNode === undefined ? -1 : g.elements.findIndex((e) => e.up === entryNode);
  nets.push({
    id,
    topo: {
      up: Uint32Array.from(g.elements.map((e) => ix(e.up))),
      down: Uint32Array.from(g.elements.map((e) => ix(e.down))),
      length: Float64Array.from(g.elements.map((e) => e.end_m)),
      nodes: g.nodes.length,
      sea: Uint32Array.from(g.nodes.flatMap((n, i) => (n.kind === "stage_boundary" ? [i] : []))),
      pools: g.pools?.length ?? 0,
      linkPool: Uint32Array.from((g.links ?? []).map((l) => poolIndex.get(l.pool) ?? 0)),
      linkNode: Uint32Array.from((g.links ?? []).map((l) => ix(l.node))),
    },
    channels,
    nodeDays,
    linkDays,
    entry: entry >= 0 ? entry : null,
    releases: [],
    engine: null,
    days: netDays,
    quality: null,
  });
  if (QUALITY_LAYERS.has(layer)) await loadQuality();
  // Play the river's (and any other reach's) handoffs into it from the start.
  build(engine ? engine.time() : now);
  frame();
}

async function loadMesh(cycle: string, id: string, meshDays: string[]): Promise<void> {
  const g = (await fetch(`${cycle}/geometry/${id}.json`).then((r) => r.json())) as MeshGeometry;
  const handoffs = Object.entries(g.edges).filter(([k]) => k !== "open");
  meshes.push({
    id,
    x: Float64Array.from(g.x),
    y: Float64Array.from(g.y),
    depth: Float64Array.from(g.depth_m),
    triangles: Uint32Array.from(g.triangles.flat()),
    open: Uint32Array.from((g.edges.open ?? []).flat()),
    handoff: Uint32Array.from(handoffs.flatMap(([, es]) => es.flat())),
    handoffId: Uint32Array.from(handoffs.flatMap(([, es], k) => es.map(() => k))),
    handoffTo: handoffs.map(([k]) => k),
    days: present(await Promise.all(meshDays.map((d) => bytes(`${cycle}/${id}/${d}.fdf`)))),
    releases: [],
    engine: null,
    dayNames: meshDays,
    quality: null,
  });
  if (QUALITY_LAYERS.has(layer)) await loadQuality();
  build(engine ? engine.time() : now);
  frame();
}

/** Layers that need the water-quality files. */
const QUALITY_LAYERS = new Set<Layer>(["temperature", "salinity", "source"]);

/** Fetch the water-quality day files of every reach not holding them yet, and load them. */
async function loadQuality(): Promise<void> {
  const get = (id: string, ds: string[]) => Promise.all(ds.map((d) => bytes(`${cycleUrl}/${id}/${d}.quality.fdf`))).then(present);
  await Promise.all([
    ...nets.filter((n) => !n.quality).map(async (n) => {
      n.quality = await get(n.id, n.days);
      for (const b of n.quality) n.engine?.load_quality(b);
    }),
    ...meshes.filter((m) => !m.quality).map(async (m) => {
      m.quality = await get(m.id, m.dayNames);
      for (const b of m.quality) m.engine?.load_quality(b);
    }),
  ]);
}

/** The join between a network and a mesh, when both are loaded. */
function join(net: Net, mesh: Mesh): Handoff | undefined {
  return index?.handoffs?.find((h) => h.from === net.id && h.to === mesh.id && h.junctions);
}

/** Fresh engines at time t: every release replayed from its start, the reaches
 * run together and passing parcels as they go. */
function build(t: number): void {
  engine?.free();
  for (const net of nets) net.engine?.free();
  for (const m of meshes) m.engine?.free();
  const first = Math.min(
    t,
    ...riverReleases.map((r) => r.start),
    ...nets.flatMap((n) => n.releases.map((r) => r.start)),
    ...meshes.flatMap((m) => m.releases.map((r) => r.start)),
  );
  const e = new Engine(starts, ends, first, 2026);
  for (const d of days) e.load_day(d);
  for (const r of riverReleases) e.add_release(r.x, r.start, r.duration, r.mass, hl(r.halfLife), PARCELS_PER_RELEASE);
  engine = e;
  for (const net of nets) {
    const T = net.topo;
    const ne = new NetworkEngine(T.up, T.down, T.length, T.nodes, T.sea, T.pools, T.linkPool, T.linkNode, first, 2027, NETWORK_DISPERSION);
    for (const b of net.channels) ne.load_channels(b);
    for (const b of net.nodeDays) ne.load_nodes(b);
    for (const b of net.linkDays) ne.load_links(b);
    for (const r of net.releases) ne.add_release(r.element, r.fraction, T.length[r.element], r.start, r.duration, r.mass, hl(r.halfLife), r.parcels);
    for (const b of net.quality ?? []) ne.load_quality(b);
    const owned = index?.handoffs?.filter((h) => h.from === net.id && h.owned && meshes.some((m) => m.id === h.to)).flatMap((h) => h.owned!) ?? [];
    ne.set_handoff(Uint32Array.from(owned));
    net.engine = ne;
  }
  for (const m of meshes) {
    const me = new MeshEngine(m.x, m.y, m.depth, m.triangles, m.open, m.handoff, m.handoffId, first, 2028, MESH_DIFFUSIVITY);
    for (const b of m.days) me.load_day(b);
    for (const r of m.releases) me.add_release(r.triangle, r.w1, r.w2, r.start, r.duration, r.mass, hl(r.halfLife), r.parcels);
    for (const b of m.quality ?? []) me.load_quality(b);
    m.engine = me;
  }
  handedIn = 0;
  run(t);
  e.seed_flow_particles(particles);
  for (const net of nets) net.engine!.seed_flow_particles(NETWORK_PARTICLES);
  for (const m of meshes) m.engine!.seed_flow_particles(MESH_PARTICLES);
}

/** Run every reach on to `to` together, passing parcels across every join. */
function run(to: number): void {
  if (!engine) return;
  while (engine.time() < to) {
    const t = Math.min(to, engine.time() + EXCHANGE_S);
    engine.step_to(t);
    // The river's end into the networks that take it.
    const out = engine.take_outflow();
    for (const net of nets) {
      if (net.entry === null) continue;
      for (let i = 0; i + 2 < out.length; i += 3) {
        net.engine!.add_release(net.entry, 0, net.topo.length[net.entry], out[i], 0, out[i + 1], out[i + 2], 1);
        handedIn += out[i + 1];
      }
    }
    // Networks on, then into the meshes that track some of their channels.
    for (const net of nets) {
      net.engine!.step_to(t);
      const h = net.engine!.take_handed(); // (junction, time, mass, half-life) ...
      for (const m of meshes) {
        const j = join(net, m);
        if (!j) continue;
        for (let i = 0; i + 3 < h.length; i += 4) {
          const at = j.junctions!.find((x) => x.node === h[i]);
          if (at && m.engine!.take_parcel(at.x, at.y, h[i + 2], h[i + 1], h[i + 3])) handedIn += h[i + 2];
        }
      }
    }
    // Meshes on, then back into the networks at the nearest junction of the join.
    for (const m of meshes) {
      m.engine!.step_to(t);
      const h = m.engine!.take_handed(); // (handoff id, x, y, time, mass, half-life) ...
      for (let i = 0; i + 5 < h.length; i += 6) {
        const net = nets.find((n) => n.id === m.handoffTo[h[i]]);
        const j = net && join(net, m);
        if (!net || !j || !j.junctions!.length) continue;
        let best = j.junctions![0];
        let d = Infinity;
        for (const x of j.junctions!) {
          const dd = (x.x - h[i + 1]) ** 2 + (x.y - h[i + 2]) ** 2;
          if (dd < d) {
            d = dd;
            best = x;
          }
        }
        net.engine!.take_parcel(best.node, h[i + 4], h[i + 5]);
        handedIn += h[i + 4];
      }
    }
  }
}

/** Play on to `to`. Never backwards: going back is a seek, a full rebuild, and
 * a request computed from an older frame must not cause one. */
function advance(to: number): void {
  if (!engine || to <= engine.time()) return;
  run(to);
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

/** Every reach's mass balance together (see Frame.massBalance). Mass passed
 * from one reach to another is counted as released once. */
function combinedBalance(networks: NetworkFrame[], meshFrames: MeshFrame[]): Float64Array {
  const [rReleased, rIn, rPast, rDecayed] = engine!.mass_balance();
  const taken = nets.some((n) => n.entry !== null && n.engine);
  const out = new Float64Array(8);
  out[0] = rReleased - handedIn;
  out[1] = rIn;
  out[2] = taken ? 0 : rPast; // handed on, it is counted in the network instead
  out[3] = rDecayed;
  for (const n of networks) {
    const [released, inWater, exported, diverted, toSea, decayed] = n.massBalance;
    out[0] += released;
    out[1] += inWater;
    out[3] += decayed;
    out[4] += exported;
    out[5] += diverted;
    out[6] += toSea;
  }
  for (const m of meshFrames) {
    const [released, inWater, toSea, , decayed] = m.massBalance;
    out[0] += released;
    out[1] += inWater;
    out[3] += decayed;
    out[7] += toSea;
  }
  return out;
}

/** A field's values centred on their mean: water level "either side of mean level" (spec 8.4). */
function centred(v: Float32Array): Float32Array {
  let s = 0, n = 0;
  for (const x of v) if (Number.isFinite(x)) { s += x; n++; }
  const m = n ? s / n : 0;
  return v.map((x) => x - m);
}

/** The field a layer shows, by its name in the published files. */
const FIELD: Partial<Record<Layer, string>> = { temperature: "temperature", salinity: "salinity", level: "level" };

/** Shares of each water source, all particles of a network, interleaved. */
function networkShares(e: NetworkEngine): Float32Array {
  const per = sources.map((s) => e.particle_values(`source:${s}`));
  const n = per[0]?.length ?? 0;
  const out = new Float32Array(n * sources.length);
  per.forEach((v, j) => v.forEach((x, i) => (out[i * sources.length + j] = x)));
  return out;
}

/** The river's water is all of the source group its handoff names (index.json handoffs). */
function riverShares(n: number): Float32Array {
  const out = new Float32Array(n * sources.length).fill(0);
  const group = index?.handoffs?.find((h) => h.from === riverId && h.source)?.source;
  const j = group ? sources.indexOf(group) : -1;
  if (j >= 0) for (let i = 0; i < n; i++) out[i * sources.length + j] = 1;
  else out.fill(NaN);
  return out;
}

function riverLayer(n: number): { particleValue?: Float32Array; particleShares?: Float32Array } {
  if (layer === "speed") return { particleValue: Float32Array.from(engine!.particle_speed()) };
  if (layer === "source") return { particleShares: riverShares(n) };
  if (FIELD[layer]) return { particleValue: new Float32Array(n).fill(NaN) }; // not modelled on the river yet
  return {};
}

function networkLayer(e: NetworkEngine): { particleValue?: Float32Array; particleShares?: Float32Array } {
  if (layer === "speed") {
    const v = e.velocities();
    return { particleValue: Float32Array.from(e.particle_element(), (k) => (k < v.length ? Math.abs(v[k]) : NaN)) };
  }
  if (layer === "source") return { particleShares: networkShares(e) };
  const f = FIELD[layer];
  if (!f) return {};
  const v = e.particle_values(f);
  return { particleValue: layer === "level" ? centred(v) : v };
}

function meshLayer(e: MeshEngine): { particleValue?: Float32Array; particleShares?: Float32Array } {
  if (layer === "speed") return { particleValue: e.particle_speed() };
  const n = e.particle_triangle().length;
  if (layer === "source") return { particleShares: new Float32Array(n * Math.max(1, sources.length)).fill(NaN) }; // the Bay's model tracks no sources
  const f = FIELD[layer];
  if (!f) return {};
  const v = e.particle_values(f);
  return { particleValue: layer === "level" ? centred(v) : v };
}

function frame(seq?: number): void {
  if (!engine) return;
  const t = engine.time();
  const c = certaintyAt(t);
  const networks: NetworkFrame[] = nets
    .filter((n) => n.engine)
    .map((n) => {
      const e = n.engine!;
      return {
        id: n.id,
        velocity: e.velocities(),
        particleElement: e.particle_element(),
        particleFraction: e.particle_fraction(),
        parcelElement: e.parcel_element(),
        parcelFraction: e.parcel_fraction(),
        parcelConcentration: e.parcel_concentration(),
        massBalance: e.mass_balance(),
        ...networkLayer(e),
      };
    });
  const meshFrames: MeshFrame[] = meshes
    .filter((m) => m.engine)
    .map((m) => {
      const e = m.engine!;
      return {
        id: m.id,
        particleTriangle: e.particle_triangle(),
        particleWeights: e.particle_weights(),
        particleSpeed: e.particle_speed(),
        parcelTriangle: e.parcel_triangle(),
        parcelWeights: e.parcel_weights(),
        parcelConcentration: e.parcel_concentration(),
        massBalance: e.mass_balance(),
        ...meshLayer(e),
      };
    });
  const riverN = engine.particle_x().length;
  const f: Frame = {
    type: "frame",
    seq,
    t,
    particleX: engine.particle_x(),
    particleLateral: engine.particle_lateral(),
    particleSpeed: engine.particle_speed(),
    parcelX: engine.parcel_x(),
    parcelConcentration: engine.parcel_concentration(),
    massBalance: combinedBalance(networks, meshFrames),
    certainty: c,
    words: words(c, t),
    networks,
    meshes: meshFrames,
    layer,
    sources,
    ...riverLayer(riverN),
  };
  const transfer: Transferable[] = [f.particleX.buffer, f.particleLateral.buffer, f.particleSpeed.buffer, f.parcelX.buffer, f.parcelConcentration.buffer];
  for (const n of networks) {
    transfer.push(n.velocity.buffer, n.particleElement.buffer, n.particleFraction.buffer, n.parcelElement.buffer, n.parcelFraction.buffer, n.parcelConcentration.buffer);
  }
  for (const m of meshFrames) {
    transfer.push(m.particleTriangle.buffer, m.particleWeights.buffer, m.particleSpeed.buffer, m.parcelTriangle.buffer, m.parcelWeights.buffer, m.parcelConcentration.buffer);
  }
  for (const x of [f, ...networks, ...meshFrames]) {
    if (x.particleValue) transfer.push(x.particleValue.buffer);
    if (x.particleShares) transfer.push(x.particleShares.buffer);
  }
  post(f, transfer);
}

self.addEventListener("message", async (e: MessageEvent<Request>) => {
  const r = e.data;
  try {
    switch (r.type) {
      case "open":
        await open(r.cycle, r.reach, r.particles);
        frame(r.seq);
        return;
      case "seek":
        build(r.t);
        break;
      case "advance":
        advance(r.to);
        break;
      case "drift":
        engine?.drift_frozen(r.dt);
        for (const net of nets) net.engine?.drift_frozen(r.dt);
        for (const m of meshes) m.engine?.drift_frozen(r.dt);
        break;
      case "release": {
        const base = { start: r.start, duration: r.duration, mass: r.mass, halfLife: r.halfLife };
        const net = r.network ? nets.find((n) => n.id === r.network!.id) : undefined;
        const mesh = r.mesh ? meshes.find((m) => m.id === r.mesh!.id) : undefined;
        if (r.mesh && mesh) {
          mesh.releases.push({ ...base, triangle: r.mesh.triangle, w1: r.mesh.w1, w2: r.mesh.w2, parcels: PARCELS_PER_RELEASE });
        } else if (r.network && net) {
          net.releases.push({ ...base, element: r.network.element, fraction: r.network.fraction, parcels: PARCELS_PER_RELEASE });
        } else {
          riverReleases.push({ ...base, x: r.x });
        }
        build(engine ? engine.time() : r.start);
        break;
      }
      case "layer":
        layer = r.layer;
        if (QUALITY_LAYERS.has(layer)) await loadQuality();
        break;
      case "clear":
        riverReleases = [];
        for (const net of nets) net.releases = [];
        for (const m of meshes) m.releases = [];
        build(engine ? engine.time() : 0);
        break;
    }
    frame(r.seq);
  } catch (err) {
    post({ type: "error", message: String(err) });
  }
});
