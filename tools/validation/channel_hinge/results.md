# Held-out check of the hinged channel fit (27 Nov 2026)

`python tools/validation/channel_hinge/holdout.py STORE region.toml` on the model runner, against every USGS gauge of `regions/sf-bay-delta/region.toml` with field measurements: each fitted on the ten years before the last two, then judged on the last two years' measured velocity (flow over area) and top width. A gauge's shape is hinged only where the hinge beats the single power law by more than 10 in BIC (`fdmr_plugins.river_routing.channel`); elsewhere both columns are the same fit.

| Gauge | Name | Fitted on | Judged on | Fit | log RMSE, velocity: single → as fitted | log RMSE, width: single → as fitted |
| --- | --- | --- | --- | --- | --- | --- |
| 11370500 | Sacramento R at Keswick | 111 | 17 | single | 0.154 → 0.154 | 0.293 → 0.293 |
| 11372000 | Clear Creek near Igo | 119 | 15 | hinged at    66.3 m3/s | 0.201 → 0.210 | 0.069 → 0.069 |
| 11374000 | Cow Creek near Millville | 109 | 16 | hinged at     0.3 m3/s | 0.357 → 0.369 | 0.156 → 0.173 |
| 11376000 | Cottonwood Creek near Cottonwood | 119 | 15 | hinged at    21.5 m3/s | 0.488 → 0.386 | 0.368 → 0.245 |
| 11376550 | Battle Creek below Coleman Fish Ha | 109 | 16 | hinged at    77.0 m3/s | 0.086 → 0.094 | 0.140 → 0.140 |
| 11377100 | Sacramento R above Bend Bridge nea | 95 | 17 | hinged at   926.0 m3/s | 0.092 → 0.081 | 0.040 → 0.058 |
| 11383500 | Deer Creek near Vina | 113 | 14 | single | 0.257 → 0.257 | 0.135 → 0.135 |
| 11389500 | Sacramento R at Colusa | 72 | 13 | single | 0.047 → 0.047 | 0.043 → 0.043 |
| 11390500 | Sacramento R below Wilkins Slough  | 65 | 12 | single | 0.051 → 0.051 | 0.063 → 0.063 |
| 11407000 | Feather R at Oroville | 21 | 4 | single | 0.012 → 0.012 | 0.010 → 0.010 |
| 11421000 | Yuba R near Marysville | 97 | 18 | hinged at    99.7 m3/s | 0.287 → 0.275 | 0.136 → 0.084 |
| 11425500 | Sacramento R at Verona | 69 | 13 | hinged at  1064.7 m3/s | 0.059 → 0.043 | 0.059 → 0.031 |
| 11446500 | American R at Fair Oaks (Nimbus Da | 77 | 11 | single | 0.123 → 0.123 | 0.088 → 0.088 |
| 11447650 | Sacramento R at Freeport | 476 | 112 | hinged at   574.8 m3/s | 0.159 → 0.049 | 0.052 → 0.042 |
| 11424000 | Bear R near Wheatland (below Camp  | 91 | 18 | single | 0.622 → 0.622 | 0.397 → 0.397 |
| 11418000 | Yuba R below Englebright Dam (Yuba | 12 | 4 | single | 0.404 → 0.404 | 0.049 → 0.049 |
| 11335000 | Cosumnes R at Michigan Bar | 73 | 12 | single | 0.295 → 0.295 | 0.194 → 0.194 |
| 11273400 | San Joaquin R above Merced R near  | 79 | 13 | hinged at    25.2 m3/s | 0.339 → 0.264 | 0.249 → 0.185 |
| 11274000 | San Joaquin R near Newman | 67 | 12 | single | 0.465 → 0.465 | 0.162 → 0.162 |
| 11274550 | San Joaquin R near Crows Landing | 63 | 12 | single | 0.384 → 0.384 | 0.154 → 0.154 |
| 11290000 | Tuolumne R at Modesto | 70 | 11 | single | 0.257 → 0.257 | 0.397 → 0.397 |
| 11303000 | Stanislaus R at Ripon | 60 | 10 | hinged at    31.1 m3/s | 0.085 → 0.073 | 0.019 → 0.046 |
| 11303500 | San Joaquin R near Vernalis | 75 | 11 | single | 0.227 → 0.227 | 0.104 → 0.104 |
| 11289650 | Tuolumne R below La Grange Dam | 78 | 22 | hinged at    16.2 m3/s | 0.430 → 0.497 | 0.257 → 0.281 |
| 11302000 | Stanislaus R at Orange Blossom Bri | 20 | 4 | single | 0.071 → 0.071 | 0.135 → 0.135 |
| 11451000 | Cache C near Lower Lake (Clear Lak | 52 | 18 | single | 0.334 → 0.334 | 0.231 → 0.231 |
| 11451300 | NF Cache C near Clearlake Oaks (In | 30 | 20 | single | 0.523 → 0.523 | 0.388 → 0.388 |
| 11451800 | Cache C at Rumsey | 74 | 30 | hinged at     3.3 m3/s | 0.427 → 0.371 | 0.298 → 0.228 |
| 11452500 | Cache C at Yolo | 62 | 15 | hinged at     4.6 m3/s | 0.270 → 0.290 | 0.237 → 0.261 |
| 11453000 | Yolo Bypass near Woodland | 80 | 15 | hinged at   331.3 m3/s | 0.527 → 0.525 | 0.637 → 0.613 |

- single: pooled log RMSE (velocity and width together) 0.2732 over 1040 values
- fitted: pooled log RMSE (velocity and width together) 0.2604 over 1040 values

Of the 30 gauges, 14 are hinged. Among them, the held-out velocity error is lower at 9 and the width error at 7. Pooled over every gauge, the error falls from 0.273 to 0.260. It is clearly better where a break has a physical reason:
- Freeport, where the tide holds the area nearly constant at low flow.
- Verona, where the river spreads at high flow.
- Cottonwood Creek, the Yuba near Marysville, the San Joaquin above the Merced and Cache Creek at Rumsey.

It is worse at La Grange, Cache Creek at Yolo and Cow Creek. Adopted model-wide, as an improvement overall, not tuned per gauge.
