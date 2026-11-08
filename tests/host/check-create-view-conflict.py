#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""create_view no longer asserts on a non-system overlap (build 591); no Wine runs.

Fallout 4 (C5Y6PW, build 580), 89 s in, while FEX started a thread and asked for 16 MB:
    Assertion failed: (view->protect & VPROT_SYSTEM), function create_view, file virtual_ios.c, line 14499.
then exit 0x80000101. Wine's create_view deletes an overlapping SYSTEM view (one that was unmapped behind its back) and asserts that
any overlapping view is a system view; here a non-system view overlapped and the assert ended the whole process. Now the overlap is
refused with STATUS_CONFLICTING_ADDRESSES and a line names both views and who made the old one.

Source shape (every host): the assert is gone, the system-view recovery is unchanged, the refusal returns before anything is
allocated or registered, and every caller of create_view handles a failure status (map_view unmaps what it mapped; free_pages'
placeholder path and virtual_create_builtin_view pass the status on; map_dosmem returns it). A compiled run of the production loop
with fake views: a system view is deleted and the loop goes on; a non-system view returns the status, deletes nothing, and prints
the line (first four, then every 256th).

Ported from upstream Madeira v0.2.0 (#516): env.MADEIRA_CREATE_VIEW_REFUSE = 0 restores Wine's assert for a non-system overlap
(kill switch, default on = the refusal above). The assert is reachable only with the switch off; with it on, the code after the
refusal sees system views only. A compiled run of the production loop in a child process per case: switch off, a system view is
still deleted; switch off, a non-system view aborts (SIGABRT, the assert); switch "1" or empty or "00" is not "0" and refuses.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


f0 = src.index('static NTSTATUS create_view( struct file_view **view_ret, void *base, size_t size, unsigned int vprot )')
f1 = src.index('\n}\n', f0) + 3
fn = src[f0:f1]
cond = 'if (!(view->protect & VPROT_SYSTEM) && ios_create_view_refuse_enabled())'
check('the refusal is taken only while env.MADEIRA_CREATE_VIEW_REFUSE is not 0; with it off Wine\'s assert stands (kill switch, #516)',
      fn.count(cond) == 1 and fn.count('assert( view->protect & VPROT_SYSTEM );') == 1 and
      fn.index(cond) < fn.index('assert( view->protect & VPROT_SYSTEM );') < fn.index('delete_view( view );'))
sw0 = src.index('static int ios_create_view_refuse_enabled( void )')
sw = src[sw0:src.index('\n}\n', sw0) + 3]
check('the switch is read once, "0" alone turns it off (MADEIRA_CREATE_VIEW_REFUSE), default on',
      'getenv( "MADEIRA_CREATE_VIEW_REFUSE" )' in sw and "on = !(e && e[0] == '0' && !e[1]);" in sw and 'static int on = -1;' in sw and
      src.count('MADEIRA_CREATE_VIEW_REFUSE" )') == 1)
check('the alignment asserts stay (they are about the caller, not about the memory)',
      'assert( !((UINT_PTR)base & host_page_mask) );' in fn and 'assert( !(size & page_mask) );' in fn)
check('a system view is still deleted, as Wine does', 'delete_view( view );' in fn and fn.index(cond) < fn.index('delete_view( view );'))
check('the refusal returns STATUS_CONFLICTING_ADDRESSES before alloc_pages_vprot / alloc_view / register_view',
      fn.index('return STATUS_CONFLICTING_ADDRESSES;') < fn.index('alloc_pages_vprot( base, size )') < fn.index('alloc_view()') < fn.index('register_view( view );'))
check('the refusal does not delete the old view', 'delete_view' not in fn[fn.index(cond):fn.index('return STATUS_CONFLICTING_ADDRESSES;')])
diag0 = src.index('static void ios_create_view_conflict')
diag = src[diag0:src.index('\n}\n', diag0) + 3]
check('the line names both views (base, size, protect), the thread and the guest rip, and the history of the old one',
      all(t in diag for t in ('[create-view] build591', 'old->base', 'old->size', 'old->protect', 'vprot', 'ios_guest_rip_now()', 'ios_vh_dump( old->base )', 'ios_vhs_dump( old->base')))
check('it is capped: the first four, then every 256th', 'if (c > 4 && (c & 255)) return;' in diag)

calls = [m.start() for m in re.finditer(r'create_view\(', src)]
sites = [src[i - 60:i + 200].replace('\n', ' ') for i in calls if src[i - 20:i].find('NTSTATUS') < 0 and 'static void' not in src[i - 20:i]]
check('four call sites besides the definition', len(sites) == 4, str(len(sites)))
mv = src[src.index('done:\n    status = create_view( view_ret, ptr, size, vprot );'):][:200]
check('map_view: a failure unmaps what was mapped and returns the status', 'if (status != STATUS_SUCCESS) unmap_area( ptr, size );' in mv and 'return status;' in mv)
check('free_pages placeholder path: the status is returned', 'status = create_view( &view, base, size, VPROT_PLACEHOLDER | VPROT_FREE_PLACEHOLDER );\n        if (status) return status;' in src)
check('map_dosmem: the status is returned', '    return create_view( view, addr, size, vprot );' in src)
bv = src[src.index('status = create_view( &view, base, size, SEC_IMAGE | SEC_FILE | VPROT_SYSTEM |'):][:2500]
check('virtual_create_builtin_view: only a success continues; the status is returned', 'if (!status)' in bv and 'return status;' in bv)

# ---------------------------------------------------------------- compiled run of the production loop
w0 = fn.index('    while ((view = find_view_range( base, size )))')
w1 = fn.index('    if (!alloc_pages_vprot( base, size ))')
loop = fn[w0:w1]
helper = src[sw0:f0]   # the switch and the diagnostic, as built
harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int NTSTATUS;
typedef uintptr_t ULONG_PTR;
#define STATUS_SUCCESS 0
#define STATUS_CONFLICTING_ADDRESSES ((NTSTATUS)0xC0000018)
#define VPROT_SYSTEM 0x0200
#define TRACE(...) do {} while (0)
struct file_view { char *base; size_t size; unsigned int protect; int deleted; };
typedef struct { struct { void *UniqueThread; } ClientId; } FAKE_TEB;
static FAKE_TEB fake_teb = { { (void *)0xb8 } };
#define NtCurrentTeb() (&fake_teb)
#define dprintf(fd, ...) do { lines++; if (getenv("QUIET_LOG") == NULL) fprintf(stderr, __VA_ARGS__); } while (0)
static int lines, history_calls, small_history_calls, deleted_count;
static unsigned long long ios_guest_rip_now(void) { return 0x7263c9f7ce; }
static unsigned long long ios_guest_ctx_rip(void) { return 0x7263c9f7d0; }
static void ios_vh_dump(const void *a) { (void)a; history_calls++; }
static unsigned ios_vhs_dump(const void *a, size_t span, unsigned max) { (void)a; (void)span; (void)max; small_history_calls++; return 0; }
static struct file_view views[4];
static int nviews;
static struct file_view *find_view_range(void *base, size_t size)
{
    int i;
    for (i = 0; i < nviews; i++)
        if (!views[i].deleted && (char *)base < views[i].base + views[i].size && views[i].base < (char *)base + size) return &views[i];
    return NULL;
}
static void delete_view(struct file_view *v) { v->deleted = 1; deleted_count++; }
''' + helper + r'''
static NTSTATUS loop_only(void *base, size_t size, unsigned int vprot)
{
    struct file_view *view;
@LOOP@
    return STATUS_SUCCESS;
}
int main(int argc, char **argv)
{
    NTSTATUS st;
    int i, fail = 0;

    if (argc > 1)   /* one scenario per process: the switch is read once */
    {
        if (!strcmp( argv[1], "nonsystem" ))   /* the overlapped view is not a system view */
        {
            views[0] = (struct file_view){ (char *)0x7c3a000000, 0x1000000, 0x1 | 0x4, 0 }; nviews = 1;
            st = loop_only((void *)0x7c3a400000 - 0x400000, 0x1000000, 0x4);
            printf("%s nonsystem returned status 0x%x, deleted %d\n", st == STATUS_CONFLICTING_ADDRESSES && !deleted_count ? "refused" : "went on", (unsigned)st, deleted_count);
            return 0;
        }
        if (!strcmp( argv[1], "system" ))
        {
            views[0] = (struct file_view){ (char *)0x7c3a000000, 0x1000000, VPROT_SYSTEM | 0x1, 0 }; nviews = 1;
            st = loop_only((void *)0x7c3a400000, 0x1000000, 0x4);
            printf("%s system status 0x%x, deleted %d\n", st == 0 && deleted_count == 1 ? "deleted" : "wrong", (unsigned)st, deleted_count);
            return 0;
        }
        return 2;
    }

    /* a stale system view: deleted, the creation goes on */
    views[0] = (struct file_view){ (char *)0x7c3a000000, 0x1000000, VPROT_SYSTEM | 0x1, 0 }; nviews = 1;
    st = loop_only((void *)0x7c3a400000, 0x1000000, 0x4);
    printf("%s system overlap: status 0x%x, deleted %d, lines %d\n", (st == 0 && deleted_count == 1 && lines == 0) ? "ok  " : "FAIL", (unsigned)st, deleted_count, lines);
    fail += !(st == 0 && deleted_count == 1 && lines == 0);

    /* a live non-system view (Fallout 4): refused, nothing deleted */
    views[0] = (struct file_view){ (char *)0x7c3a000000, 0x1000000, 0x1 | 0x4, 0 }; nviews = 1; deleted_count = 0; lines = 0;
    st = loop_only((void *)0x7c3a400000 - 0x400000, 0x1000000, 0x4);
    printf("%s non-system overlap: status 0x%x, deleted %d, lines %d, history %d+%d\n",
           (st == STATUS_CONFLICTING_ADDRESSES && deleted_count == 0 && lines == 1 && history_calls == 1 && small_history_calls == 1) ? "ok  " : "FAIL",
           (unsigned)st, deleted_count, lines, history_calls, small_history_calls);
    fail += !(st == STATUS_CONFLICTING_ADDRESSES && deleted_count == 0 && lines == 1 && history_calls == 1 && small_history_calls == 1);

    /* a system view first, then a non-system one behind it: the system one is deleted, the other refuses */
    for (i = 0; i < 4; i++) views[i].deleted = 0;
    views[0] = (struct file_view){ (char *)0x1000000, 0x400000, VPROT_SYSTEM, 0 };
    views[1] = (struct file_view){ (char *)0x1400000, 0x400000, 0x7, 0 }; nviews = 2; deleted_count = 0; lines = 0;
    st = loop_only((void *)0x1000000, 0x800000, 0x4);
    printf("%s system then non-system: status 0x%x, deleted %d\n", (st == STATUS_CONFLICTING_ADDRESSES && deleted_count == 1 && views[0].deleted && !views[1].deleted) ? "ok  " : "FAIL", (unsigned)st, deleted_count);
    fail += !(st == STATUS_CONFLICTING_ADDRESSES && deleted_count == 1 && views[0].deleted && !views[1].deleted);

    /* no overlap: the loop does nothing */
    nviews = 0; lines = 0;
    st = loop_only((void *)0x5000000, 0x4000, 0x4);
    printf("%s no overlap: status 0x%x, lines %d\n", (st == 0 && lines == 0) ? "ok  " : "FAIL", (unsigned)st, lines);
    fail += !(st == 0 && lines == 0);

    /* the cap: 300 refusals print 4 lines, then the 256th */
    views[0] = (struct file_view){ (char *)0x7c3a000000, 0x1000000, 0x7, 0 }; nviews = 1; lines = 0;
    for (i = 0; i < 300; i++) loop_only((void *)0x7c3a000000, 0x4000, 0x4);
    printf("%s cap: %d lines for 300 refusals (the counter already stands at 2: lines 3 and 4, then the 256th)\n", (lines >= 3 && lines <= 5) ? "ok  " : "FAIL", lines);
    fail += !(lines >= 3 && lines <= 5);
    return fail != 0;
}
'''.replace('@LOOP@', loop)

with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wno-implicit-function-declaration', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the production loop and the diagnostic compile on the host', cc.returncode == 0, cc.stderr[-3000:])
    if cc.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True, env=dict(os.environ, QUIET_LOG='1'))
        for line in r.stdout.splitlines():
            check(line[5:], line.startswith('ok'))
        check('every compiled scenario passes', r.returncode == 0, r.stdout[-800:])
        # the kill switch: one process per case
        def run(arg, value):
            env = dict(os.environ, QUIET_LOG='1')
            env.pop('MADEIRA_CREATE_VIEW_REFUSE', None)
            if value is not None:
                env['MADEIRA_CREATE_VIEW_REFUSE'] = value
            return subprocess.run([str(exe), arg], capture_output=True, text=True, env=env)
        r = run('nonsystem', None)
        check('switch unset: a non-system overlap is refused (the default)', r.returncode == 0 and r.stdout.startswith('refused'), r.stdout + r.stderr[-300:])
        for value in ('1', '', '00', 'off', '0x'):
            r = run('nonsystem', value)
            check('MADEIRA_CREATE_VIEW_REFUSE=%r is not "0": still refused' % value, r.returncode == 0 and r.stdout.startswith('refused'), r.stdout + r.stderr[-300:])
        r = run('nonsystem', '0')
        check('MADEIRA_CREATE_VIEW_REFUSE=0: the assert ends the process again (SIGABRT), as before build 591',
              r.returncode < 0 and -r.returncode == 6 and 'VPROT_SYSTEM' in r.stderr, 'rc=%d %s' % (r.returncode, r.stderr[-300:]))
        r = run('system', '0')
        check('MADEIRA_CREATE_VIEW_REFUSE=0: a stale system view is still deleted and the creation goes on', r.returncode == 0 and r.stdout.startswith('deleted'), r.stdout + r.stderr[-300:])
        r = run('system', None)
        check('switch unset: the system view is deleted as well', r.returncode == 0 and r.stdout.startswith('deleted'), r.stdout + r.stderr[-300:])

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
