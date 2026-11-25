// The compute host: runs the compute engine (WebAssembly) in a web worker, the
// engine's background thread (specification section 4.2). It fetches the
// published field files itself and answers the UI's requests with snapshots.
// No drawing happens here.
//
// A region has a river reach (a line) and may have network reaches (a tidal
// delta). Each has its own engine; they run in step. Parcels that pass the end
// of the river are handed to the network at the junction the index names
// (specification 7.4), at the moment they arrived, with their mass.

import init, { Engine, NetworkEngine } from "./pkg/fdmr_wasm.js";
import type { Frame, NetworkFrame, Reply, Request, Words } from "../compute-interface/messages";

interface ReachEntry {
  id: string;
  certainty: number[];
  network?: boolean;
  time?: { days: string[] };
}
interface Index {
  time: { start: string; step_s: number; days: string[] };
  now: string;
  reaches: ReachEntry[];
  handoffs?: { from: string; to: string; node: number }[];
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
// Spreading in the network's channels, m²/s: a placeholder of the right order
// for tidal channels, to be checked against DSM2's own transport (QUAL).
const NETWORK_DISPERSION = 50;

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
  /** Parcels handed on from the river, replayed with it. */
  handed: NetRelease[];
  engine: NetworkEngine | null;
}
const nets: Net[] = [];

const post = (reply: Reply, transfer: Transferable[] = []) =>
  (self as unknown as Worker).postMessage(reply, transfer);
const epoch = (iso: string) => Date.parse(iso.endsWith("Z") ? iso : iso + "Z") / 1000;
const bytes = async (url: string): Promise<Uint8Array | null> => {
  const r = await fetch(url);
  return r.ok ? new Uint8Array(await r.arrayBuffer()) : null;
};
const present = (list: (Uint8Array | null)[]): Uint8Array[] => list.filter((b): b is Uint8Array => b !== null);

async function open(cycle: string, reach: string, n: number): Promise<void> {
  await init();
  const [index, geometry] = (await Promise.all([
    fetch(`${cycle}/index.json`).then((r) => r.json()),
    fetch(`${cycle}/geometry/${reach}.json`).then((r) => r.json()),
  ])) as [Index, LineGeometry];
  starts = Float64Array.from(geometry.elements.map((e) => e.start_m));
  ends = Float64Array.from(geometry.elements.map((e) => e.end_m));
  days = present(await Promise.all(index.time.days.map((d) => bytes(`${cycle}/${reach}/${d}.fdf`))));
  particles = n;
  now = epoch(index.now);
  start = epoch(index.time.start);
  step = index.time.step_s;
  certainty = index.reaches.find((x) => x.id === reach)?.certainty ?? [1];
  build(now);
  const end = start + (certainty.length - 1) * step; // the last published step
  const netReaches = index.reaches.filter((r) => r.network);
  post({ type: "ready", start, end, now, length: engine!.length(), certainty, step, networks: netReaches.map((r) => r.id) });
  frame();
  // Network reaches load in the background: the river is ready to play first.
  for (const r of netReaches) {
    const node = index.handoffs?.find((h) => h.from === reach && h.to === r.id)?.node ?? null;
    void loadNetwork(cycle, r.id, r.time?.days ?? index.time.days, node);
  }
}

async function loadNetwork(cycle: string, id: string, netDays: string[], entryNode: number | null): Promise<void> {
  const g = (await fetch(`${cycle}/geometry/${id}.json`).then((r) => r.json())) as NetworkGeometry;
  const nodeIndex = new Map(g.nodes.map((n, i) => [n.id, i]));
  const poolIndex = new Map((g.pools ?? []).map((p, i) => [p.id, i]));
  const ix = (k: number) => nodeIndex.get(k) ?? 0;
  const [channels, nodeDays, linkDays] = await Promise.all(
    ["", ".nodes", ".links"].map((suffix) =>
      Promise.all(netDays.map((d) => bytes(`${cycle}/${id}/${d}${suffix}.fdf`))).then(present),
    ),
  );
  const entry = entryNode === null ? -1 : g.elements.findIndex((e) => e.up === entryNode);
  const net: Net = {
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
    handed: [],
    engine: null,
  };
  nets.push(net);
  // Hand on what the river has already passed on, then catch up.
  build(engine ? engine.time() : now);
  frame();
}

/** A fresh network engine at time t, with its releases and handed-on parcels replayed. */
function buildNet(net: Net, t: number): void {
  net.engine?.free();
  const all = [...net.releases, ...net.handed];
  const first = Math.min(t, ...all.map((r) => r.start));
  const T = net.topo;
  const e = new NetworkEngine(T.up, T.down, T.length, T.nodes, T.sea, T.pools, T.linkPool, T.linkNode, first, 2027, NETWORK_DISPERSION);
  for (const b of net.channels) e.load_channels(b);
  for (const b of net.nodeDays) e.load_nodes(b);
  for (const b of net.linkDays) e.load_links(b);
  for (const r of all) {
    e.add_release(r.element, r.fraction, T.length[r.element], r.start, r.duration, r.mass, r.halfLife ?? NaN, r.parcels);
  }
  e.step_to(t);
  e.seed_flow_particles(NETWORK_PARTICLES);
  net.engine = e;
}

/** Parcels the river engine passed on since last asked, as releases into each network that takes them. */
function handOn(): NetRelease[] {
  if (!engine) return [];
  const out = engine.take_outflow();
  const list: NetRelease[] = [];
  for (let i = 0; i + 2 < out.length; i += 3) {
    list.push({ element: 0, fraction: 0, start: out[i], duration: 0, mass: out[i + 1], halfLife: Number.isNaN(out[i + 2]) ? null : out[i + 2], parcels: 1 });
  }
  return list;
}

function giveTo(nets_: Net[], handed: NetRelease[], live: boolean): void {
  for (const net of nets_) {
    if (net.entry === null) continue;
    for (const h of handed) {
      const r = { ...h, element: net.entry };
      net.handed.push(r);
      if (live) net.engine?.add_release(r.element, 0, net.topo.length[r.element], r.start, 0, r.mass, r.halfLife ?? NaN, 1);
    }
  }
}

/** Fresh engines at time t, with every release replayed up to t. */
function build(t: number): void {
  engine?.free();
  const first = Math.min(t, ...riverReleases.map((r) => r.start), ...nets.flatMap((n) => n.releases.map((r) => r.start)));
  const e = new Engine(starts, ends, first, 2026);
  for (const d of days) e.load_day(d);
  for (const r of riverReleases) e.add_release(r.x, r.start, r.duration, r.mass, r.halfLife ?? NaN, PARCELS_PER_RELEASE);
  engine = e;
  e.step_to(t);
  for (const net of nets) net.handed = [];
  giveTo(nets, handOn(), false);
  e.seed_flow_particles(particles);
  for (const net of nets) buildNet(net, t);
}

function advance(to: number): void {
  if (!engine) return;
  if (to < engine.time()) {
    build(to);
    return;
  }
  engine.step_to(to);
  giveTo(nets, handOn(), true);
  for (const net of nets) net.engine?.step_to(to);
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

/** Every reach's mass balance together (see Frame.massBalance). */
function combinedBalance(networks: NetworkFrame[]): Float64Array {
  const [rReleased, rIn, rPast, rDecayed] = engine!.mass_balance();
  const taken = nets.some((n) => n.entry !== null && n.engine);
  const out = new Float64Array(7);
  out[0] = rReleased;
  out[1] = rIn;
  out[2] = taken ? 0 : rPast; // handed on, it is counted in the network instead
  out[3] = rDecayed;
  for (const n of networks) {
    const [released, inWater, exported, diverted, toSea, decayed] = n.massBalance;
    const handed = nets.find((x) => x.id === n.id)?.handed.reduce((s, h) => s + (h.start <= engine!.time() ? h.mass : 0), 0) ?? 0;
    out[0] += Math.max(0, released - handed);
    out[1] += inWater;
    out[3] += decayed;
    out[4] += exported;
    out[5] += diverted;
    out[6] += toSea;
  }
  return out;
}

function frame(): void {
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
      };
    });
  const f: Frame = {
    type: "frame",
    t,
    particleX: engine.particle_x(),
    particleLateral: engine.particle_lateral(),
    particleSpeed: engine.particle_speed(),
    parcelX: engine.parcel_x(),
    parcelConcentration: engine.parcel_concentration(),
    massBalance: combinedBalance(networks),
    certainty: c,
    words: words(c, t),
    networks,
  };
  const transfer: Transferable[] = [f.particleX.buffer, f.particleLateral.buffer, f.particleSpeed.buffer, f.parcelX.buffer, f.parcelConcentration.buffer];
  for (const n of networks) {
    transfer.push(n.velocity.buffer, n.particleElement.buffer, n.particleFraction.buffer, n.parcelElement.buffer, n.parcelFraction.buffer, n.parcelConcentration.buffer);
  }
  post(f, transfer);
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
        advance(r.to);
        break;
      case "drift":
        engine?.drift_frozen(r.dt);
        for (const net of nets) net.engine?.drift_frozen(r.dt);
        break;
      case "release": {
        const base = { start: r.start, duration: r.duration, mass: r.mass, halfLife: r.halfLife };
        const net = r.network ? nets.find((n) => n.id === r.network!.id) : undefined;
        if (r.network && net) {
          net.releases.push({ ...base, element: r.network.element, fraction: r.network.fraction, parcels: PARCELS_PER_RELEASE });
        } else {
          riverReleases.push({ ...base, x: r.x });
        }
        build(engine ? engine.time() : r.start);
        break;
      }
      case "clear":
        riverReleases = [];
        for (const net of nets) net.releases = [];
        build(engine ? engine.time() : 0);
        break;
    }
    frame();
  } catch (err) {
    post({ type: "error", message: String(err) });
  }
});
