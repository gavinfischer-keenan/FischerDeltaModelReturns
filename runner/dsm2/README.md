# Building DSM2 on Linux

DWR's DSM2 runs in the model runner as a separate, unmodified GPL program (specification section 9.4). DWR publishes ready-made DSM2 for Windows only. This folder builds it natively on Debian 13, with no Windows and no emulation.

## The missing library

DSM2's Linux build needs HEClib v6, the library it uses to read and write DSS files. DWR's build guide says HEClib v6 is not public. However, DWR's public DSM2 8.1.2 source package (on the DSM2 page at water.ca.gov) includes HEClib v6's source, in its Windows edition.

That edition compiles on Linux once two kinds of Windows-only code are replaced:

- **The C file layer** (`heclib_c/*.c`): open, read, write, seek, flush, file size and record locks, written against the Windows API. `heclib-linux/posix_io.c` provides the same functions using standard POSIX calls.
- **Seven Fortran routines** that use Windows-only modules (DFPORT, DFLIB, MSFLIB) or Windows paths: rename, delete, wait, command-line arguments, keyboard polling, scratch-file names and catalogue sorting. `heclib-linux/linux_helpers.f90` replaces them using standard Fortran.

Nothing in DSM2 itself is changed.

One trap: HEC tags each Fortran line with a platform code in columns 73–80, the old card sequence field. These files must be compiled as standard 72-column fixed form, or the compiler reads the tags as code.

## Running it

In the model runner, as the `fdmr` user:

```sh
./build_dsm2.sh                 # every stage, in order
./build_dsm2.sh heclib dsm2     # chosen stages only
```

Stages: `sources`, `heclib`, `klu`, `loki`, `hdf5`, `boost`, `dsm2`. Each stage skips itself when its output exists; delete that output to rebuild it. Everything goes under `/srv/fdmr-data/dsm2`, or `$FDMR_DSM2_ROOT` if set.

Requirements: Intel oneAPI 2023.2.4 (ifx, icx, icpx), CMake, Java 21, bison, flex, Python 3, git and curl.
