#!/bin/bash
# Configure (first time) and build FEX's aarch64 WOW64 module (libwow64fex.dll,
# shipped as xtajit.dll), the CPU backend wow64.dll loads for 32-bit x86
# programs. See docs/WOW64.md, "Building". The options are the iOS host build's:
# FEX_IOS_HOST_BUILD turns on the 32-bit guest window (ENABLE_GUEST_WINDOW),
# which FEX refuses for the ARM64EC module.
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin:$PATH"
B="$R/FEX/build-wow64"
if [ ! -f "$B/CMakeCache.txt" ]; then
    cmake -S "$R/FEX" -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_TOOLCHAIN_FILE="$R/FEX/Data/CMake/toolchain_mingw.cmake" \
        -DMINGW_TRIPLE=aarch64-w64-mingw32 \
        -DFEX_IOS_HOST_BUILD=ON -DCMAKE_C_FLAGS=-DFEX_IOS_HOST \
        -DCMAKE_CXX_FLAGS=-DFEX_IOS_HOST -DCMAKE_ASM_FLAGS=-DFEX_IOS_HOST \
        -DENABLE_LTO=OFF -DENABLE_ASSERTIONS=OFF -DENABLE_JEMALLOC_GLIBC_ALLOC=OFF \
        -DBUILD_TESTING=OFF -DBUILD_FEXCONFIG=OFF -DTUNE_ARCH=generic -DTUNE_CPU=none \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
fi
# madeira-bcd: wow64-ctor-guard (tools/patch-fex-ios-wow-threads-ctor.py): every 32-bit program on an
# iOS 18 device died in BTCpuThreadInit because the module's global thread map was an all-zero object
# (max_load_factor 0, its static initialiser had no effect there). The overlay constructs it when it
# finds it so and logs [wow64-ctor] lines with the map's state. It is applied to the build copy and
# the file is restored after; a pin that already carries it is left alone (the overlay is idempotent).
python3 "$R/tools/patch-fex-ios-wow-threads-ctor.py" "$R/FEX"
# round 630 (A2 S3): a thread that gets no emulator state ends alone (tools/patch-fex-ios-thread-init-nomem.py); optional
python3 "$R/tools/patch-fex-ios-thread-init-nomem.py" "$R/FEX" || echo "warning: the thread-init guard was not applied (FEX anchor changed)"
# round 630 (O2 S7): the code-invalidation walk skips a thread state without its caches; optional
python3 "$R/tools/patch-fex-ios-inval-null-thread.py" "$R/FEX" || echo "warning: the invalidation-walk guard was not applied (FEX anchor changed)"
trap 'git -C "$R/FEX" checkout -- Source/Windows/WOW64/Module.cpp FEXCore/Source/Interface/Core/Core.cpp' EXIT
cmake --build "$B" --target wow64fex
cp "$B/Bin/libwow64fex.dll" "$R/app/Madeira/aarch64-windows/xtajit.dll" && ls -l "$R/app/Madeira/aarch64-windows/xtajit.dll"
