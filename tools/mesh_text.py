"""Write a mesh reach's layout as plain text, for the compute engine's
command-line check (crates/fdmr-compute/examples/bay_release.rs).

    python tools/mesh_text.py GEOMETRY_JSON OUT_TXT [LON LAT]

With LON LAT, also prints that point in the mesh's own projection (x y, m),
found in the triangle that holds it. Standard library only.
"""

import json
import sys

g = json.load(open(sys.argv[1], encoding="utf-8"))
out = [f"nodes {len(g['x'])}"]
out += [f"{x} {y} {d}" for x, y, d in zip(g["x"], g["y"], g["depth_m"])]
out.append(f"triangles {len(g['triangles'])}")
out += [" ".join(map(str, t)) for t in g["triangles"]]
out.append(f"open {len(g['edges'].get('open', []))}")
out += [f"{a} {b}" for a, b in g["edges"].get("open", [])]
hand = [(a, b, k) for k, (name, es) in enumerate(e for e in g["edges"].items() if e[0] != "open") for a, b in es]
out.append(f"handoff {len(hand)}")
out += [f"{a} {b} {k}" for a, b, k in hand]
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(out) + "\n")
if len(sys.argv) > 4:
    lon, lat = float(sys.argv[3]), float(sys.argv[4])
    for a, b, c in g["triangles"]:
        xa, ya, xb, yb, xc, yc = g["lon"][a], g["lat"][a], g["lon"][b], g["lat"][b], g["lon"][c], g["lat"][c]
        d = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
        w0 = ((yb - yc) * (lon - xc) + (xc - xb) * (lat - yc)) / d
        w1 = ((yc - ya) * (lon - xc) + (xa - xc) * (lat - yc)) / d
        w2 = 1 - w0 - w1
        if min(w0, w1, w2) >= 0:
            x = w0 * g["x"][a] + w1 * g["x"][b] + w2 * g["x"][c]
            y = w0 * g["y"][a] + w1 * g["y"][b] + w2 * g["y"][c]
            print(f"{x:.1f} {y:.1f}")
            break
    else:
        print("not on the mesh")
