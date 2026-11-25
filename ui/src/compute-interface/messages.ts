// The interface between the UI and the compute engine (specification section 4.2).
// The UI sends requests and draws what comes back. These types are all the UI
// knows about the compute side.

/** Requests from the UI to the compute engine. Times are seconds since 1970 UTC. */
export type Request =
  | { type: "open"; cycle: string; reach: string; particles: number }
  | { type: "seek"; t: number }
  | { type: "advance"; to: number }
  | { type: "drift"; dt: number }
  | {
      type: "release";
      x: number; // chainage, m
      start: number;
      duration: number; // s; 0 for all at once
      mass: number; // kg
      halfLife: number | null; // s
    }
  | { type: "clear" };

/** A snapshot of the water at one moment. */
export interface Frame {
  type: "frame";
  t: number;
  /** Flow particles: chainage (m), lateral position (-1..1), speed (m/s). */
  particleX: Float64Array;
  particleLateral: Float32Array;
  particleSpeed: Float64Array;
  /** Parcels in the water: chainage (m) and concentration where each is (kg/m3). */
  parcelX: Float64Array;
  parcelConcentration: Float64Array;
  /** [released, in water, past the end, decayed], kg. */
  massBalance: Float64Array;
  /** Certainty of the flow at this moment, 1 down toward 0 (specification 8.2). */
  certainty: number;
  /** The same in words, for the timeline. */
  words: Words;
}

export type Words = "measured" | "forecast" | "rough_guide";

export interface Ready {
  type: "ready";
  start: number;
  end: number;
  now: number;
  length: number;
  /** Certainty at each published step from `start`, `step` s apart. */
  certainty: number[];
  step: number;
}

export interface Failed {
  type: "error";
  message: string;
}

export type Reply = Ready | Frame | Failed;
