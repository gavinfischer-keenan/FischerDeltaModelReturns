# Hinged channel shapes: the evidence

`fdmr_plugins.river_routing.channel.fit` gives a gauge's cross-section area and top width against flow as a power law. Where the field measurements show two regimes, such as a channel spilling onto its floodplain or a tidal reach, it uses a hinged power law instead. The hinged fit is kept only when it beats the single law by more than 10 in the Bayesian information criterion (Kass and Raftery 1995, ref:kassraftery1995). The choice is empirical (FDMR). `holdout.py` judges it on measurements it was not fitted to, and `results.md` holds the outcome.
