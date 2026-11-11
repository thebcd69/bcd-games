#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX WOW64: a page promoted under DEP-off, freed and allocated again is promoted again (night round 2026-11-11, A4 S4).

A 32-bit program whose image has no NX_COMPAT runs with DEP off: executing from any committed readable page is legal. Under a guest window the
tracker promotes such a page lazily, on the first execute attempt (InvalidationTracker::QueryExecutableRange -> PromoteDEPRegionLocked): it inserts
the page into XIntervals (and RWXIntervals when writable) and records it in DEPPromotedIntervals. The protect path forgets all three together; the
FREE paths (InvalidateContainingSection for an unmapped section, InvalidateAlignedInterval for a VirtualFree/decommit) removed it from
XIntervals/RWXIntervals but left DEPPromotedIntervals alone. When the page was allocated again and the program jumped into it,
PromoteDEPRegionLocked found the stale record ("already promoted") and returned without inserting into XIntervals: the decoder saw a page that
is not executable, `NoExec instruction in entry block`, `[iOS-noexec] status=NOEXEC`, `[noexec-who] ... now: a view ... (VirtualAlloc)`, and the
game died (Need for Speed: Underground, C86TQF, 85 s into the run).

  1. The two free paths forget the freed range in DEPPromotedIntervals as well (under the IntervalsLock they already hold; DEP-off only).
  2. PromoteDEPRegionLocked trusts a DEPPromotedIntervals record only while XIntervals still holds the page; otherwise it promotes again.
Only a page that today ends in NOEXEC changes: a page promoted and still executable is returned as before, a page promoted for the first time has
no record, and a record whose page is gone is dropped on free. DEP-off programs only (DEPDisabled); the 32-bit module only: the lazy promotion is
compiled for the guest-window build (`#if !defined(ARCHITECTURE_arm64ec)`).
Applied by tools/build-xtajit-wow64.sh (Source/Windows/Common/InvalidationTracker.cpp is restored after the build); idempotent, all-or-nothing, fails by
name when an anchor moves. Usage: patch-fex-ios-dep-promote-stale.py FEX-ROOT
"""
from pathlib import Path
import sys

MARKER = "dep-promote-stale-v1 madeira-bcd"
FILE = "Source/Windows/Common/InvalidationTracker.cpp"

EDITS = [
    # the free path of an unmapped section
    ("""    XIntervals.Remove({SectionBase, SectionBase + SectionSize});
    RWXIntervals.Remove({SectionBase, SectionBase + SectionSize});
    ForgetHotSMCPages(SectionBase, SectionBase + SectionSize);
""",
     """    XIntervals.Remove({SectionBase, SectionBase + SectionSize});
    RWXIntervals.Remove({SectionBase, SectionBase + SectionSize});
    if (DEPDisabled) {   // """ + MARKER + """: a freed page is no longer "promoted"
      DEPPromotedIntervals.Remove({SectionBase, SectionBase + SectionSize});
    }
    ForgetHotSMCPages(SectionBase, SectionBase + SectionSize);
"""),
    # the free path of a VirtualFree / decommit
    ("""    XIntervals.Remove({AlignedBase, AlignedBase + AlignedSize});
    RWXIntervals.Remove({AlignedBase, AlignedBase + AlignedSize});
    ForgetHotSMCPages(AlignedBase, AlignedBase + AlignedSize);
""",
     """    XIntervals.Remove({AlignedBase, AlignedBase + AlignedSize});
    RWXIntervals.Remove({AlignedBase, AlignedBase + AlignedSize});
    if (DEPDisabled) {   // """ + MARKER + """: a freed page is no longer "promoted"
      DEPPromotedIntervals.Remove({AlignedBase, AlignedBase + AlignedSize});
    }
    ForgetHotSMCPages(AlignedBase, AlignedBase + AlignedSize);
"""),
    # a record is trusted only while the page is still executable
    ("""  if (DEPPromotedIntervals.Query(Address).Enclosed) {
    return ProtInterval;
  }

  XIntervals.Insert(ProtInterval);
""",
     """  // """ + MARKER + """: a record of an earlier promotion counts only while the page is still executable; a page that was freed and allocated
  // again (a stale record) is promoted again, instead of being reported as not executable.
  if (DEPPromotedIntervals.Query(Address).Enclosed && XIntervals.Query(Address).Enclosed) {
    return ProtInterval;
  }

  XIntervals.Insert(ProtInterval);
"""),
]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / FILE
    if not path.is_file():
        sys.exit("patch-fex-ios-dep-promote-stale: %s not found (FEX changed)" % FILE)
    src = path.read_text()
    if MARKER in src:
        print("InvalidationTracker.cpp: the stale DEP-promotion fix is already in; nothing to do")
        return
    out = src
    for old, new in EDITS:
        n = out.count(old)
        if n != 1:
            sys.exit("patch-fex-ios-dep-promote-stale: an anchor not found once (%d times; FEX changed): %r" % (n, old.strip().splitlines()[0]))
        out = out.replace(old, new)
    path.write_text(out)
    print("InvalidationTracker.cpp: the free paths forget DEP-promoted pages and a stale promotion record is promoted again")


main()
