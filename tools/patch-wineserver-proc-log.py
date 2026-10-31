#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""wineserver: say which process asks to finish its start twice (madeira-bcd, round 624, agent C S10 / RETRO-1 R8).

MGS Ground Zeroes (Send Logs HMAHPP, PFDA0D) starts a second copy of itself that dies at once: the client logs
"[Wine init_done] FAILED" (build/ntdll-unix/server_ios.c) because the server's init_process_done refused it with
STATUS_INVALID_PARAMETER: that process's start was already done. Whether it is a reused process id, a second init of the same
process or another state cannot be read from the log, since the server says nothing. Two lines, 64 at most each a session:

  [srv-proc] new pid=0128 parent=0024 flags=0x0 rev=r624                          (DECL_HANDLER(new_process), once created)
  [srv-proc] init_process_done pid=0128 REFUSED: its start was already done (startup_state=1 peb=7ffd1000) rev=r624

Log only, WINE_IOS only; the refusal itself is unchanged. Named patch-wineserver-*, not patch-wine-*: the CI's i386 farm builds
no server and its cache is keyed on tools/patch-wine-*.py.

Usage: patch-wineserver-proc-log.py <wine source dir>. Idempotent (marker "srv-proc-log-v1"); fails by name if an anchor moved,
the file unchanged.
"""
import pathlib
import sys

MARKER = "srv-proc-log-v1"

REFUSE_OLD = """DECL_HANDLER(init_process_done)
{
    struct process *process = current->process;

    if (is_process_init_done(process))
    {
        set_error( STATUS_INVALID_PARAMETER );
        return;
    }
"""
REFUSE_NEW = """DECL_HANDLER(init_process_done)
{
    struct process *process = current->process;

    if (is_process_init_done(process))
    {
#ifdef WINE_IOS
        {   /* madeira-bcd srv-proc-log-v1 (round 624; MGS Ground Zeroes HMAHPP, PFDA0D): the refusal named, 64 lines at most */
            static unsigned int srv_proc_refused;
            if (srv_proc_refused < 64)
            {
                srv_proc_refused++;
                fprintf( stderr, "[srv-proc] init_process_done pid=%04x REFUSED: its start was already done (startup_state=%d peb=%lx) rev=r624\\n",
                         process->id, (int)process->startup_state, (unsigned long)process->peb );
            }
        }
#endif
        set_error( STATUS_INVALID_PARAMETER );
        return;
    }
"""

NEW_OLD = """    reply->pid = get_process_id( process );
    reply->handle = alloc_handle_no_access_check( current->process, process, req->access, objattr->attributes );

 done:
"""
NEW_NEW = """    reply->pid = get_process_id( process );
    reply->handle = alloc_handle_no_access_check( current->process, process, req->access, objattr->attributes );
#ifdef WINE_IOS
    {   /* madeira-bcd srv-proc-log-v1 (round 624): every new process, 64 lines at most, for the refusal above */
        static unsigned int srv_proc_new;
        if (srv_proc_new < 64)
        {
            srv_proc_new++;
            fprintf( stderr, "[srv-proc] new pid=%04x parent=%04x flags=0x%x rev=r624\\n", reply->pid, get_process_id( parent ),
                     (unsigned int)req->flags );
        }
    }
#endif

 done:
"""


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = pathlib.Path(sys.argv[1]) / "server/process.c"
    s = path.read_text()
    if MARKER in s:
        print("patch-wineserver-proc-log: already patched")
        return 0
    for name, old in (("init_process_done's refusal", REFUSE_OLD), ("new_process's reply", NEW_OLD)):
        if s.count(old) != 1:
            print(f"::error::patch-wineserver-proc-log: {name}: anchor found {s.count(old)} times (want 1) in {path}")
            return 1
    s = s.replace(REFUSE_OLD, REFUSE_NEW, 1).replace(NEW_OLD, NEW_NEW, 1)
    path.write_text(s)
    print("patch-wineserver-proc-log: [srv-proc] lines added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
