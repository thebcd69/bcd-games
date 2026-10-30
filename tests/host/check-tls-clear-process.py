#!/usr/bin/env python3
"""TlsFree across pseudo-processes: the [tls-cross-clear] line and the opt-in MADEIRA_TLS_CLEAR_OWN_PROCESS switch (ios_tls_clear.h,
virtual_clear_tls_index in virtual_ios.c); no Wine runs.

Batman: Arkham City GOTY (Steam 200260, build 578, C7DGSN): BatmanAC.exe starts BmLauncher.exe, a .NET Framework program run by Wine Mono,
and the launcher dies in libmono (EIP libmono+0x2135C9, `mov dword [ebx+0x10c], 1`, EBX = 0): Mono's inlined TlsGetValue
(mono_native_tls_get_value, TEB->TlsSlots[key]) returned NULL for its SGen thread info after the same thread had attached itself,
while the parent was tearing down. virtual_clear_tls_index (the unix side of TlsFree) walks teb_list, which on iOS holds the threads
of EVERY pseudo-process of the session, and clears cell `index` in all of them. NOT PROVEN to be the cause (the log has no TlsFree
line); this check holds the two things that were added: the log line for it, and the keyed switch, and that with the switch off the
function does exactly what it did.

Compiled runs (macOS): the production header and the production function (extracted from virtual_ios.c) are compiled against fake
TEBs, next to the function as it was before (ORIGINAL below, from the base commit), and run on the same lists of threads:
  - switch off (default): the cells end up exactly as the original leaves them, for every shape (64-bit and 32-bit TEBs of the caller's
    process and of two other processes, the 64 inline cells and the expansion cells, no caller PEB), and the foreign live cells are logged
    as CLEARED, the caller's own and the zero ones are not;
  - switch on ("1" exactly): the other processes' cells are kept and logged as SPARED; the caller's process is cleared as before;
  - the index range check and the return values are unchanged; the line count is capped.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
header_path = root / 'build/ntdll-unix/ios_tls_clear.h'
header = header_path.read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}\n', start) + 3]


fn = function(source, 'NTSTATUS virtual_clear_tls_index( ULONG index )')

# virtual_clear_tls_index as it was before ajan7-arkham (identical to upstream apart from the WINE_IOS slot base).
ORIGINAL = r'''
NTSTATUS virtual_clear_tls_index_original( ULONG index )
{
    struct ntdll_thread_data *thread_data;
    sigset_t sigset;

    if (index < TLS_MINIMUM_AVAILABLE)
    {
        server_enter_uninterrupted_section( &virtual_mutex, &sigset );
        LIST_FOR_EACH_ENTRY( thread_data, &teb_list, struct ntdll_thread_data, entry )
        {
            TEB *teb = CONTAINING_RECORD( thread_data, TEB, GdiTebBatch );
#ifdef _WIN64
            WOW_TEB *wow_teb = get_wow_teb( teb );
            if (wow_teb) wow_teb->TlsSlots[index] = 0;
            else
#endif
            teb->TlsSlots[index] = 0;
        }
        server_leave_uninterrupted_section( &virtual_mutex, &sigset );
    }
    else
    {
        index -= TLS_MINIMUM_AVAILABLE;
        if (index >= 8 * sizeof(peb->TlsExpansionBitmapBits)) return STATUS_INVALID_PARAMETER;

        server_enter_uninterrupted_section( &virtual_mutex, &sigset );
        LIST_FOR_EACH_ENTRY( thread_data, &teb_list, struct ntdll_thread_data, entry )
        {
            TEB *teb = CONTAINING_RECORD( thread_data, TEB, GdiTebBatch );
#ifdef _WIN64
            WOW_TEB *wow_teb = get_wow_teb( teb );
            if (wow_teb)
            {
                if (wow_teb->TlsExpansionSlots)
                {
                    ULONG_PTR slots = wow_teb->TlsExpansionSlots;
#ifdef WINE_IOS
                    /* a guest address in the window this TEB lives in */
                    struct ios_wow_window *slot = ios_wow_slot_at_base(
                        (ULONG_PTR)teb & ~(IOS_WOW_WINDOW_SIZE - 1) );
                    if (slot) slots += slot->base;
#endif
                    ((ULONG *)slots)[index] = 0;
                }
            }
            else
#endif
            if (teb->TlsExpansionSlots) teb->TlsExpansionSlots[index] = 0;
        }
        server_leave_uninterrupted_section( &virtual_mutex, &sigset );
    }
    return STATUS_SUCCESS;
}
'''

# ---------------------------------------------------------------- source shape
check('the header is included once, right before the function, which is the only user', source.count('#include "ios_tls_clear.h"') == 1
      and source.index('#include "ios_tls_clear.h"') < source.index('NTSTATUS virtual_clear_tls_index( ULONG index )')
      and source.count('ios_tls_clear_note(') == 4 + 0 and fn.count('ios_tls_clear_note(') == 4)
check('both loops (the 64 inline cells, the expansion cells) test foreign threads and skip them only under the switch',
      fn.count('int foreign = ios_tls_clear_is_foreign( teb->Peb, caller_peb );') == 2 and fn.count('if (own_only) continue;') == 4)
check('every cell the old function cleared is still cleared (the four assignments are all there)',
      'if (wow_teb) wow_teb->TlsSlots[index] = 0;' in fn and '\n            teb->TlsSlots[index] = 0;' in fn
      and '((ULONG *)slots)[index] = 0;' in fn and 'teb->TlsExpansionSlots[index] = 0;' in fn)
check('the caller is NtCurrentTeb() and the switch is read through the header only',
      'TEB *caller_teb = NtCurrentTeb();' in fn and 'int own_only = ios_tls_clear_own_process_only();' in fn)
forbidden = ('mmap(', 'mprotect', 'memset(', 'memcpy(', 'free(', 'malloc(', 'abort(', 'exit(', 'fopen(', 'open(', 'mach_vm_')
check('the header only reads the environment and prints', not [f for f in forbidden if f in header], str([f for f in forbidden if f in header]))
check('the switch is exactly "1"', 'e && e[0] == \'1\' && !e[1]' in header and '"MADEIRA_TLS_CLEAR_OWN_PROCESS"' in header)
check('an unknown PEB on either side is never foreign', 'return caller_peb && victim_peb && victim_peb != caller_peb;' in header)
check('capped at 40 lines then one closing line', '#define IOS_TLS_CROSS_LINES 40' in header and 'n > IOS_TLS_CROSS_LINES + 1' in header)

# ---------------------------------------------------------------- compiled runs
cc = os.environ.get('CC', 'cc')
HARNESS = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#define _WIN64 1
typedef unsigned int ULONG;
typedef unsigned long ULONG_PTR;
typedef void *HANDLE;
typedef int NTSTATUS;
#define STATUS_SUCCESS 0
#define STATUS_INVALID_PARAMETER ((NTSTATUS)0xC000000D)
#define TLS_MINIMUM_AVAILABLE 64
#define HandleToULong(h) ((ULONG)(ULONG_PTR)(h))
#define WINAPI
#include <time.h>
/* ios_tls_diag_util.h (included by the header): the PEB fields it reads, and fakes for the Mach read and the clock. The fake read is a
 * plain copy: every address the production code reads in this harness (the fake PEBs, the TEB cells) is a real object here, and a PEB's
 * LdrData is NULL, so the module walk ends at once ("nw.dll not found"). */
typedef struct { void *LdrData; ULONG TlsBitmapBits[2]; ULONG TlsExpansionBitmapBits[32]; } PEB;
typedef unsigned long mach_vm_size_t;
typedef unsigned long long mach_vm_address_t;
#define KERN_SUCCESS 0
#define mach_task_self() 0
#define mach_vm_read_overwrite(task, addr, len, buf, got) \
    ((void)(task), memcpy( (void *)(uintptr_t)(buf), (const void *)(uintptr_t)(addr), (len) ), *(got) = (len), 0)
#ifndef CLOCK_UPTIME_RAW
#define CLOCK_UPTIME_RAW 8
#endif
#define clock_gettime_nsec_np(clock) ((unsigned long long)(clock) * 0 + 1000000000ULL)
unsigned long long ios_tls_diag_seq_counter;   /* defined in virtual_ios.c, just above the function; the harness has only the function */
struct list { struct list *next, *prev; };
#define LIST_ENTRY(elem, type, field) ((type *)((char *)(elem) - offsetof(type, field)))
#define LIST_FOR_EACH_ENTRY(elem, list, type, field) \
    for ((elem) = LIST_ENTRY((list)->next, type, field); &(elem)->field != (list); (elem) = LIST_ENTRY((elem)->field.next, type, field))
#define CONTAINING_RECORD(addr, type, field) ((type *)((char *)(addr) - offsetof(type, field)))
struct ntdll_thread_data { struct list entry; };
typedef struct { ULONG TlsSlots[64]; ULONG_PTR TlsExpansionSlots; } WOW_TEB;   /* the 32-bit cells; the expansion pointer is a host pointer here */
typedef struct { void *UniqueProcess; HANDLE UniqueThread; } CLIENT_ID;
typedef struct _TEB
{
    void *Peb;
    CLIENT_ID ClientId;
    void *TlsSlots[64];
    void **TlsExpansionSlots;
    WOW_TEB *wow;
    struct ntdll_thread_data GdiTebBatch;
} TEB;
static WOW_TEB *get_wow_teb( TEB *teb ) { return teb->wow; }
static struct list teb_list = { &teb_list, &teb_list };
static int virtual_mutex;
#define server_enter_uninterrupted_section(m, s) ((void)(m), (void)(s))
#define server_leave_uninterrupted_section(m, s) ((void)(m), (void)(s))
static PEB fake_peb, *peb = &fake_peb;
static TEB *cur_teb;
#define NtCurrentTeb() (cur_teb)

#include "ios_tls_clear.h"
''' + '\n' + fn + '\n' + ORIGINAL + r'''

#define NT 5
static TEB tebs[NT];
static WOW_TEB wows[NT];
#define NEXP 1024
static void *exp64[NT][NEXP];
static ULONG exp32[NT][NEXP];
static PEB PA, PB, PC;   /* three processes: A (the caller's), B (32-bit), C (64-bit); real PEB objects, so the diagnostic reads of them are valid */

static void build(int caller_peb_null)
{
    /* 0: A's thread (64-bit TEB), the caller; 1: A's other thread; 2: B's thread (32-bit); 3: B's second thread; 4: C's thread (64-bit) */
    int procs[NT] = { 1, 1, 2, 2, 3 }, wow[NT] = { 0, 0, 1, 1, 0 };
    memset(tebs, 0, sizeof(tebs)); memset(wows, 0, sizeof(wows)); memset(exp64, 0, sizeof(exp64)); memset(exp32, 0, sizeof(exp32));
    teb_list.next = teb_list.prev = &teb_list;
    for (int i = NT - 1; i >= 0; i--)
    {
        TEB *t = &tebs[i];
        t->Peb = procs[i] == 1 ? (void *)&PA : procs[i] == 2 ? (void *)&PB : (void *)&PC;
        t->ClientId.UniqueThread = (HANDLE)(uintptr_t)(0x100 + 4 * i);
        for (int k = 0; k < 64; k++)
        {
            t->TlsSlots[k] = (void *)(uintptr_t)(0x1000000 * (i + 1) + k + 1);
            wows[i].TlsSlots[k] = 0x2000000 * (i + 1) + k + 1;
        }
        if (wow[i]) { t->wow = &wows[i]; wows[i].TlsExpansionSlots = (ULONG_PTR)exp32[i]; for (int k = 0; k < NEXP; k++) exp32[i][k] = 0x3000000 * (i + 1) + k + 1; }
        else { t->TlsExpansionSlots = exp64[i]; for (int k = 0; k < NEXP; k++) exp64[i][k] = (void *)(uintptr_t)(0x4000000 * (i + 1) + k + 1); }
        /* insert at the head, like list_add_head in init_teb */
        t->GdiTebBatch.entry.next = teb_list.next; t->GdiTebBatch.entry.prev = &teb_list;
        teb_list.next->prev = &t->GdiTebBatch.entry; teb_list.next = &t->GdiTebBatch.entry;
    }
    /* a zero cell in B's second thread and in C's thread for index 7 and expansion index 3 (70): nothing live to report there */
    wows[3].TlsSlots[7] = 0; tebs[4].TlsSlots[7] = 0; exp32[3][3] = 0; exp64[4][3] = 0;
    cur_teb = &tebs[0];
    if (caller_peb_null) { static TEB nopeb; nopeb = tebs[0]; nopeb.Peb = NULL; cur_teb = &nopeb; }   /* a copy of the caller's TEB whose PEB is unknown */
}

static void dump(void)
{
    uint64_t h = 1469598103934665603ULL;
    #define MIX(v) do { h ^= (uint64_t)(v); h *= 1099511628211ULL; } while (0)
    for (int i = 0; i < NT; i++)
    {
        for (int k = 0; k < 64; k++) { MIX((uintptr_t)tebs[i].TlsSlots[k]); MIX(wows[i].TlsSlots[k]); }
        for (int k = 0; k < NEXP; k++) { MIX((uintptr_t)exp64[i][k]); MIX(exp32[i][k]); }
    }
    printf("state=%016llx\n", (unsigned long long)h);
    for (int i = 0; i < NT; i++)
        printf("t%d cell5=%lx cell7=%lx exp3=%lx exp9=%lx\n", i,
               tebs[i].wow ? (unsigned long)wows[i].TlsSlots[5] : (unsigned long)(uintptr_t)tebs[i].TlsSlots[5],
               tebs[i].wow ? (unsigned long)wows[i].TlsSlots[7] : (unsigned long)(uintptr_t)tebs[i].TlsSlots[7],
               tebs[i].wow ? (unsigned long)exp32[i][3] : (unsigned long)(uintptr_t)exp64[i][3],
               tebs[i].wow ? (unsigned long)exp32[i][9] : (unsigned long)(uintptr_t)exp64[i][9]);
}

int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "new";
    int null_peb = argc > 2 && !strcmp(argv[2], "nopeb");
    unsigned idx[] = { 5, 7, 64 + 3, 64 + 9, 64 + 1100, 64 + 255 };
    int rets[8], n = 0;
    build(null_peb);
    for (unsigned k = 0; k < sizeof(idx) / sizeof(idx[0]); k++)
    {
        NTSTATUS r = !strcmp(mode, "orig") ? virtual_clear_tls_index_original( idx[k] ) : virtual_clear_tls_index( idx[k] );
        rets[n++] = r;
    }
    printf("rets=");
    for (int i = 0; i < n; i++) printf("%x,", (unsigned)rets[i]);
    printf("\n");
    dump();
    return 0;
}
'''


def run(binary, *args, env=None):
    e = dict(os.environ)
    e.pop('MADEIRA_TLS_CLEAR_OWN_PROCESS', None)
    e.update(env or {})
    return subprocess.run([str(binary), *args], capture_output=True, text=True, env=e)


if not shutil.which(cc):
    print('skip the compiled runs: no C compiler')
else:
    with tempfile.TemporaryDirectory(prefix='tlsclear-') as d:
        d = Path(d)
        (d / 'h.c').write_text(HARNESS)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function', '-Wno-unused-variable',
                            '-Wno-unused-but-set-variable', f'-I{header_path.parent}', str(d / 'h.c'), '-o', str(d / 'h')], capture_output=True, text=True)
        check('the production function and header compile (host cc, -Wall -Wextra -Werror)', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            h = d / 'h'
            orig = run(h, 'orig')
            default = run(h, 'new')
            on = run(h, 'new', env={'MADEIRA_TLS_CLEAR_OWN_PROCESS': '1'})
            ostate = [l for l in orig.stdout.splitlines() if l.startswith('state=')]
            check('the harness runs', orig.returncode == 0 and default.returncode == 0 and on.returncode == 0, orig.stderr + default.stderr + on.stderr)
            check('switch off: every cell of every thread ends exactly as the original function leaves it, and the same return values',
                  orig.stdout == default.stdout and 'rets=0,0,0,0,c000000d,0,' in default.stdout, orig.stdout + '\n' + default.stdout)
            check('the original run prints no line (it has none)', orig.stderr == '', orig.stderr)
            lines = [l for l in default.stderr.splitlines() if l.startswith('[tls-cross-clear] seq=') and ' rev=ajan7-arkham ' in l]
            # the threads: tid 0100 (the caller) and 0104 are process A's; 0108 and 010c are B's (32-bit TEBs); 0110 is C's (64-bit)
            check('switch off: a live cell of another process is logged as CLEARED, with the index, both thread ids, the PEBs, the width and the old value',
                  any('TlsFree(5) by tid 0100' in l and 'CLEARED' in l and 'tid 0108' in l and '32-bit' in l and 'was 0x6000006' in l for l in lines)
                  and any('TlsFree(5)' in l and 'tid 0110' in l and '64-bit' in l and 'was 0x5000006' in l for l in lines), default.stderr)
            check('switch off: nothing is logged for the caller\'s own process or for a cell that was zero',
                  not any('tid 0104' in l.split('live cell of')[1] for l in lines) and not any('TlsFree(7)' in l and ('tid 010c' in l.split('live cell of')[1] or 'tid 0110' in l.split('live cell of')[1]) for l in lines)
                  and not any('SPARED' in l for l in lines), default.stderr)
            check('switch off: the expansion cells are logged under their full index (64 + n)', any('TlsFree(67)' in l for l in lines) and any('TlsFree(73)' in l for l in lines), default.stderr)
            lines_on = [l for l in on.stderr.splitlines() if l.startswith('[tls-cross-clear] seq=') and ' rev=ajan7-arkham ' in l]
            check('switch on: the same events are logged as SPARED, none as CLEARED', lines_on and all('SPARED' in l for l in lines_on) and len(lines_on) == len(lines), on.stderr)
            # [TLS_CLEAR_DONE] is written after the store, once per CLEARED line, and reads the cell back; a SPARED cell is never written
            done = [l for l in default.stderr.splitlines() if l.startswith('[TLS_CLEAR_DONE] seq=')]
            cleared = [l for l in lines if ' CLEARED ' in l]
            check('switch off: each CLEARED line is followed by one [TLS_CLEAR_DONE] (note_seq = its seq) that reads the cell back as zero',
                  cleared and len(done) == len(cleared) and all(('note_seq=%s ' % c.split()[1].split('=')[1]) in d and '(zero)' in d
                                                                for c, d in zip(cleared, done)), default.stderr)
            check('switch on: nothing is written, so no [TLS_CLEAR_DONE]', '[TLS_CLEAR_DONE]' not in on.stderr, on.stderr)
            c5 = {l.split()[0]: l for l in on.stdout.splitlines() if l.startswith('t')}
            # tebs: t0, t1 = process A; t2, t3 = B; t4 = C. cells for index 5 / 7 and expansion 3 / 9 after the five calls
            check('switch on: the caller\'s process (both threads) is cleared as before',
                  'cell5=0 cell7=0 exp3=0 exp9=0' in c5['t0'] and 'cell5=0 cell7=0 exp3=0 exp9=0' in c5['t1'], on.stdout)
            check('switch on: the other two processes keep every cell (the 32-bit one and the 64-bit one)',
                  'cell5=0 ' not in c5['t2'] and 'cell5=0 ' not in c5['t3'] and 'cell5=0 ' not in c5['t4'] and 'exp9=0' not in c5['t2'] and 'exp9=0' not in c5['t4'], on.stdout)
            check('switch on: the return values are the same', 'rets=0,0,0,0,c000000d,0,' in on.stdout, on.stdout)
            for val in ('0', '11', 'true', '', 'yes'):
                r = run(h, 'new', env={'MADEIRA_TLS_CLEAR_OWN_PROCESS': val})
                check('MADEIRA_TLS_CLEAR_OWN_PROCESS=%r is not the switch: the original behaviour' % val, r.stdout == orig.stdout and 'SPARED' not in r.stderr, r.stdout)
            nopeb = run(h, 'new', 'nopeb', env={'MADEIRA_TLS_CLEAR_OWN_PROCESS': '1'})
            nopeb_orig = run(h, 'orig', 'nopeb')
            check('a caller whose PEB is unknown clears everything as before, even with the switch on, and logs nothing',
                  nopeb.stdout == nopeb_orig.stdout and '[tls-cross-clear]' not in nopeb.stderr, nopeb.stdout + nopeb.stderr)

            # the cap: a second harness that frees many indices
            cap_src = HARNESS.replace('unsigned idx[] = { 5, 7, 64 + 3, 64 + 9, 64 + 1100, 64 + 255 };', 'unsigned idx[64]; for (int q = 0; q < 64; q++) idx[q] = q;').replace('int rets[8], n = 0;', 'int rets[80], n = 0;')
            (d / 'cap.c').write_text(cap_src)
            cc2 = subprocess.run([cc, '-std=gnu11', '-w', f'-I{header_path.parent}', str(d / 'cap.c'), '-o', str(d / 'cap')], capture_output=True, text=True)
            check('the cap harness compiles', cc2.returncode == 0, cc2.stderr[-2000:])
            if cc2.returncode == 0:
                r = run(d / 'cap', 'new')
                cl = [l for l in r.stderr.splitlines() if l.startswith('[tls-cross-clear]')]
                check('capped: 40 event lines, then exactly one closing line, then silence', len(cl) == 41 and 'the rest of this run' in cl[-1]
                      and len([l for l in cl if 'rev=ajan7-arkham' in l]) == 40, '%d lines: %s' % (len(cl), cl[-2:]))

# ---------------------------------------------------------------- the real file still compiles for iOS (when the wine headers exist)
wine = Path(os.environ.get('WINE_SRC', root / 'wine'))
config = wine / 'build-macos/include/config.h'
if shutil.which('xcrun') and config.exists():
    try:
        sdk = subprocess.run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sdk = ''
    if sdk:
        build = root / 'build/ntdll-unix'
        flags = ['-fsyntax-only', '-arch', 'arm64', '-isysroot', sdk, '-miphoneos-version-min=17.0', '-O2', '-fPIC',
                 '-fno-strict-aliasing', '-Wall', '-Wno-implicit-function-declaration', '-Wno-int-conversion',
                 '-include', str(config), '-include', str(build / 'shims/wine_ios_exit.h'), f'-I{build}', f'-I{build / "shims"}',
                 f'-I{wine / "build-macos/dlls/ntdll"}', f'-I{wine / "dlls/ntdll"}', f'-I{wine / "dlls/ntdll/unix"}',
                 f'-I{wine / "build-macos/include"}', f'-I{wine / "include"}', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES',
                 '-DLTC_SOURCE', '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', '-DWINE_UNIX_LIB',
                 '-DWINE_IOS=1', '-Dget_thread_context=ntdll_get_thread_context', '-Dset_thread_context=ntdll_set_thread_context']
        r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(build / 'virtual_ios.c')], capture_output=True, text=True)
        check('virtual_ios.c compiles for iOS (syntax only, pinned wine headers)', r.returncode == 0, r.stderr[-2000:])
        check('...and the new header adds no warning', 'ios_tls_clear' not in r.stderr, r.stderr[-2000:])
    else:
        print('skip the iOS syntax check: no iphoneos SDK')
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

sys.exit(0 if ok else 1)
