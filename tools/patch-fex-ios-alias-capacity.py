#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A bigger alias table for the game that asks for it, and the table's state in the miss line (round 624, agent A S7).

Borderlands: Game of the Year Enhanced (Send Log V0FQAS, build 622): 60 s in, a thread calls the freshly loaded ARM64EC
cryptnet.dll through its JIT-pool copy; FEX's alias table has no entry for that copy ("[iOS-xquery] MISS ... rev=<the address
itself>"), so the block is refused ("NoExec instruction in entry block") and the game dies with an access violation, while
Wine's own table knows the copy ("[rip-leak] guest RIP ... IS POOL addr = PE ..."). The game's emulator received 120 aliases
when it registered and 184 more images afterwards; FEX's table (Source/Windows/ARM64EC/IosJitAlias.cpp) holds 256 entries and
refuses every push beyond them without a word (tools/patch-fex-ios-alias-full-quiet.py made that silent): replaying FEX's own
rules over the session's image loads, the table is full from the 17,245th log line and the last 21 images, cryptnet.dll among
them, were refused.

The array becomes 1024 entries, but the limit in force stays 256 unless env.MADEIRA_FEX_ALIAS_MAX names a bigger one (read once
in ProcessInit, clamped to 256..1024): a game that works today gets byte-for-byte the same table. The "[iOS-xquery] MISS" line
says how full the table is and how many pushes it refused, and ProcessInit logs the limit when the key is set.

Applied by tools/build-xtajit64.sh to every FEX pin, after the other overlays. Idempotent (marker "alias-capacity-v1");
fails by name when an anchor moved.

  patch-fex-ios-alias-capacity.py FEX_ROOT
"""
import sys
from pathlib import Path

MARK = "alias-capacity-v1"


def edit(text, old, new, what):
    if text.count(old) != 1:
        print("::error::patch-fex-ios-alias-capacity: %s: anchor found %d times (FEX changed)" % (what, text.count(old)))
        sys.exit(1)
    return text.replace(old, new, 1)


def main(root):
    alias = Path(root) / "Source/Windows/ARM64EC/IosJitAlias.cpp"
    module = Path(root) / "Source/Windows/ARM64EC/Module.cpp"
    a, m = alias.read_text(), module.read_text()
    if MARK in a and MARK in m:
        print("already patched")
        return
    if MARK in a or MARK in m:
        print("::error::patch-fex-ios-alias-capacity: only one of the two files carries the patch")
        sys.exit(1)

    a = edit(a, "constexpr int kMaxEntries = 256;\n",
             "/* madeira-bcd alias-capacity-v1 (round 624, Borderlands GOTY Enhanced V0FQAS): room for 1024 entries; the limit in force\n"
             " * (g_AliasCap) stays 256 unless env.MADEIRA_FEX_ALIAS_MAX asks for more (ProcessInit, IosAliasSetCapacity). Module.S walks\n"
             " * IosAliasCount entries, never the array's size. */\n"
             "constexpr int kMaxEntries = 1024;\n"
             "static int g_AliasCap = 256;\n"
             "static uint64_t g_AliasRefused = 0;\n", "kMaxEntries")
    a = edit(a, "  if (count >= kMaxEntries) {\n", "  if (count >= g_AliasCap) {   /* madeira-bcd alias-capacity-v1 */\n", "the refusal")
    a = edit(a, "    static uint64_t refused = 0;\n    __atomic_fetch_add(&refused, 1, __ATOMIC_RELAXED);\n",
             "    __atomic_fetch_add(&g_AliasRefused, 1, __ATOMIC_RELAXED);   /* madeira-bcd alias-capacity-v1: read by IosAliasStats */\n",
             "the refused counter")
    a = edit(a, "uint64_t IosJitTranslate(uint64_t Addr) {\n",
             "/* madeira-bcd alias-capacity-v1: the limit (256..1024; ProcessInit, env.MADEIRA_FEX_ALIAS_MAX) and the table's state for\n"
             " * the miss line. Called from Module.cpp, which cannot reach this TU's globals itself. */\n"
             "void IosAliasSetCapacity(int Cap) {\n"
             "  g_AliasCap = Cap < 256 ? 256 : Cap > kMaxEntries ? kMaxEntries : Cap;\n"
             "}\n"
             "\n"
             "void IosAliasStats(int* Count, int* Cap, uint64_t* Refused) {\n"
             "  *Count = g_EntryCount;\n"
             "  *Cap = g_AliasCap;\n"
             "  *Refused = __atomic_load_n(&g_AliasRefused, __ATOMIC_RELAXED);\n"
             "}\n"
             "\n"
             "uint64_t IosJitTranslate(uint64_t Addr) {\n", "IosJitTranslate")

    m = edit(m, "extern \"C\" uint64_t IosJitReverseTranslate(uint64_t Addr);\n",
             "extern \"C\" uint64_t IosJitReverseTranslate(uint64_t Addr);\n"
             "extern \"C\" void IosAliasSetCapacity(int Cap);                      // madeira-bcd alias-capacity-v1\n"
             "extern \"C\" void IosAliasStats(int* Count, int* Cap, uint64_t* Refused);   // madeira-bcd alias-capacity-v1\n",
             "the declarations")
    m = edit(m, "        LogMan::Msg::EFmt(\"[iOS-xquery] MISS tracker={} addr={:#x} rev={:#x}\",\n"
                "                          static_cast<void*>(&*InvalidationTracker), Address, IosJitReverseTranslate(Address));\n",
             "        int AliasCount = 0, AliasCap = 0;   /* madeira-bcd alias-capacity-v1: how full the table is */\n"
             "        uint64_t AliasRefused = 0;\n"
             "        IosAliasStats(&AliasCount, &AliasCap, &AliasRefused);\n"
             "        LogMan::Msg::EFmt(\"[iOS-xquery] MISS tracker={} addr={:#x} rev={:#x} alias-table={}/{} refused={}\",\n"
             "                          static_cast<void*>(&*InvalidationTracker), Address, IosJitReverseTranslate(Address), AliasCount, AliasCap,\n"
             "                          AliasRefused);\n", "the miss line")
    m = edit(m, "  LogMan::Msg::IFmt(\"FEX: branch-history-v1 madeira-bcd enabled={} exe={}\",\n"
                "                   FEXCore::Core::IosGuestBranchTraceEnabled != 0, ExecutableName);\n#endif\n",
             "  LogMan::Msg::IFmt(\"FEX: branch-history-v1 madeira-bcd enabled={} exe={}\",\n"
             "                   FEXCore::Core::IosGuestBranchTraceEnabled != 0, ExecutableName);\n"
             "  {\n"
             "    /* madeira-bcd alias-capacity-v1 (round 624): env.MADEIRA_FEX_ALIAS_MAX = 1024 lets this process's alias table hold more than\n"
             "     * 256 image copies (Borderlands GOTY Enhanced: 304). Unset or not a number: 256, as before. */\n"
             "    const char* CapOpt = getenv(\"MADEIRA_FEX_ALIAS_MAX\");\n"
             "    int Cap = 0;\n"
             "    for (const char* p = CapOpt; p && *p >= '0' && *p <= '9' && Cap < 100000; p++) {\n"
             "      Cap = Cap * 10 + (*p - '0');\n"
             "    }\n"
             "    if (Cap > 0) {\n"
             "      int Count = 0, InForce = 0;\n"
             "      uint64_t Refused = 0;\n"
             "      IosAliasSetCapacity(Cap);\n"
             "      IosAliasStats(&Count, &InForce, &Refused);\n"
             "      LogMan::Msg::IFmt(\"[iOS-alias] madeira-bcd alias table limit {} (MADEIRA_FEX_ALIAS_MAX={}) exe={}\", InForce, CapOpt, ExecutableName);\n"
             "    }\n"
             "  }\n"
             "#endif\n", "ProcessInit")
    alias.write_text(a)
    module.write_text(m)
    print("patched")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
