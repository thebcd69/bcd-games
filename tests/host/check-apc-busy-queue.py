#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_APC_BUSY_QUEUE: an async I/O completion for a running thread stays with it when nobody else can take it; no Wine runs.

The EA app installer (build 593, the owner's iPhone18,2, log 17:15:17): the bundle reached its MSI package and the package stopped at
its first custom action, "JunoInitializeSession" (1603, INST-14-1603). The action is a WiX DTF managed custom action. msiexec's custom
action server (syswow64\\msiexec.exe -Embedding) loads the action's DLL (SfxCA.dll 3.11, saved as msif50e.tmp), which starts
rundll32.exe; rundll32 hosts Wine Mono and asks the server for every MSI call over an overlapped named pipe (RemoteMsiSession: write
the request, then ReadFile for the answer and wait on the OVERLAPPED event). rundll32 has ONE thread until the CLR starts. The server's
first answer arrived while that thread was running, not waiting in the server. BCD's own Windows server cannot interrupt a running
thread on iOS (send_thread_signal always fails), so queue_apc asks ios_apc_requeue_target for another thread of the process that waits;
there was none, and the APC was dropped ("[apc-requeue] #4 async I/O APC status=00000101 for busy thread 01c0 -> dropped, no waiting
thread"). thread_apc_destroy then completes the async with the APC's own status and no data: a pipe read that had data ends with
STATUS_ALERTED (0x101) and zero bytes. SfxCA's ReadPipe takes that for a failure and closes its end of the pipe (the server's read ends
with STATUS_PIPE_BROKEN, c000014b: the next [apc-requeue] line), its later Log() reads the answer of a request through a NULL pointer
(msif50e.tmp+0x409C, `mov esi,[eax+4]`: "wine: Unhandled page fault on write access to 00000004 at address 74AF409C") and the managed
RemotableNativeMethods does the same through Marshal.ReadInt32(NULL) (two NullReferenceException faults at guest address 0 in Mono's
JIT code). Not Wine Mono, not FEX: the libmono address in the log's stack (+0x354363) is a pointer into its .rdata (a function-name
string of the GC-safe-region code), and 0x74AF409C is in SfxCA, not libmono (whose image ends at 0x74984000).

Held here (the Wine source is wine/ of this checkout, $BCD_WINE_SRC, or ~/madeira-bcd/wine; the parts that read it are skipped when
none is there, and fail when the one that is there is the commit this checkout pins and lacks the change):
  1. The reasoning's links in the Wine source: async_terminate gives STATUS_ALERTED to an async with output data, thread_apc_destroy
     completes an undelivered APC_ASYNC_IO with that status and 0 bytes, queue_apc asks ios_apc_requeue_target when the signal fails,
     every wait in NtWaitForSingleObject/NtWaitForMultipleObjects is interruptible, check_wait returns STATUS_KERNEL_APC to one.
  2. The change: the new function reads only MADEIRA_APC_BUSY_QUEUE and only the value 1 turns it on; the new line in
     ios_apc_requeue_target comes after the search for a waiting thread, needs no waiting thread, no in-process engine
     (thread->alert_sync, madsync) and the switch; the two old log texts are unchanged.
  3. The real ios_apc_requeue_enabled, ios_apc_busy_queue_enabled and ios_apc_requeue_target, extracted from thread.c and compiled on the
     host against stand-in types, next to the ORIGINAL functions (the text of the pinned commit's thread.c before this change), over
     every combination of switch (unset, 0, empty, 2, yes, 1), MADEIRA_APC_REQUEUE (unset, 0), APC type, waiting thread, suspended
     thread, terminated thread and in-process engine: with the switch anything but 1 the target AND everything written to stderr are
     the original's, byte for byte; with 1 only "no waiting thread, requeue on, async I/O APC, no in-process engine" differs (the
     target is the busy thread itself) and the log says so.
  4. The EA launcher entry sets the line (and Battle.net's does not), auto-tune's rule eaapp-installer-msi-pipe-answer-lost adds it
     after "JunoInitializeSession returned 1603" only, its fixture proposes it, the settings catalog knows the key, the CI runs this.
Needs python3 and a C compiler.
"""
from pathlib import Path
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}\n', start) + 3]


def git(*args, cwd=root):
    r = subprocess.run(['git', '-C', str(cwd)] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


# ---------------------------------------------------------------- where the Wine source is
pin = ''
tree = git('ls-tree', 'HEAD', 'wine')
if tree:
    pin = tree.split()[2]
wine = None
for candidate in (os.environ.get('BCD_WINE_SRC'), root / 'wine', Path.home() / 'madeira-bcd/wine'):
    if candidate and (Path(candidate) / 'server/thread.c').is_file():
        wine = Path(candidate)
        break
thread_c = (wine / 'server/thread.c').read_text() if wine else ''
has_change = 'ios_apc_busy_queue_enabled' in thread_c
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    # ------------------------------------------------------------ 1. the links of the reasoning
    async_c = (wine / 'server/async.c').read_text()
    sync_c = (wine / 'dlls/ntdll/unix/sync.c').read_text()
    check('an async with output data is terminated with STATUS_ALERTED ("the client fetches the data")',
          'if (iosb && iosb->out_data)\n            data.async_io.status = STATUS_ALERTED;' in async_c)
    destroy = function(thread_c, 'static void thread_apc_destroy( struct object *obj )\n{')
    check('an APC_ASYNC_IO that was never delivered completes its async with the APC\'s own status and no bytes',
          'async_set_result( apc->owner, apc->call.async_io.status, 0 );' in destroy)
    qa = function(thread_c, 'static int queue_apc( struct process *process, struct thread *thread, struct thread_apc *apc )')
    check('queue_apc asks ios_apc_requeue_target when the signal to a busy thread cannot be sent, and takes its answer as the thread',
          'if (!send_thread_signal( thread, SIGUSR1 ))' in qa and 'struct thread *target = ios_apc_requeue_target( thread, apc );' in qa
          and 'if (!target) return 0;' in qa and 'thread = target;' in qa)
    check('a system APC queued on a thread that is not waiting is taken at its next wait: check_wait returns STATUS_KERNEL_APC to an interruptible one',
          '(wait->flags & SELECT_INTERRUPTIBLE) && !list_empty( &thread->system_apc )' in function(thread_c, 'static int check_wait( struct thread *thread )')
          and 'return STATUS_KERNEL_APC;' in function(thread_c, 'static int check_wait( struct thread *thread )'))
    nwso = function(sync_c, 'NTSTATUS WINAPI NtWaitForSingleObject( HANDLE handle, BOOLEAN alertable, const LARGE_INTEGER *timeout )')
    nwmo = function(sync_c, 'NTSTATUS WINAPI NtWaitForMultipleObjects( DWORD count, const HANDLE *handles, WAIT_TYPE type,')
    check('every wait of the two NtWait* calls is interruptible (a system APC ends it), and the in-process shortcuts end at the server',
          'UINT flags = SELECT_INTERRUPTIBLE;' in nwso and 'UINT i, flags = SELECT_INTERRUPTIBLE;' in nwmo
          and 'madeira_fast_wait( handle, timeout, &left, &madeira_store )' in nwso and 'server_wait( &select_op' in nwso)
    check('a fastsync wait parks in process for at most the cap (2 ms by default) and then falls through to the server',
          'MADEIRA_FAST_CAP_NS      2000000ull' in sync_c and 'return STATUS_NOT_IMPLEMENTED;                 /* re-wait, now on the server */' in sync_c)
    check('the in-process engine (madsync) is what gives a thread an alert_sync; without it every wait is the server\'s or fastsync\'s',
          'if (get_inproc_device_fd() >= 0 && !(thread->alert_sync = create_inproc_internal_sync( 1, 0 ))) goto error;' in thread_c)

    # ------------------------------------------------------------ 2. the change, in the source
    enabled = function(thread_c, 'static int ios_apc_busy_queue_enabled( void )\n{')
    target = function(thread_c, 'static struct thread *ios_apc_requeue_target( struct thread *thread, const struct thread_apc *apc )')
    req = function(thread_c, 'static int ios_apc_requeue_enabled( void )\n{')
    # Build 660: the switch is read on every call (the server runs again in the same process for the next session of an app run; a cached
    # answer kept the first game's setting: tests/host/check-apc-switches-per-session.py). The first session of an app run is unchanged,
    # which is what the comparison with the original functions below shows.
    check('the switch is MADEIRA_APC_BUSY_QUEUE, read where it is used (no answer kept from an earlier session), and only a value starting with 1 turns it on',
          'getenv( "MADEIRA_APC_BUSY_QUEUE" )' in enabled and 'int enabled = e && e[0] == \'1\';' in enabled and 'static int enabled' not in enabled)
    check('it is announced once, from the first-use line of the requeue switch, only when it is on',
          'if (enabled) ios_apc_busy_queue_enabled();' in req and 'if (enabled)\n            fprintf( stderr, "[apc-requeue] MADEIRA_APC_BUSY_QUEUE=1' in enabled)
    new_line = 'if (!target && !thread->alert_sync && ios_apc_busy_queue_enabled()) target = thread;'
    check('the busy thread is the target only when no waiting thread was found, there is no in-process engine and the switch is on',
          new_line in target and target.index('LIST_FOR_EACH_ENTRY') < target.index(new_line) < target.index('count++;'))
    check('a switch that is off (or a disabled requeue) returns before anything of the change is looked at',
          new_line in target and target.index('!ios_apc_requeue_enabled()) return NULL;') < target.index(new_line))
    check('the two old log texts are unchanged, and the new one is a third',
          '"waiting thread" :' in target and '"dropped, no waiting thread"' in target and '"kept on the busy thread"' in target)

    # ------------------------------------------------------------ 3. the real functions, compiled, next to the originals
    ORIGINAL = r'''static int ios_apc_requeue_enabled( void )
{
    static int enabled = -1;

    if (enabled < 0)
    {
        /* On by default: an async I/O completion for a busy thread goes to a waiting thread of
         * its process, if there is one, instead of failing the I/O. 0 always drops it again. */
        const char *e = getenv( "MADEIRA_APC_REQUEUE" );
        enabled = !(e && e[0] == '0');
        fprintf( stderr, "[apc-requeue] %s (MADEIRA_APC_REQUEUE=0 restores dropping async I/O APCs "
                 "for busy threads)\n", enabled ? "on" : "off" );
    }
    return enabled;
}

static struct thread *ios_apc_requeue_target( struct thread *thread, const struct thread_apc *apc )
{
    static unsigned int count;
    struct thread *candidate, *target = NULL;

    if (apc->call.type != APC_ASYNC_IO || !ios_apc_requeue_enabled()) return NULL;
    LIST_FOR_EACH_ENTRY( candidate, &thread->process->thread_list, struct thread, proc_entry )
    {
        if (candidate == thread || candidate->state == TERMINATED || is_thread_suspended( candidate )) continue;
        if (candidate->wait && (candidate->wait->flags & SELECT_INTERRUPTIBLE))
        {
            target = candidate;
            break;
        }
    }
    count++;
    if (count <= 16 || !(count & 1023))
        fprintf( stderr, "[apc-requeue] #%u async I/O APC status=%08x for busy thread %04x -> %s %04x\n",
                 count, apc->call.async_io.status, thread->id,
                 target ? "waiting thread" : "dropped, no waiting thread", target ? target->id : 0 );
    return target;
}
'''
    # Build 660: ios_apc_requeue_target also asks MADEIRA_APC_DEFER_ISSUER (tests/host/check-apc-defer-issuer.py). That switch is unset in
    # every run below, so its function is compiled in as it is and the server's async predicate is a stand-in that is never reached.
    defer = function(thread_c, 'static int ios_apc_defer_issuer_enabled( void )\n{') if 'ios_apc_defer_issuer_enabled' in thread_c else ''
    PATCHED = ('static int ios_apc_busy_queue_enabled( void );\n\n' + req + '\n' + enabled + '\n' + defer + '\n' + target)

    HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include "wine/list.h"

#define SELECT_ALERTABLE     1
#define SELECT_INTERRUPTIBLE 2
enum run_state { RUNNING, TERMINATED };
enum apc_type { APC_NONE, APC_USER, APC_ASYNC_IO, APC_VIRTUAL_ALLOC };

struct thread_wait { unsigned int flags; };
struct process { struct list thread_list; };
struct thread
{
    struct list proc_entry;
    struct process *process;
    enum run_state state;
    struct thread_wait *wait;
    void *alert_sync;
    unsigned int id;
    int suspended;
};
struct object;
struct thread_apc
{
    struct object *owner;
    struct { enum apc_type type; struct { unsigned int status; } async_io; } call;
};
static int is_thread_suspended( struct thread *thread ) { return thread->suspended; }
static int async_can_defer_to_issuer( struct object *obj, struct thread *thread ) { abort(); }   /* only with MADEIRA_APC_DEFER_ISSUER=1 */

#include "CODE"

/* argv: apc-type(0 other, 1 async io) other-thread(0 none, 1 waiting, 2 running, 3 suspended+waiting, 4 terminated+waiting, 5 waiting not interruptible)
 *       alert_sync(0/1) calls */
int main( int argc, char **argv )
{
    struct process p;
    struct thread busy, other;
    struct thread_wait w_int = { SELECT_INTERRUPTIBLE | SELECT_ALERTABLE }, w_plain = { 0 };
    struct thread_apc apc;
    int type = atoi( argv[1] ), mode = atoi( argv[2] ), madsync = atoi( argv[3] ), calls = atoi( argv[4] ), i;

    memset( &busy, 0, sizeof(busy) ); memset( &other, 0, sizeof(other) );
    list_init( &p.thread_list );
    busy.process = &p; busy.id = 0x1c0; busy.alert_sync = madsync ? (void *)1 : NULL;
    list_add_tail( &p.thread_list, &busy.proc_entry );
    if (mode)
    {
        other.process = &p; other.id = 0x1c4;
        other.wait = mode == 2 ? NULL : mode == 5 ? &w_plain : &w_int;
        other.suspended = mode == 3;
        other.state = mode == 4 ? TERMINATED : RUNNING;
        list_add_tail( &p.thread_list, &other.proc_entry );
    }
    memset( &apc, 0, sizeof(apc) );
    apc.call.type = type ? APC_ASYNC_IO : APC_USER;
    apc.call.async_io.status = 0x101;
    for (i = 0; i < calls; i++)
    {
        struct thread *t = ios_apc_requeue_target( &busy, &apc );
        printf( "call %d: %s\n", i, !t ? "none" : t == &busy ? "busy" : t == &other ? "other" : "?" );
    }
    return 0;
}
'''
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='apc-busy-queue-'))
    try:
        exes = {}
        for name, code in (('original', ORIGINAL), ('patched', PATCHED)):
            (tmp / (name + '.c')).write_text(HARNESS.replace('#include "CODE"', code))
            r = subprocess.run([cc, '-std=gnu11', '-O1', '-Wno-unused-function', '-I' + str(wine / 'include'), '-o', str(tmp / name),
                                str(tmp / (name + '.c'))], capture_output=True, text=True)
            check('the %s functions compile on the host' % name, r.returncode == 0, r.stderr[-1500:])
            exes[name] = tmp / name
        if len(exes) == 2 and all(p.exists() for p in exes.values()):
            def run(name, env, args):
                e = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
                e.update(env)
                r = subprocess.run([str(exes[name])] + [str(a) for a in args], env=e, capture_output=True, text=True)
                return r.returncode, r.stdout, r.stderr

            switches = [None, '0', '', '2', 'yes', 'true', '1', '10']
            requeues = [None, '0']
            announce = '[apc-requeue] MADEIRA_APC_BUSY_QUEUE=1:'

            def without_announcement(text):
                return ''.join(l for l in text.splitlines(True) if not l.startswith(announce))
            same = diff = 0
            bad = []
            for sw in switches:
                for rq in requeues:
                    for typ in (0, 1):
                        for mode in range(6):
                            for madsync in (0, 1):
                                env = {}
                                if sw is not None:
                                    env['MADEIRA_APC_BUSY_QUEUE'] = sw
                                if rq is not None:
                                    env['MADEIRA_APC_REQUEUE'] = rq
                                args = (typ, mode, madsync, 20)
                                o = run('original', env, args)
                                n = run('patched', env, args)
                                on = bool(sw and sw[0] == '1')
                                if not on:
                                    # off: not one byte differs, and the announcement never appears
                                    if o != n or announce in n[2]:
                                        bad.append(('off, but a difference', env, args, o, n))
                                    same += 1
                                    continue
                                # the announcement is written when the requeue switch is first looked at: an async I/O APC, requeue on
                                if (announce in n[2]) != (typ == 1 and rq != '0'):
                                    bad.append(('on, the announcement is not where it must be', env, args, n))
                                n = (n[0], n[1], without_announcement(n[2]))
                                # on: the only place the functions may differ, and exactly how
                                changes = rq != '0' and typ == 1 and mode in (0, 2, 3, 4, 5) and madsync == 0
                                if not changes:
                                    same += 1
                                    if o != n:
                                        bad.append(('on, a difference where there must be none', env, args, o, n))
                                else:
                                    diff += 1
                                    want = o[1].replace('none', 'busy')
                                    if (n[1] != want or 'kept on the busy thread' not in n[2] or 'dropped, no waiting thread' in n[2]):
                                        bad.append(('on, the change is not just none -> busy with its two lines', env, args, n))
            check('the switch off (unset, 0, empty, 2, yes, true): the target and every byte on stderr are the original\'s; on (1, 10): they differ only where an '
                  'async I/O APC has no waiting thread, requeue is on and there is no in-process engine (%d combinations: %d unchanged, %d changed)'
                  % (same + diff, same, diff), not bad and diff > 0, str(bad[:2]))
            # the waiting thread still wins; the old drop is what the switch changes, nothing else
            o = run('patched', {'MADEIRA_APC_BUSY_QUEUE': '1'}, (1, 1, 0, 1))
            check('with the switch on a thread that waits in the server still gets the APC (the old handover is first)', o[1] == 'call 0: other\n' and 'waiting thread 01c4' in o[2], str(o))
            o = run('patched', {'MADEIRA_APC_BUSY_QUEUE': '1'}, (1, 0, 0, 1))
            check('with the switch on and no other thread the APC stays on the busy thread, and the line says so',
                  o[1] == 'call 0: busy\n' and '-> kept on the busy thread 01c0' in o[2] and '#1 async I/O APC status=00000101 for busy thread 01c0' in o[2], str(o))
            o = run('patched', {'MADEIRA_APC_BUSY_QUEUE': '1'}, (1, 0, 1, 1))
            check('with the in-process engine on, the APC is dropped as before even with the switch on (a wait there is not interruptible)',
                  o[1] == 'call 0: none\n' and 'dropped, no waiting thread' in o[2], str(o))
            o = run('patched', {'MADEIRA_APC_BUSY_QUEUE': '1', 'MADEIRA_APC_REQUEUE': '0'}, (1, 0, 0, 1))
            check('MADEIRA_APC_REQUEUE=0 still drops everything, the new switch included', o[1] == 'call 0: none\n', str(o))
            o = run('patched', {'MADEIRA_APC_BUSY_QUEUE': '1'}, (0, 0, 0, 1))
            check('an APC that is not an async I/O completion is never kept by this switch', o[1] == 'call 0: none\n' and 'kept' not in o[2], str(o))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 4. the BCD side
launchers = (root / 'app/Madeira/BCDLaunchers.swift').read_text()
ea = launchers[launchers.index('static let eaApp = BCDLauncherSpec('):]
bn = launchers[launchers.index('static let battlenet = BCDLauncherSpec('):launchers.index('static let eaApp = BCDLauncherSpec(')]
check('the EA app\'s entry sets env.MADEIRA_APC_BUSY_QUEUE = 1 and Battle.net\'s, and the common lines, do not',
      '"env.MADEIRA_APC_BUSY_QUEUE = 1"' in ea and 'MADEIRA_APC_BUSY_QUEUE' not in bn and launchers.count('"env.MADEIRA_APC_BUSY_QUEUE = 1"') == 1
      and 'MADEIRA_APC_BUSY_QUEUE' not in re.search(r'static let commonSessionConfig = \[(.*?)\]', launchers).group(1))

spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rule = next((r for r in mod.RULES if r['id'] == 'eaapp-installer-msi-pipe-answer-lost'), None)
check('auto-tune has the rule, adding the one line, after the custom action\'s 1603 only, never again once the line is in force',
      bool(rule) and rule['lines'] == ['env.MADEIRA_APC_BUSY_QUEUE = 1']
      and rule['all'] == [{'has': ['Execution halted, action L"JunoInitializeSession" returned 1603']}]
      and rule['none'] == [{'has': ['[madeira-env] game MADEIRA_APC_BUSY_QUEUE=1']}] and rule.get('noRetry') is True)
# Was "the last block before the swap rules"; later agents' blocks come after it (ajan16, 2026-10-23), so: in the table, not a swap rule.
check('the rule is in the table, in its own block before the swap rules',
      'eaapp-installer-msi-pipe-answer-lost' in [r['id'] for r in mod.RULES[:len(mod.RULES) - len(mod.SWAP_RULES)]])
fx = root / 'tests/fixtures/autotune/eaapp-installer-msi-pipe-answer-lost-2026-10-23'
expect = json.loads(fx.with_suffix('.expect').read_text()) if fx.with_suffix('.expect').exists() else {}
log = fx.with_suffix('.log').read_text() if fx.with_suffix('.log').exists() else ''
check('its fixture is the owner\'s EA log with the failing action, and proposes the line',
      expect.get('next') == ['env.MADEIRA_APC_BUSY_QUEUE = 1'] and 'eaapp-installer-msi-pipe-answer-lost' in expect.get('matched', [])
      and 'action L"JunoInitializeSession" returned 1603' in log)
check('the fixture carries no personal data (no container path, no Windows user name, no e-mail address)',
      not re.search(r'/var/mobile/Containers|/Users/|(?i:users\\+)(?!<user>|public\b|mobile\b)[^\\/"\' ]|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}', log))
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the settings catalog knows the key (a bool, off by default, read in wine/server/thread.c)',
      'ConfigOption(key: "env.MADEIRA_APC_BUSY_QUEUE", title: "An answer waiting for a busy thread is kept", kind: .bool, defaultValue: "0"' in catalog
      and 'sources: ["wine/server/thread.c"]' in catalog.split('env.MADEIRA_APC_BUSY_QUEUE')[1].split('\n')[0])
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-apc-busy-queue.py' in ci)

print('PASS: MADEIRA_APC_BUSY_QUEUE' if ok else 'FAILED: MADEIRA_APC_BUSY_QUEUE')
sys.exit(0 if ok else 1)
