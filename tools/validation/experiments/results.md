# Experiments: results (27 Nov 2026)

Part of the model's validation and calibration (issue #33). Each experiment runs DWR's historical inputs twice: once as it was, and once with the intervention taken away. The intervention is taken away by changing gate settings in the run's own copy of DWR's gate file; nothing else changes. Both runs are scored against measured daily means while the intervention was in place.

- **What runs:** DSM2 v8.5.1 (HYDRO, QUAL) on DWR's input set 2026-04-16. The as-built runs have code fingerprint `682f3c4c660d`; the corrected "without" runs `55acbf9295ca`. The two differ only in how a gate override is written (the correction below), not in any maths.
- **Where the numbers are:** `experiments.html`, served from `data/sf-bay-delta/experiments/`, lists every run with when it ran and its fingerprints.
- **Units:** salinity as EC in µS/cm; net flow tidally filtered, in m³/s.
- **Columns:** "Diff." is model less measured; "abs." is the mean absolute difference of daily means. The **effect** is the run without the intervention less the run as it was, as on the page: how much saltier (+) the water would have been without it.

**A correction made before these results.** The first "without" runs (06:48 and 07:24 UTC) were wrong, and these results replace them:
- Writing an override into a copy of DWR's gate file garbled the record, because DWR's records are stored in a form `dsstool` misreads when writing over them. The barrier stayed in place from July to September in the "never built" run.
- The two runs then came out identical over those months, which is how it was caught.
- **The fix.** `dsstool delete` removes the run's blocks first; the series is then written afresh, read back and compared, and the run stops if they differ.
- **The live cycle was never affected.** It writes its gates into fresh files, and its current gate series read back clean.

## West False River emergency drought barrier, 2021

**What was done.** In the 2021 drought DWR closed West False River with a rock barrier, in from 1 June 2021 and closed from 25 June. The aim was to keep salt water from being drawn into Franks Tract and the central Delta.

**Data.** Measured salinity and flow come from edi.1162.2 (ref:hartman2024edb). Its Bay-Delta SCHISM runs, with and without the barrier, are compared beside ours.

**Judged** 1 June to 31 December 2021:

| Station | Measured | As built (diff., abs.) | Never built (diff., abs.) | Our effect | SCHISM's effect | SCHISM abs. with / without |
|---|---|---|---|---|---|---|
| Bethel Island (BET) | 864 | 708 (−156, 163) | 1,195 (+331, 412) | +487 | +287 | 61 / 347 |
| Old R. at Bacon Island (BAC) | 613 | 580 (−34, 60) | 1,049 (+436, 439) | +452 | +72 | 106 / 167 |
| Franks Tract (FRK) | 738 | 533 (−205, 205) | 1,000 (+262, 333) | +445 | | |
| Holland Cut (HOL) | 679 | 568 (−111, 115) | 1,047 (+368, 389) | +473 | | |
| Fisherman's Cut (FCT) | 928 | 867 (−61, 154) | 1,403 (+475, 520) | +489 | | |
| False River (FAL) | 1,137 | 1,245 (+108, 276) | 1,634 (+496, 555) | +389 | | |
| Old R. at San Joaquin (OSJ) | 614 | 554 (−60, 77) | 792 (+179, 211) | +216 | | |
| Middle River (MDM) | 528 | 466 (−62, 63) | 627 (+100, 103) | +162 | | |
| Threemile Slough (TWI) | 1,279 | 1,290 (+12, 233) | 1,321 (+42, 263) | +33 | | |
| Dutch Slough (DSJ) | 1,611 | 1,708 (+97, 326) | 1,730 (+118, 305) | +21 | | |

Net flow, measured against the model as built and as never built:

| Station | Measured | As built (diff., abs.) | Never built (diff., abs.) |
|---|---|---|---|
| Fisherman's Cut | −10.2 | −8.7 (4.5) | +5.8 (17.2) |
| Old R. at San Joaquin | −16.1 | −65.5 (49.5) | −91.6 (75.6) |
| Holland Cut | +4.4 | −17.0 (21.4) | −7.4 (12.1) |
| Dutch Slough | −2.4 | +0.9 (4.6) | −1.1 (3.8) |

False River's flow gauge has only four days in the window, all before the closure, so that row is not scored. In the model, False River's net flow falls from 53 m³/s to 2.8 m³/s once the barrier closes.

**Reading:**
- **Which run is closer.** The barrier as built is closer to the measurements in 12 of 15 station series: 9 of 10 for salinity, 3 of 5 for flow.
- **Direction and size.** The model gets the barrier's effect right in direction and of the right size at the central Delta stations: a fall of 390–490 µS/cm at Bethel Island, Franks Tract, Holland Cut, Fisherman's Cut and False River.
- **Larger than SCHISM's.** At Bethel Island our effect is +487 against SCHISM's +287. At Bacon Island it is +452 against +72.
- **Too fresh with the barrier.** Our as-built run reads 110–205 µS/cm below measured at Bethel Island, Franks Tract and Holland Cut. SCHISM with the barrier is within 61 at Bethel Island. So the model likely overstates the protection the barrier gave, by up to about 200 µS/cm there.
- **Old River at the San Joaquin.** The model carries too much water up Old River, −66 against −16 measured, as built and as never built alike: a standing misfit in the channel network, not of the barrier.

## Suisun Marsh Salinity Control Gates, summer 2018

**What was done.**
- For the first time in summer, the Montezuma Slough gates were run with the tide to draw fresher water into Suisun Marsh for Delta smelt.
- Flashboards went in on 27 July, the gates were operated from 2 August to 7 September, and usual operations resumed on 17 October.
- Without the action, the gates stand open with the flashboards out, as in summer 2017.

**Data.** Measured salinity comes from edi.876.9 (ref:hartman2026smscg). No other model's runs are published with it.

**Judged** 27 July to 16 October 2018:

| Station | Measured | As built (diff., abs.) | No action (diff., abs.) | Our effect |
|---|---|---|---|---|
| Montezuma Sl. at the gates (MSL) | 6,113 | 5,956 (−157, 386) | 6,757 (+644, 873) | +801 |
| Montezuma Sl. at National Steel (NSL) | 7,300 | 6,836 (−464, 644) | 8,647 (+1,347, 1,565) | +1,811 |
| Montezuma Sl. at Beldens Landing (BDL) | 8,327 | 8,418 (+91, 724) | 12,672 (+4,344, 4,344) | +3,788 |
| Montezuma Sl. at Hunter Cut (HUN) | 13,063 | 12,973 (−90, 496) | 15,055 (+1,993, 2,265) | +1,885 |
| Suisun Sl. at Volanti (VOL) | 10,752 | 8,403 (−2,349, 2,349) | 11,259 (+507, 1,641) | +2,856 |
| Suisun Sl. at Godfather (GOD) | 15,147 | 12,933 (−2,214, 2,214) | 14,700 (−447, 777) | +1,768 |
| Grizzly Bay (GZL) | 16,349 | 15,859 (−490, 680) | 16,059 (−291, 514) | +199 |
| Honker Bay (HON) | 12,118 | 11,485 (−633, 806) | 11,238 (−880, 1,006) | −242 |
| Ryer Island (RYC) | 15,014 | 13,724 (−1,291, 1,309) | 13,660 (−1,354, 1,369) | −64 |
| Sacramento R. at Mallard Island (MAL) | 9,519 | 9,682 (+163, 436) | 9,383 (−136, 573) | −299 |
| Sacramento R. at Collinsville (CSE) | 5,275 | 5,767 (+493, 543) | 5,517 (+242, 436) | −251 |
| Sacramento R. near Sherman Island (SSI) | 1,286 | 1,965 (+679, 679) | 1,889 (+603, 603) | −76 |
| Sacramento R. at Rio Vista (RVB) | 163 | 197 (+34, 34) | 195 (+32, 32) | −2 |

**Reading:**
- **Montezuma Slough.** The model sees the action clearly along the slough itself. The as-built run is within 90–460 µS/cm of measured on average at all four stations. Without the action they would have been 0.8–3.8 mS/cm saltier, and that run misses by 0.6–4.3 mS/cm.
- **Western marsh.** In Suisun Slough the as-built run is about 2.3 mS/cm too fresh, and the no-action run is closer. The model carries the freshening too far west, or DWR's island water use there is off.
- **River and bays.** Salinity rises about 250–300 µS/cm at Mallard Island and Collinsville, as fresh water is drawn into the marsh. That is too small an effect to judge against the bays' and river's own misfits.
- **Overall.** As built is closer at 7 of 13 stations: all four on Montezuma Slough, and three of the bay and river stations.

## What this says about the model

**What these experiments test:**
- The Delta's flow and salinity as the map shows them: DWR's DSM2 on its own inputs, run by the model runner.
- They do not test the spreading maths written here. Measured dye clouds test that (`tools/validation/rhine_dye`).

**Standing misfits:**
- Old River at the San Joaquin's net flow, and Franks Tract to Bethel Island reading fresh with the barrier in.
- The western marsh reading fresh with the gates operating.
- Both are recorded here, not tuned away: DWR's own inputs and grid are used unchanged.
