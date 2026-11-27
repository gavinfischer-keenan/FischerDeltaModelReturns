# Checking the sturgeon forecast's rules against CDFW's trammel-net sets (#25)

Data: Stompe, D.K. and J.A. Hobbs. 2024. California Department of Fish and
Wildlife Adult Sturgeon Study, Sacramento-San Joaquin Watershed, 1954-2022
ver 2. Environmental Data Initiative.
https://doi.org/10.6073/pasta/fdd183bb7ddec813400a924ae2d4f0ad (CC BY 4.0;
ref:stompe2024). Accessed 27 Nov 2026.

The data are not copied here. To reproduce `results.md` (Python 3.14 with
numpy 2.5, pandas 3.1, scipy 1.18, statsmodels 0.15, patsy 1.0, gsw 3.6):

    python -I fetch_edi.py data         # checks each table's SHA-1 against DataONE
    python -I build_sets.py data out    # one row per trammel-net set, with effort
    python -I analyze.py out/sets.csv out 2000

EDI's portal sits behind a human check, so `fetch_edi.py` may ask you to
download the tables by hand into `data/`; it then verifies them.

Catch per unit effort is fish per 100 net-fathom-hours (CDFW's own effort
definition, in `build_sets.py`). Confidence intervals come from a bootstrap
over sampling days (2000 resamples, fixed seed), because sets on the same day
are not independent.
