# Satellite turbidity against the stations (issue #16), 27 Nov 2026

**Why.** The satellite was to be scored against the turbidity stations first, as every satellite source is (#9), and only used to check the model's mud if it passed.

**Data:**
- **Satellite.** NASA's HLS v2.0 surface reflectance (ref:hls2), Landsat 8/9 and Sentinel-2 at 30 m: every pass from 1 July to 27 November 2026 with at most 40% cloud over the Delta's two tiles (T10SFH, T10SEH), 115 passes.
- **Turbidity from reflectance.** Dogliotti et al. 2015 (ref:dogliotti2015), with the coefficients ACOLITE uses.
- **Stations.** The 20 CDEC turbidity stations on those tiles. A reading counts when it falls within an hour of the pass.
- **Run:** `hls_check.py`. Matchups: `matchups.csv`, kept on the runner.

**What counts.** A station counts on a pass only where all 3×3 pixels around it are water, with no cloud, shadow, cirrus or cloud nearby (Fmask). In the Delta's narrow channels that leaves five stations:
- Antioch (ANH), Mallard Island (MAL) and Hunter Cut (HUN), with 30–63 matchups each;
- False River (FAL) and Old River at Highway 4 (OH4), one each.

## Results: 126 matchups on 65 passes

| | Matchups | Satellite ÷ station (median) | Within a factor of 2 | Spearman |
|---|---|---|---|---|
| All | 126 | 1.81 | 54% | 0.40 |
| Sun 40° or more from overhead | 40 | 1.10 | **100%** | 0.86 |
| Sun less than 40° from overhead | 86 | 2.91 | 33% | 0.33 |
| (by season) September–November | 53 | 1.18 | 94% | 0.84 |
| (by season) July–August | 73 | 4.21 | 25% | 0.33 |

**By the sun's height** (solar zenith at the Delta at the pass, by NOAA's solar position equations):

| Solar zenith | Matchups | Satellite ÷ station | Within ×2 |
|---|---|---|---|
| 15–30° | 50 | 10.9 | 22% |
| 30–40° | 36 | 2.0 | 47% |
| 40–50° | 32 | 1.16 | 100% |
| 50–70° | 8 | 0.88 | 100% |

**Reading.** The error follows the sun's height, and Landsat and Sentinel-2 alike: the signature of sun glint off the water.
- Both satellites look nearly straight down, so a high sun puts its mirror image in view.
- HLS's atmospheric correction is made for land and does not remove glint.
- In summer the near-infrared, which the turbidity algorithm reads in murky water, is 60% of the red at these stations. In autumn it is 39%.

With the sun low, the satellite agrees with the stations: 10% high, every one of 40 matchups within a factor of two, rank correlation 0.86.

## Verdict: not used yet

The 40° cut was found in these data, so it is not yet a test. The candidate `hls-turbidity-glint-screened` (the register's candidates, `docs/maths.md`) needs two things:
- **a glint screen fixed before testing:** for example the glint angle from HLS's own sun and view angle bands, with a published threshold, or a published glint correction;
- **a held-out season:** passes from December onward were not used here.

Only then is it used to check the model's mud (`ssc`), and only where a channel is wide enough for clean water pixels. At 30 m that is the Bay, Suisun Bay, the lower Sacramento and San Joaquin, and the flooded islands.
