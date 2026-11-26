"""The join between a channel network and a mesh (fdmr_runner.handoff)."""

import pytest

from fdmr_runner.handoff import network_to_mesh


def square_mesh():
    """Water from -122.00 to -121.99 east and 38.00 to 38.01 north: two triangles."""
    lon = [-122.00, -121.99, -121.99, -122.00]
    lat = [38.00, 38.00, 38.01, 38.01]
    return {"lon": lon, "lat": lat, "x": [v * 1e5 for v in lon], "y": [v * 1e5 for v in lat], "triangles": [[0, 1, 2], [0, 2, 3]]}


def test_channels_are_owned_cut_where_they_enter_or_left_alone():
    nodes = [
        {"id": 1, "lon": -121.998, "lat": 38.005},  # in the mesh's water
        {"id": 2, "lon": -121.995, "lat": 38.005},  # in it
        {"id": 3, "lon": -121.985, "lat": 38.005},  # east of it
        {"id": 4, "lon": -121.980, "lat": 38.005},  # further east
    ]
    elements = [
        {"id": 10, "up": 1, "down": 2, "start_m": 0, "end_m": 260},  # wholly in the mesh
        {"id": 11, "up": 3, "down": 2, "start_m": 0, "end_m": 880},  # enters it, flowing west
        {"id": 12, "up": 4, "down": 3, "start_m": 0, "end_m": 440},  # wholly outside
    ]
    at = {n["id"]: [n["lon"], n["lat"]] for n in nodes}
    lines = [[at[e["up"]], at[e["down"]]] for e in elements]
    jn = network_to_mesh("delta", {"nodes": nodes, "elements": elements, "lines": lines}, "bay", square_mesh())
    assert jn["owned"] == [0]
    # Channel 11 runs 0.010 degrees from -121.985 to -121.995; the mesh begins at -121.990: halfway.
    [cut] = jn["cuts"]
    assert cut["element"] == 1 and cut["inside"] == "down" and cut["at"] == pytest.approx(0.5, abs=0.03)
    # Its place on the mesh is just inside it.
    assert cut["lon"] < -121.99 and cut["x"] < -121.99e5
    # The cut channel meets the owned one at node 2, inside the mesh: no junction is needed there.
    assert jn["junctions"] == []
