"""Data adapters (specification sections 4.1 and 6). Each fetches one public
source into the local data store, in SI units: series into the store's rows,
and the satellites' grids, too large for rows, into a tile cache beside it
(fdmr_store.tiles), in the format each such adapter states. Adapters only
fetch and keep; working out what the kept data says is the runner's.
Adapters depend only on the store and fdmr_geo."""
