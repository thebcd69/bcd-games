#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""SetThreadIdealProcessorEx succeeds for a valid processor (build 632; Wine fork commit c0ad4a57a, upstream Madeira 0.2.0).

Wine's kernelbase answered SetThreadIdealProcessorEx with a FIXME stub: FALSE and ERROR_CALL_NOT_IMPLEMENTED. The ideal processor is a
scheduling hint and Windows succeeds for any valid processor; upstream found Intel Embree (Counter-Strike 2's embree3.dll) setting it for each
worker thread and treating the FALSE as fatal.

BCD's archive has the stub line in six logs: Counter-Strike 2 SAHIP3 (build 574) and AJJ4RC (576), Control X49WBQ, QJAQK3, P0DERY, 6AG46N
(592). None of them is a failure this function cures: AJJ4RC called the stub five times and drew 3,917 frames (presents=3917 after 120 s);
SAHIP3's access violation is the copy-free image's NULL read (rule source2-nocopy-null-read-pool-split); Control's c0000005 is the
RtlRunOnceComplete waiter walk (rule runonce-stale-waiter-keyed-event, c0000024 from the keyed event). So there is no auto-tune rule for this
fix: nothing a log shows would select it. It is the correct answer for a hint (and a game build whose Embree is stricter than the logged ones).

Who gets it: 32-bit programs keylessly (the i386 farm is built from the Wine pin); 64-bit programs only through kernelbase-optin.dll, built from
the same pinned tree (tools/build-wine-extra-dlls.sh, ZMP) and linked by env.MADEIRA_OPTIN_DLLS = kernelbase: the shipped 64-bit kernelbase.dll
is a committed binary BCD never replaces.

Checks: the function cut from the Wine fork's kernelbase/thread.c and compiled on the host with fake calls: a valid processor succeeds and
fills "previous" (group 0, the old number, reserved 0), "previous" may be NULL, a NULL pointer / a group other than 0 / a number at
MAXIMUM_PROCESSORS or above fail with ERROR_INVALID_PARAMETER without calling SetThreadIdealProcessor, a failing SetThreadIdealProcessor
makes it fail and leaves "previous" alone; the stub is gone and SetThreadIdealProcessor is unchanged; BCD's kernelbase-fibertrace script
still applies on the patched thread.c; with the repo's llvm-mingw (when present) the file compiles for i686 and x86_64.
"""
from pathlib import Path
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


thread_c = root / 'wine/dlls/kernelbase/thread.c'
if not thread_c.is_file():
    print('SKIP: no wine sources (check out the submodule)')
else:
    src = thread_c.read_text()
    i = src.index('BOOL WINAPI DECLSPEC_HOTPATCH SetThreadIdealProcessorEx(')
    func = src[i:src.index('\n}\n', i) + 3]
    check('the FIXME stub is gone', 'stub' not in func and 'ERROR_CALL_NOT_IMPLEMENTED' not in func and 'FIXME' not in func, func[:300])
    j = src.index('DWORD WINAPI DECLSPEC_HOTPATCH SetThreadIdealProcessor(')
    plain = src[j:src.index('\n}\n', j) + 3]
    check('SetThreadIdealProcessor is as before (NtSetInformationThread ThreadIdealProcessor)',
          'NtSetInformationThread( thread, ThreadIdealProcessor, &proc, sizeof(proc) )' in plain and 'return ~0u;' in plain)

    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int BOOL; typedef void *HANDLE; typedef unsigned int DWORD; typedef unsigned short WORD; typedef unsigned char BYTE;
typedef struct _PROCESSOR_NUMBER { WORD Group; BYTE Number; BYTE Reserved; } PROCESSOR_NUMBER;
#define TRUE 1
#define FALSE 0
#define WINAPI
#define DECLSPEC_HOTPATCH
#define MAXIMUM_PROCESSORS 64
#define ERROR_INVALID_PARAMETER 87
#define TRACE(...) do {} while (0)
static DWORD last_error; static void SetLastError(DWORD e) { last_error = e; }
static int calls; static HANDLE seen_thread; static DWORD seen_proc, next_result;
static DWORD WINAPI SetThreadIdealProcessor( HANDLE thread, DWORD proc ) { calls++; seen_thread = thread; seen_proc = proc; return next_result; }
''' + func + r'''
#define EXPECT(c, ...) do { if (!(c)) { fail++; fprintf(stderr, "EXPECT failed line %d: ", __LINE__); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } } while (0)
int main(void)
{
    PROCESSOR_NUMBER ideal, prev;
    HANDLE th = (HANDLE)0x98;
    int fail = 0, n;

    /* a valid processor: success, the old number comes back in group 0 */
    memset(&ideal, 0, sizeof(ideal)); ideal.Number = 3;
    memset(&prev, 0xaa, sizeof(prev)); next_result = 5; last_error = 0;
    EXPECT(SetThreadIdealProcessorEx(th, &ideal, &prev) == TRUE, "valid processor fails");
    EXPECT(calls == 1 && seen_thread == th && seen_proc == 3, "forwarded %d %p %u", calls, seen_thread, seen_proc);
    EXPECT(prev.Group == 0 && prev.Number == 5 && prev.Reserved == 0, "previous %u/%u/%u", prev.Group, prev.Number, prev.Reserved);
    EXPECT(last_error == 0, "last error set on success");

    /* previous may be NULL */
    EXPECT(SetThreadIdealProcessorEx(th, &ideal, NULL) == TRUE, "NULL previous");

    /* the whole range up to MAXIMUM_PROCESSORS - 1 is accepted (Windows hints are not checked against the CPU count here) */
    for (n = 0; n < MAXIMUM_PROCESSORS; n++)
    {
        ideal.Number = n;
        EXPECT(SetThreadIdealProcessorEx(th, &ideal, &prev) == TRUE && seen_proc == (DWORD)n, "number %d", n);
    }

    /* invalid arguments: no call, ERROR_INVALID_PARAMETER */
    calls = 0; last_error = 0;
    EXPECT(SetThreadIdealProcessorEx(th, NULL, &prev) == FALSE && last_error == ERROR_INVALID_PARAMETER && calls == 0, "NULL ideal");
    ideal.Group = 1; ideal.Number = 0; last_error = 0;
    EXPECT(SetThreadIdealProcessorEx(th, &ideal, &prev) == FALSE && last_error == ERROR_INVALID_PARAMETER && calls == 0, "group 1");
    ideal.Group = 0; ideal.Number = MAXIMUM_PROCESSORS; last_error = 0;
    EXPECT(SetThreadIdealProcessorEx(th, &ideal, &prev) == FALSE && last_error == ERROR_INVALID_PARAMETER && calls == 0, "number 64");

    /* SetThreadIdealProcessor fails: so does this, previous untouched */
    ideal.Number = 2; next_result = ~0u; memset(&prev, 0xaa, sizeof(prev));
    EXPECT(SetThreadIdealProcessorEx(th, &ideal, &prev) == FALSE && calls == 1, "failing inner call");
    EXPECT(prev.Group == 0xaaaa && prev.Number == 0xaa && prev.Reserved == 0xaa, "previous written on failure");
    printf("RESULT %s\n", fail ? "FAIL" : "OK");
    return fail ? 1 : 0;
}
'''
    with tempfile.TemporaryDirectory() as t:
        c = Path(t) / 'i.c'
        c.write_text(harness)
        exe = Path(t) / 'i'
        cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-fsanitize=address,undefined', '-o', str(exe), str(c)],
                            capture_output=True, text=True)
        check('the function compiles on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-2000:])
        if cc.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            check('valid processors succeed and fill "previous"; NULL / group / number errors are ERROR_INVALID_PARAMETER with no inner call; a failing '
                  'inner call fails it and leaves "previous" alone', run.returncode == 0 and 'RESULT OK' in run.stdout, run.stdout + run.stderr[-1500:])

        # BCD's kernelbase build-time script still applies on the patched file
        w = Path(t) / 'wine'
        files = ('dlls/kernelbase/kernelbase.h', 'dlls/kernelbase/thread.c', 'dlls/kernelbase/debug.c', 'dlls/kernelbase/file.c', 'dlls/kernelbase/sync.c')
        for rel in files:
            (w / rel).parent.mkdir(parents=True, exist_ok=True)
            (w / rel).write_text((root / 'wine' / rel).read_text())
        r1 = subprocess.run([sys.executable, str(root / 'tools/patch-wine-kernelbase-fibertrace.py'), str(w)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(root / 'tools/patch-wine-kernelbase-fibertrace.py'), str(w)], capture_output=True, text=True)
        check('tools/patch-wine-kernelbase-fibertrace.py applies on the patched thread.c, once',
              r1.returncode == 0 and 'patched' in r1.stdout and r2.returncode == 0 and 'already' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        check('and SetThreadIdealProcessorEx is the same after it (the script does not touch it)',
              func in (w / 'dlls/kernelbase/thread.c').read_text())

        tc = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
        if (tc / 'i686-w64-mingw32-clang').exists() and (root / 'wine/build-macos/include').is_dir():
            for tgt in ('i686', 'x86_64'):
                r = subprocess.run([str(tc / (tgt + '-w64-mingw32-clang')), '-fsyntax-only', '-O2', '-Wall', '-D__WINESRC__', '-D_KERNELBASE_',
                                    '-DWINBASEAPI=', '-D_WIN32_WINNT=0x0A00', '-I' + str(root / 'wine/dlls/kernelbase'),
                                    '-I' + str(root / 'wine/build-macos/include'), '-I' + str(root / 'wine/include'), str(thread_c)],
                                   capture_output=True, text=True)
                check(f'kernelbase/thread.c compiles for {tgt}', r.returncode == 0 and ' warning' not in r.stderr, r.stderr[-1500:])
        else:
            print('note: no llvm-mingw or generated headers in this tree; the compile runs in the full tree')

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
