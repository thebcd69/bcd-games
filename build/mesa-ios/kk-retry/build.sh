#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright 2026 RageMC
# From bcd-games pull request #57 (RageMC, 2026-11-16).
# Modified by BCD (thebcd69), 2026-11-16: run as a CI step of its own after the KosmicKrisp cache steps (the cached library stays the real
# one, and Mesa is not rebuilt for it); the shim is told from the real library by its marker, so a freshly built real library replaces an
# older _real; both files are made in a temporary folder and moved into gl/ only when the shim linked, so a failed run leaves the real
# library where it was; no library in the build is not an error.
#
# kk-retry: put a small shim in front of the staged KosmicKrisp (see kk_retry.m for why).
#   app/Madeira/gl/libvulkan_kosmickrisp.dylib       -> becomes libvulkan_kosmickrisp_real.dylib
#   app/Madeira/gl/libvulkan_kosmickrisp.dylib (new) -> the shim; it re-exports the real library whole,
#   so WineProcessBridge.m (MADEIRA_VULKAN_LIBRARY) and the ICD entry points are unchanged.
# Mac self-test (a Mac with a Metal 4 GPU): build/mesa-ios/kk-retry/build.sh --test
set -eu
H="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$(cd "$H/../../.." && pwd)"
if [ "${1:-}" = "--test" ]; then
    t="$(mktemp -d)"
    xcrun clang -fobjc-arc -DKKR_TEST "$H/test_mac.m" "$H/kk_retry.m" -framework Foundation -framework Metal -o "$t/kkr-test"
    "$t/kkr-test"; exit
fi
GL="$R/app/Madeira/gl"
LIB="$GL/libvulkan_kosmickrisp.dylib"
REAL="$GL/libvulkan_kosmickrisp_real.dylib"
MARK="kk-retry: shim in front of KosmicKrisp"
if [ ! -f "$LIB" ]; then
    rm -f "$REAL"
    echo "kk-retry: no KosmicKrisp in this build; nothing to do"
    exit 0
fi
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
if grep -q "$MARK" "$LIB"; then
    # The shim is already in place (a kept tree): only the shim is made again, over the same real library.
    [ -f "$REAL" ] || { echo "::warning::kk-retry: the shim is in place but libvulkan_kosmickrisp_real.dylib is missing"; exit 1; }
    cp "$REAL" "$T/libvulkan_kosmickrisp_real.dylib"
else
    # A real library from the KosmicKrisp build or its cache: it becomes the _real one.
    cp "$LIB" "$T/libvulkan_kosmickrisp_real.dylib"
    install_name_tool -id @rpath/libvulkan_kosmickrisp_real.dylib "$T/libvulkan_kosmickrisp_real.dylib"
fi
(cd "$T" && xcrun -sdk iphoneos clang -arch arm64 -mios-version-min=26.0 -dynamiclib -fobjc-arc -O2 "$H/kk_retry.m" \
    -framework Foundation -framework Metal \
    -Wl,-reexport_library,libvulkan_kosmickrisp_real.dylib -Wl,-rpath,@loader_path \
    -install_name @rpath/libvulkan_kosmickrisp.dylib -o libvulkan_kosmickrisp.dylib)
grep -q "$MARK" "$T/libvulkan_kosmickrisp.dylib" || { echo "::warning::kk-retry: the shim has no marker; not installed"; exit 1; }
codesign -f -s - "$T/libvulkan_kosmickrisp_real.dylib" "$T/libvulkan_kosmickrisp.dylib" 2>/dev/null || true
cp "$T/libvulkan_kosmickrisp_real.dylib" "$REAL.tmp" && mv "$REAL.tmp" "$REAL"
cp "$T/libvulkan_kosmickrisp.dylib" "$LIB.tmp" && mv "$LIB.tmp" "$LIB"
echo "::notice::kk-retry: KosmicKrisp loads through the shim (Metal compiles retried after a lost compiler connection)"
ls -l "$LIB" "$REAL"
