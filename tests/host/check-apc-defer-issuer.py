#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_APC_DEFER_ISSUER: the completion of a synchronous request stays on the thread that issued it; no Wine runs (build 660).

BCD's own Windows server cannot interrupt a running thread on iOS. An async I/O completion (a system APC) for a thread that is running is
given to another thread of its process that waits in the server; when there is none it is dropped ("[apc-requeue] ... dropped, no
waiting thread") and the request ends with the APC's own status and no data.

Madeira's Wine fork (commit 243c626d56f, "server: retain blocking I/O completions on the issuing thread", intraducine) keeps one kind of
these on its thread instead: the completion of a pending SYNCHRONOUS request that has a wait handle. The thread that issued such a
request always enters wait_async() on that handle after the request's reply, and that wait is a server wait, which runs system APCs.
Madeira does it in every session. BCD takes the code behind a switch that is off: the "dropped" line is in 1,962 of the 3,881 log files of
2026-10-20 to 2026-11-10 (~/madeira-local/intake), working games among them, so the path every one of those sessions takes today is left
as it is. No BCD log shows a failure this cures, so there is no auto-tune rule; the switch is for a program that needs it.

Held here (the Wine source is wine/ of this checkout, $BCD_WINE_SRC, or ~/madeira-bcd/wine; the Wine parts are skipped when none is there
or when the one that is there does not carry the change and is not the commit this checkout pins):
  1. The predicate async_can_defer_to_issuer (server/async.c, WINE_IOS only, declared in file.h) is Madeira's: an async object, issued by
     this thread, blocking, with a wait handle.
  2. The switch reads only MADEIRA_APC_DEFER_ISSUER, on every call (the server runs again in the same process for the next session of an
     app run, so nothing is kept from an earlier game; tests/host/check-apc-switches-per-session.py flips it inside one process), and
     only the exact value 1 turns it on; in ios_apc_requeue_target it comes after the search for a waiting thread and before
     MADEIRA_APC_BUSY_QUEUE.
  3. The real functions (server/thread.c's switches and ios_apc_requeue_target, async.c's predicate) compiled on the host against stand-in
     types and driven over every combination of the switch (unset, 0, empty, yes, 10, 1x, 1), MADEIRA_APC_BUSY_QUEUE (unset, 1), the APC's owner
     (none, not an async, an async of another thread, an overlapped async, a synchronous one whose wait handle is gone, a synchronous one
     with its handle), another thread (none, waiting, running), and the in-process engine (off, on), against a model written here: with
     the switch off the answer is the one without this change in every case; with it on only "no waiting thread, a synchronous request of
     this thread with its wait handle" changes, to the issuing thread, and the log line says "issuing thread".
  4. The settings catalog knows the key, the app clears it at the end of a session, a downloaded list may set it, and the CI runs this.
Needs python3 and a C compiler.
"""
from pathlib import Path
import itertools
import os
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
has_change = 'ios_apc_defer_issuer_enabled' in thread_c
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    async_c = (wine / 'server/async.c').read_text()
    file_h = (wine / 'server/file.h').read_text()

    # ------------------------------------------------------------ 1. the predicate
    pred = function(async_c, 'int async_can_defer_to_issuer( struct object *obj, struct thread *thread )\n{')
    check('the predicate is Madeira\'s: an async object, of this thread, blocking, with a wait handle',
          'return obj && obj->ops == &async_ops && async->thread == thread &&\n           async->blocking && async->wait_handle;' in pred)
    before = async_c[:async_c.index('int async_can_defer_to_issuer(')]
    check('it is compiled for iOS only and declared in file.h under the same guard',
          before.rstrip().endswith('*/') and '#ifdef WINE_IOS\n/* A pending synchronous request' in before[-900:]
          and '#ifdef WINE_IOS\nextern int async_can_defer_to_issuer( struct object *obj, struct thread *thread );\n#endif\n' in file_h)
    check('a synchronous request is "blocking" exactly when its handle is not overlapped, and has a wait handle while it is pending',
          'async->blocking      = !is_fd_overlapped( fd );' in async_c
          and 'if (async->initial_status == STATUS_PENDING && async->blocking)' in async_c)

    # ------------------------------------------------------------ 2. the switch, in the source
    defer = function(thread_c, 'static int ios_apc_defer_issuer_enabled( void )\n{')
    busy = function(thread_c, 'static int ios_apc_busy_queue_enabled( void )\n{')
    req = function(thread_c, 'static int ios_apc_requeue_enabled( void )\n{')
    target = function(thread_c, 'static struct thread *ios_apc_requeue_target( struct thread *thread, const struct thread_apc *apc )')
    check('the predicate and the four server functions are in the Wine source', all((pred, defer, busy, req, target)))
    check('the switch is MADEIRA_APC_DEFER_ISSUER, read where it is used (no answer kept from an earlier session), and only the exact value 1 turns it on',
          'getenv( "MADEIRA_APC_DEFER_ISSUER" )' in defer and "int enabled = e && e[0] == '1' && !e[1];" in defer and 'static int enabled' not in defer
          and defer.count('getenv(') == 1)
    line = 'if (!target && ios_apc_defer_issuer_enabled() && async_can_defer_to_issuer( apc->owner, thread ))'
    busy_line = 'if (!target && !thread->alert_sync && ios_apc_busy_queue_enabled()) target = thread;'
    check('it is asked only when no waiting thread was found, before the predicate, and before MADEIRA_APC_BUSY_QUEUE',
          line in target and busy_line in target
          and target.index('LIST_FOR_EACH_ENTRY') < target.index(line) < target.index(busy_line) < target.index('count++;'))
    check('a disabled requeue, or an APC that is not an async I/O completion, returns before the switch is looked at',
          target.index('if (apc->call.type != APC_ASYNC_IO || !ios_apc_requeue_enabled()) return NULL;') < target.index(line))
    check('the three old log texts are unchanged and "issuing thread" is the fourth',
          'const char *kept = "kept on the busy thread";' in target and 'kept = "issuing thread";' in target
          and '"waiting thread" : "dropped, no waiting thread"' in target and 'target == thread ? kept :' in target)

    # ------------------------------------------------------------ 3. the real functions, compiled, against a model
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
typedef unsigned int obj_handle_t;

struct object_ops { int unused; };
struct object { const struct object_ops *ops; };
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
struct async
{
    struct object obj;
    struct thread *thread;
    obj_handle_t wait_handle;
    unsigned int blocking :1;
};
struct thread_apc
{
    struct object *owner;
    struct { enum apc_type type; struct { unsigned int status; } async_io; } call;
};
static const struct object_ops async_ops, other_ops;
static int is_thread_suspended( struct thread *thread ) { return thread->suspended; }

#include "CODE"

/* argv: owner(0 none, 1 not an async, 2 async of another thread, 3 overlapped, 4 synchronous without wait handle, 5 synchronous with it)
 *       other-thread(0 none, 1 waiting, 2 running)  alert_sync(0/1)  apc-type(0 other, 1 async io) */
int main( int argc, char **argv )
{
    struct process p;
    struct thread busy, other;
    struct thread_wait w_int = { SELECT_INTERRUPTIBLE | SELECT_ALERTABLE };
    struct thread_apc apc;
    struct async async;
    struct object plain = { &other_ops };
    int owner = atoi( argv[1] ), mode = atoi( argv[2] ), madsync = atoi( argv[3] ), type = atoi( argv[4] ), i;

    memset( &busy, 0, sizeof(busy) ); memset( &other, 0, sizeof(other) );
    list_init( &p.thread_list );
    busy.process = &p; busy.id = 0x1c0; busy.alert_sync = madsync ? (void *)1 : NULL;
    list_add_tail( &p.thread_list, &busy.proc_entry );
    other.process = &p; other.id = 0x1c4;
    if (mode)
    {
        other.wait = mode == 2 ? NULL : &w_int;
        list_add_tail( &p.thread_list, &other.proc_entry );
    }
    memset( &async, 0, sizeof(async) );
    async.obj.ops = &async_ops;
    async.thread = owner == 2 ? &other : &busy;
    async.blocking = owner != 3;
    async.wait_handle = owner == 4 ? 0 : 0x44;
    memset( &apc, 0, sizeof(apc) );
    apc.owner = owner == 0 ? NULL : owner == 1 ? &plain : &async.obj;
    apc.call.type = type ? APC_ASYNC_IO : APC_USER;
    apc.call.async_io.status = 0x103;
    for (i = 0; i < 3; i++)
    {
        struct thread *t = ios_apc_requeue_target( &busy, &apc );
        printf( "%s\n", !t ? "none" : t == &busy ? "busy" : t == &other ? "other" : "?" );
    }
    return 0;
}
'''
    code = ('static int ios_apc_busy_queue_enabled( void );\n\n' + pred + '\n' + req + '\n' + busy + '\n' + defer + '\n' + target)
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='apc-defer-issuer-'))
    try:
        (tmp / 't.c').write_text(HARNESS.replace('#include "CODE"', code))
        r = subprocess.run([cc, '-std=gnu11', '-O1', '-Wall', '-Wno-unused-function', '-Wno-unused-const-variable', '-Werror',
                            '-I' + str(wine / 'include'), '-o', str(tmp / 't'), str(tmp / 't.c')], capture_output=True, text=True)
        check('the predicate, the three switches and ios_apc_requeue_target compile on the host without a warning', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            def run(env, args):
                e = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
                e.update(env)
                r = subprocess.run([str(tmp / 't')] + [str(a) for a in args], env=e, capture_output=True, text=True)
                return r.stdout.split(), r.stderr

            bad = []
            n = changed = 0
            for sw, bq, owner, mode, madsync, typ in itertools.product([None, '0', '', 'yes', '10', '1x', '1'], [None, '1'], range(6), range(3), (0, 1), (0, 1)):
                env = {}
                if sw is not None:
                    env['MADEIRA_APC_DEFER_ISSUER'] = sw
                if bq is not None:
                    env['MADEIRA_APC_BUSY_QUEUE'] = bq
                out, err = run(env, (owner, mode, madsync, typ))
                # the model: what the server did before this change ...
                if typ == 0:
                    old = 'none'
                elif mode == 1:
                    old = 'other'
                elif bq == '1' and not madsync:
                    old = 'busy'
                else:
                    old = 'none'
                # ... and the one case the switch changes
                defers = sw == '1' and typ == 1 and mode != 1 and owner == 5
                want = 'busy' if defers else old
                n += 1
                changed += defers and old == 'none'
                if out != [want] * 3:
                    bad.append((env, owner, mode, madsync, typ, out, want))
                if typ == 1:
                    said_issuer = '-> issuing thread 01c0' in err
                    if said_issuer != bool(defers):
                        bad.append(('the log line', env, owner, mode, madsync, typ, err[-300:]))
                    if not defers and old == 'busy' and '-> kept on the busy thread 01c0' not in err:
                        bad.append(('the busy-queue line', env, owner, mode, madsync, typ, err[-300:]))
                    if not defers and old == 'none' and 'dropped, no waiting thread' not in err:
                        bad.append(('the dropped line', env, owner, mode, madsync, typ, err[-300:]))
                if (sw != '1') and 'MADEIRA_APC_DEFER_ISSUER' in err:
                    bad.append(('announced while off', env, err[-300:]))
            check('%d combinations agree with the model: off (unset, 0, empty, yes, 10, 1x) is the server without this change; on (1), only a synchronous '
                  'request of the busy thread with its wait handle and no waiting thread is kept (%d cases turn a drop into "issuing thread")'
                  % (n, changed), not bad and changed > 0, str(bad[:3]))
            out, err = run({'MADEIRA_APC_DEFER_ISSUER': '1'}, (5, 0, 1, 1))
            check('with the switch on it also holds with the in-process engine (the issuer\'s next wait is the server\'s wait_async)',
                  out == ['busy'] * 3 and '[apc-requeue] MADEIRA_APC_DEFER_ISSUER=1:' in err and '-> issuing thread 01c0' in err, err[-400:])
            out, err = run({'MADEIRA_APC_DEFER_ISSUER': '1'}, (5, 1, 0, 1))
            check('a thread that waits in the server still comes first', out == ['other'] * 3 and 'waiting thread 01c4' in err, err[-400:])
            out, err = run({'MADEIRA_APC_DEFER_ISSUER': '1', 'MADEIRA_APC_REQUEUE': '0'}, (5, 0, 0, 1))
            check('MADEIRA_APC_REQUEUE=0 still drops everything', out == ['none'] * 3, err[-400:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 4. the BCD side
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the settings catalog knows the key (a bool, off by default, read in wine/server/thread.c)',
      'ConfigOption(key: "env.MADEIRA_APC_DEFER_ISSUER", title: "An answer to a waiting request stays with its thread", kind: .bool, defaultValue: "0"' in catalog
      and 'sources: ["wine/server/thread.c"]' in catalog.split('env.MADEIRA_APC_DEFER_ISSUER')[1].split('\n')[0])
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
check('a game\'s own line ends with its session (the bridge\'s unsetenv list)', 'unsetenv("MADEIRA_APC_DEFER_ISSUER");' in bridge)
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('a downloaded list may set it (allowedEnvNames), 0 or 1',
      '"MADEIRA_APC_DEFER_ISSUER"' in policy.split('static let allowedEnvNames')[1].split(']\n')[0] and 'MADEIRA_APC_DEFER_ISSUER is 0 or 1' in policy)
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-apc-defer-issuer.py' in ci)

print('PASS: MADEIRA_APC_DEFER_ISSUER' if ok else 'FAILED: MADEIRA_APC_DEFER_ISSUER')
sys.exit(0 if ok else 1)
