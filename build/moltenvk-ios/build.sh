#!/bin/bash
# Portions copyright 2026 Connor Gow: code taken from https://github.com/willfaust/Madeira (pull request #462)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
# GPL-3.0-or-later WITH the Madeira Converter Exception, version 1.
# Build MoltenVK (Vulkan on Metal) for iOS and stage it at
# app/Madeira/gl/libMoltenVK.dylib, the Vulkan implementation Mesa's Zink
# driver (build/mesa-ios/build.sh) loads for the desktop-OpenGL backend.
#
# Source: toolchains/MoltenVK, `git clone -b v1.4.2 https://github.com/KhronosGroup/MoltenVK`
# (commit db66022459ffb663aa2b50f6b018bc2e124f5edf, checked here; the script clones it
# when missing). fetchDependencies downloads and builds SPIRV-Cross, SPIRV-Tools, cereal
# and the Vulkan headers into External/; the first run takes a while.
#
# madeira-bcd: the same script as Madeira's apart from the place of the source, the
# commit check and the clone; MoltenVK itself is used unmodified.
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${MOLTENVK_SRC:-$R/toolchains/MoltenVK}"
OUT="$R/app/Madeira/gl"
LIC="$R/app/Madeira/licenses"
MVK_COMMIT=db66022459ffb663aa2b50f6b018bc2e124f5edf

if [ ! -f "$SRC/fetchDependencies" ]; then
    echo "MoltenVK source not found at $SRC; cloning v1.4.2"
    mkdir -p "$(dirname "$SRC")"
    git clone -q -b v1.4.2 https://github.com/KhronosGroup/MoltenVK "$SRC"
fi
if [ "$(git -C "$SRC" rev-parse HEAD)" != "$MVK_COMMIT" ]; then
    echo "MoltenVK at $SRC is $(git -C "$SRC" rev-parse HEAD), expected $MVK_COMMIT (v1.4.2)" >&2
    exit 1
fi

cd "$SRC"
[ -d External/build ] || ./fetchDependencies --ios
# MOLTENVK_JOBS: xcodebuild's -jobs (default: Xcode's own choice). The Makefile finds xcodebuild on the PATH and turns every variable given on
# its command line into a preprocessor definition, so a shim in front of it is the way to add the flag.
if [ -n "${MOLTENVK_JOBS:-}" ]; then
    shim="$(mktemp -d)"
    printf '#!/bin/sh\nexec "%s" -jobs %s "$@"\n' "$(command -v xcodebuild)" "$MOLTENVK_JOBS" > "$shim/xcodebuild"
    chmod +x "$shim/xcodebuild"
    export PATH="$shim:$PATH"
fi
make ios

BIN="$SRC/Package/Release/MoltenVK/dynamic/MoltenVK.xcframework/ios-arm64/MoltenVK.framework/MoltenVK"
mkdir -p "$OUT" "$LIC"
cp "$BIN" "$OUT/libMoltenVK.dylib"
install_name_tool -id @rpath/libMoltenVK.dylib "$OUT/libMoltenVK.dylib"
git -C "$SRC" describe --tags --always > "$OUT/libMoltenVK.version"

cp "$SRC/LICENSE" "$LIC/MoltenVK-Apache-2.0.txt"
cp "$SRC/External/SPIRV-Cross/LICENSE" "$LIC/SPIRV-Cross-Apache-2.0.txt"
cp "$SRC/External/SPIRV-Tools/LICENSE" "$LIC/SPIRV-Tools-Apache-2.0.txt"
cp "$SRC/External/cereal/LICENSE" "$LIC/cereal-BSD-3-Clause.txt"
ls -l "$OUT/libMoltenVK.dylib"
