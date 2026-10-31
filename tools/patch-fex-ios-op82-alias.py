#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX (32-bit guest): translate the 0x82 alias of the byte-immediate ALU group.

In 32-bit mode the opcode byte 0x82 is 0x80 under another number: `op r/m8, imm8` with ADD, OR, ADC, SBB, AND, SUB, XOR or CMP chosen by the
ModRM reg field (Intel SDM Table A-2: Grp1 Eb,Ib with the note "invalid in 64-bit mode"). FEX's decode tables enter 0x82 (BaseTables.cpp, and
X86Tables/PrimaryGroupTables.cpp routes its ADD, OR, AND, SUB and XOR through TYPE_ARCH_DISPATCHER to SecondaryALUOp), but SecondaryALUOp's
switch only names OpToIndex(0x80), 0x81 and 0x83, so those five fell to `default:` ("Unknown ALU Op: 0x10 / 0x11 / 0x14 / 0x15 / 0x16": prefix index 2
= 0x82, reg 0 = ADD, 1 = OR, 4 = AND, 5 = SUB, 6 = XOR) and the instruction became a trap (STATUS_ILLEGAL_INSTRUCTION, c000001d). ADC, SBB and CMP
(reg 2, 3, 7) go through Bind<ADCOp/SBBOp/CMPOp, 1>, which does not look at Op->OP, and always worked.
Logs (round 624, agent E1): Ubisoft Connect (upc.exe) P4KC3V, W749H9, ZWB3JT; South Park: The Fractured But Whole S78B5M; Assassin's
Creed 2 AEKXPC (the same trap address 0x2c1662f); Assassin's Creed III Liberation HD XSSVTQ (uplay_r1_loader.dll).

The fix is five more case labels in front of the existing 0x83 ones: the same IR op, the same atomic op, the same ALUOp(..., 1) call.
The 64-bit module is not changed in behaviour (0x82 stays invalid there: the ArchSelect entry for 64-bit mode is TYPE_INVALID).

Run by tools/build-xtajit-wow64.sh (the 32-bit module only); idempotent, fails by name when an anchor moves.
Usage: patch-fex-ios-op82-alias.py FEX/FEXCore/Source/Interface/Core/OpcodeDispatcher.cpp
"""
import sys

path = sys.argv[1]
src = open(path).read()
head = "void OpDispatchBuilder::SecondaryALUOp(OpcodeArgs) {"
start = src.find(head)
if start < 0 or src.count(head) != 1:
    sys.exit("patch-fex-ios-op82-alias: SecondaryALUOp not found (FEX changed)")
end = src.find("#undef OPD", start)
body = src[start:end]
if "OpToIndex(0x82)" in body:
    print("OpcodeDispatcher.cpp: SecondaryALUOp already knows the 0x82 alias; nothing to do")
    sys.exit(0)
for reg in (0, 1, 4, 5, 6):
    anchor = "  case OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x83), %d):\n" % reg
    if body.count(anchor) != 1:
        sys.exit("patch-fex-ios-op82-alias: anchor for reg %d not found once (FEX changed)" % reg)
    label = "  case OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x82), %d):\n" % reg
    if reg == 0:
        label = "  // madeira-bcd: 0x82 is 0x80's 32-bit-only alias (tools/patch-fex-ios-op82-alias.py)\n" + label
    body = body.replace(anchor, label + anchor)
open(path, "w").write(src[:start] + body + src[end:])
print("OpcodeDispatcher.cpp: SecondaryALUOp translates the 0x82 alias (ADD, OR, AND, SUB, XOR)")
