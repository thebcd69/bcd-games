#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-heap-exec-flush.py: blocks of an executable heap are flushed from the translator (build 632; i386 farm only).

Terraria crashed right after making a character: a heap created with HEAP_CREATE_ENABLE_EXECUTE hands freed bytes out again and the
program (Wine Mono's dynamic methods) writes new code into them without a flush, which x86 does not need; FEX had turned off its write trap
for Mono's process and ran what it had translated from the old code. The patch (upstream Madeira's wine commit e7685ffad, applied as a
script for the i386 farm only) calls NtFlushInstructionCache for each block RtlAllocateHeap hands out of an executable heap and for a block
RtlReAllocateHeap grows in place. MADEIRA_HEAP_EXEC_FLUSH=0 turns it off.

Why a script and not a commit in the Wine fork: every added line shifts the __LINE__ an assert() in unsafe_heap_from_handle() carries into
the ARM64EC ntdll object (measured: mov w2, #849 becomes #868, the rest identical), and tools/build-wine-extra-dlls.sh builds ntdll-sync.dll
and ntdll-xsec.dll only when an unpatched rebuild of the pinned tree equals the shipped ntdll.dll byte for byte. Same arrangement as the x64
machine answer and [veh] add: applied before the i386 farm, put back right after it.

Checks: (a) the script applies once to the pinned heap.c and says "already patched" the second time, and names the anchor that moved when
one does; (b) every line it adds is inside an "#ifdef __i386__" region (a preprocessor walk of the patched file), so no other architecture
compiles any of it; (c) the helper and the switch's read, cut from the patched file and compiled on the host with fake Nt calls: an
executable heap flushes the block it hands out (address and size), a plain heap, a NULL block and a failed allocation flush nothing,
MADEIRA_HEAP_EXEC_FLUSH=0 turns it off and any other value leaves it on; (d) with the repo's llvm-mingw (when present) the patched file
compiles for i686 and x86_64; (e) the workflow checks it, applies it before the i386 farm and puts heap.c back before the arm64ec extra DLLs.
"""
from pathlib import Path
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location('heapflush', root / 'tools/patch-wine-heap-exec-flush.py')
hp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hp)

heap_c = root / 'wine/dlls/ntdll/heap.c'
if not heap_c.is_file():
    print('SKIP: no wine sources (check out the submodule)')
else:
    base = heap_c.read_text()
    check('the Wine fork carries no part of it (the script is the only source of the flush)', 'madeira_heap_exec_flush' not in base and
          'MADEIRA_HEAP_EXEC_FLUSH' not in base)

    # (a) apply twice, and the anchors
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / 'heap.c'
        f.write_text(base)
        p = root / 'tools/patch-wine-heap-exec-flush.py'
        r1 = subprocess.run([sys.executable, str(p), str(f)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(p), str(f)], capture_output=True, text=True)
        check('applies once to the pinned heap.c, "already patched" the second time',
              r1.returncode == 0 and 'applied' in r1.stdout and r2.returncode == 0 and 'already patched' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        patched = f.read_text()
        check('the second run changed nothing', patched == hp.apply(base))
        for what, old, new in hp.EDITS:
            g = Path(t) / 'broken.c'
            g.write_text(base.replace(old, old.replace('\n', '\n ', 1), 1))
            r = subprocess.run([sys.executable, str(p), str(g)], capture_output=True, text=True)
            check(f'a moved anchor ({what}) is named and nothing is written', r.returncode != 0 and what in (r.stdout + r.stderr)
                  and 'madeira-bcd: executable heap flush' not in g.read_text(), r.stdout + r.stderr)

        # removing exactly the added text gives back the pinned file
        undone = patched
        for what, old, new in hp.EDITS:
            undone = undone.replace(new, old)
        check('it adds only its own text (undoing the four edits gives the pinned heap.c back)', undone == base)

        # (b) every added line sits inside "#ifdef __i386__"
        def i386_lines(text):
            """line numbers (1-based) that are inside a '#ifdef __i386__' group that is not in its '#else'"""
            stack, out = [], set()
            for n, line in enumerate(text.split('\n'), 1):
                m = re.match(r'\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)', line)
                if m:
                    kind, rest = m.group(1), m.group(2).strip()
                    if kind in ('if', 'ifdef', 'ifndef'):
                        stack.append([kind, rest, False])
                    elif kind in ('else', 'elif') and stack:
                        stack[-1][2] = True
                    elif kind == 'endif' and stack:
                        stack.pop()
                    continue
                if any(k == 'ifdef' and r == '__i386__' and not e for k, r, e in stack):
                    out.add(n)
            return out
        added = [n for n, l in enumerate(patched.split('\n'), 1) if l not in set(base.split('\n'))]
        inside = i386_lines(patched)
        outside = [n for n in added if n not in inside]
        check('all %d added lines are inside "#ifdef __i386__"' % len(added), added and not outside,
              'outside: ' + str([(n, patched.split('\n')[n - 1]) for n in outside[:5]]))

        # (c) the helper and the switch on the host
        i = patched.index('static BOOL madeira_heap_exec_flush = TRUE;')
        j = patched.index('\n}\n', patched.index('static inline void madeira_heap_flush_exec')) + 3
        helper = patched[i:j]
        k = patched.index('    RtlInitUnicodeString( &name, L"MADEIRA_HEAP_EXEC_FLUSH" );')
        read = patched[k:patched.index('    if (madeira_heap_policy)', k)].replace('L"', 'u"')
        harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
typedef int BOOL; typedef unsigned short WCHAR; typedef unsigned long SIZE_T; typedef int NTSTATUS; typedef void *HANDLE;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
#define TRUE 1
#define FALSE 0
#define L u
#define HEAP_CREATE_ENABLE_EXECUTE 0x00040000
#define HEAP_GROWABLE 0x00000002
#define NtCurrentProcess() ((HANDLE)-1)
struct heap { unsigned int flags; };
static int flushes; static void *last_ptr; static SIZE_T last_size; static HANDLE last_proc;
static NTSTATUS NtFlushInstructionCache(HANDLE p, const void *a, SIZE_T s) { flushes++; last_proc = p; last_ptr = (void *)a; last_size = s; return 0; }
static const char *fake_env;      /* the value of MADEIRA_HEAP_EXEC_FLUSH, or NULL when unset */
static void RtlInitUnicodeString(UNICODE_STRING *u, const WCHAR *s) { u->Buffer = (WCHAR *)s; u->Length = 0; while (s[u->Length]) u->Length++; u->Length *= 2; u->MaximumLength = u->Length + 2; }
static NTSTATUS RtlQueryEnvironmentVariable_U(void *env, UNICODE_STRING *name, UNICODE_STRING *value)
{
    if (!fake_env || name->Length != 2 * strlen("MADEIRA_HEAP_EXEC_FLUSH") || name->Buffer[0] != 'M') return -1;
    for (value->Length = 0; fake_env[value->Length / 2]; value->Length += 2) value->Buffer[value->Length / 2] = fake_env[value->Length / 2];
    return 0;
}
''' + helper + r'''
static void init(void)
{
    UNICODE_STRING name, value;
    WCHAR buffer[2];
    madeira_heap_exec_flush = TRUE;
''' + read.replace('\n    if (madeira_heap_policy)', '') + r'''}
#define EXPECT(c, ...) do { if (!(c)) { fail++; fprintf(stderr, "EXPECT failed line %d: ", __LINE__); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } } while (0)
int main(void)
{
    static char block[64];
    struct heap exec = { HEAP_GROWABLE | HEAP_CREATE_ENABLE_EXECUTE }, plain = { HEAP_GROWABLE };
    int fail = 0;

    fake_env = NULL; init();
    madeira_heap_flush_exec(&exec, block, 40);
    EXPECT(flushes == 1 && last_ptr == block && last_size == 40 && last_proc == (HANDLE)-1, "executable heap: flushes %d", flushes);
    madeira_heap_flush_exec(&plain, block, 40);
    EXPECT(flushes == 1, "plain heap flushed");
    madeira_heap_flush_exec(&exec, NULL, 40);
    EXPECT(flushes == 1, "NULL block flushed");
    fake_env = "0"; init();
    madeira_heap_flush_exec(&exec, block, 40);
    EXPECT(flushes == 1 && !madeira_heap_exec_flush, "MADEIRA_HEAP_EXEC_FLUSH=0 still flushes");
    fake_env = "1"; init();
    madeira_heap_flush_exec(&exec, block, 8);
    EXPECT(flushes == 2 && madeira_heap_exec_flush && last_size == 8, "=1 does not flush");
    fake_env = "00"; init();
    EXPECT(madeira_heap_exec_flush, "a two-character value turns it off");
    fake_env = ""; init();
    EXPECT(madeira_heap_exec_flush, "an empty value turns it off");
    fake_env = NULL; init();
    EXPECT(madeira_heap_exec_flush, "unset turns it off");
    printf("RESULT %s\n", fail ? "FAIL" : "OK");
    return fail ? 1 : 0;
}
'''
        c = Path(t) / 'h.c'
        c.write_text(harness)
        exe = Path(t) / 'h'
        cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-fsanitize=address,undefined',
                             '-o', str(exe), str(c)], capture_output=True, text=True)
        check('the helper and the switch\'s read compile on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-2000:])
        if cc.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            check('an executable heap flushes the block it hands out; a plain heap, a NULL block and a failure flush nothing; =0 turns it off, '
                  'every other value leaves it on', run.returncode == 0 and 'RESULT OK' in run.stdout, run.stdout + run.stderr[-1500:])

        # (d) the real file compiles for i686 and x86_64 with the repo's llvm-mingw
        tc = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
        if (tc / 'i686-w64-mingw32-clang').exists() and (root / 'wine/build-macos/include').is_dir():
            (Path(t) / 'ntdll').mkdir()
            shutil_target = Path(t) / 'ntdll' / 'heap.c'
            shutil_target.write_text(patched)
            for tgt in ('i686', 'x86_64'):
                r = subprocess.run([str(tc / (tgt + '-w64-mingw32-clang')), '-fsyntax-only', '-O2', '-Wall', '-D__WINESRC__', '-D_NTSYSTEM_',
                                    '-DWINBASEAPI=', '-D_WIN32_WINNT=0x0A00', '-I' + str(root / 'wine/dlls/ntdll'),
                                    '-I' + str(root / 'wine/build-macos/include'), '-I' + str(root / 'wine/include'), str(shutil_target)],
                                   capture_output=True, text=True)
                check(f'the patched heap.c compiles for {tgt}', r.returncode == 0 and ' warning' not in r.stderr, r.stderr[-1500:])
        else:
            print('note: no llvm-mingw or generated headers in this tree; the compile runs in the full tree')

# (e) the workflow
wf = (root / '.github/workflows/build-ipa.yml').read_text()
apply_step = 'python3 tools/patch-wine-heap-exec-flush.py wine/dlls/ntdll/heap.c'
restore = re.search(r'git -C wine checkout -- dlls/ntdll/heap\.c\n', wf)
check('the workflow checks and applies it', apply_step in wf and 'python3 tests/host/check-heap-exec-flush.py' in wf)
check('heap.c is put back after the i386 farm', restore is not None)
if apply_step in wf and restore:
    farm = wf.index('- name: Build i386 farm (WoW64)')
    extra = wf.index('- name: Build extra Wine DLLs for arm64ec')
    check('applied before the i386 farm is built, put back before the arm64ec extra DLLs (which need an unpatched ntdll)',
          wf.index(apply_step) < farm < restore.start() < extra)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
