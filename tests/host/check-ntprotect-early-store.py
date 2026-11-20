#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 668 (G2 S7; FTL MYQWVW, build 659; Peggle Deluxe SF5ZDS/METSK8, build 634): NtProtectVirtualMemory writes an out-parameter
that lies INSIDE the range it changes before the change, while the page still has its old protection.

The program's OldProtect variable sat in the last page of BASS.dll, the call dropped WRITE from that page, and the store after
set_protection was a host fault inside NtProtectVirtualMemory (Madeira`NtProtectVirtualMemory+0x7a8) that ended the game. The check
reads the production function: the early store is guarded by the three range tests, comes before set_protection, carries the same
value the opt-in WRITECOPY report would give, and the stores after the change are skipped when it ran. A model of the call order then
shows the difference on a page whose write permission goes away (mprotect), with and without the early store.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
fn = src[src.index('NTSTATUS WINAPI NtProtectVirtualMemory( HANDLE process, PVOID *addr_ptr, SIZE_T *size_ptr,'):]
fn = fn[:fn.index('\n}\n')]
failures = 0


def check(cond, what):
    global failures
    print(('ok   ' if cond else 'FAIL ') + what)
    failures += 0 if cond else 1


early = fn.index('ios_ntprotect_early_stored = 1;')
setp = fn.index('status = set_protection( view, base, size, new_prot );')
check(early < setp, 'the early store comes before set_protection')
guard = fn[fn.rindex('if (((uintptr_t)old_prot >= (uintptr_t)base', 0, early):early]
check(all(f'(uintptr_t){p} >= (uintptr_t)base && (uintptr_t){p} < (uintptr_t)base + size' in guard for p in ('old_prot', 'addr_ptr', 'size_ptr')),
      'it runs only when old_prot, addr_ptr or size_ptr lies inside [base, base + size)')
check('ios_wc_old_prot_report( old, new_prot, ios_wc_image, ios_wow_base() != 0, 1, &wc_changed )' in guard,
      'it reports the same old protection the opt-in WRITECOPY path would')
late = fn[fn.index('    if (status == STATUS_SUCCESS)\n    {', setp):]
check(re.search(r'if \(!ios_ntprotect_early_stored\)[^\n]*\n#endif\n        \{\n            \*addr_ptr = base;', late) is not None,
      'the stores after a successful change are skipped when the early store ran')
check('if (ios_wc_old_prot_enabled() && !ios_ntprotect_early_stored)' in late, 'so is the WRITECOPY rewrite')

model = r'''
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <signal.h>
#include <setjmp.h>
#include <unistd.h>
#include <sys/mman.h>
static sigjmp_buf jb;
static void on_fault(int s) { (void)s; siglongjmp(jb, 1); }
/* the order of the production function: [early store] -> change protection -> [late store unless early] */
static int call(char *base, size_t size, unsigned *old_prot, int early_on)
{
    int early = 0;
    if (early_on && (uintptr_t)old_prot >= (uintptr_t)base && (uintptr_t)old_prot < (uintptr_t)base + size) { *old_prot = 4; early = 1; }
    mprotect(base, size, PROT_READ);                    /* the change drops WRITE */
    if (!early) *old_prot = 4;
    return 0;
}
int main(void)
{
    long pg = sysconf(_SC_PAGESIZE);
    struct sigaction sa; memset(&sa, 0, sizeof sa); sa.sa_handler = on_fault;
    sigaction(SIGBUS, &sa, NULL); sigaction(SIGSEGV, &sa, NULL);
    for (int early_on = 0; early_on < 2; early_on++)
    {
        char *p = mmap(NULL, pg, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        unsigned *var = (unsigned *)(p + pg - 8);       /* OldProtect inside the page being protected (BASS.dll's last page) */
        unsigned outside = 0;
        if (sigsetjmp(jb, 1)) { printf("%s fault\n", early_on ? "early" : "old"); munmap(p, pg); continue; }
        call(p, pg, var, early_on);
        call(p, pg, &outside, early_on);                /* a variable outside the range: the late store, as always */
        printf("%s ok %u %u\n", early_on ? "early" : "old", *var, outside);
        munmap(p, pg);
    }
    return 0;
}
'''
cc = shutil.which('clang') or shutil.which('cc')
with tempfile.TemporaryDirectory() as tmp:
    c, exe = Path(tmp) / 'm.c', Path(tmp) / 'm'
    c.write_text(model)
    subprocess.run([cc, '-std=c11', '-D_DARWIN_C_SOURCE', '-O0', str(c), '-o', str(exe)], check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20).stdout
check('old fault' in out, 'model: without the early store the late store faults on the now read-only page')
check('early ok 4 4' in out, 'model: with it, both the inside and the outside variable get the old protection')
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('run: python3 tests/host/check-ntprotect-early-store.py' in wf, 'CI runs this check')
print('PASS: NtProtect early store' if not failures else f'FAILED: {failures}')
sys.exit(1 if failures else 0)
