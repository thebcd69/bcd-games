#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX ARM64EC: a thread whose emulator state was destroyed no longer names it in its CPU area (night round 2026-11-11, X1 S5).

ThreadTerm (called by the EC ntdll's NtTerminateThread wrapper before the system call) destroys the thread's InternalThreadState
(`CTX->DestroyThread(ThreadState)`) but left the CPU area's emulator slots, EmulatorData[1] (ThreadState) and EmulatorData[0] (StateFrame), pointing at
the freed object. The thread (or a thread terminating it) still runs host code afterwards, and an exception there reaches ResetToConsistentState, whose
`if (!CPUArea.ThreadState()) return STATUS_SUCCESS;` is the guard meant for exactly a thread without state; with the stale pointer it went on and
called `CTX->IsAddressInCodeBuffer(Thread, pc)` on the freed state: `[fex-fault] pc=libarm64ecfex.dll+0x1d1e4 (CPUBackend::IsAddressInCodeBuffer+0x14)
fault-addr=0x97fff5daaa1303e8` (the freed memory held code bytes) on Splinter Cell Conviction's thread 0x58 right after `[thr-term] ... deinit`,
the redelivery of the engine's own fault in `exit_thread` (2QWXFW; the engine half is build/ntdll-unix/thread_ios.c, see W-FEX.md).

After the state is destroyed ThreadTerm now clears both slots. Every user of the slots already tolerates "no state": ResetToConsistentState returns
STATUS_SUCCESS (Wine goes on with the exception), the memory hooks return (they test the thread state), BTCpu64IosReleaseThreadHolds tests it. Only the
slots of a thread that is being terminated change, after the state they pointed to is gone; ThreadTerm itself keeps its local copy.
Applied by tools/build-xtajit64.sh to every pin (Module.cpp is restored after the build); idempotent, fails by name when the anchor moves.
Usage: patch-fex-ios-threadterm-clear-area.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "threadterm-clear-area-v1 madeira-bcd"
ANCHOR = "  CTX->DestroyThread(ThreadState);\n  ::VirtualFree(reinterpret_cast<void*>(CPUArea.EmulatorStackLimit()), 0, MEM_RELEASE);\n"
NEW = (
    "  CTX->DestroyThread(ThreadState);\n"
    "  // " + MARKER + ": the state is gone; the CPU area must not name it (an exception on this thread reaches ResetToConsistentState,\n"
    "  // whose `!CPUArea.ThreadState()` guard is for exactly this: Splinter Cell Conviction 2QWXFW read the freed state through the stale slot)\n"
    "  CPUArea.ThreadState() = nullptr;\n"
    "  CPUArea.StateFrame() = nullptr;\n"
    "  ::VirtualFree(reinterpret_cast<void*>(CPUArea.EmulatorStackLimit()), 0, MEM_RELEASE);\n"
)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / "Source/Windows/ARM64EC/Module.cpp"
    src = path.read_text()
    if MARKER in src:
        print("ARM64EC/Module.cpp: the ThreadTerm slot clearing is already in; nothing to do")
        return
    i = src.find("NTSTATUS ThreadTerm(HANDLE Thread, LONG ExitCode) {")
    if i < 0 or src.count("NTSTATUS ThreadTerm(HANDLE Thread, LONG ExitCode) {") != 1:
        sys.exit("patch-fex-ios-threadterm-clear-area: ThreadTerm not found once (FEX changed)")
    j = src.find("\n}\n", i)
    body = src[i:j]
    if body.count(ANCHOR) != 1:
        sys.exit("patch-fex-ios-threadterm-clear-area: the DestroyThread/VirtualFree lines not found once in ThreadTerm (FEX changed)")
    path.write_text(src[:i] + body.replace(ANCHOR, NEW) + src[j:])
    print("ARM64EC/Module.cpp: ThreadTerm clears the CPU area's thread-state and state-frame slots once the state is destroyed")


main()
