#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX: BSF/BSR/BTC/MOVSX (and CMOVcc) with a redundant REPNE/REP prefix are decoded like their unprefixed forms (night round 2026-11-11, G2 S9).

x86 processors ignore a REPNE (F2) or REP (F3) prefix on an instruction it does not apply to: `f2 0f bc ee` is BSF ebp,esi and runs. FEX's
secondary opcode tables for the F2 prefix mark 0F BB..BF invalid (BTC, BSF, BSR, MOVSX r,r/m8, MOVSX r,r/m16) although they already copy the
unprefixed entry for 0F B0..B7, B9, BA and 80..AF. Copy-protection stubs sprinkle junk prefixes over their code: Blur's obfuscated start-up
stub (43HNJX, 32-bit, build 634) reaches `f2 0f bc ee` and the decoder reports `Invalid instruction in entry block: 136C70B`,
`[entry-fail] ... invalid instruction (hlt thunk)`, exit code 0xc000001d, before the game shows anything.

The entries that were TYPE_INVALID become TYPE_COPY_OTHER (the table generator then copies the unprefixed instruction, handler included, exactly
as for the neighbouring entries):
  * RepNEModOps 0F BB..BF (the logged instruction);
  * the same class, no log yet: RepModOps 0F BB and 0F BE..BF (REP-prefixed BTC / MOVSX), and CMOVcc 0F 40..4F in both prefix tables.
An INVALID entry only ever trapped, so nothing that runs today can change; the ARM64EC and WOW64 modules share the table, both builds apply it.
Idempotent, all-or-nothing, fails by name when an anchor moves. Usage: patch-fex-ios-repne-bit-ops.py FEX-ROOT
"""
from pathlib import Path
import re
import sys

MARKER = "repne-bit-ops-v1 madeira-bcd"
FILE = "FEXCore/Source/Interface/Core/X86Tables/SecondaryTables.cpp"

REP_START = "constexpr std::array<X86InstInfo, MAX_REP_MOD_TABLE_SIZE> RepModOps = []() consteval {"
REP_END = "GenerateTableWithCopy(Table.data(), RepModOpTable,"
REPNE_START = "constexpr std::array<X86InstInfo, MAX_REPNE_MOD_TABLE_SIZE> RepNEModOps = []() consteval {"
REPNE_END = "GenerateTableWithCopy(Table.data(), RepNEModOpTable,"

# (table region, opcode start, count, what): the entry must be exactly one TYPE_INVALID line of that start and count
TARGETS = [
    ("REPNE", 0xBB, 5, "F2 0F BB..BF"),
    ("REP", 0xBB, 1, "F3 0F BB"),
    ("REP", 0xBE, 2, "F3 0F BE..BF"),
    ("REP", 0x40, 16, "F3 0F 40..4F"),
    ("REPNE", 0x40, 16, "F2 0F 40..4F"),
]


def region(text, start, end):
    a = text.index(start)
    b = text.index(end, a)
    return a, b


def entry_re(op, n):
    return re.compile(r'(?m)^(?P<head>\s*\{0x%X, %d,\s+X86InstInfo\{"",\s+)TYPE_INVALID(?P<tail>,\s+FLAGS_NONE,\s+0\}\},)$' % (op, n))


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / FILE
    if not path.is_file():
        sys.exit("patch-fex-ios-repne-bit-ops: %s not found (FEX changed)" % FILE)
    src = path.read_text()
    if MARKER in src:
        print("SecondaryTables.cpp: the REP/REPNE bit-op entries are already in; nothing to do")
        return
    out = src
    for table, op, n, what in TARGETS:
        start, end = (REPNE_START, REPNE_END) if table == "REPNE" else (REP_START, REP_END)
        if out.count(start) != 1:
            sys.exit("patch-fex-ios-repne-bit-ops: the %s table not found once (FEX changed)" % table)
        a, b = region(out, start, end)
        body = out[a:b]
        m = entry_re(op, n).findall(body)
        if len(m) != 1:
            sys.exit("patch-fex-ios-repne-bit-ops: the %s entry {0x%X, %d} (%s) is not one TYPE_INVALID line (found %d; FEX changed)" % (table, op, n, what, len(m)))
        body = entry_re(op, n).sub(lambda mo: mo.group("head") + "TYPE_COPY_OTHER" + mo.group("tail") + "   // " + MARKER + ": " + what + " = the unprefixed instruction", body)
        out = out[:a] + body + out[b:]
    path.write_text(out)
    print("SecondaryTables.cpp: REPNE/REP-prefixed BTC, BSF, BSR, MOVSX and CMOVcc decode like the unprefixed instructions")


main()
