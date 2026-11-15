#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright 2026 Paul Gofman for CodeWeavers: the C change this script writes is Wine commit 81e52628b25
# (https://gitlab.winehq.org/wine/wine, 2026-10-12), LGPL-2.1-or-later like Wine. BCD's terms above do not apply to those lines.
# See THIRD-PARTY-NOTICES.md.
"""woa-wake-order: a waker unlinks a waiter's entry before it marks the entry as woken (32-bit Windows side).

RtlWaitOnAddress (wine/dlls/ntdll/sync.c) backs SRW locks, condition variables and WaitOnAddress. A waiter queues an entry
that lives on its own stack and parks. RtlWakeAddressSingle / RtlWakeAddressAll, under the queue's spinlock, mark the entry
as woken (entry->addr = NULL), unlink it and alert the thread. When the waiter's park returns, for whatever reason (the alert,
a time-out, an alert left over from an earlier wake), it looks at entry.addr WITHOUT the spinlock: NULL means "a waker took
me out", and it returns; its stack entry is gone from that moment.

Wine 11.4, BCD's base, clears entry->addr FIRST. A waiter whose park returns just then leaves while the waker still has to
  - unlink the entry (RtlWakeAddressSingle and RtlWakeAddressAll: list_remove reads and writes through the links of a stack
    frame that is already reused; the queue, shared by every lock that hashes to it, can be left pointing into that stack), and
  - read the thread to alert out of it (RtlWakeAddressAll: entry->tid).
The window is a few instructions wide and needs a park that returns without this waker's alert, so it is rare; when it hits,
the damage is to a queue that many locks share.

Wine fixed it in commit 81e52628b25 (2026-10-12, Wine 11.20: "ntdll: Make sure entry is removed from queue if unlocked
entry.addr precheck in RtlWaitOnAddress() suggests so.", Paul Gofman), which this script writes: the waker reads the thread
id, unlinks the entry and only then clears entry->addr (WritePointerRelease); the waiter's unlocked look is a
ReadPointerAcquire. No switch: the old order touches a stack frame after its owner was told it may leave.

What it does not do: it is not the cure for the lost alert that tools/patch-wine-woa-recheck.py (env.MADEIRA_WOA_RECHECK)
works around. There the waker had finished (entry unlinked, addr cleared) and the alert itself never arrived; this change
alters nothing on that path, and the two patches do not touch each other's lines.

Compiled for the i386 ntdll only (the 32-bit programs' copy, which the build compiles from this tree): the changed lines are
under MADEIRA_WOA_WAKE_ORDER, which the patch defines for __i386__ alone, and every other build compiles the lines as they
were. That matters because the 64-bit ntdll.dll BCD ships is a committed binary, and the arm64ec builds of the opt-in ntdll
variants (tools/build-wine-extra-dlls.sh) first rebuild an unpatched ntdll and compare it byte for byte with it. The 64-bit
ntdll.dll, ntdll-sync.dll and ntdll-xsec.dll therefore keep the old order. (Not "#ifdef __i386__" at each place:
tests/host/check-woa-recheck.py compiles RtlWaitOnAddress on the host with that exact line turned on.)

Usage: patch-wine-woa-wake-order.py wine/dlls/ntdll/sync.c   (idempotent)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd woa-wake-order"

DEFINE_AT = "struct futex_entry\n{\n"
DEFINE = """/* madeira-bcd woa-wake-order (tools/patch-wine-woa-wake-order.py; Wine commit 81e52628b25 by Paul Gofman): a waker takes
 * a waiter's entry out of the queue BEFORE it clears entry->addr, and reads the thread to alert first. A waiter looks at
 * entry.addr without the queue's lock when its park returns (an alert left over from an earlier wake, a time-out); once it
 * reads NULL it returns and its stack entry is gone. Clearing first let it go while the waker still had to unlink the entry
 * and read its tid from that stack. For the i386 ntdll only (the 32-bit programs' copy, which the build compiles from this
 * tree); every other build compiles the lines as they were. */
#ifdef __i386__
#define MADEIRA_WOA_WAKE_ORDER 1
#endif

"""

WAIT_HEAD = "NTSTATUS WINAPI RtlWaitOnAddress( const void *addr, const void *cmp, SIZE_T size,\n"
WAIT_OLD = """    /* We may have already been removed by a call to RtlWakeAddressSingle() or RtlWakeAddressAll(). */
    if (entry.addr)
    {
"""
WAIT_NEW = """    /* We may have already been removed by a call to RtlWakeAddressSingle() or RtlWakeAddressAll(). */
#ifdef MADEIRA_WOA_WAKE_ORDER
    if (ReadPointerAcquire( (void * volatile *)&entry.addr ))
#else
    if (entry.addr)
#endif
    {
"""

ALL_HEAD = "void WINAPI RtlWakeAddressAll( const void *addr )\n{\n"
ALL_OLD = """            entry->addr = NULL;
            list_remove( &entry->entry );
            if (count == ARRAY_SIZE(tids))
            {
                NtAlertMultipleThreadByThreadId( tids, count, NULL, NULL );
                count = 0;
            }
            tids[count++] = (HANDLE)(ULONG_PTR)entry->tid;
"""
ALL_NEW = """#ifdef MADEIRA_WOA_WAKE_ORDER
            DWORD tid = entry->tid;
            list_remove( &entry->entry );
            WritePointerRelease( (void * volatile *)&entry->addr, NULL );
            if (count == ARRAY_SIZE(tids))
            {
                NtAlertMultipleThreadByThreadId( tids, count, NULL, NULL );
                count = 0;
            }
            tids[count++] = (HANDLE)(ULONG_PTR)tid;
#else
            entry->addr = NULL;
            list_remove( &entry->entry );
            if (count == ARRAY_SIZE(tids))
            {
                NtAlertMultipleThreadByThreadId( tids, count, NULL, NULL );
                count = 0;
            }
            tids[count++] = (HANDLE)(ULONG_PTR)entry->tid;
#endif
"""

SINGLE_HEAD = "void WINAPI RtlWakeAddressSingle( const void *addr )\n{\n"
SINGLE_OLD = """            entry->addr = NULL;
            list_remove( &entry->entry );
            break;
"""
SINGLE_NEW = """#ifdef MADEIRA_WOA_WAKE_ORDER
            list_remove( &entry->entry );
            WritePointerRelease( (void * volatile *)&entry->addr, NULL );
#else
            entry->addr = NULL;
            list_remove( &entry->entry );
#endif
            break;
"""

EDITS = ((WAIT_HEAD, WAIT_OLD, WAIT_NEW), (ALL_HEAD, ALL_OLD, ALL_NEW), (SINGLE_HEAD, SINGLE_OLD, SINGLE_NEW))


def _span(text, head):
    if text.count(head) != 1:
        raise SystemExit("patch-wine-woa-wake-order: function head not found exactly once: " + repr(head.splitlines()[0]))
    at = text.index(head)
    return at, text.index("\n}\n", at) + 3


def patch_text(text):
    """The patched text; the same text when the patch is already there. SystemExit when an anchor moved."""
    if MARKER in text:
        return text
    if text.count(DEFINE_AT) != 1:
        raise SystemExit("patch-wine-woa-wake-order: 'struct futex_entry' not found exactly once")
    for head, old, new in EDITS:
        at, end = _span(text, head)
        fn = text[at:end]
        if fn.count(old) != 1:
            raise SystemExit("patch-wine-woa-wake-order: anchor not found exactly once in " + repr(head.splitlines()[0]))
        text = text[:at] + fn.replace(old, new) + text[end:]
    at = text.index(DEFINE_AT)
    if not at < text.index(WAIT_HEAD):
        raise SystemExit("patch-wine-woa-wake-order: 'struct futex_entry' is not above RtlWaitOnAddress")
    return text[:at] + DEFINE + text[at:]


def unpatch_text(text):
    """The text before the patch (for the host test)."""
    if MARKER not in text:
        return text
    text = text.replace(DEFINE, "")
    for _head, old, new in EDITS:
        text = text.replace(new, old)
    return text


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: patch-wine-woa-wake-order.py wine/dlls/ntdll/sync.c")
    path = Path(argv[1])
    old = path.read_text()
    new = patch_text(old)
    if new == old:
        print("patch-wine-woa-wake-order: already applied:", path)
    else:
        path.write_text(new)
        print("patch-wine-woa-wake-order: patched", path)


if __name__ == "__main__":
    main(sys.argv)
