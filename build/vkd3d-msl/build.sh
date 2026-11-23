#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
#
# Builds libbcd-dxbc-msl.dylib: Wine's vkd3d-shader (DXBC -> Metal Shading Language) with BCD's patch and BCD's front door
# (bcd_dxbc_msl.c), exporting bcd_dxbc_to_msl and bcd_dxbc_free only. See build/vkd3d-msl/README.md.
#
#   build.sh <wine-dir> <out-dir> [ios|macos]
#
#   <wine-dir>  Wine's tree (the `wine` submodule); only libs/vkd3d is read, and it is copied, never changed: other steps patch
#               some of vkd3d's HLSL files in place.
#   <out-dir>   gets libbcd-dxbc-msl.dylib, libbcd-dxbc-msl.LICENSE.txt and libbcd-dxbc-msl.SOURCE.txt.
#   ios         (default) arm64, iPhoneOS SDK, minimum iOS 26.0, install name @rpath/libbcd-dxbc-msl.dylib.
#   macos       the build machine's own architecture, for build/vkd3d-msl's check (tests/host/check-vkd3d-msl.py).
#
# Only DXBC is translated, so vkd3d's HLSL compiler and preprocessor are left out (host/hlsl_stubs.c stands in for the few symbols
# the rest names): no bison, no flex, no Homebrew and no network are needed, only clang, patch, cp and the SDK.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
lib=libbcd-dxbc-msl

die() { echo "build.sh: $*" >&2; exit 1; }

[ "$#" -ge 2 ] && [ "$#" -le 3 ] || die "usage: build.sh <wine-dir> <out-dir> [ios|macos]"
wine="$1"
out="$2"
variant="${3:-ios}"
case "$variant" in ios | macos) ;; *) die "unknown variant '$variant' (ios or macos)" ;; esac
[ -f "$wine/libs/vkd3d/libs/vkd3d-shader/msl.c" ] || die "no vkd3d in '$wine' (libs/vkd3d/libs/vkd3d-shader/msl.c is missing)"
[ -f "$wine/libs/vkd3d/config.h" ] || die "'$wine/libs/vkd3d/config.h' is missing"

patches=("$here"/0001-*.patch)
[ -f "${patches[0]}" ] || die "BCD's vkd3d patch ($here/0001-*.patch) is missing"

case "$variant" in
ios)
    cc=(xcrun -sdk iphoneos clang -arch arm64 -miphoneos-version-min=26.0)
    ;;
macos)
    cc=(xcrun -sdk macosx clang -mmacosx-version-min=13.0)
    ;;
esac
"${cc[@]}" --version >/dev/null 2>&1 || die "no clang for $variant (xcrun -sdk $([ "$variant" = ios ] && echo iphoneos || echo macosx) clang)"

work="$(mktemp -d "${TMPDIR:-/tmp}/bcd-dxbc-msl.XXXXXX")"
trap 'rm -rf "$work"' EXIT

# 1. our own copy of vkd3d, patched there
mkdir -p "$work/src/libs"
cp -R "$wine/libs/vkd3d" "$work/src/libs/vkd3d"
for p in "${patches[@]}"; do
    # -f: never ask (an already patched tree is refused, not questioned); stdin is closed for the same reason
    patch -p1 -f -s --no-backup-if-mismatch --dry-run -d "$work/src" -i "$p" </dev/null >/dev/null 2>&1 \
        || die "$(basename "$p") does not apply to $wine/libs/vkd3d"
    patch -p1 -f -s --no-backup-if-mismatch -d "$work/src" -i "$p" </dev/null || die "$(basename "$p") failed half way (see above)"
done
root="$work/src/libs/vkd3d"

# vkd3d-shader names two of Wine's own headers (the list and the red-black tree); the rest of Wine's include/ stays out of reach,
# so that host/windows.h is the only Windows header it finds
mkdir -p "$work/wine-headers/wine"
for h in list.h rbtree.h; do
    [ -f "$wine/include/wine/$h" ] || die "$wine/include/wine/$h is missing"
    cp "$wine/include/wine/$h" "$work/wine-headers/wine/$h"
done

# 2. compile: vkd3d-shader without the HLSL compiler, plus the stubs and BCD's file
vkd3d_files=(
    libs/vkd3d-common/debug.c libs/vkd3d-common/error.c libs/vkd3d-common/memory.c libs/vkd3d-common/utf8.c
    libs/vkd3d-shader/checksum.c libs/vkd3d-shader/d3d_asm.c libs/vkd3d-shader/d3dbc.c libs/vkd3d-shader/dxbc.c
    libs/vkd3d-shader/dxil.c libs/vkd3d-shader/fx.c libs/vkd3d-shader/glsl.c libs/vkd3d-shader/hlsl.c
    libs/vkd3d-shader/hlsl_constant_ops.c libs/vkd3d-shader/ir.c libs/vkd3d-shader/msl.c libs/vkd3d-shader/spirv.c
    libs/vkd3d-shader/tpf.c libs/vkd3d-shader/vkd3d_shader_main.c
)
for f in "${vkd3d_files[@]}"; do
    [ -f "$root/$f" ] || die "$wine/libs/vkd3d/$f is missing (is this the vkd3d 1.19 that BCD's patch is for?)"
done

common=(-O2 -fPIC -fvisibility=hidden -ffile-prefix-map="$work/src=vkd3d" -ffile-prefix-map="$here=bcd")
vkd3d_flags=("${common[@]}" -I "$here/host" -I "$work/wine-headers" -I "$root" -I "$root/include" -I "$root/include/private" -I "$root/libs/vkd3d-shader"
    -DLIBVKD3D_SHADER_SOURCE -DVKD3D_NO_WIN32_TYPES -DVKD3D_NO_VULKAN_H -DVKD3D_SHADER_UNSUPPORTED_MSL
    -include "$root/config.h" -include "$here/host/host-config.h")
bcd_flags=("${common[@]}" -Wall -Wextra -Werror -I "$root/include" -DVKD3D_NO_WIN32_TYPES -DVKD3D_NO_VULKAN_H)

mkdir -p "$work/obj"
jobs_n="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
pids=()
names=()
running=0
fail=0
reap() {
    local i
    [ "${#pids[@]}" -gt 0 ] || return 0
    for i in "${!pids[@]}"; do
        if wait "${pids[$i]}"; then :; else
            echo "build.sh: compiling ${names[$i]} failed:" >&2
            head -n 40 "$work/obj/$(basename "${names[$i]}" .c).log" >&2
            fail=1
        fi
    done
    pids=()
    names=()
    running=0
}
compile() { # <source> <flags...>: the log goes to a file, shown only when the compile fails
    local src="$1" obj log
    shift
    obj="$work/obj/$(basename "$src" .c).o"   # the file names are unique among the sources below
    log="${obj%.o}.log"
    "${cc[@]}" "$@" -c "$src" -o "$obj" >"$log" 2>&1 &
    pids+=("$!")
    names+=("$src")
    running=$((running + 1))
    if [ "$running" -ge "$jobs_n" ]; then reap; fi
}
for f in "${vkd3d_files[@]}"; do
    compile "$root/$f" "${vkd3d_flags[@]}"
done
compile "$here/host/hlsl_stubs.c" "${vkd3d_flags[@]}"
compile "$here/bcd_dxbc_msl.c" "${bcd_flags[@]}"
reap
[ "$fail" -eq 0 ] || die "compile failed"

# 3. link: only the two API functions are exported
mkdir -p "$out"
out="$(cd "$out" && pwd)"
printf '_bcd_dxbc_to_msl\n_bcd_dxbc_free\n' >"$work/exports.txt"
"${cc[@]}" -dynamiclib -O2 -fvisibility=hidden -install_name "@rpath/$lib.dylib" \
    -Wl,-exported_symbols_list,"$work/exports.txt" -Wl,-dead_strip \
    "$work"/obj/*.o -o "$out/$lib.dylib" || die "link failed"

# 4. the licence and where the source is, beside the library
{
    cat "$wine/libs/vkd3d/COPYING"
    if [ -f "$wine/COPYING.LIB" ]; then
        printf '\n\n----------------------------------------------------------------------------------------------------------\n'
        printf 'The GNU Lesser General Public License version 2.1, as shipped with Wine (COPYING.LIB):\n\n'
        cat "$wine/COPYING.LIB"
    fi
} >"$out/$lib.LICENSE.txt"
version="$(sed -n 's/^#define PACKAGE_VERSION "\(.*\)"/\1/p' "$wine/libs/vkd3d/config.h" | head -n 1)"
commit=unknown
if top="$(git -C "$wine" rev-parse --show-toplevel 2>/dev/null)" && [ "$(cd "$top" && pwd -P)" = "$(cd "$wine" && pwd -P)" ]; then
    commit="$(git -C "$wine" rev-parse HEAD 2>/dev/null || echo unknown)"
fi
patch_names="$(for p in "${patches[@]}"; do printf 'build/vkd3d-msl/%s ' "$(basename "$p")"; done)"
echo "$lib.dylib is vkd3d-shader ${version:-unknown} (LGPL-2.1-or-later) from Wine's libs/vkd3d at Wine commit $commit, with ${patch_names}applied, built with BCD's build/vkd3d-msl/bcd_dxbc_msl.c, build/vkd3d-msl/host/ and build/vkd3d-msl/build.sh (all in the BCD repository)." \
    >"$out/$lib.SOURCE.txt"

echo "built $out/$lib.dylib ($variant)"
