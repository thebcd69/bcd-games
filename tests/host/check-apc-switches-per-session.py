#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Windows server's three async-completion switches are read for each session, not once per app run (build 660); no Wine runs.

BCD's wineserver is a thread of the app, and it runs again in the same process for the next session of an app run
(build/wineserver/mach_ios.c, ios_real_suspend_enabled: "this server runs again in the same process for the next session of the app run";
auto-tune's rule isaac-opengl-after-d3d11-game-same-run has a log of a second game in one run). MADEIRA_APC_REQUEUE and
MADEIRA_APC_BUSY_QUEUE were read once and kept in a static, and so was the new MADEIRA_APC_DEFER_ISSUER in its first form. So:
  - the EA app's launcher entry, the Dock's Ubisoft sessions and two auto-tune rules set MADEIRA_APC_BUSY_QUEUE = 1 for one session and
    the app clears it when the session ends (WineProcessBridge.m), but every later game of that app run kept it on;
  - a game whose file sets a switch, started after another game's session, never got it.
All three are now read where they are used (Wine fork commits aa9d15493b9 and a6e7999e349), as ios_real_suspend_enabled is. The path is only
taken when an async I/O completion meets a running thread, so the cost is a getenv there.

The first session of an app run is unchanged: tests/host/check-apc-busy-queue.py compares the answers and every byte written to the log
with the functions as they were. This check is about the sessions after it.

Checks (the Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none, or when the one there
lacks the change and is not this checkout's pin):
  1. None of the three functions keeps an answer: each calls getenv on every call and has no cached "enabled".
  2. The real functions (and ios_apc_requeue_target, with the server's predicate), compiled on the host, in ONE process in which the
     three variables are set and cleared between calls the way sessions of an app run do: 2,000 random steps, and the scenarios named
     above. Every answer is what a model that looks only at the variables' values at that moment gives; nothing of an earlier step
     carries over.
  3. The log: the first read says what it always said; afterwards one line per change (on, off again), none while a value stays.
  4. The app clears the three variables at the end of a session, and the CI runs this.
No auto-tune rule: a leaked switch leaves no line of its own in a log. Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import random
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
    start = text.find(signature)
    if start < 0:
        return ''
    return text[start:text.index('\n}\n', start) + 3]


def git(*args, cwd=root):
    r = subprocess.run(['git', '-C', str(cwd)] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


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
busy = function(thread_c, 'static int ios_apc_busy_queue_enabled( void )\n{')
has_change = bool(busy) and 'static int enabled' not in busy
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include "wine/list.h"
#define SELECT_INTERRUPTIBLE 2
enum run_state { RUNNING, TERMINATED };
enum apc_type { APC_NONE, APC_USER, APC_ASYNC_IO };
typedef unsigned int obj_handle_t;
struct object_ops { int unused; };
struct object { const struct object_ops *ops; };
struct thread_wait { unsigned int flags; };
struct process { struct list thread_list; };
struct thread { struct list proc_entry; struct process *process; enum run_state state; struct thread_wait *wait; void *alert_sync; unsigned int id; int suspended; };
struct async { struct object obj; struct thread *thread; obj_handle_t wait_handle; unsigned int blocking :1; };
struct thread_apc { struct object *owner; struct { enum apc_type type; struct { unsigned int status; } async_io; } call; };
static const struct object_ops async_ops;
static int is_thread_suspended( struct thread *thread ) { return thread->suspended; }
CODE
/* stdin: one step per line: "set NAME VALUE", "unset NAME", or "call S" (S = 1: the APC belongs to a synchronous request of the busy thread) */
int main(void)
{
    struct process p; struct thread busy; struct thread_apc apc; struct async async; char line[256], name[64], value[64]; int sync;
    memset( &busy, 0, sizeof(busy) ); list_init( &p.thread_list ); busy.process = &p; busy.id = 0x1c0;
    list_add_tail( &p.thread_list, &busy.proc_entry );
    memset( &async, 0, sizeof(async) ); async.obj.ops = &async_ops; async.thread = &busy; async.blocking = 1; async.wait_handle = 0x44;
    memset( &apc, 0, sizeof(apc) ); apc.call.type = APC_ASYNC_IO; apc.call.async_io.status = 0x103;
    setvbuf( stderr, NULL, _IONBF, 0 );
    while (fgets( line, sizeof(line), stdin ))
    {
        if (sscanf( line, "set %63s %63s", name, value ) == 2) setenv( name, value, 1 );
        else if (sscanf( line, "unset %63s", name ) == 1) unsetenv( name );
        else if (sscanf( line, "call %d", &sync ) == 1)
        {
            struct thread *t;
            async.blocking = sync;
            apc.owner = &async.obj;
            t = ios_apc_requeue_target( &busy, &apc );
            fprintf( stderr, "ANSWER %s\n", t == &busy ? "kept" : t ? "?" : "dropped" );
        }
    }
    return 0;
}
'''

NAMES = ('MADEIRA_APC_REQUEUE', 'MADEIRA_APC_BUSY_QUEUE', 'MADEIRA_APC_DEFER_ISSUER')


def model(env, sync):
    """what the server must answer for a completion nobody waits for, from the variables as they are now"""
    if (env.get('MADEIRA_APC_REQUEUE') or '')[:1] == '0':
        return 'dropped'
    if env.get('MADEIRA_APC_DEFER_ISSUER') == '1' and sync:
        return 'kept'
    if (env.get('MADEIRA_APC_BUSY_QUEUE') or '')[:1] == '1':
        return 'kept'
    return 'dropped'


if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    async_c = (wine / 'server/async.c').read_text()
    pred = function(async_c, 'int async_can_defer_to_issuer( struct object *obj, struct thread *thread )\n{')
    req = function(thread_c, 'static int ios_apc_requeue_enabled( void )\n{')
    defer = function(thread_c, 'static int ios_apc_defer_issuer_enabled( void )\n{')
    target = function(thread_c, 'static struct thread *ios_apc_requeue_target( struct thread *thread, const struct thread_apc *apc )')
    check('the four server functions and the predicate are in the Wine source', all((pred, req, busy, defer, target)))
    for name, f in (('MADEIRA_APC_REQUEUE', req), ('MADEIRA_APC_BUSY_QUEUE', busy), ('MADEIRA_APC_DEFER_ISSUER', defer)):
        check('%s is read with getenv on every call; the only static is what the log said last' % name,
              f.count('getenv( "%s" )' % name) == 1 and 'static int enabled' not in f and 'if (enabled < 0)' not in f
              and f.count('    static int ') == 1 and '    static int said' in f and f.rstrip().endswith('return enabled;\n}'))
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='apc-per-session-'))
    try:
        code = 'static int ios_apc_busy_queue_enabled( void );\n\n' + pred + '\n' + req + '\n' + busy + '\n' + defer + '\n' + target
        (tmp / 't.c').write_text(HARNESS.replace('CODE', code))
        r = subprocess.run([cc, '-std=gnu11', '-O1', '-Wall', '-Wno-unused-function', '-Wno-unused-const-variable', '-Werror',
                            '-I' + str(wine / 'include'), '-o', str(tmp / 't'), str(tmp / 't.c')], capture_output=True, text=True)
        check('the functions compile on the host without a warning', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            def run(steps):
                e = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
                r = subprocess.run([str(tmp / 't')], input='\n'.join(steps) + '\n', env=e, capture_output=True, text=True)
                return r.stderr.splitlines()

            def play(steps):
                """the answers the process gave, and what the model says for the same steps"""
                env, want = {}, []
                for s in steps:
                    w = s.split()
                    if w[0] == 'set':
                        env[w[1]] = w[2]
                    elif w[0] == 'unset':
                        env.pop(w[1], None)
                    else:
                        want.append(model(env, int(w[1])))
                lines = run(steps)
                return [l.split()[1] for l in lines if l.startswith('ANSWER ')], want, lines

            # the scenarios of the docstring
            got, want, lines = play(['set MADEIRA_APC_BUSY_QUEUE 1', 'call 0', 'call 0', 'unset MADEIRA_APC_BUSY_QUEUE', 'call 0', 'call 0'])
            check('a session with MADEIRA_APC_BUSY_QUEUE = 1, then one without it in the same process: kept, kept, then dropped, dropped',
                  got == want == ['kept', 'kept', 'dropped', 'dropped'], str(got))
            check('the log says so once each way: the old "on" line, and one line when it is off again',
                  sum('MADEIRA_APC_BUSY_QUEUE=1: an async I/O APC nobody else can take' in l for l in lines) == 1
                  and sum('MADEIRA_APC_BUSY_QUEUE is no longer 1' in l for l in lines) == 1, '\n     '.join(lines))
            got, want, lines = play(['call 0', 'call 1', 'set MADEIRA_APC_BUSY_QUEUE 1', 'call 0'])
            check('a game that sets it after another game\'s session gets it: dropped, dropped, then kept', got == want == ['dropped', 'dropped', 'kept'], str(got))
            got, want, lines = play(['set MADEIRA_APC_DEFER_ISSUER 1', 'call 1', 'call 0', 'unset MADEIRA_APC_DEFER_ISSUER', 'call 1', 'set MADEIRA_APC_DEFER_ISSUER 1', 'call 1'])
            check('MADEIRA_APC_DEFER_ISSUER follows the same way: kept (synchronous), dropped (overlapped), dropped once cleared, kept when set again',
                  got == want == ['kept', 'dropped', 'dropped', 'kept'], str(got))
            check('and is said twice on, once off', sum('MADEIRA_APC_DEFER_ISSUER=1:' in l for l in lines) == 2
                  and sum('MADEIRA_APC_DEFER_ISSUER is no longer 1' in l for l in lines) == 1, '\n     '.join(lines))
            got, want, lines = play(['set MADEIRA_APC_BUSY_QUEUE 1', 'set MADEIRA_APC_REQUEUE 0', 'call 0', 'unset MADEIRA_APC_REQUEUE', 'call 0', 'call 0'])
            check('MADEIRA_APC_REQUEUE = 0 in one session does not stay for the next: dropped, then kept, kept', got == want == ['dropped', 'kept', 'kept'], str(got))
            check('its line is written at the first read and at the change, not at every call',
                  [l for l in lines if l.startswith('[apc-requeue] o')] == [
                      '[apc-requeue] off (MADEIRA_APC_REQUEUE=0 restores dropping async I/O APCs for busy threads)',
                      '[apc-requeue] on (MADEIRA_APC_REQUEUE=0 restores dropping async I/O APCs for busy threads)'], '\n     '.join(lines))
            got, want, lines = play(['call 0'] * 5)
            check('a process that never sets anything says its one old line and nothing else',
                  got == ['dropped'] * 5 and [l for l in lines if l.startswith('[apc-requeue] ') and ' #' not in l]
                  == ['[apc-requeue] on (MADEIRA_APC_REQUEUE=0 restores dropping async I/O APCs for busy threads)'], '\n     '.join(lines))
            # 2,000 random steps in one process
            rnd = random.Random(660)
            steps = []
            for _ in range(2000):
                k = rnd.random()
                if k < 0.5:
                    steps.append('call %d' % rnd.randrange(2))
                elif k < 0.75:
                    steps.append('set %s %s' % (rnd.choice(NAMES), rnd.choice(['1', '0', '1', 'yes', '10'])))
                else:
                    steps.append('unset ' + rnd.choice(NAMES))
            got, want, lines = play(steps)
            diff = [i for i, (a, b) in enumerate(zip(got, want)) if a != b]
            check('2,000 random steps in one process (%d calls, %d of them kept): every answer is the one the variables give at that moment'
                  % (len(want), want.count('kept')), got == want and 200 < want.count('kept') < len(want) - 200, 'first difference at call %s' % diff[:1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
check('the app clears MADEIRA_APC_BUSY_QUEUE and MADEIRA_APC_DEFER_ISSUER at the end of a session',
      'unsetenv("MADEIRA_APC_BUSY_QUEUE");' in bridge and 'unsetenv("MADEIRA_APC_DEFER_ISSUER");' in bridge)
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-apc-switches-per-session.py' in ci)

print('PASS: the server\'s completion switches follow the session' if ok else 'FAILED: the server\'s completion switches follow the session')
sys.exit(0 if ok else 1)
