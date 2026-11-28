// The interface between the UI and the compute engine (specification section 4.2).
// The UI sends requests and draws what comes back. These types are all the UI
// knows about the compute side.

/** What colours the water (specification 3.5). */
export type Layer = "plain" | "contaminant" | "speed" | "temperature" | "salinity" | "sediment" | "level" | "source";

/** Requests from the UI to the compute engine. Times are seconds since 1970 UTC.
 * Each carries a number (`seq`, set by connect()) that the frame answering it repeats. */
export type Request = { seq?: number } & (
  | { type: "open"; cycle: string; reach: string; particles: number }
  | { type: "seek"; t: number }
  | { type: "advance"; to: number }
  | { type: "drift"; dt: number }
  | {
      type: "release";
      x: number; // chainage, m, on the river
      /** Release on another river reach (a tributary) instead: its id and chainage, m. */
      line?: { id: string; x: number };
      /** Release into a network reach instead: a channel and how far along it (0..1). */
      network?: { id: string; element: number; fraction: number };
      /** Release into a mesh reach instead: a triangle and two barycentric weights (its second and third nodes). */
      mesh?: { id: string; triangle: number; w1: number; w2: number };
      start: number;
      duration: number; // s; 0 for all at once
      mass: number; // kg
      halfLife: number | null; // s
      /** Sticks to suspended sediment: its partition coefficient, L/kg (a network reach with a sediment run). */
      kd?: number | null;
    }
  | { type: "clear" }
  | { type: "layer"; layer: Layer }
  /** Show the flow particles on the flow net over the tides, or moment by moment (spec 3.5). */
  | { type: "flow"; net: boolean });

/** Another river reach (a tributary, from its own dam) at one moment, laid
 * out as the main river's fields in Frame. */
export interface LineFrame {
  id: string;
  /** This reach's own certainty at the frame's time (specification 8.2). */
  certainty: number;
  particleX: Float64Array;
  particleLateral: Float32Array;
  particleSpeed: Float64Array;
  parcelX: Float64Array;
  parcelConcentration: Float64Array;
  particleValue?: Float32Array;
  particleShares?: Float32Array;
}

/** A network reach at one moment. Positions are an element (a channel; pools
 * follow the channels, at `channels + pool`) and a fraction along it, 0 at its
 * upstream junction. */
export interface NetworkFrame {
  id: string;
  /** This reach's own certainty at the frame's time (specification 8.2). */
  certainty: number;
  /** Each channel's mean velocity, m/s, positive downstream. */
  velocity: Float32Array;
  particleElement: Uint32Array;
  particleFraction: Float32Array;
  /** Each particle's place on its pool's water, flat (lon, lat); NaN where it
   * is not in a pool with mapped water. A pool's particles drift there. */
  particlePool: Float64Array;
  parcelElement: Uint32Array;
  parcelFraction: Float32Array;
  /** Concentration where each parcel is, kg/m3. */
  parcelConcentration: Float64Array;
  /** [released, in water, taken by the pumps, taken by other diversions, out to sea, broken down], kg. */
  massBalance: Float64Array;
  /** The layer's value where each flow particle is (NaN where there is none). */
  particleValue?: Float32Array;
  /** For the water-source layer: each particle's shares, `sources` of them per particle. */
  particleShares?: Float32Array;
}

/** A mesh reach (such as a bay) at one moment. Positions are a triangle and
 * two barycentric weights (for its second and third nodes), two per thing. */
export interface MeshFrame {
  id: string;
  /** This reach's own certainty at the frame's time (specification 8.2). */
  certainty: number;
  particleTriangle: Uint32Array;
  particleWeights: Float32Array;
  /** Speed of each flow particle, m/s. */
  particleSpeed: Float32Array;
  parcelTriangle: Uint32Array;
  parcelWeights: Float32Array;
  /** Depth-averaged concentration where each parcel is, kg/m3. */
  parcelConcentration: Float64Array;
  /** [released, in water, out to sea, handed on, broken down], kg. */
  massBalance: Float64Array;
  particleValue?: Float32Array;
  particleShares?: Float32Array;
}

/** A snapshot of the water at one moment. */
export interface Frame {
  type: "frame";
  /** The request this frame answers; none for a frame the engine sent of its
   * own accord (such as when a reach finishes loading). */
  seq?: number;
  t: number;
  /** Flow particles: chainage (m), lateral position (-1..1), speed (m/s). */
  particleX: Float64Array;
  particleLateral: Float32Array;
  particleSpeed: Float64Array;
  /** Parcels in the water: chainage (m) and concentration where each is (kg/m3). */
  parcelX: Float64Array;
  parcelConcentration: Float64Array;
  /** Every reach together, kg: [released so far, in the water, past the end of
   * the last reach, broken down, taken by the pumps, taken by other diversions,
   * out of a network at its sea boundary, out of a mesh to the open ocean,
   * in the export canals so far, carried on down the canals beyond the map,
   * bound to sediment and settled on a network's bed, lost at a join between
   * reaches (handed on where no reach could take it)]: 12 slots.
   * Mass handed from one reach to the next is counted once. */
  massBalance: Float64Array;
  /** Certainty of the flow at this moment, 1 down toward 0 (specification 8.2). */
  certainty: number;
  /** The same in words, for the timeline. */
  words: Words;
  /** Network reaches loaded so far. */
  /** Other river reaches. */
  lines: LineFrame[];
  networks: NetworkFrame[];
  /** Mesh reaches loaded so far. */
  meshes: MeshFrame[];
  /** What colours the water, and for it the river's particle values or shares. */
  layer: Layer;
  particleValue?: Float32Array;
  particleShares?: Float32Array;
  /** The water sources' names, in the order of every particleShares. */
  sources: string[];
  /** Each source's label and colour (`#rrggbb`, or "" for none), in that order: the region's own. */
  sourceLabels: string[];
  sourceColours: string[];
  /** For the latest river release, how far below it Fischer's dispersion
   * holds, m (published per element); null for none or a network or mesh release. */
  fickianM: number | null;
}

export type Words = "measured" | "forecast" | "rough_guide";

export interface Ready {
  type: "ready";
  start: number;
  end: number;
  now: number;
  /** The region's own time zone (IANA), for its dates (index.json `timezone`). */
  timezone: string;
  length: number;
  /** Certainty at each published step from `start`, `step` s apart. */
  certainty: number[];
  step: number;
  /** Network reaches (such as a tidal delta), by id: they arrive in later frames. */
  /** Other river reaches (tributaries), loading in the background. */
  lines: string[];
  networks: string[];
  /** Mesh reaches (such as a bay), by id: they arrive in later frames. */
  meshes: string[];
}

export interface Failed {
  type: "error";
  message: string;
}

export type Reply = Ready | Frame | Failed;
