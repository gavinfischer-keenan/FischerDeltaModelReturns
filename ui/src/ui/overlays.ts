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
}

export interface Overlays {
  isohalines: Isohaline[];
  stations: Station[];
  units: Record<string, string>;
}

export type OverlayKind = "x2" | "stations";

const X2_COLOUR = "#ffd23f";
const STATION_COLOUR = "#7fdbca";
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
export function addOverlays(map: Map, ov: Overlays, describe: (s: Station) => string, added: () => void = () => {}): void {
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
    map.on("mouseenter", "stations", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "stations", () => (map.getCanvas().style.cursor = ""));
  };
  whenLoaded(map, () => {
    if (map.getSource("x2")) return;
    add();
    added();
  });
}

export function showOverlay(map: Map, kind: OverlayKind, on: boolean): void {
  const ids = kind === "x2" ? ["x2-line", "x2-label"] : ["stations", "station-labels"];
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
