#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# dxcompiler.dll for 64-bit (x86-64) Windows programs: Microsoft's open-source DirectX Shader Compiler (DXC), built from source and
# shipped as app/Madeira/arm64ec-windows/dxcompiler-optin.dll, which a session links as dxcompiler.dll only for a game whose file says
# env.MADEIRA_OPTIN_DLLS = dxcompiler (app/Madeira/WineProcessBridge.m). Round 663, item 7c.
#
# Why: wgpu (the Rust graphics library of skate. 2 PC port and other ports) compiles its Direct3D 12 shaders at run time. Without a
# dxcompiler.dll it falls back to FXC (d3dcompiler_47.dll, Wine's vkd3d-shader), whose HLSL compiler has no Shader Model 5.1 resource
# arrays, and naga declares one in every shader that samples a texture (its 2048-entry sampler heap): E5017, Send Log JQ0202. With a
# dxcompiler.dll on the search path, wgpu 30 (Dx12Compiler::Auto) compiles to DXIL instead, which madeira-d3d12 converts with Apple's
# Metal Shader Converter like every other DXIL game. No dxil.dll is needed (DXC >= 1.8.2502 hashes its output itself).
#
# Source: DirectXShaderCompiler (University of Illinois/NCSA licence), tag v1.9.2609, commit 01b62ad4 (refused otherwise), cloned
# when missing; its DirectX-Headers (MIT), SPIRV-Headers and SPIRV-Tools (Apache-2.0) submodules only because the macOS configure of
# the two host tablegen tools asks for them (nothing of them is built or shipped). One patch:
# build/dxc-win/0001-mingw-w64-build.patch (Raed Rizqie's MinGW-w64 port, DXC pull request #8265). The C++ runtime of llvm-mingw
# (libc++, libunwind: Apache-2.0 with LLVM exception; MinGW-w64 runtime) is linked in. See build/dxc-win/BCD-SOURCE.txt.
#
#   MINGW=<llvm-mingw bin dir>   default <repo>/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin
#   SHIP_DIR=<dir>               default <repo>/app/Madeira/arm64ec-windows (dxcompiler-optin.dll goes there)
#   WORK=<dir>                   default <repo>/build/dxc-win/work (source, host tools, cross build; about 2 GB)
#   OUT=<dir>                    default <repo>/build/dxc-win/out (the stripped DLL and its stamp: what CI caches)
#   JOBS=<n>                     compile jobs, default the CPU count
#
# OUT holding a DLL whose stamp matches the inputs (pin, patch, toolchain file, this script, compiler version) is shipped as it is;
# anything else is built. With no llvm-mingw the script says so and exits 0 (nothing is shipped; tools/check-optin-dlls-shipped.py then
# names every list line or rule that wants the DLL). Exit 1: the build or the check of its result failed.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MINGW="${MINGW:-$ROOT/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
SHIP_DIR="${SHIP_DIR:-$ROOT/app/Madeira/arm64ec-windows}"
WORK="${WORK:-$ROOT/build/dxc-win/work}"
OUT="${OUT:-$ROOT/build/dxc-win/out}"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 4)}"
DXC_URL=https://github.com/microsoft/DirectXShaderCompiler.git
DXC_TAG=v1.9.2609
DXC_COMMIT=01b62ad47db7dee3dd33f90e7339cf9e860b3f93
PATCH="$ROOT/build/dxc-win/0001-mingw-w64-build.patch"
TOOLCHAIN="$ROOT/build/dxc-win/mingw-x86_64.cmake"
CHECK="$ROOT/tools/check-dxcompiler-shipped.py"

if [ ! -x "$MINGW/x86_64-w64-mingw32-clang++" ]; then
    echo "build-dxcompiler: no llvm-mingw at $MINGW; dxcompiler-optin.dll is not built"
    exit 0
fi
stamp() {
    { echo "$DXC_TAG $DXC_COMMIT"; shasum -a 256 "$PATCH" "$TOOLCHAIN" "${BASH_SOURCE[0]}" | cut -c1-64
      "$MINGW/x86_64-w64-mingw32-clang" --version | head -1; } | shasum -a 256 | cut -c1-64
}
STAMP="$(stamp)"
ship() {
    python3 "$CHECK" "$OUT/dxcompiler.dll"
    mkdir -p "$SHIP_DIR"
    cp "$OUT/dxcompiler.dll" "$SHIP_DIR/dxcompiler-optin.dll.tmp"
    mv "$SHIP_DIR/dxcompiler-optin.dll.tmp" "$SHIP_DIR/dxcompiler-optin.dll"
    echo "build-dxcompiler: shipped $SHIP_DIR/dxcompiler-optin.dll ($(wc -c < "$SHIP_DIR/dxcompiler-optin.dll" | tr -d ' ') bytes, DXC $DXC_TAG)"
}
if [ -f "$OUT/dxcompiler.dll" ] && [ "$(cat "$OUT/stamp" 2>/dev/null)" = "$STAMP" ]; then
    echo "build-dxcompiler: $OUT/dxcompiler.dll is current (stamp $STAMP)"
    ship
    exit 0
fi

# 1. The source at the pinned commit, with the one patch. A tree from an earlier run is reused only when it is that commit with that patch.
SRC="$WORK/src"
SRC_STAMP="$DXC_COMMIT $(shasum -a 256 "$PATCH" | cut -c1-64)"
if [ "$(cat "$WORK/src.stamp" 2>/dev/null)" != "$SRC_STAMP" ]; then
    rm -rf "$SRC" "$WORK/src.stamp" "$WORK/host" "$WORK/x64"
    mkdir -p "$WORK"
    git clone -q --depth 1 --branch "$DXC_TAG" "$DXC_URL" "$SRC"
    [ "$(git -C "$SRC" rev-parse HEAD)" = "$DXC_COMMIT" ] || { echo "build-dxcompiler: $DXC_TAG is not $DXC_COMMIT" >&2; exit 1; }
    git -C "$SRC" apply "$PATCH"
    echo "$SRC_STAMP" > "$WORK/src.stamp"
fi
# The Mac's configure (step 2) needs these three at the commits the tag pins: DXC turns SPIR-V on for every non-Windows host and asks
# for DirectX-Headers there. Nothing of them is built: step 2 builds the two tablegen tools only, and the Windows build has SPIR-V off.
# A no-op once they are there.
git -C "$SRC" submodule update -q --init --depth 1 external/DirectX-Headers external/SPIRV-Headers external/SPIRV-Tools

# 2. The two tablegen tools for the Mac itself (the cross build runs them).
COMMON=(-G Ninja -C "$SRC/cmake/caches/PredefinedParams.cmake" -DCMAKE_BUILD_TYPE=Release
        -DHLSL_INCLUDE_TESTS=OFF -DENABLE_SPIRV_CODEGEN=OFF -DSPIRV_BUILD_TESTS=OFF -DLLVM_INCLUDE_TESTS=OFF -DCLANG_INCLUDE_TESTS=OFF
        -DLLVM_BUILD_TESTS=OFF -DLLVM_APPEND_VC_REV=OFF -DLLVM_ENABLE_ASSERTIONS=OFF -DHLSL_BUILD_DXILCONV=OFF
        -DHLSL_DISABLE_SOURCE_GENERATION=ON)
# madeira-bcd (2026-11-18): HLSL_DISABLE_SOURCE_GENERATION (DXC's cmake/modules/HCT.cmake) builds from the generated sources
# the pinned commit carries in its tree. Without it DXC regenerates them, formats them with whatever clang-format is on PATH (the
# runner's Homebrew llvm) and compares them byte for byte with the committed ones; another clang-format version stopped the host
# build at include/dxc/DXIL/DxilConstants.h. Sources generated into the build directory are still made.
cmake -S "$SRC" -B "$WORK/host" "${COMMON[@]}" -DCMAKE_C_COMPILER=/usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    > "$WORK/host-configure.log" 2>&1 || { tail -30 "$WORK/host-configure.log"; exit 1; }
ninja -C "$WORK/host" -j"$JOBS" llvm-tblgen clang-tblgen > "$WORK/host-build.log" 2>&1 || { tail -30 "$WORK/host-build.log"; exit 1; }

# 3. dxcompiler.dll for x86-64 Windows: no SPIR-V, no tests, no DXBC converter; the C++ runtime linked in.
# -ffile-prefix-map: the compiler's __FILE__ strings (LLVM's asserts and unreachable messages) name the source files; they say "dxc/..."
# and "build/..." instead of the absolute path of the machine that built the DLL (the last matching map wins: SRC is inside WORK).
MAPS="-ffile-prefix-map=$WORK=build -ffile-prefix-map=$SRC=dxc"
MINGW_BIN="$MINGW" cmake -S "$SRC" -B "$WORK/x64" "${COMMON[@]}" -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
    -DLLVM_TABLEGEN="$WORK/host/bin/llvm-tblgen" -DCLANG_TABLEGEN="$WORK/host/bin/clang-tblgen" \
    -DCMAKE_C_FLAGS="$MAPS" -DCMAKE_CXX_FLAGS="$MAPS" -DCMAKE_SHARED_LINKER_FLAGS="-static -Wl,--gc-sections" \
    > "$WORK/x64-configure.log" 2>&1 || { tail -30 "$WORK/x64-configure.log"; exit 1; }
ninja -C "$WORK/x64" -j"$JOBS" dxcompiler > "$WORK/x64-build.log" 2>&1 || { tail -30 "$WORK/x64-build.log"; exit 1; }

mkdir -p "$OUT"
rm -f "$OUT/stamp"
"$MINGW/x86_64-w64-mingw32-strip" -o "$OUT/dxcompiler.dll" "$WORK/x64/bin/dxcompiler.dll"
python3 "$CHECK" "$OUT/dxcompiler.dll"
echo "$STAMP" > "$OUT/stamp"
ship
