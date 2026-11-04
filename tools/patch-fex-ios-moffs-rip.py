#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX, opt-in: MOV moffs (opcodes A0-A3) records its guest RIP like every other memory instruction (round 630, agent O3 S9).

Unreal Gold (Steam 13250, 32-bit Unreal.exe; Send Logs EQ6DMS, X3HYP5 build 622, D1D3P2, 43QKTT build 625, Y1G0A7 build 627): the fire
texture code in Fire.dll patches the constants of its own inner loop with stores of the form MOV [address],EAX, on the page of the
running block. FEX's inline self-modifying-code handler rebuilds the guest state from the block's RIP table and single-steps from
there; the table has an entry only for instructions OpDispatchBuilder::CanHaveSideEffects marks, and it knows the memory operands
GPR/RIP/SIB but not the literal address of MOV moffs. The handler therefore resumed at the last marked instruction in front of the store
and ran the register-only instructions in between a second time (Y1G0A7: "rip: 78D5DD5" then "rip: 78D5DD6" for the store at
Fire.dll+0x15DD6, the INC EAX between them ran twice), so the patched constants were off by one or two and the loop ran off its buffer
(Fire.dll+0x15E62, every start, 24 s in).

With env.MADEIRA_FEX_MOFFS_RIP = 1 (exactly "1") the four opcodes get a guest-RIP entry: one IR marker that emits no host instruction,
only an entry in the block's RIP table, so the handler resumes at the store itself. One "[fex-moffs]" line says it is on. Unset or any
other value, the generated code and tables are bit for bit as before.

Applied by tools/build-xtajit-wow64.sh only (the 32-bit module; in 64-bit code the form is MOVABS and nothing shows a 64-bit program
affected), which restores Core.cpp after its build. Idempotent; fails by name when an anchor moves (the build goes on without it).
Usage: patch-fex-ios-moffs-rip.py FEX/FEXCore/Source/Interface/Core/Core.cpp
"""
from pathlib import Path
import sys

MARKER = "moffs-rip-v1 madeira-bcd"
HEAD = ("ContextImpl::GenerateIRResult\n"
        "ContextImpl::GenerateIR(FEXCore::Core::InternalThreadState* Thread, uint64_t GuestRIP, bool ExtendedDebugInfo, uint64_t MaxInst) {\n")
HELPER = (
    "// " + MARKER + " (tools/patch-fex-ios-moffs-rip.py, round 630, Unreal Gold): env MADEIRA_FEX_MOFFS_RIP=1 gives MOV moffs\n"
    "// (opcodes A0-A3) a guest-RIP entry like every other memory instruction. CanHaveSideEffects knows the GPR/RIP/SIB memory operands\n"
    "// but not the literal address of MOV moffs, so the inline self-modifying-code handler resumed in front of such a store and ran the\n"
    "// register-only instructions before it twice. The entry emits no host instruction. Unset or any other value: unchanged. Read once.\n"
    "static bool MadeiraMoffsRip(const FEXCore::X86Tables::X86InstInfo* TableInfo) {\n"
    "  static const bool On = [] {\n"
    "    const char* Env = getenv(\"MADEIRA_FEX_MOFFS_RIP\");\n"
    "    const bool Value = Env && Env[0] == '1' && Env[1] == '\\0';\n"
    "    if (Value) {\n"
    "      LogMan::Msg::IFmt(\"[fex-moffs] MADEIRA_FEX_MOFFS_RIP=1: MOV moffs instructions record their guest RIP\");\n"
    "    }\n"
    "    return Value;\n"
    "  }();\n"
    "  return On && TableInfo && (TableInfo->Flags & FEXCore::X86Tables::InstFlags::FLAGS_MEM_OFFSET);\n"
    "}\n\n"
)
OLD = "        if (ExtendedDebugInfo || Thread->OpDispatcher->CanHaveSideEffects(TableInfo, DecodedInfo)) {\n"
NEW = ("        if (ExtendedDebugInfo || Thread->OpDispatcher->CanHaveSideEffects(TableInfo, DecodedInfo) ||\n"
       "            MadeiraMoffsRip(TableInfo)) { // madeira-bcd: MADEIRA_FEX_MOFFS_RIP=1 (tools/patch-fex-ios-moffs-rip.py)\n")


def patch(src):
    if MARKER in src:
        if src.count(HELPER + HEAD) != 1 or src.count(NEW) != 1 or OLD in src:
            raise ValueError("partial or changed moffs-rip patch")
        return src
    if src.count(HEAD) != 1:
        raise ValueError("ContextImpl::GenerateIR not found once (FEX changed)")
    start = src.index(HEAD)
    end = src.index("\n}\n", start)
    if src.count(OLD) != 1 or not (start < src.index(OLD) < end):
        raise ValueError("the guest-opcode condition of GenerateIR not found once (FEX changed)")
    return src[:start] + HELPER + src[start:].replace(OLD, NEW, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1])
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-moffs-rip: " + str(error))
    if result == source:
        print("patch-fex-ios-moffs-rip: already in")
    else:
        path.write_text(result)
        print("FEX: MOV moffs records its guest RIP with MADEIRA_FEX_MOFFS_RIP=1 (unchanged otherwise)")
