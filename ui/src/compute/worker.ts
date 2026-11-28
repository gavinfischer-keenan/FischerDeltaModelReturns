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
// mesh covers some of the network's channels; and from a network's pumps into
// the canals they feed, a network of their own (index.json `handoffs`). Each
// parcel belongs to one reach at a time, with its mass, so the total balances.

import init, { Engine, MeshEngine, NetworkEngine, Region } from "./pkg/fdmr_wasm.js";
import type { Frame, Layer, LineFrame, MeshFrame, NetworkFrame, Reply, Request, Words } from "../compute-interface/messages";

interface ReachEntry {
  id: string;
  kind?: string;
  certainty: number[];
  network?: boolean;
  dims?: number;
  time?: { days: string[]; start?: string; step_s?: number };
  quality_fields?: string[];
  /** A network reach with a sediment run: the mud's settling velocity (#16). */
  sediment?: { settling_velocity_m_s: number };
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
  /** A river reach ending on another: where along it, m. */
  at_m?: number;
  /** The water-source group the giving reach's water belongs to. */
  source?: string;
  owned?: number[];
  /** Where the join's line crosses a channel (docs/format.md, handoffs). */
  cuts?: Cut[];
  junctions?: Junction[];
  /** A network's exports that carry on into the canals: each enters this junction of the `to` network. */
  outlets?: { export: string; node: number }[];
}
interface Cut {
  element: number;
  at: number;
  inside: "up" | "down";
  x: number;
  y: number;
}
interface Index {
  timezone?: string;
  time: { start: string; step_s: number; days: string[] };
  now: string;
  reaches: ReachEntry[];
  handoffs?: Handoff[];
}
interface LineGeometry {
  elements: { start_m: number; end_m: number; width_m?: number; fickian_m?: number }[];
}
interface NetworkGeometry {
  elements: { id: number; up: number; down: number; end_m: number; width_m?: number }[];
  nodes: { id: number; kind?: string }[];
  pools?: { id: string }[];
  exports?: { name: string; node?: number; pool?: string }[];
  links?: { pool: string; node: number }[];
}
interface MeshGeometry {
  x: number[];
  y: number[];
  depth_m: number[];
  triangles: [number, number, number][];
  edges: Record<string, [number, number][]>;
  /** Each node's horizontal diffusivity, m²/s, where published (#10). */
  diffusivity_m2s?: number[];
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
  /** Sticks to sediment: partition coefficient, L/kg. */
  kd?: number | null;
}

const PARCELS_PER_RELEASE = 20_000;
// Flow particles per km² of water surface, the same in every reach
// reach, so that the water looks the same on both sides of a join: before 26
// Nov 2026 the Delta's channels held about six times the Bay's density and its
// particles ran into the Bay's sparse field in single file.
const PARTICLES_PER_KM2 = 8;
// In particle numbers, a channel counts as at least this wide, m, so that the
// narrow sloughs still show their water moving (issue #3). Drawn, its
// particles still sit within its own width.
const MIN_COUNTED_WIDTH_M = 100;
// A network without channel widths (an older publication) is seeded by volume, with this many.
const NETWORK_PARTICLES = 6_000;
// Spreading in the network's channels, m²/s. Checked 25 Nov 2026 against DSM2's
// own transport (QUAL) with a pulse released at the network's main inflow
// (tools/pulse_check.py; build plan, network tracer): at 50 the tracer matches
// QUAL's arrival, spread and peak at five channels across the network; 10 and
// 200 are clearly worse, 30 and 80 about as good.
const NETWORK_DISPERSION = 50;
// Horizontal diffusivity on a mesh reach, m²/s. It stands for mixing the
// depth-averaged mesh does not resolve, the sum of two parts, worked out on the
// Bay's own mesh and currents (25 Nov 2026, the 14 days published, by water area):
// - eddies smaller than a node's own patch of water (median 620 m across): Okubo's
//   K = 0.0103 L^1.15 (cgs), median 0.34 m²/s (Okubo 1971, Deep-Sea Research 18: 789-802; ref:okubo1971);
// - the spreading of the vertical shear the depth average hides: Elder's
//   K = 5.93 H u*, with u* = √Cd |u| and Cd = 0.0025, at every node's depth and
//   current, averaged over the tides: median 0.74 m²/s (Elder 1959, J. Fluid
//   Mech. 5: 544-560, ref:elder1959; Fischer et al. 1979, Mixing in Inland and Coastal
//   Waters, ref:fischer1979).
// The sum's median by water area is 1.1 m²/s (mean 1.35). Elder's part acts
// along the flow, so as an even spreading this errs toward too much across it.
// Used only where a mesh publishes no diffusivity of its own: the Bay's
// publisher now works out the same two parts at every node
// (fdmr_plugins.fvcom.mesh.diffusivity, geometry `diffusivity_m2s`; #10).
const MESH_DIFFUSIVITY = 1.1;
// River water above the Delta, for the salinity layer: shown in the Venice
// System's fresh class (below 0.5 psu; layers.ts), as a class, not a measurement.
const RIVER_SALINITY_PSU = 0.1;

let engine: Engine | null = null;
let days: Uint8Array[] = [];
/** The main river's water-quality day files (its temperature), once a layer needs them. */
let riverQuality: Uint8Array[] | null = null;
let starts = new Float64Array();
let ends = new Float64Array();
/** Each river element's published distance below a release before Fischer's dispersion holds, m. */
let fickian: (number | null)[] = [];
/** The main river's element widths, m, where published (the engine's shear spreading, #29). */
let riverWidths: Float64Array | null = null;
/** That distance for the latest river release, m, or null (a network or mesh release, or none). */
let releaseFickian: number | null = null;
let particles = 0;
let riverReleases: RiverRelease[] = [];
let certainty: number[] = [];
let start = 0;
let step = 900;
let now = 0;
let index: Index | null = null;
let cycleUrl = "";
let layer: Layer = "plain";
/** Flow particles on the tidally filtered flow (net flow) rather than the instant flow. */
let netFlow = false;
/** Water sources the networks carry shares of (from `source:<name>` fields). */
let sources: string[] = [];
let riverId = "river";
/** Every reach together (fdmr-wasm `Region`, fdmr_compute::region): it steps
 * them, passes parcels and flow particles across every join and keeps their
 * one mass balance (#60). The worker only says where the joins are. */
let region: Region | null = null;

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
  /** Where parcels from each river reach ending here enter: the channel starting at its junction. */
  entries: Map<string, number>;
  releases: NetRelease[];
  engine: NetworkEngine | null;
  days: string[];
  /** Water-quality day files, fetched when a layer first needs them. */
  quality: Uint8Array[] | null;
  /** Each channel's water-surface width, m, if published. */
  widths: Float64Array | null;
  /** How many flow particles it carries. */
  count: number;
  /** Canals: what reaches it is pumped out of another network. */
  canal: boolean;
  /** Each named export's junction, or pool after the junctions (as the engine counts them). */
  exportAt: Map<string, number>;
  /** Each junction id's index. */
  nodeIndex: Map<number, number>;
  /** Where each of its outlets leads (set_outlets order): a network and its junction index. */
  outlets: { to: string; node: number }[];
}
const nets: Net[] = [];

/** Another river reach (a tributary from its dam), run like the main river. */
interface Line {
  id: string;
  /** Water-quality day files (temperature), once a layer needs them. */
  quality: Uint8Array[] | null;
  starts: Float64Array;
  ends: Float64Array;
  fickian: (number | null)[];
  /** Element widths, m, where published. */
  widths: Float64Array | null;
  days: Uint8Array[];
  releases: RiverRelease[];
  engine: Engine | null;
  count: number;
}
const lines: Line[] = [];

interface Mesh {
  id: string;
  x: Float64Array;
  y: Float64Array;
  depth: Float64Array;
  /** Each node's horizontal diffusivity, m²/s, or null for MESH_DIFFUSIVITY everywhere. */
  diffusivity: Float64Array | null;
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
  fickian = geometry.elements.map((e) => e.fickian_m ?? null);
  riverWidths = widthsOf(geometry);
  days = present(await Promise.all(ix.time.days.map((d) => bytes(`${cycle}/${reach}/${d}.fdf`))));
  // As dense over the water's surface as every other reach, where the river's widths are published.
  particles = lineCount(geometry, n);
  now = epoch(ix.now);
  start = epoch(ix.time.start);
  step = ix.time.step_s;
  certainty = ix.reaches.find((x) => x.id === reach)?.certainty ?? [1];
  build(now);
  const end = start + (certainty.length - 1) * step; // the last published step
  const netReaches = ix.reaches.filter((r) => r.network);
  const lineReaches = ix.reaches.filter((r) => r.kind === "river-routing" && r.id !== reach);
  const meshReaches = ix.reaches.filter((r) => r.dims === 2);
  post({ type: "ready", start, end, now, timezone: ix.timezone ?? "UTC", length: engine!.length(), certainty, step, networks: netReaches.map((r) => r.id), meshes: meshReaches.map((r) => r.id), lines: lineReaches.map((r) => r.id) });
  frame();
  // Network and mesh reaches load in the background: the river is ready to play first.
  for (const r of lineReaches) void loadLine(cycle, r.id);
  for (const r of netReaches) void loadNetwork(cycle, r.id, r.time?.days ?? ix.time.days);
  for (const r of meshReaches) void loadMesh(cycle, r.id, r.time?.days ?? ix.time.days);
}

/** Rebuild the engines once the reaches loading in the background have come
 * in: each arriving reach would otherwise rebuild and replay every engine,
 * a dozen times over on opening. */
let rebuildTimer: ReturnType<typeof setTimeout> | null = null;
function rebuildSoon(): void {
  if (rebuildTimer !== null) clearTimeout(rebuildTimer);
  rebuildTimer = setTimeout(() => {
    rebuildTimer = null;
    build(engine ? engine.time() : now);
    frame();
  }, 250);
}

/** Flow particles for a line reach: as dense over its water as every reach, or `n` without widths. */
/** A reach's element widths, m, if every element has one. */
function widthsOf(g: LineGeometry): Float64Array | null {
  return g.elements.every((e) => e.width_m !== undefined) ? Float64Array.from(g.elements.map((e) => e.width_m!)) : null;
}

/** The published Fickian distance of the element holding chainage x, m. */
function fickianAt(st: Float64Array, fk: (number | null)[], x: number): number | null {
  let i = 0;
  while (i + 1 < st.length && st[i + 1] <= x) i++;
  return fk[i] ?? null;
}

function lineCount(g: LineGeometry, n: number): number {
  return g.elements.every((x) => x.width_m !== undefined)
    ? Math.round((PARTICLES_PER_KM2 * g.elements.reduce((s, x) => s + Math.max(x.width_m!, MIN_COUNTED_WIDTH_M) * (x.end_m - x.start_m), 0)) / 1e6)
    : n;
}

async function loadLine(cycle: string, id: string): Promise<void> {
  const g = (await fetch(`${cycle}/geometry/${id}.json`).then((r) => r.json())) as LineGeometry;
  lines.push({
    id,
    starts: Float64Array.from(g.elements.map((e) => e.start_m)),
    ends: Float64Array.from(g.elements.map((e) => e.end_m)),
    fickian: g.elements.map((e) => e.fickian_m ?? null),
    widths: widthsOf(g),
    days: present(await Promise.all((index?.time.days ?? []).map((d) => bytes(`${cycle}/${id}/${d}.fdf`)))),
    releases: [],
    engine: null,
    quality: null,
    count: lineCount(g, 100),
  });
  // Upstream reaches first, so a tributary's water reaches what it joins in the same exchange.
  const hops = (x: string, seen = 0): number => {
    const h = index?.handoffs?.find((k) => k.from === x && k.at_m !== undefined);
    return h && seen < 10 ? 1 + hops(h.to, seen + 1) : 0;
  };
  lines.sort((a, b) => hops(b.id) - hops(a.id));
  rebuildSoon();
}

async function loadNetwork(cycle: string, id: string, netDays: string[]): Promise<void> {
  const g = (await fetch(`${cycle}/geometry/${id}.json`).then((r) => r.json())) as NetworkGeometry;
  const nodeIndex = new Map(g.nodes.map((n, i) => [n.id, i]));
  const poolIndex = new Map((g.pools ?? []).map((p, i) => [p.id, i]));
  const ix = (k: number) => nodeIndex.get(k) ?? 0;
  const [channels, nodeDays, linkDays] = await Promise.all(
    // A network without pools (the canals) publishes no link files.
    ["", ".nodes", ".links"].map((suffix) =>
      suffix === ".links" && !g.links?.length
        ? Promise.resolve([])
        : Promise.all(netDays.map((d) => bytes(`${cycle}/${id}/${d}${suffix}.fdf`))).then(present),
    ),
  );
  const entries = new Map<string, number>();
  for (const h of index?.handoffs ?? []) {
    if (h.to !== id || h.node === undefined) continue;
    const c = g.elements.findIndex((e) => e.up === h.node);
    if (c >= 0) entries.set(h.from, c);
  }
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
    entries,
    releases: [],
    engine: null,
    days: netDays,
    quality: null,
    count: NETWORK_PARTICLES,
    canal: index?.reaches.find((r) => r.id === id)?.kind === "canal-network",
    exportAt: new Map((g.exports ?? []).map((e) => [e.name, e.node !== undefined ? ix(e.node) : g.nodes.length + (poolIndex.get(e.pool ?? "") ?? 0)])),
    nodeIndex,
    outlets: [],
    widths: g.elements.every((e) => e.width_m !== undefined) ? Float64Array.from(g.elements.map((e) => e.width_m!)) : null,
  });
  if (QUALITY_LAYERS.has(layer)) await loadQuality();
  // Play the river's (and any other reach's) handoffs into it from the start.
  rebuildSoon();
}

async function loadMesh(cycle: string, id: string, meshDays: string[]): Promise<void> {
  const g = (await fetch(`${cycle}/geometry/${id}.json`).then((r) => r.json())) as MeshGeometry;
  const handoffs = Object.entries(g.edges).filter(([k]) => k !== "open");
  meshes.push({
    id,
    x: Float64Array.from(g.x),
    y: Float64Array.from(g.y),
    depth: Float64Array.from(g.depth_m),
    diffusivity: g.diffusivity_m2s?.length === g.x.length ? Float64Array.from(g.diffusivity_m2s) : null,
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
  rebuildSoon();
}

/** Layers that need the water-quality files. */
const QUALITY_LAYERS = new Set<Layer>(["temperature", "salinity", "sediment", "source"]);

/** Fetch the water-quality day files of every reach not holding them yet, and load them. */
async function loadQuality(): Promise<void> {
  const get = (id: string, ds: string[]) => Promise.all(ds.map((d) => bytes(`${cycleUrl}/${id}/${d}.quality.fdf`))).then(present);
  // River reaches publish quality files only where their temperature is modelled.
  const modelled = (id: string) => !!index?.reaches.find((r) => r.id === id)?.quality_fields?.length;
  const riverDays = index?.time.days ?? [];
  await Promise.all([
    ...(riverQuality === null && modelled(riverId)
      ? [get(riverId, riverDays).then((q) => {
          riverQuality = q;
          for (const b of q) engine?.load_quality(b);
        })]
      : []),
    ...lines.filter((l) => !l.quality && modelled(l.id)).map(async (l) => {
      l.quality = await get(l.id, riverDays);
      for (const b of l.quality) l.engine?.load_quality(b);
    }),
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

/** Fresh engines at time t: every release replayed from its start, the reaches
 * run together and passing parcels as they go. */
function build(t: number): void {
  region?.free();
  region = null;
  engine?.free();
  for (const l of lines) l.engine?.free();
  for (const net of nets) net.engine?.free();
  for (const m of meshes) m.engine?.free();
  const first = Math.min(
    t,
    ...riverReleases.map((r) => r.start),
    ...lines.flatMap((l) => l.releases.map((r) => r.start)),
    ...nets.flatMap((n) => n.releases.map((r) => r.start)),
    ...meshes.flatMap((m) => m.releases.map((r) => r.start)),
  );
  const e = new Engine(starts, ends, first, 2026);
  if (riverWidths) e.set_widths(riverWidths);
  for (const d of days) e.load_day(d);
  for (const r of riverReleases) e.add_release(r.x, r.start, r.duration, r.mass, hl(r.halfLife), PARCELS_PER_RELEASE);
  for (const b of riverQuality ?? []) e.load_quality(b);
  engine = e;
  for (const l of lines) {
    const le = new Engine(l.starts, l.ends, first, 2029);
    if (l.widths) le.set_widths(l.widths);
    for (const d of l.days) le.load_day(d);
    for (const r of l.releases) le.add_release(r.x, r.start, r.duration, r.mass, hl(r.halfLife), PARCELS_PER_RELEASE);
    for (const b of l.quality ?? []) le.load_quality(b);
    l.engine = le;
  }
  for (const net of nets) {
    const T = net.topo;
    const ne = new NetworkEngine(T.up, T.down, T.length, T.nodes, T.sea, T.pools, T.linkPool, T.linkNode, first, 2027, NETWORK_DISPERSION);
    for (const b of net.channels) ne.load_channels(b);
    for (const b of net.nodeDays) ne.load_nodes(b);
    for (const b of net.linkDays) ne.load_links(b);
    // Quality first: a bound release reads the sediment where it is released.
    for (const b of net.quality ?? []) ne.load_quality(b);
    const ws = index?.reaches.find((x) => x.id === net.id)?.sediment?.settling_velocity_m_s;
    if (ws) ne.set_settling(ws);
    for (const r of net.releases) {
      if (r.kd) ne.add_bound_release(r.element, r.fraction, T.length[r.element], r.start, r.duration, r.mass, hl(r.halfLife), r.parcels, r.kd);
      else ne.add_release(r.element, r.fraction, T.length[r.element], r.start, r.duration, r.mass, hl(r.halfLife), r.parcels);
    }
    const owned = index?.handoffs?.filter((h) => h.from === net.id && h.owned && meshes.some((m) => m.id === h.to)).flatMap((h) => h.owned!) ?? [];
    ne.set_handoff(Uint32Array.from(owned));
    const cuts = index?.handoffs?.filter((h) => h.from === net.id && h.cuts && meshes.some((m) => m.id === h.to)).flatMap((h) => h.cuts!) ?? [];
    ne.set_cuts(Uint32Array.from(cuts.map((c) => c.element)), Float64Array.from(cuts.map((c) => c.at)), Uint8Array.from(cuts.map((c) => (c.inside === "down" ? 1 : 0))));
    // Exports feeding a canal that is loaded carry on into it; the rest leave the model.
    net.outlets = [];
    const at: number[] = [];
    for (const h of index?.handoffs ?? []) {
      const to = nets.find((n) => n.id === h.to);
      if (h.from !== net.id || !h.outlets || !to) continue;
      for (const o of h.outlets) {
        const i = net.exportAt.get(o.export);
        const node = to.nodeIndex.get(o.node);
        if (i === undefined || node === undefined) continue;
        at.push(i);
        net.outlets.push({ to: to.id, node });
      }
    }
    ne.set_outlets(Uint32Array.from(at));
    if (netFlow) ne.set_net_flow(true);
    net.count = NETWORK_PARTICLES;
    if (net.widths) {
      // The engine gets the channels' true widths (settling divides by the
      // depth over them); the floor applies only to where particles start (#50).
      ne.set_widths(net.widths);
      ne.set_min_seed_width(MIN_COUNTED_WIDTH_M);
      const w = net.widths.map((x) => Math.max(x, MIN_COUNTED_WIDTH_M));
      // The network's own water surface: not channels a mesh tracks, nor past a cut.
      const ownedSet = new Set(owned);
      const cutAt = new Map(cuts.map((c) => [c.element, c.inside === "down" ? c.at : 1 - c.at]));
      let area = 0;
      for (let c = 0; c < w.length; c++) if (!ownedSet.has(c)) area += w[c] * T.length[c] * (cutAt.get(c) ?? 1);
      net.count = Math.round((PARTICLES_PER_KM2 * area) / 1e6);
    }
    net.engine = ne;
  }
  for (const m of meshes) {
    const me = new MeshEngine(m.x, m.y, m.depth, m.triangles, m.open, m.handoff, m.handoffId, first, 2028, MESH_DIFFUSIVITY);
    if (m.diffusivity) me.set_node_diffusivity(m.diffusivity);
    for (const b of m.days) me.load_day(b);
    for (const r of m.releases) me.add_release(r.triangle, r.w1, r.w2, r.start, r.duration, r.mass, hl(r.halfLife), r.parcels);
    for (const b of m.quality ?? []) me.load_quality(b);
    if (netFlow) me.set_net_flow(true);
    m.engine = me;
  }
  region = makeRegion();
  run(t);
  e.seed_flow_particles(particles);
  for (const l of lines) l.engine!.seed_flow_particles(l.count);
  for (const net of nets) net.engine!.seed_flow_particles(net.count);
  for (const m of meshes) m.engine!.seed_flow_particles(Math.round((PARTICLES_PER_KM2 * meshArea(m)) / 1e6));
}

/** A mesh's water surface, m²: its triangles' area. */
function meshArea(m: Mesh): number {
  const { x, y, triangles: t } = m;
  let a2 = 0;
  for (let i = 0; i + 2 < t.length; i += 3) {
    const [a, b, c] = [t[i], t[i + 1], t[i + 2]];
    a2 += Math.abs((x[b] - x[a]) * (y[c] - y[a]) - (x[c] - x[a]) * (y[b] - y[a]));
  }
  return a2 / 2;
}

/** Run every reach on to `to` together, passing parcels across every join. */
function run(to: number): void {
  region?.step_to(to);
}

/** The region of the reaches just built: each join of index.json `handoffs`
 * given to it as plain numbers (reaches by the index the region gave them). */
function makeRegion(): Region {
  const r = new Region();
  // Tributaries upstream first (as `lines` is sorted), then the main river.
  const rivers: [string, Engine][] = [...lines.filter((l) => l.engine).map((l): [string, Engine] => [l.id, l.engine!]), [riverId, engine!]];
  const riverAt = new Map(rivers.map(([id, e]) => [id, r.add_river(e)]));
  const netAt = new Map(nets.filter((n) => n.engine).map((n) => [n.id, r.add_network(n.engine!, n.canal)]));
  const meshAt = new Map(meshes.filter((m) => m.engine).map((m) => [m.id, r.add_mesh(m.engine!)]));
  const hs = index?.handoffs ?? [];
  // Each river into a network's channel or part way along another river; a
  // handoff to a reach that is not loaded loses what reaches it, on the books.
  for (const [id, from] of riverAt) {
    const h = hs.find((k) => k.from === id && (k.node !== undefined || k.at_m !== undefined));
    if (!h) continue;
    if (h.at_m !== undefined) {
      const to = riverAt.get(h.to);
      if (to === undefined) r.river_lost(from);
      else r.river_to_river(from, to, h.at_m);
      continue;
    }
    const to = netAt.get(h.to);
    const c = nets.find((n) => n.id === h.to)?.entries.get(id);
    if (to === undefined || c === undefined) r.river_lost(from);
    else r.river_to_network(from, to, c);
  }
  for (const net of nets) {
    const ni = netAt.get(net.id);
    if (ni === undefined) continue;
    // Exports into a loaded canal (net.outlets is in set_outlets order).
    net.outlets.forEach((o, k) => {
      const to = netAt.get(o.to);
      if (to !== undefined) r.outlet(ni, k, to, o.node);
    });
    // Its crossings into each mesh, both ways. The cuts are numbered across
    // all of its joins into meshes, in order, as build gave them to set_cuts.
    let base = 0;
    for (const h of hs) {
      const mi = meshAt.get(h.to);
      if (h.from !== net.id || mi === undefined) continue;
      const cuts = h.cuts ?? [];
      cuts.forEach((c, k) => {
        r.cut_out(ni, base + k, mi, c.x, c.y);
        r.crossing_back(mi, ni, true, base + k, c.x, c.y);
      });
      for (const j of h.junctions ?? []) {
        r.junction_out(ni, j.node, mi, j.x, j.y);
        r.crossing_back(mi, ni, false, j.node, j.x, j.y);
      }
      base += cuts.length;
    }
  }
  // Each mesh's handoff edges, by id, to the network each hands to.
  for (const m of meshes) {
    const mi = meshAt.get(m.id);
    if (mi === undefined) continue;
    m.handoffTo.forEach((to, edge) => {
      const ni = netAt.get(to);
      if (ni !== undefined) r.edge(mi, edge, ni);
    });
  }
  return r;
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

/** A reach's own certainty at time t, on its own time axis (format 1.1: the
 * Delta's and the Bay's are their own), so each reach fades by its own (#60). */
function reachCertainty(id: string, t: number): number {
  const r = index?.reaches.find((x) => x.id === id);
  if (!r?.certainty?.length) return certaintyAt(t);
  const t0 = r.time?.start ? epoch(r.time.start) : start;
  const st = r.time?.step_s ?? step;
  const i = Math.max(0, Math.min(r.certainty.length - 1, Math.round((t - t0) / st)));
  return r.certainty[i] ?? 1;
}

/** The three tiers of section 3.4, as in fdmr-math's certainty::words. */
function words(c: number, t: number): Words {
  if (t <= now) return "measured";
  return c >= 0.5 ? "forecast" : "rough_guide";
}

/** A field's values centred on their mean: water level "either side of mean level" (spec 8.4). */
function centred(v: Float32Array): Float32Array {
  let s = 0, n = 0;
  for (const x of v) if (Number.isFinite(x)) { s += x; n++; }
  const m = n ? s / n : 0;
  return v.map((x) => x - m);
}

/** The field a layer shows, by its name in the published files. */
const FIELD: Partial<Record<Layer, string>> = { temperature: "temperature", salinity: "salinity", sediment: "ssc", level: "level" };

/** Shares of each water source, all particles of a network, interleaved. */
function networkShares(e: NetworkEngine): Float32Array {
  const per = sources.map((s) => e.particle_values(`source:${s}`));
  const n = per[0]?.length ?? 0;
  const out = new Float32Array(n * sources.length);
  per.forEach((v, j) => v.forEach((x, i) => (out[i * sources.length + j] = x)));
  return out;
}

/** The source group a river reach's water belongs to: the one its handoff
 * names, or that of the reach it joins (index.json handoffs). */
function groupOf(id: string, hops = 0): string | undefined {
  const h = index?.handoffs?.find((k) => k.from === id && (k.node !== undefined || k.at_m !== undefined));
  if (!h) return undefined;
  return h.source ?? (h.at_m !== undefined && hops < 10 ? groupOf(h.to, hops + 1) : undefined);
}

/** A river reach's water is all of its source group. */
function riverShares(n: number, id = riverId): Float32Array {
  const out = new Float32Array(n * sources.length).fill(0);
  const group = groupOf(id);
  const j = group ? sources.indexOf(group) : -1;
  if (j >= 0) for (let i = 0; i < n; i++) out[i * sources.length + j] = 1;
  else out.fill(NaN);
  return out;
}

function riverLayer(n: number, e: Engine = engine!, id = riverId): { particleValue?: Float32Array; particleShares?: Float32Array } {
  if (layer === "speed") return { particleValue: Float32Array.from(e.particle_speed()) };
  if (layer === "source") return { particleShares: riverShares(n, id) };
  // The rivers above the Delta are fresh water: shown as fresh, not measured.
  if (layer === "salinity") return { particleValue: new Float32Array(n).fill(RIVER_SALINITY_PSU) };
  const f = FIELD[layer];
  if (f) return { particleValue: e.particle_values(f) }; // NaN where the river's field is not published
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
        certainty: reachCertainty(n.id, t),
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
        certainty: reachCertainty(m.id, t),
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
  const lineFrames: LineFrame[] = lines
    .filter((l) => l.engine)
    .map((l) => {
      const e = l.engine!;
      return {
        id: l.id,
        certainty: reachCertainty(l.id, t),
        particleX: e.particle_x(),
        particleLateral: e.particle_lateral(),
        particleSpeed: e.particle_speed(),
        parcelX: e.parcel_x(),
        parcelConcentration: e.parcel_concentration(),
        ...riverLayer(e.particle_x().length, e, l.id),
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
    massBalance: region!.mass_balance(), // every reach together (see Frame.massBalance)
    certainty: c,
    words: words(c, t),
    lines: lineFrames,
    networks,
    meshes: meshFrames,
    layer,
    sources,
    fickianM: releaseFickian,
    ...riverLayer(riverN),
  };
  const transfer: Transferable[] = [f.particleX.buffer, f.particleLateral.buffer, f.particleSpeed.buffer, f.parcelX.buffer, f.parcelConcentration.buffer];
  for (const l of lineFrames) {
    transfer.push(l.particleX.buffer, l.particleLateral.buffer, l.particleSpeed.buffer, l.parcelX.buffer, l.parcelConcentration.buffer);
  }
  for (const n of networks) {
    transfer.push(n.velocity.buffer, n.particleElement.buffer, n.particleFraction.buffer, n.parcelElement.buffer, n.parcelFraction.buffer, n.parcelConcentration.buffer);
  }
  for (const m of meshFrames) {
    transfer.push(m.particleTriangle.buffer, m.particleWeights.buffer, m.particleSpeed.buffer, m.parcelTriangle.buffer, m.parcelWeights.buffer, m.parcelConcentration.buffer);
  }
  for (const x of [f, ...lineFrames, ...networks, ...meshFrames]) {
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
        region?.drift_frozen(r.dt);
        break;
      case "release": {
        const base = { start: r.start, duration: r.duration, mass: r.mass, halfLife: r.halfLife };
        const net = r.network ? nets.find((n) => n.id === r.network!.id) : undefined;
        const mesh = r.mesh ? meshes.find((m) => m.id === r.mesh!.id) : undefined;
        if (r.mesh && mesh) {
          mesh.releases.push({ ...base, triangle: r.mesh.triangle, w1: r.mesh.w1, w2: r.mesh.w2, parcels: PARCELS_PER_RELEASE });
        } else if (r.line && lines.some((l) => l.id === r.line!.id)) {
          const l = lines.find((l) => l.id === r.line!.id)!;
          l.releases.push({ ...base, x: r.line.x });
          releaseFickian = fickianAt(l.starts, l.fickian, r.line.x);
        } else if (r.network && net) {
          // A pollutant that sticks to sediment needs the sediment fields where it is released.
          if (r.kd && !net.quality) await loadQuality();
          net.releases.push({ ...base, element: r.network.element, fraction: r.network.fraction, parcels: PARCELS_PER_RELEASE, kd: r.kd ?? null });
        } else {
          riverReleases.push({ ...base, x: r.x });
          releaseFickian = fickianAt(starts, fickian, r.x);
        }
        if (r.mesh || r.network) releaseFickian = null;
        build(engine ? engine.time() : r.start);
        break;
      }
      case "flow":
        netFlow = r.net;
        // The river above the Delta has no tide: its flow is the same either way.
        for (const net of nets) net.engine?.set_net_flow(netFlow);
        for (const m of meshes) m.engine?.set_net_flow(netFlow);
        break;
      case "layer":
        layer = r.layer;
        if (QUALITY_LAYERS.has(layer)) await loadQuality();
        break;
      case "clear":
        riverReleases = [];
        releaseFickian = null;
        for (const l of lines) l.releases = [];
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
