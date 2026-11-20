#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# Portions copyright 2026 intraducine: code taken from https://github.com/willfaust/Madeira (pull request #476, tools/build/prepare-graphics.sh)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
#
# Build Mesa's KosmicKrisp (Vulkan 1.4 on Metal 4) for iOS arm64 -> app/Madeira/gl/libvulkan_kosmickrisp.dylib, with its
# licence at app/Madeira/licenses/KosmicKrisp-Mesa-license.rst. A game uses it only when its own file says
# `vulkan = kosmickrisp` (docs/VULKAN.md); the library needs iOS 26 and a GPU of the Metal 4 family.
#
# This is BCD's driver of Madeira's two recipes, which do the compiling:
#   KK_PROFILE=playport (default)  build-playport-kosmickrisp.sh: Mesa at commit b39d173c (26.3.0-devel, fetched by
#                                  fetch-playport.py with its SHA-256) + the Playport authors' 31 patches and BCD's 0032 to 0035 (playport-patches/):
#                                  geometry shaders, transform feedback, the driver's shader cache, the iOS presentation fixes.
#   KK_PROFILE=release             build-kosmickrisp.sh: Mesa 26.2.4 (the release tarball, by its SHA-256) + the six
#                                  patches of kosmickrisp-patches/.
# What it adds to them: the sources are fetched here by checksum or commit into toolchains/ (where CI keeps downloads), and the
# three things Mesa's OpenCL kernel compiler (mesa_clc) needs on the build machine and Homebrew does not give ready-made are
# built from pinned sources: SPIRV-Tools and SPIRV-LLVM-Translator (the one matching the machine's LLVM). The Python tools
# are toolchains/mesa-venv, shared with build.sh (OSMesa).
#
# Build machine: Xcode with the iPhoneOS 26 SDK, cmake, ninja, git, python3, and LLVM 21 or 22 with its clang libraries and
# opencl-c.h (Homebrew's `llvm`; KK_LLVM_CONFIG names another llvm-config).
#
# Usage: build/mesa-ios/build-kosmickrisp-bcd.sh          (env: KK_PROFILE, KK_LLVM_CONFIG, KK_TOOLCHAINS, JOBS)
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$R/build/mesa-ios"
T="${KK_TOOLCHAINS:-$R/toolchains}"
PROFILE="${KK_PROFILE:-playport}"
VENV="${MESA_VENV:-$T/mesa-venv}"
OUT="$R/app/Madeira/gl"
# madeira-bcd (U6 counter-review L6): at most 4 jobs unless JOBS says otherwise; the step runs inside BCD's whole build on an 8-core
# build machine, and the host libraries take about 17 minutes with 6.
JOBS="${JOBS:-$(n=$(sysctl -n hw.ncpu 2>/dev/null || echo 4); [ "$n" -lt 4 ] && echo "$n" || echo 4)}"
export JOBS
MESA_RELEASE=26.2.4
MESA_RELEASE_SHA256=bce5f7fbebb934373b86c999a064d52fb5065878dc57f287f95346648ec832e9
MESA_PLAYPORT=b39d173ca9369c19fda2635be6655d308067e52d   # fetch-playport.py holds the archive's SHA-256
SPIRV_TOOLS=ef96ed763b43b59b33b31b362f09a02b729fa1c9     # v2026.4
SPIRV_HEADERS=496543121ce6419f23d6fa5d7194ba66c36212d2   # the revision SPIRV-Tools v2026.4 names in its DEPS

case "$PROFILE" in
  playport|release) ;;
  *) echo "KK_PROFILE is playport or release, not '$PROFILE'" >&2; exit 2 ;;
esac

# The machine's LLVM, and the SPIRV-LLVM-Translator that matches it (the translator is built against one LLVM major version).
LLVM_CONFIG="${KK_LLVM_CONFIG:-}"
if [ -z "$LLVM_CONFIG" ]; then
    if command -v brew >/dev/null 2>&1 && [ -x "$(brew --prefix llvm 2>/dev/null)/bin/llvm-config" ]; then
        LLVM_CONFIG="$(brew --prefix llvm)/bin/llvm-config"
    else
        LLVM_CONFIG="$(command -v llvm-config || true)"
    fi
fi
[ -n "$LLVM_CONFIG" ] && [ -x "$LLVM_CONFIG" ] || { echo "llvm-config not found: install Homebrew's llvm or set KK_LLVM_CONFIG" >&2; exit 1; }
LLVM_PREFIX="$("$LLVM_CONFIG" --prefix)"
LLVM_MAJOR="$("$LLVM_CONFIG" --version | cut -d. -f1)"
case "$LLVM_MAJOR" in
  21) TRANSLATOR=29758b55816c14abb3e4142d42aca7a95bf46710 ;;   # Madeira's pin for LLVM 21 (tools/build/prepare-graphics.sh); not built by BCD yet
  22) TRANSLATOR=a2c6155836cfb24e4d6a3fe704fb60122422e5b4 ;;   # v22.1.8
  *) echo "LLVM $LLVM_MAJOR ($LLVM_CONFIG): BCD pins a SPIRV-LLVM-Translator for LLVM 21 and 22 only" >&2; exit 1 ;;
esac
[ -f "$LLVM_PREFIX/lib/cmake/llvm/LLVMConfig.cmake" ] || { echo "$LLVM_PREFIX has no LLVM cmake files (lib/cmake/llvm)" >&2; exit 1; }

# A source at exactly one commit, in a folder named by that commit and used for nothing else. A folder that a failed or interrupted
# fetch left (an empty repository, or one at another commit) is fetched again, so one failed build does not fail every later one
# (U6 counter-review H2); a folder that is not a git checkout is refused. In BCD's builds the GitHub URLs reach the local mirrors
# ~/madeira-local/kaynak/ucuncu/KhronosGroup--<repo>.git (yerel-git.sh), which hold the pinned commits.
checkout() {
    local dest=$1 url=$2 commit=$3
    if [ -e "$dest" ] && [ ! -d "$dest/.git" ]; then echo "$dest is not a git checkout; remove the folder" >&2; exit 1; fi
    [ -d "$dest/.git" ] || { mkdir -p "$dest"; git init -q "$dest"; }
    if [ "$(git -C "$dest" rev-parse -q --verify HEAD 2>/dev/null)" != "$commit" ]; then
        git -C "$dest" fetch -q --depth 1 "$url" "$commit"
        git -C "$dest" checkout -q --force FETCH_HEAD
    fi
    [ "$(git -C "$dest" rev-parse HEAD)" = "$commit" ] || { echo "Wrong source version at $dest; remove the folder" >&2; exit 1; }
}

# Python tools (meson >= 1.9.1, mako, pyyaml, packaging, ninja), as build.sh makes them.
if [ ! -x "$VENV/bin/meson" ] || [ ! -x "$VENV/bin/ninja" ]; then
    echo "Python build tools not found at $VENV; creating it"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q meson mako pyyaml packaging ninja
fi
export PATH="$VENV/bin:$PATH"

# Host libraries of mesa_clc, per LLVM major version.
HOSTSRC="$T/kosmickrisp-host/src"
HOST="$T/kosmickrisp-host/llvm-$LLVM_MAJOR"
mkdir -p "$HOSTSRC" "$HOST"
if [ ! -f "$HOST/lib/pkgconfig/SPIRV-Tools.pc" ]; then
    checkout "$HOSTSRC/SPIRV-Tools-$SPIRV_TOOLS" https://github.com/KhronosGroup/SPIRV-Tools.git "$SPIRV_TOOLS"
    checkout "$HOSTSRC/SPIRV-Headers-$SPIRV_HEADERS" https://github.com/KhronosGroup/SPIRV-Headers.git "$SPIRV_HEADERS"
    cmake -S "$HOSTSRC/SPIRV-Tools-$SPIRV_TOOLS" -B "$HOST/build-spirv-tools" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$HOST" \
        -DSPIRV-Headers_SOURCE_DIR="$HOSTSRC/SPIRV-Headers-$SPIRV_HEADERS" \
        -DSPIRV_SKIP_TESTS=ON -DSPIRV_SKIP_EXECUTABLES=ON -DSPIRV_WERROR=OFF
    cmake --build "$HOST/build-spirv-tools" --parallel "$JOBS"
    cmake --install "$HOST/build-spirv-tools"
fi
if [ ! -f "$HOST/lib/pkgconfig/LLVMSPIRVLib.pc" ]; then
    checkout "$HOSTSRC/SPIRV-LLVM-Translator-$TRANSLATOR" https://github.com/KhronosGroup/SPIRV-LLVM-Translator.git "$TRANSLATOR"
    # The translator names the SPIRV-Headers revision it is written against.
    TRANSLATOR_HEADERS="$(tr -d '[:space:]' < "$HOSTSRC/SPIRV-LLVM-Translator-$TRANSLATOR/spirv-headers-tag.conf")"
    checkout "$HOSTSRC/SPIRV-Headers-$TRANSLATOR_HEADERS" https://github.com/KhronosGroup/SPIRV-Headers.git "$TRANSLATOR_HEADERS"
    cmake -S "$HOSTSRC/SPIRV-LLVM-Translator-$TRANSLATOR" -B "$HOST/build-translator" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$HOST" \
        -DLLVM_DIR="$LLVM_PREFIX/lib/cmake/llvm" -DBUILD_SHARED_LIBS=ON -DLLVM_SPIRV_INCLUDE_TESTS=OFF \
        -DLLVM_EXTERNAL_SPIRV_HEADERS_SOURCE_DIR="$HOSTSRC/SPIRV-Headers-$TRANSLATOR_HEADERS"
    cmake --build "$HOST/build-translator" --parallel "$JOBS"
    cmake --install "$HOST/build-translator"
fi

export KK_LLVM_CONFIG="$LLVM_CONFIG"
export KK_HOST_PKG_CONFIG_PATH="$HOST/lib/pkgconfig:$HOST/share/pkgconfig"
export KK_OUT="$OUT"
export KK_WORK="$T/kosmickrisp/$PROFILE"
mkdir -p "$KK_WORK" "$OUT" "$R/app/Madeira/licenses"

if [ "$PROFILE" = playport ]; then
    SRC="$KK_WORK/mesa-$MESA_PLAYPORT"
    sh "$HERE/kk-stale-source.sh" "$SRC" "$HERE/playport-patches"   # build 676: a changed patch means a fresh tree
    PATCHES=$(ls "$HERE"/playport-patches/*.patch | wc -l | tr -d ' ')
    # fetch-playport.py (run by the recipe) downloads the archive of that commit, checks its SHA-256 and unpacks it once.
    bash "$HERE/build-playport-kosmickrisp.sh"
else
    SRC="$KK_WORK/mesa-$MESA_RELEASE"
    sh "$HERE/kk-stale-source.sh" "$SRC" "$HERE/kosmickrisp-patches"
    PATCHES=$(ls "$HERE"/kosmickrisp-patches/kosmickrisp-ios-*.patch | wc -l | tr -d ' ')
    if [ ! -f "$SRC/src/kosmickrisp/meson.build" ]; then
        tarball="$KK_WORK/mesa-$MESA_RELEASE.tar.xz"
        [ -f "$tarball" ] || { curl -fsSL --retry 3 -o "$tarball.part" "https://archive.mesa3d.org/mesa-$MESA_RELEASE.tar.xz"; mv "$tarball.part" "$tarball"; }
        got="$(shasum -a 256 "$tarball" | awk '{print $1}')"
        [ "$got" = "$MESA_RELEASE_SHA256" ] || { echo "mesa-$MESA_RELEASE.tar.xz has sha256 $got, expected $MESA_RELEASE_SHA256" >&2; exit 1; }
        tar -xf "$tarball" -C "$KK_WORK"
    fi
    KK_MESA_SRC="$SRC" KK_PROFILE=madeira bash "$HERE/build-kosmickrisp.sh"
fi

# What the bundle gets besides the library: Mesa's licence text of this very source, and what the library was built from.
LIB="$OUT/libvulkan_kosmickrisp.dylib"
cp "$SRC/docs/license.rst" "$R/app/Madeira/licenses/KosmicKrisp-Mesa-license.rst"
printf '%s profile=%s patches=%s llvm=%s\n' "$(basename "$SRC")" "$PROFILE" "$PATCHES" "$("$LLVM_CONFIG" --version)" > "$OUT/libvulkan_kosmickrisp.version"

# The library must be what the app expects: arm64 for iOS 26.0, and usable as the process's Vulkan library without a loader.
archs="$(xcrun lipo -archs "$LIB")"
[ "$archs" = arm64 ] || { echo "$LIB is '$archs', not arm64" >&2; exit 1; }
minos="$(xcrun vtool -show-build "$LIB" | awk '$1 == "minos" {print $2}')"
[ "$minos" = 26.0 ] || { echo "$LIB has minos '$minos', not 26.0" >&2; exit 1; }
for symbol in _vkGetInstanceProcAddr _vkGetDeviceProcAddr; do
    xcrun nm -gU "$LIB" | grep -q " T $symbol\$" || { echo "$LIB does not export $symbol" >&2; exit 1; }
done
if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$LIB"; fi
ls -l "$OUT/libvulkan_kosmickrisp.dylib" "$OUT/libvulkan_kosmickrisp.version" "$R/app/Madeira/licenses/KosmicKrisp-Mesa-license.rst"
