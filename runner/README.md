# The model runner: setting it up and updating it

The model runner is one Debian 13 container (on this project, CT 705
`fdmr-runner`) that fetches the data, runs the models and publishes each
cycle, and serves the viewer. Everything it needs is in this repository; this
page is the order to do it in. Commands run as the `fdmr` user unless marked
**root**. Its data lives under `/srv/fdmr-data` (`FDMR_DATA`), its code in
`~fdmr/fdmr` (a checkout of this repository), its Python in `~fdmr/venv`.

## Once: a new runner

1. **root**: the system packages: Python 3.13 with venv, a C toolchain, Node
   20 or later with npm, and curl.

   ```sh
   apt-get install -y python3 python3-venv python3-dev build-essential nodejs npm curl git
   adduser --disabled-password --gecos "" fdmr
   mkdir -p /srv/fdmr-data && chown fdmr:fdmr /srv/fdmr-data
   ```

2. Rust and wasm-pack, for the engine and its WebAssembly build:

   ```sh
   curl https://sh.rustup.rs -sSf | sh -s -- -y
   . ~/.cargo/env && rustup target add wasm32-unknown-unknown && cargo install wasm-pack --locked
   ```

3. The code and its Python environment. `python/` is installed with its test
   extras (pyproject.toml); `crates/fdmr-py`, the engine's Python binding, is
   built by maturin through pip:

   ```sh
   git clone https://github.com/gavinfischer-keenan/FischerDeltaModelReturns.git ~/fdmr
   python3 -m venv ~/venv
   ~/venv/bin/pip install './fdmr/python[test]' ./fdmr/crates/fdmr-py
   ```

4. DSM2 (the Delta's model), built from DWR's public sources:
   `runner/dsm2/build_dsm2.sh` (see `runner/dsm2/README.md`). It needs Intel
   oneAPI 2023.2 and takes about an hour.

5. **root**: the schedule (systemd timers for the hourly, Delta and nightly
   cycles) and the viewer's web server (Caddy on port 8091):

   ```sh
   sh ~fdmr/fdmr/runner/schedule/install.sh
   sh ~fdmr/fdmr/runner/web/install.sh
   ```

6. The viewer, built and put live: `sh ~/fdmr/runner/update.sh`.

7. The public route: the site's edge proxy (on this project CT 703, shared
   with other sites) sends `/sturgeon/<region>/` to the runner's port 8091.
   Its Sturgeon lines are copied in `runner/web/edge-route.caddy`, for
   reference only: the edge's own file is the one in force.

## Every update

Change the code only between cycles, holding the cycle lock, so a running
cycle never imports a mix of old and new: `flock /srv/fdmr-data/.cycle.lock
git pull` in `~/fdmr`. Then `sh ~/fdmr/runner/update.sh`, which takes the same
lock to replace the Python binding. It rebuilds the Python binding, then the engine's WebAssembly, the viewer, and
puts the viewer live (`runner/web/deploy_app.sh`). The cycles pick up the new
Python code on their next run. After a change to the schedule or the web
server, run their install script again as root.

Credentials (never in this repository): NASA Earthdata's token in
`~/.config/fdmr/earthdata-token`, OpenET's key and USGS's key where their
adapters say.
