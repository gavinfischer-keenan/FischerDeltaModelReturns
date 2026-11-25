# Published flow-field format, version 1

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

`start_m` and `end_m` are distances along the reach (chainage). `lines` holds a short polyline per element, longitude and latitude, from its upstream end to its downstream end.

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
| `level` | m | Water surface elevation against the region's datum |
| `temperature` | degC | Water temperature |
| `salinity` | psu | Salinity |
| `source:<name>` | 1 | Share of water from one source, 0 to 1 |
| `net_velocity` | m/s | Velocity averaged over the tides, for the net-flow display |

A reader ignores fields it does not know, so new fields do not need a new major version. Anything that changes how existing bytes are read needs a new major version.
