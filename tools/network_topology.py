"""Write a network reach's layout as plain text, for the compute engine's
command-line check (crates/fdmr-compute/examples/delta_release.rs).

    python tools/network_topology.py GEOMETRY_JSON OUT_TXT [JUNCTION ...]

For each JUNCTION id given, also prints the index of a channel that starts
there, to release into. Standard library only.
"""

import json
import sys

g = json.load(open(sys.argv[1], encoding="utf-8"))
ix = {n["id"]: i for i, n in enumerate(g["nodes"])}
pools = {p["id"]: i for i, p in enumerate(g.get("pools", []))}
out = [f"channels {len(g['elements'])}"]
out += [f"{ix[e['up']]} {ix[e['down']]} {e['end_m']}" for e in g["elements"]]
out.append(f"nodes {len(g['nodes'])}")
out.append("sea " + " ".join(str(i) for i, n in enumerate(g["nodes"]) if n.get("kind") == "stage_boundary"))
out.append(f"pools {len(pools)}")
out.append(f"links {len(g.get('links', []))}")
out += [f"{pools[lk['pool']]} {ix[lk['node']]}" for lk in g.get("links", [])]
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(out) + "\n")
for j in sys.argv[3:]:
    k = int(j)
    chans = [i for i, e in enumerate(g["elements"]) if e["up"] == k] or [i for i, e in enumerate(g["elements"]) if e["down"] == k]
    print(f"junction {k}: channel index {chans[0] if chans else None} (DSM2 channel {g['elements'][chans[0]]['id'] if chans else None})")
