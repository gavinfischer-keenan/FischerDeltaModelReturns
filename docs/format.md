# Published flow-field format, version 1.1

The model runner writes this format and the compute engine reads it (specification sections 4.2 and 8.3). It names no place: every region and every model kind uses it unchanged.

Only this format carries a version number (specification section 4.3). A reader refuses a file whose major version it does not know, rather than misreading it.

## Files

For one region and one model cycle:

```
<cycle>/
  index.json                  what is published, and how certain it is
  <reach>/<YYYY-MM-DD>.fdf    one binary field file per reach per day
```

A what-if is a full set of forecast days under `whatif/<name>/`, sharing the main cycle's past days.

## index.json

```json
{
  "format": "fdmr-flow-field",
  "format_version": [1, 0],
  "region": "sf-bay-delta",
  "cycle": "2026-11-25T06:00Z",
  "created": "2026-11-25T06:41:12Z",
  "time": { "start": "2026-11-11T06:00Z", "step_s": 900, "days": ["2026-11-11", "..."] },
  "now": "2026-11-25T06:00Z",
  "data_age_s": { "usgs": 1800, "cnrfc": 9000 },
  "reaches": [
    {
      "id": "river",
      "kind": "river-routing",
      "dims": 1,
      "elements": 412,
      "layers": 1,
      "fields": ["velocity", "volume", "level", "temperature", "salinity"],
      "certainty": [1.0, 1.0, "... one value per time step"]
    }
  ],
  "status": "ok"
}
```

`certainty` has one value per published time step, from 1 down toward 0 (specification section 8.2).

**Version 1.1** adds two things, both additive, so a 1.0 reader still reads every 1.0 reach:

- A reach may carry its own `time` block, laid out like the top-level one, and its own `now`. This is for a reach published on its own schedule. For example, the Delta's DSM2 run happens every 6 hours while the river runs hourly. A reach without one uses the top-level `time`.
- A reach may be a network (`"network": true`); its geometry is described below.

**Checks published with a reach.** A reach entry may also carry how its model compared with measurements this cycle, for anyone to read. A reader that does not use them ignores them.

- `net_checks` (a network reach): for each gauge, the model's net flow (its flow through the gauge's channel, Godin-filtered) against the gauge's own tidally filtered flow, as daily means over the last 14 days. Each entry has `name`, `site`, `channel`, `days`, `measured_mean`, `model_mean`, `mean_difference` and `daily_mae`, in m³/s.
- `overlap` (a mesh reach fed by a network): both models' flow across the join before correction. It gives `net` (the mean) and `tidal` (the standard deviation about it), in m³/s.
- `outflow_correction` (the same): what was added to the mesh's currents so that its flow through the join by each crossing matches the network's flow in that channel at every hour. `crossings` gives, per crossing, both models' net and tidal flow before the correction, their correlation and `opposite_share` (how often they ran opposite ways); the totals are as below. It gives both models' net flow and tide size before the correction (`noaa_net`, `dsm2_net`, `noaa_tidal`, `dsm2_tidal`, `tidal_correlation`), the flow added (`added_mean`, `added_range`) and the largest hourly miss left (`largest_miss`), in m³/s.
- `outflow_checks` (a mesh reach fed by a network): the network's net outflow across the join, as daily means, against each measured outflow. Each has `name`, `site`, and `all`, `dry` and `wet`, each with `days`, `model_mean`, `measured_mean` and `difference` (m³/s). A dry day is one with no rain running off.
- `quality_seam` (a mesh reach fed by a network): for `salinity` and `temperature`, the mean step across each crossing of the join (network less mesh, absolute) before and after the mesh's values are made to meet the network's there (`step_before`, `step_after`, one per crossing in the join's order).
- `quality_correction` (a mesh reach): for `salinity` and `temperature`, each station's mean misfit before correction (measured less modelled) and `left_out`, the mean miss at that station when the correction is made from the other stations alone; plus `measured_to`, the last measured day. A station marked `check_only` steers nothing; its `left_out` is the miss of the correction made from all the others, an independent check.
- `currents_check` (a mesh reach): the mesh's currents against high-frequency radar's measured surface currents, every past hour both have, at each radar cell with data at least half the hours and a mesh node within `near_m`: `cells`, `hours`, `pairs`, both mean speeds (m/s), the RMS of the vector difference, and the complex correlation (Kundu 1976): its magnitude (`correlation`, 0 to 1) and `veering_deg`, the angle the mesh's currents turn from the radar's, anticlockwise. The radar measures the top metre or so; the mesh's currents are depth-averaged.
- `swot_checks` (a river reach): the river's published widths against the SWOT satellite's measured widths over the last 60 days, per SWORD reach (`id`, `km`, `passes`, `swot_width`, `model_width`, m), and the `median_ratio` of model to SWOT.

The cycle's `overlays.json` lists the map's stations, from any agency (`source`): each with its latest readings and, where a model covers it (a network's channel, a river's element or a mesh's node within 1 km; `model_reach`, `model_km`), that model's value beside each reading, and `checks`: its readings over the last 14 days against that model's at the same moments (`readings`, `mean_misfit`, `rms_misfit`, measured less modelled). Each structure lists its latest `readings` (a series named in its `daily` list is the daily one, allowed three days); a dam with `storage_report` adds `storage`: `capacity_m3` and `average_m3` (the historical average for the date, from DWR's daily reservoir report) and, against its own storage reading, `percent_full` and `percent_of_average`; a dam with `snow` adds, per basin group, `group`, `site`, `stations`, `swe_mm` (the group's mean of DWR's reviewed snow sensor values), `percent_april1` (their total against their April 1 averages' total) and `change_7d_mm`. With overlays.toml's `[satellite_temperature]`, `satellite_temperature` gives the water-surface temperature seen from orbit (ECOSTRESS, 70 m) over its last `days`: `passes`, `newest`, `stations` (each station's reading nearest a pass, within 30 minutes, against the satellite at the nearest pixel whose eight neighbours are all clear water: `pairs`, `mean_difference`, `rms_difference`, overall and `per_station`), and `models`, per reach, each channel, element or node whose points fall on such pixels at a pass inside the reach's time axis (`places`, `pairs`, `mean_difference`, `rms_difference`). Differences are the satellite less the station or the model; the satellite sees the water's skin. With region.toml's `[river_temperature.land]`, `land_temperature` sets the land along each river against the land at the airport whose weather that stretch takes (fdmr_runner.land): ECOSTRESS land surface temperature over the land within `band_m` of the river and within `airport_m` of the airport, at each pass that saw both (within 10 minutes), over the last `days`. Per `reach` and `zone` (airport), `day` and `night` apart (local solar hour 8 to 17, 20 to 5): `passes`, `band_mean`, `airport_mean` and `contrast` (band less airport, degC). The place means themselves are kept in the store under source `ecostress` (sites `band:<reach>:<zone>` and `airport:<zone>`, parameter `land_surface_temperature`). With region.toml's `[soil_moisture]`, `soil_moisture` gives how wet the land is where rain runs off into the water unmeasured (fdmr_runner.soil; NASA SMAP Level 4, 9 km, three-hourly, about two days behind): per area (`site`: `river:<reach>`, the land within `corridor_m` of each river below its dam, or `area:<name>`, a box), the latest period's middle `t` and its means over the area's land cells: `rootzone` and `surface` (water by volume, m3/m3), `wetness` (0 to 1, of what the soil holds), `percentile` (the root zone against that place's own climate for the time of year) and `rootzone_change_7d`. Every period is kept in the store under source `smap`. Each area also gives the water reaching it: `rain_24h_mm` and `rain_7d_mm` to `rain_day_end` (the Weather Service's daily precipitation analysis, 24 hours to 12:00 UTC; `rain_7d_days` days found) and `rain_7d_percent_normal` (against PRISM's normal for those days), and `snowmelt_7d_mm` and `snow_water_mm` to `snow_day_end` (SNODAS, modelled; no melt where there is no snowpack). Kept daily under sources `nws-precip` and `snodas`. `tide_residual` gives, per NOAA tide station measuring water level, its measured level less NOAA's astronomical prediction (both above the station's MLLW datum) as hourly means over the last 7 days: `latest_hour` and `latest_m`, `mean_m`, `highest_m` (with `highest_at`), `lowest_m` and `hours`, m. The residual is everything that is not the tide: storm surge, wind set-up, low pressure, river flow at the Delta end, and the ocean's own slow rise and fall (El Nino, coastal waves); MLLW is the 1983-2001 epoch, so sea level's rise since is in it too. Station readings now include `wind_speed`, `wind_direction` (degrees true, from), `wind_gust`, `air_pressure` (hPa) and `water_level` (m above MLLW) where a station measures them. `level_checks` sets each station that measures its level (CDEC `gage_height`) against the reach that publishes `level` nearest it (within 1 km), over the last 7 days, with each series' own mean taken away, since a gauge and a model rarely share a datum: `steps`, `rms_m`, `range_ratio` (the model's tidal range, 5th to 95th percentile, against the station's), `lag_min` (how late the model's tide runs) and `mean_offset_m` (meaningful only on a shared datum).

The cycle's `skill.json` (index `skill`) scores the prediction ledger (fdmr_runner.ledger, issue #9). Four times a day a cycle's predictions are kept at every check point (each map station a model covers, for the quality it measures; each river gauge below a release, flow; each network flow check that names its channel, flow), hourly to 48 hours ahead and six-hourly to 15 days, with the cycle's own index.json (without its large arrays) as the record of how it was made. Each kept prediction whose time has come is set against the reading nearest its time, within 30 minutes. `skill.json` gives, over the last `window_days` (30), the `cycles` kept, and `groups`: per `reach`, `parameter` and `lead` band (0-6 h, 6-24 h, 1-2 days, 2-5 days, 5-15 days) the `pairs`, `mean_misfit` and `rms_misfit`, measured less predicted, in the parameter's stored units (m3/s, degC, uS/cm, psu); and `points`, the same per check point (`reach`, `source`, `site`, `parameter`, `name`, `leads`). `kept_this_cycle` is how many values this cycle added to the ledger (0 between the four a day).

## Reach geometry (`geometry/<reach>.json`)

Where each element lies, for the compute engine and for drawing. For a 1D reach:

```json
{
  "reach": "river",
  "dims": 1,
  "elements": [{ "start_m": 0.0, "end_m": 1002.3 }, "..."],
  "lines": [[[-122.4462, 40.6078], ["..."]], "..."]
}
```

A river reach whose temperature is modelled also has `<day>.quality.fdf` per day, with `temperature` (degC) per element, and its index entry lists `quality_fields` and carries `temperature_checks`: for each station measuring it, its chainage (`km`), `readings`, the model's misfit before correction (`mean_misfit`, `rms_misfit`, degC, measured less modelled) and its miss when the correction is made from the other stations alone (`left_out_mean`, `left_out_rms`).

`start_m` and `end_m` are distances along the reach (chainage). `width_m` (optional) is the element's water-surface width at its median flow over the published steps: a reader spreads flow particles across it, and seeds them evenly over the water's surface, as for a network. `lines` holds a short polyline per element, longitude and latitude, from its upstream end to its downstream end.

### Network geometry (1D, version 1.1)

A channel network, such as a tidal delta, is many short channels meeting at junctions. Each element is one channel:

```json
{
  "reach": "delta",
  "dims": 1,
  "network": true,
  "elements": [{ "id": 1, "up": 1, "down": 2, "start_m": 0.0, "end_m": 5762.1 }, "..."],
  "nodes": [{ "id": 1, "lon": -121.3141, "lat": 37.6712 }, "..."],
  "lines": [[[-121.3141, 37.6712], ["..."]], "..."]
}
```

- `id` is the flow source's own channel number.
- `up` and `down` are junction ids. Flow is positive from `up` to `down`.
- `start_m` is 0 and `end_m` is the channel's length.
- `width_m` (optional) is the channel's mean water-surface width: for DSM2, each of its cross-sections' width at the channel's mean wetted area over the run's last week, averaged along it. A reader spreads the channel's flow particles across it, and seeds them evenly over the water's surface, as on a mesh, so that particles are as dense on both sides of a join.
- `nodes` gives each junction's position. A junction may carry a `kind`:
  - `stage_boundary` is where the sea sets the level; water leaving there goes to sea;
  - `flow_boundary` is where a river or boundary flow enters or leaves.
- `lines` runs from the `up` junction to the `down` one.

A network may also have pools: well-mixed bodies of water, such as flooded islands and forebays, joined to junctions.

```json
"pools": [{ "id": "clifton_court", "area_m2": 8534000, "bottom_m": -2.36, "nodes": [72] }],
"links": [{ "pool": "clifton_court", "node": 72 }],
"exports": [{ "name": "swp", "label": "State Water Project pumps (Banks)", "pool": "clifton_court" }]
```

- A pool may carry `lon` and `lat`, where it lies, for drawing; without them a reader places it among the junctions it joins.
- `links` lists every pool's connections to junctions. Their order is the order of the link field file below.
- `exports` names the external flows that are exports: water pumped out of the network. Each sits at a `node` or in a `pool`.

Each day of a network reach then has two more field files beside `<day>.fdf`:

- **`<day>.nodes.fdf`** has one element per junction, in `nodes` order, then one per pool, in `pools` order. Its fields:
  - `inflow` (m3/s): water coming in from outside;
  - `export` (m3/s): water taken by the named exports;
  - `withdrawal` (m3/s): water taken by anything else, such as farm intakes;
  - `volume` (m3): each pool's water, with no value at plain junctions.
- **`<day>.links.fdf`** has one element per link. Its `flow` field (m3/s) is positive from the pool to its junction.
- **`<day>.quality.fdf`** has one element per channel, like `<day>.fdf`, and holds water-quality fields: `ec` (uS/cm), `salinity` (psu), `temperature` (degC) and `source:<name>` (shares, 0 to 1). The reach's index entry lists them as `quality_fields`. They live in their own file so that moving parcels does not mean downloading them; a reader loads it only to show them. A `.nodes.fdf` may also carry each pool's `ec`.

### Mesh geometry (2D, version 1.1)

A 2D reach is a triangular mesh, such as the Bay's (from NOAA's forecast, coarsened). Its geometry file:

```json
{
  "reach": "bay", "dims": 2,
  "lon": [-122.48, ...], "lat": [37.81, ...],
  "x": [1960123.4, ...], "y": [631234.5, ...],
  "depth_m": [12.3, ...],
  "triangles": [[0, 1, 2], ...],
  "edges": { "open": [[4, 9], ...], "delta": [[5310, 5311], ...] }
}
```

- `lon`, `lat`: each node, for drawing. `x`, `y` (m): the same nodes in a planar projection (the source model's own), for moving parcels.
- `depth_m`: still-water depth at each node, positive down, on the datum of the `level` field.
- `triangles`: node triples, counter-clockwise in `x`, `y`.
- `edges`: boundary edges that are not land, as node pairs: `open` where water leaves to the open sea, and one list per handoff, named by the reach it hands to. Every other boundary edge is land.

Each day's `<day>.fdf` has one element per node, `u` and `v` (m/s, depth-averaged, east and north) and `level` (m), all linear within each triangle. `<day>.quality.fdf` holds `temperature` (degC) and `salinity` (psu), depth-averaged. A 2D reach's steps may differ from the other reaches' (the Bay's are hourly, as NOAA publishes them), and it may end sooner: its entry in `index.json` carries its own `time`.

### Handoffs between reaches (version 1.1)

`index.json` may list where parcels leaving one reach enter the next:

```json
"handoffs": [{ "from": "river", "to": "delta", "node": 330 }]
```

Here, parcels passing the end of the `river` reach enter the `delta` network at junction 330, in the channel that starts there, at the moment they arrived, with their mass. A handoff may also name the water-source group (`"source": "sac"`) the giving reach's water belongs to, for showing water sources upstream of the network.

A river reach may also end on another, as a tributary joins a river: `{ "from": "feather", "to": "river", "at_m": 354012.3 }`. Parcels passing its end enter the other reach `at_m` metres along it. A reach with no `source` of its own has that of the reach it flows into.

Where a network reach meets a mesh reach that covers some of its water (the Delta and the Bay), the join is a line, the mesh's edge, and runs both ways:

```json
{ "from": "delta", "to": "bay", "owned": [416, 417, ...],
  "cuts": [{ "element": 51, "at": 0.809, "inside": "down", "x": 2009876.5, "y": 541234.4, "lon": -121.8003, "lat": 38.0191 }, ...],
  "junctions": [] }
```

- `owned`: the network's channels lying in the mesh's water from end to end. The network still publishes their flow; nothing is tracked in them, and a reader does not draw them.
- `cuts`: every channel with one end in the mesh's water, cut where it enters (`at`, 0 to 1 along the channel from its upstream end; `inside`, which end is the mesh's). The network draws and tracks only its own part. A parcel or flow particle carried past the cut toward the mesh passes to the mesh at `x`, `y` (the cut's place in the mesh's coordinates, just inside it). One crossing the mesh's handoff edges (named after the network, in its geometry's `edges`) comes back at the nearest cut, into that channel's own part, moving out. Cuts are numbered in the order listed, across all of a network's joins.
- `junctions`: any junction inside the mesh where one of the network's own channels, wholly outside, meets an owned one: it serves as that channel's cut, at its end (`element`, entered at its upstream end for `fraction` 0, downstream for 1).

The model runner makes the mesh's flow through its edges by each cut equal the network's flow in that channel at the cut, hour by hour, and the mesh's water quality meet the network's there (specification 7.4; published as the mesh reach's `outflow_correction` and `quality_seam`).

Where a network's pumps feed canals (a reach of kind `canal-network`), the join lists `outlets` instead:

```json
{ "from": "delta", "to": "canals", "outlets": [{ "export": "swp", "node": 1 }, "..."] }
```

Each names one of the giving network's `exports` and the junction of the canals reach its water enters. What that export's pumps take, parcels and flow particles, is passed on there instead of leaving the model; the network's other exports still take things out. The canals reach is itself a network (its geometry also lists `inlets`, the same pairs). Each canal is a chain of channels, its flow at every moment the giving network's export, the same along it. Its `exports` are each canal's last junction, where the water is carried on beyond the map. Its `quality_fields` are the intake's, carried down at the water's travel time (`fdmr_runner.canals`; regions/<id>/canals.toml gives each canal's route, its end and its sourced cross-sections).

Each parcel belongs to one reach at a time, with its mass, so the reaches together conserve mass whatever their two flows do (specification 7.4).

## Field files (`.fdf`)

Little-endian throughout. A file holds one reach for one day.

### Header

| Bytes | Type | Content |
| --- | --- | --- |
| 4 | bytes | magic `FDF1` |
| 2 | u16 | major version (1) |
| 2 | u16 | minor version (0) |
| 2 | u16 | length of the reach id, then that many bytes of UTF-8 |
| 1 | u8 | dimensions: 1, 2 or 3 |
| 4 | u32 | elements: channel segments (1D) or mesh nodes (2D, 3D) |
| 4 | u32 | layers: 1 for 1D and 2D; the depth axis is always present (specification section 4.1) |
| 4 | u32 | time steps in this file |
| 8 | i64 | time of the first step, seconds since 1970-01-01 UTC |
| 4 | u32 | step length in seconds |
| 2 | u16 | number of fields |

Then one descriptor per field:

| Bytes | Type | Content |
| --- | --- | --- |
| 2 | u16 | length of the name, then the name, UTF-8 |
| 2 | u16 | length of the unit, then the unit, UTF-8 (SI) |
| 1 | u8 | encoding: 0 = f32, 1 = i16 quantised |
| 8 | f64 | scale (quantised only) |
| 8 | f64 | offset (quantised only) |

### Data

The fields follow in descriptor order. Each field is `steps x layers x elements` values, with the element index varying fastest.

- An `f32` field stores values as they are.
- An `i16` field stores `q`, where `value = offset + scale * q`. The writer picks `scale` and `offset` from the field's range, so the rounding error is at most `scale / 2`. `q = -32768` means "no value".

### Standard field names

| Name | Unit | Meaning |
| --- | --- | --- |
| `flow` | m3/s | Flow through the element, positive downstream (1D) |
| `velocity` | m/s | Mean velocity along the channel, positive downstream (1D) |
| `u`, `v` | m/s | Eastward and northward velocity (2D, 3D) |
| `volume` | m3 | Water volume of the element |
| `area` | m2 | Cross-section area (1D) |
| `dispersion` | m2/s | Longitudinal dispersion coefficient (1D) |
| `level` | m | Water surface elevation against the region's datum |
| `temperature` | degC | Water temperature |
| `salinity` | psu | Salinity |
| `source:<name>` | 1 | Share of water from one source, 0 to 1 |
| `net_velocity` | m/s | Velocity averaged over the tides, for the net-flow display |
| `flow_up`, `flow_down` | m3/s | Flow at an element's upstream and downstream ends, positive downstream (1D networks, where flow changes along a channel with the tide) |

A reader ignores fields it does not know, so new fields do not need a new major version. Anything that changes how existing bytes are read needs a new major version.
