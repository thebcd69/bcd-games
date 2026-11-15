#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The orphan-lock reaper's stamped mode becomes the default (round 660, M030/M141; owner decision 2026-11-15).

tools/patch-wine-lock-orphan.py made MADEIRA_LOCK_ORPHAN = stamped an opt-in: strikes once per lock per monitor cycle, and only locks a live
thread has stamped (FEX's code-buffer locks) can be released. As it was, the reaper released locks their live owners held: Horizon Zero
Dawn's "Error" stop (build 479), Eslabong's abort (6X62HT), the freeze of ANBWF5 and the false releases of C87WFF, DV4W70 and WT4C0B.
GTA V, GTA V Enhanced, RDR2, Horizon Zero Dawn, Horizon Forbidden West and Rift Apart play with stamped from their list rows, and The Last
of Us Part I did on builds 656-657, where it ended a freeze the old reaper caused. From build 663 stamped is the default:

  unset (or any other value)  stamped
  legacy                      the reaper as it was before build 663
  stamped                     stamped (as before)
  0                           no orphan-lock releases at all (as before)

The start says which mode runs, once. Applied after tools/patch-wine-lock-orphan.py (whose mode selection is the anchor) and the waiter-read
patch; idempotent; refuses, writing nothing, when the anchor moved.

Usage: patch-wine-lock-orphan-default.py [path/to/wine/dlls/ntdll/unix/sync.c]
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd lock-orphan default stamped"
NEEDS = "madeira-bcd lock-orphan (tools/patch-wine-lock-orphan.py)"

OLD = """        const char *e = getenv( "MADEIRA_LOCK_ORPHAN" );
        orphan_mode = !e ? 0 : !strcmp( e, "stamped" ) ? 1 : !strcmp( e, "0" ) ? 2 : 0;
        if (orphan_mode)
            dprintf( 2, "[lock-orphan] madeira-bcd MADEIRA_LOCK_ORPHAN=%s: %s\\n", e,
                     orphan_mode == 1 ? "only locks a live thread has stamped are released, after 3 monitor cycles"
                                      : "no orphan-lock releases" );
"""
NEW = """        const char *e = getenv( "MADEIRA_LOCK_ORPHAN" );
        /* """ + MARKER + """ (round 660, M030/M141, owner 2026-11-15): stamped from build 663; legacy is the
         * reaper as it was (it released locks their live owners held: HZD 479, Eslabong 6X62HT, ANBWF5). */
        orphan_mode = !e ? 1 : !strcmp( e, "legacy" ) ? 0 : !strcmp( e, "0" ) ? 2 : 1;
        if (!e)
            dprintf( 2, "[lock-orphan] madeira-bcd stamped by default: only locks a live thread has stamped are released, after 3 monitor "
                        "cycles (MADEIRA_LOCK_ORPHAN=legacy: the reaper as before build 663)\\n" );
        else
            dprintf( 2, "[lock-orphan] madeira-bcd MADEIRA_LOCK_ORPHAN=%s: %s\\n", e,
                     orphan_mode == 1 ? "only locks a live thread has stamped are released, after 3 monitor cycles"
                     : orphan_mode == 2 ? "no orphan-lock releases" : "the reaper as before build 663" );
"""


def patch(source):
    if MARKER in source:
        return source
    if NEEDS not in source:
        raise ValueError("apply tools/patch-wine-lock-orphan.py first")
    if source.count(OLD) != 1:
        raise ValueError("lock-orphan mode selection not found exactly once (found %d): the reaper moved" % source.count(OLD))
    return source.replace(OLD, NEW)


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "wine/dlls/ntdll/unix/sync.c")
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-lock-orphan-default: %s (%s)" % (error, path))
    if result == source:
        print("patch-wine-lock-orphan-default: already patched")
    else:
        path.write_text(result)
        print("patch-wine-lock-orphan-default: applied to %s" % path)


if __name__ == "__main__":
    main()
