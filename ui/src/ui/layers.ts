// Colouring the water (specification 3.5 and 8.4): drawing only. The compute
// engine gives each flow particle the value of the chosen field where it is;
// this turns values into colours and describes the legend. Every scale is
// readable with the common colour-vision deficiencies: viridis, cividis and
// inferno (sequential), purple-orange (diverging), and the Okabe-Ito palette
// for water sources. The basemap is dark, so each scale starts above its
// darkest colours, which would vanish against it.

import type { Layer } from "../compute-interface/messages";

export const NEUTRAL = "#cfe6ff"; // plain water
const MISSING = "#8a96a6"; // a field this part of the system does not have

type Stops = [number, string][];

interface Scale {
  /** Value to the position on the scale (such as log10 for speed). */
  at: (v: number) => number;
  stops: Stops;
  /** Legend ticks: value and label. */
  ticks: [number, string][];
  unit: string;
}

const VIRIDIS = ["#31688e", "#21918c", "#35b779", "#90d743", "#fde725"];
const CIVIDIS = ["#4f6fa8", "#7f7c75", "#aaa073", "#dcc75e", "#fee838"];
const INFERNO = ["#932667", "#bc3754", "#dd513a", "#fca50a", "#fcffa4"];
const PUOR = ["#8073ac", "#b2abd2", "#f7f7f7", "#fdb863", "#e08214"];

const spread = (lo: number, hi: number, colours: string[]): Stops =>
  colours.map((c, i) => [lo + ((hi - lo) * i) / (colours.length - 1), c]);

export const SCALES: Partial<Record<Layer, Scale>> = {
  speed: {
    at: (v) => Math.log10(Math.max(Math.abs(v), 0.01)),
    stops: spread(-2, Math.log10(2), VIRIDIS),
    ticks: [[0.01, "0.01"], [0.1, "0.1"], [0.5, "0.5"], [2, "2"]],
    unit: "m/s",
  },
  temperature: { at: (v) => v, stops: spread(6, 24, INFERNO), ticks: [[6, "6"], [12, "12"], [18, "18"], [24, "24"]], unit: "°C" },
  salinity: {
    // Fresh water to the sea on a square-root scale, so the low end, where the
    // Delta's water is, is not squeezed into one colour.
    at: (v) => Math.sqrt(Math.max(v, 0)),
    stops: spread(0, Math.sqrt(34), CIVIDIS),
    ticks: [[0.1, "0.1"], [2, "2"], [10, "10"], [34, "34"]],
    unit: "psu",
  },
  level: { at: (v) => v, stops: spread(-1, 1, PUOR), ticks: [[-1, "−1"], [0, "mean"], [1, "+1"]], unit: "m from the reach's mean" },
};

/** Water sources (Okabe-Ito colours), by the names the published fields use. */
export const SOURCES: Record<string, { colour: string; key: string }> = {
  sac: { colour: "#56B4E9", key: "source_sac" },
  sjr: { colour: "#E69F00", key: "source_sjr" },
  east: { colour: "#009E73", key: "source_east" },
  sea: { colour: "#0072B2", key: "source_sea" },
  ag: { colour: "#D55E00", key: "source_ag" },
};

const rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const hex = (c: number[]) => "#" + c.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("");

/** `colour` faded toward neutral as certainty falls (spec 8.4). */
export function fade(colour: string, certainty: number): string {
  const a = rgb(colour), b = rgb("#9aa7b4");
  const c = Math.max(0, Math.min(1, certainty));
  return hex(a.map((v, i) => v * c + b[i] * (1 - c)));
}

function along(stops: Stops, x: number): string {
  if (x <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [x0, c0] = stops[i - 1], [x1, c1] = stops[i];
      const f = (x - x0) / (x1 - x0);
      const a = rgb(c0), b = rgb(c1);
      return hex(a.map((v, k) => v + (b[k] - v) * f));
    }
  }
  return stops[stops.length - 1][1];
}

/** A particle's colour for a value of the layer's field. */
export function colour(layer: Layer, v: number): string {
  if (layer === "plain" || layer === "contaminant") return NEUTRAL;
  const s = SCALES[layer];
  if (!s || !Number.isFinite(v)) return MISSING;
  return along(s.stops, s.at(v));
}

/** A particle's colour from its water's shares of each source (in `names` order): the sources' colours mixed. */
export function blend(names: string[], shares: Float32Array, i: number): string {
  const k = names.length;
  let total = 0;
  const mix = [0, 0, 0];
  for (let j = 0; j < k; j++) {
    const s = shares[i * k + j];
    if (!Number.isFinite(s) || s <= 0) continue;
    const c = rgb(SOURCES[names[j]]?.colour ?? MISSING);
    for (let m = 0; m < 3; m++) mix[m] += c[m] * s;
    total += s;
  }
  return total > 0.05 ? hex(mix.map((v) => v / total)) : MISSING;
}

/** The legend's gradient as a CSS background, and where each tick sits on it (0 to 1). */
export function legend(layer: Layer): { gradient: string; ticks: [number, string][]; unit: string } | null {
  const s = SCALES[layer];
  if (!s) return null;
  const lo = s.stops[0][0], hi = s.stops[s.stops.length - 1][0];
  const gradient = `linear-gradient(to right, ${s.stops.map(([x, c]) => `${c} ${(((x - lo) / (hi - lo)) * 100).toFixed(1)}%`).join(", ")})`;
  return { gradient, ticks: s.ticks.map(([v, label]) => [(s.at(v) - lo) / (hi - lo), label]), unit: s.unit };
}
