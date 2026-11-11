#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# Copyright 2026 Taala2 (bcd-games#33)
# Modified by BCD (thebcd69), 2026-11-11: written for the keyed MADEIRA_TLS_DIAG form of that pull request.
"""The helpers of build/ntdll-unix/ios_tls_diag_util.h on the host: the switch, the fault-safe read, the allocation bit of a TLS index and the
walk of a PEB's module list for an ASCII name (the PEB / LDR layouts of x64 Windows, which Wine's headers have at the same offsets:
PEB.LdrData 0x18, TlsBitmapBits 0x80, TlsExpansionBitmapBits 0x240, PEB_LDR_DATA.InLoadOrderModuleList 0x10, LDR_DATA_TABLE_ENTRY.DllBase 0x30,
SizeOfImage 0x40, BaseDllName 0x58). The Mach read is replaced by a read that succeeds only inside registered buffers, so every failure path
of the real helpers is exercised; nothing here touches a real process."""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
header = root / 'build/ntdll-unix/ios_tls_diag_util.h'
text = header.read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


check('the switch is exactly "1", read once, default off', 'e && e[0] == \'1\' && !e[1]' in text and 'getenv( "MADEIRA_TLS_DIAG" )' in text
      and 'static int mode = -1;' in text)
check('the helpers write nothing (no store through a pointer that is not their own local)',
      not any(w in text for w in ('mach_vm_write', 'vm_protect', 'memcpy(', 'mmap(')))

HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
typedef unsigned int ULONG;
typedef unsigned short WCHAR;
/* x64 Windows layout (verified against Wine's winternl.h): only the fields the helpers name */
typedef struct { char p0[0x18]; uint64_t LdrData; char p1[0x80 - 0x20]; ULONG TlsBitmapBits[2]; char p2[0x240 - 0x88]; ULONG TlsExpansionBitmapBits[32]; } PEB;
_Static_assert(offsetof(PEB, LdrData) == 0x18 && offsetof(PEB, TlsBitmapBits) == 0x80 && offsetof(PEB, TlsExpansionBitmapBits) == 0x240, "PEB layout");
typedef unsigned long mach_vm_size_t;
typedef unsigned long long mach_vm_address_t;
#define KERN_SUCCESS 0
#define mach_task_self() 0
#ifndef CLOCK_UPTIME_RAW
#define CLOCK_UPTIME_RAW 8
#endif
#undef clock_gettime_nsec_np
#define clock_gettime_nsec_np(c) ((unsigned long long)(c) * 0 + 5ULL)
/* mapped regions: a read inside one of them succeeds, any other fails (as mach_vm_read_overwrite does for unmapped memory) */
static struct { uintptr_t lo, hi; } reg[64]; static int nreg; static int reads;
static void map(const void *p, size_t n) { reg[nreg].lo = (uintptr_t)p; reg[nreg].hi = (uintptr_t)p + n; nreg++; }
static int fake_read(uintptr_t a, size_t n, uintptr_t buf, mach_vm_size_t *got)
{
    reads++;
    for (int i = 0; i < nreg; i++) if (a >= reg[i].lo && a + n <= reg[i].hi) { memcpy((void *)buf, (void *)a, n); *got = n; return 0; }
    return 1;
}
#define mach_vm_read_overwrite(task, addr, len, buf, got) ((void)(task), fake_read((uintptr_t)(addr), (len), (uintptr_t)(buf), (got)))
unsigned long long ios_tls_diag_seq_counter;
#include "ios_tls_diag_util.h"

/* one fake module entry: LDR_DATA_TABLE_ENTRY bytes with the offsets above, and its name in UTF-16 */
struct ent { uint64_t next; char pad[0x28]; uint64_t base; char pad2[0x8]; uint32_t size; char pad3[0x14]; uint16_t nlen, nmax; uint32_t pad4; uint64_t nbuf; };
_Static_assert(offsetof(struct ent, base) == 0x30 && offsetof(struct ent, size) == 0x40 && offsetof(struct ent, nlen) == 0x58 && offsetof(struct ent, nbuf) == 0x60, "LDR entry layout");
static struct ent ents[600]; static WCHAR names[600][20];
static struct { char hdr[0x10]; uint64_t head_next, head_prev; } ldr;
static PEB peb;

static void build(int n, const char **mods, uint64_t *bases, uint32_t *sizes)
{
    memset(&ldr, 0, sizeof(ldr)); memset(&peb, 0, sizeof(peb)); memset(ents, 0, sizeof(ents)); nreg = 0;
    peb.LdrData = (uint64_t)(uintptr_t)&ldr;
    uint64_t head = (uint64_t)(uintptr_t)&ldr.head_next;     /* ldr + 0x10 */
    uint64_t prev = head;
    for (int i = 0; i < n; i++)
    {
        for (int k = 0; mods[i][k]; k++) names[i][k] = mods[i][k], names[i][k + 1] = 0;
        ents[i].base = bases[i]; ents[i].size = sizes[i]; ents[i].nlen = 2 * strlen(mods[i]); ents[i].nmax = ents[i].nlen + 2; ents[i].nbuf = (uint64_t)(uintptr_t)names[i];
        if (i == 0) ldr.head_next = (uint64_t)(uintptr_t)&ents[i]; else ents[i - 1].next = (uint64_t)(uintptr_t)&ents[i];
        (void)prev;
        map(&ents[i], sizeof(ents[i])); map(names[i], sizeof(names[i]));
    }
    if (n) ents[n - 1].next = head; else ldr.head_next = head;
    map(&peb, sizeof(peb)); map(&ldr, sizeof(ldr));
}

int main(void)
{
    uint64_t base = 0; uint32_t size = 0;
    const char *m1[] = { "game.exe", "ntdll.dll", "NW.DLL", "kernel32.dll" };
    uint64_t b1[] = { 0x140000000ULL, 0x180000000ULL, 0x7ff600000000ULL, 0x1a0000000ULL };
    uint32_t s1[] = { 0x1000, 0x2000, 0x89B7000, 0x3000 };
    build(4, m1, b1, s1);
    assert(ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb, "nw.dll", &base, &size) == 1 && base == 0x7ff600000000ULL && size == 0x89B7000);
    puts("found: nw.dll, case-insensitive, base and size returned");
    assert(ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb, "nw.dll", NULL, NULL) == 1);
    puts("found: NULL out-pointers are allowed");
    assert(ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb, "nw_elf.dll", &base, &size) == 0);
    puts("absent: 0 (list walked)");
    assert(ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb, "nw.dl", &base, &size) == 0 && ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb, "nw.dllx", &base, &size) == 0);
    puts("prefix and suffix of a name do not match");
    assert(ios_tls_diag_find_module(0, "nw.dll", &base, &size) == -1 && ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb + 1, "nw.dll", &base, &size) == -1);
    assert(ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb, "a_name_that_is_far_too_long.dll", &base, &size) == -1);
    puts("NULL / unaligned PEB and an over-long name: -1");
    peb.LdrData = 0; assert(ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb, "nw.dll", &base, &size) == -1);
    puts("no loader data: -1");
    build(4, m1, b1, s1); ents[1].nbuf = 0x1234; /* an unreadable name buffer is skipped, the walk goes on */
    assert(ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb, "nw.dll", &base, &size) == 1);
    puts("an unreadable name buffer is skipped");
    build(4, m1, b1, s1); ents[1].next = 0xdead0000;        /* a link into unmapped memory ends the walk with -1, no fault */
    assert(ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb, "nw.dll", &base, &size) == -1);
    puts("a broken link: -1");
    /* a loop that never reaches the head is cut at 512 entries */
    build(4, m1, b1, s1); ents[3].next = (uint64_t)(uintptr_t)&ents[0]; reads = 0;
    assert(ios_tls_diag_find_module((uint64_t)(uintptr_t)&peb, "zzz.dll", &base, &size) == 0 && reads < 512 * 8);
    puts("a cyclic list stops after the guard");
    /* the allocation bit */
    peb.TlsBitmapBits[0] = 1u << 5; peb.TlsBitmapBits[1] = 1u << 3; peb.TlsExpansionBitmapBits[2] = 1u << 7;
    uint64_t pa = (uint64_t)(uintptr_t)&peb;
    assert(ios_tls_diag_peb_bit(pa, 5) == 1 && ios_tls_diag_peb_bit(pa, 6) == 0 && ios_tls_diag_peb_bit(pa, 35) == 1 && ios_tls_diag_peb_bit(pa, 63) == 0);
    assert(ios_tls_diag_peb_bit(pa, 64 + 64 + 7) == 1 && ios_tls_diag_peb_bit(pa, 64 + 64 + 8) == 0 && ios_tls_diag_peb_bit(pa, 64 + 1023) == 0);
    assert(ios_tls_diag_peb_bit(pa, 64 + 1024) == -1 && ios_tls_diag_peb_bit(0, 5) == -1 && ios_tls_diag_peb_bit(pa + 4, 5) == -1);
    puts("allocation bit: inline and expansion bitmaps, out of range and unknown PEB give -1");
    /* the read */
    uint32_t v; static char buf[16] = { 1, 2, 3, 4 }; map(buf, sizeof buf);
    assert(ios_tls_diag_read((uint64_t)(uintptr_t)buf, &v, 4) && v == 0x04030201);
    assert(!ios_tls_diag_read(0x8000, &v, 4) && !ios_tls_diag_read(0xFFFFFFFFFFFFFFFCULL, &v, 8) && !ios_tls_diag_read((uint64_t)(uintptr_t)buf, &v, 0));
    assert(!ios_tls_diag_read(0x800000000000ULL - 2, &v, 4) && !ios_tls_diag_read((uint64_t)(uintptr_t)buf + 14, &v, 4));
    puts("read: success inside a mapping; below 0x10000, wrap-around, 47-bit limit, zero length and a read past the mapping all fail");
    /* the sequence */
    assert(ios_tls_diag_seq() == 1 && ios_tls_diag_seq() == 2 && ios_tls_diag_now_ns() == 5);
    puts("sequence counts from 1; the clock goes through clock_gettime_nsec_np");
    puts("PASS");
    return 0;
}
'''

cc = os.environ.get('CC', 'cc')
if not shutil.which(cc):
    print('skip the compiled run: no C compiler')
else:
    with tempfile.TemporaryDirectory(prefix='tlsdiag-') as d:
        d = Path(d)
        (d / 'h.c').write_text(HARNESS)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function', f'-I{header.parent}',
                            str(d / 'h.c'), '-o', str(d / 'h')], capture_output=True, text=True)
        check('the production header compiles against the harness (host cc, -Wall -Wextra -Werror)', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            env = {k: v for k, v in os.environ.items() if k != 'MADEIRA_TLS_DIAG'}
            r = subprocess.run([str(d / 'h')], capture_output=True, text=True, env=env)
            check('the helper runs pass (find_module, peb_bit, read, seq)', r.returncode == 0 and r.stdout.rstrip().endswith('PASS'), r.stdout + r.stderr)
            for line in r.stdout.splitlines()[:-1]:
                print('     ' + line)
            for val, want in (('1', 1), ('0', 0), ('11', 0), ('true', 0), ('', 0)):
                (d / 'sw.c').write_text(HARNESS.split('int main(void)')[0] + 'int main(void){ printf("%d", ios_tls_diag_enabled()); return 0; }\n')
                subprocess.run([cc, '-std=gnu11', '-w', f'-I{header.parent}', str(d / 'sw.c'), '-o', str(d / 'sw')], check=True)
                e = dict(env); e['MADEIRA_TLS_DIAG'] = val
                out = subprocess.run([str(d / 'sw')], capture_output=True, text=True, env=e).stdout
                check('MADEIRA_TLS_DIAG=%r gives %d' % (val, want), out == str(want), out)
            out = subprocess.run([str(d / 'sw')], capture_output=True, text=True, env=env).stdout
            check('MADEIRA_TLS_DIAG unset gives 0', out == '0', out)
sys.exit(0 if ok else 1)
