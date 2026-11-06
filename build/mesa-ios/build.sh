#!/bin/bash
# Copyright 2026 Connor Gow and 125hz. This file was taken from https://github.com/willfaust/Madeira (pull request #462)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
# GPL-3.0-or-later WITH the Madeira Converter Exception, version 1.
# Build Mesa's OSMesa with the Zink driver for iOS (arm64) -> libOSMesa.dylib,
# staged at app/Madeira/gl/. Desktop OpenGL 3.3+ for the winios GL driver
# (build/win32u-unix/opengl_ios.c, "zink" backend): OSMesa renders into an
# IOSurface-backed buffer, Zink turns GL into Vulkan, MoltenVK
# (build/moltenvk-ios/build.sh) turns Vulkan into Metal.
#
# Mesa 25.0.x on purpose: OSMesa was removed in 25.1.
# OSMesa insists on a software gallium driver being built; softpipe is the one
# without LLVM. At runtime GALLIUM_DRIVER=zink selects Zink through the same
# software-winsys helper.
#
# Source: toolchains/mesa-25.0.7 (https://archive.mesa3d.org/mesa-25.0.7.tar.xz,
# sha256 592272df3cf01e85e7db300c449df5061092574d099da275d19e97ef0510f8a6, checked
# here when the script downloads it), with build/mesa-ios/patches/*.patch applied.
# The patches modify Mesa and are offered under Mesa's licence (MIT), so they can
# go upstream as they are. Python deps (meson, mako, pyyaml, ninja) come from
# toolchains/mesa-venv, created here when missing; bison and flex are the ones
# that ship with macOS.
#
# madeira-bcd: the same script and patches as Madeira's (MIT patches, GPL script);
# only the places of the sources changed (toolchains/ is where CI keeps downloads
# and its caches), and the tarball is downloaded and checked here.
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$R/build/mesa-ios"
SRC="${MESA_SRC:-$R/toolchains/mesa-25.0.7}"
MVK="${MOLTENVK_SRC:-$R/toolchains/MoltenVK}"
VENV="${MESA_VENV:-$R/toolchains/mesa-venv}"
B="${MESA_BUILD:-$HERE/obj}"
OUT="$R/app/Madeira/gl"
SDK="$(xcrun --sdk iphoneos --show-sdk-path)"
MESA_TAR_SHA256=592272df3cf01e85e7db300c449df5061092574d099da275d19e97ef0510f8a6

if [ ! -f "$SRC/meson.build" ]; then
    tar_dir="$(dirname "$SRC")"
    mkdir -p "$tar_dir"
    echo "Mesa source not found at $SRC; downloading mesa-25.0.7.tar.xz"
    curl -fsSL -o "$tar_dir/mesa-25.0.7.tar.xz" https://archive.mesa3d.org/mesa-25.0.7.tar.xz
    got="$(shasum -a 256 "$tar_dir/mesa-25.0.7.tar.xz" | awk '{print $1}')"
    if [ "$got" != "$MESA_TAR_SHA256" ]; then
        echo "mesa-25.0.7.tar.xz has sha256 $got, expected $MESA_TAR_SHA256" >&2
        exit 1
    fi
    tar -xf "$tar_dir/mesa-25.0.7.tar.xz" -C "$tar_dir"
    [ "$tar_dir/mesa-25.0.7" = "$SRC" ] || mv "$tar_dir/mesa-25.0.7" "$SRC"
fi
if [ ! -x "$VENV/bin/meson" ] || [ ! -x "$VENV/bin/ninja" ]; then
    echo "Python build tools not found at $VENV; creating it"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q meson mako pyyaml packaging ninja
fi
if [ ! -d "$MVK" ]; then
    echo "MoltenVK not found at $MVK; run build/moltenvk-ios/build.sh first" >&2
    exit 1
fi

export PATH="$VENV/bin:$PATH"

# Patches are applied once; the marker keeps re-runs idempotent.
for p in "$HERE"/patches/[0-9]*.patch; do
    [ -e "$p" ] || continue
    m="$SRC/.madeira-applied-$(basename "$p")"
    if [ ! -f "$m" ]; then
        patch -d "$SRC" -p1 --forward < "$p"
        touch "$m"
    fi
done

mkdir -p "$B"
sed "s|@SDK@|$SDK|g" "$HERE/ios-arm64.cross" > "$B/ios-arm64.cross"

# On-disk shader cache (ml1342): GLSL->NIR and Zink's SPIR-V/pipeline caches persist
# between runs, compressed with the SDK's zlib. The GL driver points
# MESA_SHADER_CACHE_DIR at Library/Caches (build/win32u-unix/opengl_ios.c).
CACHE_OPTS=(-Dshader-cache=enabled -Dshader-cache-default=true -Dshader-cache-max-size=256M -Dzlib=enabled)

if [ ! -f "$B/build.ninja" ]; then
    meson setup "$B" "$SRC" --cross-file "$B/ios-arm64.cross" \
        --buildtype=release -Db_ndebug=true --default-library=shared \
        -Dplatforms= \
        -Dgallium-drivers=softpipe,zink \
        -Dvulkan-drivers= \
        -Dosmesa=true \
        -Dopengl=true -Dgles1=disabled -Dgles2=disabled \
        -Dglx=disabled -Degl=disabled -Dgbm=disabled \
        -Dllvm=disabled "${CACHE_OPTS[@]}" -Dxmlconfig=disabled \
        -Dzstd=disabled -Dlibunwind=disabled -Dvalgrind=disabled -Dlmsensors=disabled \
        -Dexpat=disabled -Dbuild-tests=false \
        -Dvideo-codecs= \
        -Dmoltenvk-dir="$MVK"
else
    # an existing build dir from before ml1342 had the cache off
    meson configure "$B" "${CACHE_OPTS[@]}"
fi
ninja -C "$B" ${MESA_JOBS:+-j"$MESA_JOBS"}

mkdir -p "$OUT" "$R/app/Madeira/licenses"
cp "$B/src/gallium/targets/osmesa/libOSMesa.8.dylib" "$OUT/libOSMesa.dylib"
install_name_tool -id @rpath/libOSMesa.dylib "$OUT/libOSMesa.dylib"
xcrun -sdk iphoneos strip -x "$OUT/libOSMesa.dylib"
cp "$SRC/docs/license.rst" "$R/app/Madeira/licenses/Mesa-license.rst"
echo "$(basename "$SRC")" > "$OUT/libOSMesa.version"
ls -l "$OUT"
