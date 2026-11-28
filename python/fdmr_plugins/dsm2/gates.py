"""A gate's position, open or shut, inferred from the measured flow through
it (#72): the rule both the Delta model's live gates (fdmr_runner.delta) and
the map's gate markers (fdmr_runner.overlays) use. The rule's two settings
are the region's, with the checks behind them (dsm2.toml [[live_gate]]
open_above, max_gap_h); there is no default."""

from __future__ import annotations


def gate_states(readings: list[tuple[int, float]], open_above: float, max_gap_s: int) -> list[tuple[int, bool]]:
    """Open or closed from measured flow through a gate, as change points
    (time, open) from the first reading.

    An open tidal gate carries flow both ways and passes near zero at every
    slack tide, so one low reading does not mean shut; a shut gate still leaks a
    little. So: the gate is open from the first to the last of a run of
    readings reaching `open_above` either way, each within `max_gap_s` of the
    one before, and shut from the reading after. A run needs two such readings,
    so a lone spike is noise."""
    if not readings:
        return []
    big = [t for t, q in readings if abs(q) >= open_above]
    runs: list[list[int]] = []
    for t in big:
        if runs and t - runs[-1][-1] <= max_gap_s:
            runs[-1].append(t)
        else:
            runs.append([t])
    runs = [r for r in runs if len(r) >= 2]
    times = [t for t, _ in readings]
    out: list[tuple[int, bool]] = []
    for r in runs:
        if not out and r[0] > times[0]:
            out.append((times[0], False))
        out.append((r[0], True))
        after = next((t for t in times if t > r[-1]), None)
        if after is not None:
            out.append((after, False))
    return out or [(times[0], False)]


def states_by_rule(readings: list[tuple[int, float]], rule: dict) -> list[tuple[int, bool]]:
    """gate_states with a gate's own settings (dsm2.toml [[live_gate]]: `open_above` m3/s, `max_gap_h`)."""
    return gate_states(readings, rule["open_above"], int(rule["max_gap_h"] * 3600))
