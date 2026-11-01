#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX ARM64EC: an exception that arrives after the emulator was torn down goes on to Wine (round 626, agent C S4).

ResetToConsistentStateImpl (Source/Windows/ARM64EC/Module.cpp) asks the file-scope context whether the faulting pc is JIT code
(`bool IsJIT = CTX->IsAddressInCodeBuffer(Thread, NativeContext->Pc);`). When a process is ending, a late exception on one of its
exiting threads can arrive after `CTX` (a `fextl::unique_ptr`) was already reset: the handler then faults on the null pointer itself
(`[fex-fault] pc=libarm64ecfex.dll+0x1123c4 ... fault-addr=0x0` in builds 624-625, about 50 times in a row: Clair Obscur:
Expedition 33's crash reporter child HWMBZ2 and MKX1Z2, DQQR62, CDPEFN, 1FJAWW). Without a context nothing in FEX can handle the
exception, so the patch returns false there ("not FEX's"), as the handler already does for every pc outside the JIT: Wine
dispatches the exception to the thread, which is ending anyway.

Only the CTX == NULL case changes, and that case faults inside FEX today; with a context, the handler runs as before.
Applied by tools/build-xtajit64.sh to every pin (Module.cpp is restored after the build); idempotent, fails by name when the
anchor moves.
Usage: patch-fex-ios-ec-teardown-ctx.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "ec-teardown-ctx-v1 madeira-bcd"
ANCHOR = "  bool IsJIT = CTX->IsAddressInCodeBuffer(Thread, NativeContext->Pc);\n"
GUARD = (
    "  if (!CTX) {   // " + MARKER + " (round 626): the emulator was torn down while the process ends; nothing here can\n"
    "    return false;   // handle this exception, so it goes on to Wine (as for any pc outside the JIT) instead of faulting here\n"
    "  }\n"
)

if len(sys.argv) != 2:
    sys.exit(__doc__.strip().splitlines()[-1])
path = Path(sys.argv[1]) / "Source/Windows/ARM64EC/Module.cpp"
src = path.read_text()
if MARKER in src:
    print("ARM64EC/Module.cpp: the torn-down context guard is already in; nothing to do")
    sys.exit(0)
head = "bool ResetToConsistentStateImpl(const ThreadCPUArea CPUArea, EXCEPTION_RECORD* Exception, CONTEXT* GuestContext, ARM64_NT_CONTEXT* NativeContext) {"
if src.count(head) != 1:
    sys.exit("patch-fex-ios-ec-teardown-ctx: ResetToConsistentStateImpl not found once (FEX changed)")
start = src.index(head)
end = src.index("\n}\n", start)
if src.count(ANCHOR, start, end) != 1:
    sys.exit("patch-fex-ios-ec-teardown-ctx: the IsJIT line not found once in ResetToConsistentStateImpl (FEX changed)")
at = src.index(ANCHOR, start, end)
path.write_text(src[:at] + GUARD + src[at:])
print("ARM64EC/Module.cpp: an exception after the emulator was torn down goes on to Wine (no fault on a null context)")
