#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
#
# Build DXMT's Direct3D 9 frontend (dxmt/src/d3d9) for arm64ec and ship it as
# app/Madeira/arm64ec-windows/d3d9-dxmt.dll, NEXT TO the d3d9.dll that is there
# (Wine's own, drawing through wined3d's OpenGL backend).
#
# Why: a 64-bit Direct3D 9 game loads C:\windows\sysx64\d3d9.dll, Wine's. Wine's
# d3d9 needs wined3d's OpenGL backend and BCD has no OpenGL, so
# "wined3d_caps_gl_ctx_create Failed to find a suitable pixel format" /
# "Direct3DCreate9 called -> d3d9_init FAILED" ends every such start (Darksiders II
# Deathinitive Edition: "Could not initialize the graphics subsystem, probably because
# the resolution 1112 x 832 is not supported for fullscreen display"; PICO PARK).
# The 32-bit games have DXMT's D3D9 frontend instead (d3d9-emulated.dll, i386,
# build/wine-i386/build.sh); this is the same frontend, from the same sources, compiled
# for arm64ec and loaded like the shipped DXMT d3d11.dll / dxgi.dll.
#
# Wine's d3d9.dll stays the default for every game. app/Madeira/WineProcessBridge.m
# links d3d9-dxmt.dll in as C:\windows\sysx64\d3d9.dll (and system32 in an ARM64EC
# session) only when env.MADEIRA_D3D9_X64 = dxmt is set (the game's own file, or
# bcd.cfg); the farms are relinked from the bundle every session, so dropping the line
# restores Wine's d3d9.dll at the next start. The 32-bit shim and d3d9-emulated.dll are
# neither touched nor rebuilt here: nothing in this script writes outside OUT and DEST.
#
# Built the way DXMT's meson build builds the d3d9 target for an arm64ec host
# (buildtype=release: -O3 -DNDEBUG, the project's flags and defines, the util sources of
# an aarch64 Windows host -- the headless WSI files --, the DXMT core in a thin archive,
# the d3d9 sources in meson.build order, d3d9.def, version.rc, meson's default Windows
# libraries), with the llvm-mingw upstream used (20260421), against an import library for
# winemetal.dll derived from the binary that ships beside it (the way build-d3d11-dll.sh
# does). Run it AFTER the DXMT patch steps and the i386 farm: it compiles the tree as it
# is, the same tree d3d9-emulated.dll was built from, so both are one frontend. The
# metallib the DXMT core embeds (dxmt_command) is taken byte for byte from the committed
# d3d11.dll (src/dxmt/meson.build builds it with xcrun metal; dxmt_command.metal has not
# changed since before that binary was built, the script checks that when it can).
#
# Any failure leaves the bundle untouched (Wine's d3d9.dll ships alone), removes no file
# and exits 1 with a ::warning::; the workflow step continues on error.
#
#   D3D9X64_OUT=<dir>    work directory (default build/dxmt-d3d9-x64)
#   D3D9X64_NO_SHIP=1    build and check, but do not copy into the bundle
#   DXMT_DIR=<dir>       the DXMT tree (default: dxmt in the repository root)
#   SHIP_DIR=<dir>       where the committed arm64ec DLLs are (default app/Madeira/arm64ec-windows)
#   MINGW=<dir>          llvm-mingw bin directory (default: the CI toolchain)
#   JOBS=<n>             parallel compiles (default: CPU count)
# Run from the repository root.
set -eu
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
D="${DXMT_DIR:-$R/dxmt}"
U="$D/src/util"
X="$D/src/dxmt"
N="$D/src/d3d9"
SHIP="${SHIP_DIR:-$R/app/Madeira/arm64ec-windows}"
OUT="${D3D9X64_OUT:-$R/build/dxmt-d3d9-x64}"
DEST="$SHIP/d3d9-dxmt.dll"
CXX="$MINGW/arm64ec-w64-mingw32-clang++"
CC="$MINGW/arm64ec-w64-mingw32-clang"
WINDRES="$MINGW/arm64ec-w64-mingw32-windres"
AR="$MINGW/llvm-ar"
READOBJ="$MINGW/llvm-readobj"
NM="$MINGW/llvm-nm"
JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"

fail() {
    echo "::warning::d3d9-dxmt.dll NOT built ($*). Wine's d3d9.dll ships alone; env.MADEIRA_D3D9_X64 = dxmt has no effect in this build."
    exit 1
}

[ -x "$CXX" ] || fail "no arm64ec clang++ at $CXX: llvm-mingw missing"
[ -f "$N/d3d9_device.cpp" ] && [ -f "$N/d3d9.def" ] || fail "dxmt/src/d3d9 is not checked out"
[ -f "$X/dxmt_command.cpp" ] && [ -f "$U/wsi_window_headless.cpp" ] || fail "dxmt/src/dxmt or dxmt/src/util is not checked out"
for f in d3d11.dll winemetal.dll; do
    [ -f "$SHIP/$f" ] || fail "the committed arm64ec $f is missing"
done

rm -rf "$OUT"
mkdir -p "$OUT/obj" "$OUT/gen"
REV="$(git -C "$D" rev-parse --short HEAD 2>/dev/null || echo unknown)"

exports() { "$READOBJ" --coff-exports "$1" | awk '$1 == "Name:" && $2 != "" { print $2 }' | sort -u; }

# winemetal.dll's import library, from the binary that ships beside it.
{ echo "LIBRARY winemetal.dll"; echo "EXPORTS"; exports "$SHIP/winemetal.dll"; } > "$OUT/winemetal.def"
"$MINGW/llvm-dlltool" -m arm64ec -d "$OUT/winemetal.def" -l "$OUT/libwinemetal.a" || fail "llvm-dlltool failed for winemetal.dll"

# version.h (vcs_tag): the describe of the DXMT checkout, as meson writes it.
VER="$(git -C "$D" describe --always 2>/dev/null || echo unknown)"
sed "s/@VCS_TAG@/$VER/" "$D/version.h.in" > "$OUT/gen/version.h"

# dxmt_command.h: the embedded metallib, out of the committed d3d11.dll (the same
# extraction build-d3d11-dll.sh does; it needs no Metal compiler).
"$NM" --defined-only "$SHIP/d3d11.dll" > "$OUT/nm.ship" || fail "llvm-nm cannot read the committed d3d11.dll"
python3 - "$SHIP/d3d11.dll" "$OUT/nm.ship" "$OUT/gen/dxmt_command.h" "$OUT/gen/dxmt_command.metallib" "$X/dxmt_command.metal" <<'PY' || fail "could not take dxmt_command (the embedded metallib) out of the committed d3d11.dll"
import struct, sys
dll, nm, header, blob, metal = sys.argv[1:6]
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
# The pin's dxmt_command.metal must not have grown a function the committed d3d11.dll's library lacks (the DXMT core asks
# its library for them by name when a device is made): every vertex, fragment and kernel function the source defines has
# to be in the library taken out of the binary.
import re
names = re.findall(r"\[\[(?:vertex|fragment|kernel)\]\][^\n(]*?\b(\w+)\s*\(", open(metal).read())
missing = sorted({x for x in names if x.encode() not in data})
if not names or missing:
    sys.exit("the committed d3d11.dll's metallib lacks %s: dxmt_command.metal changed since it was built" % (missing or "every function (no name found in dxmt_command.metal)"))
print("dxmt_command: all %d functions of dxmt_command.metal are in it" % len(set(names)))
open(blob, "wb").write(data)
with open(header, "w") as h:
    h.write("unsigned char dxmt_command[] = {\n")
    for i in range(0, n, 12):
        h.write("  " + ", ".join("0x%02x" % b for b in data[i:i + 12]) + ("," if i + 12 < n else "") + "\n")
    h.write("};\nunsigned int dxmt_command_len = %d;\n" % n)
print("dxmt_command: %d bytes from the committed d3d11.dll" % n)
PY
METAL_NOTE="metallib taken from the committed d3d11.dll"
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

# A patched presenter must never reuse the old embedded present shader.
bash "$R/tools/build-auto-hdr-shaders.sh" "$X" "$OUT/gen" || fail "Auto HDR shader failed"
if grep -qF 'madeira-bcd: auto HDR' "$X/dxmt_command.metal"; then
    METAL_NOTE="Auto HDR shader compiled from the patched source (Metal 3.1)"
fi

# DXMT's meson.build: compiler_args, the project defines, buildtype=release
# (-O3, b_ndebug=if-release -> NDEBUG), C++20. -fmacro-prefix-map maps the source root away
# as meson's '-fmacro-prefix-map=../=' does from its build directory. An arm64ec host is
# not x86, so none of the -msse flags apply; DXMT_IOS is defined for every Windows host.
COMMON=(
    -O3 -DNDEBUG
    -Wimplicit-fallthrough -Wno-missing-field-initializers -Wno-unused-parameter
    -Wno-cast-function-type -Wno-unused-private-field -Wno-microsoft-exception-spec
    -Wno-extern-c-compat -Wno-unused-const-variable -Wno-missing-braces -fblocks
    -DNOMINMAX -D_WIN32_WINNT=0xa00 -DDXMT_IOS=1
)
CXXFLAGS=(-std=c++20 "${COMMON[@]}" -DDXMT_PAGE_SIZE=4096)
CFLAGS=("${COMMON[@]}")
INC_BASE=(-I"$D/include" -I"$D/libs")
INC_UTIL=(-I"$U" "${INC_BASE[@]}")
INC_DXMT=(-I"$X" -I"$U" -I"$D/src/airconv" -I"$D/src/winemetal" "${INC_BASE[@]}" -I"$OUT/gen")
INC_D3D9=(-I"$N" "${INC_BASE[@]}" -I"$X" -I"$U" -I"$D/src/winemetal" -I"$D/src/airconv" -I"$OUT/gen")

: > "$OUT/jobs"
job() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$OUT/jobs"; }
compile_one() {  # compile_one <source> <object> <flag set>
    local src="$1" obj="$2" set="$3" inc
    case "$set" in
        util) inc=("${INC_UTIL[@]}") ;;
        dxmt) inc=("${INC_DXMT[@]}") ;;
        d3d9) inc=("${INC_D3D9[@]}") ;;
    esac
    case "$src" in
        *.c) "$CC" "${CFLAGS[@]}" -fmacro-prefix-map="$D/=" "${inc[@]}" -c "$src" -o "$obj" ;;
        *)   "$CXX" "${CXXFLAGS[@]}" -fmacro-prefix-map="$D/=" "${inc[@]}" -c "$src" -o "$obj" ;;
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

# util_lib (src/util/meson.build: an aarch64 Windows host uses the headless wsi files),
# dxmt_lib (src/dxmt/meson.build) and the d3d9 sources (src/d3d9/meson.build), each in
# meson.build order.
UTIL_SRC=(util_env.cpp util_string.cpp util_bloom.cpp util_futex.cpp thread.cpp
          com/com_guid.cpp com/com_private_data.cpp config/config.cpp log/log.cpp
          sha1/sha1.c sha1/sha1_util.cpp
          wsi_monitor_headless.cpp wsi_window_headless.cpp wsi_platform_win32.cpp)
DXMT_SRC=(dxmt_format dxmt_names dxmt_command_queue dxmt_command dxmt_capture dxmt_info
          dxmt_device dxmt_buffer dxmt_texture dxmt_context dxmt_dynamic dxmt_staging
          dxmt_hud_state dxmt_allocation dxmt_presenter dxmt_sampler
          dxmt_resource_initializer dxmt_mem_census dxmt_bcn dxmt_shader_cache)
D3D9_SRC=(d3d9 d3d9_buffer d3d9_census d3d9_clear_quad d3d9_cube_texture d3d9_device d3d9_format
          d3d9_fvf d3d9_interface d3d9_mem d3d9_query d3d9_shader d3d9_shader_scan
          d3d9_state_block d3d9_state_defaults d3d9_surface d3d9_swapchain d3d9_texture
          d3d9_validation d3d9_vertex_declaration d3d9_volume d3d9_volume_texture)
obj_name() { echo "$1" | tr '/.' '__'; }

UTIL_OBJ=(); DXMT_OBJ=(); D3D9_OBJ=()
for s in "${UTIL_SRC[@]}"; do
    o="$OUT/obj/util_$(obj_name "$s").o"; job "$U/$s" "$o" util; UTIL_OBJ+=("$o")
done
for s in "${DXMT_SRC[@]}"; do
    o="$OUT/obj/dxmt_$s.o"; job "$X/$s.cpp" "$o" dxmt; DXMT_OBJ+=("$o")
done
for s in "${D3D9_SRC[@]}"; do
    [ -f "$N/$s.cpp" ] || fail "$s.cpp is not in dxmt/src/d3d9: the source list of this script is out of date (src/d3d9/meson.build)"
    o="$OUT/obj/$s.o"; job "$N/$s.cpp" "$o" d3d9; D3D9_OBJ+=("$o")
done
# BCD's own addition, not in DXMT: the [fault-dump] report for a 64-bit guest's access violation (tools/d3d9-x64-fault-dump.cpp, ajan17-ds2).
# It is compiled into this DLL and nowhere else (not into d3d9-emulated.dll, not into the DXMT sources), and registers its handler only
# in a game whose environment has MADEIRA_D3D9_LAST=1. It is linked after the DXMT objects, so the list of DXMT sources above and the
# meson.build comparison below are untouched.
FAULT_DUMP_SRC="$R/tools/d3d9-x64-fault-dump.cpp"
[ -f "$FAULT_DUMP_SRC" ] && [ -f "$R/tools/d3d9-x64-fault-dump.hpp" ] || fail "tools/d3d9-x64-fault-dump.cpp is missing"
FAULT_DUMP_OBJ="$OUT/obj/bcd_d3d9_x64_fault_dump.o"
job "$FAULT_DUMP_SRC" "$FAULT_DUMP_OBJ" d3d9
# A source added to src/d3d9/meson.build must be added above, or the DLL would lack it.
LISTED="$(sed -n "/^d3d9_src = \[/,/^\]/p" "$N/meson.build" | grep -o "'[a-z0-9_]*\.cpp'" | tr -d "'" | sort | tr '\n' ' ')"
HAVE="$(for s in "${D3D9_SRC[@]}"; do echo "$s.cpp"; done | sort | tr '\n' ' ')"
[ "$LISTED" = "$HAVE" ] || fail "src/d3d9/meson.build lists other sources than this script ($LISTED)"
echo "compiling $(wc -l < "$OUT/jobs" | tr -d ' ') files, $JOBS at a time (DXMT $VER)"
run_jobs
D3D9_OBJ+=("$FAULT_DUMP_OBJ")
"$WINDRES" -i "$N/version.rc" -o "$OUT/obj/version.o" 2>> "$OUT/build.err" || fail "windres version.rc failed"

# Thin archives in source order, as meson makes them ("csrDT"), so lld pulls the same
# members in the same order.
"$AR" csrDT "$OUT/libutil.a" "${UTIL_OBJ[@]}"
"$AR" csrDT "$OUT/libdxmt.a" "${DXMT_OBJ[@]}"

# meson: the d3d9 objects in order, the resource, the module definition file, the
# dependency libraries in d3d9's dependency order (util, winemetal, dxmt: one group),
# util_dep's -lntdll, -static (libc++ in, UCRT through api-ms-win-crt-*), file alignment
# 4096 and meson's default Windows libraries.
"$CXX" -shared -o "$OUT/d3d9.dll" "${D3D9_OBJ[@]}" "$OUT/obj/version.o" "$N/d3d9.def" \
    "$OUT/libutil.a" "$OUT/libdxmt.a" -L"$OUT" -lwinemetal -lntdll \
    -static -Wl,--file-alignment=4096 \
    -lkernel32 -luser32 -lgdi32 -lwinspool -lshell32 -lole32 -loleaut32 -luuid -lcomdlg32 -ladvapi32 \
    2>> "$OUT/build.err" || { grep -m 20 "error\|undefined" "$OUT/build.err"; fail "linking d3d9.dll failed"; }

# Padded with zeros to SizeOfImage + 0x10000 when the file is shorter, as tools/build-wine-extra-dlls.sh pads the
# DLLs it ships (the iOS mapping path reads up to the image's size from the file; the DXMT DLLs that carry debug
# information are longer than that anyway, this one is built without any). The COFF symbol table stays, so a crash
# backtrace in the log names this module's functions.
python3 - "$OUT/d3d9.dll" <<'PY' || fail "padding the image failed"
import struct, sys
p = sys.argv[1]; d = open(p, 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
target = struct.unpack_from('<I', d, pe + 24 + 56)[0] + 0x10000
if len(d) < target:
    open(p, 'ab').write(b'\0' * (target - len(d)))
PY

# The result: an ARM64EC image, exporting exactly what d3d9.def names, and importing only
# from winemetal.dll and the system and UCRT ones -- never from dxgi.dll or d3d11.dll (a
# game that loads d3d9.dll must not pull in a second graphics stack).
"$READOBJ" --file-headers "$OUT/d3d9.dll" | grep -q "IMAGE_FILE_MACHINE_ARM64EC" || fail "the result is not an ARM64EC image"
awk 'NR > 2 && $1 != "" { print $1 }' "$N/d3d9.def" | sort -u > "$OUT/exports.expected"
exports "$OUT/d3d9.dll" > "$OUT/exports.built"
diff "$OUT/exports.expected" "$OUT/exports.built" > "$OUT/exports.diff" \
    || { cat "$OUT/exports.diff"; fail "its exports differ from src/d3d9/d3d9.def"; }
"$READOBJ" --coff-imports "$OUT/d3d9.dll" | awk '$1 == "Name:" { print $2 }' | sort -u > "$OUT/imports.built"
grep -qix "winemetal.dll" "$OUT/imports.built" || fail "it does not import winemetal.dll"
if grep -iE '^(dxgi|d3d11|d3d10core|d3d12|opengl32|wined3d)\.dll$' "$OUT/imports.built"; then
    fail "it imports another graphics DLL"
fi
LC_ALL=C grep -aqF "$VER" "$OUT/d3d9.dll" || fail "the DXMT version string is not in the result"
LC_ALL=C grep -aqF "rev=ajan17-ds2" "$OUT/d3d9.dll" || fail "the [fault-dump] report (tools/d3d9-x64-fault-dump.cpp) is not in the result"

if [ "${D3D9X64_NO_SHIP:-0}" = "1" ]; then
    echo "d3d9.dll built at $OUT/d3d9.dll (not shipped, D3D9X64_NO_SHIP=1; DXMT $VER, $METAL_NOTE)"
    exit 0
fi
cp "$OUT/d3d9.dll" "$DEST.tmp" && mv -f "$DEST.tmp" "$DEST" || fail "copying into the bundle failed"
echo "::notice::d3d9-dxmt.dll built from DXMT $VER for arm64ec ($(wc -c < "$DEST" | tr -d ' ') bytes, exports = d3d9.def, imports winemetal.dll and system DLLs only; $METAL_NOTE) and shipped next to Wine's d3d9.dll, which stays the default (env.MADEIRA_D3D9_X64 = dxmt selects it)"
