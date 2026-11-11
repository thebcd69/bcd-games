#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX ARM64EC: AllocWatch::Clear does nothing, and touches no thread-local, when nothing is watched (night round 2026-11-11, X1 S6).

FEXCore's DeadFlagCalculationEliminination pass ends with AllocWatch::Clear() (RedundantFlagCalculationElimination.cpp:964, 1011, 1043, 1047), a diagnostic
of round 611 that retires "this thread's watches": it takes CurrentThreadId() first, which in the ARM64EC module is the address of a `static thread_local`
byte (implicit TLS), then scans the 512-slot table. Nothing registers a watch any more (ml621: no caller of AllocWatch::Watch is left), so the table is
empty, the scan is dead work on every block compile, and the implicit TLS access is a hazard: the ARM64EC module's TLS is known to be unusable before a
thread's TEB->ThreadLocalStoragePointer exists (PassManager.cpp's note), and the first compile of a 32-bit child's first thread ran inside Wine's loader_init
before it did: `[fex-fault] pc=libarm64ecfex.dll+0x108244 (AllocWatch::Clear+0x14) ... TEB[0x58]=0x0` (GTA V Legacy's child GTA5.exe, ZDWTFF).

The first statement of Clear() now returns when nothing is armed and no watch is live (`!FEX_AllocWatch_Armed && !LiveWatches`): exactly the state in which
the old body removed nothing, did not disarm anything and called nothing observable, so nothing that works today changes (a build that registers watches
takes the old path). Applied to the ARM64EC module only (the WOW64 module reads the thread id from TPIDRRO_EL0 and never had the hazard); applied by
tools/build-xtajit64.sh (AllocWatch.cpp is restored after the build); idempotent, fails by name when the anchor moves.
Usage: patch-fex-ios-allocwatch-clear.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "allocwatch-clear-v1 madeira-bcd"
FILE = "FEXCore/Source/Utils/AllocWatch.cpp"
ANCHOR = "void Clear() {\n  const uint64_t Self = CurrentThreadId();\n"
NEW = (
    "void Clear() {\n"
    "  // " + MARKER + ": nothing is watched (nothing registers a watch since ml621): nothing to retire and no thread id to read; the ARM64EC module's\n"
    "  // implicit TLS is not usable on a thread that has no TLS pointer yet (ZDWTFF)\n"
    "  if (!FEX_AllocWatch_Armed && LiveWatches.load(std::memory_order_relaxed) == 0) {\n"
    "    return;\n"
    "  }\n"
    "  const uint64_t Self = CurrentThreadId();\n"
)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / FILE
    if not path.is_file():
        sys.exit("patch-fex-ios-allocwatch-clear: %s not found (FEX changed)" % FILE)
    src = path.read_text()
    if MARKER in src:
        print("AllocWatch.cpp: the empty-table early return is already in; nothing to do")
        return
    if src.count(ANCHOR) != 1:
        sys.exit("patch-fex-ios-allocwatch-clear: the start of AllocWatch::Clear not found once (FEX changed)")
    path.write_text(src.replace(ANCHOR, NEW))
    print("AllocWatch.cpp: Clear returns at once when nothing is watched (no thread-local read, no table scan)")


main()
