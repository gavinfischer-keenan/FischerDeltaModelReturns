// The map and its layers: drawing only (specification section 8.4).
// The self-hosted basemap, the river, flow particles and pollutant particles,
// the pollutant's colour faded toward neutral as certainty falls.

import {
  AttributionControl,
  Map,
  NavigationControl,
  addProtocol,
  setWorkerUrl,
  type ExpressionSpecification,
  type GeoJSONSource,
} from "maplibre-gl";
import mapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import "maplibre-gl/dist/maplibre-gl.css";
import { Protocol } from "pmtiles";
import { layers, namedFlavor } from "@protomaps/basemaps";

// MapLibre runs its own worker; ship it with the page rather than fetch it from elsewhere.
setWorkerUrl(mapWorkerUrl);
// The basemap is one PMTiles file on our own server, read in pieces (specification 5.1).
addProtocol("pmtiles", new Protocol().tile);
import type { Frame } from "../compute-interface/messages";
import { blend, colour, fade } from "./layers";
import type { MeshGeometry, MeshPlace } from "./mesh";
import type { NetworkPlace } from "./network";
import type { Reach } from "./reach";

const MAX_DRAWN_PARCELS = 4000;

// Concentration on a log scale; the low end is still a strong colour (spec 8.4).
const POLLUTANT_STOPS: [number, string][] = [[-6, "#5b2a86"], [-4, "#b5338a"], [-2, "#f26a4f"], [0, "#fde74c"]];
const NEUTRAL = "#9aa7b4";

/** `colour` faded toward neutral as certainty falls (spec 8.4). */
function faded(colour: string, certainty: number): string {
  const rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const a = rgb(colour), b = rgb(NEUTRAL);
  const c = Math.max(0, Math.min(1, certainty));
  return "#" + a.map((v, i) => Math.round(v * c + b[i] * (1 - c)).toString(16).padStart(2, "0")).join("");
}

function pollutantColour(certainty: number): ExpressionSpecification {
  return ["interpolate", ["linear"], ["get", "c"], ...POLLUTANT_STOPS.flatMap(([v, h]) => [v, faded(h, certainty)])] as ExpressionSpecification;
}
let drawnCertainty = 1;
let drawnRings = false;
const RING = "#ff2fd1";

export function createMap(container: HTMLElement, reach: Reach, region: string): Map {
  // Fonts, icons and tiles all come from our own server: nothing from a third party.
  const base = location.href.replace(/[?#].*$/, "").replace(/[^/]*$/, "");
  const map = new Map({
    container,
    style: {
      version: 8,
      glyphs: `${base}basemap/assets/fonts/{fontstack}/{range}.pbf`,
      sprite: `${base}basemap/assets/sprites/v4/dark`,
      sources: {
        protomaps: {
          type: "vector",
          url: `pmtiles://${base}basemap/${region}.pmtiles`,
          attribution: '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>',
        },
      },
      layers: layers("protomaps", namedFlavor("dark"), { lang: "en" }),
    },
    // A view in the address (a shared link) wins; otherwise fit the river.
    bounds: /[#&]view=/.test(location.hash) ? undefined : reach.bounds(),
    fitBoundsOptions: { padding: 40 },
    attributionControl: false,
    hash: "view", // the view lives in the address (#view=zoom/lat/lon), so a view can be shared
  });
  map.addControl(new AttributionControl({ compact: true }), "bottom-right");
  // Keep the view inside the region's basemap extract, so its edges never show.
  map.once("sourcedata", () => {
    const b = (map.getSource("protomaps") as { bounds?: [number, number, number, number] } | undefined)?.bounds;
    if (b) map.setMaxBounds([b[0], b[1], b[2], b[3]]);
  });
  map.addControl(new NavigationControl({ showCompass: false }), "top-right");
  // Once the visitor moves the map, the reaches loading later leave the view alone.
  map.on("movestart", (e) => {
    if ((e as { originalEvent?: Event }).originalEvent) viewTouched = true;
  });
  map.on("load", () => {
    map.addSource("river", { type: "geojson", data: reach.asGeoJSON() });
    map.addLayer({
      id: "river",
      type: "line",
      source: "river",
      paint: { "line-color": "#2b6cb0", "line-opacity": 0.75, "line-width": ["interpolate", ["linear"], ["zoom"], 6, 2, 12, 10] },
      layout: { "line-cap": "round", "line-join": "round" },
    });
    const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
    map.addSource("flow", { type: "geojson", data: empty });
    map.addLayer({
      id: "flow",
      type: "circle",
      source: "flow",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 0.8, 12, 2.5],
        "circle-color": ["coalesce", ["get", "col"], "#cfe6ff"],
        // Plain water fades where it is slow; coloured by a field, every particle shows its colour.
        "circle-opacity": ["case", ["has", "col"], 0.95, ["interpolate", ["linear"], ["get", "s"], 0, 0.35, 1.5, 0.9]],
      },
    });
    map.addSource("pollutant", { type: "geojson", data: empty });
    map.addLayer({
      id: "pollutant",
      type: "circle",
      source: "pollutant",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 1.6, 12, 4],
        "circle-color": pollutantColour(1),
        "circle-opacity": 0.9,
      },
    });
  });
  return map;
}

interface NetworkGeometry {
  elements: { id: number }[];
  lines: [number, number][][];
}

/** Draw a channel network's channels (docs/format.md, network reaches), each
 * coloured later by its current speed (drawNetworks). */
export function addNetwork(map: Map, id: string, g: NetworkGeometry, hidden: Set<number> = new Set()): void {
  const add = () => {
    const src = `net-${id}`;
    if (map.getSource(src)) return;
    map.addSource(src, {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: g.lines.flatMap((line, k) =>
          hidden.has(k)
            ? []
            : [{
                type: "Feature" as const,
                id: k,
                properties: { channel: g.elements[k]?.id },
                geometry: { type: "LineString" as const, coordinates: line },
              }],
        ),
      },
    });
    map.addLayer(
      {
        id: src,
        type: "line",
        source: src,
        paint: {
          // Speed, either way: dark when slack, bright when the tide runs hard.
          "line-color": [
            "interpolate", ["linear"], ["abs", ["coalesce", ["feature-state", "v"], 0]],
            0, "#1f3b5c", 0.2, "#2b6cb0", 0.6, "#63b3ed", 1.2, "#e6f4ff",
          ],
          "line-width": ["interpolate", ["linear"], ["zoom"], 8, 1.2, 12, 4],
          "line-opacity": 0.9,
        },
        layout: { "line-cap": "round", "line-join": "round" },
      },
      map.getLayer("flow") ? "flow" : undefined,
    );
  };
  if (map.isStyleLoaded()) add();
  else map.once("load", add);
}

// The first view: the river, widened to each reach as it loads, unless the
// address held a view or the visitor has moved the map since.
let viewBounds: [number, number, number, number] | null = null;
let viewTouched = /[#&]view=/.test(location.hash);

/** Widen the first view to take in these points (drawing only). */
export function widenView(map: Map, lons: number[], lats: number[]): void {
  if (viewTouched || !lons.length) return;
  const b: [number, number, number, number] = [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
  if (!viewBounds) {
    const c = map.getBounds();
    viewBounds = [c.getWest(), c.getSouth(), c.getEast(), c.getNorth()];
  }
  viewBounds = [Math.min(viewBounds[0], b[0]), Math.min(viewBounds[1], b[1]), Math.max(viewBounds[2], b[2]), Math.max(viewBounds[3], b[3])];
  // Room at the top for the title and at the bottom for the controls.
  map.fitBounds(viewBounds, { padding: { top: 70, bottom: 190, left: 30, right: 30 }, duration: 0 });
}

/** Draw a mesh reach's water (docs/format.md, 2D reaches) under the particles. */
export function addMesh(map: Map, id: string, g: MeshGeometry): void {
  const add = () => {
    const src = `mesh-${id}`;
    if (map.getSource(src)) return;
    map.addSource(src, {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: g.triangles.map(([a, b, c]) => ({
          type: "Feature",
          properties: {},
          geometry: { type: "Polygon", coordinates: [[[g.lon[a], g.lat[a]], [g.lon[b], g.lat[b]], [g.lon[c], g.lat[c]], [g.lon[a], g.lat[a]]]] },
        })),
      },
    });
    map.addLayer(
      { id: src, type: "fill", source: src, paint: { "fill-color": "#1f3b5c", "fill-opacity": 0.45, "fill-antialias": false } },
      map.getLayer("flow") ? "flow" : undefined,
    );
  };
  if (map.isStyleLoaded()) add();
  else map.once("load", add);
}

const drawnNetworkTime = new globalThis.Map<string, number>();

/** Colour each network channel by its speed at the frame's time. */
export function drawNetworks(map: Map, f: Frame): void {
  for (const n of f.networks) {
    const src = `net-${n.id}`;
    if (!map.getSource(src)) continue;
    // Repaint at most every 5 minutes of model time: speeds change slowly.
    if (Math.abs((drawnNetworkTime.get(n.id) ?? -1e12) - f.t) < 300) continue;
    drawnNetworkTime.set(n.id, f.t);
    for (let k = 0; k < n.velocity.length; k++) map.setFeatureState({ source: src, id: k }, { v: n.velocity[k] });
  }
}

/** Draw a frame from the compute engine. Drawing only: placing positions on the lines. */
export function drawFrame(map: Map, reach: Reach, f: Frame, nets: NetworkPlace[] = [], meshes: MeshPlace[] = []): void {
  const flow = map.getSource("flow") as GeoJSONSource | undefined;
  const pollutant = map.getSource("pollutant") as GeoJSONSource | undefined;
  if (!flow || !pollutant) return;
  const pts: GeoJSON.Feature[] = [];
  // Each flow particle's colour from the layer (layers.ts), faded with certainty.
  const coloured = f.layer !== "plain" && f.layer !== "contaminant";
  const paint = (value?: Float32Array, shares?: Float32Array) => (i: number): string | undefined => {
    if (!coloured) return undefined;
    const c = f.layer === "source" ? (shares ? blend(f.sources, shares, i) : undefined) : colour(f.layer, value ? value[i] : NaN);
    return c && fade(c, f.certainty);
  };
  const riverCol = paint(f.particleValue, f.particleShares);
  for (let i = 0; i < f.particleX.length; i++) {
    pts.push({
      type: "Feature",
      properties: { s: f.particleSpeed[i], col: riverCol(i) },
      geometry: { type: "Point", coordinates: reach.place(f.particleX[i], f.particleLateral[i], 40) },
    });
  }
  for (const nf of f.networks) {
    const net = nets.find((x) => x.id === nf.id);
    if (!net) continue;
    const netCol = paint(nf.particleValue, nf.particleShares);
    for (let i = 0; i < nf.particleElement.length; i++) {
      const e = nf.particleElement[i];
      if (net.hidden.has(e)) continue; // another reach shows that water
      const lateral = ((i * 2654435761) % 1000) / 500 - 1;
      pts.push({
        type: "Feature",
        properties: { s: Math.abs(nf.velocity[e] ?? 0), col: netCol(i) },
        geometry: { type: "Point", coordinates: net.place(e, nf.particleFraction[i], lateral, 25, i) },
      });
    }
  }
  for (const mf of f.meshes) {
    const mesh = meshes.find((x) => x.id === mf.id);
    if (!mesh) continue;
    const meshCol = paint(mf.particleValue, mf.particleShares);
    for (let i = 0; i < mf.particleTriangle.length; i++) {
      pts.push({
        type: "Feature",
        properties: { s: mf.particleSpeed[i], col: meshCol(i) },
        geometry: { type: "Point", coordinates: mesh.place(mf.particleTriangle[i], mf.particleWeights[2 * i], mf.particleWeights[2 * i + 1]) },
      });
    }
  }
  flow.setData({ type: "FeatureCollection", features: pts });
  const n = f.parcelX.length;
  const every = Math.max(1, Math.ceil(n / MAX_DRAWN_PARCELS));
  const ps: GeoJSON.Feature[] = [];
  for (let i = 0; i < n; i += every) {
    const lateral = ((i * 2654435761) % 1000) / 500 - 1; // a steady spread across the channel
    ps.push({
      type: "Feature",
      properties: { c: Math.log10(Math.max(1e-9, f.parcelConcentration[i] * 1000)) }, // mg/L
      geometry: { type: "Point", coordinates: reach.place(f.parcelX[i], lateral, 40) },
    });
  }
  for (const nf of f.networks) {
    const net = nets.find((x) => x.id === nf.id);
    if (!net) continue;
    const m = nf.parcelElement.length;
    const each = Math.max(1, Math.ceil(m / MAX_DRAWN_PARCELS));
    for (let i = 0; i < m; i += each) {
      const lateral = ((i * 2654435761) % 1000) / 500 - 1;
      ps.push({
        type: "Feature",
        properties: { c: Math.log10(Math.max(1e-9, nf.parcelConcentration[i] * 1000)) }, // mg/L
        geometry: { type: "Point", coordinates: net.place(nf.parcelElement[i], nf.parcelFraction[i], lateral, 25, i) },
      });
    }
  }
  for (const mf of f.meshes) {
    const mesh = meshes.find((x) => x.id === mf.id);
    if (!mesh) continue;
    const m = mf.parcelTriangle.length;
    const each = Math.max(1, Math.ceil(m / MAX_DRAWN_PARCELS));
    for (let i = 0; i < m; i += each) {
      ps.push({
        type: "Feature",
        properties: { c: Math.log10(Math.max(1e-9, mf.parcelConcentration[i] * 1000)) }, // mg/L
        geometry: { type: "Point", coordinates: mesh.place(mf.parcelTriangle[i], mf.parcelWeights[2 * i], mf.parcelWeights[2 * i + 1]) },
      });
    }
  }
  pollutant.setData({ type: "FeatureCollection", features: ps });
  const c = Math.round(f.certainty * 50) / 50; // restyle only when it visibly changes
  const rings = f.layer !== "contaminant";
  if (c !== drawnCertainty || rings !== drawnRings) {
    drawnCertainty = c;
    drawnRings = rings;
    // Coloured by something else, the plume is drawn as rings in one strong
    // colour, so it is not lost over the field (spec 8.4).
    map.setPaintProperty("pollutant", "circle-color", rings ? "rgba(0,0,0,0)" : pollutantColour(c));
    map.setPaintProperty("pollutant", "circle-stroke-color", fade(RING, c));
    map.setPaintProperty("pollutant", "circle-stroke-width", rings ? 1.4 : 0);
  }
}
