// Placing things on a mesh reach on the map: drawing only.
// The compute engine answers with a triangle and two barycentric weights (for
// its second and third nodes); this turns those into longitude and latitude
// using the published node positions, and finds the triangle under a click.

import { metres } from "./reach";

export interface MeshGeometry {
  lon: number[];
  lat: number[];
  triangles: [number, number, number][];
  edges: Record<string, [number, number][]>;
}

export class MeshPlace {
  constructor(readonly id: string, readonly geometry: MeshGeometry) {}

  /** The water nearest a point off the mesh: just inside a triangle at the nearest node. */
  nearest(lon: number, lat: number): { triangle: number; w1: number; w2: number; metres: number } {
    const g = this.geometry;
    let node = 0;
    let best = Infinity;
    for (let i = 0; i < g.lon.length; i++) {
      const d = metres([g.lon[i], g.lat[i]], [lon, lat]);
      if (d < best) {
        best = d;
        node = i;
      }
    }
    const t = Math.max(0, g.triangles.findIndex((tri) => tri.includes(node)));
    const k = g.triangles[t].indexOf(node);
    // Weights for nodes 2 and 3: 0.9 at the nearest node, the rest shared.
    const w = [0.05, 0.05, 0.05];
    w[k] = 0.9;
    return { triangle: t, w1: w[1], w2: w[2], metres: best };
  }

  /** Where triangle `t` at weights `w1`, `w2` is. */
  place(t: number, w1: number, w2: number): [number, number] {
    const [a, b, c] = this.geometry.triangles[t];
    const { lon, lat } = this.geometry;
    const w0 = 1 - w1 - w2;
    return [w0 * lon[a] + w1 * lon[b] + w2 * lon[c], w0 * lat[a] + w1 * lat[b] + w2 * lat[c]];
  }

  /** The triangle under a point and its weights there, or null if the point is not on the mesh. */
  locate(lon: number, lat: number): { triangle: number; w1: number; w2: number } | null {
    const g = this.geometry;
    for (let t = 0; t < g.triangles.length; t++) {
      const [a, b, c] = g.triangles[t];
      const [xa, ya, xb, yb, xc, yc] = [g.lon[a], g.lat[a], g.lon[b], g.lat[b], g.lon[c], g.lat[c]];
      if (lon < Math.min(xa, xb, xc) || lon > Math.max(xa, xb, xc) || lat < Math.min(ya, yb, yc) || lat > Math.max(ya, yb, yc)) continue;
      const d = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc);
      const w0 = ((yb - yc) * (lon - xc) + (xc - xb) * (lat - yc)) / d;
      const w1 = ((yc - ya) * (lon - xc) + (xa - xc) * (lat - yc)) / d;
      const w2 = 1 - w0 - w1;
      if (w0 >= 0 && w1 >= 0 && w2 >= 0) return { triangle: t, w1, w2 };
    }
    return null;
  }
}
