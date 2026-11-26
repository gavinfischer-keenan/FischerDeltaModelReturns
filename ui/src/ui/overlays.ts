// Overlays drawn over the water (specification 3.5, 5.3): drawing only. The
// server works out where X2 is and gathers the stations' readings
// (overlays.json beside the cycle's index); this places them on the map at
// the moment shown and says what they are.

import { Popup, type GeoJSONSource, type Map } from "maplibre-gl";
import { whenLoaded } from "./map";

export interface Isohaline {
  id: string;
  threshold: number;
  field: string;
  anchors: { site: string; name: string; km: number }[];
  /** The axis, [lon, lat, km] every ~200 m. */
  axis: [number, number, number][];
  t0: number;
  step_s: number;
  /** Position each step, km; null where it is off the axis (`outside` says which side). */
  km: (number | null)[];
  outside: string[];
  check?: { days: { day: string; km: number | null; model_km?: number | null }[]; mean_difference_km: number | null; largest_difference_km: number | null };
}

export interface Reading {
  v: number;
  t: number;
  model?: number;
}

export interface Station {
  site: string;
  name: string;
  lon: number;
  lat: number;
  readings: Record<string, Reading>;
  /** Who measures it (a `source_<name>` text), and which reach's model sits beside it. */
  source?: string;
  model_reach?: string;
}

export interface Structure {
  id: string;
  kind: "dam" | "pumps" | "gate";
  name: string;
  site: string;
  lon: number;
  lat: number;
  readings: Record<string, Reading>;
  /** Readings are daily means (posted the day after). */
  daily: boolean;
  /** A gate's position now, read from the flow through it. */
  state?: { open: boolean; t: number; changed_at?: number };
  model_note?: string;
}

/** A named place on the water, and the piece of the model it is tied to. */
export interface Landmark {
  id: string;
  name: string;
  lon: number;
  lat: number;
  model?: { reach: string; element: number; km: number };
}

export interface Overlays {
  isohalines: Isohaline[];
  stations: Station[];
  structures?: Structure[];
  landmarks?: Landmark[];
  units: Record<string, string>;
}

export type OverlayKind = "x2" | "stations" | "structures" | "landmarks";

/** Each kind's colour (also the legend's). */
export const STRUCTURE_COLOUR: Record<Structure["kind"], string> = { dam: "#f4a261", pumps: "#ef476f", gate: "#b388eb" };

const X2_COLOUR = "#ffd23f";
const STATION_COLOUR = "#7fdbca";
const LANDMARK_COLOUR = "#e8eef5";
const FONT = ["Noto Sans Medium"];

/** Where an isohaline is at time t: km and a point on the axis, or which side of it, past or future held. */
export function isohalineAt(h: Isohaline, t: number): { km: number | null; outside: string; lon?: number; lat?: number; bearing?: number } {
  if (!h.km.length) return { km: null, outside: "missing" };
  const i = Math.max(0, Math.min(h.km.length - 1, Math.round((t - h.t0) / h.step_s)));
  const km = h.km[i];
  if (km === null) return { km: null, outside: h.outside[i] || "missing" };
  // The axis segment holding km, and its direction.
  let k = 1;
  while (k < h.axis.length - 1 && h.axis[k][2] < km) k++;
  const [x0, y0, k0] = h.axis[k - 1], [x1, y1, k1] = h.axis[k];
  const f = k1 > k0 ? (km - k0) / (k1 - k0) : 0;
  const lon = x0 + (x1 - x0) * f, lat = y0 + (y1 - y0) * f;
  const bearing = Math.atan2((x1 - x0) * Math.cos((lat * Math.PI) / 180), y1 - y0);
  return { km, outside: "", lon, lat, bearing };
}

/** A line across the axis, `half` km either side of the point. */
function across(lon: number, lat: number, bearing: number, half: number): [number, number][] {
  const dLat = half / 111.195, dLon = half / (111.195 * Math.cos((lat * Math.PI) / 180));
  const nx = Math.cos(bearing), ny = -Math.sin(bearing); // perpendicular to the axis's direction
  return [[lon - nx * dLon, lat - ny * dLat], [lon + nx * dLon, lat + ny * dLat]];
}

const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

/** Add the overlays' layers (hidden) once the map's style is ready, then call `added`. */
export function addOverlays(map: Map, ov: Overlays, describe: (s: Station) => string, describeStructure: (s: Structure) => string, added: () => void = () => {}): void {
  const add = () => {
    map.addSource("x2", { type: "geojson", data: empty });
    map.addLayer({ id: "x2-line", type: "line", source: "x2", paint: { "line-color": X2_COLOUR, "line-width": 3, "line-dasharray": [2, 1] }, layout: { visibility: "none" } });
    map.addLayer({
      id: "x2-label",
      type: "symbol",
      source: "x2",
      layout: { visibility: "none", "text-field": ["get", "label"], "text-font": FONT, "text-size": 12, "text-offset": [0, -1.2], "symbol-placement": "point" },
      paint: { "text-color": X2_COLOUR, "text-halo-color": "#0d1726", "text-halo-width": 1.5 },
    });
    map.addSource("stations", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: ov.stations.map((s, i) => ({ type: "Feature", id: i, properties: { site: s.site }, geometry: { type: "Point", coordinates: [s.lon, s.lat] } })),
      },
    });
    map.addLayer({
      id: "stations",
      type: "circle",
      source: "stations",
      layout: { visibility: "none" },
      paint: { "circle-radius": 5, "circle-color": "#0d1726", "circle-stroke-color": STATION_COLOUR, "circle-stroke-width": 2 },
    });
    map.addLayer({
      id: "station-labels",
      type: "symbol",
      source: "stations",
      minzoom: 9,
      layout: { visibility: "none", "text-field": ["get", "site"], "text-font": FONT, "text-size": 11, "text-offset": [0, 1.1], "text-anchor": "top" },
      paint: { "text-color": STATION_COLOUR, "text-halo-color": "#0d1726", "text-halo-width": 1.5 },
    });
    const popup = new Popup({ closeButton: true, maxWidth: "22rem" });
    map.on("click", "stations", (e) => {
      const f = e.features?.[0];
      if (!f) return;
      const s = ov.stations[Number(f.id)];
      popup.setLngLat([s.lon, s.lat]).setHTML(describe(s)).addTo(map);
    });
    const structures = ov.structures ?? [];
    map.addSource("structures", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: structures.map((s, i) => ({
          type: "Feature",
          id: i,
          properties: { kind: s.kind, name: s.name, open: s.state ? s.state.open : null },
          geometry: { type: "Point", coordinates: [s.lon, s.lat] },
        })),
      },
    });
    const kindColour = ["match", ["get", "kind"], "dam", STRUCTURE_COLOUR.dam, "pumps", STRUCTURE_COLOUR.pumps, STRUCTURE_COLOUR.gate] as unknown as string;
    map.addLayer({
      id: "structures",
      type: "circle",
      source: "structures",
      layout: { visibility: "none" },
      paint: {
        "circle-radius": 7,
        // A closed gate is drawn hollow, an open one (or a dam or pumps) filled.
        "circle-color": ["case", ["==", ["get", "open"], false], "#0d1726", kindColour] as unknown as string,
        "circle-stroke-color": kindColour,
        "circle-stroke-width": 2.5,
      },
    });
    map.addLayer({
      id: "structure-labels",
      type: "symbol",
      source: "structures",
      minzoom: 8,
      layout: { visibility: "none", "text-field": ["get", "name"], "text-font": FONT, "text-size": 11, "text-offset": [0, 1.2], "text-anchor": "top", "text-max-width": 12 },
      paint: { "text-color": kindColour, "text-halo-color": "#0d1726", "text-halo-width": 1.5 },
    });
    map.on("click", "structures", (e) => {
      const f = e.features?.[0];
      if (!f) return;
      const s = structures[Number(f.id)];
      popup.setLngLat([s.lon, s.lat]).setHTML(describeStructure(s)).addTo(map);
    });
    // Landmarks: a small mark and the name beside it, under the others' markers.
    map.addSource("landmarks", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: (ov.landmarks ?? []).map((l, i) => ({ type: "Feature", id: i, properties: { name: l.name }, geometry: { type: "Point", coordinates: [l.lon, l.lat] } })),
      },
    });
    map.addLayer(
      {
        id: "landmarks",
        type: "circle",
        source: "landmarks",
        layout: { visibility: "none" },
        paint: { "circle-radius": 3, "circle-color": LANDMARK_COLOUR, "circle-stroke-color": "#0d1726", "circle-stroke-width": 1.5 },
      },
      "stations",
    );
    map.addLayer(
      {
        id: "landmark-labels",
        type: "symbol",
        source: "landmarks",
        layout: {
          visibility: "none",
          "text-field": ["get", "name"],
          "text-font": FONT,
          "text-size": ["interpolate", ["linear"], ["zoom"], 7, 11, 11, 13],
          "text-anchor": "left",
          "text-offset": [0.7, 0],
          "text-max-width": 14,
          "text-optional": true,
        },
        paint: { "text-color": LANDMARK_COLOUR, "text-halo-color": "#0d1726", "text-halo-width": 1.5 },
      },
      "stations",
    );
    for (const id of ["stations", "structures"]) {
      map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
    }
  };
  whenLoaded(map, () => {
    if (map.getSource("x2")) return;
    add();
    added();
  });
}

export function showOverlay(map: Map, kind: OverlayKind, on: boolean): void {
  const ids = {
    x2: ["x2-line", "x2-label"],
    stations: ["stations", "station-labels"],
    structures: ["structures", "structure-labels"],
    landmarks: ["landmarks", "landmark-labels"],
  }[kind];
  for (const id of ids) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

/** Move X2 to time t. */
export function drawIsohaline(map: Map, h: Isohaline, t: number, label: (km: number) => string): void {
  const src = map.getSource("x2") as GeoJSONSource | undefined;
  if (!src) return;
  const at = isohalineAt(h, t);
  if (at.km === null || at.lon === undefined) {
    src.setData(empty);
    return;
  }
  const line = across(at.lon, at.lat!, at.bearing!, 1.5);
  src.setData({
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: line } },
      { type: "Feature", properties: { label: label(at.km) }, geometry: { type: "Point", coordinates: line[1] } },
    ],
  });
}
