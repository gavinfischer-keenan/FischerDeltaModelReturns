// The map and its layers: drawing only (specification section 8.4).
// Version 0: a plain background, the river, flow particles and pollutant
// particles. The self-hosted basemap comes next.

import { AttributionControl, Map, NavigationControl, addProtocol, setWorkerUrl, type GeoJSONSource } from "maplibre-gl";
import mapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import "maplibre-gl/dist/maplibre-gl.css";
import { Protocol } from "pmtiles";
import { layers, namedFlavor } from "@protomaps/basemaps";

// MapLibre runs its own worker; ship it with the page rather than fetch it from elsewhere.
setWorkerUrl(mapWorkerUrl);
// The basemap is one PMTiles file on our own server, read in pieces (specification 5.1).
addProtocol("pmtiles", new Protocol().tile);
import type { Frame } from "../compute-interface/messages";
import type { Reach } from "./reach";

const MAX_DRAWN_PARCELS = 4000;

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
    bounds: reach.bounds(),
    fitBoundsOptions: { padding: 40 },
    attributionControl: false,
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
        // Concentration on a log scale; the low end is still a strong colour (spec 8.4).
        "circle-color": [
          "interpolate", ["linear"], ["get", "c"],
          -6, "#5b2a86", -4, "#b5338a", -2, "#f26a4f", 0, "#fde74c",
        ],
        "circle-opacity": 0.9,
      },
    });
  });
  return map;
}

/** Draw a frame from the compute engine. Drawing only: placing chainage on the line. */
export function drawFrame(map: Map, reach: Reach, f: Frame): void {
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
  pollutant.setData({ type: "FeatureCollection", features: ps });
}
