// Colouring the water (specification 3.5 and 8.4): drawing only. The compute
// engine gives each flow particle the value of the chosen field where it is;
// this turns values into colours and describes the legend. Every scale is
// readable with the common colour-vision deficiencies: the Okabe-Ito colours
// in hue order for speed, temperature, salinity and sediment, changing hue at
// values that mean something (for sediment, at even steps of its log scale); purple-orange (diverging) for water level; and the
// Okabe-Ito colours for water sources. The basemap is dark, so no scale uses
// a colour dark enough to vanish against it.

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

const PUOR = ["#8073ac", "#b2abd2", "#f7f7f7", "#fdb863", "#e08214"];

// The Okabe-Ito colours (Okabe and Ito 2008, Color Universal Design), chosen
// to stay distinct from one another with the common colour-vision
// deficiencies. The scales below run through them in hue order, so each
// band of values gets its own hue and the middle of a scale stands out, not
// only its ends.
const OI = { blue: "#0072B2", sky: "#56B4E9", green: "#009E73", yellow: "#F0E442", orange: "#E69F00", vermillion: "#D55E00", purple: "#CC79A7" };

const spread = (lo: number, hi: number, colours: string[]): Stops =>
  colours.map((c, i) => [lo + ((hi - lo) * i) / (colours.length - 1), c]);

/** Stops at given values, placed on the scale by `at`. */
const at = (f: (v: number) => number, pairs: [number, string][]): Stops => pairs.map(([v, c]) => [f(v), c]);

const logSpeed = (v: number) => Math.log10(Math.max(Math.abs(v), 0.01));
const rootSalinity = (v: number) => Math.sqrt(Math.max(v, 0));
const logSediment = (v: number) => Math.log10(Math.max(v, 0.5));

export const SCALES: Partial<Record<Layer, Scale>> = {
  speed: {
    at: logSpeed,
    stops: at(logSpeed, [[0.01, OI.blue], [0.05, OI.sky], [0.2, OI.green], [0.5, OI.yellow], [1, OI.orange], [2, OI.vermillion]]),
    ticks: [[0.01, "0.01"], [0.05, "0.05"], [0.2, "0.2"], [0.5, "0.5"], [1, "1"], [2, "2"]],
    unit: "m/s",
  },
  // Hue changes where water temperature matters to the river's salmon: 13.3 °C
  // (56 °F), the daily limit set for winter-run Chinook below the dams (State
  // Water Board Order 90-5; NMFS's biological opinions), 16 °C, and 20 °C, the
  // usual upper limit for migrating salmon (EPA Region 10, 2003, Temperature
  // Water Quality Standards guidance).
  temperature: {
    at: (v) => v,
    stops: [[6, OI.blue], [10, OI.sky], [13.3, OI.green], [16, OI.yellow], [20, OI.orange], [24, OI.vermillion]],
    ticks: [[6, "6"], [10, "10"], [13.3, "13.3"], [16, "16"], [20, "20"], [24, "24"]],
    unit: "°C",
  },
  // Fresh water to the sea on a square-root scale, so the low end, where the
  // Delta's water is, is not squeezed into one colour. Hue changes at the
  // Venice System's classes of brackish water (Symposium on the
  // Classification of Brackish Waters, Venice, 1958): fresh below 0.5 psu,
  // oligohaline to 5 (X2, 2 psu, inside it), mesohaline to 18, polyhaline to
  // 30, then sea water; so where fresh and salt water mix stands out.
  salinity: {
    at: rootSalinity,
    stops: at(rootSalinity, [[0, OI.blue], [0.5, OI.sky], [2, OI.green], [5, OI.yellow], [18, OI.orange], [30, OI.vermillion], [34, OI.purple]]),
    ticks: [[0.5, "0.5"], [2, "2"], [5, "5"], [18, "18"], [30, "30"]],
    unit: "psu",
  },
  // Suspended sediment, the Delta model's mud (issues #16, #17): from about 1
  // mg/L in clear water to hundreds in a storm, so on a log scale. The hue
  // changes at even steps of that scale (1, 3, 10, 30, 100, 300 mg/L); unlike
  // temperature and salinity, no thresholds are implied.
  sediment: {
    at: logSediment,
    stops: at(logSediment, [[1, OI.blue], [3, OI.sky], [10, OI.green], [30, OI.yellow], [100, OI.orange], [300, OI.vermillion]]),
    ticks: [[1, "1"], [3, "3"], [10, "10"], [30, "30"], [100, "100"], [300, "300"]],
    unit: "mg/L",
  },
  level: { at: (v) => v, stops: spread(-1, 1, PUOR), ticks: [[-1, "−1"], [0, "mean"], [1, "+1"]], unit: "m from the reach's mean" },
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

/** A particle's colour from its water's shares of each source (in `colours`
 * order, the region's own colours, "" for none): the sources' colours mixed. */
export function blend(colours: string[], shares: Float32Array, i: number): string {
  const k = colours.length;
  let total = 0;
  const mix = [0, 0, 0];
  for (let j = 0; j < k; j++) {
    const s = shares[i * k + j];
    if (!Number.isFinite(s) || s <= 0) continue;
    const c = rgb(colours[j] || MISSING);
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
