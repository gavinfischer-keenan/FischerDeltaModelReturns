#!/usr/bin/env bash
# Build DWR's DSM2 natively on Linux (Debian 13) with Intel oneAPI 2023.2.
#
# DSM2 is run as a separate, unmodified GPL program (specification section 9.4).
# This script only fetches public sources, builds DSM2's dependencies and runs
# DWR's own build. HEClib v6, which DWR does not publish on its own, comes from
# the HEClib source DWR includes in its public DSM2 8.1.2 source package.
#
# Usage: build_dsm2.sh [stage...]
#   stages: sources heclib klu loki hdf5 boost dsm2   (default: all, in order)
# Each stage skips itself when its output already exists. Delete the output to redo it.
set -euo pipefail

ROOT=${FDMR_DSM2_ROOT:-/srv/fdmr-data/dsm2}
SRC=$ROOT/sources
DEPS=$ROOT/build/deps          # becomes DSM2's deps/ folder
DSM2_TAG=v8.5.1
JOBS=${JOBS:-$(nproc)}
OVERLAY=${FDMR_HECLIB_OVERLAY:-$(cd "$(dirname "$0")" && pwd)/heclib-linux}
UA="fdmr-runner"

DSM2_812_SRC_URL="https://data.cnra.ca.gov/dataset/039928f9-5b4c-47e9-9953-69aa7bf1fd66/resource/dbbffee8-7aa2-4795-a659-d3f20e16052a/download/dsm2_v8_1_2_src_revised.zip"
BOOST_URL="https://archives.boost.io/release/1.83.0/source/boost_1_83_0.tar.gz"
HDF5_URL="https://github.com/HDFGroup/hdf5/releases/download/hdf5-1_14_2/hdf5-1_14_2.tar.gz"
SUITESPARSE_URL="https://github.com/DrTimothyAldenDavis/SuiteSparse/archive/refs/tags/v4.0.2.tar.gz"
LOKI_URL="https://sourceforge.net/projects/loki-lib/files/Loki/Loki%200.1.7/loki-0.1.7.tar.gz/download"

log() { printf '\n=== %s: %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fetch() { [ -s "$2" ] || curl -sSfL -A "$UA" -o "$2" "$1"; }

oneapi() {
  # setvars.sh trips over `set -u`, so relax it while sourcing.
  set +u; . /opt/intel/oneapi/setvars.sh --force >/dev/null; set -u
  export CC=icx CXX=icpx FC=ifx F77=ifx
}

stage_sources() {
  log "fetching sources into $SRC"
  mkdir -p "$SRC" && cd "$SRC"
  fetch "$DSM2_812_SRC_URL" dsm2_v8_1_2_src.zip
  fetch "$BOOST_URL" boost_1_83_0.tar.gz
  fetch "$HDF5_URL" hdf5-1_14_2.tar.gz
  fetch "$SUITESPARSE_URL" SuiteSparse-4.0.2.tar.gz
  fetch "$LOKI_URL" loki-0.1.7.tar.gz
  [ -d dsm2 ] || git clone --quiet --depth 1 --branch "$DSM2_TAG" https://github.com/CADWRDeltaModeling/dsm2.git dsm2
  ls -la
}

stage_heclib() {
  local out=$DEPS/heclib-6/lib/libheclib.a
  [ -s "$out" ] && { log "heclib already built"; return; }
  log "building HEClib v6"
  oneapi
  local w=$ROOT/build/heclib-work
  rm -rf "$w" && mkdir -p "$w/obj"
  unzip -q -o "$SRC/dsm2_v8_1_2_src.zip" 'third_party/heclib_vs2008/*' -d "$w/src"
  local h=$w/src/third_party/heclib_vs2008
  # This is HEClib's Windows edition. Its C file layer (heclib_c/*.c) and seven
  # Fortran routines use Windows-only calls; our Linux versions of those, in
  # heclib-linux/ beside this script, are compiled instead. Subversion metadata is skipped.
  local windows_only='/(stdinc|crenam|waits|cdelet|cparms|tempname|sortfilesfort)\.for$'
  find "$h" -path '*/.svn' -prune -o -type f \( -iname '*.for' -o -iname '*.f' \) -print \
    | grep -viE "$windows_only" > "$w/fortran.txt"
  : > "$w/c.txt"
  cp "$OVERLAY"/*.f90 "$OVERLAY"/*.c "$w/"
  echo "$w/posix_io.c" >> "$w/c.txt"
  # Windows ignores the case of include names and Linux does not: gather every
  # header into one folder under its own, lower-case and upper-case names.
  mkdir -p "$w/include"
  find "$h" -path '*/.svn' -prune -o -type f -iname '*.h' -print | while read -r f; do
    b=$(basename "$f"); cp -f "$f" "$w/include/$b"
    cp -f "$f" "$w/include/${b,,}" 2>/dev/null || true; cp -f "$f" "$w/include/${b^^}" 2>/dev/null || true
  done
  local incs="-I$w/include"
  local failed=$w/failed.txt; : > "$failed"
  while read -r f; do
    o=$w/obj/$(echo "${f#$h/}" | tr '/' '_').o
    ifx -c -O2 -fpic -fixed -assume byterecl $incs "$f" -o "$o" 2>>"$w/fortran.log" || echo "$f" >> "$failed"
  done < "$w/fortran.txt"
  ifx -c -O2 -fpic "$w/linux_helpers.f90" -o "$w/obj/linux_helpers.o" 2>>"$w/fortran.log" || echo "$w/linux_helpers.f90" >> "$failed"
  while read -r f; do
    o=$w/obj/$(echo "${f#$h/}" | tr '/' '_').o
    icx -c -O2 -fpic $incs "$f" -o "$o" 2>>"$w/c.log" || echo "$f" >> "$failed"
  done < "$w/c.txt"
  echo "compiled: $(ls "$w/obj" | wc -l)  failed: $(wc -l < "$failed")"
  if [ -s "$failed" ]; then
    echo "files that did not compile (see $w/fortran.log and $w/c.log):"; cat "$failed"
    return 1
  fi
  mkdir -p "$(dirname "$out")"
  ar rcs "$out" "$w"/obj/*.o
  log "heclib: $out"
}

stage_klu() {
  local out=$DEPS/suitesparse-4.0.2/lib/libKLU.a
  [ -s "$out" ] && { log "KLU already built"; return; }
  log "building KLU (SuiteSparse 4.0.2) as one libKLU.a"
  oneapi
  local w=$ROOT/build/klu-work
  rm -rf "$w" && mkdir -p "$w/obj" && tar -xzf "$SRC/SuiteSparse-4.0.2.tar.gz" -C "$w"
  local s; s=$(find "$w" -maxdepth 1 -type d -name 'SuiteSparse*' | head -1)
  local inc="-I$s/SuiteSparse_config -I$s/AMD/Include -I$s/COLAMD/Include -I$s/BTF/Include -I$s/KLU/Include"
  icx -c -O2 -fpic $inc "$s/SuiteSparse_config/SuiteSparse_config.c" -o "$w/obj/ss_config.o"
  # AMD, COLAMD, BTF and KLU are each built in int and long variants, as their own Makefiles do.
  for lib in AMD COLAMD BTF KLU; do
    for f in "$s/$lib"/Source/*.c; do
      b=$(basename "$f" .c)
      icx -c -O2 -fpic $inc "$f" -o "$w/obj/${lib}_${b}_i.o"
      icx -c -O2 -fpic $inc -DDLONG "$f" -o "$w/obj/${lib}_${b}_l.o"
    done
  done
  mkdir -p "$(dirname "$out")" "$DEPS/suitesparse-4.0.2/include"
  ar rcs "$out" "$w"/obj/*.o
  cp "$s"/SuiteSparse_config/*.h "$s"/{AMD,COLAMD,BTF,KLU}/Include/*.h "$DEPS/suitesparse-4.0.2/include/"
  log "KLU: $out"
}

stage_loki() {
  [ -f "$DEPS/loki-0.1.7/include/loki/LokiTypeInfo.h" ] && { log "Loki already in place"; return; }
  log "unpacking Loki 0.1.7 (headers only)"
  mkdir -p "$DEPS" && tar -xzf "$SRC/loki-0.1.7.tar.gz" -C "$DEPS"
}

stage_hdf5() {
  [ -f "$DEPS/hdf5-1.14.2/lib/libhdf5_fortran.a" ] && { log "HDF5 already built"; return; }
  log "building HDF5 1.14.2 with C, high-level and Fortran libraries (static)"
  oneapi
  local w=$ROOT/build/hdf5-work
  rm -rf "$w" && mkdir -p "$w" && tar -xzf "$SRC/hdf5-1_14_2.tar.gz" -C "$w"
  local s; s=$(find "$w" -maxdepth 2 -name configure -printf '%h\n' | head -1)
  cd "$s"
  ./configure --quiet --prefix="$DEPS/hdf5-1.14.2" --enable-fortran --enable-hl \
    --disable-shared --enable-static --with-pic --disable-tests --disable-tools
  make -s -j "$JOBS" && make -s install
}

stage_boost() {
  [ -f "$DEPS/boost_1_83_0/stage/lib/libboost_filesystem.a" ] && { log "Boost already built"; return; }
  log "building Boost 1.83.0 (filesystem, regex, test; static)"
  mkdir -p "$DEPS" && tar -xzf "$SRC/boost_1_83_0.tar.gz" -C "$DEPS"
  cd "$DEPS/boost_1_83_0"
  ./bootstrap.sh --with-libraries=filesystem,regex,test >/dev/null
  # Built with GCC: icpx shares GCC's C++ library and calling conventions.
  ./b2 -j "$JOBS" -d0 toolset=gcc link=static runtime-link=shared threading=multi variant=release cxxflags=-fPIC stage
}

stage_dsm2() {
  log "building DSM2 $DSM2_TAG"
  oneapi
  local d=$SRC/dsm2
  ln -sfn "$DEPS" "$d/deps"
  cd "$d"
  cmake -E remove_directory build && cmake -E make_directory build && cd build
  cmake -C ../linux_options.cmake -G "Unix Makefiles" -DCMAKE_BUILD_TYPE:STRING=Release .. > "$ROOT/build/dsm2-cmake.log" 2>&1 \
    || { tail -40 "$ROOT/build/dsm2-cmake.log"; return 1; }
  cmake --build . --target all -j "$JOBS" > "$ROOT/build/dsm2-make.log" 2>&1 \
    || { grep -nE 'error|Error' "$ROOT/build/dsm2-make.log" | head -40; return 1; }
  mkdir -p "$ROOT/bin" && find . -type f -executable \( -name hydro -o -name qual -o -name gtm -o -name hydro_gtm \) -exec cp {} "$ROOT/bin/" \;
  ls -la "$ROOT/bin"
}

stages=("$@"); [ ${#stages[@]} -eq 0 ] && stages=(sources heclib klu loki hdf5 boost dsm2)
for s in "${stages[@]}"; do "stage_$s"; done
log "done: ${stages[*]}"
