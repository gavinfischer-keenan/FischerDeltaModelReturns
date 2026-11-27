# Measured dye clouds on the Waal and the Nederrijn-Lek (issue #27)

**Source.** Van Mazijk, A. (1996), *One-dimensional approach of transport phenomena of dissolved matter in rivers*, PhD thesis, TU Delft (Communications on Hydraulic and Geotechnical Engineering 96-3; repository.tudelft.nl, uuid:789593e2-fd72-4800-9abe-3163d09e2179). The data are its Figs 4.4.20 and 4.4.21 (pp. 224–225): rhodamine WT from the Rhine Alarm Model tracer release of June 1991, measured at Bimmen (Rhine km 865.02, the left bank), Vuren (Waal km 951.8) and Hagestein (Lek km 946.5).

**Digitized** from the thesis scan with `digitize.py`:
- **Axes:** from the frame and the gridlines.
- **Bimmen's squares:** by a morphological opening.
- **The + marks:** where a vertical and a horizontal stroke cross.
- **Clean-up:** points on the wrong curve dropped by time (Bimmen before day 8.4; downstream after day 6.7).
- **Checked against the thesis's own Table 4.4.9** (∫φ dt, the mass flux per unit discharge):

| Station | Digitized | Thesis |
|---|---|---|
| Bimmen | 0.302 / 0.300 | 0.300 |
| Vuren | 0.201 | 0.197 |
| Hagestein | 0.204 | 0.207 |

The JSON files hold the points as (day after release, µg/l).

**Measured.**
- On the Waal the peak travels at the water's speed: 22.8 h, 1.06 m/s, against van Mazijk's 1.082.
- The centroid lags 9 hours behind (31.7 h, 0.76 m/s).
- The skew grows from +1.08 to +1.40.
- Change-of-moment K is 8,900 m²/s.

This is the signature of storage in the groyne fields (van Mazijk Table 4.4.10, dead-zone ratio β: 0.2756 on the Waal, 0.1715 on the Lek).

**Simulated** with `simulate.py`, the engine's physics (#29) plus transient storage:
- The measured Bimmen curve is sent downstream.
- A parcel in the flow enters storage at rate a and leaves at a/β, coming back anywhere across the flow.
- a = 1e-5 s⁻¹ is fitted on the Waal and judged on the Lek, which is held out.
- Hydraulics: the sourced channels (region.toml) at van Mazijk's flow velocities.

| Reach | Run | Centroid (d) | Spread (d) | Skew | Peak (d) | Shape RMSE (1/d) |
|---|---|---|---|---|---|---|
| Waal, fit | measured | 7.943 | 0.826 | +1.40 | 7.35 | |
| | no storage | 7.579 | 0.502 | +0.90 | 7.25 | 0.077 |
| | storage, a = 1e-5 | 7.902 | 0.682 | +0.96 | 7.53 | 0.051 |
| Lek, held out | measured | 8.218 | 0.706 | +1.18 | 7.73 | |
| | no storage | 7.974 | 0.513 | +0.85 | 7.73 | 0.056 |
| | storage, a = 1e-5 | 8.229 | 0.608 | +0.79 | 7.91 | 0.053 |

**Verdict (27 Nov 2026): not adopted yet.**
- On the held-out Lek, storage puts the centroid right (+0.01 d against −0.24 d) and the spread closer.
- But it delays the peak by 0.18 d, so the whole shape is only 5% better.

**Data caveats** (van Mazijk):
- Bimmen was sampled at the left bank, and the tracer was not fully mixed across the river there.
- A third of the mass is lost by Vuren (Table 4.4.9).

**Next tests:**
- The 1989 and 1990 releases as further held-out reaches (Figs 3.4.23, 4.4.2).
- Storage exchanging with the near-bank flow, which may bring the peak earlier.
