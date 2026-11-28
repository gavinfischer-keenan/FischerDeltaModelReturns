# Experiments: results (27 Nov 2026)

Part of the model's validation and calibration (issue #33). Each experiment runs DWR's historical inputs twice: once as it was, and once with the intervention taken away. The intervention is taken away by changing gate settings in the run's own copy of DWR's gate file; nothing else changes. Both runs are scored against measured daily means while the intervention was in place.

- **What runs:** DSM2 v8.5.1 (HYDRO, QUAL) on DWR's input set 2026-04-16. The as-built runs have code fingerprint `682f3c4c660d`; the corrected "without" runs `55acbf9295ca`. The two differ only in how a gate override is written (the correction below), not in any maths. The 2015 barrier and 2023 gate runs have `cc3bb5e8aee1`, which adds only timing of each run's phases. Both barrier experiments were run again on 28 Nov (2021 `b61961a26ca3`, 2015 `4eeb19e0e546`) when Franks Tract's station came to be compared with the tract's own water (below); nothing else in them changed.
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
| Franks Tract (FRK) | 738 | 615 (−123, 137) | 1,193 (+455, 500) | +549 | | |
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
| Old R. at Quimby Island | −5.3 | −34.6 (29.3) | −36.1 (30.8) |
| Holland Cut | +4.4 | −17.0 (21.4) | −7.4 (12.1) |
| Dutch Slough | −2.4 | +0.9 (4.6) | −1.1 (3.8) |

False River's flow gauge has only four days in the window, all before the closure, so that row is not scored. In the model, False River's net flow falls from 53 m³/s to 2.8 m³/s once the barrier closes.

**Reading:**
- **Which run is closer.** The barrier as built is closer to the measurements in 12 of 15 station series: 9 of 10 for salinity, 3 of 5 for flow.
- **Direction and size.** The model gets the barrier's effect right in direction and of the right size at the central Delta stations: a fall of 390–490 µS/cm at Bethel Island, Franks Tract, Holland Cut, Fisherman's Cut and False River.
- **Larger than SCHISM's.** At Bethel Island our effect is +487 against SCHISM's +287. At Bacon Island it is +452 against +72.
- **Too fresh with the barrier.** Our as-built run reads 110–156 µS/cm below measured at Bethel Island, Franks Tract and Holland Cut. SCHISM with the barrier is within 61 at Bethel Island. So the model likely overstates the protection the barrier gave, by up to about 200 µS/cm there.
- **Old River at the San Joaquin.** The model carries too much water up Old River, −66 against −16 measured, as built and as never built alike: a standing misfit in the channel network, not of the barrier.

## West False River emergency drought barrier, 2015

**What was done.** In the 2015 drought DWR first closed West False River with a rock barrier, and took it out that autumn. DWR's input set has it in from 14 May to 1 November 2015. It is the same barrier as in 2021, so this is the same test in a second year.

**Data.**
- Salinity comes from DWR's continuous stations, edi.2385.1 (ref:frantzich2026ncro), and from the CEMP stations at Franks Tract and Threemile Slough, edi.1177.9 (ref:nelson2026cemp).
- Net flow comes from edi.1162.2 (ref:hartman2024edb).
- No other model's runs for 2015 are published with them.

**Judged** 14 May to 31 October 2015:

| Station | Measured | As built (diff., abs.) | Never built (diff., abs.) | Our effect |
|---|---|---|---|---|
| Bethel Island (BET) | 992 | 711 (−281, 281) | 1,189 (+197, 262) | +478 |
| Old R. at Bacon Island (BAC) | 747 | 573 (−175, 175) | 1,019 (+272, 297) | +455 |
| Franks Tract (FRK) | 861 | 578 (−282, 282) | 1,182 (+322, 341) | +541 |
| Holland Cut (HOL) | 804 | 563 (−241, 241) | 1,034 (+230, 270) | +474 |
| Fisherman's Cut (FCT) | 992 | 809 (−183, 189) | 1,305 (+313, 314) | +496 |
| False River (FAL) | 1,331 | 1,244 (−87, 199) | 1,612 (+281, 398) | +368 |
| Old R. at San Joaquin (OSJ) | 662 | 513 (−149, 149) | 742 (+79, 117) | +224 |
| Threemile Slough (TWI) | 1,408 | 1,245 (−163, 174) | 1,301 (−107, 129) | +39 |
| San Joaquin R. at Blind Point (BLP) | 3,062 | 2,988 (−74, 422) | 2,750 (−312, 392) | −239 |

Net flow:

| Station | Measured | As built (diff., abs.) | Never built (diff., abs.) |
|---|---|---|---|
| Fisherman's Cut | −3.4 | −8.3 (5.1) | +5.0 (8.7) |
| Old R. at San Joaquin | −13.9 | −60.7 (47.0) | −83.6 (69.7) |
| Old R. at Quimby Island | −15.4 | −31.1 (15.9) | −32.0 (16.7) |
| Holland Cut | +4.2 | −12.2 (16.4) | −3.7 (8.2) |
| Dutch Slough | −2.6 | +0.2 (3.8) | −1.7 (3.1) |

False River's flow gauge reads only at the two ends of the window: 14–21 May, as the barrier went in, and 11–31 October. Those days read −23 to −175 m³/s in October, which neither run reproduces (as built +32 on average). As in 2021, that row is not scored.

**Reading:**
- **Which run is closer.** As built is closer in 7 of 13 scored series: 5 of 9 for salinity, 2 of 4 for flow. That is fewer than in 2021, and for a reason the next point gives.
- **Effect.** The model's effect is almost the same as in 2021: a fall of 450–500 µS/cm at Bethel Island, Bacon Island, Franks Tract, Holland Cut and Fisherman's Cut.
- **Too fresh with the barrier, again and more so.** The as-built run reads 175–282 µS/cm below measured at those stations, against 110–156 in 2021.
  - At Bethel Island, January to April 2015 (no barrier) the model is 20–140 µS/cm fresh. From June to October (barrier in) it is 210–445 fresh. In November and December (barrier out) it is 100–170 fresh.
  - So most of the shortfall, about 200 µS/cm at Bethel Island, comes with the barrier. The never-built run, saltier than measured by about 200, is the closer of the two at Bethel Island.
  - In 2021 SCHISM's effect at Bethel Island was 287 against our 487. Both years say the same: the model overstates the barrier's protection, by about 200 µS/cm at Bethel Island.
- **Old River at the San Joaquin.** Too much water goes up Old River again, −61 against −14 measured: the same standing misfit as in 2021.
- **Blind Point.** In the model the lower San Joaquin at Blind Point is about 240 µS/cm saltier with the barrier than without. That is too small to judge against the station's day-to-day misfit (abs. about 400).

**Franks Tract, corrected 28 Nov 2026 (#42).** The measured Franks Tract station (CEMP's FRK) stands in the tract's open water, mid-tract (38.0464 N, 121.5981 W). It was first compared with DWR's own FRK output, which is channel 121: Old River along the tract's east edge, 1.5 km away. DSM2 models the tract itself as the reservoir `franks_tract`, so the station is now compared with the reservoir's water. The as-built shortfall there fell from 205 to 123 µS/cm in 2021 and from 361 to 282 in 2015. So a fifth to two fifths of that station's misfit (22% in 2015, 40% in 2021) was where it was compared, not the model. Bethel Island and Holland Cut are channel stations and are unchanged: the model still reads fresh there with the barrier in.

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

## Suisun Marsh Salinity Control Gates, summer 2023

**What was done.**
- 2023 was a wet year. The State Water Project's incidental take permit required 100 thousand acre-feet of water to be used to freshen the marsh for Delta smelt.
- DWR put the Montezuma Slough flashboards in on 14 August and ran two of the three gates with the tide from 15 August until the water was used, on 18 October.
- The gates were then left open until they were closed for repairs on 6 November.
- Without the action, the gates stand open with the flashboards out, as they had from 11 June.

**Data.** Measured salinity comes from edi.876.9 (ref:hartman2026smscg), as for 2018. Hunter Cut has only 32 days in the window and Volanti 65; the others have 82–84.

**Judged** 14 August to 5 November 2023:

| Station | Measured | As built (diff., abs.) | No action (diff., abs.) | Our effect |
|---|---|---|---|---|
| Montezuma Sl. at the gates (MSL) | 2,462 | 2,834 (+371, 525) | 3,394 (+932, 953) | +561 |
| Montezuma Sl. at National Steel (NSL) | 2,746 | 3,169 (+423, 593) | 4,484 (+1,738, 1,738) | +1,315 |
| Montezuma Sl. at Beldens Landing (BDL) | 3,401 | 3,854 (+453, 699) | 7,948 (+4,547, 4,547) | +4,019 |
| Montezuma Sl. at Hunter Cut (HUN) | 8,162 | 8,064 (−98, 379) | 10,701 (+2,539, 2,539) | +2,294 |
| Suisun Sl. at Volanti (VOL) | 5,834 | 3,959 (−1,876, 1,876) | 6,643 (+809, 1,856) | +2,753 |
| Suisun Sl. at Godfather (GOD) | 10,326 | 7,809 (−2,517, 2,517) | 9,922 (−404, 903) | +2,113 |
| Grizzly Bay (GZL) | 11,166 | 11,008 (−158, 539) | 11,247 (+80, 569) | +238 |
| Honker Bay (HON) | 7,324 | 7,378 (+54, 860) | 7,052 (−272, 828) | −326 |
| Ryer Island (RYC) | 10,286 | 9,250 (−1,036, 1,066) | 9,148 (−1,138, 1,159) | −103 |
| Sacramento R. at Mallard Island (MAL) | 5,411 | 6,048 (+637, 866) | 5,675 (+264, 688) | −373 |
| Sacramento R. at Collinsville (CSE) | 2,475 | 3,178 (+703, 773) | 2,907 (+432, 630) | −271 |
| Sacramento R. near Sherman Island (SSI) | 503 | 931 (+429, 429) | 869 (+366, 366) | −62 |
| Sacramento R. at Rio Vista (RVB) | 139 | 135 (−4, 7) | 135 (−4, 7) | −1 |

**Reading:**
- **Montezuma Slough.** As in 2018, the model sees the action clearly along the slough. Without it the four stations would have been 0.6–4.0 mS/cm saltier, and that run misses by 0.9–4.5 mS/cm. The as-built run is within 100–450 µS/cm of measured on average, this time on the salty side at the three upper stations.
- **Western marsh, in a second year.** Suisun Slough again reads too fresh as built, by 1.9–2.5 mS/cm (2.2–2.3 in 2018), and the no-action run is again closer. So this misfit is not a feature of one year. It is now seen in a wet year as well as in a below-normal one.
- **River and bays.** The model's Sacramento River below Rio Vista reads 430–700 µS/cm salty in both runs. The action's effect there (−270 to −370) is smaller than that misfit, so the no-action run comes out closer only because it is fresher.
- **Overall.** As built is closer at 7 of 13 stations: all four on Montezuma Slough, Grizzly Bay, Ryer Island and Rio Vista. That is the same count as in 2018.

## The circulation round Franks Tract (28 Nov 2026, #41, #42)

**The flow gauges as a cross-section.** These are the central Delta's flow gauges: Old River at the San Joaquin (OSJ), Old River at Quimby Island (ORQ), Holland Cut, Fisherman's Cut, False River and Dutch Slough.
- **The data.** ORQ is read from HYDRO's tidefile (channel 119), since DWR's output files name no station there.
- **The signs.** Each gauge's and channel's sign was checked: both are positive seaward.
- **Monthly means, 2021, measured / model as built, m³/s:**

| 2021 | OSJ | ORQ | Holland Cut | Fisherman's Cut | False River |
|---|---|---|---|---|---|
| April | +31 / −73 | +27 / −24 | +9 / +4 | +11 / +5 | −47 / +56 |
| May | +32 / −75 | +20 / −28 | +2 / 0 | +14 / +4 | −56 / +50 |

**What it shows.** Whenever the False River gap is open, the model runs the net circulation round Franks Tract the wrong way:
- **Measured.** About 50 m³/s comes in from the San Joaquin through False River and goes back out north along Old River.
- **Model.** The water goes south up Old River and out through False River.
- **Both years.** 2015 shows the same: April–May and October–December, False River −43 to −63 measured against +21 to +58 in the model.

This net flow is driven by the tides, through the tract's openings, with no river or pump behind it: tidal pumping.

**Why the model cannot make it.** DWR's Franks Tract is one fully mixed reservoir, and every one of its seven connections has the same coefficient for flow in as for flow out (`reservoir_std_delta_grid.inp`). With the flood and the ebb passing each opening alike, it has no way to make this circulation.

**What follows from it:**
- **Old River.** This is the likely cause of Old River's too-southward net flow (#41).
- **The barrier.** It is the likely cause of the barrier looking too protective at Bethel Island and Holland Cut (#42). In the real Delta False River brings San Joaquin water into the tract, and the barrier stops that; in the model the gap already runs the other way.

**What we do about it.** We set no asymmetry at the tract's openings ourselves. That would be tuning one place until the gauges agree. It stays a recorded misfit, with its cause known, unless a published source for the openings' flood and ebb behaviour turns up. DWR's own historical runs should be checked for the same reversal next.

## What this says about the model

**What these experiments test:**
- The Delta's flow and salinity as the map shows them: DWR's DSM2 on its own inputs, run by the model runner.
- They do not test the spreading maths written here. Measured dye clouds test that (`tools/validation/rhine_dye`).

**Standing misfits.** Each is now seen in two years, so none is a feature of one year:
- Old River at the San Joaquin's net flow: −61 to −66 m³/s modelled against −14 to −16 measured, in 2015 and 2021, with or without the barrier (#41). Its cause is in the next section.
- Franks Tract to Bethel Island reading fresh with the barrier in, in 2015 and 2021. The model overstates the barrier's protection by about 200 µS/cm at Bethel Island (#42).
- The western marsh (Suisun Slough) reading 1.9–2.5 mS/cm fresh with the gates operating, in 2018 and 2023 (#43).
- The Sacramento River below Rio Vista reading 400–700 µS/cm salty, and Ryer Island about 1 mS/cm fresh, in 2018 and 2023 (#44, #45).
- In October 2015 False River's flow gauge reads large flows while DWR's inputs have the barrier closed. This is not yet explained (#46).
- All are recorded here, not tuned away: DWR's own inputs and grid are used unchanged.
