<script lang="ts">
  // The page: map, timeline and controls. Display only: every number shown
  // comes from the compute engine through the interface (specification 4.2).
  import { onMount } from "svelte";
  import type { Map } from "maplibre-gl";
  import { connect } from "../compute-interface/connect";
  import type { Frame, Layer, Ready, Words } from "../compute-interface/messages";
  import text from "../../../text/en.json";
  import { Reach, type ReachGeometry } from "./reach";
  import { addMesh, createMap, drawFrame, widenView } from "./map";
  import { MeshPlace, type MeshGeometry } from "./mesh";
  import { legend } from "./layers";
  import { NetworkPlace, type ChannelCut, type NetworkGeometry } from "./network";
  import { addOverlays, drawIsohaline, isohalineAt, showOverlay, STRUCTURE_COLOUR, type Overlays, type Station, type Structure } from "./overlays";
  /** A join between reaches, as index.json gives it (docs/format.md, handoffs). */
  type Join = { from: string; to?: string; owned?: number[]; cuts?: ChannelCut[] };

  const params = new URLSearchParams(location.search);
  const region = params.get("region") ?? "sf-bay-delta";
  // Absolute, because the compute engine's worker resolves URLs from its own script's folder.
  const cycle = new URL(`data/${region}/current`, location.href).href;
  const FLOW_PARTICLES = 3000;
  const NOW_SPEEDUP = 900; // in "Now", 1 s on screen shows 15 min of the water's motion

  let mapEl: HTMLDivElement;
  let map: Map;
  let reach: Reach;
  const engine = connect();

  let ready = $state<Ready | null>(null);
  let error = $state<string | null>(null);
  type Mode = "now" | "past" | "next";
  let mode = $state<Mode>("now");
  let words = $state<Words>("measured");
  let playing = $state(false);
  let hoursPerSecond = $state(2);
  let t = $state(0);
  let balance = $state<number[]>(new Array(12).fill(0)); // the slots of Frame.massBalance
  let fickianM = $state<number | null>(null);
  const LAYERS: Layer[] = ["plain", "contaminant", "speed", "temperature", "salinity", "sediment", "level", "source"];
  let layer = $state<Layer>("plain");
  let sources = $state<{ label: string; colour: string }[]>([]);
  const key = (k: string) => (text as Record<string, string>)[k] ?? k;
  function setLayer(l: Layer) {
    layer = l;
    engine.send({ type: "layer", layer: l });
  }
  let overlays = $state<Overlays | null>(null);
  let showX2 = $state(false);
  let showStations = $state(false);
  let showStructures = $state(false);
  let showLandmarks = $state(true); // on at first: they say where things are
  const x2 = $derived(overlays?.isohalines.find((h) => h.id === "x2") ?? null);
  const x2Now = $derived(x2 ? isohalineAt(x2, t) : null);
  const x2Label = (km: number) => `X2 · ${km.toFixed(1)} km`;
  function toggle(kind: "x2" | "stations" | "structures" | "landmarks", on: boolean) {
    if (kind === "x2") showX2 = on;
    else if (kind === "stations") showStations = on;
    else if (kind === "structures") showStructures = on;
    else showLandmarks = on;
    if (map) showOverlay(map, kind, on);
    if (kind === "x2" && on && map && x2) drawIsohaline(map, x2, t, x2Label);
  }
  const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
  const UNIT: Record<string, string> = { degC: "°C", "uS/cm": "µS/cm", "mg/L": "mg/L", NTU: "NTU", FNU: "FNU", "m3/s": "m³/s", psu: "psu", "m/s": "m/s" };
  const n0 = (x: number) => Math.round(x).toLocaleString("en-US");
  /** A flow in m³/s, with cubic feet per second beside it, as the agencies report it. */
  const flow = (q: number) => `<b>${n0(q)}</b> m³/s (${n0(q / 0.028316846592)} cfs)`;
  /** Water held, in million m³, with thousand acre-feet beside it. */
  const held = (v: number) => `<b>${n0(v / 1e6)}</b> million m³ (${n0(v / 1233.48184 / 1000)} thousand acre-feet)`;
  const day = (s: number) => new Date(s * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: ready?.timezone ?? "UTC" });
  /** A dam's, pumping plant's or gate's popup: what it releases, pumps or lets through, and a gate's position. */
  function describeStructure(s: Structure): string {
    const rows = Object.entries(s.readings).map(([p, r]) => {
      const unit = overlays!.units[p];
      const v = unit === "m3/s" ? flow(r.v) : unit === "m3" ? held(r.v) : `<b>${r.v}</b> ${unit ?? ""}`;
      const age = s.daily ? text.structure_daily.replace("{day}", day(r.t)) : `${Math.max(0, Math.round((Date.now() / 1000 - r.t) / 3600))} h`;
      return `<tr><td>${esc(key(`reading_${p}`))}</td><td>${v}</td><td class="age">${age}</td></tr>`;
    });
    let state = "";
    if (s.state) {
      state = `<p class="state"><b>${s.state.open ? text.gate_open : text.gate_closed}</b> · ${text.gate_from_flow}${s.state.changed_at ? ` · ${text.gate_since} ${fmt(s.state.changed_at)}` : ""}</p>`;
    }
    const note = s.model_note ? `<p>${esc(key(`model_note_${s.model_note}`))}</p>` : "";
    return `<div class="station"><b>${esc(s.name)}</b> <span class="site">${esc(s.site)}</span>${state}<table>${rows.join("") || `<tr><td>${text.station_no_readings}</td></tr>`}</table>${note}<p>${text.structure_source}</p></div>`;
  }
  /** A station's popup: each reading, the model's value beside it where it has one, and how old it is. */
  function describe(s: Station): string {
    const rows = Object.entries(s.readings).map(([p, r]) => {
      const u = UNIT[overlays!.units[p]] ?? overlays!.units[p] ?? "";
      const v = (x: number) => (Math.abs(x) >= 100 ? x.toFixed(0) : x.toPrecision(3));
      const model = r.model !== undefined ? ` <span class="model">${text.station_model} ${v(r.model)}</span>` : "";
      const age = Math.max(0, Math.round((Date.now() / 1000 - r.t) / 3600));
      return `<tr><td>${esc(key(`reading_${p}`))}</td><td><b>${v(r.v)}</b> ${u}${model}</td><td class="age">${age < 1 ? text.station_just_now : `${age} h`}</td></tr>`;
    });
    const src = key(`source_${s.source}`);  // the station's own source, as published (#66, #89)
    const model = s.model_reach ? (key(`model_${s.model_reach}`) !== `model_${s.model_reach}` ? key(`model_${s.model_reach}`) : text.model_river) : text.station_no_model;
    return `<div class="station"><b>${esc(s.name)}</b> <span class="site">${esc(s.site)}</span><table>${rows.join("") || `<tr><td>${text.station_no_readings}</td></tr>`}</table><p>${text.station_measured}: ${esc(src)}. ${text.station_modelled}: ${esc(model)}.</p></div>`;
  }
  let netFlow = $state(false);
  function setFlow(net: boolean) {
    netFlow = net;
    engine.send({ type: "flow", net });
  }
  const nets: NetworkPlace[] = [];
  /** Other river reaches (tributaries from their dams), by id. */
  const lineReaches = new globalThis.Map<string, Reach>();
  const meshes: MeshPlace[] = [];
  let placing = $state(false);
  let draft = $state<{
    x: number;
    lon: number;
    lat: number;
    network?: { id: string; element: number; fraction: number };
    mesh?: { id: string; triangle: number; w1: number; w2: number };
    line?: { id: string; x: number };
  } | null>(null);
  let mass = $state(100);
  let continuousHours = $state(0);
  let halfLifeHours = $state<number | null>(null);
  let kdLPerKg = $state<number | null>(null);

  let busy = false;
  let pending = 0; // the request whose frame frees the play loop
  let last = 0;

  /** Send a request the play loop waits on; only its own frame moves the clock. */
  function request(r: Parameters<typeof engine.send>[0]): void {
    busy = true;
    pending = engine.send(r);
  }

  const fmt = (s: number) =>
    new Date(s * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: ready?.timezone ?? "UTC", timeZoneName: "short" }); // the region's own clock (#67)

  function onFrame(f: Frame) {
    // A frame the engine sent of its own accord, or one answering an older
    // request, is drawn but does not move the clock or free the loop.
    if (f.seq === pending) {
      busy = false;
      t = f.t;
    } else if (f.seq === undefined && !busy) {
      t = f.t;
    }
    balance = Array.from(f.massBalance);
    fickianM = f.fickianM;
    sources = f.sourceLabels.map((label, j) => ({ label, colour: f.sourceColours[j] }));
    words = f.words;
    if (map && reach) drawFrame(map, reach, f, nets, meshes, lineReaches);
    if (map && showX2 && x2) drawIsohaline(map, x2, f.t, x2Label);
  }

  function tick(ms: number) {
    const dt = last ? Math.min(0.1, (ms - last) / 1000) : 0;
    last = ms;
    if (ready && !busy) {
      if (mode === "now") {
        request({ type: "drift", dt: dt * NOW_SPEEDUP }); // "Now" holds the moment and lets the water drift
      } else if (playing) {
        const to = Math.min(ready.end, t + dt * hoursPerSecond * 3600);
        if (to >= ready.end) playing = false;
        request({ type: "advance", to });
      }
    }
    requestAnimationFrame(tick);
  }

  function setMode(m: Mode) {
    mode = m;
    playing = m === "next";
    if (!ready) return;
    const to = m === "past" ? ready.start : ready.now;
    t = to; // so the play loop starts from here
    request({ type: "seek", t: to });
  }

  // The slider's track, shaded by certainty: full colour where measured, fading
  // through the forecast (specification 3.4). Display only: the values come
  // from the compute engine.
  function track(r: Ready): string {
    const span = r.end - r.start;
    if (span <= 0) return "";
    const pct = (s: number) => ((s - r.start) / span) * 100;
    const stops = [`#2f6db5 0%`, `#2f6db5 ${pct(r.now).toFixed(2)}%`];
    const every = Math.max(1, Math.floor(r.certainty.length / 40));
    for (let i = 0; i < r.certainty.length; i += every) {
      const s = r.start + i * r.step;
      if (s <= r.now) continue;
      const a = 0.15 + 0.85 * r.certainty[i];
      stops.push(`rgba(47, 109, 181, ${a.toFixed(2)}) ${pct(s).toFixed(2)}%`);
    }
    return `background: linear-gradient(to right, ${stops.join(", ")});`;
  }
  const forecastAhead = $derived(ready !== null && ready.end > ready.now);

  function scrub(e: Event) {
    if (!ready) return;
    const to = Number((e.target as HTMLInputElement).value);
    mode = to > ready.now ? "next" : "past";
    playing = false;
    busy = true;
    t = to;
    request({ type: "seek", t: to });
  }

  // Without a forecast there is no future to play into, so a release made in
  // "Now" starts this far back and plays forward to now.
  const NO_FORECAST_LOOKBACK = 3 * 24 * 3600;
  // A click this far from any water, m, places no release.
  const MAX_SNAP_M = 3000;

  function release() {
    if (!draft || !ready) return;
    const back = mode === "now" && !forecastAhead;
    const start = back ? Math.max(ready.start, ready.now - NO_FORECAST_LOOKBACK) : mode === "now" ? ready.now : t;
    if (mode === "now") {
      t = start; // so the play loop starts from here, not from the old "now"
      request({ type: "seek", t: start });
    }
    request({
      type: "release",
      x: draft.x,
      // A plain copy: a reactive proxy cannot be sent to the worker.
      network: draft.network ? $state.snapshot(draft.network) : undefined,
      mesh: draft.mesh ? $state.snapshot(draft.mesh) : undefined,
      line: draft.line ? $state.snapshot(draft.line) : undefined,
      start,
      duration: Math.max(0, continuousHours) * 3600,
      mass,
      halfLife: halfLifeHours ? halfLifeHours * 3600 : null,
      kd: kdLPerKg && kdLPerKg > 0 ? kdLPerKg : null,
    });
    draft = null;
    if (layer !== "contaminant") setLayer("contaminant"); // the plume's own colours (spec 3.5)
    if (mode === "now") {
      mode = back ? "past" : "next";
      playing = true;
    }
  }

  onMount(async () => {
    document.title = text.title; // the page title comes from the text files too
    engine.onReply((r) => {
      if (r.type === "ready") {
        // Channels a mesh reach tracks (index.json handoffs) are left to it.
        const ix = fetch(`${cycle}/index.json`).then((x) => x.json());
        const joins = ix.then((x: { handoffs?: Join[] }) => x.handoffs ?? []);
        // What is drawn over the water: X2 and the stations (overlays.json).
        ix.then((x: { overlays?: string }) => (x.overlays ? fetch(`${cycle}/${x.overlays}`).then((o) => o.json()) : null))
          .then((o: Overlays | null) => {
            if (!o) return;
            overlays = o;
            // Once drawn, show whichever the visitor has already switched on.
            addOverlays(map, o, describe, describeStructure, () => {
              showOverlay(map, "x2", showX2);
              showOverlay(map, "stations", showStations);
              showOverlay(map, "structures", showStructures);
              showOverlay(map, "landmarks", showLandmarks);
              if (showX2 && x2) drawIsohaline(map, x2, t, x2Label);
            });
          })
          .catch((e) => console.warn("overlays not shown:", e)); // the water is shown whether or not its overlays load
        for (const id of r.lines) {
          fetch(`${cycle}/geometry/${id}.json`)
            .then((x) => x.json())
            .then((g: ReachGeometry) => lineReaches.set(id, new Reach(g)));
        }
        for (const id of r.networks) {
          Promise.all([fetch(`${cycle}/geometry/${id}.json`).then((x) => x.json()), joins]).then(([g, hs]: [NetworkGeometry, Join[]]) => {
            const mine = hs.filter((h) => h.from === id && r.meshes.length);
            const place = new NetworkPlace(id, g, mine.flatMap((h) => h.owned ?? []), mine.flatMap((h) => h.cuts ?? []));
            nets.push(place);
          });
        }
        for (const id of r.meshes) {
          fetch(`${cycle}/geometry/${id}.json`)
            .then((x) => x.json())
            .then((g: MeshGeometry) => {
              addMesh(map, id, g);
              meshes.push(new MeshPlace(id, g));
              widenView(map, g.lon, g.lat);
            });
        }
        ready = r;
        t = r.now;
      } else if (r.type === "frame") onFrame(r);
      else error = r.message;
    });
    try {
      // The main reach: the cycle's first river-routing reach (the publisher's own choice).
      const index = (await fetch(`${cycle}/index.json`, { cache: "no-store" }).then((r) => r.json())) as { reaches: { id: string; kind: string }[] };
      const main = (index.reaches.find((x) => x.kind === "river-routing") ?? index.reaches[0]).id;
      const geometry = (await fetch(`${cycle}/geometry/${main}.json`).then((r) => r.json())) as ReachGeometry;
      reach = new Reach(geometry);
      map = createMap(mapEl, reach, region);
      map.on("click", (e) => {
        if (!placing) return;
        // Open water of a mesh reach takes a release just where it is clicked.
        for (const m of meshes) {
          const at = m.locate(e.lngLat.lng, e.lngLat.lat);
          if (at) {
            draft = { x: 0, lon: e.lngLat.lng, lat: e.lngLat.lat, mesh: { id: m.id, ...at } };
            placing = false;
            return;
          }
        }
        // Otherwise the nearest water: the river, a channel of a network reach,
        // or the edge of a mesh reach. A click far from any water places nothing.
        const near = reach.nearest(e.lngLat.lng, e.lngLat.lat);
        const [lon, lat] = reach.place(near.x);
        let best = near.metres;
        let pick: typeof draft = { x: near.x, lon, lat };
        for (const [id, lr] of lineReaches) {
          const n = lr.nearest(e.lngLat.lng, e.lngLat.lat);
          if (n.metres < best) {
            best = n.metres;
            const [llon, llat] = lr.place(n.x);
            pick = { x: 0, lon: llon, lat: llat, line: { id, x: n.x } };
          }
        }
        for (const net of nets) {
          const n = net.nearest(e.lngLat.lng, e.lngLat.lat);
          if (n.metres < best) {
            best = n.metres;
            const [nlon, nlat] = net.place(n.element, n.fraction);
            pick = { x: 0, lon: nlon, lat: nlat, network: { id: net.id, element: n.element, fraction: n.fraction } };
          }
        }
        for (const m of meshes) {
          const n = m.nearest(e.lngLat.lng, e.lngLat.lat);
          if (n.metres < best) {
            best = n.metres;
            const [mlon, mlat] = m.place(n.triangle, n.w1, n.w2);
            pick = { x: 0, lon: mlon, lat: mlat, mesh: { id: m.id, triangle: n.triangle, w1: n.w1, w2: n.w2 } };
          }
        }
        if (best > MAX_SNAP_M) return; // still placing: no water near enough
        draft = pick;
        placing = false;
      });
      request({ type: "open", cycle, reach: main, particles: FLOW_PARTICLES });
      requestAnimationFrame(tick);
    } catch (e) {
      error = String(e);
    }
  });
</script>

<div class="app">
  <div class="map" bind:this={mapEl} class:placing></div>

  <header>
    <img class="logo" src="./sturgeon-logo.webp" alt="" width="48" height="48" />
    <div>
      <h1>{text.title}</h1>
      <p class="disclaimer">{text.disclaimer}</p>
    </div>
  </header>

  {#if error}
    <div class="panel error">{text.error} {error}</div>
  {:else if !ready}
    <div class="panel">{text.loading}</div>
  {:else}
    <div class="panel controls">
      <div class="modes">
        <button class:on={mode === "now"} onclick={() => setMode("now")}>{text.mode_now}</button>
        <button class:on={mode === "past"} onclick={() => setMode("past")}>{text.mode_past}</button>
        <button class:on={mode === "next"} disabled={!forecastAhead} title={forecastAhead ? "" : text.mode_next_soon} onclick={() => setMode("next")}>{text.mode_next}</button>
      </div>
      <div class="time">
        <input type="range" min={ready.start} max={ready.end} step="900" value={t} oninput={scrub} style={track(ready)} />
        <span class="when" class:faint={words === "rough_guide"}>{fmt(t)} · {text[words]}</span>
      </div>
      {#if mode !== "now"}
        <div class="play">
          <button onclick={() => (playing = !playing)}>{playing ? text.pause : text.play}</button>
          <label>{text.speed}
            <select bind:value={hoursPerSecond}>
              {#each [0.5, 1, 2, 6, 12] as h}<option value={h}>{h} {text.hours_per_second}</option>{/each}
            </select>
          </label>
        </div>
      {/if}
      <div class="release">
        {#if draft}
          <label>{text.release_mass} <input type="number" min="0" bind:value={mass} /></label>
          <label>{text.release_continuous} <input type="number" min="0" bind:value={continuousHours} /></label>
          <label>{text.release_half_life} <input type="number" min="0" bind:value={halfLifeHours} /></label>
          <label>{text.release_kd} <input type="number" min="0" bind:value={kdLPerKg} /></label>
          {#if mode === "now"}<span class="note">{forecastAhead ? text.release_plays_ahead : text.release_starts_back}</span>{/if}
          <button class="go" onclick={release}>{text.release_go}</button>
          <button onclick={() => (draft = null)}>{text.cancel}</button>
        {:else if placing}
          <span>{text.click_the_river}</span> <button onclick={() => (placing = false)}>{text.cancel}</button>
        {:else}
          <button onclick={() => (placing = true)}>{text.add_pollutant}</button>
          {#if balance[0] > 0}
            <button onclick={() => request({ type: "clear" })}>{text.clear}</button>
          {/if}
        {/if}
      </div>
      {#if balance[0] > 0}
        <div class="balance">
          {text.mass_in_water}: {balance[1].toFixed(1)} kg
          {#if balance[8] > 0} · {text.mass_in_canals}: {balance[8].toFixed(1)} kg{/if}
          {#if balance[9] > 0} · {text.mass_down_canals}: {balance[9].toFixed(1)} kg{/if}
          {#if balance[4] > 0} · {text.mass_pumped}: {balance[4].toFixed(1)} kg{/if}
          {#if balance[5] > 0} · {text.mass_diverted}: {balance[5].toFixed(1)} kg{/if}
          {#if balance[6] > 0} · {text.mass_to_sea}: {balance[6].toFixed(1)} kg{/if}
          {#if balance[7] > 0} · {text.mass_to_ocean}: {balance[7].toFixed(1)} kg{/if}
          {#if balance[10] > 0} · {text.mass_on_bed}: {balance[10].toFixed(1)} kg{/if}
          {#if balance[11] > 0} · {text.mass_lost_at_join}: {balance[11].toFixed(1)} kg{/if}
          {#if balance[2] > 0} · {text.mass_past_end}: {balance[2].toFixed(1)} kg{/if}
          · {text.mass_decayed}: {balance[3].toFixed(1)} kg
        </div>
        {#if fickianM !== null}
          <div class="balance">{text.fickian_note.replace("{km}", fickianM >= 10_000 ? (fickianM / 1000).toFixed(0) : (fickianM / 1000).toFixed(1))}</div>
        {/if}
      {/if}
      <div class="layer">
        <label>{text.colour_by}
          <select value={layer} onchange={(e) => setLayer((e.target as HTMLSelectElement).value as Layer)}>
            {#each LAYERS as l}<option value={l}>{key(`layer_${l}`)}</option>{/each}
          </select>
        </label>
        {#if legend(layer)}
          {@const lg = legend(layer)!}
          <div class="legend">
            <div class="bar" style={`background: ${lg.gradient}`}></div>
            <div class="ticks">{#each lg.ticks as [x, label]}<span style={`left: ${(x * 100).toFixed(1)}%`}>{label}</span>{/each}</div>
            <span class="unit">{lg.unit}{layer === "level" ? ` · ${text.legend_river_grey}` : ""}{layer === "temperature" ? ` · ${text.legend_river_temperature}` : ""}{layer === "salinity" ? ` · ${text.legend_rivers_fresh} · ${text.legend_salinity_bay}` : ""}{layer === "sediment" ? ` · ${text.legend_sediment}` : ""}</span>
          </div>
        {:else if layer === "source"}
          <div class="legend swatches">
            {#each sources as s}<span><i style={`background: ${s.colour || "#8a96a6"}`}></i>{s.label}</span>{/each}
            <span class="unit">{text.legend_source_note}</span>
          </div>
        {:else if layer === "plain"}
          <span class="unit">{text.legend_plain}</span>
        {/if}
      </div>
      <div class="layer">
        <span>{text.flow_as}</span>
        <label><input type="radio" name="flow" checked={!netFlow} onchange={() => setFlow(false)} /> {text.flow_moment}</label>
        <label><input type="radio" name="flow" checked={netFlow} onchange={() => setFlow(true)} /> {text.flow_net}</label>
        {#if netFlow}<span class="unit">{text.flow_net_note}</span>{/if}
      </div>
      {#if overlays}
        <div class="layer">
          <span>{text.overlays}</span>
          {#if x2}<label><input type="checkbox" checked={showX2} onchange={(e) => toggle("x2", (e.target as HTMLInputElement).checked)} /> {text.overlay_x2}</label>{/if}
          <label><input type="checkbox" checked={showStations} onchange={(e) => toggle("stations", (e.target as HTMLInputElement).checked)} /> {text.overlay_stations}</label>
          {#if overlays.landmarks?.length}<label><input type="checkbox" checked={showLandmarks} onchange={(e) => toggle("landmarks", (e.target as HTMLInputElement).checked)} /> {text.overlay_landmarks}</label>{/if}
          {#if overlays.structures?.length}<label><input type="checkbox" checked={showStructures} onchange={(e) => toggle("structures", (e.target as HTMLInputElement).checked)} /> {text.overlay_structures}</label>{/if}
          {#if showX2 && x2Now}
            <span class="unit">
              {#if x2Now.km !== null}{text.x2_at.replace("{km}", x2Now.km.toFixed(1))}{:else}{key(`x2_${x2Now.outside}`)}{/if}
              {#if x2?.check?.mean_difference_km != null} · {text.x2_check.replace("{mean}", Math.abs(x2.check.mean_difference_km).toFixed(1)).replace("{largest}", (x2.check.largest_difference_km ?? 0).toFixed(1)).replace("{days}", String(x2.check.days.filter((d) => d.km !== null && d.model_km != null).length))}{/if}
            </span>
          {/if}
          {#if showStations}<span class="unit">{text.stations_note}</span>{/if}
          {#if showStructures}
            <span class="legend swatches">
              {#each Object.entries(STRUCTURE_COLOUR) as [k, c]}<span><i style={`background: ${c}`}></i>{key(`structure_${k}`)}</span>{/each}
              <span class="unit">{text.structures_note}</span>
            </span>
          {/if}
        </div>
      {/if}
      <div class="updated">{text.updated}: {fmt(ready.now)}</div>
    </div>
  {/if}
</div>

<style>
  :global(html, body, #app) { margin: 0; height: 100%; font-family: system-ui, sans-serif; color: #e8eef6; background: #0d1726; }
  .app { position: relative; height: 100%; }
  .map { position: absolute; inset: 0; }
  .map.placing { cursor: crosshair; }
  header { position: absolute; top: 12px; left: 12px; background: #0d1726cc; padding: 8px 12px; border-radius: 8px; max-width: 28rem; display: flex; gap: 10px; align-items: center; }
  .logo { width: 48px; height: 48px; border-radius: 50%; background: #fff; flex: none; }
  h1 { font-size: 1.05rem; margin: 0; }
  .disclaimer { font-size: 0.75rem; opacity: 0.8; margin: 4px 0 0; }
  .panel { position: absolute; left: 12px; right: 12px; bottom: 12px; background: #0d1726e6; padding: 10px 12px; border-radius: 8px; display: grid; gap: 8px; font-size: 0.85rem; }
  .error { color: #ffb4a8; }
  .modes, .play, .release { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .time { display: flex; gap: 10px; align-items: center; }
  .time input { flex: 1; appearance: none; height: 6px; border-radius: 3px; }
  .when { min-width: 14rem; }
  .faint { opacity: 0.7; }
  button { background: #1c2d45; color: inherit; border: 1px solid #34507a; border-radius: 6px; padding: 4px 10px; cursor: pointer; }
  button.on, button.go { background: #2f6db5; border-color: #2f6db5; }
  button:disabled { opacity: 0.45; cursor: default; }
  input[type="number"] { width: 5rem; }
  .balance, .updated, .note { font-size: 0.75rem; opacity: 0.85; }
  .layer { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .legend { display: grid; gap: 2px; min-width: 14rem; flex: 1; max-width: 26rem; }
  .legend .bar { height: 8px; border-radius: 4px; }
  .legend .ticks { position: relative; height: 1em; font-size: 0.7rem; }
  .legend .ticks span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
  .unit { font-size: 0.7rem; opacity: 0.8; }
  .swatches { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 0.75rem; }
  :global(.station table) { border-collapse: collapse; margin-top: 4px; font-size: 0.8rem; }
  :global(.station td) { padding: 1px 6px 1px 0; }
  :global(.station .site), :global(.station .age), :global(.station .model), :global(.station p) { opacity: 0.7; font-size: 0.75rem; }
  :global(.station p) { margin: 4px 0 0; }
  :global(.station p.state) { opacity: 1; font-size: 0.8rem; }
  :global(.maplibregl-popup-content) { background: #0d1726; color: #e8eef6; }
  :global(.maplibregl-popup-tip) { border-top-color: #0d1726 !important; border-bottom-color: #0d1726 !important; }
  .swatches i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; vertical-align: -1px; }
</style>
