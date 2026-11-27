# Run times

What takes the time on the runner (CT 705), measured on 27 November 2026, to choose what to speed up first. The ranking below is from before any change; the results of the first changes follow it. Each cycle now records, for every step before publish, its time and peak memory (`cycle_steps` in each cycle's index.json, written by `fdmr_runner.stepstat`). Wall-clock times come from the cycles' systemd journal; an experiment's phases come from its own record (`runs[].phases` in `data/sf-bay-delta/experiments/<name>.json`).

## Ranked by time spent per day

| Rank | What | One run | Runs per day | Per day | Kind |
|---|---|---|---|---|---|
| 1 | Bay-Delta fetch in the hourly cycle: about 250 requests, one after another, 1–2 s each | 6.5–7 min | 24 | about 160 min | waiting on the network |
| 2 | Publish in the hourly cycle | about 2 min | 24 | about 48 min | compute |
| 3 | Rhine-Meuse hourly cycle, whole | about 70 s | 24 | about 28 min | mostly fetch |
| 4 | Delta cycle's DSM2 runs: HYDRO 112 s, QUAL 89 s (EC 34, sources 23, temperature 32), GTM sediment 197 s | about 6.6 min | 4 | about 27 min | compute |
| 5 | Fetch in the Delta cycle | about 6 min | 4 | about 24 min | waiting on the network |
| 6 | Bay (NOAA) step in the Delta cycle, including a 30 s gather | about 3 min | 4 | about 12 min | compute and fetch |
| 7 | Publish in the Delta cycle and the nightly cycle | about 2 min each | 5 | about 10 min | compute |
| 8 | Nightly fetch: 303 requests, one after another | 7.75 min | 1 | 7.75 min | waiting on the network |
| 9 | Nightly Delta hindcast | 416 s | 1 | about 7 min | compute |

**Whole cycles:**
- **Delta cycle:** about 19.5 min of wall-clock time for 563 s of CPU, with a memory peak of 6.0 GB of the container's 8 GB.
- **Nightly cycle:** about 19 min of wall-clock time for 578 s of CPU, with a memory peak of 3.9 GB.

## After the first changes (27 Nov 2026, 23:20 UTC hourly cycle)

The same hourly cycle, measured the same way, before (22:20) and after (23:20):

| | Before | After | Change |
|---|---|---|---|
| Whole hourly cycle, three regions | 10 min 16 s | 4 min 33 s | #34, #35 |
| CPU | 133 s | 42 s | |
| Memory peak | 2.64 GB | 1.66 GB | |
| Bay-Delta fetch | about 6.6 min | 153 s (257 fetches, no failures) | #34: hosts side by side, the pause per host; USGS's series lists kept a day |
| Bay-Delta publish | about 2 min | 31 s | #35 |
| Rhine-Meuse fetch | about 70 s | 68 s | one host (Rijkswaterstaat), so unchanged |

**Fetch (#34).** Each request is planned onto its host's queue, and the queues run side by side. USGS is now the longest queue, with 76 requests and a 1 s pause between them. The pause between requests to the same host is unchanged, so no host is asked faster than before.

**Publish (#35).** Profiled whole, under cProfile, a Bay-Delta publish took 169 s. Of that, 117 s was in the map's overlays.
- **The satellite temperature comparison, 61 s.** Each pixel was projected to UTM and its window checked in Python, 671,000 times.
- **The water-extent summary, 22 s,** rebuilt every hour from tiles that change every few days.
- **The level checks, 15 s,** which read a reach's record once per station.
- **The landmarks' nearest elements, 11 s.**
- **The 15 river reaches, 48 s.** Their dispersion and lateral mixing were worked out one step at a time.

Each was changed to give the same output: old and new wrote the same files byte for byte, on the live cycle at a fixed time.
- **The overlays** went from 95 s to 34 s, and to 12 s while the water-extent tiles are unchanged.
- **A whole publish** went from 35 s to 29.5 s, without the profiler.

## Issues

Each optimisation has its own issue:

| Issue | What it covers |
|---|---|
| #34 | Fetch: pause per host, and fetch hosts side by side |
| #35 | Publish: profile it, and skip reaches that have not changed |
| #36 | Memory: the Delta cycle's 6.0 GB peak |
| #37 | DSM2: run the QUAL and GTM runs side by side |
| #38 | GTM sediment |
| #39 | The experiments' hold on the cycle lock |
| #40 | The Bay step and the nightly hindcast |

## What this says

- **Fetching is the largest cost.** Its requests run one after another, and in the hourly cycle that is about 160 minutes a day of waiting. Running requests side by side, within each source's published rate limits, is the largest single saving, and it changes no maths.
- **Publishing costs about an hour a day.** It runs in every cycle, so it is the second target.
- **GTM sediment, at 197 s, is the slowest single computation.** It takes longer than HYDRO in the Delta cycle.
- **Memory.** The Delta cycle peaks at 6.0 GB of the container's 8 GB, and `/tmp` is in RAM as well. Speeding things up must not raise that peak, for example by running DSM2 modules side by side.

## Experiments

Each scenario is one HYDRO and one QUAL run over the experiment's period:

| Experiment | Period | HYDRO | QUAL | Build, gate override, read output | Waiting for the cycle lock |
|---|---|---|---|---|---|
| West False River barrier, 2015 | 18 months | 425–431 s | 139–140 s | under 5 s | 0 and 607 s |
| Suisun Marsh gates, 2023 | 12 months | 318–322 s | 92 s | under 3 s | 1,207 and 0 s |

- **HYDRO takes about 0.8–0.9 s per simulated day** and QUAL about 0.25 s per day.
- **The lock wait explains the earlier mystery.** Writing the gate override takes under a second. The 10–20 extra minutes seen in some runs were spent waiting for the cycle lock, while a Delta cycle ran.
- **The cost of holding the lock.** An experiment holds the lock for 7–10 minutes per scenario, so an hourly cycle that falls due waits behind it.
