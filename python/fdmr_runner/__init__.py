"""The model runner: scheduling, fetching and publishing (specification sections 4 and 9.3).
It reads region packages and drives adapters and model-kind plug-ins through their interfaces."""

import os
from pathlib import Path

# Where the runner keeps its data, runs and installed tools: FDMR_DATA, as
# runner/schedule/cycle.sh sets it, else the runner's own (runner/README.md).
DATA = Path(os.environ.get("FDMR_DATA", "/srv/fdmr-data"))
