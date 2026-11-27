# The bloom forecast's rules against the 2014–2019 surveys (27 Nov 2026)

**Data.** Kurobe, Lehman, Lesmeister and Teh (2022), EDI edi.1076.1 (ref:kurobe2022). The table's MD5 matches EDI's record.

**Samples.** 424 samples, collected from July to November on 103 days, with water temperature (10.9–27.3 °C) and salinity (0.02–11.2) measured at each.

**Method.** Each sample takes the forecast's class from its own measured temperature and salinity (`regions/sf-bay-delta/blooms.toml`). The boundaries (19 and 25 °C, 5 psu) come from the literature, not from these data, so this is an out-of-sample test. Intervals are 95%, from 2000 bootstrap resamples of sampling days. Run `check_1076.py`.

| Class | n | Cells detected (qPCR) | Median cells/mL | Median biovolume (µm³/L) | Microcystin detected |
|---|---|---|---|---|---|
| Unlikely | 137 | 0.82 [0.72, 0.91] | 244 | 6.5×10⁶ | 0.12 [0.06, 0.18] |
| Possible | 255 | 0.84 [0.76, 0.91] | 4,035 | 2.3×10⁸ | 0.45 [0.34, 0.54] |
| Likely | 32 | 0.84 [0.64, 0.97] | 74,700 | 1.2×10⁹ | 0.56 [0.28, 0.78] |

**What the classes do and don't separate:**
- **Abundance:** they rank it about 300-fold in cells and 200-fold in biovolume.
- **Toxin:** detection rises with the class.
- **Presence:** they do not separate it. qPCR finds some cells in more than 80% of samples whatever the class. So the forecast says where the water favours a bloom, not where cells are present.

**The salinity mask, in warm water (≥ 19 °C):**
- At or above 5 psu (12 samples): median cells 782/mL, microcystin detected in 25%.
- Below 5 psu (287 samples): median cells 4,310/mL, microcystin detected in 46%.

Fewer cells at higher salinity, but on few samples.

# The rules against the surveys' visual index, 2007–2021 (27 Nov 2026)

**Data.** Bashevkin et al. (2023), EDI edi.539.4, `environment.csv` (ref:bashevkin2023; MD5 checked).

**Samples.** 7,523 samples rated for *Microcystis* (1 absent to 5 very high), each with surface temperature and salinity. They come from five surveys (DOP, FMWT, FRP, STN, YBFMP), on 1,051 days. Run `check_539.py`. Intervals are 95%, from 2000 bootstrap resamples of sampling days.

| Class | n | Present (2+) | Medium or more (3+) | High or more (4+) |
|---|---|---|---|---|
| Unlikely | 3,588 | 0.196 [0.173, 0.219] | 0.049 [0.037, 0.061] | 0.015 [0.010, 0.021] |
| Possible | 3,716 | 0.416 [0.380, 0.452] | 0.171 [0.147, 0.197] | 0.066 [0.052, 0.081] |
| Likely | 219 | 0.662 [0.578, 0.741] | 0.402 [0.301, 0.495] | 0.196 [0.119, 0.280] |

**Classes.** Each step up multiplies the chance of a medium or worse bloom by about 3 and 2, with no overlap between the intervals.

**The salinity mask, in warm water (≥ 19 °C):**
- Below 5 psu (3,935 samples): present 0.430, medium or more 0.184.
- At or above 5 psu (1,265 samples): present 0.334, medium or more 0.096.

So the mask halves blooms but does not remove them. Blooms are carried seaward from fresher water.
