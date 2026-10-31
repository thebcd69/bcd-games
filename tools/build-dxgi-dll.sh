#!/bin/bash
# Build DXMT's dxgi.dll (dxmt/src/dxgi) for arm64ec from the submodule source,
# with IDXGIFactory7 and EnumAdapterByLuid added (tools/patch-dxgi-factory7.py)
# and CheckInterfaceSupport's UMD version taken from D3DKMT when
# env.MADEIRA_KMT_ADAPTER = 1 (tools/patch-dxgi-umd-version.py), with
# video-memory budget-change events when env.MADEIRA_DXGI_BUDGET_EVENTS = 1
# (tools/patch-dxgi-budget-events.py), and ship it NEXT TO upstream's
# committed binary as app/Madeira/arm64ec-windows/dxgi-src.dll.
#
# Why: the 64-bit dxgi.dll in the bundle is a prebuilt binary upstream
# committed (9e8291e); CI never compiled it, so a change to dxmt/src/dxgi
# never reached the phone. Its factory answers only up to IDXGIFactory6, and
# GTA V Enhanced asks for IDXGIFactory7 (a4966eed-76db-44da-84c1-ee9a7afb20a8),
# gets E_NOINTERFACE, keeps a NULL factory and stops with ERR_GFX_D3D_NOD3D12
# (build 296, logs 2026-10-01 20:16 and 20:41).
#
# Upstream's dxgi.dll stays the default for every game. WineProcessBridge.m
# links dxgi-src.dll in as C:\windows\system32\dxgi.dll (and sysx64) only when
# env.MADEIRA_DXGI_SRC = 1 is set (the game's own file, or bcd.cfg), so it
# can be tried per game and dropped again without a build.
#
# Built the way DXMT's meson build does (buildtype=release: -O3 -DNDEBUG, the
# project's flags and defines, util/dxmt sources in thin archives so only what
# dxgi references is linked and in the same order, meson's default Windows
# libraries), with the llvm-mingw upstream used (20260421, clang 22.1.4),
# against an import library derived from the shipped winemetal.dll. The
# workflow runs it BEFORE the DXMT patch steps, so the sources are the pristine
# pin. To prove the recipe, the factory is also linked WITHOUT the patch
# (OUT/plain/dxgi.dll) and compared with upstream's committed dxgi.dll: the same
# symbols at the same addresses means this source build is upstream's binary
# plus the patch and nothing else (a mismatch is reported, not fatal).
#
# Any failure leaves the bundle untouched (upstream's dxgi.dll ships alone) and
# exits 1 with a ::warning::; the workflow step continues on error.
#
#   DXGI_OUT=<dir>    work directory (default build/dxmt-dxgi)
#   DXGI_NO_SHIP=1    build and check, but do not copy into the bundle
# Run from the repository root.
set -eu
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
D="$R/dxmt"
U="$D/src/util"
G="$D/src/dxgi"
SHIP="$R/app/Madeira/arm64ec-windows"
OUT="${DXGI_OUT:-$R/build/dxmt-dxgi}"
DEST="$SHIP/dxgi-src.dll"
CXX="$MINGW/arm64ec-w64-mingw32-clang++"
CC="$MINGW/arm64ec-w64-mingw32-clang"
WINDRES="$MINGW/arm64ec-w64-mingw32-windres"
AR="$MINGW/llvm-ar"
READOBJ="$MINGW/llvm-readobj"
NM="$MINGW/llvm-nm"

fail() {
    echo "::warning::dxgi-src.dll NOT built ($*). Upstream's dxgi.dll ships alone; env.MADEIRA_DXGI_SRC = 1 has no effect in this build."
    exit 1
}

[ -x "$CXX" ] || fail "no arm64ec clang++ at $CXX: llvm-mingw missing"
[ -f "$G/dxgi_factory.cpp" ] || fail "dxmt/src/dxgi is not checked out"
[ -f "$SHIP/dxgi.dll" ] && [ -f "$SHIP/winemetal.dll" ] || fail "the committed arm64ec dxgi.dll / winemetal.dll are missing"

# The current pin already has Factory7 and the UMD version. The separate copy
# now also supplies opt-in x64 COM entry points, which the tracked DLL lacks.

rm -rf "$OUT"
mkdir -p "$OUT/obj" "$OUT/src" "$OUT/plain"
REV="$(git -C "$D" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# winemetal.dll's import library, from the binary that ships beside it.
{ echo "LIBRARY winemetal.dll"; echo "EXPORTS"
  "$READOBJ" --coff-exports "$SHIP/winemetal.dll" | awk '$1 == "Name:" && $2 != "" { print $2 }' | sort -u; } > "$OUT/winemetal.def"
"$MINGW/llvm-dlltool" -m arm64ec -d "$OUT/winemetal.def" -l "$OUT/libwinemetal.a" || fail "llvm-dlltool failed for winemetal.dll"

# The factory with IDXGIFactory7, on a copy (the submodule stays untouched).
cp "$G/dxgi_factory.cpp" "$OUT/src/dxgi_factory.cpp"
python3 "$R/tools/patch-dxgi-factory7.py" "$OUT/src/dxgi_factory.cpp" || fail "tools/patch-dxgi-factory7.py did not apply"
python3 "$R/tools/patch-dxgi-x64-entry.py" "$OUT/src/dxgi_factory.cpp" || fail "tools/patch-dxgi-x64-entry.py did not apply"
# Opt-in for one game (round 624, Slay the Spire 2): a composition swap chain served on the game's window under
# env.MADEIRA_DXGI_COMPOSITION = 1. A missed anchor must not cost every game dxgi-src.dll: the copy keeps today's answer.
COMPOSITION=" + opt-in composition swap chains"
python3 "$R/tools/patch-dxgi-composition.py" "$OUT/src/dxgi_factory.cpp" || {
    COMPOSITION=""
    echo "::warning::tools/patch-dxgi-composition.py did not apply: dxgi-src.dll is built without composition swap chains (env.MADEIRA_DXGI_COMPOSITION has no effect)"
}
# The adapter with the D3DKMT UMD version (run-time switch MADEIRA_KMT_ADAPTER), also on a copy,
# and with video-memory budget-change events (run-time switch MADEIRA_DXGI_BUDGET_EVENTS).
cp "$G/dxgi_adapter.cpp" "$OUT/src/dxgi_adapter.cpp"
python3 "$R/tools/patch-dxgi-umd-version.py" "$OUT/src/dxgi_adapter.cpp" || fail "tools/patch-dxgi-umd-version.py did not apply"
# Opt-in for one game, so a missed anchor must not cost every game dxgi-src.dll:
# the copy is left unpatched and the build goes on.
BUDGET_EVENTS=" + opt-in budget events"
python3 "$R/tools/patch-dxgi-budget-events.py" "$OUT/src/dxgi_adapter.cpp" || {
    BUDGET_EVENTS=""
    echo "::warning::tools/patch-dxgi-budget-events.py did not apply: dxgi-src.dll is built without budget events (env.MADEIRA_DXGI_BUDGET_EVENTS has no effect)"
}

# DXMT's meson.build: compiler_args, the project defines, buildtype=release
# (-O3, b_ndebug=if-release -> NDEBUG), C++20, the include paths of dxgi's
# dependencies (dxmt_dep, util_dep, winemetal_dep, airconv_forward_dep).
COMMON=(
    -O3 -DNDEBUG
    -Wimplicit-fallthrough -Wno-missing-field-initializers -Wno-unused-parameter
    -Wno-cast-function-type -Wno-unused-private-field -Wno-microsoft-exception-spec
    -Wno-extern-c-compat -Wno-unused-const-variable -Wno-missing-braces -fblocks
    -DNOMINMAX -D_WIN32_WINNT=0xa00 -DDXMT_PAGE_SIZE=4096 -DDXMT_IOS=1
    -I"$G" -I"$D/src/dxmt" -I"$U" -I"$D/src/winemetal" -I"$D/src/airconv"
    -I"$D/include" -I"$D/libs"
    -I"$R/madeira-d3d12/src/pe"
)
CXXFLAGS=(-std=c++20 "${COMMON[@]}")
CFLAGS=("${COMMON[@]}")

compile() {  # compile <source> <object> [extra flags]
    local src="$1" obj="$2"; shift 2
    case "$src" in
        *.c) "$CC" "${CFLAGS[@]}" "$@" -c "$src" -o "$obj" ;;
        *)   "$CXX" "${CXXFLAGS[@]}" "$@" -c "$src" -o "$obj" ;;
    esac 2>> "$OUT/build.err" || { grep -m 20 "error" "$OUT/build.err"; fail "compiling $(basename "$src") failed"; }
}

# util_lib (src/util/meson.build: an aarch64 Windows host uses the headless
# wsi files) and the part of dxmt_lib dxgi uses (dxmt_format.cpp). Thin
# archives in source order, as meson makes them ("csrDT"), so lld pulls the
# same members in the same order as upstream's link.
UTIL_SRC=(util_env.cpp util_string.cpp util_bloom.cpp util_futex.cpp thread.cpp
          com/com_guid.cpp com/com_private_data.cpp config/config.cpp log/log.cpp
          sha1/sha1.c sha1/sha1_util.cpp
          wsi_monitor_headless.cpp wsi_window_headless.cpp wsi_platform_win32.cpp)
UTIL_OBJ=()
for s in "${UTIL_SRC[@]}"; do
    o="$OUT/obj/util_$(echo "$s" | tr '/.' '__').o"
    compile "$U/$s" "$o"
    UTIL_OBJ+=("$o")
done
"$AR" csrDT "$OUT/libutil.a" "${UTIL_OBJ[@]}"
compile "$D/src/dxmt/dxmt_format.cpp" "$OUT/obj/dxmt_format.o"
"$AR" csrDT "$OUT/libdxmt.a" "$OUT/obj/dxmt_format.o"

for s in dxgi_output dxgi_options dxgi; do
    compile "$G/$s.cpp" "$OUT/obj/$s.o"
done
compile "$G/dxgi_adapter.cpp" "$OUT/obj/dxgi_adapter_plain.o"
compile "$OUT/src/dxgi_adapter.cpp" "$OUT/obj/dxgi_adapter.o"
compile "$G/dxgi_factory.cpp" "$OUT/obj/dxgi_factory_plain.o"
compile "$OUT/src/dxgi_factory.cpp" "$OUT/obj/dxgi_factory.o" "-DMADEIRA_DXGI_SRC_REV=\"$REV\""
"$WINDRES" -i "$G/version.rc" -o "$OUT/obj/version.o" 2>> "$OUT/build.err" || fail "windres version.rc failed"

# meson: dxgi_src in order, the resource, the module definition file, the
# dependency archives, -static (libc++ in, UCRT through api-ms-win-crt-*), file
# alignment 4096, util_dep's -lntdll and meson's default Windows libraries
# (cpp_winlibs; gdi32 has D3DKMTOpenAdapterFromLuid).
link_dll() {  # link_dll <factory object> <output> <adapter object>
    "$CXX" -shared -o "$2" \
        "$3" "$1" "$OUT/obj/dxgi_output.o" "$OUT/obj/dxgi_options.o" "$OUT/obj/dxgi.o" \
        "$OUT/obj/version.o" "$G/dxgi.def" \
        "$OUT/libdxmt.a" "$OUT/libutil.a" -L"$OUT" -lwinemetal -lntdll \
        -static -Wl,--file-alignment=4096 \
        -lkernel32 -luser32 -lgdi32 -lwinspool -lshell32 -lole32 -loleaut32 -luuid -lcomdlg32 -ladvapi32 \
        2>> "$OUT/build.err" || { grep -m 20 "error" "$OUT/build.err"; fail "linking $(basename "$2") failed"; }
}
link_dll "$OUT/obj/dxgi_factory_plain.o" "$OUT/plain/dxgi.dll" "$OUT/obj/dxgi_adapter_plain.o"
link_dll "$OUT/obj/dxgi_factory.o" "$OUT/dxgi.dll" "$OUT/obj/dxgi_adapter.o"

# The new DLL must export exactly what upstream's does: Wine and every game
# bind to these names (DXMT's d3d11 and our nvapi64 import them too).
exports() { "$READOBJ" --coff-exports "$1" | awk '$1 == "Name:" && $2 != "" { print $2 }' | sort -u; }
exports "$SHIP/dxgi.dll" > "$OUT/exports.upstream"
exports "$OUT/dxgi.dll" > "$OUT/exports.built"
[ -s "$OUT/exports.upstream" ] || fail "could not read the exports of the committed dxgi.dll"
diff "$OUT/exports.upstream" "$OUT/exports.built" > "$OUT/exports.diff" \
    || { cat "$OUT/exports.diff"; fail "its exports differ from the committed dxgi.dll"; }
"$READOBJ" --file-headers "$OUT/dxgi.dll" | grep -q "IMAGE_FILE_MACHINE_ARM64EC" || fail "the result is not an ARM64EC image"
if ! grep -q "MTLDXGIObject<IDXGIFactory7>" "$G/dxgi_factory.cpp"; then
    LC_ALL=C grep -aq "madeira-bcd dxgi.dll from DXMT source" "$OUT/dxgi.dll" || fail "the Factory7 patch is not in the result"
fi
if ! grep -q "GetUmdDriverVersion()" "$G/dxgi_adapter.cpp"; then
    LC_ALL=C grep -aq "CheckInterfaceSupport: UMD version" "$OUT/dxgi.dll" || fail "the UMD version patch is not in the result"
fi
"$NM" --defined-only "$OUT/dxgi.dll" | grep -q "RegisterAdaptersChangedEvent" || fail "RegisterAdaptersChangedEvent is not in the result"
if [ -n "$BUDGET_EVENTS" ]; then
    LC_ALL=C grep -aq "\[budget-event\] registered cookie=" "$OUT/dxgi.dll" || fail "the budget events patch is not in the result"
fi
if [ -n "$COMPOSITION" ]; then
    LC_ALL=C grep -aq "MADEIRA_DXGI_COMPOSITION" "$OUT/dxgi.dll" || fail "the composition swap chain patch is not in the result"
fi
python3 "$R/tests/host/check-x64-graphics-entry.py" --factory "$OUT/dxgi.dll" || fail "the x64 factory entries failed validation"

# The recipe check: the unpatched link against upstream's binary. Same
# symbols at the same addresses = same code and layout (only the toolchain's
# own build paths and the timestamp can differ). Reported, never fatal.
"$NM" --defined-only "$SHIP/dxgi.dll" | sort > "$OUT/plain/nm.upstream"
"$NM" --defined-only "$OUT/plain/dxgi.dll" | sort > "$OUT/plain/nm.built"
TOTAL=$(wc -l < "$OUT/plain/nm.upstream" | tr -d ' ')
SAME=$(comm -12 "$OUT/plain/nm.upstream" "$OUT/plain/nm.built" | wc -l | tr -d ' ')
if [ "$TOTAL" -gt 0 ] && [ "$SAME" = "$TOTAL" ] && [ "$(wc -l < "$OUT/plain/nm.built" | tr -d ' ')" = "$TOTAL" ]; then
    MATCH="reproduces upstream's dxgi.dll without the patch ($TOTAL symbols at the same addresses)"
else
    MATCH="does NOT reproduce upstream's dxgi.dll without the patch ($SAME of $TOTAL symbols match)"
    echo "::warning::dxgi.dll source build $MATCH: the committed binary was built from other sources or flags, so dxgi-src.dll may differ from it in more than IDXGIFactory7 -- compare before relying on it ($OUT/plain)"
fi

if [ "${DXGI_NO_SHIP:-0}" = "1" ]; then
    echo "dxgi.dll built at $OUT/dxgi.dll (not shipped, DXGI_NO_SHIP=1); the recipe $MATCH"
    exit 0
fi
cp "$OUT/dxgi.dll" "$DEST.tmp" && mv -f "$DEST.tmp" "$DEST" || fail "copying into the bundle failed"
echo "::notice::dxgi-src.dll built from DXMT $REV with IDXGIFactory7 + EnumAdapterByLuid + the D3DKMT UMD version$BUDGET_EVENTS$COMPOSITION ($(wc -c < "$DEST" | tr -d ' ') bytes, exports = upstream's) and shipped next to upstream's dxgi.dll, which stays the default (env.MADEIRA_DXGI_SRC = 1 selects it); the recipe $MATCH"
