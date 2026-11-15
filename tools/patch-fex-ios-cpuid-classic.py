#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX CPUID, opt-in: the classic bits every real x86 processor reports (round 626, agent G1 S7; value 2: upstream merge 2026-11-14).

FEX's CPUIDEmu::Function_01h reports EDX bit 2 (debugging extensions) and bit 3 (page size extension) as 0; no real x86 processor
since the Pentium does. Burnout Paradise: The Ultimate Box (Steam 24740, 32-bit BurnoutParadise.exe; Send Logs AQY7NM build 574 and
7MSNGW build 623) tests `cpuid(1); test byte [esp+4],4` (EDX bit 2) in its CPU check and shows its own box "CPU Error / This
machine does not support the SSE2 Command Set", then exits.

With env.MADEIRA_FEX_CPUID_CLASSIC = 1 (exactly "1") both bits are reported as 1 and one line says so
("[fex-cpuid] MADEIRA_FEX_CPUID_CLASSIC=1: ..."). With exactly "2" leaf 0x80000001 also reports EDX bit 26 (1 GB pages), which
FEX reports as 0 too: together these are the three bits FEX's own project turned on for every program (FEX-Emu 9365e6240, Ryan
Houdek, "CPUID: Adds a few new bits"; on Madeira's fork as e0f03c4e1, branch codex/fex-cpuid-test). In BCD they stay opt-in:
unset or any other value, both leaves are bit for bit what they are today, and "1" is what it was before "2" existed. User-mode
code cannot use the features behind the bits, so only a program that tests them sees a difference.

Applied by tools/build-xtajit-wow64.sh (the 32-bit module, the one Burnout needs) and tools/build-xtajit64.sh (every pin); both
restore CPUID.cpp after the build. Idempotent; a file the earlier version of this script patched (cpuid-classic-v1) is brought to
this one; fails by name when an anchor moves, without writing.
Usage: patch-fex-ios-cpuid-classic.py FEX/FEXCore/Source/Interface/Core/CPUID.cpp
"""
import sys

MARKER = "cpuid-classic-v2 madeira-bcd"
V1_MARKER = "cpuid-classic-v1 madeira-bcd"
HEAD = "FEXCore::CPUID::FunctionResults CPUIDEmu::Function_01h(uint32_t Leaf) const {\n"
HEAD_EXT = "FEXCore::CPUID::FunctionResults CPUIDEmu::Function_8000_0001h(uint32_t Leaf) const {\n"
# what the earlier version of this script wrote (a tree a stopped build left patched): undone first, text for text
V1_HELPER = (
    "// " + V1_MARKER + " (tools/patch-fex-ios-cpuid-classic.py, round 626): env MADEIRA_FEX_CPUID_CLASSIC=1 reports CPUID.1:EDX DE and PSE\n"
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
V1_NEW = ("            (MadeiraCpuidClassic() << 2) |  // Debugging extensions (madeira-bcd: MADEIRA_FEX_CPUID_CLASSIC=1)\n"
          "            (MadeiraCpuidClassic() << 3) |  // Page size extension (madeira-bcd: MADEIRA_FEX_CPUID_CLASSIC=1)\n")
HELPER = (
    "// " + MARKER + " (tools/patch-fex-ios-cpuid-classic.py; round 626, value 2 since build 660): env MADEIRA_FEX_CPUID_CLASSIC=1\n"
    "// reports CPUID.1:EDX DE and PSE as every real x86 processor does (Burnout Paradise tests EDX bit 2); =2 also reports 1 GB pages\n"
    "// in CPUID.80000001h:EDX. Unset or any other value: both leaves as before. Read once. Returns 0, 1 or 2.\n"
    "static uint32_t MadeiraCpuidClassic() {\n"
    "  static const uint32_t Level = [] {\n"
    "    const char* Env = getenv(\"MADEIRA_FEX_CPUID_CLASSIC\");\n"
    "    const uint32_t Value = (Env && (Env[0] == '1' || Env[0] == '2') && Env[1] == '\\0') ? static_cast<uint32_t>(Env[0] - '0') : 0u;\n"
    "    if (Value == 1) {\n"
    "      LogMan::Msg::IFmt(\"[fex-cpuid] MADEIRA_FEX_CPUID_CLASSIC=1: CPUID leaf 1 reports DE and PSE (EDX bits 2 and 3)\");\n"
    "    } else if (Value == 2) {\n"
    "      LogMan::Msg::IFmt(\"[fex-cpuid] MADEIRA_FEX_CPUID_CLASSIC=2: CPUID leaf 1 reports DE and PSE (EDX bits 2 and 3), leaf 0x80000001 1 GB pages (EDX bit 26)\");\n"
    "    }\n"
    "    return Value;\n"
    "  }();\n"
    "  return Level;\n"
    "}\n\n"
)
OLD = ("            (0 << 2) |  // Debugging extensions\n"
       "            (0 << 3) |  // Page size extension\n")
NEW = ("            ((MadeiraCpuidClassic() ? 1u : 0u) << 2) |  // Debugging extensions (madeira-bcd: MADEIRA_FEX_CPUID_CLASSIC=1 or 2)\n"
       "            ((MadeiraCpuidClassic() ? 1u : 0u) << 3) |  // Page size extension (madeira-bcd: MADEIRA_FEX_CPUID_CLASSIC=1 or 2)\n")
OLD_EXT = "            (0 << 26) |                               // 1 gigabit pages\n"
NEW_EXT = "            ((MadeiraCpuidClassic() == 2 ? 1u : 0u) << 26) | // 1 gigabit pages (madeira-bcd: MADEIRA_FEX_CPUID_CLASSIC=2)\n"

if len(sys.argv) != 2:
    sys.exit(__doc__.strip().splitlines()[-1])
path = sys.argv[1]
src = open(path).read()
if MARKER in src:
    print("CPUID.cpp: the classic-bits opt-in is already in; nothing to do")
    sys.exit(0)
was_v1 = V1_MARKER in src
if was_v1:
    if src.count(V1_HELPER) != 1 or src.count(V1_NEW) != 1:
        sys.exit("patch-fex-ios-cpuid-classic: the file carries cpuid-classic-v1 but not as the earlier script wrote it; restore CPUID.cpp")
    src = src.replace(V1_HELPER, "", 1).replace(V1_NEW, OLD, 1)
if src.count(HEAD) != 1:
    sys.exit("patch-fex-ios-cpuid-classic: Function_01h not found once (FEX changed)")
start = src.index(HEAD)
end = src.index("\n}\n", start)
if src.count(OLD, start, end) != 1:
    sys.exit("patch-fex-ios-cpuid-classic: the EDX DE/PSE lines not found once in Function_01h (FEX changed)")
if src.count(HEAD_EXT) != 1 or src.index(HEAD_EXT) < end:
    sys.exit("patch-fex-ios-cpuid-classic: Function_8000_0001h not found once after Function_01h (FEX changed)")
xstart = src.index(HEAD_EXT)
xend = src.index("\n}\n", xstart)
if src.count(OLD_EXT, xstart, xend) != 1:
    sys.exit("patch-fex-ios-cpuid-classic: the 1 GB pages line not found once in Function_8000_0001h (FEX changed)")
src = src[:xstart] + src[xstart:xend].replace(OLD_EXT, NEW_EXT, 1) + src[xend:]
body = src[start:end].replace(OLD, NEW, 1)
open(path, "w").write(src[:start] + HELPER + body + src[end:])
print("CPUID.cpp: leaf 1 reports DE and PSE with MADEIRA_FEX_CPUID_CLASSIC=1 (EDX unchanged otherwise)"
      "; =2 adds 1 GB pages in leaf 0x80000001" + (" (brought up from cpuid-classic-v1)" if was_v1 else ""))
