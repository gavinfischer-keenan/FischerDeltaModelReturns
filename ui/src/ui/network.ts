// Placing things in a channel network on the map: drawing only.
// The compute engine answers with an element (a channel, or a pool after the
// channels) and a fraction along it; this turns those into longitude and
// latitude using the published channel lines and junction positions.

import { cumulative, metres, pointOnLine } from "./reach";

/** Where the join's line crosses a channel: the part beyond `at` on the
 * `inside` side is another reach's (docs/format.md, handoffs). */
export interface ChannelCut {
  element: number;
  at: number;
  inside: "up" | "down";
}

/** The network's own part of a channel: (from, to) as fractions along it. */
export function ownPart(cut: ChannelCut | undefined): [number, number] {
  if (!cut) return [0, 1];
  return cut.inside === "down" ? [0, cut.at] : [cut.at, 1];
}

export interface NetworkGeometry {
  /** `width_m`: the channel's mean water-surface width (docs/format.md). */
  elements: { id: number; up: number; down: number; end_m: number; width_m?: number }[];
  nodes: { id: number; lon: number; lat: number; kind?: string }[];
  lines: [number, number][][];
  /** `lon`, `lat`: where the pool lies, if published. */
  pools?: { id: string; nodes: number[]; area_m2: number; lon?: number; lat?: number }[];
}

export class NetworkPlace {
  private cum: Float64Array[];
  private poolCentre: [number, number][];
  private poolRadiusM: number[];

  /** Channels another reach tracks (a mesh covers them): not shown, not offered for releases. */
  readonly hidden: Set<number>;

  /** Channels cut by the join: shown and offered only on the network's side. */
  readonly cut: Map<number, ChannelCut>;

  constructor(readonly id: string, readonly geometry: NetworkGeometry, hidden: number[] = [], cuts: ChannelCut[] = []) {
    this.hidden = new Set(hidden);
    this.cut = new Map(cuts.map((c) => [c.element, c]));
    this.cum = geometry.lines.map(cumulative);
    const at = new Map(geometry.nodes.map((n) => [n.id, [n.lon, n.lat] as [number, number]]));
    this.poolCentre = (geometry.pools ?? []).map((p) => {
      if (p.lon !== undefined && p.lat !== undefined) return [p.lon, p.lat];
      const pts = p.nodes.map((n) => at.get(n)).filter((x): x is [number, number] => !!x);
      if (!pts.length) return [0, 0];
      return [pts.reduce((s, q) => s + q[0], 0) / pts.length, pts.reduce((s, q) => s + q[1], 0) / pts.length];
    });
    // Parcels in a pool are spread over a disc about the pool's size.
    this.poolRadiusM = (geometry.pools ?? []).map((p) => Math.sqrt(p.area_m2 / Math.PI) * 0.6);
  }

  /** Half a channel's width, m, to spread its particles across: the water's
   * own width where the geometry gives it. */
  halfWidth(e: number): number {
    const w = this.geometry.elements[e]?.width_m;
    return w && w > 0 ? w / 2 : 25;
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
      if (this.hidden.has(e)) return;
      const c = this.cum[e];
      const [a, b] = ownPart(this.cut.get(e));
      line.forEach((p, i) => {
        if (c[i] < a || c[i] > b) return;
        const d = metres(p, [lon, lat]);
        if (d < best.metres) best = { element: e, fraction: c[i], metres: d };
      });
    });
    return best;
  }
}
