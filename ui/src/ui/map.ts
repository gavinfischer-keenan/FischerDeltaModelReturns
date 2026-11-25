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
        "circle-color": "#cfe6ff",
        "circle-opacity": ["interpolate", ["linear"], ["get", "s"], 0, 0.35, 1.5, 0.9],
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
export function addNetwork(map: Map, id: string, g: NetworkGeometry): void {
  const add = () => {
    const src = `net-${id}`;
    if (map.getSource(src)) return;
    map.addSource(src, {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: g.lines.map((line, k) => ({
          type: "Feature",
          id: k,
          properties: { channel: g.elements[k]?.id },
          geometry: { type: "LineString", coordinates: line },
        })),
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
export function drawFrame(map: Map, reach: Reach, f: Frame, nets: NetworkPlace[] = []): void {
  const flow = map.getSource("flow") as GeoJSONSource | undefined;
  const pollutant = map.getSource("pollutant") as GeoJSONSource | undefined;
  if (!flow || !pollutant) return;
  const pts: GeoJSON.Feature[] = [];
  for (let i = 0; i < f.particleX.length; i++) {
    pts.push({
      type: "Feature",
      properties: { s: f.particleSpeed[i] },
      geometry: { type: "Point", coordinates: reach.place(f.particleX[i], f.particleLateral[i], 40) },
    });
  }
  for (const nf of f.networks) {
    const net = nets.find((x) => x.id === nf.id);
    if (!net) continue;
    for (let i = 0; i < nf.particleElement.length; i++) {
      const e = nf.particleElement[i];
      const lateral = ((i * 2654435761) % 1000) / 500 - 1;
      pts.push({
        type: "Feature",
        properties: { s: Math.abs(nf.velocity[e] ?? 0) },
        geometry: { type: "Point", coordinates: net.place(e, nf.particleFraction[i], lateral, 25, i) },
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
  pollutant.setData({ type: "FeatureCollection", features: ps });
  const c = Math.round(f.certainty * 50) / 50; // restyle only when it visibly changes
  if (c !== drawnCertainty) {
    drawnCertainty = c;
    map.setPaintProperty("pollutant", "circle-color", pollutantColour(c));
  }
}
