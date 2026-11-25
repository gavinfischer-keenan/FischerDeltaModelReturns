# Fischer Delta Model Returns

Where water goes from Shasta Dam to the Golden Gate: now, over the last 14 days and over the next 14, shown as moving particles on a map. A free, non-commercial visualization, to live at `tripofisch.com/sturgeon/`.

- [Specification](docs/specification.md), version 1.0
- [Build plan](docs/build-plan.md)
- [Published flow-field format](docs/format.md)

## Layout

The code is built from separate modules with explicit seams (specification section 4.3). `seams.toml` lists what each module may depend on, and `tools/check_seams.py` enforces it.

| Folder | Module | Language |
| --- | --- | --- |
| `crates/fdmr-math` | Math library: pure calculation, nothing about any place | Rust |
| `crates/fdmr-format` | Published flow-field format: reader and writer | Rust |
| `python/` | Model runner, model-kind plug-ins, data adapters, local data store | Python |
| `ui/` | The UI: display only | TypeScript |
| `regions/` | Region packages: data only | TOML |
| `text/` | On-screen words: data only | |
| `runner/dsm2` | Builds DWR's DSM2 natively on Linux | Shell |

Folders appear as each module is built.

## Checks

```sh
python tools/check_seams.py
cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test --release
```

CI runs the same checks on every push.
