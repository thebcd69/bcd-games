#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright 2026 Paul Gofman for CodeWeavers: the C change this script writes is Wine commit da00e5f5cc9
# (https://gitlab.winehq.org/wine/wine, 2026-03-23), LGPL-2.1-or-later like Wine. BCD's terms above do not apply to those lines.
# See THIRD-PARTY-NOTICES.md.
"""veh-next-handler: the exception dispatcher reads the next vectored handler after the current one returned (32-bit Windows side).

call_vectored_handlers (wine/dlls/ntdll/exception.c) walks the list of vectored exception handlers. It leaves the list's lock
while a handler runs. Wine 11.4, BCD's base, reads the pointer to the NEXT list entry before it leaves the lock and uses it
after the handler returned. If that next handler is removed meanwhile (RtlRemoveVectoredExceptionHandler from another thread,
or from the running handler itself), its entry is unlinked and freed, and the dispatcher then
  - calls the freed entry's handler (a handler the program has just removed),
  - takes it out of the list a second time, and
  - frees it a second time.
No timing is needed when a handler removes the one after it; with two threads it is a race.

This is a regression in Wine itself, between Wine commit 4749bd2bb74 (2025-12-05, in Wine 11.4) and Wine commit da00e5f5cc9
(2026-03-23, "ntdll: Do not keep next handler pointer outside of lock in call_vectored_handlers()", Paul Gofman), which this
script writes: the line `entry = entry->Flink;` moves from before the unlock to right after the lock is taken again, where
the current entry is still held by this call's reference. One more consequence, as before the regression: a handler added at
the end of the list while the last handler runs is called in the same dispatch.

No switch: the old order is a use after free.

Applied for the i386 farm only (32-bit programs) and put back right after it, next to tools/patch-wine-veh-add-log.py
(.github/workflows/build-ipa.yml): the 64-bit ntdll.dll BCD ships is a committed binary, and the arm64ec builds of the opt-in
ntdll variants (tools/build-wine-extra-dlls.sh) first rebuild an unpatched ntdll and compare it byte for byte with it. The
64-bit ntdll.dll therefore keeps the old order.

Usage: patch-wine-veh-next-handler.py <wine source root>   (idempotent: a second run says "already applied")
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd veh-next-handler"

HEAD = "static LONG call_vectored_handlers( EXCEPTION_RECORD *rec, CONTEXT *context )\n{\n"

EARLY_OLD = """        handler = CONTAINING_RECORD( entry, VECTORED_HANDLER, entry );
        entry = entry->Flink;
        ++*handler->count;
"""
EARLY_NEW = """        handler = CONTAINING_RECORD( entry, VECTORED_HANDLER, entry );
        ++*handler->count;
"""

LATE_OLD = """        RtlEnterCriticalSection( &vectored_handlers_section );
        if (!--*handler->count)  /* removed during execution */
"""
LATE_NEW = """        RtlEnterCriticalSection( &vectored_handlers_section );
        /* madeira-bcd veh-next-handler (tools/patch-wine-veh-next-handler.py; Wine commit da00e5f5cc9 by Paul Gofman): the
         * next handler is read here, under the lock, from the entry this call still holds. Read before the handler ran, it
         * could be an entry that another thread, or the handler itself, removed and freed meanwhile. */
        entry = entry->Flink;
        if (!--*handler->count)  /* removed during execution */
"""


def _function(text):
    at = text.find(HEAD)
    if at < 0 or text.count(HEAD) != 1:
        raise SystemExit("patch-wine-veh-next-handler: call_vectored_handlers not found exactly once")
    return at, text.index("\n}\n", at) + 3


def patch_text(text):
    """The patched text; the same text when the patch is already there. SystemExit when an anchor moved."""
    if MARKER in text:
        return text
    at, end = _function(text)
    fn = text[at:end]
    for anchor in (EARLY_OLD, LATE_OLD):
        if fn.count(anchor) != 1:
            raise SystemExit("patch-wine-veh-next-handler: anchor not found exactly once in call_vectored_handlers: "
                             + repr(anchor.strip().splitlines()[1 if anchor is EARLY_OLD else 0]))
    if fn.index(EARLY_OLD) > fn.index(LATE_OLD) or fn.count("entry = entry->Flink;") != 1:
        raise SystemExit("patch-wine-veh-next-handler: call_vectored_handlers is not the function this patch was written for")
    fn = fn.replace(EARLY_OLD, EARLY_NEW).replace(LATE_OLD, LATE_NEW)
    return text[:at] + fn + text[end:]


def unpatch_text(text):
    """The text before the patch (for the host test)."""
    return text.replace(LATE_NEW, LATE_OLD).replace(EARLY_NEW, EARLY_OLD) if MARKER in text else text


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: patch-wine-veh-next-handler.py <wine source root>")
    path = Path(argv[1]) / "dlls/ntdll/exception.c"
    old = path.read_text()
    new = patch_text(old)
    if new == old:
        print("patch-wine-veh-next-handler: already applied:", path)
    else:
        path.write_text(new)
        print("patch-wine-veh-next-handler: patched", path)


if __name__ == "__main__":
    main(sys.argv)
