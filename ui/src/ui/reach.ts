// Placing things along a 1D reach on the map: drawing only.
// The compute engine answers in chainage (m along the reach); this turns
// chainage into longitude and latitude using the published element lines.

export interface ReachGeometry {
  elements: { start_m: number; end_m: number }[];
  lines: [number, number][][];
}

const M_PER_DEG_LAT = 111_195;

export class Reach {
  private starts: Float64Array;
  private cum: Float64Array[]; // cumulative length along each element's line, 0..1

  constructor(readonly geometry: ReachGeometry) {
    this.starts = Float64Array.from(geometry.elements.map((e) => e.start_m));
    this.cum = geometry.lines.map(cumulative);
  }

  get length(): number {
    const e = this.geometry.elements;
    return e[e.length - 1].end_m;
  }

  /** Element index for chainage x. */
  element(x: number): number {
    let lo = 0;
    let hi = this.starts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (this.starts[mid] <= x) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  /** Longitude and latitude at chainage x, nudged sideways by `lateral` x `halfWidthM`. */
  place(x: number, lateral = 0, halfWidthM = 0): [number, number] {
    const e = this.element(x);
    const { start_m, end_m } = this.geometry.elements[e];
    const f = Math.min(1, Math.max(0, (x - start_m) / (end_m - start_m || 1)));
    return pointOnLine(this.geometry.lines[e], this.cum[e], f, lateral, halfWidthM);
  }

  /** The chainage nearest a point, for snapping a click to the river. */
  nearest(lon: number, lat: number): { x: number; metres: number } {
    let best = { x: 0, metres: Infinity };
    this.geometry.lines.forEach((line, e) => {
      const { start_m, end_m } = this.geometry.elements[e];
      const c = this.cum[e];
      line.forEach((p, i) => {
        const d = metres(p, [lon, lat]);
        if (d < best.metres) best = { x: start_m + c[i] * (end_m - start_m), metres: d };
      });
    });
    return best;
  }

  /** Every element line, for drawing the river. */
  asGeoJSON(): GeoJSON.Feature<GeoJSON.MultiLineString> {
    return { type: "Feature", properties: {}, geometry: { type: "MultiLineString", coordinates: this.geometry.lines } };
  }

  bounds(): [[number, number], [number, number]] {
    let [w, s, e, n] = [180, 90, -180, -90];
    for (const line of this.geometry.lines)
      for (const [x, y] of line) {
        w = Math.min(w, x);
        e = Math.max(e, x);
        s = Math.min(s, y);
        n = Math.max(n, y);
      }
    return [
      [w, s],
      [e, n],
    ];
  }
}

/** Cumulative length along a line, 0 at its start to 1 at its end. */
export function cumulative(line: [number, number][]): Float64Array {
  const c = new Float64Array(line.length);
  for (let i = 1; i < line.length; i++) c[i] = c[i - 1] + metres(line[i - 1], line[i]);
  const total = c[c.length - 1] || 1;
  return c.map((v) => v / total);
}

/** The point a fraction f along a line (with its cumulative lengths), nudged
 * sideways by `lateral` (-1..1) x `halfWidthM`. */
export function pointOnLine(line: [number, number][], cum: Float64Array, f: number, lateral = 0, halfWidthM = 0): [number, number] {
  if (line.length < 2) return line[0] ?? [0, 0];
  let i = 1;
  while (i < cum.length - 1 && cum[i] < f) i++;
  const seg = cum[i] - cum[i - 1] || 1;
  const g = Math.min(1, Math.max(0, (f - cum[i - 1]) / seg));
  const [x0, y0] = line[i - 1];
  const [x1, y1] = line[i];
  let lon = x0 + g * (x1 - x0);
  let lat = y0 + g * (y1 - y0);
  if (lateral && halfWidthM) {
    // Perpendicular to the line, in degrees.
    const kx = Math.cos((lat * Math.PI) / 180);
    const dx = (x1 - x0) * kx;
    const dy = y1 - y0;
    const n = Math.hypot(dx, dy) || 1;
    const off = (lateral * halfWidthM) / M_PER_DEG_LAT;
    lon += ((-dy / n) * off) / kx;
    lat += (dx / n) * off;
  }
  return [lon, lat];
}

export function metres(a: [number, number], b: [number, number]): number {
  const kx = Math.cos((((a[1] + b[1]) / 2) * Math.PI) / 180);
  return Math.hypot((b[0] - a[0]) * kx, b[1] - a[1]) * M_PER_DEG_LAT;
}
