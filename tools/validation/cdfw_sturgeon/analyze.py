"""Test the FDMR fishing-forecast salinity / temperature rules against CDFW trammel-net sets.

Usage:  python -I analyze.py <sets.csv from build_sets.py> <out_dir> [n_boot]

CPUE = white sturgeon per 100 net-fathom-hours (CDFW unit). Two estimators per band:
  ratio  = sum(catch) / sum(effort)          (primary; weights sets by effort)
  mean   = mean of per-set catch/effort       (CDFW's 'average CPUE per net set')
95 % CIs: percentile bootstrap resampling SAMPLING DAYS (release_date) within the band,
because sets on one day share conditions and fish (n_boot reps, seed 20261126).
Model: negative-binomial regression, catch ~ cr(salinity, df=4) + temp + C(year),
offset log(effort); the salinity of peak predicted CPUE is found on a grid and its CI by
re-fitting on day-resampled data.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

SEED = 20261126
SAL_EDGES = [-np.inf, 0.5, 10, 16, 25, np.inf]
SAL_LABELS = ["<0.5", "0.5-10", "10-16", "16-25", ">=25"]
RULE_SCORE = {"<0.5": "0.4 (0.7 Nov-May)", "0.5-10": "1.0", "10-16": "0.7", "16-25": "0.4", ">=25": "0.2"}
TEMP_EDGES = [-np.inf, 9, 24.0000001, np.inf]   # 9-24 inclusive is 'in range'
TEMP_LABELS = ["<9", "9-24", ">24"]


def stats(df, y, rng, n_boot):
    """Return dict of sets, days, fish, ratio CPUE, mean CPUE, share>0, with day-bootstrap CIs."""
    days = df["release_date"].values
    ud = np.unique(days)
    grp = {d: np.where(days == d)[0] for d in ud}
    c, e = df[y].values, df["effort_100nfh"].values
    r = c / e
    out = dict(sets=len(df), days=len(ud), fish=float(c.sum()),
               cpue_ratio=c.sum() / e.sum() if len(df) else np.nan,
               cpue_mean=r.mean() if len(df) else np.nan,
               share_pos=(c > 0).mean() if len(df) else np.nan)
    if len(ud) >= 2:
        bs = np.empty((n_boot, 3))
        for b in range(n_boot):
            idx = np.concatenate([grp[d] for d in rng.choice(ud, len(ud), replace=True)])
            bs[b] = (c[idx].sum() / e[idx].sum(), r[idx].mean(), (c[idx] > 0).mean())
        lo, hi = np.percentile(bs, [2.5, 97.5], axis=0)
        out.update(ratio_lo=lo[0], ratio_hi=hi[0], mean_lo=lo[1], mean_hi=hi[1], pos_lo=lo[2], pos_hi=hi[2])
    return out


def table(df, by, labels, y, rng, n_boot):
    rows = []
    for lab in labels:
        s = df[df[by] == lab]
        rows.append(dict(band=lab, **stats(s, y, rng, n_boot)) if len(s) else dict(band=lab, sets=0))
    return pd.DataFrame(rows)


def fmt(t):
    def ci(r, a, lo, hi, nd=2):
        if pd.isna(r.get(a)):
            return "-"
        s = f"{r[a]:.{nd}f}"
        if lo in r and not pd.isna(r[lo]):
            s += f" [{r[lo]:.{nd}f}, {r[hi]:.{nd}f}]"
        return s
    lines = ["| band | sets | days | fish | CPUE ratio [95% CI] | mean per-set CPUE [95% CI] | share of sets with catch [95% CI] |",
             "|---|---|---|---|---|---|---|"]
    for _, r in t.iterrows():
        r = r.to_dict()
        if isinstance(r["band"], float) and float(r["band"]).is_integer():
            r["band"] = int(r["band"])
        lines.append(f"| {r['band']} | {int(r.get('sets', 0))} | {int(r['days']) if 'days' in r and not pd.isna(r['days']) else 0} | "
                     f"{r.get('fish', 0) if not pd.isna(r.get('fish', np.nan)) else 0:.0f} | {ci(r,'cpue_ratio','ratio_lo','ratio_hi')} | "
                     f"{ci(r,'cpue_mean','mean_lo','mean_hi')} | {ci(r,'share_pos','pos_lo','pos_hi',3)} |")
    return "\n".join(lines)


def nb_peak(df, n_boot_peak, rng):
    import statsmodels.formula.api as smf
    import warnings
    warnings.filterwarnings("ignore")
    d = df.dropna(subset=["salinity", "temp_c"]).copy()
    d["year"] = d["release_year"].astype(int)
    d["logE"] = np.log(d["effort_100nfh"])
    lo_s, hi_s = np.percentile(d["salinity"], [1, 99])
    grid = np.linspace(lo_s, hi_s, 200)
    multi_year = d["year"].nunique() > 1
    form = "wst ~ cr(salinity, df=4, constraints='center') + temp_c" + (" + C(year)" if multi_year else "")

    def fit(dd):
        m = smf.negativebinomial(form, data=dd, offset=dd["logE"]).fit(disp=0, maxiter=200)
        nd = pd.DataFrame({"salinity": grid, "temp_c": dd["temp_c"].median(),
                           "year": dd["year"].mode()[0], "logE": 0.0})
        # predictions must reuse the spline basis from the training data -> use model's design info
        pred = m.predict(nd, offset=np.zeros(len(nd)))
        return m, np.asarray(pred)

    m, pred = fit(d)
    peak = grid[np.argmax(pred)]
    # linear-only comparison model (is there curvature at all?)
    form_lin = "wst ~ salinity + temp_c" + (" + C(year)" if multi_year else "")
    mlin = smf.negativebinomial(form_lin, data=d, offset=d["logE"]).fit(disp=0, maxiter=200)
    ud = d["release_date"].unique()
    grp = {k: v.index for k, v in d.groupby("release_date")}
    peaks = []
    for _ in range(n_boot_peak):
        dd = d.loc[np.concatenate([grp[k] for k in rng.choice(ud, len(ud), replace=True)])].reset_index(drop=True)
        try:
            peaks.append(grid[np.argmax(fit(dd)[1])])
        except Exception:
            pass
    curve = pd.DataFrame({"salinity": grid, "pred_cpue_at_median_temp": pred})
    return dict(model=m, mlin=mlin, peak=peak, peak_ci=np.percentile(peaks, [2.5, 97.5]) if peaks else (np.nan, np.nan),
                n_peak_boot=len(peaks), curve=curve, n=len(d), range=(lo_s, hi_s), form=form)


def main(sets_csv, out_dir, n_boot=2000):
    rng = np.random.default_rng(SEED)
    o = Path(out_dir); o.mkdir(parents=True, exist_ok=True)
    s = pd.read_csv(sets_csv)
    s = s[s["effort_100nfh"] > 0].copy()
    s["sal_band"] = pd.cut(s["salinity"], SAL_EDGES, right=False, labels=SAL_LABELS).astype(object)
    s["temp_band"] = pd.cut(s["temp_c"], TEMP_EDGES, right=False, labels=TEMP_LABELS).astype(object)
    R = [f"# Results (sets file: {sets_csv})", "",
         f"Trammel-net sets 2005-2022 with effort: {len(s)}; years {s.release_year.min()}-{s.release_year.max()}",
         f"Sets per year: {s.release_year.value_counts().sort_index().to_dict()}",
         f"Sets per month: {s.month.value_counts().sort_index().to_dict()}",
         f"Sets per tagging_location: {s.tagging_location.value_counts().to_dict()}",
         f"Sets with salinity: {int(s.salinity.notna().sum())} (years {sorted(s[s.salinity.notna()].release_year.unique().tolist())}); "
         f"salinity range {s.salinity.min():.2f}-{s.salinity.max():.2f}",
         f"Sets with temperature: {int(s.temp_c.notna().sum())}; temp range {s.temp_c.min():.1f}-{s.temp_c.max():.1f} degC",
         f"Total white sturgeon {s.wst.sum():.0f} (sublegal {s.wst_sublegal.sum():.0f}, slot {s.wst_slot.sum():.0f}, "
         f"overslot {s.wst_overslot.sum():.0f}, no FL {s.wst_size_unknown.sum():.0f}); green sturgeon {s.gst.sum():.0f}",
         f"Bootstrap: {n_boot} reps, resampling sampling days, seed {SEED}", ""]

    sal = s.dropna(subset=["salinity"])
    for y, lab in [("wst", "all white sturgeon"), ("wst_slot", "slot-size (101.6-152.4 cm FL) white sturgeon"),
                   ("wst_sublegal", "sub-slot (<101.6 cm FL) white sturgeon")]:
        t = table(sal, "sal_band", SAL_LABELS, y, rng, n_boot)
        t.insert(1, "rule_score", t["band"].map(RULE_SCORE))
        t.to_csv(o / f"salinity_bands_{y}.csv", index=False)
        R += [f"## Salinity bands - {lab}", fmt(t), ""]
    # finer binned curve, 2-psu bins
    fine_edges = np.arange(0, np.ceil(sal.salinity.max() / 2) * 2 + 2, 2) if len(sal) else np.array([0, 2])
    sal = sal.assign(fine=pd.cut(sal.salinity, fine_edges, right=False).astype(str))
    labs = [str(x) for x in pd.cut(pd.Series(fine_edges[:-1] + 1e-9), fine_edges, right=False).cat.categories]
    tf = table(sal, "fine", labs, "wst", rng, n_boot)
    tf.to_csv(o / "salinity_2psu_bins_wst.csv", index=False)
    R += ["## White sturgeon CPUE in 2-psu bins (binned curve)", fmt(tf), ""]

    for y, lab in [("wst", "all white sturgeon, all years with temperature")]:
        t = table(s.dropna(subset=["temp_c"]), "temp_band", TEMP_LABELS, y, rng, n_boot)
        t.to_csv(o / "temp_bands_wst.csv", index=False)
        R += [f"## Temperature bands - {lab}", fmt(t), ""]
    tt = table(s.dropna(subset=["temp_c"]).assign(t2=lambda x: (np.floor(x.temp_c / 2) * 2).astype(int).astype(str)),
               "t2", [str(v) for v in sorted((np.floor(s.temp_c.dropna() / 2) * 2).astype(int).unique())], "wst", rng, n_boot)
    tt.to_csv(o / "temp_2C_bins_wst.csv", index=False)
    R += ["## White sturgeon CPUE in 2-degC bins (lower edge shown)", fmt(tt), ""]

    s["month"] = s["month"].astype("Int64"); s["release_year"] = s["release_year"].astype(int)
    tm = table(s, "month", sorted(s.month.dropna().unique().tolist()), "wst", rng, n_boot)
    tm.to_csv(o / "month_wst.csv", index=False)
    R += ["## White sturgeon by month (all years)", fmt(tm), ""]
    ty = table(s, "release_year", sorted(s.release_year.unique().tolist()), "wst", rng, n_boot)
    ty.to_csv(o / "year_wst.csv", index=False)
    R += ["## White sturgeon by year", fmt(ty), ""]
    tl = table(s, "tagging_location", s.tagging_location.value_counts().index.tolist(), "wst", rng, n_boot)
    tl.to_csv(o / "location_wst.csv", index=False)
    R += ["## White sturgeon by tagging_location (all years)", fmt(tl), ""]
    if len(sal):
        R += ["Salinity by tagging_location (sets with salinity): " +
              repr(sal.groupby("tagging_location").salinity.describe()[["count", "min", "50%", "max"]].round(2).to_dict("index")), ""]

    # green sturgeon 'avoid' flag ranges
    g = s.copy()
    g["sal_ge_8.8"] = np.where(g.salinity.isna(), np.nan, (g.salinity >= 8.8).astype(float))
    g["temp_11.9_21.9"] = np.where(g.temp_c.isna(), np.nan, g.temp_c.between(11.9, 21.9).astype(float))
    for col in ["sal_ge_8.8", "temp_11.9_21.9"]:
        gg = g.dropna(subset=[col]).assign(k=lambda x: np.where(x[col] == 1, "inside", "outside"))
        t = table(gg, "k", ["inside", "outside"], "gst", rng, n_boot)
        t.to_csv(o / f"green_{col}.csv", index=False)
        R += [f"## Green sturgeon vs {col} (inside = condition true)", fmt(t), ""]
    gb = g.dropna(subset=["sal_ge_8.8", "temp_11.9_21.9"])
    if len(gb):
        gb = gb.assign(k=lambda x: np.where((x["sal_ge_8.8"] == 1) & (x["temp_11.9_21.9"] == 1), "both", "not both"))
        t = table(gb, "k", ["both", "not both"], "gst", rng, n_boot)
        t.to_csv(o / "green_both.csv", index=False)
        R += ["## Green sturgeon vs both conditions", fmt(t), ""]

    # model
    if sal.dropna(subset=["temp_c"]).shape[0] >= 30:
        res = nb_peak(sal, 200, rng)
        m = res["model"]
        res["curve"].to_csv(o / "nb_salinity_curve.csv", index=False)
        R += ["## Negative-binomial model of white sturgeon catch",
              f"Formula: {res['form']}, offset log(effort in 100 net-fathom-h); n = {res['n']} sets",
              f"Salinity grid limited to 1st-99th percentile of data: {res['range'][0]:.2f}-{res['range'][1]:.2f}",
              f"AIC spline model {m.aic:.1f} vs linear-salinity model {res['mlin'].aic:.1f}",
              f"Linear model salinity coef {res['mlin'].params['salinity']:.4f} per psu "
              f"(95% CI {res['mlin'].conf_int().loc['salinity', 0]:.4f}, {res['mlin'].conf_int().loc['salinity', 1]:.4f}), "
              f"temp coef {res['mlin'].params['temp_c']:.4f} per degC "
              f"(95% CI {res['mlin'].conf_int().loc['temp_c', 0]:.4f}, {res['mlin'].conf_int().loc['temp_c', 1]:.4f})",
              f"Peak predicted CPUE at salinity {res['peak']:.2f} psu (day-bootstrap 95% CI {res['peak_ci'][0]:.2f}-"
              f"{res['peak_ci'][1]:.2f}, {res['n_peak_boot']} successful refits)",
              "Spline model params:", m.summary().tables[1].as_text(), ""]
    (o / "results.md").write_text("\n".join(R), encoding="utf-8")
    print("\n".join(R))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 2000)
