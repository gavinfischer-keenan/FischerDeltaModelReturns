// Placing things in a channel network on the map: drawing only.
// The compute engine answers with an element (a channel, or a pool after the
// channels) and a fraction along it; this turns those into longitude and
// latitude using the published channel lines and junction positions.

import { cumulative, metres, pointOnLine } from "./reach";

export interface NetworkGeometry {
  elements: { id: number; up: number; down: number; end_m: number }[];
  nodes: { id: number; lon: number; lat: number; kind?: string }[];
  lines: [number, number][][];
  pools?: { id: string; nodes: number[]; area_m2: number }[];
}

export class NetworkPlace {
  private cum: Float64Array[];
  private poolCentre: [number, number][];
  private poolRadiusM: number[];

  constructor(readonly id: string, readonly geometry: NetworkGeometry) {
    this.cum = geometry.lines.map(cumulative);
    const at = new Map(geometry.nodes.map((n) => [n.id, [n.lon, n.lat] as [number, number]]));
    this.poolCentre = (geometry.pools ?? []).map((p) => {
      const pts = p.nodes.map((n) => at.get(n)).filter((x): x is [number, number] => !!x);
      if (!pts.length) return [0, 0];
      return [pts.reduce((s, q) => s + q[0], 0) / pts.length, pts.reduce((s, q) => s + q[1], 0) / pts.length];
    });
    // Parcels in a pool are spread over a disc about the pool's size.
    this.poolRadiusM = (geometry.pools ?? []).map((p) => Math.sqrt(p.area_m2 / Math.PI) * 0.6);
  }

  get channels(): number {
    return this.geometry.lines.length;
  }

  /** Where element `e` at fraction `f` is, nudged sideways by `lateral` x `halfWidthM`.
   * For a pool, `seed` spreads things steadily over it. */
  place(e: number, f: number, lateral = 0, halfWidthM = 0, seed = 0): [number, number] {
    if (e < this.channels) return pointOnLine(this.geometry.lines[e], this.cum[e], f, lateral, halfWidthM);
    const p = e - this.channels;
    const [lon, lat] = this.poolCentre[p] ?? [0, 0];
    const a = ((seed * 2654435761) % 1000) / 1000 * 2 * Math.PI;
    const r = Math.sqrt(((seed * 40503) % 997) / 997) * (this.poolRadiusM[p] ?? 0);
    const kx = Math.cos((lat * Math.PI) / 180);
    return [lon + (r * Math.cos(a)) / (111_195 * kx), lat + (r * Math.sin(a)) / 111_195];
  }

  /** The channel and fraction along it nearest a point, for placing a release. */
  nearest(lon: number, lat: number): { element: number; fraction: number; metres: number } {
    let best = { element: 0, fraction: 0, metres: Infinity };
    this.geometry.lines.forEach((line, e) => {
      const c = this.cum[e];
      line.forEach((p, i) => {
        const d = metres(p, [lon, lat]);
        if (d < best.metres) best = { element: e, fraction: c[i], metres: d };
      });
    });
    return best;
  }
}
