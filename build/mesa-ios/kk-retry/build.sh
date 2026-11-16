#!/bin/bash
# SPDX-License-Identifier: MIT
# kk-retry: put a small shim in front of the staged KosmicKrisp (see kk_retry.m for why).
# Run after build/mesa-ios/build-kosmickrisp-bcd.sh (which calls it unless BCD_KK_RETRY=0).
#   app/Madeira/gl/libvulkan_kosmickrisp.dylib       -> renamed libvulkan_kosmickrisp_real.dylib
#   app/Madeira/gl/libvulkan_kosmickrisp.dylib (new) -> the shim; it re-exports the real library whole,
#   so WineProcessBridge.m (MADEIRA_VULKAN_LIBRARY) and the ICD entry points are unchanged.
# Safe to run twice: when _real exists the shim is only rebuilt.
# Mac self-test (no iPad needed): build/mesa-ios/kk-retry/build.sh --test
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
[ -f "$LIB" ] || { echo "kk-retry: $LIB not found; build KosmicKrisp first" >&2; exit 1; }
if [ ! -f "$REAL" ]; then
    mv "$LIB" "$REAL"
    install_name_tool -id @rpath/libvulkan_kosmickrisp_real.dylib "$REAL"
fi
cd "$GL"
xcrun -sdk iphoneos clang -arch arm64 -mios-version-min=26.0 -dynamiclib -fobjc-arc -O2 "$H/kk_retry.m" \
    -framework Foundation -framework Metal \
    -Wl,-reexport_library,libvulkan_kosmickrisp_real.dylib -Wl,-rpath,@loader_path \
    -install_name @rpath/libvulkan_kosmickrisp.dylib -o "$LIB"
codesign -f -s - "$REAL" "$LIB" 2>/dev/null || true
ls -l "$LIB" "$REAL"
