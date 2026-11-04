#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX: RDTSCP and RDPID report processor 0 on iOS instead of ending the process at translation time (round 630, agent G3 S11).

Hearts of Iron IV's Paradox launcher stub dowser.exe (TM3HCY, build 614) is built with Go, whose runtime keeps an RDTSCP path; FEX's
ProcessorID op reads the CPU number from TPIDRRO_EL0, which iOS does not provide, and on Windows that branch is
ERROR_AND_DIE_FMT("Unsupported"): the assertion ("A <tid> Unsupported", the only FEX assertion line in about 2,600 archive logs) ended
the process while the block was being translated, whether or not the instruction would ever run. It now loads the constant 0 (the
IA32_TSC_AUX value RDTSCP returns and the value RDPID returns): a processor number the guest can always use (it sees six). The same
edit is upstream FEX's 7b46eb299. CPUID is not touched: what a program's CPU check sees stays as it is.

Only a path that ends the process today changes: no working program has either instruction in a translated block. Applied by
tools/build-xtajit64.sh and tools/build-xtajit-wow64.sh (MiscOps.cpp is FEXCore's, built into both modules; each restores it after its
build); idempotent; when FEX already handles the case itself, or the anchor moved, it says so (the build goes on).
Usage: patch-fex-ios-processorid.py FEX/FEXCore/Source/Interface/Core/JIT/MiscOps.cpp
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: ProcessorID reports CPU 0"
ANCHOR = ("#ifdef _WIN32\n"
          "  else {\n"
          "    // If on Windows and TPIDRRO isn't supported (like in wine), then this is a programming error.\n"
          "    ERROR_AND_DIE_FMT(\"Unsupported\");\n"
          "  }\n")
NEW = ("#ifdef _WIN32\n"
       "  else {\n"
       "    // " + MARKER + " (tools/patch-fex-ios-processorid.py, round 630, Hearts of Iron IV's dowser.exe TM3HCY): iOS has no CPU\n"
       "    // number in TPIDRRO_EL0, and a Go runtime keeps an RDTSCP path that FEX translates without ever running it. RDTSCP's IA32_TSC_AUX\n"
       "    // and RDPID read 0 instead of the translation ending the process.\n"
       "    LoadConstant(ARMEmitter::Size::i32Bit, GetReg(Node), 0);\n"
       "    return;\n"
       "  }\n")


def patch(src):
    if MARKER in src:
        if src.count(NEW) != 1:
            raise ValueError("partial or changed processorid patch")
        return src, "already patched"
    fn = src.index("DEF_OP(ProcessorID) {") if "DEF_OP(ProcessorID) {" in src else -1
    if fn < 0:
        raise ValueError("DEF_OP(ProcessorID) not found (FEX changed)")
    body = src[fn:src.index("\n}\n", fn)]
    if 'ERROR_AND_DIE_FMT("Unsupported");' not in body:
        return src, "FEX handles it itself"
    if src.count(ANCHOR) != 1 or src.index(ANCHOR) < fn:
        raise ValueError("the _WIN32 branch of DEF_OP(ProcessorID) not found once (FEX changed)")
    return src.replace(ANCHOR, NEW, 1), "applied"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result, how = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-processorid: " + str(error))
    if result != source:
        path.write_text(result)
    print("patch-fex-ios-processorid: " + how)
