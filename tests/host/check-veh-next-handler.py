#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright the Wine authors (LGPL-2.1-or-later): this test compiles add_vectored_handler, remove_vectored_handler and
# call_vectored_handlers cut from Wine's dlls/ntdll/exception.c at run time; none of their text is in this file. BCD's terms above do not
# apply to those lines.
"""The exception dispatcher no longer calls a vectored handler that was removed and freed while the one before it ran
(tools/patch-wine-veh-next-handler.py: Wine commit da00e5f5cc9, written into the 32-bit ntdll at build time; build 660).

Wine 11.4's call_vectored_handlers reads the pointer to the next list entry before it leaves the list's lock for the handler
call and uses it afterwards. A handler that removes the handler after it (or another thread that does) leaves that pointer
on a freed entry: the dispatcher calls the freed handler, unlinks it a second time and frees it a second time. The patch
reads the next entry after the handler returned, under the lock.

Checks (the Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none):
  1. the script: it changes the text, a second run changes nothing, undoing gives the text back, a moved anchor fails; with
     tools/patch-wine-veh-add-log.py (the other script on this file) the result is the same in either order; the patched
     function has one `entry = entry->Flink;`, between taking the lock again and the count test;
  2. the three real functions (the unpatched and the patched call_vectored_handlers side by side, the real add and remove),
     compiled with -Wall -Wextra -Werror under ASan + UBSan with a heap stand-in that keeps freed blocks readable and
     remembers them, against a model written here, over every script of three handlers that each do one of seven things on
     their call (nothing, remove the first, second or third handler, add a fourth at the end or at the front, answer
     "continue execution") and over 3,000 random scripts of up to five handlers with up to two actions each:
       - patched: the calls, their order, the final list and the frees are the model's in every script; no handler runs
         after it was freed, nothing is freed twice, the list is never touched without the lock, a handler never runs
         under it;
       - unpatched: the same as patched in every script where no handler removes the one right after itself and none
         adds one at the end while it is the last; where one removes the handler right after itself, the freed handler
         is called and freed twice (the defect, shown, not assumed); where the last one adds a handler at the end, the
         new handler is not called in that dispatch (it is with the patch, as in Wine before the regression of
         commit 4749bd2bb74 and since da00e5f5cc9);
  3. the patched file compiles for i686 with llvm-mingw and generated headers (the full tree; the CI runs this);
  4. the build: the script runs in an "i386 farm only" step before the farm is built, dlls/ntdll/exception.c is in the
     put-back step after it, and CI runs this check.
NOT covered: two real threads (the same pointer is used after the same unlock; the single-thread case needs no timing); the
64-bit ntdll.dll, which is a committed binary and keeps the old order.
"""
from pathlib import Path
import importlib.util
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
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)[:1500]) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), root / 'tools' / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


veh = load('patch-wine-veh-next-handler')
wine = None
for candidate in (os.environ.get('BCD_WINE_SRC'), root / 'wine', Path.home() / 'madeira-bcd/wine'):
    if candidate and (Path(candidate) / 'dlls/ntdll/exception.c').is_file():
        wine = Path(candidate)
        break
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')


def cut(text, start, end='\n}\n'):
    at = text.index(start)
    return text[at:text.index(end, at) + len(end)]


HARNESS_HEAD = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int32_t LONG; typedef uint32_t ULONG, DWORD; typedef uintptr_t ULONG_PTR; typedef void *HANDLE; typedef int BOOL;
#define TRUE 1
#define FALSE 0
#define WINAPI
#define EXCEPTION_CONTINUE_SEARCH 0
#define EXCEPTION_CONTINUE_EXECUTION (-1)
typedef struct _LIST_ENTRY { struct _LIST_ENTRY *Flink, *Blink; } LIST_ENTRY, *PLIST_ENTRY;
typedef struct { DWORD ExceptionCode, ExceptionFlags; } EXCEPTION_RECORD;
typedef struct { int unused; } CONTEXT;
typedef struct { EXCEPTION_RECORD *ExceptionRecord; CONTEXT *ContextRecord; } EXCEPTION_POINTERS;
typedef LONG (*PVECTORED_EXCEPTION_HANDLER)( EXCEPTION_POINTERS * );
#define CONTAINING_RECORD(a, t, f) ((t *)((char *)(a) - offsetof(t, f)))
#define TRACE(...) do { } while (0)
#define ERR(...) do { } while (0)

/* what went wrong, as bits */
enum { CALLED_FREED = 1, DOUBLE_FREE = 2, LIST_WITHOUT_LOCK = 4, HANDLER_UNDER_LOCK = 8, UNBALANCED_LOCK = 16 };
static int flags, lock_depth;
typedef struct { int unused; } RTL_CRITICAL_SECTION;
static RTL_CRITICAL_SECTION vectored_handlers_section;
static void RtlEnterCriticalSection( RTL_CRITICAL_SECTION *cs ) { (void)cs; lock_depth++; }
static void RtlLeaveCriticalSection( RTL_CRITICAL_SECTION *cs ) { (void)cs; if (--lock_depth < 0) flags |= UNBALANCED_LOCK; }
static void InsertHeadList( LIST_ENTRY *l, LIST_ENTRY *e )
{ if (!lock_depth) flags |= LIST_WITHOUT_LOCK; e->Flink = l->Flink; e->Blink = l; l->Flink->Blink = e; l->Flink = e; }
static void InsertTailList( LIST_ENTRY *l, LIST_ENTRY *e )
{ if (!lock_depth) flags |= LIST_WITHOUT_LOCK; e->Flink = l; e->Blink = l->Blink; l->Blink->Flink = e; l->Blink = e; }
static void RemoveEntryList( LIST_ENTRY *e )
{ if (!lock_depth) flags |= LIST_WITHOUT_LOCK; e->Flink->Blink = e->Blink; e->Blink->Flink = e->Flink; }

/* the heap: a freed block stays readable (so the unpatched function can be watched instead of crashing) and is remembered */
#define MAX_BLOCKS 64
static struct { void *p; int freed; } blocks[MAX_BLOCKS];
static int n_blocks, n_frees;
static HANDLE GetProcessHeap( void ) { return NULL; }
static void *RtlAllocateHeap( HANDLE heap, ULONG fl, size_t size )
{ void *p = calloc( 1, size ); (void)heap; (void)fl; if (n_blocks == MAX_BLOCKS) abort(); blocks[n_blocks].p = p; blocks[n_blocks++].freed = 0; return p; }
static int block_freed( const void *p ) { int i; for (i = 0; i < n_blocks; i++) if (blocks[i].p == p) return blocks[i].freed; return -1; }
static BOOL RtlFreeHeap( HANDLE heap, ULONG fl, void *p )
{
    int i;
    (void)heap; (void)fl;
    if (!p) return TRUE;
    for (i = 0; i < n_blocks; i++) if (blocks[i].p == p) { if (blocks[i].freed) flags |= DOUBLE_FREE; blocks[i].freed = 1; n_frees++; return TRUE; }
    abort();
}
static void *RtlEncodePointer( void *p ) { return (void *)((uintptr_t)p ^ 0x5a5a5a5a); }
static void *RtlDecodePointer( void *p ) { return (void *)((uintptr_t)p ^ 0x5a5a5a5a); }
"""

HARNESS_TAIL = r"""
/* one script: "<n handlers> <actions of handler 0>,<actions of handler 1>,..." where actions are letters:
 * '-' nothing, 'a'..'e' remove handler 0..4, 'T' add the spare handler at the end, 'F' add it at the front, 'X' answer continue execution */
#define MAX_H 6
static VECTORED_HANDLER *reg[MAX_H];    /* registered now (the program's own view) */
static VECTORED_HANDLER *made[MAX_H];   /* the entry each handler got when it was added */
static char acts[MAX_H][8];
static int done[MAX_H];
static char called[64]; static int n_called;

static LONG run( int k, EXCEPTION_POINTERS *ptrs );
#define STUB(k) static LONG h##k( EXCEPTION_POINTERS *p ) { return run( k, p ); }
STUB(0) STUB(1) STUB(2) STUB(3) STUB(4) STUB(5)
static const PVECTORED_EXCEPTION_HANDLER stubs[MAX_H] = { h0, h1, h2, h3, h4, h5 };

static LONG run( int k, EXCEPTION_POINTERS *ptrs )
{
    LONG ret = EXCEPTION_CONTINUE_SEARCH;
    const char *a;

    (void)ptrs;
    if (lock_depth) flags |= HANDLER_UNDER_LOCK;
    if (block_freed( made[k] ) != 0) flags |= CALLED_FREED;   /* its entry was already freed when the dispatcher called it */
    if (n_called < 63) called[n_called++] = '0' + k;
    if (done[k]) return ret;
    done[k] = 1;
    for (a = acts[k]; *a; a++)
    {
        if (*a >= 'a' && *a <= 'e')
        {
            int j = *a - 'a';
            if (reg[j]) { VECTORED_HANDLER *h = reg[j]; reg[j] = NULL; remove_vectored_handler( &vectored_exception_handlers, h ); }
        }
        else if (*a == 'T' || *a == 'F')
        {
            if (!made[MAX_H - 1]) made[MAX_H - 1] = reg[MAX_H - 1] = add_vectored_handler( &vectored_exception_handlers, *a == 'F', stubs[MAX_H - 1] );
        }
        else if (*a == 'X') ret = EXCEPTION_CONTINUE_EXECUTION;
    }
    return ret;
}

int main( void )
{
    char line[256];

    while (fgets( line, sizeof(line), stdin ))
    {
        int which, n, i, k = 0, pos = 0, list_ok = 1;
        char order[64]; int n_order = 0;
        LIST_ENTRY *e;
        EXCEPTION_RECORD rec = { 0xc0000005, 0 };
        CONTEXT ctx = { 0 };
        LONG ret;

        if (sscanf( line, "%d %d %n", &which, &n, &pos ) < 2) continue;
        vectored_exception_handlers.Flink = vectored_exception_handlers.Blink = &vectored_exception_handlers;
        for (i = 0; i < n_blocks; i++) free( blocks[i].p );
        memset( reg, 0, sizeof(reg) ); memset( made, 0, sizeof(made) ); memset( acts, 0, sizeof(acts) ); memset( done, 0, sizeof(done) );
        flags = lock_depth = n_called = n_blocks = n_frees = 0;
        for (i = 0; line[pos + i] && line[pos + i] != '\n'; i++)
        {
            if (line[pos + i] == ',') { k++; continue; }
            if (line[pos + i] != '-' && strlen( acts[k] ) < 7) acts[k][strlen( acts[k] )] = line[pos + i];
        }
        for (i = 0; i < n; i++) made[i] = reg[i] = add_vectored_handler( &vectored_exception_handlers, 0, stubs[i] );
        ret = which ? call_vectored_handlers_new( &rec, &ctx ) : call_vectored_handlers_old( &rec, &ctx );
        if (lock_depth) flags |= UNBALANCED_LOCK;
        /* the list afterwards: every entry a live registered handler, links consistent */
        for (e = vectored_exception_handlers.Flink; e != &vectored_exception_handlers && n_order < 60; e = e->Flink)
        {
            if (e->Flink->Blink != e || e->Blink->Flink != e) list_ok = 0;
            for (i = 0; i < MAX_H; i++) if (reg[i] && &reg[i]->entry == e) break;
            if (i == MAX_H || block_freed( reg[i] ) != 0) { list_ok = 0; order[n_order++] = '?'; } else order[n_order++] = '0' + i;
        }
        called[n_called] = 0; order[n_order] = 0;
        printf( "%s|%s|%d|%d|%d|%d\n", called, order, (int)ret, flags, n_frees, list_ok );
    }
    return 0;
}
"""


def model(n, actions, new):
    """What one dispatch does: (calls, final list, answer, frees). new=False reads the next entry before the handler runs, as Wine 11.4
    does; it returns None when that entry is freed during the call and the walk goes on (the use after free: no model for the rest)."""
    lst = list(range(n))
    count = {i: 1 for i in lst}
    registered, done, spare, spare_added = set(lst), set(), 5, False
    calls, frees, ret = '', 0, 0
    cur = lst[0] if lst else None
    while cur is not None:
        at = lst.index(cur)
        early = lst[at + 1] if at + 1 < len(lst) else None
        early_freed = False
        count[cur] += 1
        calls += str(cur)
        ret = 0
        if cur not in done:
            done.add(cur)
            for a in (actions[cur] if cur < len(actions) else ''):
                if 'a' <= a <= 'e':
                    j = ord(a) - ord('a')
                    if j in registered:
                        registered.discard(j)
                        count[j] -= 1
                        if not count[j]:
                            lst.remove(j)
                            frees += 1
                            early_freed = early_freed or j == early
                elif a in 'TF':
                    if not spare_added:
                        spare_added = True
                        count[spare] = 1
                        registered.add(spare)
                        lst.insert(0 if a == 'F' else len(lst), spare)
                elif a == 'X':
                    ret = -1
        at = lst.index(cur)
        late = lst[at + 1] if at + 1 < len(lst) else None
        count[cur] -= 1
        if not count[cur]:
            lst.remove(cur)
            frees += 1
        if ret == -1:
            break
        if not new and early_freed:
            return None
        cur = late if new else early
    return calls, ''.join(str(i) for i in lst), ret, frees


def run_model(n, actions, new):
    return model(n, actions, new)


if wine:
    text = (wine / 'dlls/ntdll/exception.c').read_text()
    stock = veh.unpatch_text(text)
    patched = veh.patch_text(stock)

    # ---------------------------------------------------------------- 1. the script
    check('the script changes exception.c and adds its marker', patched != stock and veh.MARKER in patched and veh.MARKER not in stock)
    check('a second run changes nothing', veh.patch_text(patched) == patched)
    check('undoing the edits gives back the text before the patch', veh.unpatch_text(patched) == stock)
    for name, anchor in (('early', veh.EARLY_OLD), ('late', veh.LATE_OLD)):
        try:
            veh.patch_text(stock.replace(anchor, anchor.replace('( ', '(  ', 1)))
            check('a moved %s anchor fails' % name, False)
        except SystemExit:
            check('a moved %s anchor fails' % name, True)
    fn_old, fn_new = cut(stock, veh.HEAD), cut(patched, veh.HEAD)
    relock = fn_new.rindex('RtlEnterCriticalSection( &vectored_handlers_section );')
    check('the patched function reads the next entry once, after it took the lock again and before the count test',
          fn_new.count('entry = entry->Flink;') == 1
          and relock < fn_new.index('entry = entry->Flink;') < fn_new.index('if (!--*handler->count)')
          and fn_new.index('ret = func( &except_ptrs );') < relock)
    comment = [l for l in veh.LATE_NEW.split('\n') if l not in veh.LATE_OLD.split('\n') and 'entry = entry->Flink;' not in l]
    lines_old, lines_new = fn_old.split('\n'), fn_new.split('\n')
    for l in comment:
        lines_new.remove(l)
    check('apart from the comment the patch only moves that one line (Wine commit da00e5f5cc9 is that move)',
          sorted(lines_old) == sorted(lines_new) and lines_old != lines_new)
    check('the header names Wine\'s commit and author', 'Paul Gofman' in (root / 'tools/patch-wine-veh-next-handler.py').read_text().split('"""')[0]
          and 'da00e5f5cc9' in (root / 'tools/patch-wine-veh-next-handler.py').read_text().split('"""')[0])

    add_log = load('patch-wine-veh-add-log')
    tmp = Path(tempfile.mkdtemp(prefix='veh-next-handler-order-'))
    try:
        results = []
        if add_log.MARKER in stock:
            print('note: this tree already has tools/patch-wine-veh-add-log.py applied; the two orders below start from that text')
        for order in ('log-first', 'next-first'):
            t = tmp / order
            (t / 'dlls/ntdll').mkdir(parents=True)
            (t / 'dlls/kernelbase').mkdir(parents=True)
            (t / 'dlls/ntdll/exception.c').write_text(stock)
            shutil.copy(wine / 'dlls/kernelbase/debug.c', t / 'dlls/kernelbase/debug.c')
            steps = ['patch-wine-veh-add-log.py', 'patch-wine-veh-next-handler.py']
            if order == 'next-first':
                steps.reverse()
            rc = [subprocess.run([sys.executable, str(root / 'tools' / s), str(t)], capture_output=True, text=True) for s in steps * 2]
            results.append((all(r.returncode == 0 for r in rc), (t / 'dlls/ntdll/exception.c').read_text()))
        check('with tools/patch-wine-veh-add-log.py (the other script on this file): both apply, twice, in either order, to the same text',
              results[0][0] and results[1][0] and results[0][1] == results[1][1] and veh.MARKER in results[0][1],
              'log-first ok=%s next-first ok=%s' % (results[0][0], results[1][0]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------------- 2. the real functions against the model
    cc = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
    typedef = cut(stock, 'typedef struct\n{\n    LIST_ENTRY                   entry;', '} VECTORED_HANDLER;\n')
    heads = ('static LIST_ENTRY vectored_exception_handlers = { &vectored_exception_handlers, &vectored_exception_handlers };\n')
    check('the list head is where the test expects it', heads in stock)
    add_fn = cut(stock, 'static VECTORED_HANDLER *add_vectored_handler( LIST_ENTRY *handler_list, ULONG first,')
    rem_fn = cut(stock, 'static ULONG remove_vectored_handler( LIST_ENTRY *handler_list, VECTORED_HANDLER *handler )')
    src = (HARNESS_HEAD + '#include <stddef.h>\n' + typedef + heads + add_fn + rem_fn
           + fn_old.replace('call_vectored_handlers(', 'call_vectored_handlers_old(')
           + fn_new.replace('call_vectored_handlers(', 'call_vectored_handlers_new(') + HARNESS_TAIL)
    tmp = Path(tempfile.mkdtemp(prefix='veh-next-handler-'))
    try:
        (tmp / 't.c').write_text(src)
        r = subprocess.run([cc, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-fno-sanitize-recover=undefined', '-o', str(tmp / 't'), str(tmp / 't.c')], capture_output=True, text=True)
        check('the real add, remove and both dispatch functions compile with -Wall -Wextra -Werror under ASan + UBSan', r.returncode == 0, r.stderr)
        if r.returncode == 0:
            scripts = []
            acts7 = ['-', 'a', 'b', 'c', 'T', 'F', 'X']
            for a in acts7:
                for b in acts7:
                    for c in acts7:
                        scripts.append((3, [a, b, c]))
            n_exhaustive = len(scripts)
            rng = random.Random(660)
            for _ in range(3000):
                n = rng.randint(1, 5)
                scripts.append((n, [''.join(rng.choice('-abcdeTFX') for _ in range(rng.randint(1, 2))) for _ in range(n)]))
            feed = ''.join('%d %d %s\n' % (which, n, ','.join(a)) for n, a in scripts for which in (0, 1))
            p = subprocess.run([str(tmp / 't')], input=feed, capture_output=True, text=True, timeout=120)
            out = p.stdout.split('\n')
            check('the harness ran every script (%d, of which %d are every combination of three handlers)' % (len(scripts), n_exhaustive),
                  p.returncode == 0 and len(out) == 2 * len(scripts) + 1, p.stderr[-800:])
            if p.returncode == 0 and len(out) == 2 * len(scripts) + 1:
                bad_new, bad_old, uaf, uaf_shown, tail_adds, same = [], [], 0, 0, 0, 0
                for i, (n, a) in enumerate(scripts):
                    acts = [x.replace('-', '') for x in a]
                    got_old, got_new = out[2 * i].split('|'), out[2 * i + 1].split('|')
                    m_new, m_old = run_model(n, acts, True), run_model(n, acts, False)
                    want_new = [m_new[0], m_new[1], str(m_new[2]), '0', str(m_new[3]), '1']
                    if got_new != want_new:
                        bad_new.append((n, a, got_new, want_new))
                    if m_old is None:
                        uaf += 1
                        if int(got_old[3]) & 1 and int(got_old[3]) & 2:
                            uaf_shown += 1
                        else:
                            bad_old.append((n, a, got_old, 'expected: freed handler called, freed twice'))
                    else:
                        want_old = [m_old[0], m_old[1], str(m_old[2]), '0', str(m_old[3]), '1']
                        if got_old != want_old:
                            bad_old.append((n, a, got_old, want_old))
                        if m_old == m_new:
                            same += 1
                        else:
                            tail_adds += 1
                            if not (m_new[0].startswith(m_old[0]) and m_new[0][len(m_old[0]):] == '5'):
                                bad_old.append((n, a, got_old, 'the only benign difference should be the handler added at the end'))
                check('patched: calls, order, final list, answer and frees are the model\'s in all %d scripts; no handler runs after its entry '
                      'was freed, nothing is freed twice, the list is only touched under the lock, no handler runs under it' % len(scripts),
                      not bad_new, bad_new[:3])
                check('unpatched: the same as patched in %d scripts; in %d, where the last handler adds one at the end, the new handler is not '
                      'called in that dispatch (patched: it is, as before Wine\'s regression)' % (same, tail_adds),
                      not [b for b in bad_old if 'freed handler' not in str(b[3])] and same > 0 and tail_adds > 0, bad_old[:3])
                check('unpatched: in each of the %d scripts where a handler removes the one right after it, the dispatcher calls the freed '
                      'handler and frees it a second time; patched: never (above)' % uaf, uaf > 0 and uaf_shown == uaf, bad_old[:3])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------------- 3. the whole file for i686
    tc = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
    gen = next((g for g in (wine / 'build-i386/include', wine / 'build-macos/include') if g.is_dir()), None)
    if (tc / 'i686-w64-mingw32-clang').exists() and gen:
        tmp = Path(tempfile.mkdtemp(prefix='veh-next-handler-pe-'))
        try:
            (tmp / 'exception.c').write_text(patched)
            r = subprocess.run([str(tc / 'i686-w64-mingw32-clang'), '-fsyntax-only', '-O2', '-Wall', '-Wdeclaration-after-statement', '-D__WINESRC__',
                                '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-D__WINE_PE_BUILD', '-D_MSVCR_VER=0',
                                '-I' + str(wine / 'dlls/ntdll'), '-I' + str(gen), '-I' + str(wine / 'include'), '-I' + str(wine / 'include/msvcrt'),
                                str(tmp / 'exception.c')], capture_output=True, text=True)
            check('the patched ntdll/exception.c compiles for i686', r.returncode == 0 and ' error' not in r.stderr, r.stderr[-1500:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        print('note: no llvm-mingw or generated headers in this tree; the compile runs in the full tree')

# ---------------------------------------------------------------- 4. the build
ci = (root / '.github/workflows/build-ipa.yml').read_text()
step = 'python3 tools/patch-wine-veh-next-handler.py wine'
farm, back = ci.find('- name: Build i386 farm (WoW64)'), ci.find('- name: Put back the files patched for the i386 farm only')
check('the build applies the script before the i386 farm is built, in a step named "(i386 farm only)"',
      step in ci and 0 < ci.index(step) < farm and '(i386 farm only)' in ci[ci.rindex('- name:', 0, ci.index(step)):ci.index(step)])
check('dlls/ntdll/exception.c is put back after the farm (the 64-bit ntdll proof compiles the unpatched file)',
      0 < farm < back and 'dlls/ntdll/exception.c' in ci[back:ci.index('\n\n', back)])
check('CI runs this check before it applies the script', 'python3 tests/host/check-veh-next-handler.py' in ci
      and ci.index('python3 tests/host/check-veh-next-handler.py') < ci.index(step))
check('the notices name the script and Wine\'s commit', 'patch-wine-veh-next-handler.py' in (root / 'THIRD-PARTY-NOTICES.md').read_text()
      and 'da00e5f5cc9' in (root / 'THIRD-PARTY-NOTICES.md').read_text())

print('PASS: vectored handler list, next entry' if ok else 'FAILED: vectored handler list, next entry')
sys.exit(0 if ok else 1)
