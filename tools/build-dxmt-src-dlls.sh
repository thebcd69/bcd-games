#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
#
# Build DXMT's 64-bit d3d10core.dll (dxmt/src/d3d10/d3d10core.cpp) and winemetal.dll (the PE half of dxmt/src/winemetal) for
# arm64ec from the dxmt submodule and ship them as app/Madeira/arm64ec-windows/d3d10core-src.dll and winemetal-src.dll, NEXT TO
# the committed binaries, which stay the default for every game.
#
# Why: app/Madeira/arm64ec-windows/d3d10core.dll and winemetal.dll are binaries upstream committed on 2026-09-29 (9e8291e, built
# 2026-09-28 from willfaust/dxmt b15e8ba); the CI never compiled them. Together with d3d11-src.dll and dxgi-src.dll they make the
# 64-bit DXMT set built from BCD's pin ("the fresh set"): env.MADEIRA_DXMT_FRESH = 1 (the game's own file, or bcd.cfg) links all
# four in as C:\windows\sysx64 (and system32 in an ARM64EC session) d3d11.dll, dxgi.dll, d3d10core.dll and winemetal.dll
# (WineProcessBridge.m). Without the key nothing links them.
#
# Built the way DXMT's meson build builds these two targets for an arm64ec host, as tools/build-d3d11-dll.sh and
# tools/build-dxgi-dll.sh do for theirs (buildtype=release: -O3 -DNDEBUG, the project's flags and defines, util in a thin archive,
# meson's default Windows libraries, the llvm-mingw upstream used, 20260421):
#   d3d10core.dll  src/d3d10/meson.build: d3d10core.cpp + version.rc + d3d10core.def, against import libraries for D3D11.DLL and
#                  DXGI.DLL made from d3d11.def / dxgi.def (meson links the built DLLs' import libraries, which carry those names),
#                  util_lib and util_dep's -lntdll. It imports D3D11CoreCreateDevice; the DXGI import is dropped by the linker.
#   winemetal.dll  src/winemetal/meson.build: main.c, airconv_thunks.c, winemetal_thunks.c, wmt_api_census.c (C, no module
#                  definition file: the thunks export themselves), linked with Wine's libwinecrt0.a and libntdll.a of the arm64ec
#                  Wine build (WINE_EC, -Dwine_build_path) and dbghelp. No `winebuild --builtin` mark: the committed arm64ec
#                  winemetal.dll has none (the i386 farm's does), so Wine treats the two the same way.
# Every BCD patch script of the workflow leaves these sources alone (patch-winemetal-* and patch-airconv-* change the unix side
# only), so the result is the same on the pristine pin and on the tree the build already patched; the script checks it.
#
# Checks before shipping: both are ARM64EC images; their exports are exactly the committed binaries' (winemetal: every unix call
# thunk the d3d11/dxgi/d3d12/d3d9/nvapi DLLs import); they import from the same DLLs as the committed ones; winemetal imports
# no Wine-private ntdll export beyond the ones winecrt0 needs. The recipe check (informational, as in build-d3d11-dll.sh):
# the symbols of each DLL against the committed one.
#
# Any failure leaves the bundle untouched (the committed DLLs ship alone, env.MADEIRA_DXMT_FRESH = 1 then links nothing) and
# exits 1 with a ::warning::; the workflow step continues on error.
#
#   DXMTSRC_OUT=<dir>    work directory (default build/dxmt-src-dlls)
#   DXMTSRC_NO_SHIP=1    build and check, but do not copy into the bundle
#   DXMT_DIR=<dir>       the DXMT tree (default: dxmt in the repository root)
#   SHIP_DIR=<dir>       where the committed arm64ec DLLs are (default app/Madeira/arm64ec-windows)
#   WINE_EC=<dir>        the arm64ec Wine build (default wine/build-arm64ec): libs/winecrt0/arm64ec-windows/libwinecrt0.a and
#                        dlls/ntdll/arm64ec-windows/libntdll.a
#   MINGW=<dir>          llvm-mingw bin directory (default: the CI toolchain)
#   JOBS=<n>             parallel compiles (default: CPU count)
# Run from the repository root.
set -eu
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
D="${DXMT_DIR:-$R/dxmt}"
U="$D/src/util"
W="$D/src/winemetal"
T="$D/src/d3d10"
SHIP="${SHIP_DIR:-$R/app/Madeira/arm64ec-windows}"
OUT="${DXMTSRC_OUT:-$R/build/dxmt-src-dlls}"
WINE_EC="${WINE_EC:-$R/wine/build-arm64ec}"
CXX="$MINGW/arm64ec-w64-mingw32-clang++"
CC="$MINGW/arm64ec-w64-mingw32-clang"
WINDRES="$MINGW/arm64ec-w64-mingw32-windres"
AR="$MINGW/llvm-ar"
READOBJ="$MINGW/llvm-readobj"
NM="$MINGW/llvm-nm"
JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"
CRT0="$WINE_EC/libs/winecrt0/arm64ec-windows/libwinecrt0.a"
WNTDLL="$WINE_EC/dlls/ntdll/arm64ec-windows/libntdll.a"

fail() {
    echo "::warning::d3d10core-src.dll / winemetal-src.dll NOT built ($*). The committed d3d10core.dll and winemetal.dll ship alone; env.MADEIRA_DXMT_FRESH = 1 links nothing in this build."
    exit 1
}

[ -x "$CXX" ] || fail "no arm64ec clang++ at $CXX: llvm-mingw missing"
[ -f "$T/d3d10core.cpp" ] && [ -f "$T/d3d10core.def" ] || fail "dxmt/src/d3d10 is not checked out"
for f in main.c airconv_thunks.c winemetal_thunks.c wmt_api_census.c; do
    [ -f "$W/$f" ] || fail "dxmt/src/winemetal/$f is missing"
done
for f in d3d10core.dll winemetal.dll; do
    [ -f "$SHIP/$f" ] || fail "the committed arm64ec $f is missing"
done
[ -f "$CRT0" ] && [ -f "$WNTDLL" ] || fail "the arm64ec Wine build has no libwinecrt0.a / libntdll.a ($WINE_EC)"
# The source lists of src/d3d10/meson.build (d3d10core_src) and src/winemetal/meson.build (winemetal_src): a file added there
# must be added here, or the DLL would lack it.
LISTED_W="$(sed -n "/^winemetal_src = \[/,/^\]/p" "$W/meson.build" | grep -o "'[a-z0-9_]*\.c'" | tr -d "'" | tr '\n' ' ')"
[ "$LISTED_W" = "main.c airconv_thunks.c winemetal_thunks.c wmt_api_census.c " ] || fail "src/winemetal/meson.build lists other sources ($LISTED_W)"
LISTED_T="$(sed -n "/^d3d10core_src = \[/,/^\]/p" "$T/meson.build" | grep -o "'[a-z0-9_]*\.cpp'" | tr -d "'" | tr '\n' ' ')"
[ "$LISTED_T" = "d3d10core.cpp " ] || fail "src/d3d10/meson.build lists other d3d10core sources ($LISTED_T)"

rm -rf "$OUT"
mkdir -p "$OUT/obj"
REV="$(git -C "$D" rev-parse --short HEAD 2>/dev/null || echo unknown)"
exports() { "$READOBJ" --coff-exports "$1" | awk '$1 == "Name:" && $2 != "" { print $2 }' | sort -u; }
imports() { "$READOBJ" --coff-imports "$1" | awk '$1 == "Name:" { print $2 }' | sort -u; }

# Import libraries for D3D11.DLL and DXGI.DLL from the module definition files (the names meson's import libraries carry).
"$MINGW/llvm-dlltool" -m arm64ec -d "$D/src/d3d11/d3d11.def" -l "$OUT/libd3d11.a" || fail "llvm-dlltool failed for d3d11.def"
"$MINGW/llvm-dlltool" -m arm64ec -d "$D/src/dxgi/dxgi.def" -l "$OUT/libdxgi.a" || fail "llvm-dlltool failed for dxgi.def"

# DXMT's meson.build: compiler_args, the project defines, buildtype=release (-O3, b_ndebug=if-release -> NDEBUG); C++20 and
# DXMT_PAGE_SIZE for C++ only. -fmacro-prefix-map maps the source root away as meson's '-fmacro-prefix-map=../=' does.
COMMON=(
    -O3 -DNDEBUG
    -Wimplicit-fallthrough -Wno-missing-field-initializers -Wno-unused-parameter
    -Wno-cast-function-type -Wno-unused-private-field -Wno-microsoft-exception-spec
    -Wno-extern-c-compat -Wno-unused-const-variable -Wno-missing-braces -fblocks
    -DNOMINMAX -D_WIN32_WINNT=0xa00 -DDXMT_IOS=1 -fmacro-prefix-map="$D/="
)
CXXFLAGS=(-std=c++20 "${COMMON[@]}" -DDXMT_PAGE_SIZE=4096)
CFLAGS=("${COMMON[@]}")
INC_BASE=(-I"$D/include" -I"$D/libs")

: > "$OUT/jobs"
job() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$OUT/jobs"; }
compile_one() {  # compile_one <source> <object> <flag set>
    local src="$1" obj="$2" set="$3" inc
    case "$set" in
        util)      inc=(-I"$U" "${INC_BASE[@]}") ;;
        d3d10core) inc=(-I"$T" "${INC_BASE[@]}" -I"$D/src/dxgi" -I"$U") ;;
        winemetal) inc=(-I"$W" "${INC_BASE[@]}" -I"$D/src/airconv") ;;
    esac
    case "$src" in
        *.c) "$CC" "${CFLAGS[@]}" "${inc[@]}" -c "$src" -o "$obj" ;;
        *)   "$CXX" "${CXXFLAGS[@]}" "${inc[@]}" -c "$src" -o "$obj" ;;
    esac > "$obj.err" 2>&1 || { echo "$src" >> "$OUT/failed"; return 1; }
}
run_jobs() {
    local w
    rm -f "$OUT/failed"
    for ((w = 0; w < JOBS; w++)); do
        ( awk -F'\t' -v w="$w" -v n="$JOBS" '(NR - 1) % n == w' "$OUT/jobs" | while IFS="$(printf '\t')" read -r src obj set; do
              compile_one "$src" "$obj" "$set" || true
          done ) &
    done
    wait
    if [ -s "$OUT/failed" ]; then
        while read -r f; do
            o="$(awk -F'\t' -v s="$f" '$1 == s { print $2; exit }' "$OUT/jobs")"
            echo "=== $f"; grep -m 10 -A3 "error" "$o.err" || tail -20 "$o.err"
        done < "$OUT/failed"
        fail "compiling $(wc -l < "$OUT/failed" | tr -d ' ') file(s) failed, first $(basename "$(head -1 "$OUT/failed")")"
    fi
    while IFS="$(printf '\t')" read -r src obj set; do
        [ -s "$obj" ] || fail "no object for $(basename "$src")"
    done < "$OUT/jobs"
}

# util_lib (src/util/meson.build: an aarch64 Windows host uses the headless wsi files), in source order, as the other arm64ec
# DXMT builds compile it; d3d10core uses its logger only.
UTIL_SRC=(util_env.cpp util_string.cpp util_bloom.cpp util_futex.cpp thread.cpp
          com/com_guid.cpp com/com_private_data.cpp config/config.cpp log/log.cpp
          sha1/sha1.c sha1/sha1_util.cpp
          wsi_monitor_headless.cpp wsi_window_headless.cpp wsi_platform_win32.cpp)
WM_SRC=(main airconv_thunks winemetal_thunks wmt_api_census)
UTIL_OBJ=(); WM_OBJ=()
for s in "${UTIL_SRC[@]}"; do
    o="$OUT/obj/util_$(echo "$s" | tr '/.' '__').o"; job "$U/$s" "$o" util; UTIL_OBJ+=("$o")
done
job "$T/d3d10core.cpp" "$OUT/obj/d3d10core.o" d3d10core
for s in "${WM_SRC[@]}"; do
    o="$OUT/obj/winemetal_$s.o"; job "$W/$s.c" "$o" winemetal; WM_OBJ+=("$o")
done
echo "compiling $(wc -l < "$OUT/jobs" | tr -d ' ') files, $JOBS at a time (DXMT $REV)"
run_jobs
"$WINDRES" -i "$T/version.rc" -o "$OUT/obj/d3d10core_version.o" 2>> "$OUT/build.err" || fail "windres d3d10/version.rc failed"
"$AR" csrDT "$OUT/libutil.a" "${UTIL_OBJ[@]}"

# meson: the objects in order, the resource, the module definition file, the dependency libraries in the target's dependency
# order (d3d10core: d3d11, dxgi, util + util_dep's -lntdll; winemetal: winecrt0, Wine's ntdll, dbghelp), -static, file
# alignment 4096 and meson's default Windows libraries.
WINLIBS=(-lkernel32 -luser32 -lgdi32 -lwinspool -lshell32 -lole32 -loleaut32 -luuid -lcomdlg32 -ladvapi32)
"$CXX" -shared -o "$OUT/d3d10core.dll" "$OUT/obj/d3d10core.o" "$OUT/obj/d3d10core_version.o" "$T/d3d10core.def" \
    -L"$OUT" -ld3d11 -ldxgi "$OUT/libutil.a" -lntdll \
    -static -Wl,--file-alignment=4096 "${WINLIBS[@]}" \
    2>> "$OUT/build.err" || { grep -m 20 "error\|undefined" "$OUT/build.err"; fail "linking d3d10core.dll failed"; }
"$CC" -shared -o "$OUT/winemetal.dll" "${WM_OBJ[@]}" \
    "$CRT0" "$WNTDLL" -ldbghelp \
    -static -Wl,--file-alignment=4096 "${WINLIBS[@]}" \
    2>> "$OUT/build.err" || { grep -m 20 "error\|undefined" "$OUT/build.err"; fail "linking winemetal.dll failed"; }

# Padded with zeros to SizeOfImage + 0x10000 when the file is shorter, as build-d3d9-x64-dll.sh and the extra Wine DLLs are
# (the iOS mapping path reads up to the image's size from the file). The COFF symbol table stays, so a crash backtrace in the
# log names the functions.
for f in d3d10core winemetal; do
    python3 - "$OUT/$f.dll" <<'PY' || fail "padding $f.dll failed"
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY
done

# The results: ARM64EC images whose exports and imported DLLs are exactly the committed binaries'.
for f in d3d10core winemetal; do
    "$READOBJ" --file-headers "$OUT/$f.dll" | grep -q "IMAGE_FILE_MACHINE_ARM64EC" || fail "$f.dll is not an ARM64EC image"
    exports "$SHIP/$f.dll" > "$OUT/$f.exports.committed"
    exports "$OUT/$f.dll" > "$OUT/$f.exports.built"
    [ -s "$OUT/$f.exports.committed" ] || fail "could not read the exports of the committed $f.dll"
    diff "$OUT/$f.exports.committed" "$OUT/$f.exports.built" > "$OUT/$f.exports.diff" \
        || { cat "$OUT/$f.exports.diff"; fail "$f.dll's exports differ from the committed $f.dll"; }
    imports "$SHIP/$f.dll" > "$OUT/$f.imports.committed"
    imports "$OUT/$f.dll" > "$OUT/$f.imports.built"
    diff "$OUT/$f.imports.committed" "$OUT/$f.imports.built" > "$OUT/$f.imports.diff" \
        || { cat "$OUT/$f.imports.diff"; fail "$f.dll imports from other DLLs than the committed $f.dll"; }
done
# winemetal reaches its unix side through __wine_unix_call (winecrt0); the committed binary imports these ntdll functions and
# nothing else from ntdll, and so must this one (a Wine-private export a new winecrt0 wanted would not resolve in BCD's ntdll).
"$READOBJ" --coff-imports "$OUT/winemetal.dll" | awk '$1 == "Name:" { dll = $2 } $1 == "Symbol:" && dll == "ntdll.dll" { print $2 }' \
    | sort -u > "$OUT/winemetal.ntdll.built"
"$READOBJ" --coff-imports "$SHIP/winemetal.dll" | awk '$1 == "Name:" { dll = $2 } $1 == "Symbol:" && dll == "ntdll.dll" { print $2 }' \
    | sort -u > "$OUT/winemetal.ntdll.committed"
diff "$OUT/winemetal.ntdll.committed" "$OUT/winemetal.ntdll.built" > "$OUT/winemetal.ntdll.diff" \
    || { cat "$OUT/winemetal.ntdll.diff"; fail "winemetal.dll imports other ntdll functions than the committed winemetal.dll"; }
LC_ALL=C grep -aqF "Wine builtin DLL" "$OUT/winemetal.dll" && fail "winemetal.dll carries a Wine builtin mark (the committed one has none)"

# The recipe check (reported, never fatal): the names of the functions each DLL defines against the committed binary.
NOTE=""
for f in d3d10core winemetal; do
    "$NM" --defined-only "$SHIP/$f.dll" | awk '{ print $2, $3 }' | sort > "$OUT/$f.names.committed"
    "$NM" --defined-only "$OUT/$f.dll" | awk '{ print $2, $3 }' | sort > "$OUT/$f.names.built"
    TOTAL=$(wc -l < "$OUT/$f.names.committed" | tr -d ' ')
    SAME=$(comm -12 "$OUT/$f.names.committed" "$OUT/$f.names.built" | wc -l | tr -d ' ')
    NOTE="$NOTE $f.dll $SAME of $TOTAL committed symbol names;"
done

if [ "${DXMTSRC_NO_SHIP:-0}" = "1" ]; then
    echo "d3d10core.dll and winemetal.dll built at $OUT (not shipped, DXMTSRC_NO_SHIP=1; DXMT $REV);$NOTE"
    exit 0
fi
for f in d3d10core winemetal; do
    cp "$OUT/$f.dll" "$SHIP/$f-src.dll.tmp" && mv -f "$SHIP/$f-src.dll.tmp" "$SHIP/$f-src.dll" || fail "copying $f-src.dll into the bundle failed"
done
echo "::notice::d3d10core-src.dll ($(wc -c < "$SHIP/d3d10core-src.dll" | tr -d ' ') bytes) and winemetal-src.dll ($(wc -c < "$SHIP/winemetal-src.dll" | tr -d ' ') bytes) built from DXMT $REV (exports and imported DLLs = the committed ones;$NOTE) and shipped next to the committed d3d10core.dll and winemetal.dll, which stay the default (env.MADEIRA_DXMT_FRESH = 1 selects them)"
