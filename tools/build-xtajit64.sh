#!/bin/bash
# Build the ARM64EC FEX module (libarm64ecfex.dll) from the FEX submodule with
# tools/patch-fex-ios-mapview-selfshared.py and tools/patch-fex-ios-avx.py
# applied, and ship it as xtajit64.dll (and a copy as xtajit64-avx.dll, which
# WineProcessBridge.m links in when a game turns AVX on; the AVX patch is
# itself gated on MADEIRA_FEX_AVX=1). Since build 231 this replaces upstream's
# committed module: without the map-notification fix a thread that loads a
# DLL from translated code waits on itself (God of War).
#
# build/fex-arm64ec/build.sh does not record everything the committed DLL was
# built with; the options below reproduce it. FEX_IOS_HOST must reach the
# ASSEMBLER too: Module.S holds the iOS transition code (TEB from TPIDR, not
# x18; the FFS bypass). Builds 138-148 missed CMAKE_ASM_FLAGS, got the stock
# ExitToX64 (`str x30,[sp,#-8]!`, sp off 16-byte alignment) and every x64 DLL
# entry point crashed -- with section sizes identical, so sizes prove nothing.
# Before shipping, the unpatched source is built and compared with the
# committed DLL by function list and by the instructions of the Module.S
# transition code; any drift keeps the AVX build out. Run from the repo root.
set -eu
R="$(pwd)"
MINGW="${MINGW:-$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin}"
export PATH="$MINGW:$PATH"
B="${FEX_ARM64EC_BUILD:-$R/FEX/build-arm64ec}"
SHIP="$R/app/Madeira/arm64ec-windows/xtajit64.dll"
AVX="$R/app/Madeira/arm64ec-windows/xtajit64-avx.dll"
CPUF="Source/Windows/Common/CPUFeatures.cpp"
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || nproc)"

# The iOS static-library steps patch FEX sources in place; this module is
# built from the pristine submodule, as the committed one was.
git -C FEX diff --name-only | while read -r f; do git -C FEX checkout -- "$f"; done

# The WoW64 series (125hz, upstream PR #28) ships an xtajit64.dll built from
# its own FEX change, which the submodule does not pin. Its ARM64EC interface
# is upstream's (same BTCpu64* exports; one extra, IosAliasStats, is a
# diagnostic counter the unix side reads if present), so the AVX build stays
# on the submodule's source and is checked against the module upstream built
# from it -- the one that shipped before the merge, fetched by commit because
# CI checks out one commit deep.
WOW64_XTAJIT_SHA=2754e830663ea3f413622a1899a67ca8e33380be31d04ddfc68073ee99315c54
UPSTREAM_XTAJIT_COMMIT=873fe25b010c43252407da79658daed0d06fa725
REF="$SHIP"
if [ "$( (sha256sum "$SHIP" 2>/dev/null || shasum -a 256 "$SHIP") | cut -d' ' -f1)" = "$WOW64_XTAJIT_SHA" ]; then
    REF="$B.upstream-xtajit64.dll"
    git cat-file -e "$UPSTREAM_XTAJIT_COMMIT^{commit}" 2>/dev/null \
        || git fetch -q --depth=1 origin "$UPSTREAM_XTAJIT_COMMIT"
    git show "$UPSTREAM_XTAJIT_COMMIT:app/Madeira/arm64ec-windows/xtajit64.dll" > "$REF"
    echo "=== xtajit64.dll is the WoW64 series' build; comparing with upstream's (${UPSTREAM_XTAJIT_COMMIT:0:7}) ==="
fi

# TUNE_CPU: FEX's default "native" probes /proc/cpuinfo through a script that
# needs pkg_resources; on a Linux x86 host it settles on cortex-a78, which is
# what reproduces the committed module, and on the macOS runner it aborts the
# configure. Name it outright.
# Always (re)configure: a cached tree from before the ASM flag would keep it.
{
    cmake -S "$R/FEX" -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_TOOLCHAIN_FILE="$R/FEX/Data/CMake/toolchain_mingw.cmake" \
        -DMINGW_TRIPLE=arm64ec-w64-mingw32 -DTUNE_CPU=cortex-a78 \
        -DFEX_IOS_HOST_BUILD=ON \
        -DCMAKE_C_FLAGS=-DFEX_IOS_HOST=1 -DCMAKE_CXX_FLAGS=-DFEX_IOS_HOST=1 \
        -DCMAKE_ASM_FLAGS=-DFEX_IOS_HOST=1 \
        -DENABLE_LTO=OFF -DENABLE_FEX_ALLOCATOR=ON -DENABLE_JEMALLOC_GLIBC_ALLOC=ON \
        -DBUILD_FEXCONFIG=OFF -DENABLE_CCACHE=OFF -DBUILD_TESTING=OFF -DBUILD_THUNKS=OFF \
        -DENABLE_ASSERTIONS=OFF > "$B.cfg.log" 2>&1 \
        || { tail -30 "$B.cfg.log"; exit 1; }
}

build() {
    cmake --build "$B" --target arm64ecfex -j"$JOBS" > "$B.build.log" 2>&1 \
        || { grep -m 20 "error" "$B.build.log"; exit 1; }
}

# Section sizes, export names, every function name, and the Module.S
# transition code (DispatchJump .. the end of CheckCall) instruction by
# instruction with addresses blanked -- a different link order moves every
# function, so raw bytes cannot be compared.
fingerprint() {
    "$MINGW/llvm-objdump" -d --no-show-raw-insn "$1" | grep -E '^[0-9a-f]+ <.+>:$' | sed 's/^[0-9a-f]* //' | sort
    "$MINGW/llvm-objdump" -d --no-show-raw-insn "$1" \
        | awk '/<DispatchJump>:/{f=1} f && /^ *[0-9a-f]+:/{l=$0; sub(/^ *[0-9a-f]+:[ \t]*/,"",l); print l} /<CheckCall>:/{c=1} c && /\tret$/{exit}' \
        | sed -E 's/0x[0-9a-f]+//g; s/<[^>]*>//g'
    sectionsizes "$1"
}
sectionsizes() {
    python3 - "$1" <<'PY'
import struct, sys
d = open(sys.argv[1], 'rb').read()
pe = struct.unpack_from('<I', d, 0x3c)[0]
n = struct.unpack_from('<H', d, pe + 6)[0]
o = pe + 24 + struct.unpack_from('<H', d, pe + 20)[0]
for _ in range(n):
    name = d[o:o + 8].rstrip(b'\0').decode()
    if name in ('.text', '.rdata', '.pdata', '.hexpthk', '.a64xrm', '.tls'):
        print(name, hex(struct.unpack_from('<I', d, o + 8)[0]))
    o += 40
PY
    "$MINGW/llvm-readobj" --coff-exports "$1" | awk '$1 == "Name:" { print $2 }'
}

echo "=== unpatched rebuild, compared with the committed xtajit64.dll ==="
build
if ! diff <(fingerprint "$REF") <(fingerprint "$B/Bin/libarm64ecfex.dll"); then
    # madeira-bcd (build 231): upstream's committed module is 4 KB of .text
    # smaller than a rebuild of its own pin, with the same function list and
    # the same Module.S transition code. Report it, but ship the patched
    # rebuild anyway: the self-deadlock fix below is not optional.
    echo "::warning::the rebuilt FEX module differs from the committed xtajit64.dll (see the diff above); shipping the patched rebuild"
else
    echo "  matches the committed module"
fi

# madeira-bcd: the patched module replaces upstream's xtajit64.dll.
#  - patch-fex-ios-mapview-selfshared.py: NotifyMapViewOfSection must not take
#    CodeInvalidationMutex exclusively on a thread that holds it shared (God of
#    War waited on itself inside LdrLoadDll, builds 226-230).
#  - patch-fex-ios-intervals-reentry.py: a memory notification raised by an
#    allocation made under InvalidationTracker's IntervalsLock (a log line
#    growing FEX's heap) must not wait on its own thread (God of War, builds
#    230-231: HandleMemoryProtectionNotification -> EFmt -> rpmalloc ->
#    VirtualAlloc -> NotifyMemoryAlloc -> the same lock).
#  - patch-fex-ios-ircap-tls.py: FEX's IR-capture mark was a thread_local that
#    landed in the game's own TLS[0] block and zeroed its bytes +0x8..+0xf on
#    every block compile (God of War's allocator-stack index, builds 234-238).
#  - patch-fex-ios-teb-tsd.py: the WinAPI shims' GetCurrentTEB() read x18,
#    which is 0 on some iOS threads (God of War's TlsGetValue AV, build 239);
#    they take the TEB from the TSD slot like Module.cpp's IOSLoadTEB.
#  - patch-fex-ios-cpuid-index.py: CPUID's brand-string/hybrid leaves index
#    the per-CPU table with the raw host CPU number (out of range on iOS).
#  - patch-fex-ios-alias-full-quiet.py: no LogMan call when the alias table is
#    full (it ran nested inside a unix syscall and corrupted its frame).
#  - patch-fex-ios-alias-retire-jit.py: a new image copy on a reused JIT range
#    retires the dead entry that still covers it (reverse translation walks
#    oldest-first and found the dead image).
#  - patch-fex-ios-avx.py: AVX/AVX2 only when MADEIRA_FEX_AVX=1 at launch, so
#    the same module serves both; xtajit64-avx.dll is kept as a copy for the
#    bridge's existing switch.
#  - patch-fex-ios-smc-store-fastpath.py: on the SMC path, plain stores and
#    stores already backpatched to DMB + STR are decided without the
#    backpatch lock (two Mach-emulated stores per fault) and without the
#    "Unhandled JIT SIGBUS" line; same Pc result. MADEIRA_FEX_SMC_FASTPATH=0
#    restores the locked path.
#  - patch-fex-ios-callret-elide.py: the JIT no longer maintains the call-ret
#    shadow stack, which nothing reads on iOS (9 instructions per guest CALL
#    and per RET); linked calls become `bl`. MADEIRA_FEX_CALLRET_ELIDE=0 keeps
#    the stack.
#  - patch-fex-ios-ec-ret.py: a guest call into ARM64EC code leaves the JIT
#    with `blr` and its return enters the block with `ret` after an L1 probe
#    (10 instructions instead of 22), so the return-address predictor stays
#    balanced. MADEIRA_FEX_EC_RET=0 keeps `br` and the loop-top re-entry.
#  - patch-fex-ios-ec-bitmap-cache.py: the loop top's EC code check loads
#    PEB->EcCodeBitMap from the thread's frame instead of walking TPIDRRO_EL0,
#    the TEB and the PEB (4 instructions less on every call into ARM64EC code
#    through the import table). MADEIRA_FEX_EC_BITMAP_CACHE=0 keeps the walk.
#  - patch-fex-ios-ec-exit-lean.py: JIT exits into ARM64EC code go through
#    ExitFunctionECJit (Module.S) when AFP is off: no FPCR test, CPU area and
#    doorbell from the frame, the call counted per thread (no atomic per call)
#    and a per-thread alias hit; 31 instructions instead of 44.
#    MADEIRA_FEX_EC_EXIT_LEAN=0 keeps ExitFunctionEC.
#  - patch-fex-ios-alias-xlate-bounds.py: IosJitReverseTranslate returns an
#    address outside every JIT copy at once instead of walking the alias
#    table (it runs for every block lookup the dispatcher misses). Same
#    answers; no switch.
#  - patch-fex-ios-ec-ret-direct.py: RetToEntryThunk enters the JIT without
#    its own EC code bitmap test (the loop top tests L1 misses): 11
#    instructions less on every return from ARM64EC code.
#    MADEIRA_FEX_EC_RET_DIRECT=0 keeps the test.
#  - patch-fex-ios-mono-bp-unlocked-miss.py: opt-in (MADEIRA_MONO_BP_UNLOCKED_MISS=1):
#    MonoBackpatcherWrite stores an alias-table miss without CodeInvalidationMutex,
#    so the access violation that store raises no longer leaves the mutex write-owned
#    (the "[fexlock] STUCK write-wait" owner == waiter hang). Applied to every pin.
# madeira-bcd: since the 2026-10-15 upstream merge the FEX pin (f59cab9) carries
# willfaust/FEX #15-#20, which are these overlays as the fork applied them, except
# the Launcher full-SMC opt-in and the rpmalloc spans (rpmalloc#2 is still open).
# The overlays' own checks see the pin's combined files, not the state each one
# expects after the ones before it, and stop; on such a pin only those two are
# applied. An older pin still gets the whole sequence.
if grep -q "ec-ret-direct-v1 madeira-bcd" "$R/FEX/Source/Windows/ARM64EC/Module.cpp"; then
    echo "=== FEX $(git -C FEX rev-parse --short HEAD) carries the fork's ARM64EC overlays (willfaust/FEX #15-#20); adding the rpmalloc spans and the Launcher full-SMC opt-in ==="
    python3 "$R/tools/patch-fex-ios-rpmalloc-span8.py" "$R/FEX/External/rpmalloc/rpmalloc/rpmalloc.c" --span-mb 4
    python3 "$R/tools/patch-fex-ios-launcher-smc.py" "$R/FEX/Source/Windows/ARM64EC/Module.cpp"
else
    echo "=== with the map-notification, IntervalsLock and IRCapRIP fixes and the AVX opt-in ==="
    python3 "$R/tools/patch-fex-ios-mapview-selfshared.py" "$R/FEX/Source/Windows/ARM64EC/Module.cpp"
    python3 "$R/tools/patch-fex-ios-intervals-reentry.py" "$R/FEX/Source/Windows/Common"
    python3 "$R/tools/patch-fex-ios-ircap-tls.py" "$R/FEX/FEXCore/Source/Interface/IR/PassManager.cpp"
    python3 "$R/tools/patch-fex-ios-teb-tsd.py" "$R/FEX/Source/Windows/Common/Priv.h"
    python3 "$R/tools/patch-fex-ios-cpuid-index.py" "$R/FEX/FEXCore/Source/Interface/Core/CPUID.cpp"
    python3 "$R/tools/patch-fex-ios-avx.py" "$R/FEX/$CPUF"
    python3 "$R/tools/patch-fex-ios-alias-full-quiet.py" "$R/FEX/Source/Windows/ARM64EC/IosJitAlias.cpp"
    python3 "$R/tools/patch-fex-ios-alias-retire-jit.py" "$R/FEX/Source/Windows/ARM64EC/IosJitAlias.cpp"
    # Keep the pinned allocator intact; only the patched ARM64EC rebuild uses
    # coherent 4 MiB spans. The unpatched fingerprint above retains its geometry.
    python3 "$R/tools/patch-fex-ios-rpmalloc-span8.py" "$R/FEX/External/rpmalloc/rpmalloc/rpmalloc.c" --span-mb 4
    python3 "$R/tools/patch-fex-ios-branch-history.py" "$R/FEX"
    python3 "$R/tools/patch-fex-ios-launcher-smc.py" "$R/FEX/Source/Windows/ARM64EC/Module.cpp"
    # Each thread reserved 16 MiB of call-return stack in the 12 GB FEX arena; the
    # JIT only ever touches [2 MiB, 6 MiB) of it (licensed GTA V ran out, 434/436).
    python3 "$R/tools/patch-fex-ios-callret-8mb.py" "$R/FEX"
    python3 "$R/tools/patch-fex-ios-smc-store-fastpath.py" "$R/FEX"
    python3 "$R/tools/patch-fex-ios-callret-elide.py" "$R/FEX"
    python3 "$R/tools/patch-fex-ios-ec-ret.py" "$R/FEX"
    python3 "$R/tools/patch-fex-ios-ec-bitmap-cache.py" "$R/FEX"
    python3 "$R/tools/patch-fex-ios-ec-exit-lean.py" "$R/FEX"
    python3 "$R/tools/patch-fex-ios-alias-xlate-bounds.py" "$R/FEX"
    python3 "$R/tools/patch-fex-ios-ec-ret-direct.py" "$R/FEX"
fi
# Opt-in, whichever pin: MADEIRA_MONO_BP_UNLOCKED_MISS=1 (round 623).
python3 "$R/tools/patch-fex-ios-mono-bp-unlocked-miss.py" "$R/FEX/FEXCore/Source/Interface/Core/Core.cpp"
# Opt-in, whichever pin (round 624, agent A S7; Borderlands GOTY Enhanced V0FQAS): the alias table has room for 1024 image copies,
# the limit in force stays 256 unless env.MADEIRA_FEX_ALIAS_MAX names more; the miss line says how full the table is.
python3 "$R/tools/patch-fex-ios-alias-capacity.py" "$R/FEX"
build
git -C FEX checkout -- "$CPUF" Source/Windows/ARM64EC/Module.cpp Source/Windows/ARM64EC/Module.S Source/Windows/Common/InvalidationTracker.h Source/Windows/Common/InvalidationTracker.cpp FEXCore/Source/Interface/IR/PassManager.cpp Source/Windows/Common/Priv.h FEXCore/Source/Interface/Core/CPUID.cpp Source/Windows/ARM64EC/IosJitAlias.cpp FEXCore/include/FEXCore/Core/CoreState.h FEXCore/Source/Interface/Core/JIT/BranchOps.cpp Source/Windows/ARM64EC/libarm64ecfex.def FEXCore/include/FEXCore/Debug/InternalThreadState.h FEXCore/Source/Interface/Core/Core.cpp Source/Windows/Common/CallRetStack.h FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp FEXCore/Source/Interface/Core/ArchHelpers/Arm64Emitter.h FEXCore/Source/Interface/Core/ArchHelpers/Arm64Emitter.cpp FEXCore/Source/Interface/Core/JIT/JIT.cpp
git -C FEX/External/rpmalloc checkout -- rpmalloc/rpmalloc.c
cp "$B/Bin/libarm64ecfex.dll" "$SHIP"
cp "$B/Bin/libarm64ecfex.dll" "$AVX"
echo "::notice::xtajit64.dll (and xtajit64-avx.dll) built from FEX $(git -C FEX rev-parse --short HEAD) with the map-notification and IntervalsLock self-deadlock fixes, IRCapRIP out of the game's TLS, the TSD-slot TEB for the WinAPI shims, the CPUID index wrap, and the MADEIRA_FEX_AVX opt-in, and shipped"
