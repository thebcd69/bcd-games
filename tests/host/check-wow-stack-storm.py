#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A dead thread's 32-bit stack is released in its own window, and a storm in a child process ends that process only; no Wine runs.

Battle.net (build 592, 2026-10-23, log 2887384d): the launcher (a 32-bit CEF program) runs six Chromium helpers, each a 32-bit
pseudo-process in its own 4 GB guest window, and every one lays its threads' 8 MB stacks out at the same guest addresses. exit_thread()
hands the PREVIOUS exiting thread's TEB to the NEXT exiting thread (thread_ios.c, `prev_teb`), and virtual_free_teb() released the dead
thread's 32-bit stack at guest + ios_wow_base() of the thread doing the freeing. A worker of one helper exited, the next exit was a thread
of the network-service helper, and guest 0x05dd0000 + the network service's window was the LIVE stack of its Chrome_ChildIOThread. The
release was held 2 s (MADEIRA_FREE_DELAY_MS) and deleted the view; the thread went on running on pages that were PROT_NONE, the redelivery
guard counted 2000 identical faults and ended the whole app (iOS home screen) although the faulting thread belonged to a helper.

Held here:
  1. ios_wow_stack_free.h + ios_wow_stack_free_target (virtual_ios.c): the decision table (compiled), and the function itself run on the
     Battle.net layout (six windows, one live stack at the same guest address in two of them): default = the old address except where it
     would delete a live view (then the dead thread's own window, or nothing when that window is gone); MADEIRA_WOW_STACK_FREE_OWN_WINDOW
     = 1 always its own window, = 0 exactly the old address;
  2. ios_storm_end.h + the guard's terminal (signal_arm64_ios.c): the decision table (compiled): the session's first process, an unknown
     PEB, a fault outside the JIT pool and a process that owns the fixed-base image keep the old ending; a live child ends that process
     only; a laggard thread of an ended child ends that thread only; MADEIRA_REDELIV_CHILD_ONLY=0 is the old ending for everything;
  3. the worker that ends the process (server_ios.c) does the steps process_exit_wrapper does for a child and nothing of what it does for
     the session's first process; the guard still has exactly one task_terminate;
  4. the four changed files compile for iOS (syntax only) when the pinned wine headers exist;
  5. ios_bus_syscall_fault (signal_arm64_ios.c): a SIGBUS (protection fault) inside a system call that has a __TRY returns to the
     __EXCEPT block instead of becoming a guest exception on the kernel stack (EA app installer, build 592: ReadProcessMemory on its own
     stack's reserved lowest page ended two installer processes); the real handle_syscall_fault runs against a fake context: with a
     jmp_buf and inside the call the context is redirected to longjmp, without a jmp_buf, outside a system call or without a TEB nothing
     is touched.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
nt = root / 'build/ntdll-unix'
virtual = (nt / 'virtual_ios.c').read_text()
signal = (nt / 'signal_arm64_ios.c').read_text()
server = (nt / 'server_ios.c').read_text()
process = (nt / 'process_ios.c').read_text()
wsf_path = nt / 'ios_wow_stack_free.h'
storm_path = nt / 'ios_storm_end.h'
wsf = wsf_path.read_text()
storm = storm_path.read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}\n', start) + 3]


# ---------------------------------------------------------------- source shape: the stack release
vft = function(virtual, 'void virtual_free_teb( TEB *teb )')
target = function(virtual, 'static void *ios_wow_stack_free_target( TEB *dead, void *guest )')
check('virtual_free_teb aims the 32-bit stack release through ios_wow_stack_free_target and still releases through NtFreeVirtualMemory',
      'ptr = ios_wow_stack_free_target( teb, ptr );' in vft and 'if (ptr) NtFreeVirtualMemory( GetCurrentProcess(), &ptr, &size, MEM_RELEASE );' in vft)
check('the freeing thread\'s window is no longer added to the dead thread\'s guest address in virtual_free_teb itself',
      'ptr = (char *)ptr + ios_wow_base();' not in vft)
check('the old address is still guest + the freeing thread\'s window (the default for every case but one)',
      'ULONG_PTR caller_base = ios_wow_base(), dead_base;' in target and 'char *old_ptr = (char *)guest + caller_base;' in target
      and 'return old_ptr;' in target)
check('the switch is read through the header only; =0 returns the old address before anything else is looked at',
      'int mode = ios_wsf_mode()' in target and 'if (mode == IOS_WSF_MODE_OFF) return old_ptr;' in target
      and target.index('if (mode == IOS_WSF_MODE_OFF) return old_ptr;') < target.index('ios_wow_window_base_of_addr('))
check('the 64-bit stack, the CPU area and the kernel stack (host addresses) are released as before',
      'if (teb->DeallocationStack) ios_stack_release( teb->DeallocationStack );' in vft
      and 'NtFreeVirtualMemory( GetCurrentProcess(), (void **)&teb->ChpeV2CpuAreaInfo, &size, MEM_RELEASE );' in vft
      and 'NtFreeVirtualMemory( GetCurrentProcess(), &thread_data->kernel_stack, &size, MEM_RELEASE );' in vft)
check('the header is included once, before its user', virtual.count('#include "ios_wow_stack_free.h"') == 1
      and virtual.index('#include "ios_wow_stack_free.h"') < virtual.index('static void *ios_wow_stack_free_target('))
check('the line says what it did and is capped (8, then powers of two)', '[wow-stack-free] #%d' in target and 'n <= 8 || !(n & (n - 1))' in target)
check('the window lookup reads the registry only, and a slot counts as live only for the PEB it is bound to',
      'ios_wow_windows[i].peb && ios_wow_windows[i].peb == peb' in virtual and '!ios_wow_windows[i].leaked && !ios_wow_windows[i].dead' in virtual)

# ---------------------------------------------------------------- source shape: the storm guard
term_at = signal.index('[redeliv] terminating process rev=ml465')
call = 'if (ios_storm_end_child_only( thread, thread_teb, pc ))'
check('the guard asks ios_storm_end_child_only once, after the forensic dump and before the whole-app ending',
      signal.count(call) == 1 and signal.index('[redeliv-mem] x%d=0x%llx: %016llx') < signal.index(call) < term_at)
check('the whole-app ending is still there exactly once, unchanged (task_terminate, _exit, park)',
      signal.count('task_terminate( mach_task_self() );') == 1 and signal.count('_exit( 76 );') == 1
      and 'task_terminate( mach_task_self() );\n            _exit( 76 );\n            for (;;) pause();' in signal)
sef = function(signal, 'static int ios_storm_end_child_only( thread_t thread, uintptr_t thread_teb, uint64_t pc )')
check('it never ends the app itself (no task_terminate, _exit, exit, abort)', not [t for t in ('task_terminate', '_exit(', ' exit(', 'abort(', 'pthread_exit') if t in sef])
check('it reads the faulting thread\'s PEB through vm_read_overwrite and asks ios_storm_action',
      'vm_read_overwrite( mach_task_self(), (vm_address_t)(thread_teb + offsetof(TEB, Peb))' in sef and 'ios_storm_action( enabled,' in sef)
check('only a fault in the JIT pool counts as guest code', 'in_guest = (rxb && pc >= rxb && pc < rxb + ios_jit_pool_size_global) || ios_jit_low_contains( (uintptr_t)pc );' in sef)
check('the storming thread is ended with thread_terminate (suspend when that fails), as the [zombie] rule does',
      'thread_terminate( thread )' in sef and 'thread_suspend( thread )' in sef)
# Round 630 (agent T1 S30): a process already inside ExitProcess ends with the status it chose (ios_exiting_status), others with c0000005.
check('a process that could not be handed to the worker keeps the old ending; one already exiting ends with its own status',
      'if (!ios_end_child_process_async( peb, exiting ? (int)chosen : (int)STATUS_ACCESS_VIOLATION )) return 0;' in sef
      and 'int exiting = ios_exiting_status( peb, &chosen );' in sef)
check('32 endings in a session is the limit, then the old ending', 'ended >= 32' in sef)
check('after a handled storm the counter slot and the latch are cleared and the delivery returns',
      'redeliv[rslot].thread = 0;' in signal[signal.index(call):term_at] and 'ios_redeliv_terminating = 0;' in signal[signal.index(call):term_at]
      and 'return 1;' in signal[signal.index(call):term_at])
check('the header is included once, before its user', signal.count('#include "ios_storm_end.h"') == 1
      and signal.index('#include "ios_storm_end.h"') < signal.index('static int ios_storm_end_child_only('))

# ---------------------------------------------------------------- source shape: the worker (server_ios.c)
pew0 = server.index('void process_exit_wrapper( int status )')
pew = server[pew0:server.index('\n}\n', pew0) + 3]
child_branch = pew[pew.index('if (i >= 0)'):pew.index('    else\n    {\n        /* No slot')]
worker = function(server, 'static void *ios_storm_end_thread( void *arg )')
steps = ['ios_fdt_note_close(', 'close(', 'ios_child_slot_exited(', 'ios_child_slot_note_exit(', 'ios_dead_pebs[', 'ios_fd_cache_release(',
         'ios_subfloor_release_owner(', 'ios_jit_reclaim_process(', 'ios_wow_window_release(']
check('every release process_exit_wrapper makes for a child is made by the worker too (%d steps)' % len(steps),
      all(s in child_branch and s in worker for s in steps), str([s for s in steps if s not in worker]))
check('the worker does nothing process_exit_wrapper does only for the session\'s first process or only on the owning thread',
      not [t for t in ('wine_launched_process_did_exit', 'close( fd_socket )', 'ios_retire_own_fixed_base_image', 'ios_exe_win_mark_ready')
                       if t in worker] and not re.search(r'(?<![A-Za-z0-9_])exit\(', worker))
check('the worker claims the registry entry with a compare-and-swap, so an ordinary exit and the storm cannot both close the socket',
      '__sync_bool_compare_and_swap( &ios_proc_sockets[i].peb, a.peb, NULL )' in worker)
check('ios_end_child_process_async starts nothing for a PEB that is not a live child',
      'if (!ios_peb_is_live_child( peb )) return 0;' in function(server, 'int ios_end_child_process_async( void *peb, int status )'))
live = function(server, 'int ios_peb_is_live_child( const void *peb )')
check('ios_peb_is_live_child answers 1 only for an entry of the child registry (the first process has none), lock-free',
      'for (i = 0; i < n; i++) if (ios_proc_sockets[i].peb == peb) return 1;' in live and 'return 0;' in live and 'pthread_mutex' not in live)
check('the child table can name the process', 'int ios_child_slot_name( void *peb, char *out, size_t len )' in process and 'strncpy' not in process[process.index('int ios_child_slot_name('):process.index('int ios_child_slot_note_exit(')])
check('a process that owns the fixed-base image is asked for', 'ios_exe_win_owned_by( peb )' in sef and 'int ios_exe_win_owned_by( void *peb )' in virtual)

# ---------------------------------------------------------------- the headers, compiled
cc = os.environ.get('CC', 'cc')
HEADER_HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include "ios_wow_stack_free.h"
#include "ios_storm_end.h"
int main(int argc, char **argv)
{
    int mode = ios_wsf_mode(), enabled = ios_storm_child_only_enabled();
    (void)argv;
    printf("mode=%d enabled=%d\n", mode, enabled);
    if (argc > 1)
    {
        for (int caller = 0; caller < 3; caller++)
        for (int dead = 0; dead < 3; dead++)
        for (int live = 0; live < 2; live++)
        for (int hits = 0; hits < 2; hits++)
            printf("w %d %d %d %d %d\n", caller, dead, live, hits, ios_wsf_decide( mode, caller ? 0x7700000000ull * caller : 0, dead ? 0x7700000000ull * dead : 0, live, hits ));
        for (int v = 0; v < 64; v++)
            printf("s %d %d %d %d %d %d %d\n", v & 1, (v >> 1) & 1, (v >> 2) & 1, (v >> 3) & 1, (v >> 4) & 1, (v >> 5) & 1,
                   ios_storm_action( v & 1, (v >> 1) & 1, (v >> 2) & 1, (v >> 3) & 1, (v >> 4) & 1, (v >> 5) & 1 ));
    }
    return 0;
}
'''


def expected_wsf(mode, caller, dead, live, hits):
    # 0 old, 1 own, 2 skip
    if mode == 0:
        return 0
    cb = 0x7700000000 * caller if caller else 0
    db = 0x7700000000 * dead if dead else 0
    if not db or db == cb:
        return 0
    if mode == 2 and not hits:
        return 0
    return 1 if live else 2


def expected_storm(enabled, have_peb, dead, live_child, fixed, guest):
    if not enabled or not have_peb or not guest:
        return 0
    if dead:
        return 2
    if not live_child:
        return 0
    if fixed:
        return 0
    return 1


def run(binary, *args, env=None):
    e = dict(os.environ)
    e.pop('MADEIRA_WOW_STACK_FREE_OWN_WINDOW', None)
    e.pop('MADEIRA_REDELIV_CHILD_ONLY', None)
    e.update(env or {})
    return subprocess.run([str(binary), *args], capture_output=True, text=True, env=e)


if not shutil.which(cc):
    print('skip the compiled runs: no C compiler')
else:
    with tempfile.TemporaryDirectory(prefix='wowstack-') as d:
        d = Path(d)
        (d / 'h.c').write_text(HEADER_HARNESS)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', f'-I{nt}', str(d / 'h.c'), '-o', str(d / 'h')], capture_output=True, text=True)
        check('the two headers compile (host cc, -Wall -Wextra -Werror)', c.returncode == 0, c.stderr[-2000:])
        if c.returncode == 0:
            h = d / 'h'
            for env_val, mode in ((None, 2), ('', 2), ('0', 0), ('1', 1), ('2', 2), ('01', 2), ('true', 2), (' 1', 2)):
                r = run(h, env=None if env_val is None else {'MADEIRA_WOW_STACK_FREE_OWN_WINDOW': env_val})
                check('MADEIRA_WOW_STACK_FREE_OWN_WINDOW=%r is mode %d' % (env_val, mode), r.stdout.startswith('mode=%d ' % mode), r.stdout)
            for env_val, enabled in ((None, 1), ('', 1), ('0', 0), ('1', 1), ('00', 1), ('off', 1)):
                r = run(h, env=None if env_val is None else {'MADEIRA_REDELIV_CHILD_ONLY': env_val})
                check('MADEIRA_REDELIV_CHILD_ONLY=%r is %s' % (env_val, 'on' if enabled else 'off'), r.stdout.split('enabled=')[1].strip() == str(enabled), r.stdout)
            for env_val, mode in ((None, 2), ('0', 0), ('1', 1)):
                r = run(h, 'table', env=None if env_val is None else {'MADEIRA_WOW_STACK_FREE_OWN_WINDOW': env_val})
                bad = []
                for line in r.stdout.splitlines():
                    p = line.split()
                    if p[0] != 'w':
                        continue
                    caller, dead, live, hits, got = map(int, p[1:])
                    if got != expected_wsf(mode, caller, dead, live, hits):
                        bad.append(line)
                check('stack release table, mode %d: every one of the 36 inputs gives the documented answer' % mode, not bad and len([l for l in r.stdout.splitlines() if l.startswith('w ')]) == 36, str(bad[:4]))
            # the safety claims, stated directly
            r = run(h, 'table', env={'MADEIRA_WOW_STACK_FREE_OWN_WINDOW': '0'})
            check('switch =0: the answer is the old address for all 36 inputs', all(l.endswith(' 0') for l in r.stdout.splitlines() if l.startswith('w ')))
            r = run(h, 'table')
            rows = [list(map(int, l.split()[1:])) for l in r.stdout.splitlines() if l.startswith('w ')]
            check('default: the answer differs from the old address only when the old address is a live view and the dead thread\'s window is another one',
                  all((row[4] != 0) == (row[3] == 1 and row[1] != 0 and row[1] != row[0]) for row in rows), str(rows[:3]))
            check('default: a dead window is never released into', all(row[4] != 1 for row in rows if row[2] == 0))
            check('default: a thread of the same window, or a TEB in no window, is never redirected',
                  all(row[4] == 0 for row in rows if row[1] == row[0] or row[1] == 0))
            r = run(h, 'table')
            bad = []
            for line in r.stdout.splitlines():
                p = line.split()
                if p[0] != 's':
                    continue
                en, hp, dd, lc, fx, ig, got = map(int, p[1:])
                if got != expected_storm(en, hp, dd, lc, fx, ig):
                    bad.append(line)
            check('storm table: all 64 inputs give the documented answer', not bad and len([l for l in r.stdout.splitlines() if l.startswith('s ')]) == 64, str(bad[:4]))
            storm_rows = [list(map(int, l.split()[1:])) for l in r.stdout.splitlines() if l.startswith('s ')]
            check('storm: with the switch off, without a PEB or outside the JIT pool the answer is always the whole-app ending',
                  all(row[6] == 0 for row in storm_rows if not row[0] or not row[1] or not row[5]))
            check('storm: a PEB nobody registered (not dead, not a live child) and the owner of the fixed-base image keep the whole-app ending',
                  all(row[6] == 0 for row in storm_rows if row[0] and row[1] and row[5] and not row[2] and (not row[3] or row[4])))
            check('storm: the only way to end a process is a live child that does not own the fixed-base image',
                  all(row[6] != 1 or (row[3] == 1 and row[4] == 0 and row[2] == 0) for row in storm_rows))

        # --------------------------------------------------------- the real function on the Battle.net layout
        win0 = function(virtual, 'static ULONG_PTR ios_wow_window_base_of_addr( const void *addr, const void *peb, int *live )')
        hit0 = function(virtual, 'static int ios_wow_stack_old_target_hits_view( void *ptr )')
        FUNCS = win0 + '\n' + hit0 + '\n' + target
        SCEN = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned long ULONG_PTR;
typedef long LONG;
typedef void *HANDLE;
typedef int sigset_t_fake;
#define sigset_t sigset_t_fake
#define HandleToULong(h) ((unsigned)(ULONG_PTR)(h))
#define IOS_WOW_WINDOW_SIZE ((ULONG_PTR)0x100000000ull)
#define dprintf(fd, ...) fprintf(stderr, __VA_ARGS__)
#define InterlockedIncrement(p) (++*(p))
typedef struct { void *UniqueProcess; HANDLE UniqueThread; } CLIENT_ID;
typedef struct _TEB { void *Peb; CLIENT_ID ClientId; } TEB;
struct ios_wow_window { void *peb; ULONG_PTR base; unsigned leaked; unsigned dead; };
struct ios_wow_placeholder { ULONG_PTR base; };
static struct ios_wow_window ios_wow_windows[8];
static unsigned ios_wow_window_count;
static struct ios_wow_placeholder ios_wow_placeholders[8];
static unsigned ios_wow_placeholder_count;
static ULONG_PTR caller_window;
static ULONG_PTR ios_wow_base(void) { return caller_window; }
struct file_view { void *base; size_t size; };
static struct file_view views[16];
static int nviews;
static int virtual_mutex;
#define server_enter_uninterrupted_section(m, s) ((void)(m), (void)(s))
#define server_leave_uninterrupted_section(m, s) ((void)(m), (void)(s))
static struct file_view *find_view( void *addr, size_t size )
{
    (void)size;
    for (int i = 0; i < nviews; i++)
        if ((char *)addr >= (char *)views[i].base && (char *)addr < (char *)views[i].base + views[i].size) return &views[i];
    return NULL;
}
static int is_view_valloc( struct file_view *v ) { (void)v; return 1; }
#include "ios_wow_stack_free.h"
''' + '\n' + FUNCS + r'''

'''
        # The TEB address is what the window lookup keys on, so the scenario needs TEBs AT window addresses: real pages are mapped
        # there (a hint, never MAP_FIXED; the scenario is skipped when the kernel does not give exactly that address).
        SCEN2 = SCEN + r'''
#include <sys/mman.h>
static void *peb_of[8];
static TEB *teb_at( ULONG_PTR addr, void *peb, unsigned tid )
{
    void *p = mmap( (void *)addr, 0x4000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0 );
    TEB *t;
    if (p != (void *)addr) { fprintf(stderr, "mmap %lx failed\n", addr); exit(2); }
    t = p; t->Peb = peb; t->ClientId.UniqueThread = (HANDLE)(ULONG_PTR)tid;
    return t;
}
static const char *answer( void *r, ULONG_PTR old, ULONG_PTR own )
{
    if (!r) return "skip";
    if ((ULONG_PTR)r == old) return "old";
    if ((ULONG_PTR)r == own) return "own";
    return "other";
}

int main(int argc, char **argv)
{
    ULONG_PTR B76 = 0x7600000000ull, B77 = 0x7700000000ull, B78 = 0x7800000000ull, B73 = 0x7300000000ull;
    void *guest = (void *)0x05dd0000;
    TEB *t77, *t78, *t76, *t73, *tfree, *tnone;
    (void)argc; (void)argv;
    ios_wow_windows[0] = (struct ios_wow_window){ &peb_of[0], B76, 0, 1 };   /* the exited renderer: dead, its PEB still named */
    ios_wow_windows[1] = (struct ios_wow_window){ &peb_of[1], B77, 0, 0 };
    ios_wow_windows[2] = (struct ios_wow_window){ &peb_of[2], B78, 0, 0 };
    ios_wow_windows[3] = (struct ios_wow_window){ &peb_of[4], B73, 0, 0 };   /* re-adopted by a NEW process (peb 4) */
    ios_wow_window_count = 4;
    ios_wow_placeholders[0].base = 0x7200000000ull; ios_wow_placeholder_count = 1;
    t77 = teb_at( B77 + 0x290000, &peb_of[1], 0x390 );
    t78 = teb_at( B78 + 0x290000, &peb_of[2], 0x354 );
    t76 = teb_at( B76 + 0x280000, &peb_of[0], 0x660 );
    t73 = teb_at( B73 + 0x290000, &peb_of[3], 0x700 );   /* a dead process's TEB in a window that now belongs to another PEB */
    tfree = teb_at( 0x7200000000ull + 0x290000, &peb_of[5], 0x800 );   /* in a placeholder (no process) */
    tnone = teb_at( 0x7a00000000ull + 0x290000, &peb_of[6], 0x900 );   /* in no window and no placeholder */
    views[0] = (struct file_view){ (void *)(B77 + 0x05dd0000), 0x800000 };   /* the network service's live thread stack */
    views[1] = (struct file_view){ (void *)(B78 + 0x05dd0000), 0x800000 };   /* the storage service's */
    nviews = 2;

    /* A: thread 037c of the network service (window 0x77) frees the TEB of the storage service's thread 0354 (window 0x78):
     *    the old address is the network service's LIVE stack */
    caller_window = B77;
    printf("A cross-live-hit %s\n", answer( ios_wow_stack_free_target( t78, guest ), B77 + 0x05dd0000, B78 + 0x05dd0000 ));
    /* B: the same thread frees a TEB of its own window: the old address, whatever stands there */
    printf("B same-window %s\n", answer( ios_wow_stack_free_target( t77, guest ), B77 + 0x05dd0000, B77 + 0x05dd0000 ));
    /* C: the dead thread's window is gone (the renderer exited): the live stack at the old address must not be released */
    printf("C cross-dead-hit %s\n", answer( ios_wow_stack_free_target( t76, guest ), B77 + 0x05dd0000, B76 + 0x05dd0000 ));
    /* D: nothing stands at the old address (guest 0x04000000 in the caller's window): the old address, as before */
    printf("D cross-live-nohit %s\n", answer( ios_wow_stack_free_target( t78, (void *)0x04000000 ), B77 + 0x04000000, B78 + 0x04000000 ));
    /* E: the window was torn down and handed to another process: this TEB is not that process's, so it is not "live" */
    printf("E recycled-window-hit %s\n", answer( ios_wow_stack_free_target( t73, guest ), B77 + 0x05dd0000, B73 + 0x05dd0000 ));
    /* F: a TEB in a placeholder (an unadopted, torn-down window): like a dead window, the live stack at the old address is not released */
    printf("F placeholder %s\n", answer( ios_wow_stack_free_target( tfree, guest ), B77 + 0x05dd0000, 0x7200000000ull + 0x05dd0000 ));
    /* H: a TEB in no window at all: nothing to tell apart, the old address */
    printf("H no-window %s\n", answer( ios_wow_stack_free_target( tnone, guest ), B77 + 0x05dd0000, 0x7a00000000ull + 0x05dd0000 ));
    /* G: a thread without a window frees a WoW thread's TEB (its old address is a low guest number, nothing is mapped there) */
    caller_window = 0;
    printf("G caller-without-window %s\n", answer( ios_wow_stack_free_target( t78, guest ), 0x05dd0000, B78 + 0x05dd0000 ));
    return 0;
}
'''
        (d / 's.c').write_text(SCEN2)
        s = subprocess.run([cc, '-std=gnu11', '-w', f'-I{nt}', str(d / 's.c'), '-o', str(d / 's')], capture_output=True, text=True)
        check('ios_wow_stack_free_target and its two helpers, extracted from virtual_ios.c, compile against the fakes', s.returncode == 0, s.stderr[-3000:])
        if s.returncode == 0 and sys.platform in ('darwin', 'linux'):
            # the fixed addresses are 0x7x00000000: on macOS arm64 that range is free in a small process; fall back to a skip if it is not
            r = run(d / 's')
            if r.returncode == 2:
                print('skip the scenario run: the fixed addresses could not be mapped here (%s)' % r.stderr.strip()[:100])
            else:
                check('the scenario program runs', r.returncode == 0, r.stderr + r.stdout)
                out = {l.split()[0]: l.split()[2] for l in r.stdout.splitlines() if len(l.split()) == 3}
                check('A  Battle.net: a thread of the network service frees a storage-service TEB while the old address is a live stack -> released in the storage service\'s own window',
                      out.get('A') == 'own', r.stdout)
                check('B  same window -> the old address', out.get('B') == 'old', r.stdout)
                check('C  the dead thread\'s window is gone and the old address is a live stack -> not released', out.get('C') == 'skip', r.stdout)
                check('D  no live view at the old address -> the old address, as before', out.get('D') == 'old', r.stdout)
                check('E  a slot handed to another process is not live for this TEB: the live stack at the old address is not released', out.get('E') == 'skip', r.stdout)
                check('F  a TEB in a placeholder (torn-down window) and the old address is a live stack -> not released', out.get('F') == 'skip', r.stdout)
                check('H  a TEB in no window at all -> the old address', out.get('H') == 'old', r.stdout)
                check('G  a caller without a window, nothing mapped at the old low address -> the old address', out.get('G') == 'old', r.stdout)
                check('the line is printed for the cross-window cases and says live/gone, the old address and what was done',
                      '[wow-stack-free] #1' in r.stderr and 'released in its own window' in r.stderr and 'not released (its window is gone)' in r.stderr
                      and 'released at the old address (as before)' in r.stderr and 'IS a live view' in r.stderr, r.stderr)
                for env_val, expect in (('0', {'A': 'old', 'C': 'old', 'E': 'old', 'F': 'old', 'G': 'old'}),
                                  ('1', {'A': 'own', 'B': 'old', 'C': 'skip', 'D': 'own', 'E': 'skip', 'F': 'skip', 'G': 'own', 'H': 'old'})):
                    r2 = run(d / 's', env={'MADEIRA_WOW_STACK_FREE_OWN_WINDOW': env_val})
                    out2 = {l.split()[0]: l.split()[2] for l in r2.stdout.splitlines() if len(l.split()) == 3}
                    check('MADEIRA_WOW_STACK_FREE_OWN_WINDOW=%s: %s' % (env_val, ', '.join('%s %s' % kv for kv in sorted(expect.items()))),
                          all(out2.get(k) == v for k, v in expect.items()), r2.stdout)
                    if env_val == '0':
                        check('=0 prints nothing', '[wow-stack-free]' not in r2.stderr, r2.stderr)
        else:
            print('skip the scenario run (needs macOS or Linux for the 0x7x00000000 mappings)')

# ---------------------------------------------------------------- source shape + compiled run: a protection fault inside a system call's __TRY
bus0 = signal.index('static void bus_handler( int signal, siginfo_t *siginfo, void *sigcontext )')
bus = signal[bus0:signal.index('\n}\n', bus0) + 3]
seg0 = signal.index('static void segv_handler( int signal, siginfo_t *siginfo, void *sigcontext )')
seg = signal[seg0:signal.index('\n}\n', seg0) + 3]
hsf = function(signal, 'static int ios_bus_syscall_fault( ucontext_t *ctx, EXCEPTION_RECORD *rec )')
hsc = function(signal, 'static BOOL handle_syscall_fault( ucontext_t *context, EXCEPTION_RECORD *rec )')
call1 = 'if (ios_bus_syscall_fault( bus_ctx, &vrec ))'
check('bus_handler asks ios_bus_syscall_fault twice: once when the page cannot be repaired, once for a permission fault',
      bus.count(call1) == 2 and signal.count('ios_bus_syscall_fault(') == 3)
heal = bus.index('guard/watch/commit state, was fatal before ml420')
probe = bus.index('rd_kr = mach_vm_read_overwrite(')
check('the first call is after wine\'s own repair (virtual_handle_fault) and before the readability probe and every diagnostic of the unreadable class',
      heal < bus.index(call1) < probe)
check('the second call opens the permission-fault block (data abort, not an alignment fault)',
      bus.index('if ((ec == 0x24 || ec == 0x25) && (esr & 0x3f) != 0x21)') < bus.rindex(call1) < bus.index('static unsigned protection_av_logs;') + 400)
check('each call returns through ios_fixup_x18_for_return, like segv_handler does after handle_syscall_fault',
      bus.count(call1 + '\n            {\n                ios_fixup_x18_for_return( bus_ctx );\n                return;\n            }') == 1
      and 'if (ios_bus_syscall_fault( bus_ctx, &vrec ))\n                    {\n                        ios_fixup_x18_for_return( bus_ctx );\n                        return;\n                    }' in bus)
check('segv_handler is unchanged (it still calls handle_syscall_fault itself, once)', seg.count('handle_syscall_fault( context, &rec )') == 1 and 'ios_bus_syscall_fault' not in seg)
check('the helper does nothing without a TEB or without an active __TRY (jmp_buf), and then asks handle_syscall_fault',
      'if (!NtCurrentTeb() || !ntdll_get_thread_data()->jmp_buf) return 0;' in hsf and 'if (!handle_syscall_fault( ctx, rec )) return 0;' in hsf
      and hsf.index('jmp_buf') < hsf.index('handle_syscall_fault'))
check('the line is capped at 16', 'said < 16' in hsf and '<= 16' in hsf)
check('the check for a system call is the helper\'s own, unchanged: the stack pointer on the kernel stack (is_inside_syscall)',
      'if (!is_inside_syscall( SP_sig(context) )) return FALSE;' in hsc)

if shutil.which(cc):
    with tempfile.TemporaryDirectory(prefix='busfault-') as d:
        d = Path(d)
        h = r"""
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned long ULONG_PTR; typedef unsigned int DWORD; typedef unsigned long DWORD64; typedef int BOOL;
#define TRUE 1
#define FALSE 0
#define TRACE(...) do {} while (0)
#define dprintf(fd, ...) fprintf(stderr, __VA_ARGS__)
#define ucontext_t fake_ucontext_t
typedef struct { unsigned long x[31]; unsigned long sp, pc, lr, fp; } fake_ucontext_t;
#define PC_sig(c) ((c)->pc)
#define SP_sig(c) ((c)->sp)
#define LR_sig(c) ((c)->lr)
#define FP_sig(c) ((c)->fp)
#define REGn_sig(n, c) ((c)->x[n])
typedef struct { DWORD ExceptionCode; DWORD ExceptionFlags; void *ExceptionRecord; void *ExceptionAddress; DWORD NumberParameters; ULONG_PTR ExceptionInformation[15]; } EXCEPTION_RECORD;
struct syscall_frame { unsigned long pad[102]; };
struct ntdll_thread_data { void *kernel_stack; struct syscall_frame *syscall_frame; void *jmp_buf; };
typedef struct _TEB { struct ntdll_thread_data td; } TEB;
static TEB the_teb;
static TEB *cur_teb = &the_teb;
#define NtCurrentTeb() (cur_teb)
static struct ntdll_thread_data *ntdll_get_thread_data(void) { return &NtCurrentTeb()->td; }
static struct syscall_frame *get_syscall_frame(void) { return ntdll_get_thread_data()->syscall_frame; }
static BOOL is_inside_syscall( ULONG_PTR sp )
{
    return ((char *)sp >= (char *)ntdll_get_thread_data()->kernel_stack && (char *)sp <= (char *)get_syscall_frame());
}
void __wine_syscall_dispatcher_return(void) {}
""" + hsc + "\n" + hsf + r"""
static char kstack[0x10000];
int main(void)
{
    ucontext_t c; EXCEPTION_RECORD rec; int r;
    jmp_buf jb;
    memset(&rec, 0, sizeof rec); rec.ExceptionCode = 0xc0000005; rec.NumberParameters = 2; rec.ExceptionInformation[1] = 0x7203a00000ul;
    the_teb.td.kernel_stack = kstack; the_teb.td.syscall_frame = (struct syscall_frame *)(kstack + 0x8000);

    /* 1: inside a system call, a __TRY is active */
    memset(&c, 0, sizeof c); c.sp = (unsigned long)(kstack + 0x7000); c.pc = 0x1234; c.lr = 0x5678; the_teb.td.jmp_buf = jb;
    r = ios_bus_syscall_fault(&c, &rec);
    printf("1 ret=%d pc_is_longjmp=%d x0_is_jmpbuf=%d x1=%lu jmpbuf_cleared=%d x18=%lu\n", r, c.pc == (unsigned long)longjmp, c.x[0] == (unsigned long)jb, c.x[1], the_teb.td.jmp_buf == NULL, c.x[18]);

    /* 2: inside a system call, no __TRY */
    memset(&c, 0, sizeof c); c.sp = (unsigned long)(kstack + 0x7000); c.pc = 0x1234; the_teb.td.jmp_buf = NULL;
    r = ios_bus_syscall_fault(&c, &rec);
    printf("2 ret=%d pc=%lx x0=%lx sp=%lx\n", r, c.pc, c.x[0], c.sp - (unsigned long)kstack);

    /* 3: a __TRY is active but the stack pointer is not on the kernel stack (guest code on the emulator stack) */
    memset(&c, 0, sizeof c); c.sp = 0x12345000ul; c.pc = 0x1234; the_teb.td.jmp_buf = jb;
    r = ios_bus_syscall_fault(&c, &rec);
    printf("3 ret=%d pc=%lx x0=%lx jmpbuf_kept=%d\n", r, c.pc, c.x[0], the_teb.td.jmp_buf == jb);

    /* 4: no TEB at all */
    memset(&c, 0, sizeof c); c.sp = (unsigned long)(kstack + 0x7000); c.pc = 0x1234;
    cur_teb = NULL;
    r = ios_bus_syscall_fault(&c, &rec);
    printf("4 ret=%d pc=%lx\n", r, c.pc);
    cur_teb = &the_teb;

    /* 5: the stack pointer just above the syscall frame (user mode) */
    memset(&c, 0, sizeof c); c.sp = (unsigned long)(kstack + 0x8000 + 8); c.pc = 0x1234; the_teb.td.jmp_buf = jb;
    r = ios_bus_syscall_fault(&c, &rec);
    printf("5 ret=%d pc=%lx\n", r, c.pc);

    /* 6: 20 faults: the line is printed 16 times */
    for (int i = 0; i < 20; i++)
    {
        memset(&c, 0, sizeof c); c.sp = (unsigned long)(kstack + 0x7000); the_teb.td.jmp_buf = jb;
        ios_bus_syscall_fault(&c, &rec);
    }
    return 0;
}
"""
        (d / 'b.c').write_text(h)
        c = subprocess.run([cc, '-std=gnu11', '-w', str(d / 'b.c'), '-o', str(d / 'b')], capture_output=True, text=True)
        check('handle_syscall_fault and ios_bus_syscall_fault, extracted from signal_arm64_ios.c, compile against a fake context', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            r = subprocess.run([str(d / 'b')], capture_output=True, text=True)
            lines = {l.split()[0]: l for l in r.stdout.splitlines()}
            check('inside a system call with a __TRY: handled, the context returns through longjmp( jmp_buf, 1 ) and the jmp_buf is cleared',
                  r.returncode == 0 and 'ret=1 pc_is_longjmp=1 x0_is_jmpbuf=1 x1=1 jmpbuf_cleared=1' in lines.get('1', ''), r.stdout)
            check('inside a system call without a __TRY: not handled and nothing in the context changed', lines.get('2', '').startswith('2 ret=0 pc=1234 x0=0 sp=7000'), r.stdout)
            check('a __TRY is active but the stack pointer is not on the kernel stack: not handled, the jmp_buf kept', lines.get('3', '') == '3 ret=0 pc=1234 x0=0 jmpbuf_kept=1', r.stdout)
            check('no TEB: not handled', lines.get('4', '') == '4 ret=0 pc=1234', r.stdout)
            check('above the syscall frame (user mode): not handled', lines.get('5', '') == '5 ret=0 pc=1234', r.stdout)
            check('the [syscall-fault] line is printed at most 16 times', len([l for l in r.stderr.splitlines() if l.startswith('[syscall-fault]')]) == 16
                  and 'rev=ajan11' in r.stderr, r.stderr)

# ---------------------------------------------------------------- the changed files compile for iOS (when the wine headers exist)
wine = Path(os.environ.get('WINE_SRC', root / 'wine'))
config = wine / 'build-macos/include/config.h'
if shutil.which('xcrun') and config.exists():
    try:
        sdk = subprocess.run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sdk = ''
    if sdk:
        flags = ['-fsyntax-only', '-arch', 'arm64', '-isysroot', sdk, '-miphoneos-version-min=17.0', '-O2', '-fPIC',
                 '-fno-strict-aliasing', '-Wno-implicit-function-declaration', '-Wno-int-conversion',
                 '-include', str(config), '-include', str(nt / 'shims/wine_ios_exit.h'), f'-I{nt}', f'-I{nt / "shims"}',
                 f'-I{nt / "../madsync"}', '-DHAVE_LINUX_NTSYNC_H=1',
                 f'-I{wine / "build-macos/dlls/ntdll"}', f'-I{wine / "dlls/ntdll"}', f'-I{wine / "dlls/ntdll/unix"}',
                 f'-I{wine / "build-macos/include"}', f'-I{wine / "include"}', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES',
                 '-DLTC_SOURCE', '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', '-DWINE_UNIX_LIB',
                 '-DWINE_IOS=1', '-Dget_thread_context=ntdll_get_thread_context', '-Dset_thread_context=ntdll_set_thread_context']
        for name in ('virtual_ios.c', 'signal_arm64_ios.c', 'server_ios.c', 'process_ios.c'):
            r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(nt / name)], capture_output=True, text=True)
            check('%s compiles for iOS (syntax only, pinned wine headers)' % name, r.returncode == 0, r.stderr[-2000:])
else:
    print('skip the iOS syntax compiles: no iPhoneOS SDK or no wine headers (set WINE_SRC)')

sys.exit(0 if ok else 1)
