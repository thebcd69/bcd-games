#!/usr/bin/env python3
"""Chromium's out-of-memory exit names the failed request's size at
NtTerminateProcess; no Wine runs.

SocialClubHelper.exe raised 0xE0000008 after 24 minutes of GTA V Enhanced (log
2026-10-08 13:39:08, build 451) and the game quit with it. The exception is
dispatched in the PE ntdll and the unhandled-exception filter calls
NtTerminateProcess, so the [chromium-oom] line in NtRaiseException never ran.
build/ntdll-unix/process_ios.c's ios_term_oom_record looks for the
EXCEPTION_RECORD on the thread's stack instead. Compiles it with a fake TEB and
a fake mach_vm_read_overwrite that reads this process's memory, and checks:
  - a record anywhere in the top 256 KB of the stack, also across the edge of
    its 64 KB read chunks, is found and printed with the request size and the
    two page file figures;
  - a record lookalike with a nested record, without EXCEPTION_NONCONTINUABLE
    or with 0 parameters is skipped; a stack without a record says so;
  - an unreadable chunk is skipped, not fatal;
and textually that NtTerminateProcess calls it for exit code 0xE0000008 of the
current process only, before the capped [term-stack] dump.

Also compiles virtual_ios.c's ios_sc2_note_growth, which says in 256 MB steps
how far the helper has committed into each of its large grants (13:56 in that
log: a commit past chrome_elf.dll's real 4 GB pool), and checks: a line at each
new 256 MB step and none below it or for a lower commit; a commit past the real
part reported with the real and reported sizes; the metadata regions left out;
a restarted helper (new PEB) counted from 0 again; and textually that
ios_sc2_note_commit feeds it every commit inside a grant.
Needs python3 and a C compiler.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/process_ios.c').read_text()

start = source.index('static void ios_term_oom_record( TEB *teb )')
function = source[start:source.index('\n}\n', start) + 3]

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
typedef void *PVOID;
typedef uintptr_t ULONG_PTR;
typedef long LONG;
typedef struct { PVOID ExceptionList, StackBase, StackLimit; } NT_TIB;
typedef struct { NT_TIB Tib; } TEB;
typedef uint64_t mach_vm_address_t, mach_vm_size_t;
typedef int kern_return_t;
#define KERN_SUCCESS 0
static int mach_task_self(void) { return 0; }
static uint64_t bad_lo, bad_hi;   /* an unreadable range */
static kern_return_t mach_vm_read_overwrite(int task, mach_vm_address_t a, mach_vm_size_t n, mach_vm_address_t to, mach_vm_size_t *got)
{
    (void)task;
    if (a < bad_hi && a + n > bad_lo) return 1;
    memcpy((void *)(uintptr_t)to, (void *)(uintptr_t)a, n);
    *got = n;
    return KERN_SUCCESS;
}
static LONG InterlockedIncrement(LONG *p) { return ++*p; }
static char out[1 << 16];
static size_t outn;
static int fake_dprintf(int fd, const char *f, ...)
{
    va_list ap; int w;
    (void)fd;
    va_start(ap, f);
    w = vsnprintf(out + outn, sizeof out - outn, f, ap);
    va_end(ap);
    if (w > 0) outn += (size_t)w;
    return w;
}
#define dprintf fake_dprintf
''' + function + r'''
static void put(uint64_t *q, uint32_t code, uint32_t flags, uint64_t nested, uint64_t np, uint64_t a0)
{
    q[0] = code | ((uint64_t)flags << 32);
    q[1] = nested;
    q[2] = 0x154440110ull;   /* the address in the 13:39 log */
    q[3] = np;
    q[4] = a0;
    q[5] = 3ull << 30;
    q[6] = 12ull << 30;
}
int main(void)
{
    size_t words = (1 << 20) / 8;   /* a 1 MB stack */
    uint64_t *stack = aligned_alloc(4096, words * 8);
    TEB teb;
    uint64_t base = (uint64_t)(uintptr_t)stack, top = base + words * 8, lo = top - 0x40000;
    size_t at;

    teb.Tib.StackLimit = stack;
    teb.Tib.StackBase = (PVOID)(uintptr_t)top;

    /* 1: lookalikes first (nested record, continuable, no parameters), then the record */
    memset(stack, 0, words * 8);
    put(stack + words - 2000, 0xE0000008u, 1, 0x1234, 3, 0x111);
    put(stack + words - 1900, 0xE0000008u, 0, 0, 3, 0x222);
    put(stack + words - 1800, 0xE0000008u, 1, 0, 0, 0x333);
    put(stack + words - 1200, 0xE0000008u, 1, 0, 3, 0x200000);
    outn = 0; out[0] = 0;
    ios_term_oom_record(&teb);
    printf("%s", out);
    if (!strstr(out, "3 parameter(s): request 0x200000 bytes (2 MB), 3072 MB, 12288 MB")
        || !strstr(out, "raised at 0x154440110")) { printf("FAIL: the record in the stack's top was not reported\n"); return 1; }
    {   /* round 624: count the reported records instead of looking for "0x111" anywhere: the stack's own address can hold
         * it (aligned_alloc gave 0x1112c2000 once, and the check failed on a correct line) */
        int records = 0;
        const char *s = out;
        while ((s = strstr(s, "record at 0x"))) { records++; s++; }
        if (records != 1 || strstr(out, "request 0x111 ") || strstr(out, "request 0x222 ") || strstr(out, "request 0x333 "))
        { printf("FAIL: a lookalike was reported\n"); return 1; }
    }

    /* 2: a record across a chunk edge: the first chunk ends at lo + 64 KB */
    memset(stack, 0, words * 8);
    at = (size_t)((lo - base) / 8) + 8192 - 6;   /* starts 6 qwords before the edge */
    put(stack + at, 0xE0000008u, 1, 0, 3, 0x4000000);
    outn = 0; out[0] = 0;
    ios_term_oom_record(&teb);
    printf("%s", out);
    if (!strstr(out, "request 0x4000000 bytes (64 MB)")) { printf("FAIL: a record across a chunk edge was missed\n"); return 1; }

    /* 3: one record, the chunk before it unreadable */
    memset(stack, 0, words * 8);
    put(stack + words - 300, 0xE0000008u, 1, 0, 1, 0x10000);
    bad_lo = lo; bad_hi = lo + 0x10000;
    outn = 0; out[0] = 0;
    ios_term_oom_record(&teb);
    printf("%s", out);
    if (!strstr(out, "1 parameter(s): request 0x10000 bytes (0 MB)\n")) { printf("FAIL: an unreadable chunk stopped the scan\n"); return 1; }
    bad_lo = bad_hi = 0;

    /* 4: no record */
    memset(stack, 0, words * 8);
    outn = 0; out[0] = 0;
    ios_term_oom_record(&teb);
    printf("%s", out);
    if (!strstr(out, "no 0xE0000008 record")) { printf("FAIL: a stack without a record was not reported as such\n"); return 1; }
    printf("PASS: the record is found in the top 256 KB (across chunk edges and past an unreadable chunk), lookalikes are skipped, a missing record is said\n");
    free(stack);
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-oom-exit-') as directory:
    c = Path(directory) / 'oom.c'
    exe = Path(directory) / 'oom'
    c.write_text(harness)
    build = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined',
                            '-o', str(exe), str(c)], capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    print(run.stdout, end='')
    assert run.returncode == 0, run.stdout + run.stderr

term = source[source.index('NTSTATUS WINAPI NtTerminateProcess( HANDLE handle, LONG exit_code )'):]
term = term[:term.index('static int term_log_count')]
assert ('if ((unsigned int)exit_code == 0xE0000008u && handle == NtCurrentProcess())\n'
        '        ios_term_oom_record( NtCurrentTeb() );') in term, \
    'NtTerminateProcess must look for the record before the capped [term-stack] dump'
print('PASS: NtTerminateProcess looks for the record for exit code 0xE0000008 of the current process, before the capped dump')

# --- layout 2 grant growth ------------------------------------------------------
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
start = native.index('static void ios_sc2_note_growth( uint64_t a, uint64_t size, const struct ios_sc2_gv *gv, void *peb )')
growth = native[start:native.index('\n}\n', start) + 3]
enums = native[native.index('enum { IOS_SC2_NONE,'):]
enums = enums[:enums.index('\n', enums.index('enum { IOS_SC_K_V1')) + 1]
gv = native[native.index('struct ios_sc2_gv {'):]
gv = gv[:gv.index('};') + 2] + '\n'
harness2 = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
""" + enums + gv + r"""
static const char *ios_sc_kind_what(int kind) { return kind == IOS_SC2_E ? "chrome_elf.dll's PartitionAlloc pools" : "another grant"; }
static char out[1 << 16];
static size_t outn;
static int fake_dprintf(int fd, const char *f, ...)
{
    va_list ap; int w;
    (void)fd;
    va_start(ap, f);
    w = vsnprintf(out + outn, sizeof out - outn, f, ap);
    va_end(ap);
    if (w > 0) outn += (size_t)w;
    return w;
}
#define dprintf fake_dprintf
""" + growth + r"""
#define MB (1ull << 20)
int main(void)
{
    struct ios_sc2_gv e = { 0x7800000000ull, 4096 * MB, 0x7800000000ull, 32768 * MB, IOS_SC2_E };
    struct ios_sc2_gv j = { 0x7400000000ull, 8192 * MB, 0x7400000000ull, 16384 * MB, IOS_SC2_J2 };
    int a = 1, b = 2;
    ios_sc2_note_growth(e.view + 10 * MB, 2 * MB, &e, &a);
    ios_sc2_note_growth(e.view + 200 * MB, 2 * MB, &e, &a);
    if (outn) { printf("FAIL: a line below the first 256 MB step: %s", out); return 1; }
    ios_sc2_note_growth(e.view + 300 * MB, 2 * MB, &e, &a);
    ios_sc2_note_growth(e.view + 280 * MB, 2 * MB, &e, &a);
    ios_sc2_note_growth(e.view + 400 * MB, 2 * MB, &e, &a);
    ios_sc2_note_growth(e.view + 600 * MB, 2 * MB, &e, &a);
    ios_sc2_note_growth(j.view + 900 * MB, 4096, &j, &a);
    ios_sc2_note_growth(0x794fe00000ull, 0x410000, &e, &a);   /* the 13:56 commit */
    ios_sc2_note_growth(e.view + 300 * MB, 2 * MB, &e, &b);   /* a restarted helper */
    printf("%s", out);
    if (strcmp(out,
        "[sc-cef] layout 2: SocialClubHelper.exe has committed chrome_elf.dll's PartitionAlloc pools up to +302 MB (4096 MB real of 32768 MB reported)\n"
        "[sc-cef] layout 2: SocialClubHelper.exe has committed chrome_elf.dll's PartitionAlloc pools up to +602 MB (4096 MB real of 32768 MB reported)\n"
        "[sc-cef] layout 2: SocialClubHelper.exe has committed chrome_elf.dll's PartitionAlloc pools up to +5378 MB (4096 MB real of 32768 MB reported)\n"
        "[sc-cef] layout 2: SocialClubHelper.exe has committed chrome_elf.dll's PartitionAlloc pools up to +302 MB (4096 MB real of 32768 MB reported)\n"))
    { printf("FAIL: unexpected growth lines\n"); return 1; }
    printf("PASS: growth lines at each new 256 MB step only, past the real part too, metadata left out, a restarted helper counted again\n");
    return 0;
}
"""
with tempfile.TemporaryDirectory(prefix='madeira-sc-growth-') as directory:
    c = Path(directory) / 'growth.c'
    exe = Path(directory) / 'growth'
    c.write_text(harness2)
    build = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined',
                            '-o', str(exe), str(c)], capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    print(run.stdout, end='')
    assert run.returncode == 0, run.stdout + run.stderr

note = native[native.index('static void ios_sc2_note_commit( void *addr, SIZE_T size )'):]
note = note[:note.index('\n}\n')]
assert ('if (gi >= 0 && (c != IOS_SC2_C_OK || a - g[gi].view < g[gi].real))   /* a commit in this grant */\n'
        '        ios_sc2_note_growth( a, size, &g[gi], peb );') in note, 'ios_sc2_note_commit must feed the growth lines'
assert 'int i, n = 0, gi = -1, c, owner;' in note, 'gi must start at -1 so a commit outside every grant is not counted'
print('PASS: ios_sc2_note_commit feeds every commit inside a grant to the growth lines')
