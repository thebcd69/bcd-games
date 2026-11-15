#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""ARM64EC ntdll: the `[rtcs] pre:` log line no longer reads the CPU area of a thread that has none (source form; night round 2026-11-11, R1 S3b).

A native ARM64 thread of an ARM64EC session (services.exe, cmd.exe, a 32-bit child) has TEB->ChpeV2CpuAreaInfo == NULL
(build/ntdll-unix/thread_ios.c, "NOT setting cpu_area"). dlls/ntdll/signal_arm64ec.c prepare_exception_arm64ec logs its first 40
exceptions with `ERR( "[rtcs] pre: ... insim=%u", ..., get_arm64ec_cpu_area()->InSimulation )`; ERR's arguments are evaluated before any output
filter, so the FIRST exception of such a thread read NULL+0 and the fault was redelivered into the same code: "[redeliv] 256 identical
redeliveries ... addr=0x0" and the process ended (KT9K9S, MRX800, tur626's 28 sessions).

This is the source form of the fix that tools/patch-ntdll-rtcs-null-area.py (4 bytes in the committed binary) and the night round's
W-FEX-wine-ntdll.patch describe; the committed arm64ec ntdll.dll is the UNPATCHED file (sha256 596ba0e4...), so no build ships the fix today.
The rebuilt 64-bit set (tools/build-wine-fresh-dlls.sh, env.MADEIRA_WIN64_FRESH = 1) is where this script's result reaches a device.

The only change: `get_arm64ec_cpu_area() ? get_arm64ec_cpu_area()->InSimulation : 0` as the last argument of that ERR. Keyless: the old code
reads a NULL pointer. Idempotent; fails by name when the line moved.
Usage: patch-wine-ntdll-rtcs-area.py WINE-DIR | path/to/wine/dlls/ntdll/signal_arm64ec.c
"""
from pathlib import Path
import sys

FILE = "dlls/ntdll/signal_arm64ec.c"
MARKER = "tools/patch-wine-ntdll-rtcs-area.py"
OLD = """            ERR( "[rtcs] pre: code=%08x addr=%p armPc=%p ecRip=%p rtcs=%p insim=%u\\n",
                 (unsigned int)rec->ExceptionCode, rec->ExceptionAddress,
                 (void *)(ULONG_PTR)arm_ctx->Pc, (void *)(ULONG_PTR)context->AMD64_Context.Rip,
                 pResetToConsistentState, get_arm64ec_cpu_area()->InSimulation );
"""
NEW = """            /* madeira-bcd (night round 2026-11-11, R1 S3b; tools/patch-wine-ntdll-rtcs-area.py): a native ARM64 thread of an ARM64EC session has no
             * CPU area (ChpeV2CpuAreaInfo == NULL, build/ntdll-unix/thread_ios.c "NOT setting cpu_area"). ERR's arguments are evaluated before any
             * output filter, so this read of NULL+0 killed such a thread on its first exception (the "identical redeliveries" storm: KT9K9S, MRX800). */
            ERR( "[rtcs] pre: code=%08x addr=%p armPc=%p ecRip=%p rtcs=%p insim=%u\\n",
                 (unsigned int)rec->ExceptionCode, rec->ExceptionAddress,
                 (void *)(ULONG_PTR)arm_ctx->Pc, (void *)(ULONG_PTR)context->AMD64_Context.Rip,
                 pResetToConsistentState, get_arm64ec_cpu_area() ? get_arm64ec_cpu_area()->InSimulation : 0 );
"""


def main():
    arg = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    path = arg if arg.suffix == ".c" else arg / FILE
    text = path.read_text()
    if MARKER in text:
        print("ntdll [rtcs] CPU area guard: already patched")
        return
    if text.count(OLD) != 1:
        sys.exit("patch-wine-ntdll-rtcs-area: the [rtcs] pre: ERR of prepare_exception_arm64ec was not found exactly once in " + str(path))
    path.write_text(text.replace(OLD, NEW))
    print("ntdll [rtcs] CPU area guard: patched")


if __name__ == "__main__":
    main()
