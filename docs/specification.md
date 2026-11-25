# Fischer Delta Model Returns: User and Engineering Specification

Version 1.0 · 24 November 2026

## 1. Summary

Fischer Delta Model Returns is a free web app that shows how water is moving between Shasta Dam and the Golden Gate, now, over the last 14 days and over the next 14. It shows the water's temperature and salinity, where a pollutant or a new outfall released anywhere in it will travel, and what a levee breach or a change in dam release would do. Everything plays as a movie of moving particles on a zoomable map.

It will live under Utilities on tripofisch.com, run entirely on hardware we own, and carry a non-commercial license. The code lives in the FischerDeltaModelReturns repository.

**Key decisions**

| Topic | Decision |
| --- | --- |
| Extent | Shasta Dam to the Golden Gate |
| Conditions | One timeline: the last 14 days, now, and a 14-day forecast. The past window rolls forward and is always kept up to date; nothing older is replayed |
| Delta engine | DWR's DSM2, run on our server |
| River above Sacramento | Our own routing model using Hugo Fischer's dispersion method |
| Bay below Chipps Island | NOAA's Bay current forecast, fetched as data; our own tracking on top. The Delta hands off to the Bay at Chipps Island, with Martinez as the fallback if Phase 0 rules Chipps out |
| Release times | Now, any time in the forecast, or any time in the last 14 days |
| Data and compute | Fetch public data freely; all modelling and display on our hardware |
| Server | The `webserver` Proxmox host only (Intel Core Ultra 9 185H, 62 GB, 8 TB NVMe), and no other machine, ever. Every other machine runs Electric Eel and is never touched by this app. The model runner is its own capped container on that host, separate from the web edge |
| Movie length | 14 days back and 14 days forward. The colour fades as certainty fades into the forecast |
| Engine and regions | One engine for any geography: a shared math library, and model kinds picked reach by reach in each region's package. Launch ships one region; the Mississippi, the Thames, the Nile and others are later candidates |
| Display | Moving particles show the water's direction and speed everywhere, always; particles carrying a pollutant take its colour |
| Map layers | Toggles choose what colours the water (plain, contaminant, speed, temperature, salinity, water level, water source), whether flow is shown moment by moment or net over the tides, and which overlays are drawn, including the X2 line and measured station readings |
| Temperature and salinity | Computed on the server for every reach and shown as layers in version 1 |
| Releases | All at once, continuous for a set time, or an ongoing outfall such as a sewage plant discharge; optional steady breakdown by half-life |
| What-if scenarios | Changed dam releases and levee breaches, from a menu computed in advance on the server |
| UI and compute | The UI only displays; the compute only computes. They talk through one interface and could run on separate machines. In version 1 the compute engine runs on the visitor's device |
| Plug-ins | Model kinds and math components are plug-ins behind versioned interfaces; the format and parcels carry a depth axis from the start, so 3D mixing models can be added later |
| Release locations | Anywhere in the modelled network, including the San Joaquin side of the Delta up to Vernalis |
| Historic layers | After launch |
| Original FORTRAN | Not used; ownership not pursued |
| License | PolyForm Noncommercial 1.0.0 for code, CC BY-NC 4.0 for content |
| Commercial use | None |

**Not yet verified.** Each item below is a Phase 0 task in section 10.

- DSM2 building and running on Debian. DWR supports Linux builds, but one library must be requested from them.
- The exact live feed for every DSM2 boundary input.
- NOAA's Bay forecast: horizon, file sizes, how far back its archive goes, and whether its mesh resolves Suisun Bay up to Chipps Island well enough for the handoff there (section 7.4).
- A public copy of DSM2's map geometry. DWR's documentation points to internal network drives.
- Enough USGS field measurements and conductance records on the river to calibrate water velocity and dispersion.
- How long one DSM2 run takes, which sets how many what-if scenarios can be computed each cycle.
- A public source for levee locations and island floor elevations in the Delta.

This is version 1.0, the agreed specification for the build. Everything in it is decided unless marked *to confirm*; those items are checked in Phase 0. The decisions log in section 10 records what was decided and when. The build plan is in [build-plan.md](build-plan.md).

## 2. Heritage

Hugo B. Fischer's two main methods are the working core of the app, one in each of the reaches we model ourselves.

| His work | Where it runs in the app |
| --- | --- |
| Longitudinal dispersion in natural streams ([Caltech thesis, 1966](https://thesis.library.caltech.edu/9180)) | The river from Keswick to Sacramento: how a slug of pollutant stretches as it travels |
| Lagrangian parcel tracking (DELSAL, the salt half of the Fischer Delta Model) | The tracer engine everywhere: the pollutant is carried as parcels of water |
| The Fischer Delta Model | The ancestor of DWR's DSM2, which supplies the Delta flows |

**Visible heritage in the product**

- A History section telling the story in words and pictures: the thesis, the work at sewage treatment plant outfalls where it started, the 1970s and 1980s Delta studies, the field trips, the punch cards, the Fischer Delta Model, and its line of descent to DSM2.
- After launch: a "1982 view" layer showing his original channel grid, if the published reports yield the geometry.
- After launch: a historical layer of the salinity and velocity measurements collected on the Delta field trips, with dates.
- After launch: scenario presets that reproduce questions he was paid to answer, such as a release at a named outfall.
- **The Eureka story**, in the History section: a rhodamine dye study at the Eureka sewage outfall on Humboldt Bay. The dye built up in the oysters, the filter feeders, and turned them bright red. It is the real-world version of the bioaccumulation feature planned for after launch (section 3.10). A 1982 report whose title ends "Humboldt Bay", credited to Hugo B. Fischer, Inc. with Edmund H. Smith, is listed in the Smith Collection at UC Davis's Cadet Hand Library, the Bodega Marine Laboratory's library. A copy is to be requested, to confirm it is this study and for pictures and details.

The original FORTRAN is not used and its ownership is not pursued; the punch cards appear only as photographs. Source material: the family papers and photographs, and the [Hugo B. Fischer papers at UC Riverside](https://public.ucrlib.aspace.cdlib.org/repositories/5/resources/816), which hold the 1982 DELFLO and DELSAL reports, and the Humboldt Bay report in the [Cadet Hand Library's Smith Collection](https://marinescience.ucdavis.edu/sites/g/files/dgvnsk9221/files/inline-files/CHL_Smith_Catalog.pdf).

## 3. User specification

A visitor watches the water move: as it is now, over the last two weeks, or over the next two. They choose what the map shows, such as temperature or salinity. They can pick a point on the water, describe what was released or what a new outfall would discharge, and watch where it goes, or ask what a levee breach would do.

### 3.1 Who it is for

Curious members of the public, students, boaters and anglers, and people who know the Delta. It is an educational tool. It is not for emergency response, permitting or legal use, and every screen says so.

### 3.2 The map

- Opens on the whole system, Shasta Dam to the Golden Gate, with the modelled waterways highlighted.
- Zooms smoothly from the whole system down to a single slough.
- Shows place names, towns, bridges, dams, pumping plants and gauging stations.
- Shows current conditions before any pollutant is added: particles drifting with the water in every reach, showing its direction and speed, and the state of the tide. In the Delta and the Bay the particles visibly turn around with the tide.
- States when the conditions were last updated.

### 3.3 Adding a pollutant or an outfall

The user presses "Add pollutant" and clicks or taps the water. The point snaps to the nearest modelled channel. A short form then asks:

| Question | Choices | Default |
| --- | --- | --- |
| How is it released? | All at once; continuously for a set duration; or as an ongoing outfall | All at once |
| How much? | All at once: a mass, or a volume with a concentration. Continuous or outfall: a flow rate with a concentration, such as millions of gallons a day at milligrams per litre | None; required |
| When? | Now, a chosen time in the forecast, or a chosen time in the last 14 days | Now |
| Does it break down? | No, or a half-life | No |

Version 1 treats the pollutant as dissolved: it moves with the water and does not float or sink. It can break down at a steady rate, given as a half-life, which covers, for example, the die-off of bacteria from sewage. Up to five releases and outfalls can be on the map at once. The contaminant layer shows them combined, or one at a time if the user picks one.

**Outfalls.** An outfall is a new continuous discharge, such as a new or enlarged sewage treatment plant outfall. It runs from its start time to the end of the timeline. Started at the beginning of the past window, it shows the steady plume the outfall settles into, and how each tide works it back and forth. The outfall's own flow is not added to the channel's flow; where it is more than a few percent of the channel's flow, the app says so.

A release in the past answers "if something went in three days ago, where is it now, and where is it going?" It plays forward from the past release time through now and on into the forecast.

### 3.4 The timeline and the movie

There is one timeline, from 14 days ago through now to 14 days ahead, and three ways to use it.

| Mode | What it shows | Time |
| --- | --- | --- |
| **Now** (the default on opening) | The water as it is moving at this moment | Stands still at now. The particles keep moving over the current flow, sped up so the motion is visible |
| **Last 14 days** | How the water has actually moved: tides, release changes, storms, pumping | Plays the recent past. Built from measured conditions, so it is shown at full certainty |
| **Next 14 days** | The forecast | Plays forward from now, fading as certainty fades |

- The past window rolls: each day the oldest day drops off and the newest is added, so it is always the last 14 days.
- Particles show the flow in every mode, with or without a pollutant (section 8.4).
- The movie can run straight through, from 14 days ago to 14 days ahead. A "Now" button jumps back to the present at any time.
- With a pollutant, the movie plays forward from the release time.
- Controls: play, pause, speed, and a time slider with the date, the tide state and a "now" mark.
- The plume is drawn as the particles carrying it: the same moving particles that show the flow, coloured by concentration, with a legend. Zoomed out to the whole system, where particles are too small to see, the channels and Bay are coloured instead (section 8.4).
- The leading edge and the peak are marked.
- The forecast runs 14 days. The further ahead, the less certain, and the display shows that: as certainty fades, so does the plume's colour, until a plume far into the forecast is a pale wash over the map. The time slider is shaded the same way, so the user can see where each reach passes from solid forecast into assumption. Section 8 gives the limits for each reach and how certainty is worked out.
- Read-off answers carry the same certainty, in words, in three tiers: "measured" in the past window, "forecast" at full certainty, and "rough guide" once certainty falls below one half.

### 3.5 What the map shows: layers and toggles

The particles always move with the water. The user chooses what colours them and what else is drawn.

**Colour the water by** (one at a time)

| Choice | What it shows | Computed by |
| --- | --- | --- |
| Plain water (the default) | Neutral particles: direction and speed only | The flow field |
| Contaminant | Concentration of the user's releases and outfalls. Chosen automatically when a release is added | The compute engine (section 4.2) |
| Speed | How fast the water is moving | The flow field |
| Temperature | Water temperature | The server (section 7.5) |
| Salinity | Salt content, from fresh river water to the ocean | The server (section 7.5) |
| Water level | Height of the water surface against a fixed datum: tides, flood rises, a filling island | The flow field |
| Water source | Where the water came from: the Sacramento, the San Joaquin, the eastside rivers, the ocean, shown as a blend | The server (section 7.5) |

When the water is coloured by something other than contaminant, any active plume is drawn as an outline, so it is not lost.

**Show the flow as** (one at a time)

| Choice | What it shows |
| --- | --- |
| Moment by moment (the default) | The flow at each instant. Tidal water sloshes back and forth |
| Net flow | The flow averaged over the tides: where the water is actually going over a day. It shows, for example, Old and Middle rivers running backwards toward the pumps |

**Overlays** (any combination)

- Gauges and stations, with their latest readings. These include measured values the model does not compute, such as dissolved oxygen and turbidity.
- Dams, pumping plants, gates and barriers, with their current state and pumping rates.
- Levees and breach sites, used by the what-if tool (section 3.7).
- Landmarks used for arrival times, and place names.
- The X2 line: the estimated point where the water near the bed reaches about 2 parts per thousand of salt. It is a standard Delta measure of how far the sea reaches inland.
- Map style: light or dark. Aerial imagery comes later.
- After launch: the historic layers in section 2.

The legend follows the choice of colour. Everything in the forecast fades as certainty fades, whatever it is coloured by.

### 3.6 Answers the user can read off

- When the pollutant first arrives at a chosen place, when it peaks, and at what concentration.
- A graph of concentration over time at any point the user clicks.
- Arrival times at a fixed list of landmarks, such as the Delta pumping plants, Rio Vista, Martinez and the Golden Gate.

### 3.7 What-if scenarios: dam releases and levee breaches

Some questions change the water itself, not only what is in it. Each what-if changes the flow field, so it is computed on the server (section 7.6). Every screen showing one says plainly that it is a what-if, not the forecast.

**Dam releases.** The user sees the current Keswick release and can try a different one. The tool shows the expected change in water level and when it reaches the downstream gauges. It also shows that the change in water level arrives before the water released with it, since a wave travels faster than the water carrying it (section 7.1).

**Levee breaches.** Most Delta islands are farmland below sea level, held dry by levees. The user picks a breach site and sees what follows:

- water pouring through the breach and the island filling;
- once full, the island filling and draining with every tide, like a new bay;
- tidal flows across the Delta pulled toward it;
- salinity pushed further inland;
- the changes reaching the pumps.

This is what happened when Jones Tract flooded in 2004. A breach on the river's levees above Sacramento instead spills flow onto the floodplain. A pollutant or outfall can be added on top of a breach, to see how the breach changes where it goes.

**How they are offered.** Each cycle, the server computes a menu of what-ifs in advance: a few release levels, and a set of breach sites, each with a standard breach opening now. Picking one loads its files like any other, so nothing the user enters leaves their device. Custom what-ifs, with any release, any levee, any breach width and any time, would need a model run on request. That would send the scenario's settings to our server, so it comes after launch, with section 3.8 changed to match.

### 3.8 Privacy and operation

- No account and no tracking. What the user enters never leaves their device. What-ifs come from a menu computed in advance, so they keep to this rule.
- Works on a phone, tablet or desktop browser.
- If the live data is stale, the app says how old it is and still runs.

### 3.9 Out of scope for version 1

- The 1982 grid and all historic layers.
- Oil and other floating spills; sediment-bound or sinking material.
- Chemical reactions other than a steady half-life.
- The heat or salt a release itself carries: a release does not change the water's temperature or salinity.
- Wind effects on the Bay beyond NOAA's own forecast.
- Custom what-if runs on request.
- The San Joaquin River above Vernalis.
- Any replay older than the rolling 14-day window.
- Bioaccumulation, planned for after launch (section 3.10).

### 3.10 Planned after launch: bioaccumulation

Filter feeders such as oysters, mussels and clams concentrate some pollutants far above the level in the water around them, and stay contaminated long after the water is clean. The red oysters of the Eureka story (section 2) are that effect, made visible.

- The user places a virtual shellfish bed: clicks the water, picks a species, and picks a substance from a short menu.
- The app shows the tissue concentration over time beside the water concentration at that spot. It rises while the plume passes and falls slowly afterwards.
- Substances come from a menu of real ones with published uptake and loss rates for each species, such as common metals and some organic chemicals in mussels, oysters and clams. A made-up pollutant has no such rates, so it cannot be used.
- It runs in the compute engine on the concentration history the tracer already produces at that spot. It costs almost nothing, and nothing the user enters leaves their device.
- Like the rest of the app, it is illustrative, not a seafood-safety tool, and every screen showing it says so.

**Later steps.** Many pollutants are taken in with food rather than straight from the water. One famous case in this region is selenium in Suisun Bay clams, passed on to the sturgeon and diving ducks that eat them. Modelling that needs the dissolved pollutant to move onto particles first, which version 1 does not do (section 3.9). Food-web models that carry a chemical from plankton up through shellfish, fish and birds have been applied to San Francisco Bay for PCBs and mercury, and could follow after that.

**Planned for now, built later.** Like other geographies, bioaccumulation is coming but not first. Version 1 leaves room for it, so it needs no engine changes later:

- The tracer engine can produce the concentration history at any point, which the read-off graph in section 3.6 already needs.
- Each parcel carries the identity of its substance and a phase field. In version 1 the phase is always "dissolved"; the field is there for particle-bound pollutants later.
- A receptor, such as a shellfish bed, is a plug-in kind (section 4.1). It reads concentration histories and writes its own results, without touching the tracer engine.
- The substance and species rates are a data file shared by every region, with a source recorded for each rate.

## 4. Engineering: architecture

The model runner computes where the water is going and what it is like. The compute engine works out where a pollutant goes in that water. The UI only shows the results.

The flow field, the temperature and salinity, and the menu of what-ifs are the same for every visitor, so the model runner computes them once per cycle and publishes them as static files. A pollutant release is different for every visitor, so the compute engine works it out from those files. In version 1 the compute engine runs on the visitor's own device, which keeps the app interactive and private. Section 4.2 explains how the UI and the compute engine are kept apart.

```mermaid
flowchart TB
  subgraph SRC["Public data sources, fetched on a schedule"]
    A1["USGS river gauges"]
    A2["DWR releases, gates"]
    A3["River forecasts"]
    A4["NOAA tides, currents"]
  end
  subgraph RUN["Model runner (our server): all modelling"]
    B1["Fetch, store and build model inputs"]
    B2["River model: ours, Fischer dispersion"]
    B3["Delta: DSM2 flow, salinity, temperature"]
    B4["Bay: NOAA currents, temperature, salinity"]
    B6["What-if menu: reruns with one change"]
    B5["Publish flow-field files and status"]
    B1 --> B2 --> B5
    B1 --> B3 --> B5
    B1 --> B4 --> B5
    B2 --> B6
    B3 --> B6
    B6 --> B5
  end
  subgraph WEB["Web guest: serves static files only"]
    C1["Pages, map tiles, flow-field files"]
  end
  subgraph CMP["Compute engine: browser background thread in v1, or its own box"]
    D2["Tracer: Fischer parcel method"]
    D3["Answers: arrival, peak, histories"]
    D2 --> D3
  end
  subgraph UI["UI: display only"]
    D1["Map, particles, layers, controls"]
  end
  SRC --> B1
  B5 --> C1
  C1 -- "flow-field files" --> D2
  C1 -- "pages, map tiles" --> D1
  D1 -. "requests" .-> D2
  D3 -- "snapshots, answers" --> D1
```

Data flows one way, from the sources down to the UI. The only thing that crosses from the server to a visitor is a set of static files. The UI sends the compute engine requests, and the compute engine sends back results.

**Server side, on a schedule**

1. Fetch public measurements and forecasts.
2. Build model inputs for the forecast window, and refresh the recent past from the latest measurements.
3. Run the river model and DSM2, for flow, temperature and salinity; read in the NOAA Bay forecast.
4. Run the what-if menu: each scenario is a rerun with one thing changed.
5. Convert all three reaches, and each what-if, to one compact published format.
6. Publish the files and a small status file to the web server, and roll the 14-day past window forward.

**Compute engine and UI, on demand**

1. The UI loads the map. The compute engine loads the flow-field files for the time being shown, starting with now.
2. The UI passes on the user's releases, outfalls and choices.
3. The compute engine moves the parcels and flow particles and works out the answers.
4. The UI draws what the compute engine sends: particles, colours, graphs and answers.

In version 1, nothing a visitor does requires a call back to the server beyond fetching static files, and no third-party server is contacted at all.

### 4.1 One engine, many geographies

Launch has one region, Shasta Dam to the Golden Gate. The engine is built from the start for many: later the Mississippi, the Thames, the Nile and other rivers, estuaries and bays. Two things make that possible: a shared library of math components, and a region package that picks which kind of model runs where.

Sections 5 to 9 describe the launch region. This section says which parts of it are general.

#### Three layers

| Layer | What it is | Changes per region? |
| --- | --- | --- |
| Math library | The equations, each written and tested once | Never |
| Model kinds | Ready-made models built from the library, one for each kind of water | Never; a region picks among them |
| Region package | The network, a model kind for each reach, data feeds, settings and written content | Yes. This is all a new region writes |

#### The math library

The library is pure calculation, with nothing about any place in it. Each component is tested against textbook cases and, where possible, against the launch region.

| Component | Used by |
| --- | --- |
| Longitudinal dispersion, from Fischer's estimate | River routing; 1D tidal network |
| Flood-wave routing, with travel time and attenuation that depend on flow | River routing |
| Hydraulic geometry: fitting area, width, depth and velocity against flow | River calibration |
| Lag and change of shape of a signal between two gauges | Calibrating wave speed, water speed and dispersion |
| Parcel movement: carried by the flow plus a random step for dispersion, in 1D channels and on 2D meshes | Tracer engine, every reach |
| Junction splitting by share of outflow; mixing in pools; capture by sinks | Tracer engine |
| Handing parcels across a line between two reaches | Tracer engine, every join |
| Tidal fit with a river-outflow drift term | Extending a 2D forecast past its end; tide predictions where no official ones exist |
| Transport of heat and salt along 1D channels, with heat exchange at the surface driven by weather | River temperature; any 1D reach without its own water-quality model |
| Weir flow through an opening between a channel and a pool | Levee breaches; spills into bypasses |
| Tidal averaging of velocity | The net-flow display |
| First-order decay by half-life | Tracer engine |
| Uptake and loss in a living receptor: a one-compartment model driven by the water's concentration history (after launch) | Bioaccumulation (section 3.10) |
| Split between dissolved and particle-bound pollutant (after launch) | Uptake with food; sediment-bound pollutants |
| Certainty: horizons and fade per reach | Every reach; the display |
| Quantising and packing fields into the published format | Publisher |

#### Model kinds

Each reach of a region is one of these kinds. The kind is chosen by the water's physics and by what data exists there.

| Kind | Water it suits | Flow comes from | At launch |
| --- | --- | --- | --- |
| River routing | One-way rivers with flow gauges | Our model, driven by gauges and river forecasts | Shasta Dam to Sacramento |
| 1D tidal network | Tidal rivers and branching channels | A one-dimensional hydrodynamic model we run | The Delta, with DSM2 |
| 2D field | Wide estuaries, bays and lagoons | A fetched forecast, or a two-dimensional model we run | San Francisco Bay, with NOAA's forecast |
| Well-mixed pool | Lakes, reservoirs, forebays, flooded islands | A water balance of inflows and outflows | Keswick Reservoir; DSM2's reservoirs |
| Ocean boundary | Where water leaves the region | Nothing. Parcels past it are counted out | Past the Golden Gate |
| 3D field (future plug-in) | Deep or stratified water, where fresh water rides over salt and the two flow in different directions | A fetched three-dimensional forecast, or a 3D mixing model we run | Not at launch. NOAA's Bay forecast is already three-dimensional; we average it over depth for now |

Every kind writes the same published format (section 8.3), so the tracer engine and the display never know which kind produced a reach. Every kind can also carry temperature and salinity. A flooded island is a pool joined to a channel by a breach, so what-ifs need no new kind. Within a kind the flow source can be swapped. The launch region's 1D tidal network is DSM2, but nothing outside its adapter assumes that.

#### Plug-ins

Model kinds and math components are plug-ins behind fixed interfaces, so new ones, such as 3D mixing models, can be added later without changing the engine.

- **A model-kind plug-in** takes the region's settings, the local data store and the shared clock. It writes its reaches in the published format. It declares what it provides: its dimensions (1D, 2D or 3D), the fields it carries (velocity, volume, level, temperature, salinity, mixing), its forecast horizon and its certainty settings.
- **A math-component plug-in** replaces or adds one calculation behind the same inputs and outputs. Examples are another dispersion estimate, a turbulence or vertical-mixing scheme, or a heat-exchange formula.
- **Conformance tests.** Every plug-in must pass the engine's tests before a region can use it: mass balance, the published format, and textbook cases for its kind.
- **Versioned interfaces.** The plug-in interfaces and the published format carry version numbers, so an old plug-in keeps working when the engine moves on, or fails clearly.
- **Outside programs.** A plug-in can wrap an outside program, as the DSM2 adapter does. A GPL model is run as a separate, unmodified program, never linked into our code (section 9.4).

**Ready for 3D from the start.** So that a 3D plug-in does not mean a rewrite, version 1 builds in the depth axis even though nothing uses it yet:

- The published format allows a depth dimension, in layers. 1D and 2D reaches have one layer.
- A parcel has a depth coordinate. In 1D and 2D reaches it stays undefined and is ignored.
- The math library separates horizontal movement from vertical mixing, so a vertical-mixing component can be added on its own.
- The display can show a depth-averaged view, which is all version 1 needs, and later the surface, the bed or a chosen depth, or a slice through the water.

Moving parcels in 3D is heavier work, and may be too much for a phone. A 3D plug-in may therefore have its tracking done by a compute engine on a server (section 4.2), or publish depth-averaged fields for phones and full 3D for desktops. That choice is made when the first 3D plug-in is built.

**The gap for other regions: a general 1D tidal model.** DSM2 is set up, calibrated and maintained for the Delta. The tidal Thames, the Mississippi below Baton Rouge or another delta would each need a 1D tidal model of its own. There are two routes, to be decided before the second region:

- Set up DSM2, or another open 1D model, on the new network.
- Write our own 1D tidal solver into the math library from the published equations. Fischer's DELFLO was exactly this kind of model, so this would bring back his flow half as well as his transport half. It would be checked against DSM2 in the Delta, where DSM2 is the reference. It must be written from published equations, not from DSM2's source, to stay clear of DSM2's GPL license (section 9.4).

Either way, a new region needs its own channel survey and calibration data, and that is usually the harder part.

#### Picking model kinds for a region

A region package lists its reaches. For each one it names a model kind, a flow source, the data adapters that feed it and its handoffs to neighbouring reaches. It also lists its what-if sites: dams whose releases can be changed, and levee breach sites with the pool behind each. A new region is set up by writing that list and gathering its data, not by changing the engine.

The rows below are sketches to show that the same kinds recur. They are not researched; each region would start with its own Phase 0.

| Region | Reaches and kinds |
| --- | --- |
| San Francisco Bay-Delta (launch) | River routing from Shasta Dam to Sacramento; 1D tidal network for the Delta, with DSM2; 2D field for the Bay, from NOAA; pools for Keswick and DSM2's reservoirs |
| Lower Mississippi | River routing above the reach of the tide; 1D tidal network to the passes; 2D field at the mouth, if a NOAA Gulf forecast resolves it |
| Thames | River routing down to Teddington Lock, the tidal limit; 1D tidal network from Teddington through London to the estuary; 2D field for the outer estuary, if a European shelf forecast is fine enough |
| Nile | River routing from the Aswan High Dam through the barrage-controlled delta branches to the Mediterranean, driven by global river forecasts where gauges are sparse |
| Humboldt Bay | 2D field for the bay: a NOAA forecast if one covers it (to check), otherwise a 2D model we run as a plug-in. The home of the Eureka story, which could become a preset there |

#### Data adapters, in tiers

| Tier | Examples | Serves |
| --- | --- | --- |
| Regional | CDEC and DWR operations data, California | One region |
| National | USGS gauges, NOAA tides and forecast systems, National Weather Service River Forecast Centers and weather forecasts, in the US; the Environment Agency's real-time river and tide data, in England | Every region in that country |
| Global | Copernicus global river-flow forecasts (GloFAS); Copernicus Marine ocean and shelf forecasts; global tide models; global weather forecasts | Anywhere, as a fallback where national data is thin |

- A region uses the most local tier available for each input. Its certainty settings reflect which tier that is: a reach driven by global forecasts starts less certain than one driven by local gauges.
- Each adapter records its source's license and the attribution it requires, and the region's credits show them.
- A source whose terms forbid our use is not adapted. The global sources' terms are to be checked one by one.
- The fetch-only rule (section 1) holds everywhere: we fetch data, and all modelling runs on our hardware.

#### Place-neutral basics

- **Time:** UTC inside the engine. Each region sets the time zone for display.
- **Units:** SI inside. Each region sets display units: cubic feet per second and miles in the US, cubic metres per second and kilometres elsewhere. The user can switch.
- **Coordinates:** longitude and latitude in published files. Each region names a local projection for its own geometry work.
- **Tides:** tide datums and the names of tide states differ by country; each region supplies its own.
- **Language:** interface text is kept apart from code, so a region can be translated later (section 4.2).

**Keeping it honest.** Nothing place-specific may appear in the engine's code: no station numbers, channel numbers, place names or coordinates. To test this, a second, small region is built alongside the first: a single gauged river outside California, with no tide, using only the river model and national adapters. It runs in the test suite from Phase 1. If the engine needs changing to run it, something regional leaked into the engine. This is why it matters: with only one region, Delta assumptions creep into the engine unnoticed, and the Mississippi or the Thames would later mean a rewrite. A second, deliberately plain region catches that on the day it happens, while it is cheap to fix. The river is chosen at the start of Phase 1.

**Serving several regions** (*after version 1*). Each region publishes its own flow-field set. The UI loads a short list of regions and opens the one chosen. A region can run on its own model runner, so adding one does not slow another.

### 4.2 UI and compute kept apart

The UI only displays, and the compute only computes. They talk through one defined interface, so they can run on different machines. In version 1 they share the visitor's device, but nothing in either assumes that.

| Part | Does | Never does |
| --- | --- | --- |
| Model runner, on the server | Flow, temperature, salinity and what-ifs; publishes files | Draw anything |
| Compute engine | Moves parcels and flow particles; works out concentrations, arrival times, peaks, histories, certainty and, later, bioaccumulation | Draw anything or know about the screen |
| UI | Map, particles, colours, legend, controls, words on screen | Any modelling or calculation of results |

- **The interface.** The UI sends requests: load this stretch of time, add this release, show this layer, give the history at this point. The compute engine replies with snapshots of particle positions and values at each time step, and with answers. The UI only smooths between snapshots so the motion looks continuous; that is drawing, not modelling.
- **Where the compute engine runs.** In version 1 it runs in a background thread of the visitor's browser: instant, free to scale and private. The same engine can instead run on its own machine and serve a UI over the network. A visitor's releases would then travel to that machine, and section 3.8 would change to say so.
- **One compute codebase.** The compute engine is written once and runs in both places, so moving it is a deployment change, not a rewrite. The compute engine and the math library are written in Rust, compiled to WebAssembly for the browser and natively for a server. The model runner is written in Python and calls the same Rust math through bindings, so the math exists once.
- **Words on screen.** All text the UI shows is kept in separate files, not in code. Version 1 is in English only; another language is a new set of files.
- **Testing apart.** The compute engine is tested without any UI. The UI is tested against a recorded stream of compute results.

### 4.3 Code structure and standards

The code is built from small, separate modules, with every seam between them explicit. Compute and UI are never mixed.

**The seams.** Each of these is its own module, or set of modules, with its own interface:

| Module | Depends only on |
| --- | --- |
| Math library | Nothing else in the project |
| Published format: reading and writing | Nothing else in the project |
| Model-kind plug-ins | Math library, published format, plug-in interface |
| Data adapters | Local data store interface |
| Model runner: scheduling and publishing | Plug-in and adapter interfaces |
| Region packages | Data only; no code beyond what the plug-ins already provide |
| Compute engine | Math library, published format |
| UI | The compute engine's interface, and text and style files |
| Text and style files | Nothing; data only |

**Rules**

- Modules talk only through their published interfaces: typed messages, defined file formats or declared function signatures. No module reaches into another's internals.
- Dependencies run one way, as in the table. Nothing depends on the UI, and the math library depends on nothing.
- No state is shared across a seam except through its interface.
- The UI holds no modelling or calculation of results; the compute engine and model runner hold no drawing code. Section 4.2 sets this out.
- Nothing place-specific in the engine (section 4.1); nothing language-specific in code (section 4.2).
- Every module builds and is tested on its own, against its interface, with stand-ins for its neighbours.
- Interfaces carry version numbers. A change that breaks one is a new version, not a quiet edit.
- The build checks these rules automatically: a dependency check fails the build if any module imports something its row in the table does not allow.

## 5. Engineering: the map

The map is OpenStreetMap data served from our own server as a single file, drawn in the browser by an open-source renderer, with our own waterway geometry on top.

### 5.1 Base map

| Piece | Choice | Why |
| --- | --- | --- |
| Renderer | [MapLibre GL JS](https://maplibre.org/) | Open source, smooth zoom, runs entirely in the browser |
| Map data | [Protomaps](https://protomaps.com/) basemap built from OpenStreetMap | Free, and one file with no tile server to run |
| Delivery | A regional extract in PMTiles format on our web server | The browser reads only the byte ranges it needs |
| Fonts and icons | Copied to our server | The default examples load these from third parties, which we do not allow |

The extract covers Northern California from Shasta Lake to the coast. The whole-planet file is about 100 GB; a regional cut should be a small fraction of that, to be measured.

An aerial imagery option is deferred. Free imagery exists but is large to self-host.

### 5.2 Waterway geometry

The modelled waterways are drawn as our own layer, because the plume must follow the model's channels exactly.

| Reach | Geometry source | Status |
| --- | --- | --- |
| Shasta to Sacramento | USGS national hydrography river centreline, measured in river miles | Source to confirm |
| Delta | DSM2 channel centrelines and nodes. [DWR's documentation](https://github.com/CADWRDeltaModeling/dsm2) describes shapefiles of these, but on internal drives | Ask DWR, or rebuild from the published channel inputs |
| Bay | The NOAA Bay model's mesh, about 54,000 nodes, for placing currents; OpenStreetMap shoreline for drawing | To confirm |

Each Delta channel on the map carries its DSM2 channel number, so a click maps directly to a model location.

### 5.3 Reference layers

Gauging stations with live readings, dams, pumping plants, gates and barriers with their current state, levees and breach sites, and named landmarks used for arrival times. Section 3.5 lists which the user can switch on and off.

## 6. Engineering: live data feeds

Every input comes from a free public source; three of the twelve have been checked and nine still need checking.

| Input | Source | Used by | Status |
| --- | --- | --- | --- |
| River flow and stage at gauges, Keswick to Freeport | [USGS Water Data APIs](https://api.waterdata.usgs.gov/docs/ogcapi/migration/) | River model, calibration | Checked. Build on the new API only: the old WaterServices shuts down in February 2027 |
| Field measurements at river gauges: area, width, mean velocity | USGS Water Data APIs | River velocity and channel shape (section 9.1) | To confirm the new API serves them, and how often each gauge is measured |
| Specific conductance and water temperature at river gauges | USGS Water Data APIs | River tracer calibration (section 9.1) | To confirm which gauges record them continuously |
| Keswick and Shasta releases, Delta exports, gate positions | California Data Exchange Center (DWR) | River model, DSM2 | To confirm station by station |
| River flow forecasts | [River Forecast Center hourly CSV files](https://www.cnrfc.noaa.gov/deterministicHourlyProductCSV.php), by forecast group | River model, DSM2 inflows | Checked. Upper and Lower Sacramento groups are published; about 5 days ahead |
| Tide at Martinez, observed and predicted | NOAA tide stations | DSM2 seaward boundary | To confirm station and format |
| Bay currents, nowcast and forecast | [NOAA San Francisco Bay Operational Forecast System](https://tidesandcurrents.noaa.gov/ofs/sfbofs/sfbofs_info.html), NetCDF files | Bay reach | Checked. Four runs a day; grid covers Suisun Bay to the ocean. Horizon believed to be about 48 hours, to confirm |
| Bay temperature and salinity | The same NOAA files as the currents | Bay layers | To confirm the variables are included |
| Temperature, conductance, dissolved oxygen and turbidity at river, Delta and Bay stations | CDEC and USGS | Checking temperature and salinity; station overlay | To confirm station by station |
| Keswick release temperature | CDEC or Reclamation | River temperature model | To confirm |
| Weather forecast: air temperature, sunshine, humidity, wind | National Weather Service | River and Delta temperature models | To confirm product and format |
| Levee locations and island floor elevations | DWR Delta levee and island data | Breach what-ifs | To confirm a public source |

### 6.1 What DSM2 needs

DSM2 has a documented forecasting mode that fits this job. It uses recent field data for current conditions, extends the Martinez tide with an astronomical forecast, and projects the Delta one to ten weeks ahead.

Its boundary inputs, from DWR's own checklist, are:

- Sacramento River inflow, with the Yolo Bypass
- San Joaquin River inflow
- Smaller eastside inflows
- Banks and Jones export pumping, and smaller diversions
- Delta island consumptive use, taken as DWR's average values
- Gate and barrier operations
- Stage at Martinez

For salinity and temperature, DSM2's QUAL module also needs salinity at Martinez and at each inflow, inflow temperatures, and weather.

DSM2 reads these from the Army Corps' DSS time-series format. Converting our fetched data into that format is the main piece of glue code on the server.

### 6.2 Fetching rules

- Fetch hourly for gauges, and on each new cycle for forecasts.
- Keep every fetched value in a local store, so a source outage degrades the forecast instead of stopping it.
- Record the time and source of every value, and publish the age of the data with the flow field.
- Be a polite client: identify ourselves, cache, and never fetch more often than the source updates.

## 7. Engineering: the reach models

Each reach produces the same things, water velocity along its waterways over time, with temperature and salinity, by the method that suits its physics. Sections 7.5 and 7.6 cover temperature, salinity and what-ifs across all three reaches.

| | River | Delta | Bay |
| --- | --- | --- | --- |
| Extent | Shasta Dam to Sacramento | Sacramento and Vernalis to Chipps Island, with Suisun Marsh | Chipps Island to the Golden Gate, with Suisun Bay |
| Character | One-way river flow | Tidal, branching channels | Wide tidal bays |
| Flow source | Our routing model | DSM2 HYDRO, forecast mode | NOAA Bay forecast, fetched |
| Who does the math | Us | Us, running DWR's code | NOAA for currents, us for tracking |
| Calibration | Automated, ours | Done by DWR | Not needed |

### 7.1 River: Shasta Dam to Sacramento

**Structure.** The river is a chain of segments between gauges. Keswick Reservoir, between Shasta and Keswick dams, is a single mixed pool. The Keswick release drives everything below it. Tributary inflows are added where they join, taken from gauges; the largest, the Feather and the American, join just above Sacramento.

**Two speeds.** The river carries two different things at two different speeds, and the model keeps them apart.

| | A change in flow | A pollutant |
| --- | --- | --- |
| Travels as | A wave | Part of the water |
| Speed | Wave speed (celerity) | Mean water velocity: flow divided by cross-section area |
| Drives | Flow and stage at the gauges; the dam-release tool | The movie |
| Calibrated from | Release changes arriving at gauges | USGS field measurements and natural tracers (section 9.1) |

The wave is faster than the water. In a wide channel, theory puts it at about one and a half to one and two-thirds times the water velocity; each segment's ratio is measured, not assumed. A pollutant released at the same moment as a change in release arrives downstream later than the change in water level. Using one speed for both would make every pollutant arrival time too early.

**Flow routing.** Changes in release travel downstream as a wave. Each segment has a wave travel time and an attenuation that depend on flow.

**Water velocity and channel shape.** Each segment has a relation between flow and its cross-section area, width and depth, and so its mean water velocity. These start from the measurements USGS makes at each gauge, and are corrected toward the reach average by the tracer checks in section 9.1, because a gauge site is not a typical cross-section.

**Pollutant transport.** Within each segment the pollutant moves at the mean water velocity and spreads along the river by longitudinal dispersion. The dispersion coefficient starts from Fischer's estimate, K = 0.011 U²W² / (d u\*), where U is mean velocity, W width, d mean depth and u\* shear velocity, all taken from the segment's channel shape at the current flow. The estimate is known to be good only to within a factor of several, which is why section 9.1 checks it against the river's own tracers.

**Water levels from releases.** Routed flow at each gauge is converted to stage with that gauge's published rating curve. This gives the dam-release tool in the user specification.

**Known complications.**

- In floods the river spills over weirs into the Sutter and Yolo bypasses. Version 1 detects this from gauges and warns that the forecast is unreliable. After launch, the spill is modelled with the weir and pool components built for levee breaches (section 7.6).
- Large diversions remove water and pollutant in irrigation season. They are represented where gauged.
- Pools, eddies and backwaters hold some water back and let it go slowly, which gives a real plume a longer tail than the dispersion model produces. Version 1 does not model this; the tracer checks in section 9.1 show how large the error is.

### 7.2 Delta: Sacramento to Chipps Island

DSM2's HYDRO module runs on our server in forecast mode and writes flow and stage for every channel through the forecast window. Its QUAL module runs after it, on the server, for salinity and temperature, which are the same for every visitor (section 7.5). A visitor's own releases are tracked in the compute engine, not in QUAL, and QUAL doubles as a check on our tracer engine.

The whole DSM2 network is open for releases, including the San Joaquin side and the south Delta up to Vernalis, and the Suisun Marsh sloughs. That makes the San Joaquin inflow and its forecast a required feed, not an optional one.

DSM2 still runs its full network to Martinez, because its seaward boundary is the Martinez tide. Its channels seaward of Chipps Island are not used for tracking; they are kept as an overlap with the Bay model, to check one against the other (section 7.4).

A run covers a warm-up period of recent days, so the model starts from realistic conditions, followed by the forecast.

### 7.3 Bay: Chipps Island to the Golden Gate

NOAA's forecast gives currents on a triangular mesh at hourly steps. The server reduces these to depth-averaged currents on a lighter mesh for the compute engine.

Beyond the end of NOAA's forecast, the server extends the currents with a fit made from the archive of past NOAA fields. At each mesh point the fit has two parts: the tides, and a net seaward drift that grows with Delta outflow. The extension uses DSM2's forecast outflow at Chipps Island, so the Bay and the Delta agree on how much river water is leaving. In Suisun Bay that drift matters as much as the tide, which is why a tides-only extension is not enough. The extension is our own calculation and does not include wind.

A tidal fit needs at least a month of past fields to separate the main tides, and a year to do it well. The archive must be in hand before Phase 3: either NOAA's public archive goes back far enough, or we start saving the forecasts ourselves now.

### 7.4 Joining the reaches

- **River to Delta** at the Sacramento River inflow point of DSM2. The river model's outflow and pollutant load become the Delta's inflow.
- **Delta to Bay** at Chipps Island, detailed below.
- All three share one clock and one published time step.

**Why Chipps Island and not Martinez.** Both DSM2 and NOAA cover Suisun Bay, so one has to govern it.

- Suisun Bay is a wide, shallow bay with side bays and shoals. DSM2 represents it as a few one-dimensional channels, so a plume there could not spread sideways across the shoals into Grizzly and Honker bays. NOAA's two-dimensional mesh can show that.
- A model is least reliable near its own boundary. Martinez is DSM2's seaward boundary, where its water simply arrives from or leaves to "the ocean". At Chipps Island, DSM2 is well inside its network.
- Chipps Island is already the Delta's standard reference point: Delta outflow and the salinity line known as X2 are both reckoned there.

**The handoff line.** A line across the estuary at Chipps Island, from the Suisun Marsh shore to Mallard Island, split into segments, one for each DSM2 channel it crosses. The Suisun Marsh sloughs stay with DSM2, because DSM2 models the Montezuma Slough salinity control gates, so the line also takes in each point where a marsh slough opens into Suisun Bay. The exact points are set from both grids in Phase 3.

**Crossing the line.**

- Each parcel belongs to exactly one engine at a time, so nothing is counted twice.
- A parcel leaving a DSM2 channel seaward enters the Bay mesh on that channel's segment of the line, placed across it in proportion to the local flow.
- A Bay parcel crossing the line landward enters the DSM2 channel that owns that segment.
- Parcels cross in both directions with every tide. The method conserves mass whatever the two models do.

**The overlap.** The two models are forced separately, so their flows across the line will not match exactly. Between Chipps Island and Martinez both run, and the server compares their tidal and net flows each cycle and publishes the difference. A persistent mismatch shows up as parcels piling up or thinning out at the line; how large a mismatch is acceptable is set during Phase 3.

**Fallback.** If Phase 0 finds NOAA's mesh too coarse in Suisun Bay, or not reaching Chipps Island, the handoff moves to Martinez, and Suisun Bay is tracked in DSM2's channels with the sideways-spreading limit above noted on screen.

### 7.5 Temperature and salinity

Temperature and salinity are the same for every visitor, so the server computes them alongside the flow and publishes them with it. The compute engine's tracer does not carry them.

| Reach | Temperature | Salinity |
| --- | --- | --- |
| River | Our own 1D heat model: the Keswick release temperature carried downstream, warming and cooling with the weather. Checked against USGS gauges | Fresh water. Shown from gauge conductance, not modelled |
| Delta | DSM2 QUAL, driven by weather and inflow temperatures | DSM2 QUAL, which DWR calibrates for salinity; sea salt enters at Martinez |
| Bay | NOAA's forecast | NOAA's forecast |

- **Beyond each forecast.** Past the end of NOAA's forecast, Bay temperature and salinity are held at their last forecast values, averaged over a tide, and fade in certainty like the currents. Salinity changes slowly, so this stays a fair guide for days; temperature less so. River and Delta temperature beyond the weather forecast are held the same way.
- **The past window** uses the same models driven by measured inputs. Where a station measures temperature or conductance, the station overlay shows the measured value beside the model's.
- **Units.** Temperature in °C or °F, by region setting. Salinity in parts per thousand, with electrical conductivity alongside in the Delta, where it is the usual measure.
- **Water source.** QUAL can carry a separate marker for each inflow; DWR calls this fingerprinting. That gives the share of Sacramento, San Joaquin, eastside, farm drainage and ocean water in every channel, for the water-source layer. In the Bay, the ocean share follows from salinity.
- **X2** is estimated from the salinity field, near the bed where NOAA's three-dimensional output allows.

### 7.6 What-if scenarios

Each what-if is a full model run with one thing changed, published as its own set of files (section 8.3). It starts at now and runs to the end of the forecast; the past is shared with the main run. Temperature and salinity are rerun with it, so a breach shows salinity moving inland.

**Dam releases.** The river model reruns with a changed Keswick release. For a few release levels, DSM2 reruns too, so the change reaches the Delta.

**Levee breaches in the Delta.**

- The flooded island is a pool joined to its channel by the breach. DSM2 represents this with a reservoir and a connection to it, the way breaches such as Jones Tract in 2004 have been studied with it (*to confirm*).
- Flow through the breach follows the weir equation, for its width and the difference in water level across it. While the island fills, water pours in on every tide. Once full, the island fills and drains with each tide.
- Each breach site in the region package names the island's area and floor elevation, the channel it opens to, and a standard breach width. The breach widens to that width over a few hours.

**Levee breaches on the river.** A breach on the river above Sacramento takes flow out onto the floodplain. In the river routing model it is a sink using the same weir equation, with the floodplain as a pool. The routing model is not a flood model, so river breaches are marked as a rough guide.

**Menu size.** Every what-if is a full run every cycle. How many fit is set once Phase 0 has measured a DSM2 run. The starting target is six Delta breach sites, two river breach sites and four release levels, chosen with an eye to the islands most often discussed. The final numbers are set after Phase 0.

**After launch:** custom what-ifs on request (section 3.7), more breach sites, changed gate or pumping operations, and flood spills into the bypasses, all using the same machinery.

## 8. Engineering: tracer engine, forecast and display

The pollutant is carried as thousands of parcels, each holding a share of the released mass, moved through the published flow field by the compute engine.

### 8.1 Tracer engine

- **Release.** An instant release creates all parcels at once. A continuous release creates them steadily over its duration. An outfall creates them steadily from its start to the end of the timeline.
- **Decay.** If the release has a half-life, each parcel's mass falls by it every step.
- **Movement.** Each time step, a parcel moves with the local velocity plus a random step that represents dispersion.
- **Junctions.** At a channel junction a parcel takes an outgoing channel with probability proportional to that channel's share of the outflow.
- **Losses.** A parcel drawn into an export pump or diversion leaves the system and is counted.
- **Concentration.** Mass in the parcels within a stretch of channel, divided by the water volume of that stretch.
- **Mass balance.** Released mass equals mass in the water plus mass exported plus mass past the Golden Gate plus mass decayed, checked every step.

In version 1 the engine runs in a background thread of the visitor's browser, apart from the UI (section 4.2), so the map stays responsive. Parcel count is set by device capability: about 20,000 on a phone to start, more on a desktop. The parcel count and the number of flow particles are tuned in Phase 4 by measured frame rate.

### 8.2 Forecast horizon

| Reach | Solid forecast | Beyond that |
| --- | --- | --- |
| River | About 5 days, from published flow forecasts | Assume the last forecast flow persists |
| Delta | Tides for all 14 days. Inflows as far as the river forecasts, about 5 days; exports and gates as far as their published schedules. DSM2 can run ten weeks, but its inputs cannot | Inflows, exports and gates held at their last known values |
| Bay | To the end of NOAA's forecast | Our extension: tides plus outflow drift, no wind (section 7.3) |

Forecast length: 14 days, with uncertainty shown growing along the timeline. Water from Keswick takes several days to reach the Delta, so upstream releases need most of that window.

**Certainty.** Every reach has a certainty value at every published time step, from 1 down toward 0.

- Inside a reach's solid forecast, certainty is 1.
- Past it, certainty falls steadily to a floor at day 14. The rate and floor are set per reach; to start, every reach falls to 0.2.
- Once the river and Bay skill records (sections 9.1 and 9.2) are long enough, the rates are set from how far past forecasts actually drifted at each lead time, rather than by hand.
- Each parcel carries its own certainty: the lowest value it has passed through. A parcel moved by assumed flow stays uncertain, even if it later enters a reach that is still inside its solid forecast.
- The colour of a stretch of channel or patch of Bay is faded by the certainty of the parcels in it, weighted by their mass.

Temperature and salinity in the forecast fade by the same certainty as the flow in their reach.

This is certainty about the flow, not a statistical confidence. It does not cover errors in the models themselves inside the solid forecast, or in what the user entered.

**After launch: real spread.** The River Forecast Center also publishes ensemble flow forecasts, many possible futures rather than one. Running the river and DSM2 once per ensemble member would give real spread: a plume drawn from all of them, faded where they disagree. That multiplies the server's work and the published files, so it waits until the single forecast works.

### 8.3 Published flow-field format

- One small index file: time range, time step, data age per source, model status, and each reach's certainty at each time step.
- Binary files per reach per day, holding velocity and water volume per channel segment or mesh node per time step, quantised to keep them small. The compute engine fetches only the days it is about to play, so opening on "Now" loads one day, not 28.
- Optional fields per reach per day, beside velocity and volume: water level, temperature, salinity and water-source shares.
- A tidally averaged velocity per reach for the net-flow display. Averaging over the tides needs data on both sides, so the first and last half-day of the timeline use a shorter average.
- **What-ifs.** Each what-if on the menu is a full set of forecast days under its own name, sharing the main run's past days.
- The timeline holds 28 days: 14 past, 14 forecast.
- **Past days** are built from measured conditions: the river model driven by observed gauges, DSM2's warm-up period on observed inputs, and NOAA's nowcasts. Measurements arrive late and are revised, so the last two days of the past are rebuilt each cycle. Older past days are final; they never change, and browsers can cache them.
- **Forecast days** are rebuilt every cycle.
- Each day ends with the river and DSM2 states needed to start the next, so the past and forecast join without a jump at "now".
- Rough sizes, to be measured: the Delta is a few megabytes for two weeks; the Bay is the largest and may need coarsening. The past window doubles the total held, but not what a visitor downloads.
- The index file and the forecast days are replaced as a set, so a browser never mixes two cycles.

### 8.4 Display

The water is always shown moving: particles drift along every river, channel and slough and across the Bay, showing which way the water is going and how fast. When a pollutant is added, the particles carrying it take on its colour, and that colour fades as certainty fades.

**Flow particles, always on.**

- Small dots with short trailing streaks, moving with the water in the published flow field. Direction is the direction of travel; speed shows as how fast they move and how long their streaks are.
- In the channels they move along the channel and spread across its width when zoomed in far enough to see it. In the Bay they move in two dimensions.
- They are a neutral colour by default, so they read as "water" and leave every colour free for the pollutant. When the user colours the water by speed, temperature, salinity, water level or source (section 3.5), the flow particles take that colour from the field where they are.
- In net-flow mode they move with the tidally averaged velocity instead of the instant one.
- The tide is visible: in the Delta and the Bay the particles slow, stop and turn around with each tide.
- They run before any release, so the map always shows current conditions (section 3.2), and they keep running through the movie at the movie's speed.
- They are spread evenly through the water and kept at a steady density, so empty or crowded patches mean nothing. They are for showing the flow only and carry no mass.

**Pollutant particles.**

- The tracer engine's parcels (section 8.1) are drawn as particles in the same style, so the pollutant is visibly carried by the same water the flow particles show.
- Each is coloured by the concentration where it is, the stretch of channel or patch of Bay around it, not by its own share of mass. This keeps the colour smooth across the plume.
- Concentration uses a logarithmic scale, since concentrations span many orders of magnitude.
- Certainty fades the colour back toward the neutral flow-particle colour. Far into the forecast the plume becomes a pale tint on the moving water rather than a bright cloud.
- The palette is chosen so that its low end is still a strong colour and never pale. A faded high concentration must not look like a low concentration, so fading and concentration stay distinguishable.
- The legend shows both: concentration across, certainty down.
- When the water is coloured by something else, the pollutant particles are drawn as rings in a single strong colour, so the plume stays visible over the field.

**Colour scales.** Each layer has its own scale: concentration and speed on logarithmic scales; temperature, salinity and water-source shares on sequential scales; water level on a scale that runs either side of mean level. All are chosen to work for colour-blind users, and all fade toward neutral with certainty.

**Zoom.**

- At the whole-system view, Shasta to the Golden Gate, particles would be smaller than a pixel. There, each channel is drawn as a line coloured by the chosen layer and faded by certainty, and the Bay as a shaded field, with the particles faded out.
- Zooming in hands over smoothly from lines to particles. The zoom level where this happens is tuned in Phase 4.

**Rendering and other details.**

- Particle positions come from the compute engine, as snapshots at each time step (section 4.2). The UI smooths between snapshots and draws the particles with the graphics hardware through a custom map layer, so tens of thousands of particles stay smooth on a phone.
- The number of flow particles drawn is set by device capability, like the parcel count.
- The time slider drives the particles, the plume and the tide indicator together.
- Colours are chosen to work for colour-blind users and in both light and dark map styles.

## 9. Engineering: calibration, validation, deployment, licensing

Only the river model needs calibrating, and it calibrates itself from data the river already produces.

### 9.1 Automated river calibration

The river model has three sets of parameters, each calibrated from a different kind of evidence. Keeping them apart is the point: the wave outruns the water (section 7.1), so evidence about one says nothing direct about the other.

| Parameters | Used for | Calibrated from |
| --- | --- | --- |
| Wave travel time and attenuation | Flow and stage forecasts; the dam-release tool | Keswick release changes arriving at downstream gauges |
| Water velocity and channel shape | Pollutant travel time; the inputs to Fischer's estimate | USGS field measurements at each gauge, corrected by natural tracers |
| Dispersion | How fast the plume spreads | Fischer's estimate, checked against natural tracers |

- **Wave travel time.** Every change in Keswick release is a natural experiment. The server detects the change, finds its arrival at each downstream gauge, and updates the flow-to-wave-travel-time relation for each segment. How a sharp release change smooths out on the way down calibrates the wave's attenuation. Neither is used for pollutant travel or spreading.
- **Water velocity and channel shape.** Several times a year USGS measures each gauge's flow by hand, recording cross-section area, width and mean velocity. Fitting these against flow gives velocity, width and depth at any flow, at each gauge. New measurements are picked up as USGS publishes them.
- **Natural tracers.** Specific conductance and water temperature, where gauges record them, carry signals that move with the water: a pulse of tributary or drainage water, or a step in release temperature from Shasta's temperature control. When a signal passes two gauges, the lag between them gives the reach-average water travel time, which corrects the gauge-site velocity toward the reach average. The change in the signal's shape gives dispersion. Conductance is the better tracer; temperature is not conserved, since the river warms and cools along the way, so it is used for timing only.
- **Published studies.** Look for past time-of-travel or dye studies on the Sacramento below Keswick, and use any found as a one-off check on velocity and dispersion.
- **Schedule.** Recalibration runs nightly. Parameters change slowly and are logged, so a bad night's data cannot swing the model.
- **Skill scores.** Two, kept separate and published: predicted against observed arrival of flow changes, which scores the wave; and predicted against observed lag of tracer signals, which scores pollutant travel.

### 9.2 Validation

| What | Checked against |
| --- | --- |
| River flow and stage forecasts | Observed flow and stage at the gauges |
| River pollutant travel time and spreading | Natural tracer signals between gauges; any published time-of-travel studies |
| DSM2 flows from our run | Observed flows at DWR's check stations, such as Rio Vista |
| Our tracer engine in the Delta | DSM2's own transport modules on the same flow field |
| Our tracer engine overall | Measured conductivity, which is a natural tracer of seawater |
| Bay extension, tides and outflow drift | Later NOAA forecasts for the same hours |
| Delta to Bay handoff | DSM2 against NOAA flows across the Chipps Island line, and through the overlap to Martinez, every cycle |
| Certainty fade rates | How far past forecasts drifted from the past window that replaced them |
| River temperature | USGS gauge temperatures |
| Delta and Bay temperature and salinity | Measured temperature and conductance at CDEC and USGS stations |
| Net flow | Tidally filtered flows that USGS publishes at Delta stations |
| Levee breach what-ifs | Offline only, not in the app: a rerun of the 2004 Jones Tract breach against what was measured then |
| Mass balance | Exact, every run, including decay |

### 9.3 Deployment

- **One machine only.** Everything this app runs on the server side runs on the `webserver` host. No other machine is used, for anything, ever: they run Electric Eel.
- **Sharing the host with Electric Eel.** The same host runs Electric Eel's containers and the shared web edge, so this app always gives way:
  - the model runner has a fixed cap on cores and memory;
  - it runs at lower CPU and disk priority than every Electric Eel container;
  - it is pinned to the processor's efficiency cores, leaving the performance cores to Electric Eel.
- **Model runner**: its own Debian container on that host, holding the data fetchers, DSM2, the compilers and the scheduled jobs. Starting size: 8 efficiency cores, 8 GB of memory (12 at most), and its own 1 TB disk from the host's storage. Modelling and serving stay separate: the web edge only serves pages and files, and no model code is installed on it.
- **Web**: served at a path on tripofisch.com (name to choose), from the app's own folder, `/srv/fdmr`, on the existing web edge (container 703). It is reached through a Caddy route, the way Lux is served at `/play/`, so no new hostname or Cloudflare tunnel change is needed.
  - The model runner pushes each finished set of files over rsync and SSH, with a key that can write only into that folder. Each cycle goes into a new folder, and one link switch makes it live. The web edge never reaches into the model runner.
  - The web edge also carries Electric Eel's traffic. Any change to it is checked before Caddy reloads, made through the Caddyfile's master copy, and made only with the owner's OK.
- **Cloudflare caching**: past days never change, so they are sent with long-lived cache headers, and a Cloudflare cache rule serves them from Cloudflare's edge. This is required, not optional. The site shares one home internet connection and one tunnel with Electric Eel, and a burst of visitors must not be able to fill them.
- **Cadence**: gauges hourly; river model hourly; DSM2 every 6 hours, matching NOAA's four Bay cycles a day. The what-if menu reruns with each DSM2 cycle; if it does not fit in 6 hours, once a day.
- **Failure behaviour**: if a run fails, the last good flow field stays published and the status file says how old it is.
- **DSM2 on Linux first.** Phase 0 asks DWR for the Linux library and tries the Linux build in the model runner. A Windows VM for DWR's prebuilt release costs memory the host has little of to spare. It is used only if the Linux build stalls, kept small, and removed as soon as Linux works.

### 9.4 Licensing and credit

- **Our code**: PolyForm Noncommercial 1.0.0 for the software, and Creative Commons BY-NC 4.0 for the written and heritage content. None of the data sources requires GPL for our code, because DSM2 runs as a separate, unmodified program.
- **DSM2**: GNU GPL version 3 or later. We run it unmodified and do not redistribute it. Any code of ours that copies or modifies DSM2 source would have to be GPL, and GPL cannot carry a non-commercial restriction, so we keep the two strictly apart.
- **DWR data**: credited as the source; DWR's terms bar commercial redistribution.
- **USGS and NOAA data**: US government public data, credited.
- **Map**: OpenStreetMap data under the Open Database License, with the required attribution on the map.
- **Disclaimer** on every screen: educational model, not for emergency or regulatory use.

This section is not legal advice; the license choice deserves a second look before launch.

## 10. Build phases and open questions

The build runs in six phases, each ending in something that can be seen working; Phase 0 retires the risks before any map is drawn. The detailed plan, with workstreams, exit tests and risks, is in [build-plan.md](build-plan.md).

1. **Phase 0, proof.** Run DSM2 in the model runner: the Linux build if DWR's library is in hand, otherwise DWR's prebuilt Windows release in a small, temporary VM. Feed it today's conditions and get a forecast flow field out, with QUAL salinity. Time one run, to size the what-if menu. Confirm every data feed in section 6. Email DWR for the Linux library and the grid geometry.
2. **Phase 1, foundations and the river.** The module structure and its build checks (section 4.3); the published format, with its depth axis; the compute engine and UI kept apart from the first line (section 4.2). River routing model with automated calibration of both speeds, wave and water, shown on the map from Shasta Dam to Sacramento. First working pollutant movie. This phase is entirely our own code and Fischer's method. The math library, the model kinds and the region package (section 4.1) are set up here, with the small test region beside the real one.
3. **Phase 2, the Delta.** DSM2 on a schedule, with QUAL salinity, temperature and water-source fingerprinting; flow-field publishing; parcel tracking through the channel network. River and Delta joined.
4. **Phase 3, the Bay.** NOAA currents, temperature and salinity; two-dimensional tracking; the extension with outflow drift; the handoff at Chipps Island.
5. **Phase 4, product.** Layers and toggles; the river temperature model; outfalls and decay; read-off answers and landmarks; the what-if menu of dam releases and levee breaches; the History section; phone layout; and the Utilities page on tripofisch.com.
6. **Phase 5, hardening.** Native Linux DSM2, validation report, monitoring, and license and credits review. Launch follows this phase. The 1982 grid, the field-data layer and the historic scenario presets are a post-launch release.

### Decisions log, 24 November 2026

- License: PolyForm Noncommercial 1.0.0 for code, CC BY-NC 4.0 for content. See section 9.4.
- The model runner is its own Proxmox guest; the web guest only serves. See section 9.3.
- Movie length is 14 days, with growing uncertainty shown. See sections 3.4 and 8.2.
- The 1982 grid and all historic layers come after launch.
- The punch cards are not read. The app does not depend on the original code or on any claim to it. A History section will hold pictures.
- The San Joaquin side of the Delta is in scope as a release location, up to DSM2's boundary at Vernalis. The river above Vernalis stays out.
- This document is kept in the repository's docs folder.
- River calibration keeps wave speed and water speed apart. Release changes calibrate flow routing only; pollutant travel and spreading come from USGS field measurements and natural tracers. See sections 7.1 and 9.1.
- One timeline: the last 14 days, now, and the next 14 days. The past window rolls forward and is kept up to date. "Now" is the opening view. See section 3.4.
- Moving particles show the flow everywhere, always. Particles carrying a pollutant take its colour, and the colour fades as certainty fades. See sections 3.4, 8.2 and 8.4.
- Model kinds and math components are plug-ins behind versioned interfaces, with conformance tests. The published format and the parcels carry a depth axis from version 1, so 3D mixing models can be added later without a rewrite. See section 4.1.
- The engine is built to serve any geography. Its math is a shared library written once; each region's package picks a model kind for each reach (river routing, 1D tidal network, 2D field, pool, ocean boundary) and its data feeds. Launch ships Shasta Dam to the Golden Gate; other estuaries and rivers follow. See section 4.1.
- The Delta hands off to the Bay at Chipps Island rather than Martinez. Martinez is the fallback if Phase 0 finds NOAA's mesh cannot support the handoff at Chipps. See section 7.4.
- A pollutant can be released at any time in the last 14 days as well as now or in the forecast; it plays forward through now into the forecast. See section 3.3.
- Confirmed: the Bay extension of tides plus outflow drift (section 7.3), the search for published river dye studies (section 9.1), and the certainty method (section 8.2).
- Map layers: toggles choose what colours the water (plain, contaminant, speed, temperature, salinity, water level, water source), whether the flow is shown moment by moment or net over the tides, and which overlays are drawn, including the X2 line and measured readings such as dissolved oxygen and turbidity. All four are in version 1. See section 3.5.
- Temperature and salinity are in version 1, computed on the server for every reach. See section 7.5.
- Releases include ongoing outfalls, such as a new sewage plant discharge, and an optional half-life. See section 3.3.
- What-ifs, changed dam releases and levee breaches, come from a menu computed in advance on the server, so user input still never leaves the device. See sections 3.7 and 7.6.
- Bioaccumulation in shellfish is planned for after launch: a virtual shellfish bed, with a menu of real substances and species. See section 3.10.
- The Eureka dye study, the oysters turned red at the Humboldt Bay outfall, is a story in the History section. See section 2.
- The UI only displays and the compute only computes, through one interface, so they can run on separate machines. In version 1 the compute engine runs in the visitor's browser. All on-screen text is kept out of code; English only at launch. See section 4.2.
- The specification is agreed as version 1.0. The build follows the plan in build-plan.md.
- Hardware: the `webserver` host only. No other machine is touched by this app, for any reason; they run Electric Eel. On that host the app gives way to Electric Eel: capped, lower priority, pinned to the efficiency cores. Cloudflare caching of past days is required. See section 9.3.
- Languages: Rust for the math library and compute engine; Python for the model runner, data adapters and publisher, calling the Rust math. See section 4.2 and build-plan.md.
- Code is built from small modules with explicit seams; compute and UI are never mixed, and the build enforces the allowed dependencies. See section 4.3.
- Swept all remaining proposals into decisions:
  - up to five releases at once;
  - certainty in words in three tiers;
  - a second, plain test region outside California from Phase 1;
  - the self-hosted map stack;
  - a flood-spill warning in version 1, with modelling after launch;
  - a starting what-if menu of six Delta breaches, two river breaches and four release levels;
  - about 20,000 parcels on a phone to start;
  - the update cadence;
  - custom what-ifs and ensemble spread after launch.

### Open technical questions, answered in Phase 0

- Does DSM2's forecast mode run cleanly from a script, and how long does one run take?
- What is the real forecast horizon and file size of NOAA's Bay product?
- Is DSM2's channel geometry available outside DWR's network?
- Which stations give each export, gate and inflow value in near real time?
- Which river gauges have field measurements, conductance and temperature records long enough to calibrate water velocity and dispersion? Are there published time-of-travel or dye studies on the Sacramento?
- How far back does NOAA's public archive of Bay forecasts go? If it is short, start saving them ourselves at once, for the Bay extension's fit.
- Do NOAA's Bay files carry temperature and salinity, and at what depths?
- How does DSM2's forecast mode set salinity at Martinez, and what weather inputs does QUAL need for temperature?
- Where are public levee locations and island floor elevations for the Delta, and how has DSM2 represented past breaches?

### After launch: before the second region

- A general 1D tidal model: DSM2 or another open model set up on the new network, or our own solver in the math library (section 4.1)?
- Which region comes second, judged by how much public data it has and whether a 2D forecast already covers its estuary?

## Sources

- [DSM2 source, build guide and documentation](https://github.com/CADWRDeltaModeling/dsm2), read 24 November 2026
- [DSM2 at the Department of Water Resources](https://water.ca.gov/Library/Modeling-and-Analysis/Bay-Delta-Region-models-and-tools/Delta-Simulation-Model-II)
- [NOAA San Francisco Bay Operational Forecast System](https://tidesandcurrents.noaa.gov/ofs/sfbofs/sfbofs_info.html)
- [NOAA forecast system data on AWS](https://docs.opendata.aws/noaa-ofs-pds/readme.html)
- [USGS Water Data APIs migration guide](https://api.waterdata.usgs.gov/docs/ogcapi/migration/)
- [California Nevada River Forecast Center hourly forecast files](https://www.cnrfc.noaa.gov/deterministicHourlyProductCSV.php)
- [Protomaps](https://protomaps.com/)
- [Hugo B. Fischer papers, UC Riverside](https://public.ucrlib.aspace.cdlib.org/repositories/5/resources/816)
- [Fischer, Longitudinal Dispersion in Laboratory and Natural Streams, Caltech 1966](https://thesis.library.caltech.edu/9180)
- Fischer, List, Koh, Imberger and Brooks, *Mixing in Inland and Coastal Waters*, Academic Press 1979: the source of the dispersion estimate in section 7.1. Citation from memory, to check against the book.

The NOAA, USGS and River Forecast Center entries were checked from search results, not read in full; Phase 0 reads each one properly.
