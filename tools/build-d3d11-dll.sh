#!/bin/bash
# Build DXMT's d3d11.dll (dxmt/src/d3d11 + d3d10) for arm64ec from the submodule
# source, with ID3D11DeviceContext1::SwapDeviceContextState implemented
# (tools/patch-dxmt-context-state-swap.py, applied to a COPY of the sources),
# and ship it NEXT TO upstream's committed binary as
# app/Madeira/arm64ec-windows/d3d11-src.dll.
#
# Why: the 64-bit d3d11.dll in the bundle is a prebuilt binary upstream
# committed (9e8291e); CI never compiled it, so the "Patch DXMT context state
# swap" step changed sources nothing 64-bit was built from. Wine's d2d1 brackets
# its draws with SwapDeviceContextState, which upstream leaves IMPLEMENT_ME
# (abort): the Rockstar Games Launcher exits with code 3 right after
# "err: src\d3d11\d3d11_context_impl.cpp:SwapDeviceContextState is not
# implemented." (build 366, the message still there).
#
# Upstream's d3d11.dll stays the default for every game. WineProcessBridge.m
# links d3d11-src.dll in as C:\windows\system32\d3d11.dll (and sysx64) only
# when env.MADEIRA_D3D11_SRC = 1 is set (the game's own file, or bcd.cfg).
#
# madeira-bcd: the DXMT core (dxmt/src/dxmt) is compiled from a copy too, with
# small staging rings for the programs env.DXMT_SMALL_RINGS names
# (tools/patch-d3d11-src-small-rings.py, a run-time switch, off by default).
# Opt-in per game, so a missed anchor leaves the copy unpatched and the build
# goes on without it. The d3d11/d3d10 objects of d3d11-src.dll include the
# copy's headers and link the copy's objects; the unpatched DLL of the recipe
# check below is built from the submodule alone, as before.
#
# Built the way DXMT's meson build does (buildtype=release: -O3 -DNDEBUG, the
# project's flags and defines, -fmacro-prefix-map so __FILE__ reads
# "src\d3d11\..." as in upstream's binary, the d3d11 and d3d10 sources in
# meson.build order, DXBCParser/dxmt/util as thin archives so lld pulls the
# same members in the same order, meson's default Windows libraries), with
# the llvm-mingw upstream used (20260421), against import libraries for DXGI.DLL
# (dxmt/src/dxgi/dxgi.def, what meson links: the name and ordinal hints of
# upstream's import table; each name is checked against the shipped dxgi.dll)
# and winemetal.dll (derived from the shipped binary).
#
# Two generated headers of the meson build:
#   version.h       vcs_tag, `git describe --always`. Upstream's binary carries
#                   the describe of its own checkout (with tags); when that
#                   names the pinned commit it is used as is, so the string is
#                   the one meson would write there; else `git describe`.
#   dxmt_command.h  dxmt_command.metal through xcrun metal + metallib + xxd
#                   (src/dxmt/meson.build). Taken byte for byte from the
#                   committed d3d11.dll (its dxmt_command array), so no Metal
#                   compiler is needed and the embedded library is exactly
#                   upstream's. With xcrun (macOS CI) the .metal is also
#                   compiled and compared, as information.
# The airconv shader headers (air_msad.h, ...) are not needed: d3d11 only
# includes airconv's headers (airconv_forward_dep), it compiles none of it.
#
# The workflow runs this BEFORE the DXMT patch steps, so the submodule is the
# pristine pin. To prove the recipe, the DLL is also linked WITHOUT the patch
# (OUT/plain/d3d11.dll) and compared with upstream's committed d3d11.dll: the
# same symbols at the same addresses means this source build is upstream's
# binary plus the patch and nothing else (a mismatch is reported, not fatal;
# llvm-mingw's Linux build differs from the macOS one in libc++abi's embedded
# build paths, which moves .rdata, so off CI the name count is the useful one).
#
# Any failure leaves the bundle untouched (upstream's d3d11.dll ships alone)
# and exits 1 with a ::warning::; the workflow step continues on error.
#
#   D3D11_OUT=<dir>    work directory (default build/dxmt-d3d11)
#   D3D11_NO_SHIP=1    build and check, but do not copy into the bundle
#   MINGW=<dir>        llvm-mingw bin directory (default: the CI toolchain)
#   JOBS=<n>           parallel compiles (default: CPU count)
#   D3D11_AUTO_HDR=1   also build the experimental Auto HDR presenter/shader;
#                     needs Xcode's Metal compiler, even when the pin has the
#                     older context/ring fixes (their early exit is bypassed).
# Run from the repository root.
set -eu
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
D="$R/dxmt"
U="$D/src/util"
X="$D/src/dxmt"
G="$D/src/dxgi"
P="$D/libs/DXBCParser"
SHIP="$R/app/Madeira/arm64ec-windows"
OUT="${D3D11_OUT:-$R/build/dxmt-d3d11}"
DEST="$SHIP/d3d11-src.dll"
CXX="$MINGW/arm64ec-w64-mingw32-clang++"
CC="$MINGW/arm64ec-w64-mingw32-clang"
WINDRES="$MINGW/arm64ec-w64-mingw32-windres"
AR="$MINGW/llvm-ar"
READOBJ="$MINGW/llvm-readobj"
NM="$MINGW/llvm-nm"
JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"
MARK="madeira-bcd: SwapDeviceContextState in use"

fail() {
    echo "::warning::d3d11-src.dll NOT built ($*). Upstream's d3d11.dll ships alone; env.MADEIRA_D3D11_SRC = 1 has no effect in this build."
    exit 1
}

[ -x "$CXX" ] || fail "no arm64ec clang++ at $CXX: llvm-mingw missing"
[ -f "$D/src/d3d11/d3d11_context_impl.cpp" ] && [ -f "$D/src/d3d10/d3d10_device.cpp" ] || fail "dxmt/src/d3d11 is not checked out"
for f in d3d11.dll dxgi.dll winemetal.dll; do
    [ -f "$SHIP/$f" ] || fail "the committed arm64ec $f is missing"
done

# madeira-bcd: since the 2026-10-15 upstream merge the dxmt pin (128b297) has
# both patches (willfaust/dxmt #22 and #28), and so does upstream's committed
# d3d11.dll (Will's build at 2dc8d16). d3d11-src.dll would add nothing: it is
# not built, a stale copy is removed, and env.MADEIRA_D3D11_SRC = 1 keeps
# upstream's d3d11.dll (WineProcessBridge.m logs that). An older pin builds it
# as before.
if grep -qF "madeira-bcd: context state swap" "$D/src/d3d11/d3d11_context_impl.cpp" \
        && grep -qF "[small-rings] madeira-bcd" "$D/src/dxmt/dxmt_ring_bump_allocator.hpp" \
        && LC_ALL=C grep -aqF "$MARK" "$SHIP/d3d11.dll" \
        && LC_ALL=C grep -aqF "[small-rings] madeira-bcd" "$SHIP/d3d11.dll" \
        && [ "${D3D11_AUTO_HDR:-0}" != "1" ]; then
    [ "${D3D11_NO_SHIP:-0}" = "1" ] || rm -f "$DEST"
    echo "::notice::d3d11-src.dll not needed: DXMT $(git -C "$D" rev-parse --short HEAD 2>/dev/null || echo unknown) and upstream's committed d3d11.dll both carry SwapDeviceContextState and the opt-in small staging rings (env.DXMT_SMALL_RINGS); env.MADEIRA_D3D11_SRC = 1 keeps upstream's d3d11.dll"
    exit 0
fi

rm -rf "$OUT"
mkdir -p "$OUT/obj" "$OUT/plain/obj" "$OUT/gen" "$OUT/tree/src"
REV="$(git -C "$D" rev-parse --short HEAD 2>/dev/null || echo unknown)"

exports() { "$READOBJ" --coff-exports "$1" | awk '$1 == "Name:" && $2 != "" { print $2 }' | sort -u; }

# winemetal.dll's import library, from the binary that ships beside it.
{ echo "LIBRARY winemetal.dll"; echo "EXPORTS"; exports "$SHIP/winemetal.dll"; } > "$OUT/winemetal.def"
"$MINGW/llvm-dlltool" -m arm64ec -d "$OUT/winemetal.def" -l "$OUT/libwinemetal.a" || fail "llvm-dlltool failed for winemetal.dll"
# DXGI.DLL's, from dxgi.def as meson's (the import table names DXGI.DLL), and
# every name in it must be exported by the dxgi.dll that ships.
exports "$SHIP/dxgi.dll" > "$OUT/dxgi.exports"
for n in $(awk 'NR > 2 && $1 != "" { print $1 }' "$G/dxgi.def"); do
    grep -qx "$n" "$OUT/dxgi.exports" || fail "the committed dxgi.dll does not export $n (dxgi.def)"
done
"$MINGW/llvm-dlltool" -m arm64ec -d "$G/dxgi.def" -l "$OUT/libdxgi.a" || fail "llvm-dlltool failed for dxgi.def"

# version.h (vcs_tag).
UPVER="$(LC_ALL=C strings -a "$SHIP/d3d11.dll" | grep -m 1 -E '^v[0-9][0-9.]*-[0-9]+-g[0-9a-f]+$' || true)"
case "$UPVER" in
    *-g"$REV") VER="$UPVER" ;;
    *) VER="$(git -C "$D" describe --always 2>/dev/null || echo unknown)" ;;
esac
sed "s/@VCS_TAG@/$VER/" "$D/version.h.in" > "$OUT/gen/version.h"

# dxmt_command.h: upstream's embedded metallib, out of the committed d3d11.dll.
"$NM" --defined-only "$SHIP/d3d11.dll" > "$OUT/nm.ship" || fail "llvm-nm cannot read the committed d3d11.dll"
python3 - "$SHIP/d3d11.dll" "$OUT/nm.ship" "$OUT/gen/dxmt_command.h" "$OUT/gen/dxmt_command.metallib" <<'PY' || fail "could not take dxmt_command (the embedded metallib) out of the committed d3d11.dll"
import struct, sys
dll, nm, header, blob = sys.argv[1:5]
pe = open(dll, "rb").read()
o = struct.unpack_from("<I", pe, 0x3C)[0]
nsec, optsz = struct.unpack_from("<H", pe, o + 6)[0], struct.unpack_from("<H", pe, o + 20)[0]
base = struct.unpack_from("<Q", pe, o + 24 + 24)[0]
secs = [struct.unpack_from("<IIII", pe, o + 24 + optsz + 40 * i + 8) for i in range(nsec)]
def at(va, n):
    rva = va - base
    for vsize, vaddr, rawsize, rawptr in secs:
        if vaddr <= rva and rva + n <= vaddr + min(vsize, rawsize):
            return pe[rawptr + rva - vaddr: rawptr + rva - vaddr + n]
    sys.exit("address 0x%x (+%d) is not in the file" % (va, n))
sym = {}
for line in open(nm):
    f = line.split()
    if len(f) == 3:
        sym[f[2]] = int(f[0], 16)
if "dxmt_command" not in sym or "dxmt_command_len" not in sym:
    sys.exit("no dxmt_command / dxmt_command_len symbol")
n = struct.unpack("<I", at(sym["dxmt_command_len"], 4))[0]
if not 1000 < n < (8 << 20):
    sys.exit("implausible dxmt_command_len %d" % n)
data = at(sym["dxmt_command"], n)
if data[:4] != b"MTLB":
    sys.exit("dxmt_command does not start with a metallib header")
open(blob, "wb").write(data)
with open(header, "w") as h:
    h.write("unsigned char dxmt_command[] = {\n")
    for i in range(0, n, 12):
        h.write("  " + ", ".join("0x%02x" % b for b in data[i:i + 12]) + ("," if i + 12 < n else "") + "\n")
    h.write("};\nunsigned int dxmt_command_len = %d;\n" % n)
print("dxmt_command: %d bytes from the committed d3d11.dll" % n)
PY
METAL_NOTE="metallib taken from upstream's binary"
if command -v xcrun > /dev/null 2>&1 && xcrun -sdk macosx metal --version > /dev/null 2>&1; then
    if (cd "$OUT/gen" && xcrun -sdk macosx metal -o dxmt_command.air -c "$X/dxmt_command.metal" \
            && xcrun -sdk macosx metallib -o dxmt_command.xcrun.metallib dxmt_command.air) > "$OUT/gen/metal.log" 2>&1; then
        if cmp -s "$OUT/gen/dxmt_command.xcrun.metallib" "$OUT/gen/dxmt_command.metallib"; then
            METAL_NOTE="$METAL_NOTE, identical to this Xcode's build of dxmt_command.metal"
        else
            METAL_NOTE="$METAL_NOTE; this Xcode's build of dxmt_command.metal differs ($(wc -c < "$OUT/gen/dxmt_command.xcrun.metallib" | tr -d ' ') vs $(wc -c < "$OUT/gen/dxmt_command.metallib" | tr -d ' ') bytes, compiler version)"
        fi
    fi
fi

# The d3d11 and d3d10 sources with SwapDeviceContextState, on a copy (the
# submodule stays untouched). The patch changes d3d11_context_state.hpp, so
# every d3d11/d3d10 file is compiled from the copy.
cp -R "$D/src/d3d11" "$D/src/d3d10" "$OUT/tree/src/"
python3 "$R/tools/patch-dxmt-context-state-swap.py" "$OUT/tree/src/d3d11" || fail "tools/patch-dxmt-context-state-swap.py did not apply"
grep -q "madeira-bcd: context state swap" "$OUT/tree/src/d3d11/d3d11_context_impl.cpp" || fail "the swap patch left d3d11_context_impl.cpp unchanged"
# madeira-bcd: the DXMT core on a copy as well, with small staging rings
# (run-time switch DXMT_SMALL_RINGS). Opt-in per game, so a missed anchor must
# not cost every game d3d11-src.dll: the copy is left unpatched and the build
# goes on.
cp -R "$D/src/dxmt" "$OUT/tree/src/"
SMALL_RINGS=" + opt-in small staging rings"
python3 "$R/tools/patch-d3d11-src-small-rings.py" "$OUT/tree/src/dxmt" || {
    SMALL_RINGS=""
    echo "::warning::tools/patch-d3d11-src-small-rings.py did not apply: d3d11-src.dll is built without small staging rings (env.DXMT_SMALL_RINGS has no effect)"
}

# Auto HDR is an additional opt-in experiment, so the pristine recipe check
# keeps upstream's metadata and embedded shader. Only the private copy gets
# the new presenter and its matching shader header. Enable in CI/builds with
# D3D11_AUTO_HDR=1; games still opt in separately through their config.
if [ "${D3D11_AUTO_HDR:-0}" = "1" ]; then
    python3 "$R/tools/patch-dxmt-auto-hdr.py" --presenter-dir "$OUT/tree/src/dxmt" || fail "Auto HDR patch failed"
    python3 "$R/tools/patch-dxmt-hdr-live.py" --presenter-dir "$OUT/tree/src/dxmt" || fail "Live HDR patch failed"
    bash "$R/tools/build-auto-hdr-shaders.sh" "$OUT/tree/src/dxmt" "$OUT/tree/gen" || fail "Auto HDR shader failed"
    METAL_NOTE="Auto HDR shader compiled from the patched source (Metal 3.1)"
fi

# DXMT's meson.build: compiler_args, the project defines, buildtype=release
# (-O3, b_ndebug=if-release -> NDEBUG), C++20. -fmacro-prefix-map maps the
# source root away as meson's '-fmacro-prefix-map=../=' does from its build
# directory; it is added per file with the root that file was taken from.
COMMON=(
    -O3 -DNDEBUG
    -Wimplicit-fallthrough -Wno-missing-field-initializers -Wno-unused-parameter
    -Wno-cast-function-type -Wno-unused-private-field -Wno-microsoft-exception-spec
    -Wno-extern-c-compat -Wno-unused-const-variable -Wno-missing-braces -fblocks
    -DNOMINMAX -D_WIN32_WINNT=0xa00 -DDXMT_IOS=1
)
CXXFLAGS=(-std=c++20 "${COMMON[@]}" -DDXMT_PAGE_SIZE=4096)
CFLAGS=("${COMMON[@]}")
# Include paths per meson target (its own directory first, then the
# dependencies' include_directories, then the generated headers). The dxmt
# directory is the one of the root a file is compiled from: the submodule's
# ($X) for the unpatched build, the copy's for d3d11-src.dll.
INC_BASE=(-I"$D/include" -I"$D/libs")
INC_UTIL=(-I"$U" "${INC_BASE[@]}")
INC_PARSER=(-I"$P" "${INC_BASE[@]}")
INC_DXMT=(-I"$U" -I"$D/src/airconv" -I"$D/src/winemetal" "${INC_BASE[@]}" -I"$OUT/gen")

# Compile jobs: one line per object, "<root>\t<source>\t<object>\t<flag set>",
# run JOBS at a time (plain bash 3 job control: N workers over slices).
: > "$OUT/jobs"
job() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$OUT/jobs"; }
compile_one() {  # compile_one <root> <source> <object> <flag set>
    local root="$1" src="$2" obj="$3" set="$4" inc
    case "$set" in
        util)   inc=("${INC_UTIL[@]}") ;;
        parser) inc=("${INC_PARSER[@]}" -Wno-extern-c-compat -Wno-unknown-pragmas) ;;
        dxmt)   inc=(-I"$root/src/dxmt" -I"$root/gen" "${INC_DXMT[@]}") ;;
        d3d11)  inc=(-I"$root/src/d3d11" -I"$G" -I"$root/src/dxmt" -I"$D/src/airconv" -I"$D/src/winemetal" -I"$U" "${INC_BASE[@]}" -I"$OUT/gen") ;;
    esac
    case "$src" in
        *.c) "$CC" "${CFLAGS[@]}" -fmacro-prefix-map="$root/=" -fmacro-prefix-map="$D/=" "${inc[@]}" -c "$src" -o "$obj" ;;
        *)   "$CXX" "${CXXFLAGS[@]}" -fmacro-prefix-map="$root/=" -fmacro-prefix-map="$D/=" "${inc[@]}" -c "$src" -o "$obj" ;;
    esac > "$obj.err" 2>&1 || { echo "$src" >> "$OUT/failed"; return 1; }
}
run_jobs() {
    local w
    rm -f "$OUT/failed"
    for ((w = 0; w < JOBS; w++)); do
        ( awk -F'\t' -v w="$w" -v n="$JOBS" '(NR - 1) % n == w' "$OUT/jobs" | while IFS="$(printf '\t')" read -r root src obj set; do
              compile_one "$root" "$src" "$obj" "$set" || true
          done ) &
    done
    wait
    if [ -s "$OUT/failed" ]; then
        while read -r f; do
            o="$(awk -F'\t' -v s="$f" '$2 == s { print $3; exit }' "$OUT/jobs")"
            echo "=== $f"; grep -m 10 -A3 "error" "$o.err" || tail -20 "$o.err"
        done < "$OUT/failed"
        fail "compiling $(wc -l < "$OUT/failed" | tr -d ' ') file(s) failed, first $(basename "$(head -1 "$OUT/failed")")"
    fi
    while IFS="$(printf '\t')" read -r root src obj set; do
        [ -s "$obj" ] || fail "no object for $(basename "$src")"
    done < "$OUT/jobs"
}

# util_lib (src/util/meson.build: an aarch64 Windows host uses the headless
# wsi files), dxmt_lib (src/dxmt/meson.build) and DXBCParser
# (libs/DXBCParser/meson.build), each in source order.
UTIL_SRC=(util_env.cpp util_string.cpp util_bloom.cpp util_futex.cpp thread.cpp
          com/com_guid.cpp com/com_private_data.cpp config/config.cpp log/log.cpp
          sha1/sha1.c sha1/sha1_util.cpp
          wsi_monitor_headless.cpp wsi_window_headless.cpp wsi_platform_win32.cpp)
DXMT_SRC=(dxmt_format dxmt_names dxmt_command_queue dxmt_command dxmt_capture dxmt_info
          dxmt_device dxmt_buffer dxmt_texture dxmt_context dxmt_dynamic dxmt_staging
          dxmt_hud_state dxmt_allocation dxmt_presenter dxmt_sampler
          dxmt_resource_initializer dxmt_mem_census dxmt_bcn dxmt_shader_cache)
PARSER_SRC=(DXBCUtils BlobContainer ShaderBinary)
# src/d3d11/meson.build: d3d11_src, then d3d10_src.
D3D11_SRC=(d3d11/d3d11_class_linkage d3d11/d3d11_device d3d11/d3d11_input_layout
           d3d11/d3d11_inspection d3d11/d3d11_query d3d11/d3d11_shader
           d3d11/d3d11_state_object d3d11/d3d11_swapchain d3d11/d3d11_texture
           d3d11/d3d11_buffer d3d11/d3d11 d3d11/d3d11_resource_helper
           d3d11/d3d11_resource_view_helper d3d11/d3d11_texture_device
           d3d11/d3d11_texture_linear d3d11/d3d11_texture_dynamic
           d3d11/d3d11_resource_staging d3d11/dxmt_resource_binding d3d11/d3d11_pipeline
           d3d11/d3d11_pipeline_gs d3d11/d3d11_pipeline_ts d3d11/d3d11_enumerable
           d3d11/d3d11_pipeline_cache d3d11/d3d11_context_imm d3d11/d3d11_context_def
           d3d11/d3d11_multithread d3d11/d3d11_fence
           d3d10/d3d10_buffer d3d10/d3d10_device d3d10/d3d10_state_object
           d3d10/d3d10_texture d3d10/d3d10_util d3d10/d3d10_view)
obj_name() { echo "$1" | tr '/.' '__'; }

UTIL_OBJ=(); DXMT_OBJ=(); PLAIN_DXMT_OBJ=(); PARSER_OBJ=(); PLAIN_OBJ=(); PATCHED_OBJ=()
for s in "${UTIL_SRC[@]}"; do
    o="$OUT/obj/util_$(obj_name "$s").o"; job "$D" "$U/$s" "$o" util; UTIL_OBJ+=("$o")
done
for s in "${DXMT_SRC[@]}"; do
    o="$OUT/plain/obj/dxmt_$s.o"; job "$D" "$X/$s.cpp" "$o" dxmt; PLAIN_DXMT_OBJ+=("$o")
    o="$OUT/obj/dxmt_$s.o"; job "$OUT/tree" "$OUT/tree/src/dxmt/$s.cpp" "$o" dxmt; DXMT_OBJ+=("$o")
done
for s in "${PARSER_SRC[@]}"; do
    o="$OUT/obj/parser_$s.o"; job "$D" "$P/$s.cpp" "$o" parser; PARSER_OBJ+=("$o")
done
for s in "${D3D11_SRC[@]}"; do
    o="$OUT/plain/obj/$(obj_name "$s").o"; job "$D" "$D/src/$s.cpp" "$o" d3d11; PLAIN_OBJ+=("$o")
    o="$OUT/obj/$(obj_name "$s").o"; job "$OUT/tree" "$OUT/tree/src/$s.cpp" "$o" d3d11; PATCHED_OBJ+=("$o")
done
echo "compiling $(wc -l < "$OUT/jobs" | tr -d ' ') files, $JOBS at a time"
run_jobs
"$WINDRES" -i "$D/src/d3d11/version.rc" -o "$OUT/obj/version.o" 2>> "$OUT/build.err" || fail "windres version.rc failed"

# Thin archives in source order, as meson makes them ("csrDT"); the DXMT core
# twice, the submodule's for the unpatched DLL and the copy's for d3d11-src.dll.
"$AR" csrDT "$OUT/libutil.a" "${UTIL_OBJ[@]}"
"$AR" csrDT "$OUT/plain/libdxmt.a" "${PLAIN_DXMT_OBJ[@]}"
"$AR" csrDT "$OUT/libdxmt.a" "${DXMT_OBJ[@]}"
"$AR" csrDT "$OUT/libDXBCParser.a" "${PARSER_OBJ[@]}"

# meson: the d3d11/d3d10 objects in order, the resource, the module definition
# file, the dependency libraries in d3d11's dependency order (dxgi, DXBCParser,
# dxmt, winemetal, util), util_dep's -lntdll, -static (libc++ in, UCRT through
# api-ms-win-crt-*), file alignment 4096 and meson's default Windows libraries.
link_dll() {  # link_dll <output> <libdxmt.a> <objects...>
    local out="$1" dxmt="$2"; shift 2
    "$CXX" -shared -o "$out" "$@" "$OUT/obj/version.o" "$D/src/d3d11/d3d11.def" \
        "$OUT/libdxgi.a" "$OUT/libDXBCParser.a" "$dxmt" -L"$OUT" -lwinemetal "$OUT/libutil.a" -lntdll \
        -static -Wl,--file-alignment=4096 \
        -lkernel32 -luser32 -lgdi32 -lwinspool -lshell32 -lole32 -loleaut32 -luuid -lcomdlg32 -ladvapi32 \
        2>> "$OUT/build.err" || { grep -m 20 "error" "$OUT/build.err"; fail "linking $(basename "$out") failed"; }
}
link_dll "$OUT/plain/d3d11.dll" "$OUT/plain/libdxmt.a" "${PLAIN_OBJ[@]}"
link_dll "$OUT/d3d11.dll" "$OUT/libdxmt.a" "${PATCHED_OBJ[@]}"

# The new DLL must export exactly what upstream's does, and import from the
# same DLLs (DXGI.DLL, winemetal.dll, the system and UCRT ones).
exports "$SHIP/d3d11.dll" > "$OUT/exports.upstream"
exports "$OUT/d3d11.dll" > "$OUT/exports.built"
[ -s "$OUT/exports.upstream" ] || fail "could not read the exports of the committed d3d11.dll"
diff "$OUT/exports.upstream" "$OUT/exports.built" > "$OUT/exports.diff" \
    || { cat "$OUT/exports.diff"; fail "its exports differ from the committed d3d11.dll"; }
imports() { "$READOBJ" --coff-imports "$1" | awk '$1 == "Name:" { print $2 }' | sort -u; }
imports "$SHIP/d3d11.dll" > "$OUT/imports.upstream"
imports "$OUT/d3d11.dll" > "$OUT/imports.built"
diff "$OUT/imports.upstream" "$OUT/imports.built" > "$OUT/imports.diff" \
    || { cat "$OUT/imports.diff"; fail "it imports from other DLLs than the committed d3d11.dll"; }
"$READOBJ" --file-headers "$OUT/d3d11.dll" | grep -q "IMAGE_FILE_MACHINE_ARM64EC" || fail "the result is not an ARM64EC image"
LC_ALL=C grep -aqF "$MARK" "$OUT/d3d11.dll" || fail "the SwapDeviceContextState patch is not in the result"
if ! LC_ALL=C grep -aqF "$MARK" "$SHIP/d3d11.dll"; then
    LC_ALL=C grep -aqF "$MARK" "$OUT/plain/d3d11.dll" && fail "the unpatched build carries the patch marker: the submodule is not pristine"
fi
if [ -n "$SMALL_RINGS" ]; then
    LC_ALL=C grep -aqF "[small-rings] madeira-bcd" "$OUT/d3d11.dll" || fail "the small staging rings patch is not in the result"
fi
if ! LC_ALL=C grep -aqF "[small-rings] madeira-bcd" "$SHIP/d3d11.dll"; then
    LC_ALL=C grep -aqF "[small-rings] madeira-bcd" "$OUT/plain/d3d11.dll" && fail "the unpatched build carries the small rings marker: the submodule is not pristine"
fi
if [ "${D3D11_AUTO_HDR:-0}" = "1" ]; then
    LC_ALL=C grep -aqF "[auto-hdr] requested" "$OUT/d3d11.dll" || fail "Auto HDR presenter is missing"
    if LC_ALL=C grep -aqF "[auto-hdr] requested" "$OUT/plain/d3d11.dll"; then
        fail "Auto HDR leaked into the pristine recipe check"
    fi
fi
LC_ALL=C grep -aqF "$VER" "$OUT/d3d11.dll" || fail "the DXMT version string is not in the result"

# The recipe check: the unpatched link against upstream's binary. Same
# symbols at the same addresses = same code and layout (only the timestamp and
# the build id can differ). Reported, never fatal.
sort "$OUT/nm.ship" > "$OUT/plain/nm.upstream"
"$NM" --defined-only "$OUT/plain/d3d11.dll" | sort > "$OUT/plain/nm.built"
TOTAL=$(wc -l < "$OUT/plain/nm.upstream" | tr -d ' ')
BUILT=$(wc -l < "$OUT/plain/nm.built" | tr -d ' ')
SAME=$(comm -12 "$OUT/plain/nm.upstream" "$OUT/plain/nm.built" | wc -l | tr -d ' ')
awk '{ print $2, $3 }' "$OUT/plain/nm.upstream" | sort > "$OUT/plain/names.upstream"
awk '{ print $2, $3 }' "$OUT/plain/nm.built" | sort > "$OUT/plain/names.built"
NAMES=$(comm -12 "$OUT/plain/names.upstream" "$OUT/plain/names.built" | wc -l | tr -d ' ')
if [ "$TOTAL" -gt 0 ] && [ "$SAME" = "$TOTAL" ] && [ "$BUILT" = "$TOTAL" ]; then
    MATCH="reproduces upstream's d3d11.dll without the patch ($TOTAL symbols at the same addresses)"
else
    MATCH="does NOT reproduce upstream's d3d11.dll without the patch ($SAME of $TOTAL symbols at the same address, $NAMES of $TOTAL names present; built has $BUILT)"
    echo "::warning::d3d11.dll source build $MATCH: the committed binary was built from other sources, flags or toolchain, so d3d11-src.dll may differ from it in more than SwapDeviceContextState -- compare before relying on it ($OUT/plain)"
fi

if [ "${D3D11_NO_SHIP:-0}" = "1" ]; then
    echo "d3d11.dll built at $OUT/d3d11.dll (not shipped, D3D11_NO_SHIP=1; DXMT $VER$SMALL_RINGS, $METAL_NOTE); the recipe $MATCH"
    exit 0
fi
cp "$OUT/d3d11.dll" "$DEST.tmp" && mv -f "$DEST.tmp" "$DEST" || fail "copying into the bundle failed"
echo "::notice::d3d11-src.dll built from DXMT $VER with SwapDeviceContextState$SMALL_RINGS ($(wc -c < "$DEST" | tr -d ' ') bytes, exports and imported DLLs = upstream's; $METAL_NOTE) and shipped next to upstream's d3d11.dll, which stays the default (env.MADEIRA_D3D11_SRC = 1 selects it); the recipe $MATCH"
