#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright 2026 Dragooon: code taken from https://github.com/willfaust/FEX (pull request #40, commit 71d78809: no invalidation for an
# unmapped section that was never executable; the sub-floor check follows the same pull request's commit e6906d84) under GPL-3.0-or-later with
# the Madeira Converter Exception and modified for BCD in November 2026. BCD's terms above do not apply to those lines. See THIRD-PARTY-NOTICES.md.
"""FEX: skip the code invalidation of an unmapped section that was never executable (opt-in: MADEIRA_FEX_SECTION_SKIP=1).

Every unmapped section view takes the exclusive CodeInvalidationMutex, walks every thread's lookup cache and writes an "[iOS-xrem] via=section"
line. A game that streams assets through file views does that dozens of times a second, for data that was never code: BCD's pulled logs hold
1.85 million of these lines in 4,304 logs (29 logs with 10,000 or more: Red Dead Redemption 2, GTA V and IV, Call of Duty: World at War,
Far Cry 3, Fallout 4), and 118 logs have "[fexlock] STUCK" lines behind that lock.

Nothing is compiled from memory outside XIntervals (code is only translated from executable memory, and taking a range out of XIntervals
invalidates it first), so a freed section that does not intersect XIntervals has nothing to invalidate. With MADEIRA_FEX_SECTION_SKIP=1
(exactly "1"; unset or anything else: not one byte of behaviour changes) InvalidateContainingSection(Free) then
  - leaves out InvalidateIntervalInternal, and
  - writes "[iOS-xrem] via=section-data #N" for the first 8 and then every 1024th of them instead of one line each;
the bookkeeping under IntervalsLock that follows (XIntervals, RWXIntervals, the DEP-promoted pages of tools/patch-fex-ios-dep-promote-stale.py, the
hot SMC pages) runs as before. A section that intersects XIntervals takes the full path as before.

The pull request's own section path has no second condition; its later commit adds one for VirtualFree. A sub-floor image (a fixed base below
the address map's floor, mapped high, ml1207) has two names for every byte and its code can be compiled under either; an unmap named by the
other one would miss XIntervals and skip InvalidateIntervalInternal, whose last part is where the other name is dropped. So the skip is also
refused whenever the range touches a sub-floor window under either name (IosSubfloorEnum, ARM64EC module only: the WOW64 module has none).

Applied by tools/build-xtajit64.sh and tools/build-xtajit-wow64.sh (Source/Windows/Common is built into both modules; both restore the file
after their build); idempotent; a moved anchor refuses the patch and the build goes on without it (a warning).
Usage: patch-fex-ios-section-skip.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "section-skip-v1 madeira-bcd"
FILE = "Source/Windows/Common/InvalidationTracker.cpp"

HELPER_ANCHOR = ("static std::atomic<uint64_t> NestedNotificationSkips {0};\n"
                 "static std::atomic<uint64_t> NestedNotificationSkipsReported {0};\n")
HELPER = ("\n"
          "// " + MARKER + " (tools/patch-fex-ios-section-skip.py): MADEIRA_FEX_SECTION_SKIP=1 turns on the skip for unmapped sections that\n"
          "// were never executable (InvalidateContainingSection). Exactly \"1\"; off otherwise. Read once.\n"
          "static bool SectionSkipEnabled() {\n"
          "  static const bool Enabled = [] {\n"
          "    const char* Env = getenv(\"MADEIRA_FEX_SECTION_SKIP\");\n"
          "    const bool On = Env && Env[0] == '1' && !Env[1];\n"
          "    if (On) {\n"
          "      LogMan::Msg::EFmt(\"[iOS-xrem] madeira-bcd section-skip=1 (MADEIRA_FEX_SECTION_SKIP=1): an unmapped section that was never \"\n"
          "                        \"executable takes no invalidation\");\n"
          "    }\n"
          "    return On;\n"
          "  }();\n"
          "  return Enabled;\n"
          "}\n")

BODY_OLD = ("  InvalidateIntervalInternal(SectionBase, SectionSize);\n"
            "\n"
            "  if (Free) {\n"
            "    std::unique_lock Lock(IntervalsLock);\n"
            "    LogMan::Msg::EFmt(\"[iOS-xrem] via=section tracker={} {:#x}-{:#x}\", static_cast<void*>(this),\n"
            "                      SectionBase, SectionBase + SectionSize);\n")
BODY_NEW = ("  /* " + MARKER + " (tools/patch-fex-ios-section-skip.py, willfaust/FEX pull request #40 by Dragooon): with\n"
            "   * MADEIRA_FEX_SECTION_SKIP=1 a freed section that never was executable has nothing to invalidate. Nothing is compiled from a\n"
            "   * range outside XIntervals, and a range leaves XIntervals only after it is invalidated. A range that touches a sub-floor\n"
            "   * image's window (either of its two names, ml1207) takes the full path regardless. */\n"
            "  bool SkipInvalidation = false;\n"
            "  if (Free && SectionSkipEnabled()) {\n"
            "    // section-skip-v1 decision begin\n"
            "    bool Executable;\n"
            "    {\n"
            "      std::unique_lock Lock(IntervalsLock);\n"
            "      Executable = XIntervals.Intersect({SectionBase, SectionBase + SectionSize});\n"
            "    }\n"
            "#if defined(FEX_IOS_HOST) && defined(ARCHITECTURE_arm64ec)\n"
            "    {\n"
            "      const uint64_t End = SectionBase + SectionSize;\n"
            "      uint64_t Low, Real, WinSize;\n"
            "      for (int i = 0; !Executable && IosSubfloorEnum(i, &Low, &Real, &WinSize); i++) {\n"
            "        if (WinSize && ((SectionBase < Low + WinSize && End > Low) || (Real && SectionBase < Real + WinSize && End > Real))) {\n"
            "          Executable = true;\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "#endif\n"
            "    SkipInvalidation = !Executable;\n"
            "    // section-skip-v1 decision end\n"
            "  }\n"
            "  if (!SkipInvalidation) {\n"
            "    InvalidateIntervalInternal(SectionBase, SectionSize);\n"
            "  }\n"
            "\n"
            "  if (Free) {\n"
            "    std::unique_lock Lock(IntervalsLock);\n"
            "    if (SkipInvalidation) {\n"
            "      static std::atomic<uint32_t> DataUnmaps;\n"
            "      const auto N = DataUnmaps.fetch_add(1, std::memory_order_relaxed) + 1;\n"
            "      if (N <= 8 || !(N & 1023)) {\n"
            "        LogMan::Msg::EFmt(\"[iOS-xrem] via=section-data #{} tracker={} {:#x}-{:#x}: never executable, no invalidation\", N,\n"
            "                          static_cast<void*>(this), SectionBase, SectionBase + SectionSize);\n"
            "      }\n"
            "    } else {\n"
            "      LogMan::Msg::EFmt(\"[iOS-xrem] via=section tracker={} {:#x}-{:#x}\", static_cast<void*>(this),\n"
            "                        SectionBase, SectionBase + SectionSize);\n"
            "    }\n")


def patch(src):
    if MARKER in src:
        if src.count(HELPER_ANCHOR + HELPER) != 1 or src.count(BODY_NEW) != 1:
            raise ValueError("partial or changed section-skip")
        return src
    if src.count(HELPER_ANCHOR) != 1:
        raise ValueError("NestedNotificationSkips and its Reported counter not found once (FEX changed)")
    if src.count(BODY_OLD) != 1:
        raise ValueError("InvalidateContainingSection's InvalidateIntervalInternal call and [iOS-xrem] via=section line not found once (FEX changed)")
    return src.replace(HELPER_ANCHOR, HELPER_ANCHOR + HELPER, 1).replace(BODY_OLD, BODY_NEW, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / FILE
    if not path.is_file():
        sys.exit("patch-fex-ios-section-skip: %s not found (FEX changed)" % FILE)
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-fex-ios-section-skip: " + str(error))
    if result == source:
        print("patch-fex-ios-section-skip: already in")
    else:
        path.write_text(result)
        print("FEX: an unmapped section that was never executable takes no invalidation (MADEIRA_FEX_SECTION_SKIP=1; off by default)")
