#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-veh-add-log.py: [veh] add, which of a 32-bit program's own modules registers an exception handler (round 624; log only).

osu! (SV6C4C), Deadpool and Diablo II end after an exception one of the program's handlers took, and no log said whose handler. The patch logs
the registration in ntdll (RtlAddVectoredExceptionHandler, RtlAddVectoredContinueHandler) and kernelbase (SetUnhandledExceptionFilter) with
module+RVA, leaves Windows' and Wine's own files out (a path under \\windows\\), and stops at 16 lines a process.

Checks: the helper (cut from the patch script) compiled on the host against a fake loader: a game module, a system module (left out, also in
upper case), a handler in no module, the 16-line cap; the registration calls are unchanged around the new line; on the pinned Wine source
(when checked out) it applies once to both files and compiles for i686 and x86_64 with the repo's llvm-mingw; the workflow applies it before
the i386 farm and puts both files back before the arm64ec extra DLLs (they compare an unpatched ntdll/kernelbase with the shipped binaries).
"""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location('vehpatch', root / 'tools/patch-wine-veh-add-log.py')
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)

check('the registration is unchanged: the line comes first, the call that registers is the same',
      vp.NT_EXC_NEW.replace('    bcd_veh_log_owner( "exception", first, func );   /* madeira-bcd: [veh] add */\n', '') == vp.NT_EXC_OLD
      and vp.NT_CONT_NEW.replace('    bcd_veh_log_owner( "continue", first, func );   /* madeira-bcd: [veh] add */\n', '') == vp.NT_CONT_OLD
      and vp.KB_NEW.replace('    bcd_veh_log_owner( "unhandled-filter", 0, filter );   /* madeira-bcd: [veh] add */\n', '') == vp.KB_OLD)
check('the helper only reads and prints (no write, no lock, no allocation)',
      not [t for t in ('RtlAllocateHeap', 'RtlEnterCriticalSection', 'WriteFile', 'NtWrite', 'memcpy') if t in vp.HELPER])

harness = r'''
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
typedef int BOOL; typedef unsigned short WCHAR; typedef unsigned int ULONG; typedef long LONG; typedef unsigned long ULONG_PTR;
#define TRUE 1
#define FALSE 0
#define L
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { void *DllBase; UNICODE_STRING FullDllName, BaseDllName; } LDR_DATA_TABLE_ENTRY;
#define InterlockedIncrement(p) (__sync_add_and_fetch((p), 1))
#define ERR(...) printf(__VA_ARGS__)
static char game[0x2000], sys32[0x1000], jit[16];
static WCHAR gfull[64], gbase[16], sfull[64], sbase[16];
static LDR_DATA_TABLE_ENTRY gmod, smod;
static void wset(WCHAR *d, const char *s, UNICODE_STRING *u) { size_t i; for (i = 0; s[i]; i++) d[i] = (unsigned char)s[i]; d[i] = 0;
                                                                if (u) { u->Buffer = d; u->Length = (unsigned short)(i * 2); } }
static const char *debugstr_w(const WCHAR *w) { static char out[64]; size_t i; for (i = 0; w && w[i] && i < 63; i++) out[i] = (char)w[i]; out[i] = 0; return out; }
static int LdrFindEntryForAddress(const void *a, LDR_DATA_TABLE_ENTRY **m)
{
    if ((const char *)a >= game && (const char *)a < game + sizeof(game)) { *m = &gmod; return 0; }
    if ((const char *)a >= sys32 && (const char *)a < sys32 + sizeof(sys32)) { *m = &smod; return 0; }
    return 1;
}
''' + vp.HELPER.replace('L"\\\\windows\\\\"', 'u"\\\\windows\\\\"') + r'''
int main(int argc, char **argv)
{
    int i;
    gmod.DllBase = game; wset(gfull, "C:\\Games\\osu!\\osu!auth.dll", &gmod.FullDllName); wset(gbase, "osu!auth.dll", &gmod.BaseDllName);
    smod.DllBase = sys32; wset(sfull, argc > 1 ? "C:\\WINDOWS\\SysWOW64\\kernelbase.dll" : "C:\\windows\\system32\\ntdll.dll", &smod.FullDllName);
    wset(sbase, "ntdll.dll", &smod.BaseDllName);
    bcd_veh_log_owner("exception", 1, game + 0x1234);
    bcd_veh_log_owner("exception", 0, sys32 + 0x10);
    bcd_veh_log_owner("continue", 0, jit);
    bcd_veh_log_owner("unhandled-filter", 0, 0);
    for (i = 0; i < 20; i++) bcd_veh_log_owner("exception", 0, game + i);
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the helper compiles on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-2000:])
    if cc.returncode == 0:
        out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
        lines = out.splitlines()
        check('a game module\'s handler: kind, first, address, module+RVA',
              lines[:1] and lines[0].startswith('[veh] add exception first=1 handler=') and lines[0].endswith(' = osu!auth.dll+0x1234'), out[:300])
        check('a handler in Windows\' or Wine\'s own files is left out; one in no module says so; a NULL filter is not logged',
              'ntdll.dll' not in out and '[veh] add continue first=0 handler=' in out and 'in no module (generated code)' in out
              and 'unhandled-filter' not in out, out[:400])
        check('16 lines a process at most', len(lines) == 16, str(len(lines)))
        up = subprocess.run([str(exe), 'upper'], capture_output=True, text=True).stdout
        check('the system path test ignores case (C:\\WINDOWS\\SysWOW64)', 'kernelbase' not in up and 'ntdll' not in up)

wine = root / 'wine'
exc, dbg = wine / 'dlls/ntdll/exception.c', wine / 'dlls/kernelbase/debug.c'
if exc.is_file() and dbg.is_file():
    def committed(rel, fallback):
        r = subprocess.run(['git', '-C', str(wine), 'show', 'HEAD:' + rel], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 and r.stdout else fallback.read_text()
    with tempfile.TemporaryDirectory() as t:
        w = Path(t) / 'wine'
        (w / 'dlls/ntdll').mkdir(parents=True)
        (w / 'dlls/kernelbase').mkdir(parents=True)
        (w / 'dlls/ntdll/exception.c').write_text(committed('dlls/ntdll/exception.c', exc))
        (w / 'dlls/kernelbase/debug.c').write_text(committed('dlls/kernelbase/debug.c', dbg))
        p = root / 'tools/patch-wine-veh-add-log.py'
        r1 = subprocess.run([sys.executable, str(p), str(w)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(p), str(w)], capture_output=True, text=True)
        check('applies once to both files', r1.returncode == 0 and r1.stdout.count('[veh] add applied') == 2
              and r2.returncode == 0 and r2.stdout.count('already applied') == 2, r1.stdout + r1.stderr + r2.stdout)
        tc = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
        if (tc / 'i686-w64-mingw32-clang').exists():
            for tgt in ('i686', 'x86_64'):
                for rel in ('dlls/ntdll/exception.c', 'dlls/kernelbase/debug.c'):
                    r = subprocess.run([str(tc / (tgt + '-w64-mingw32-clang')), '-fsyntax-only', '-O2', '-Wall', '-D__WINESRC__', '-D_NTSYSTEM_',
                                        '-D_KERNELBASE_', '-DWINBASEAPI=', '-D_WIN32_WINNT=0x0A00', '-I' + str(wine / Path(rel).parent),
                                        '-I' + str(wine / 'build-macos/include'), '-I' + str(wine / 'include'), str(w / rel)],
                                       capture_output=True, text=True)
                    check(f'{rel} compiles for {tgt} with the patch', r.returncode == 0 and ' warning' not in r.stderr, r.stderr[-1500:])
        else:
            print('note: no llvm-mingw in this tree; the compile runs in the full tree')
else:
    print('SKIP: no wine sources (check out the submodule)')

wf = (root / '.github/workflows/build-ipa.yml').read_text()
apply_step = 'python3 tools/patch-wine-veh-add-log.py wine'
restore = 'git -C wine checkout -- dlls/ntdll/env.c dlls/kernelbase/process.c dlls/ntdll/exception.c dlls/kernelbase/debug.c'
check('the workflow checks and applies it, and puts both files back', apply_step in wf and restore in wf
      and 'python3 tests/host/check-veh-add-log.py' in wf)
if apply_step in wf and restore in wf:
    farm = wf.index('- name: Build i386 farm (WoW64)')
    extra = wf.index('- name: Build extra Wine DLLs for arm64ec')
    check('applied before the i386 farm is built, put back before the arm64ec extra DLLs (which need an unpatched ntdll/kernelbase)',
          wf.index(apply_step) < farm < wf.index(restore) < extra)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
