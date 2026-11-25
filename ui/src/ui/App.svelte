<script lang="ts">
  // The page: map, timeline and controls. Display only: every number shown
  // comes from the compute engine through the interface (specification 4.2).
  import { onMount } from "svelte";
  import type { Map } from "maplibre-gl";
  import { connect } from "../compute-interface/connect";
  import type { Frame, Ready, Words } from "../compute-interface/messages";
  import text from "../../../text/en.json";
  import { Reach, type ReachGeometry } from "./reach";
  import { addNetwork, createMap, drawFrame, drawNetworks } from "./map";

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
  let balance = $state<number[]>([0, 0, 0, 0]);
  let placing = $state(false);
  let draft = $state<{ x: number; lon: number; lat: number } | null>(null);
  let mass = $state(100);
  let continuousHours = $state(0);
  let halfLifeHours = $state<number | null>(null);

  let busy = false;
  let last = 0;

  const fmt = (s: number) =>
    new Date(s * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

  function onFrame(f: Frame) {
    busy = false;
    t = f.t;
    balance = Array.from(f.massBalance);
    words = f.words;
    if (map && reach) drawFrame(map, reach, f);
    if (map) drawNetworks(map, f);
  }

  function tick(ms: number) {
    const dt = last ? Math.min(0.1, (ms - last) / 1000) : 0;
    last = ms;
    if (ready && !busy) {
      if (mode === "now") {
        busy = true; // "Now" holds the moment and lets the water drift
        engine.send({ type: "drift", dt: dt * NOW_SPEEDUP });
      } else if (playing) {
        const to = Math.min(ready.end, t + dt * hoursPerSecond * 3600);
        if (to >= ready.end) playing = false;
        busy = true;
        engine.send({ type: "advance", to });
      }
    }
    requestAnimationFrame(tick);
  }

  function setMode(m: Mode) {
    mode = m;
    playing = m === "next";
    if (!ready) return;
    busy = true;
    const to = m === "past" ? ready.start : ready.now;
    t = to; // so the play loop starts from here
    engine.send({ type: "seek", t: to });
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
    engine.send({ type: "seek", t: to });
  }

  // Without a forecast there is no future to play into, so a release made in
  // "Now" starts this far back and plays forward to now.
  const NO_FORECAST_LOOKBACK = 3 * 24 * 3600;

  function release() {
    if (!draft || !ready) return;
    const back = mode === "now" && !forecastAhead;
    const start = back ? Math.max(ready.start, ready.now - NO_FORECAST_LOOKBACK) : mode === "now" ? ready.now : t;
    if (mode === "now") {
      t = start; // so the play loop starts from here, not from the old "now"
      engine.send({ type: "seek", t: start });
    }
    engine.send({
      type: "release",
      x: draft.x,
      start,
      duration: Math.max(0, continuousHours) * 3600,
      mass,
      halfLife: halfLifeHours ? halfLifeHours * 3600 : null,
    });
    draft = null;
    if (mode === "now") {
      mode = back ? "past" : "next";
      playing = true;
    }
  }

  onMount(async () => {
    document.title = text.title; // the page title comes from the text files too
    engine.onReply((r) => {
      if (r.type === "ready") {
        for (const id of r.networks) {
          fetch(`${cycle}/geometry/${id}.json`)
            .then((x) => x.json())
            .then((g) => addNetwork(map, id, g));
        }
        ready = r;
        t = r.now;
      } else if (r.type === "frame") onFrame(r);
      else error = r.message;
    });
    try {
      const geometry = (await fetch(`${cycle}/geometry/river.json`).then((r) => r.json())) as ReachGeometry;
      reach = new Reach(geometry);
      map = createMap(mapEl, reach, region);
      map.on("click", (e) => {
        if (!placing) return;
        const near = reach.nearest(e.lngLat.lng, e.lngLat.lat);
        const [lon, lat] = reach.place(near.x);
        draft = { x: near.x, lon, lat };
        placing = false;
      });
      engine.send({ type: "open", cycle, reach: "river", particles: FLOW_PARTICLES });
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
          {#if mode === "now"}<span class="note">{forecastAhead ? text.release_plays_ahead : text.release_starts_back}</span>{/if}
          <button class="go" onclick={release}>{text.release_go}</button>
          <button onclick={() => (draft = null)}>{text.cancel}</button>
        {:else if placing}
          <span>{text.click_the_river}</span> <button onclick={() => (placing = false)}>{text.cancel}</button>
        {:else}
          <button onclick={() => (placing = true)}>{text.add_pollutant}</button>
          {#if balance[0] > 0}
            <button onclick={() => engine.send({ type: "clear" })}>{text.clear}</button>
          {/if}
        {/if}
      </div>
      {#if balance[0] > 0}
        <div class="balance">
          {text.mass_in_water}: {balance[1].toFixed(1)} kg · {text.mass_past_end}: {balance[2].toFixed(1)} kg ·
          {text.mass_decayed}: {balance[3].toFixed(1)} kg
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
</style>
