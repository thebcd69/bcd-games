#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Russell Barton
"""madeira-bcd woa-recheck: with env.MADEIRA_WOA_RECHECK = 1 an infinite wait on an address re-checks its entry every second
(32-bit Windows side).

RtlWaitOnAddress (wine/dlls/ntdll/sync.c) backs SRW locks, condition variables and WaitOnAddress. A waiter queues an entry
under the queue's spinlock and parks in NtWaitForAlertByThreadId; a waker removes the entry under the same spinlock and then
alerts the thread. If that alert never arrives, the waiter sleeps forever although it was woken: Ubisoft Connect's browser
(upc.exe, 32-bit) stopped in 2 of 11 Far Cry 3 starts with its CrBrowserMain and Chrome_InProcGpuThread threads parked for
80-100 s on SRW locks that were free (word 0x00000002: no owner, one exclusive waiter). Why the alert is lost is not known.

With this patch an infinite RtlWaitOnAddress in the i386 ntdll (the 32-bit programs' copy, which the build compiles from this
tree) waits in one-second slices. After each slice it looks at its own entry under the spinlock: if a waker already removed
it, the alert never came, and it returns STATUS_SUCCESS, a spurious wake-up that every caller handles (SRW locks and
condition variables loop; WaitOnAddress may return spuriously on Windows too); otherwise it waits again. Each such return is
logged, up to 32 lines per process: "[woa-rescue] tid=... addr=... after N s: dequeued, no alert within the slice". A wait
with a timeout is unchanged.

The slice is a recognisable value (MADEIRA_WOA_SLICE, 1 s + 100 ns). The unix side (wine/dlls/ntdll/unix/sync.c,
NtWaitForAlertByThreadId) holds the switch: unless env.MADEIRA_WOA_RECHECK is 1 it waits a slice out as the infinite wait it
stands for, exactly the old behaviour; with it, it keeps the [waiters] registry's start time and "infinite" flag across the
slices of one park, so the >60 s park dump and the hang census still see one park. The switch is read at each slice, so it
is the session's own. The patch does not change the 64-bit ntdll.dll (a prebuilt binary in this build).

Usage: patch-wine-woa-recheck.py wine/dlls/ntdll/sync.c wine/dlls/ntdll/unix/sync.c   (idempotent)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd woa-recheck"

# ---------------------------------------------------------------- PE side: wine/dlls/ntdll/sync.c
PE_HEAD = """/***********************************************************************
 *           RtlWaitOnAddress   (NTDLL.@)
 */
NTSTATUS WINAPI RtlWaitOnAddress( const void *addr, const void *cmp, SIZE_T size,
                                  const LARGE_INTEGER *timeout )
{"""

PE_HELPER = """#ifdef __i386__
/* madeira-bcd woa-recheck (tools/patch-wine-woa-recheck.py): an infinite wait on an address waits in one-second slices
 * (MADEIRA_WOA_SLICE, which the unix side recognises: unless env.MADEIRA_WOA_RECHECK is 1 it waits the infinite wait the
 * slice stands for). After each slice the waiter looks at its own entry under the queue's spinlock: removed by a waker
 * whose alert never came ends the wait with STATUS_SUCCESS (a spurious wake-up, which every caller handles); otherwise it
 * waits again. */
#define MADEIRA_WOA_SLICE (-10000001)
static NTSTATUS madeira_woa_wait( struct futex_queue *queue, struct futex_entry *entry, const void *addr, SIZE_T size )
{
    static LONG logged;
    LARGE_INTEGER slice;
    unsigned int seconds = 0;
    BOOL dequeued;
    NTSTATUS ret;

    slice.QuadPart = MADEIRA_WOA_SLICE;
    for (;;)
    {
        ret = NtWaitForAlertByThreadId( addr, &slice );
        if (ret != STATUS_TIMEOUT) return ret;
        seconds++;
        spin_lock( &queue->lock );
        dequeued = !entry->addr;
        spin_unlock( &queue->lock );
        if (dequeued)
        {
            if (InterlockedIncrement( &logged ) <= 32)
                ERR( "[woa-rescue] tid=%04lx addr=%p size=%Iu after %u s: dequeued, no alert within the slice\\n",
                     (unsigned long)GetCurrentThreadId(), addr, size, seconds );
            return STATUS_SUCCESS;
        }
    }
}
#endif

"""

PE_WAIT_OLD = """    ret = NtWaitForAlertByThreadId( addr, timeout );
"""
PE_WAIT_NEW = """#ifdef __i386__
    if (!timeout) ret = madeira_woa_wait( queue, &entry, addr, size );   /* madeira-bcd woa-recheck */
    else
#endif
    ret = NtWaitForAlertByThreadId( addr, timeout );
"""

# ---------------------------------------------------------------- unix side: wine/dlls/ntdll/unix/sync.c
UX_FIELD_OLD = """    ULONGLONG since;    /* NtQuerySystemTime at park entry */
    int inf;            /* 1 = INFINITE wait */
} ios_alert_waiters[IOS_ALERT_WAITER_MAX];
"""
UX_FIELD_NEW = """    ULONGLONG since;    /* NtQuerySystemTime at park entry */
    int inf;            /* 1 = INFINITE wait */
    const void *slice;  /* madeira-bcd woa-recheck: the address whose slice just timed out (the park goes on) */
} ios_alert_waiters[IOS_ALERT_WAITER_MAX];
"""

UX_HEAD = """NTSTATUS WINAPI NtWaitForAlertByThreadId( const void *address, const LARGE_INTEGER *timeout )
{"""

UX_HELPER = """/* madeira-bcd woa-recheck (tools/patch-wine-woa-recheck.py): the 32-bit ntdll's RtlWaitOnAddress waits an infinite
 * wait out in slices of exactly MADEIRA_WOA_SLICE, re-checking its queue entry after each. With
 * env.MADEIRA_WOA_RECHECK = 1 the slices of one park keep its [waiters] start time and count as infinite; otherwise a
 * slice is waited out as the infinite wait it stands for (the old behaviour). Read at each slice. */
#define MADEIRA_WOA_SLICE (-10000001)
static int madeira_woa_recheck(void)
{
    const char *e = getenv( "MADEIRA_WOA_RECHECK" );

    return e && e[0] == '1' && !e[1];
}

"""

UX_NORM_OLD = """        int ios_wslot;

        if (timeout)
        {
            if (timeout->QuadPart == TIMEOUT_INFINITE)
                timeout = NULL;
            else
                end = get_absolute_timeout( timeout );
        }
"""
UX_NORM_NEW = """        int ios_wslot;
        int ios_woa_slice = timeout && timeout->QuadPart == MADEIRA_WOA_SLICE;   /* madeira-bcd woa-recheck */

        if (ios_woa_slice && !madeira_woa_recheck())
        {
            timeout = NULL;   /* madeira-bcd woa-recheck not on: the infinite wait the slice stands for */
            ios_woa_slice = 0;
        }
        if (timeout)
        {
            if (timeout->QuadPart == TIMEOUT_INFINITE)
                timeout = NULL;
            else
                end = get_absolute_timeout( timeout );
        }
"""

UX_REG_OLD = """            ios_alert_waiters[ios_wslot].since = ios_wnow.QuadPart;
            ios_alert_waiters[ios_wslot].inf = !timeout;
            ios_alert_waiters[ios_wslot].addr = address ? address : (const void *)0x1;
"""
UX_REG_NEW = """            /* madeira-bcd woa-recheck: the next slice of the same park keeps its start and counts as infinite */
            if (!ios_woa_slice || ios_alert_waiters[ios_wslot].slice != (address ? address : (const void *)0x1))
                ios_alert_waiters[ios_wslot].since = ios_wnow.QuadPart;
            ios_alert_waiters[ios_wslot].inf = !timeout || ios_woa_slice;
            ios_alert_waiters[ios_wslot].slice = NULL;
            ios_alert_waiters[ios_wslot].addr = address ? address : (const void *)0x1;
"""

UX_TIMEOUT_OLD = """            if (ret == -1 && errno == ETIMEDOUT)
            {
                if (ios_wslot >= 0) ios_alert_waiters[ios_wslot].addr = NULL;
"""
UX_TIMEOUT_NEW = """            if (ret == -1 && errno == ETIMEDOUT)
            {
                if (ios_wslot >= 0)
                {
                    /* madeira-bcd woa-recheck: a slice that timed out leaves its park open for the next slice */
                    ios_alert_waiters[ios_wslot].slice = ios_woa_slice ? ios_alert_waiters[ios_wslot].addr : NULL;
                    ios_alert_waiters[ios_wslot].addr = NULL;
                }
"""


def _need(text, anchors, what):
    for anchor in anchors:
        if text.count(anchor) != 1:
            raise SystemExit(f"patch-wine-woa-recheck: {what}: anchor not found exactly once: {anchor.strip().splitlines()[0]!r}")


def _inside(text, head, anchors, what):
    at = text.index(head)
    end = text.index("\n}\n", at)
    for anchor in anchors:
        if not at < text.index(anchor) < end:
            raise SystemExit(f"patch-wine-woa-recheck: {what}: an anchor is not inside {head.splitlines()[-2].strip()!r}")


def patch_pe(text):
    if MARKER in text:
        return text
    _need(text, (PE_HEAD, PE_WAIT_OLD), "dlls/ntdll/sync.c")
    _inside(text, PE_HEAD, (PE_WAIT_OLD,), "dlls/ntdll/sync.c")
    at = text.index(PE_HEAD)
    text = text[:at] + PE_HELPER + text[at:]
    return text.replace(PE_WAIT_OLD, PE_WAIT_NEW)


def unpatch_pe(text):
    return text.replace(PE_HELPER, "").replace(PE_WAIT_NEW, PE_WAIT_OLD)


def patch_unix(text):
    if MARKER in text:
        return text
    _need(text, (UX_FIELD_OLD, UX_HEAD, UX_NORM_OLD, UX_REG_OLD, UX_TIMEOUT_OLD), "dlls/ntdll/unix/sync.c")
    _inside(text, UX_HEAD, (UX_NORM_OLD, UX_REG_OLD, UX_TIMEOUT_OLD), "dlls/ntdll/unix/sync.c")
    at = text.index(UX_HEAD)
    text = text[:at] + UX_HELPER + text[at:]
    for old, new in ((UX_FIELD_OLD, UX_FIELD_NEW), (UX_NORM_OLD, UX_NORM_NEW), (UX_REG_OLD, UX_REG_NEW),
                     (UX_TIMEOUT_OLD, UX_TIMEOUT_NEW)):
        text = text.replace(old, new)
    return text


def unpatch_unix(text):
    text = text.replace(UX_HELPER, "")
    for old, new in ((UX_FIELD_OLD, UX_FIELD_NEW), (UX_NORM_OLD, UX_NORM_NEW), (UX_REG_OLD, UX_REG_NEW),
                     (UX_TIMEOUT_OLD, UX_TIMEOUT_NEW)):
        text = text.replace(new, old)
    return text


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__)
    for path, fn in ((Path(argv[1]), patch_pe), (Path(argv[2]), patch_unix)):
        old = path.read_text()
        new = fn(old)
        if new != old:
            path.write_text(new)
            print("patch-wine-woa-recheck: patched", path)
        else:
            print("patch-wine-woa-recheck: already patched", path)


if __name__ == "__main__":
    main(sys.argv)
