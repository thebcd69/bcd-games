#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX CPUID, opt-in: leaf 1 reports DE and PSE as every real x86 processor does (round 626, agent G1 S7).

FEX's CPUIDEmu::Function_01h reports EDX bit 2 (debugging extensions) and bit 3 (page size extension) as 0; no real x86 processor
since the Pentium does. Burnout Paradise: The Ultimate Box (Steam 24740, 32-bit BurnoutParadise.exe; Send Logs AQY7NM build 574 and
7MSNGW build 623) tests `cpuid(1); test byte [esp+4],4` (EDX bit 2) in its CPU check and shows its own box "CPU Error / This
machine does not support the SSE2 Command Set", then exits.

With env.MADEIRA_FEX_CPUID_CLASSIC = 1 (exactly "1") both bits are reported as 1 and one line says so
("[fex-cpuid] MADEIRA_FEX_CPUID_CLASSIC=1: ..."); unset or any other value, EDX is bit for bit what it is today. User-mode code
cannot use the features behind the two bits, so only a program that tests them sees a difference.

Applied by tools/build-xtajit-wow64.sh (the 32-bit module, the one Burnout needs) and tools/build-xtajit64.sh (every pin); both
restore CPUID.cpp after the build. Idempotent; fails by name when an anchor moves.
Usage: patch-fex-ios-cpuid-classic.py FEX/FEXCore/Source/Interface/Core/CPUID.cpp
"""
import sys

MARKER = "cpuid-classic-v1 madeira-bcd"
HEAD = "FEXCore::CPUID::FunctionResults CPUIDEmu::Function_01h(uint32_t Leaf) const {\n"
HELPER = (
    "// " + MARKER + " (tools/patch-fex-ios-cpuid-classic.py, round 626): env MADEIRA_FEX_CPUID_CLASSIC=1 reports CPUID.1:EDX DE and PSE\n"
    "// as every real x86 processor does (Burnout Paradise tests EDX bit 2). Unset or any other value: EDX as before. Read once.\n"
    "static uint32_t MadeiraCpuidClassic() {\n"
    "  static const uint32_t On = [] {\n"
    "    const char* Env = getenv(\"MADEIRA_FEX_CPUID_CLASSIC\");\n"
    "    const uint32_t Value = (Env && Env[0] == '1' && Env[1] == '\\0') ? 1u : 0u;\n"
    "    if (Value) {\n"
    "      LogMan::Msg::IFmt(\"[fex-cpuid] MADEIRA_FEX_CPUID_CLASSIC=1: CPUID leaf 1 reports DE and PSE (EDX bits 2 and 3)\");\n"
    "    }\n"
    "    return Value;\n"
    "  }();\n"
    "  return On;\n"
    "}\n\n"
)
OLD = ("            (0 << 2) |  // Debugging extensions\n"
       "            (0 << 3) |  // Page size extension\n")
NEW = ("            (MadeiraCpuidClassic() << 2) |  // Debugging extensions (madeira-bcd: MADEIRA_FEX_CPUID_CLASSIC=1)\n"
       "            (MadeiraCpuidClassic() << 3) |  // Page size extension (madeira-bcd: MADEIRA_FEX_CPUID_CLASSIC=1)\n")

if len(sys.argv) != 2:
    sys.exit(__doc__.strip().splitlines()[-1])
path = sys.argv[1]
src = open(path).read()
if MARKER in src:
    print("CPUID.cpp: the classic DE/PSE opt-in is already in; nothing to do")
    sys.exit(0)
if src.count(HEAD) != 1:
    sys.exit("patch-fex-ios-cpuid-classic: Function_01h not found once (FEX changed)")
start = src.index(HEAD)
end = src.index("\n}\n", start)
if src.count(OLD, start, end) != 1:
    sys.exit("patch-fex-ios-cpuid-classic: the EDX DE/PSE lines not found once in Function_01h (FEX changed)")
body = src[start:end].replace(OLD, NEW, 1)
open(path, "w").write(src[:start] + HELPER + body + src[end:])
print("CPUID.cpp: leaf 1 reports DE and PSE with MADEIRA_FEX_CPUID_CLASSIC=1 (EDX unchanged otherwise)")
