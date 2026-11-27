# Experiments (issue #33)

Past interventions in the Delta, run as they were and with the intervention taken away, then scored against what was measured. They are defined in `regions/sf-bay-delta/experiments.toml`, run by `fdmr_runner.experiment`, and shown on `experiments.html`.

To reproduce:

1. Download the EDI packages as zip files: edi.1162.2 (the West False River barrier) and edi.876.9 (the Suisun Marsh gates).
2. Rebuild the measured daily means. This writes `regions/sf-bay-delta/experiments/<name>/measured.csv` and `schism.csv`:

   ```sh
   python tools/validation/experiments/prepare_edi.py DOWNLOADS regions/sf-bay-delta/experiments
   ```

3. Run on the runner. Each scenario is one HYDRO and QUAL run over the experiment's period, about 18 months, run while holding the cycle's lock:

   ```sh
   cd python && python -m fdmr_runner.experiment ../regions/sf-bay-delta /srv/fdmr-data/scratch/experiments \
       /srv/fdmr-data/publish/sf-bay-delta/experiments --lock /srv/fdmr-data/.cycle.lock
   ```

Re-scoring without running DSM2 again takes `--rescore`. A new experiment is a new `[[experiment]]` block in the TOML file, with no new code, as long as its intervention is a gate setting in DWR's gate file.
