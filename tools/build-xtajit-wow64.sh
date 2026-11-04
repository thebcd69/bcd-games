#!/bin/bash
# Build FEX's aarch64 WOW64 module (libwow64fex.dll, shipped as
# app/Madeira/aarch64-windows/xtajit.dll -- the CPU backend wow64.dll loads for
# 32-bit x86 programs) from the FEX submodule with madeira-bcd's patches that
# apply to it, and ship it over the committed copy. Options are
# build/fex-wow64/build.sh's (FEX_IOS_HOST_BUILD turns on the 32-bit guest
# window). Run from the repo root; on a failed build the committed module stays.
#
#  - patch-fex-ios-cpuid-classic.py: opt-in (MADEIRA_FEX_CPUID_CLASSIC=1): CPUID leaf 1
#    reports DE and PSE in EDX as real processors do (Burnout Paradise's CPU check).
#  - patch-fex-ios-cpuid-index.py: CPUID 0x80000002-4 indexed the per-CPU table
#    with the raw host CPU number; 32-bit Crysis died in strlen(0xfff68000)
#    inside Function_8000_0002h before its first frame (log 2026-09-30 11:57).
#  - patch-fex-ios-teb-tsd.py: the WinAPI shims' GetCurrentTEB() read x18,
#    which is 0 on some iOS threads (32-bit Crysis, TlsGetValue, log 12:25).
#  - patch-fex-ios-wow-threads-ctor.py: the failure-path safety net of the 32-bit start on
#    iOS 18 (builds 573/574: every 32-bit program died in BTCpuThreadInit on the module's
#    all-zero global thread map). The cause is fixed in build/ntdll-unix/virtual_ios.c
#    ([wow-cpu-pool], build 576: the module's constructors ran in its executable PE view, the
#    exports in its pool copy); this overlay only constructs the map when it still finds it
#    zero and logs [wow64-ctor] lines with its state, so the next iOS 18 log shows which of
#    the two happened. A map the static initialiser touched is never touched.
#  - patch-fex-ios-op82-alias.py (round 624): SecondaryALUOp had no case for 0x82, the 32-bit-only
#    alias of 0x80 (ADD/OR/AND/SUB/XOR r/m8, imm8): "Unknown ALU Op: 0x10..0x16" and c000001d in
#    Ubisoft's protection code (Ubisoft Connect, South Park, Assassin's Creed 2). Optional like the
#    guard: a drifted anchor only skips it. The ARM64EC module needs nothing (0x82 is invalid in 64-bit mode).
#  - patch-fex-ios-thread-init-nomem.py (round 630, A2 S3): a thread for which FEXCore's CreateThread returns
#    NULL (the emulator's window full: the 32-bit Steam client on a 63 GB map) ends alone with STATUS_NO_MEMORY
#    instead of dereferencing it and ending the whole process. Optional like the guard.
#  - patch-fex-ios-moffs-rip.py (round 630, O3 S9): opt-in (MADEIRA_FEX_MOFFS_RIP=1): MOV moffs (A0-A3) records its
#    guest RIP, so an inline self-modifying store of that form resumes at itself (Unreal Gold's fire textures).
set -eu
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
export PATH="$MINGW:$PATH"
B="${FEX_WOW64_BUILD:-$R/FEX/build-wow64}"
SHIP="$R/app/Madeira/aarch64-windows/xtajit.dll"
CPUIDF="FEXCore/Source/Interface/Core/CPUID.cpp"
OPDISPF="FEXCore/Source/Interface/Core/OpcodeDispatcher.cpp"
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || nproc)"

git -C FEX diff --name-only | while read -r f; do git -C FEX checkout -- "$f"; done
python3 "$R/tools/patch-fex-ios-cpuid-index.py" "$R/FEX/$CPUIDF"
python3 "$R/tools/patch-fex-ios-teb-tsd.py" "$R/FEX/Source/Windows/Common/Priv.h"
# the safety net must never cost the two required fixes above: a drifted anchor only skips it
python3 "$R/tools/patch-fex-ios-wow-threads-ctor.py" "$R/FEX" || echo "::warning::the thread-map guard was not applied (FEX anchor changed); xtajit.dll is built without it"
python3 "$R/tools/patch-fex-ios-op82-alias.py" "$R/FEX/$OPDISPF" || echo "::warning::the 0x82 alias patch was not applied (FEX anchor changed); xtajit.dll is built without it"
python3 "$R/tools/patch-fex-ios-thread-init-nomem.py" "$R/FEX" || echo "::warning::the thread-init guard was not applied (FEX anchor changed); xtajit.dll is built without it"
# round 630 (O2 S7, Outlast Y09766): the code-invalidation walk skips a thread state without its caches ([inval-null])
python3 "$R/tools/patch-fex-ios-inval-null-thread.py" "$R/FEX" || echo "::warning::the invalidation-walk guard was not applied (FEX anchor changed); xtajit.dll is built without it"
# round 630 (G3 S11): RDTSCP/RDPID report processor 0 instead of the translation ending the process
python3 "$R/tools/patch-fex-ios-processorid.py" "$R/FEX/FEXCore/Source/Interface/Core/JIT/MiscOps.cpp" || echo "::warning::the ProcessorID fix was not applied (FEX anchor changed); xtajit.dll is built without it"
# opt-in (round 630, O3 S9, Unreal Gold Y1G0A7): MADEIRA_FEX_MOFFS_RIP=1 gives MOV moffs a guest-RIP entry; unchanged without it
python3 "$R/tools/patch-fex-ios-moffs-rip.py" "$R/FEX/FEXCore/Source/Interface/Core/Core.cpp" || echo "::warning::the MOV moffs RIP opt-in was not applied (FEX anchor changed); xtajit.dll is built without it"
# opt-in (round 626, Burnout Paradise 7MSNGW): MADEIRA_FEX_CPUID_CLASSIC=1 reports CPUID.1:EDX DE and PSE; EDX unchanged without it
python3 "$R/tools/patch-fex-ios-cpuid-classic.py" "$R/FEX/$CPUIDF" || echo "::warning::the CPUID DE/PSE opt-in was not applied (FEX anchor changed); xtajit.dll is built without it"
restore() { git -C "$R/FEX" checkout -- "$CPUIDF" Source/Windows/Common/Priv.h Source/Windows/WOW64/Module.cpp "$OPDISPF" FEXCore/Source/Interface/Core/Core.cpp FEXCore/Source/Interface/Core/JIT/MiscOps.cpp; }
trap restore EXIT

cmake -S "$R/FEX" -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE="$R/FEX/Data/CMake/toolchain_mingw.cmake" \
    -DMINGW_TRIPLE=aarch64-w64-mingw32 \
    -DFEX_IOS_HOST_BUILD=ON -DCMAKE_C_FLAGS=-DFEX_IOS_HOST \
    -DCMAKE_CXX_FLAGS=-DFEX_IOS_HOST -DCMAKE_ASM_FLAGS=-DFEX_IOS_HOST \
    -DENABLE_LTO=OFF -DENABLE_ASSERTIONS=OFF -DENABLE_JEMALLOC_GLIBC_ALLOC=OFF \
    -DBUILD_TESTING=OFF -DBUILD_FEXCONFIG=OFF -DENABLE_CCACHE=OFF -DBUILD_THUNKS=OFF \
    -DTUNE_ARCH=generic -DTUNE_CPU=none \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 > "$B.cfg.log" 2>&1 \
    || { tail -30 "$B.cfg.log"; exit 1; }
cmake --build "$B" --target wow64fex -j"$JOBS" > "$B.build.log" 2>&1 \
    || { grep -m 20 "error" "$B.build.log"; exit 1; }

"$MINGW/llvm-readobj" --coff-exports "$B/Bin/libwow64fex.dll" | awk '$1 == "Name:" { print $2 }' | sort > "$B.new-exports"
"$MINGW/llvm-readobj" --coff-exports "$SHIP" | awk '$1 == "Name:" { print $2 }' | sort > "$B.old-exports"
if ! diff "$B.old-exports" "$B.new-exports"; then
    echo "::warning::the rebuilt WOW64 module exports differ from the committed xtajit.dll; keeping the committed one"
    exit 1
fi
cp "$B/Bin/libwow64fex.dll" "$SHIP"
echo "::notice::xtajit.dll (WOW64) built from FEX $(git -C FEX rev-parse --short HEAD) with the CPUID index wrap, the TSD-slot TEB for the WinAPI shims, the thread-map guard and the 0x82 alias, and shipped"
