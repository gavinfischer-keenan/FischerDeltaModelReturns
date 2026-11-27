"""Build a per-net-set table from EDI edi.1479.2 (CDFW Adult Sturgeon Study).

Usage:  python -I build_sets.py <data_dir> <out_dir>

Definitions (sources in brackets):
- Effort, CDFW's own definition [2010 Field Season Summary, DuBois, Matt & Harris, CDFG,
  p.4]: unit = 100 net-fathom-hours; soak time (end of set -> start of retrieve) weighted
  100 %, deployment (set start -> set end) and retrieval (retrieve start -> retrieve end)
  weighted 50 %.  effort = net_length_fathoms/100 * hours.  If net_length_fathoms is
  missing, net_num_panels * 25 fathoms is used (EML: panels are 25 fathoms).
- Catch [same source]: all white sturgeon (new, recaptured, untagged) = sum of `count`.
- Size class: the 2013+ slot of 40-60 in fork length = 101.6-152.4 cm FL [IEP Sturgeon
  page, "2013: Regulation change from total length to fork length (now 40-60 inches)"].
  It is applied to every year as a fixed size class, NOT as the legal status at the time
  (the slot was 117-168 cm TL in 2010 per the 2010 summary). Fish without FL = 'size_unknown'.
- Salinity: `salinity_ppt` as reported (YSI meters, 2021-2022 only per the EML methods);
  where missing but `conductivity_uScm` is present, practical salinity is computed with
  PSS-78 via TEOS-10 gsw.SP_from_C at p = 0 dbar. Because the EML does not say whether
  conductivity_uScm is in-situ conductivity or specific conductance (25 degC), both are
  computed and the one that reproduces the reported salinity_ppt (median abs. diff.) is
  used; the choice is printed.
- Only gear_type == 'trammel_net' sets, 2005-2022.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

SLOT_FL_CM = (101.6, 152.4)


def hours(date, a, b):
    t0 = pd.to_datetime(date + " " + a, errors="coerce")
    t1 = pd.to_datetime(date + " " + b, errors="coerce")
    return (t1 - t0).dt.total_seconds() / 3600.0


def main(data_dir, out_dir):
    d, o = Path(data_dir), Path(out_dir); o.mkdir(parents=True, exist_ok=True)
    ns = pd.read_csv(d / "Netset_CDFW_Sturgeon_Study.csv", na_values=["NA"], dtype={"netset_ID": str},
                     keep_default_na=True, low_memory=False)
    ct = pd.read_csv(d / "Catch_CDFW_Sturgeon_Study1.csv", na_values=["NA"], dtype={"netset_ID": str},
                     keep_default_na=True, low_memory=False, encoding_errors="replace")
    log = []
    log.append(f"netset rows {len(ns)}, catch rows {len(ct)}")
    log.append("gear_type counts: " + ns["gear_type"].value_counts(dropna=False).to_dict().__repr__())
    ns = ns[(ns["gear_type"] == "trammel_net") & ns["release_year"].between(2005, 2022)].copy()
    log.append(f"trammel-net sets 2005-2022: {len(ns)}")

    date = ns["release_date"].astype(str)
    deploy = hours(date, ns["set_start_time"].astype(str), ns["set_end_time"].astype(str))
    soak = hours(date, ns["set_end_time"].astype(str), ns["retrieve_start_time"].astype(str))
    retr = hours(date, ns["retrieve_start_time"].astype(str), ns["retrieve_end_time"].astype(str))
    fath = ns["net_length_fathoms"].where(ns["net_length_fathoms"].notna(), ns["net_num_panels"] * 25)
    ns["soak_h"] = soak
    ns["effort_h"] = soak + 0.5 * deploy + 0.5 * retr
    ns["net_fathoms"] = fath
    ns["effort_100nfh"] = fath / 100.0 * ns["effort_h"]
    bad = ~(ns["effort_100nfh"] > 0) | (deploy < 0) | (soak < 0) | (retr < 0)
    log.append(f"sets dropped for missing/negative times or net length: {int(bad.sum())}")
    ns = ns[~bad].copy()

    # species
    log.append("catch CommonName values (top 40): " + repr(ct["CommonName"].value_counts().head(40).to_dict()))
    name = ct["CommonName"].astype(str).str.lower()
    ct["count"] = pd.to_numeric(ct["count"], errors="coerce").fillna(1)
    wst = ct[name.str.contains("white sturgeon")].copy()
    gst = ct[name.str.contains("green sturgeon")].copy()
    fl = pd.to_numeric(wst["fork_length_cm"], errors="coerce")
    wst["cls"] = np.where(fl.isna(), "size_unknown",
                          np.where(fl < SLOT_FL_CM[0], "sublegal", np.where(fl <= SLOT_FL_CM[1], "slot", "overslot")))
    agg = wst.groupby("netset_ID")["count"].sum().rename("wst")
    bycls = wst.pivot_table(index="netset_ID", columns="cls", values="count", aggfunc="sum")
    bycls.columns = ["wst_" + c for c in bycls.columns]
    g = gst.groupby("netset_ID")["count"].sum().rename("gst")
    unmatched = set(wst["netset_ID"].dropna()) - set(ns["netset_ID"])
    log.append(f"white sturgeon catch rows {len(wst)} (fish {wst['count'].sum():.0f}); "
               f"green sturgeon rows {len(gst)} (fish {gst['count'].sum():.0f}); "
               f"WST netset_IDs not in kept sets: {len(unmatched)}")
    ns = ns.join(agg, on="netset_ID").join(bycls, on="netset_ID").join(g, on="netset_ID")
    for c in ["wst", "gst", "wst_sublegal", "wst_slot", "wst_overslot", "wst_size_unknown"]:
        if c not in ns:
            ns[c] = 0.0
        ns[c] = ns[c].fillna(0.0)

    # salinity
    import gsw
    cond_mScm = pd.to_numeric(ns["conductivity_uScm"], errors="coerce") / 1000.0
    temp = pd.to_numeric(ns["water_temp_c"], errors="coerce")
    sp_insitu = pd.Series(gsw.SP_from_C(cond_mScm.values, temp.values, 0.0), index=ns.index)
    sp_spc25 = pd.Series(gsw.SP_from_C(cond_mScm.values, 25.0, 0.0), index=ns.index)
    rep = pd.to_numeric(ns["salinity_ppt"], errors="coerce")
    both = rep.notna() & cond_mScm.notna()
    choice = "in-situ conductivity (C at water_temp_c)"
    sp_from_c = sp_insitu
    if both.sum() >= 5:
        e1 = (sp_insitu[both] - rep[both]).abs().median()
        e2 = (sp_spc25[both] - rep[both]).abs().median()
        log.append(f"conductivity check on {int(both.sum())} sets with both: median |SP-reported| "
                   f"in-situ {e1:.3f}, as specific conductance {e2:.3f}")
        if e2 < e1:
            choice, sp_from_c = "specific conductance (C at 25 degC)", sp_spc25
    log.append(f"PSS-78 conductivity interpretation used: {choice}")
    ns["sal_source"] = np.where(rep.notna(), "reported", np.where(sp_from_c.notna(), "pss78", "none"))
    ns["salinity"] = rep.where(rep.notna(), sp_from_c)
    ns["temp_c"] = temp
    ns["month"] = pd.to_datetime(ns["release_date"], errors="coerce").dt.month
    for k in ("lat", "lon"):
        cols = [f"{k}_set_start", f"{k}_set_end", f"{k}_ret_start", f"{k}_ret_end"]
        ns[k] = ns[cols].apply(pd.to_numeric, errors="coerce").mean(axis=1)
    log.append("salinity source counts: " + repr(ns["sal_source"].value_counts().to_dict()))
    log.append("sets with salinity by year: " + repr(ns[ns["salinity"].notna()]["release_year"].value_counts().sort_index().to_dict()))
    log.append("sets with temperature by year: " + repr(ns[ns["temp_c"].notna()]["release_year"].value_counts().sort_index().to_dict()))
    keep = ["netset_ID", "release_year", "release_date", "month", "vessel", "net_set", "tagging_location", "tide",
            "lat", "lon", "net_fathoms", "soak_h", "effort_h", "effort_100nfh", "temp_c", "salinity", "sal_source",
            "secchi_cm", "DO_mgl", "wst", "wst_sublegal", "wst_slot", "wst_overslot", "wst_size_unknown", "gst"]
    ns[keep].to_csv(o / "sets.csv", index=False)
    (o / "build_log.txt").write_text("\n".join(log), encoding="utf-8")
    print("\n".join(log))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
