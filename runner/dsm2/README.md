# Building DSM2 on Linux

DWR's DSM2 runs in the model runner as a separate, unmodified GPL program (specification section 9.4). DWR publishes ready-made DSM2 for Windows only. This folder builds it natively on Debian 13, with no Windows and no emulation.

## The missing library

DSM2's Linux build needs HEClib v6, the library it uses to read and write DSS files. DWR's build guide says HEClib v6 is not public. However, DWR's public DSM2 8.1.2 source package (on the DSM2 page at water.ca.gov) includes HEClib v6's source, in its Windows edition.

That edition compiles on Linux once two kinds of Windows-only code are replaced:

- **The C file layer** (`heclib_c/*.c`): open, read, write, seek, flush, file size and record locks, written against the Windows API. `heclib-linux/posix_io.c` provides the same functions using standard POSIX calls.
- **Seven Fortran routines** that use Windows-only modules (DFPORT, DFLIB, MSFLIB) or Windows paths: rename, delete, wait, command-line arguments, keyboard polling, scratch-file names and catalogue sorting. `heclib-linux/linux_helpers.f90` replaces them using standard Fortran.

Two more pieces of the Windows edition are handled differently:

- `heclib/intel_extra.for` only maps Fortran names onto Windows calling conventions (`_openf_@20` and the like). It is left out on Linux.
- The in-memory buffer helpers (`readmem`, `writemem`, `reallocatemem`) store a memory address in a 4-byte INTEGER, which cannot hold a 64-bit address. `posix_io.c` keeps the real addresses in a table and gives the Fortran code a small handle instead.

Nothing in DSM2 itself is changed. Only HYDRO and QUAL are built, the two programs the app uses.

## Compiler workaround

ifx 2023.2.4 crashes, with an internal compiler error, on `dsm2/src/gtm/gtm_subs.f90` at every optimisation level. HYDRO depends on that file, so it cannot be skipped. The classic ifort compiler, in the same oneAPI release, compiles it cleanly against the modules ifx has already built. The script does this automatically when the crash appears.

## First results, 25 November 2026

On the model runner's 8 efficiency cores, with DWR's DSM2 input release of 16 April 2026:

- **HYDRO:** 1 to 15 September 2014 (14 days) in **13 seconds**. Normal end; DWR's DSS inputs read and DSS output written through the ported HEClib. Tide file: 551 channels at 5-minute steps, no missing values, flows and water levels in physical ranges.
- **QUAL**, salinity (EC), same period: **4 seconds**, normal end. One harmless HDF5 message at shutdown: QUAL tries to flush an HDF5 output file this study never opens, after all output is written.

DWR's public input release also includes georeferenced shapefiles of the DSM2 grid (channel centrelines, nodes, reservoirs, gates, stations). The Delta map layer can come straight from those.

One trap: HEC tags each Fortran line with a platform code in columns 73–80, the old card sequence field. These files must be compiled as standard 72-column fixed form, or the compiler reads the tags as code.

## Running it

In the model runner, as the `fdmr` user:

```sh
./build_dsm2.sh                 # every stage, in order
./build_dsm2.sh heclib dsm2     # chosen stages only
```

Stages: `sources`, `heclib`, `klu`, `loki`, `hdf5`, `boost`, `dsm2`. Each stage skips itself when its output exists; delete that output to rebuild it. Everything goes under `/srv/fdmr-data/dsm2`, or `$FDMR_DSM2_ROOT` if set.

## Requirements

Debian packages, from Intel's oneAPI apt repository and Debian's own:

```sh
apt-get install intel-oneapi-compiler-fortran-2023.2.4 \
  intel-oneapi-compiler-dpcpp-cpp-and-cpp-classic-2023.2.4 intel-oneapi-mkl-devel-2023.2.0 \
  cmake make build-essential openjdk-21-jdk-headless bison flex git curl unzip zlib1g-dev \
  python3-dev python3-numpy python-is-python3
```

Notes from the first build:

- **The `latest` compiler link.** Installing MKL also installs Intel's older 2023.2.0 compiler runtime, and moves oneAPI's `latest` compiler link onto it, which has no compilers. The script loads the exact compiler and MKL versions, so it is not affected; interactive shells may be.
- **Requirements DWR's build guide leaves out:** MKL, Boost's `system` library, Python's development headers and NumPy, and a `python` command (Debian ships only `python3`, hence `python-is-python3`).
- **CMake 3.27 and later** ignore the upper-case `BOOST_ROOT` in DSM2's `linux_options.cmake`. The script passes every library's location explicitly.
