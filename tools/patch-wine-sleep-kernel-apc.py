#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Russell Barton
"""madeira-bcd sleep-kernel-apc: with env.MADEIRA_SLEEP_KERNEL_APC = 1 a non-alertable sleep runs queued I/O completions, as on Windows.

On Windows an I/O completion (a kernel-mode APC) runs in any wait, alertable or not; only user APCs
need an alertable one. Wine's NtDelayExecution (wine/dlls/ntdll/unix/sync.c) makes an ALERTABLE sleep
a server wait (SELECT_INTERRUPTIBLE | SELECT_ALERTABLE), where the server delivers system APCs, but a
NON-alertable sleep is a host select(). Elsewhere Wine's SIGUSR1 interrupts that select() to run a
system APC; on iOS send_thread_signal cannot reach the thread (wineserver/mach_ios.c, get_process_port),
so a completion queued for a sleeping thread waits until the thread next enters the server, which a
thread that only sleeps never does. Far Cry 3 (Ubisoft Connect) reads its launcher pipe overlapped and
then sleeps: the read never completes and the game exits after 15 s.

With env.MADEIRA_SLEEP_KERNEL_APC = 1 (default off) a non-alertable sleep with a timeout (or an infinite
one) is server_wait(NULL, 0, SELECT_INTERRUPTIBLE, timeout): the same wait as an alertable sleep without
SELECT_ALERTABLE, so system APCs run during it and user APCs still do not, as on Windows. Sleep(0) (a zero
timeout) and the yield before a timed sleep are unchanged. Each such sleep costs one server round trip, so
the switch is per game (Far Cry 3's list). Read at each sleep, so it is the session's own. iOS only (WINE_IOS).

Usage: patch-wine-sleep-kernel-apc.py wine/dlls/ntdll/unix/sync.c   (idempotent)
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd sleep-kernel-apc"

HEAD = """NTSTATUS WINAPI NtDelayExecution( BOOLEAN alertable, const LARGE_INTEGER *timeout )
{"""

HELPER = """#ifdef WINE_IOS
/* madeira-bcd sleep-kernel-apc (tools/patch-wine-sleep-kernel-apc.py): with env.MADEIRA_SLEEP_KERNEL_APC = 1 a
 * non-alertable sleep waits in the server with SELECT_INTERRUPTIBLE, so a system APC (an I/O completion) queued for
 * the sleeping thread runs during the sleep, as on Windows; on iOS nothing can interrupt the host select(). Read at
 * each sleep, so the value is the session's own. */
static int madeira_sleep_kernel_apc(void)
{
    const char *e = getenv( "MADEIRA_SLEEP_KERNEL_APC" );

    return e && e[0] == '1' && !e[1];
}
#endif

"""

FOREVER_OLD = """    if (!timeout || timeout->QuadPart == TIMEOUT_INFINITE)  /* sleep forever */
    {
        for (;;) select( 0, NULL, NULL, NULL, NULL );
    }"""
FOREVER_NEW = """    if (!timeout || timeout->QuadPart == TIMEOUT_INFINITE)  /* sleep forever */
    {
#ifdef WINE_IOS
        if (madeira_sleep_kernel_apc())   /* madeira-bcd sleep-kernel-apc */
            for (;;) server_wait( NULL, 0, SELECT_INTERRUPTIBLE, NULL );
#endif
        for (;;) select( 0, NULL, NULL, NULL, NULL );
    }"""

TIMED_OLD = """        status = NtYieldExecution();
        if (!when) return status;
"""
TIMED_NEW = """        status = NtYieldExecution();
        if (!when) return status;
#ifdef WINE_IOS
        if (madeira_sleep_kernel_apc())   /* madeira-bcd sleep-kernel-apc: system APCs run during the sleep */
        {
            server_wait( NULL, 0, SELECT_INTERRUPTIBLE, timeout );
            return STATUS_SUCCESS;
        }
#endif
"""


def patch(text):
    if MARKER in text:
        return text
    for anchor in (HEAD, FOREVER_OLD, TIMED_OLD):
        if text.count(anchor) != 1:
            raise SystemExit(f"patch-wine-sleep-kernel-apc: anchor not found exactly once: {anchor.splitlines()[0].strip()!r}")
    head_at = text.index(HEAD)
    body = text[head_at:]
    if body.index(FOREVER_OLD) > body.index("\n}\n") or body.index(TIMED_OLD) > body.index("\n}\n"):
        raise SystemExit("patch-wine-sleep-kernel-apc: the anchors are not inside NtDelayExecution")
    text = text[:head_at] + HELPER + text[head_at:]
    return text.replace(FOREVER_OLD, FOREVER_NEW).replace(TIMED_OLD, TIMED_NEW)


def unpatch(text):
    return text.replace(HELPER, "").replace(FOREVER_NEW, FOREVER_OLD).replace(TIMED_NEW, TIMED_OLD)


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    path = Path(argv[1])
    old = path.read_text()
    new = patch(old)
    if new != old:
        path.write_text(new)
        print("patch-wine-sleep-kernel-apc: patched", path)
    else:
        print("patch-wine-sleep-kernel-apc: already patched", path)


if __name__ == "__main__":
    main(sys.argv)
