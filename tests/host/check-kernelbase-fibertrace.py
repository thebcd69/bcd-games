#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""kernelbase-fibertrace.dll: Wine's kernelbase that logs the fiber calls; no Wine runs.

Microsoft Flight Simulator 2024 (build 540) stops with its own "FiberError"
once its job system has run out of free fibers, and Wine's fiber functions log
nothing. tools/patch-wine-kernelbase-fibertrace.py adds [fiber-trace] lines to
dlls/kernelbase (thread.c, plus one call each in debug.c, file.c and sync.c);
tools/build-wine-extra-dlls.sh ships the result as kernelbase-fibertrace.dll
next to the untouched kernelbase.dll; app/Madeira/WineProcessBridge.m links it
as sysx64's kernelbase.dll (and system32's in an ARM64EC session) only for
env.MADEIRA_FIBER_TRACE = 1.

Checks:
  - the patch applies to the submodule's kernelbase (every anchor once), a
    second run changes nothing, and it only adds lines (one line of
    GetQueuedCompletionStatusEx becomes a block);
  - the trace block, cut out of the patched thread.c and compiled on the host
    with stubs, logs: the banner, CreateFiberEx and ConvertThreadToFiberEx, a
    summary every 2 s with the read and completion-port counts, a fiber parked
    3 s with the x64 calls on its stack (call rel32, an import through
    [rip+disp32] resolved to its module, call [reg+disp8]; a value without a
    call before it is skipped), its next run, a switch into a running fiber,
    into one still being left and into a deleted one, DeleteFiber, and every
    fiber once at an unhandled exception; an unhandled MSVC C++ exception's
    thrown types, what() for a std::exception, the object's vftable and the
    x64 calls on the throwing stack (no what() for another type, nothing for
    a code that is not 0xe06d7363); no line reaches 720 bytes;
  - --verify accepts a DLL with the marker and the shipped exports and refuses
    one without the marker or with other exports (synthetic PEs);
  - the build step patches, builds, strips, verifies and restores the tree;
    WineProcessBridge.m links the DLL only for MADEIRA_FIBER_TRACE=1, as sysx64's
    kernelbase.dll in any session and system32's too in an ARM64EC one (a
    Steam game runs in the Dock's aarch64 session), and says so either way, and drops the switch after the
    session; CI runs this check and verifies the shipped DLL; the catalog
    documents the switch; .gitignore keeps the built DLL out of git.
The host compile is skipped when no C compiler is found. MADEIRA_WINE_SRC
names another Wine tree (the submodule's by default).
"""
from pathlib import Path
import difflib
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
script = root / 'tools/patch-wine-kernelbase-fibertrace.py'
spec = importlib.util.spec_from_file_location('fibertrace', script)
ft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ft)
wine = Path(os.environ.get('MADEIRA_WINE_SRC', root / 'wine'))
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


# ---- the patch --------------------------------------------------------------
assert (wine / 'dlls/kernelbase/thread.c').is_file(), 'wine submodule missing: ' + str(wine)
with tempfile.TemporaryDirectory(prefix='madeira-fibertrace-') as directory:
    tree = Path(directory)
    for name in ft.FILES:
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(wine / name, tree / name)
    before = {name: (tree / name).read_text() for name in ft.FILES}
    run = subprocess.run([sys.executable, str(script), str(tree)], capture_output=True, text=True)
    check('the patch applies (%s)' % (run.stdout.strip() or run.stderr.strip()), run.returncode == 0)
    after = {name: (tree / name).read_text() for name in ft.FILES}
    again = subprocess.run([sys.executable, str(script), str(tree)], capture_output=True, text=True)
    check('a second run changes nothing',
          again.returncode == 0 and 'already patched' in again.stdout and
          all((tree / name).read_text() == after[name] for name in ft.FILES))
    removed = []
    for name in ft.FILES:
        for line in difflib.ndiff(before[name].split('\n'), after[name].split('\n')):
            if line.startswith('- '):
                removed.append((name, line[2:]))
    check('lines are only added (one GetQueuedCompletionStatusEx line becomes a block)',
          removed == [('dlls/kernelbase/sync.c', '    if (ret == STATUS_SUCCESS) return TRUE;')])
    thread = after['dlls/kernelbase/thread.c']
    check('every fiber function calls the trace',
          all(s in thread for s in ('    ft_switch( current_fiber, new_fiber );\n',
                                    '    ft_after_switch( current_fiber );\n}\n',
                                    '    ft_after_switch( fiber );\n    __TRY\n',
                                    '    ft_created( fiber, stack_commit, stack_reserve );\n',
                                    '    ft_converted( fiber );\n    return fiber;\n',
                                    '    if (!fiber) return;\n    ft_gone( fiber, TRUE );\n',
                                    '        ft_gone( fiber, FALSE );\n',
                                    '    LONG                  ft_index;')))
    check('UnhandledExceptionFilter dumps the table first, with the exception\'s context',
          '    BOOL nested;\n\n    madeira_fibertrace_dump( rec, epointers->ContextRecord );' in after['dlls/kernelbase/debug.c'])
    check('the reads and completion-port calls are counted',
          after['dlls/kernelbase/file.c'].count('madeira_fibertrace_io(') == 4 and
          after['dlls/kernelbase/sync.c'].count('madeira_fibertrace_io(') == 2)
    begin = thread.index('/* madeira-bcd fiber trace begin')
    block = thread[begin:thread.index('/* madeira-bcd fiber trace end */', begin)]

# ---- the trace block on the host --------------------------------------------
PRELUDE = r'''
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>
#ifndef __x86_64__
#define __arm64ec__ 1
#endif
typedef unsigned char BYTE; typedef int INT; typedef int LONG; typedef unsigned int DWORD; typedef int BOOL;
typedef long long LONGLONG; typedef unsigned long long ULONGLONG; typedef uintptr_t ULONG_PTR; typedef size_t SIZE_T;
typedef unsigned short WCHAR; typedef void *HANDLE;
#define TRUE 1
#define FALSE 0
#define HandleToLong(h) ((LONG)(intptr_t)(h))
#define HandleToULong(h) ((unsigned int)(uintptr_t)(h))
#define CONTAINING_RECORD(a, type, field) ((type *)((char *)(a) - offsetof(type, field)))
typedef struct _LIST_ENTRY { struct _LIST_ENTRY *Flink, *Blink; } LIST_ENTRY;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { LIST_ENTRY InLoadOrderLinks, InMemoryOrderLinks, InInitializationOrderLinks; void *DllBase;
                 void *EntryPoint; unsigned int SizeOfImage; UNICODE_STRING FullDllName, BaseDllName; } LDR_DATA_TABLE_ENTRY;
typedef struct { LIST_ENTRY InMemoryOrderModuleList; } PEB_LDR_DATA;
typedef struct { UNICODE_STRING ImagePathName; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { void *ImageBaseAddress; PEB_LDR_DATA *LdrData; RTL_USER_PROCESS_PARAMETERS *ProcessParameters; } PEB;
typedef struct { struct { void *StackBase; void *FiberData; } Tib; struct { HANDLE UniqueProcess, UniqueThread; } ClientId;
                 PEB *Peb; } TEB;
static TEB teb;
static TEB *NtCurrentTeb(void) { return &teb; }
typedef struct { unsigned short e_magic; unsigned char pad[0x3a]; int e_lfanew; } IMAGE_DOS_HEADER;
typedef struct { unsigned int Signature; struct { unsigned int SizeOfImage; } OptionalHeader; } IMAGE_NT_HEADERS;
typedef struct { unsigned int ExceptionCode, ExceptionFlags; void *ExceptionRecord; void *ExceptionAddress;
                 unsigned int NumberParameters; ULONG_PTR ExceptionInformation[15]; } EXCEPTION_RECORD;
typedef struct { ULONG_PTR Rsp; } CONTEXT;
struct fiber_data { void *param; void *except; void *stack_base; void *stack_limit; void *stack_allocation;
                    struct { ULONG_PTR Rsp; } context; DWORD flags; void *start; LONG ft_index; };
#define InterlockedCompareExchange(d, x, c) __sync_val_compare_and_swap((d), (c), (x))
#define InterlockedCompareExchange64(d, x, c) __sync_val_compare_and_swap((d), (c), (x))
#define InterlockedExchange(d, v) __sync_lock_test_and_set((d), (v))
#define InterlockedIncrement(d) __sync_add_and_fetch((d), 1)
#define InterlockedExchangeAdd(d, v) __sync_fetch_and_add((d), (v))
static void *InterlockedExchangePointer(void *volatile *d, void *v) { return __sync_lock_test_and_set(d, v); }
static ULONGLONG now_ms;
static ULONGLONG GetTickCount64(void) { return now_ms; }
#define MESSAGE(...) printf(__VA_ARGS__)
#define __TRY if (1) {
#define __EXCEPT_PAGE_FAULT } else {
#define __ENDTRY }
'''

DRIVER = r'''
static unsigned char exe[0x20000] __attribute__((aligned(16)));
static unsigned char kb[0x10000] __attribute__((aligned(16)));
static ULONG_PTR stack2[512], stack3[512], stack_main[64], stack_w[64];
static WCHAR path[] = { 'C',':','\\','G','\\','F','l','i','g','h','t','S','i','m','u','l','a','t','o','r','2','0','2','4','.','e','x','e',0 };
static WCHAR kbname[] = { 'k','e','r','n','e','l','b','a','s','e','.','d','l','l',0 };
static void put32(unsigned int off, int v) { memcpy(exe + off, &v, 4); }
static void on(LONG tid, struct fiber_data *f) { teb.ClientId.UniqueThread = (HANDLE)(intptr_t)tid; teb.Tib.FiberData = f; }
static void sw(struct fiber_data *cur, struct fiber_data *to) { ft_switch(cur, to); teb.Tib.FiberData = to; ft_after_switch(to); }

int main(void)
{
    static PEB peb; static PEB_LDR_DATA ldr; static RTL_USER_PROCESS_PARAMETERS params; static LDR_DATA_TABLE_ENTRY mod;
    struct fiber_data main_f = {0}, f2 = {0}, f3 = {0}, w = {0};
    ULONG_PTR slot_target = (ULONG_PTR)kb + 0x1230, base = (ULONG_PTR)exe;
    EXCEPTION_RECORD rec = {0};

    ((IMAGE_DOS_HEADER *)exe)->e_lfanew = 0x40;
    ((IMAGE_NT_HEADERS *)(exe + 0x40))->OptionalHeader.SizeOfImage = sizeof(exe);
    exe[0x2000] = 0xe8; put32(0x2001, 0x5000 - 0x2005);                 /* call exe+0x5000, returns to +0x2005 */
    exe[0x3000] = 0xff; exe[0x3001] = 0x15; put32(0x3002, 0x8000 - 0x3006); /* call [exe+0x8000], returns to +0x3006 */
    memcpy(exe + 0x8000, &slot_target, sizeof(slot_target));               /* the import slot: kernelbase.dll+0x1230 */
    exe[0x4000] = 0xff; exe[0x4001] = 0x50; exe[0x4002] = 0x18;             /* call [rax+0x18], returns to +0x4003 */
    params.ImagePathName.Buffer = path; params.ImagePathName.Length = sizeof(path) - 2;
    mod.DllBase = kb; mod.SizeOfImage = sizeof(kb); mod.BaseDllName.Buffer = kbname; mod.BaseDllName.Length = sizeof(kbname) - 2;
    ldr.InMemoryOrderModuleList.Flink = &mod.InMemoryOrderLinks; mod.InMemoryOrderLinks.Flink = &ldr.InMemoryOrderModuleList;
    peb.ImageBaseAddress = exe; peb.LdrData = &ldr; peb.ProcessParameters = &params;
    teb.Peb = &peb; teb.ClientId.UniqueProcess = (HANDLE)0x20;

    now_ms = 1000;
    on(0xa8, NULL);
    main_f.stack_base = stack_main + 64; main_f.stack_allocation = stack_main;
    teb.Tib.FiberData = &main_f; ft_converted(&main_f);                            /* #1 */
    f2.start = exe + 0x7000; f2.stack_base = stack2 + 512; f2.stack_allocation = stack2; f2.context.Rsp = (ULONG_PTR)(stack2 + 512);
    ft_created(&f2, 0x10000, 0x40000);                                              /* #2 */
    f3.start = exe + 0x7000; f3.stack_base = stack3 + 512; f3.stack_allocation = stack3; f3.context.Rsp = (ULONG_PTR)(stack3 + 512);
    ft_created(&f3, 0x10000, 0x40000);                                              /* #3, never run */

    sw(&main_f, &f2);                                   /* arms the report clock at 1000 */
    stack2[110] = base + 0x2005; stack2[115] = base + 0x6000; stack2[120] = base + 0x3006; stack2[130] = base + 0x4003;
    f2.context.Rsp = (ULONG_PTR)(stack2 + 100);         /* what switch_fiber would save */
    sw(&f2, &main_f);                                   /* #2 parked at 1000 */
    madeira_fibertrace_io(1, 3); madeira_fibertrace_io(0, 2); madeira_fibertrace_io(4, 5);
    now_ms = 3100; sw(&main_f, &main_f);                /* report: #2 parked 2100 ms, not yet listed */
    now_ms = 5200; sw(&main_f, &main_f);                /* report: #2 parked 4200 ms, listed with its calls */
    now_ms = 5300; sw(&main_f, &f2);                    /* #2 runs again */

    on(0xb0, NULL);
    w.stack_base = stack_w + 64; w.stack_allocation = stack_w;
    teb.Tib.FiberData = &w; ft_converted(&w);                                       /* #4 */
    ft_switch(&w, &f2);                                 /* WRONG: #2 runs on 00a8 */
    teb.ClientId.UniqueThread = (HANDLE)0xa8;
    ft_switch(&f2, &main_f);                            /* 00a8 leaves #2 (registers not saved yet) */
    teb.ClientId.UniqueThread = (HANDLE)0xb0;
    ft_switch(&w, &f2);                                 /* WRONG: still being left */
    ft_gone(&f3, TRUE);                                 /* DeleteFiber(#3) */
    ft_switch(&w, &f3);                                 /* WRONG: #3 was deleted */

    rec.ExceptionCode = 0x80000003; rec.ExceptionAddress = exe + 0x3e95;
    teb.Tib.FiberData = &w;
    madeira_fibertrace_dump(&rec, NULL);
    madeira_fibertrace_dump(&rec, NULL);                /* once only */

    {   /* an unhandled C++ exception: std::bad_alloc, thrown below exe+0x3006's call */
        static ULONG_PTR obj[6], stack_t[64];
        static const char what[] = "bad allocation";
        CONTEXT ctx = {0};

        put32(0xa00c, 0xa100);                                                  /* ThrowInfo at +0xa000 */
        put32(0xa100, 2); put32(0xa104, 0xa200); put32(0xa108, 0xa240);        /* two catchable types */
        put32(0xa204, 0xa300); put32(0xa20c, -1); put32(0xa214, 24);
        put32(0xa244, 0xa340); put32(0xa24c, -1); put32(0xa254, 24);
        strcpy((char *)exe + 0xa310, ".?AVbad_alloc@std@@");                    /* type descriptors: name at +16 */
        strcpy((char *)exe + 0xa350, ".?AVexception@std@@");
        obj[0] = base + 0x9000; obj[1] = (ULONG_PTR)what;
        stack_t[10] = base + 0x6000; stack_t[20] = base + 0x3006;
        ctx.Rsp = (ULONG_PTR)stack_t;
        teb.Tib.StackBase = stack_t + 64;
        rec.ExceptionCode = 0xe06d7363; rec.NumberParameters = 4;
        rec.ExceptionInformation[0] = 0x19930520; rec.ExceptionInformation[1] = (ULONG_PTR)obj;
        rec.ExceptionInformation[2] = base + 0xa000; rec.ExceptionInformation[3] = base;
        madeira_fibertrace_dump(&rec, &ctx);
        put32(0xa100, 1); put32(0xa104, 0xa380); put32(0xa384, 0xa400);        /* not a std::exception */
        strcpy((char *)exe + 0xa410, ".?AUhresult_error@winrt@@");
        madeira_fibertrace_dump(&rec, NULL);
        rec.ExceptionCode = 0xc0000005;                                         /* not a C++ exception */
        madeira_fibertrace_dump(&rec, &ctx);
    }
    return 0;
}
'''

cc = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
if not cc:
    print('SKIP host compile (no C compiler)')
else:
    with tempfile.TemporaryDirectory(prefix='madeira-fibertrace-cc-') as directory:
        c = Path(directory) / 'ft.c'
        exe = Path(directory) / 'ft'
        c.write_text(PRELUDE + block + DRIVER)
        built = subprocess.run([cc, '-std=gnu11', '-O1', '-Wall', '-Werror', '-Wno-unused-function', '-Wno-unknown-warning-option',
                                '-Wno-format-truncation', '-o', str(exe), str(c)],
                               capture_output=True, text=True)
        check('the trace block compiles on the host', built.returncode == 0)
        if built.returncode:
            print(built.stderr[:3000])
        else:
            out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
            lines = out.split('\n')
            print('\n'.join('     | ' + line for line in lines[:60]))

            def has(*parts):
                return any(all(p in line for p in parts) for line in lines)

            check('every line starts with [fiber-trace] pid:tid',
                  all(line.startswith('[fiber-trace] 0020:00') for line in lines if line))
            check('no line reaches 720 bytes', max(len(line) for line in lines) < 720)
            check('the banner names the program and the exe range',
                  has('fiber-trace-v1 madeira-bcd on in FlightSimulator2024.exe: exe '))
            check('ConvertThreadToFiberEx and CreateFiberEx are logged',
                  has('#1 ConvertThreadToFiberEx(') and
                  has('#2 CreateFiberEx(commit 0x10000, reserve 0x40000, flags 0, start exe+0x7000'))
            check('the summary at 3100 ms: 4 fibers so far, #2 not listed yet, the read counts',
                  has('3 fibers: 1 running, 1 parked (0 for 3000 ms or more), 1 never run, 0 being left, 0 gone',
                      'overlapped ReadFile 2 done at once, 3 pending, 0 failed', 'completion-port entries 5') and
                  not has('#2 0x', 'parked 2100 ms'))
            check('#2 listed once when parked 4200 ms, with the three calls on its stack and not the stray value',
                  has('#2 ', 'parked 4200 ms, last on 00a8', 'start exe+0x7000', 'stack used 0x',
                      ' exe+0x2005>exe+0x5000', ' exe+0x3006>[exe+0x8000]=kernelbase.dll+0x1230', ' exe+0x4003>[r+0x18]')
                  and not has('exe+0x6000') and sum('parked 4200 ms' in line for line in lines) == 1)
            check('its next run is logged', has('#2 runs again after 4300 ms parked'))
            check('a switch into a fiber running elsewhere is WRONG', has('WRONG: SwitchToFiber(#2), which runs on thread 00a8'))
            check('a switch into a fiber still being left is WRONG',
                  has('WRONG: SwitchToFiber(#2) while thread 00a8 is still leaving it'))
            check('DeleteFiber is logged, and a switch into the deleted fiber is WRONG',
                  has('#3 DeleteFiber, run 0 times') and has('WRONG: SwitchToFiber(#3), a deleted fiber'))
            check('the unhandled exception dumps every live fiber once',
                  has('unhandled exception 80000003 at exe+0x3e95 on fiber #4; every fiber follows') and
                  has('at the exception: 4 fibers') and has('#1 ', 'a converted thread') and
                  has('#2 ', 'running on 00b0') and has('#4 ', 'being left by 00b0') and not has('#3 0x') and
                  sum('every fiber follows' in line for line in lines) == 1)
            check('an unhandled C++ exception names its types, what(), the object\'s vftable and the throwing calls',
                  has('unhandled C++ exception: object 0x', 'types: .?AVbad_alloc@std@@ .?AVexception@std@@') and
                  has('what(): "bad allocation"') and has('object: vftable exe+0x9000, then ') and
                  has('x64 calls on the throwing stack: exe+0x3006>[exe+0x8000]=kernelbase.dll+0x1230'))
            check('another type gets no what() line, no context no stack line, another code nothing',
                  has('unhandled C++ exception: object 0x', 'types: .?AUhresult_error@winrt@@') and
                  sum('what():' in line for line in lines) == 1 and
                  sum('throwing stack' in line for line in lines) == 1 and
                  sum('unhandled C++ exception' in line for line in lines) == 2)

# ---- --verify on synthetic PEs ----------------------------------------------
def pe(names, extra=b''):
    """a PE32+ image with one section holding an export table of names"""
    sec_rva, sec_raw = 0x1000, 0x200
    strings = b''
    name_rvas = []
    base = sec_rva + 40 + 4 * len(names)
    for n in names:
        name_rvas.append(base + len(strings))
        strings += n.encode() + b'\0'
    exp = struct.pack('<IIHHIIIIIII', 0, 0, 0, 0, 0, 1, 0, len(names), 0, sec_rva + 40, 0)
    body = exp + b''.join(struct.pack('<I', r) for r in name_rvas) + strings + extra
    hdr = bytearray(sec_raw)
    hdr[0:2] = b'MZ'
    struct.pack_into('<I', hdr, 0x3c, 0x40)
    hdr[0x40:0x44] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', hdr, 0x44, 0x8664, 1, 0, 0, 0, 240, 0x2022)
    opt = 0x58
    struct.pack_into('<H', hdr, opt, 0x20b)
    struct.pack_into('<II', hdr, opt + 112, sec_rva, 40)
    s = opt + 240
    hdr[s:s + 8] = b'.rdata\0\0'
    struct.pack_into('<IIII', hdr, s + 8, len(body), sec_rva, len(body), sec_raw)
    return bytes(hdr) + body


with tempfile.TemporaryDirectory(prefix='madeira-fibertrace-pe-') as directory:
    d = Path(directory)
    (d / 'shipped.dll').write_bytes(pe(['CreateFiberEx', 'SwitchToFiber']))
    (d / 'good.dll').write_bytes(pe(['CreateFiberEx', 'SwitchToFiber'], ft.MARKER.encode()))
    (d / 'plain.dll').write_bytes(pe(['CreateFiberEx', 'SwitchToFiber']))
    (d / 'other.dll').write_bytes(pe(['CreateFiberEx'], ft.MARKER.encode()))

    def verify(name):
        return subprocess.run([sys.executable, str(script), '--verify', str(d / name), str(d / 'shipped.dll')],
                              capture_output=True, text=True).returncode

    check('--verify accepts the marker with the shipped exports', verify('good.dll') == 0)
    check('--verify refuses a DLL without the marker', verify('plain.dll') != 0)
    check('--verify refuses other exports', verify('other.dll') != 0)

# ---- build step, app, CI, catalog -------------------------------------------
build = (root / 'tools/build-wine-extra-dlls.sh').read_text()
fbt = build[build.index('if [ -n "$FBT" ]; then'):]
fbt = fbt[:fbt.index('\nfi\n') + 4]
# The shipped kernelbase.dll is only read, never written: each line that names it is a build condition that tests it
# ([ -f ... ]) or a check that compares exports with it (its last argument). Round 624 added kernelbase-optin.dll's two (ZMP).
kb_uses = [l.strip() for l in build.splitlines() if '"$SHIP/kernelbase.dll"' in l]
kb_read_only = (len(kb_uses) >= 2 and
                '--verify "$SHIP/kernelbase-fibertrace.dll.tmp" "$SHIP/kernelbase.dll"; then' in '\n'.join(kb_uses) and
                all((l.startswith('if [ "${MADEIRA_') and l.endswith('[ -f "$SHIP/kernelbase.dll" ]; then') and
                     l.count('"$SHIP/kernelbase.dll"') == 1) or
                    (l.startswith('python3 "$R/tools/') and l.endswith(' "$SHIP/kernelbase.dll"; then') and
                     l.count('"$SHIP/kernelbase.dll"') == 1 and '>' not in l)
                    for l in kb_uses))
check('the build patches, builds kernelbase, strips like build-modules.sh, verifies, then ships',
      'patch-wine-kernelbase-fibertrace.py" "$R/wine"' in fbt and
      'make -C "$B" -j"$JOBS" dlls/kernelbase/arm64ec-windows/kernelbase.dll' in fbt and
      'arm64ec-w64-mingw32-strip" --strip-debug' in fbt and
      '--verify "$SHIP/kernelbase-fibertrace.dll.tmp" "$SHIP/kernelbase.dll"' in fbt and
      'mv "$SHIP/kernelbase-fibertrace.dll.tmp" "$SHIP/kernelbase-fibertrace.dll"' in fbt and
      kb_read_only)
check('the build restores the five files and drops the patched objects',
      all(f in fbt for f in ft.FILES) and 'git -C "$R/wine" checkout -- $kbsrc' in fbt and
      fbt.count('rm -f "$kb" "$B"/dlls/kernelbase/arm64ec-windows/*.o') == 2)
guard = re.search(r'\[ -n "\$todo((?:\$[A-Z]+)+)" \] \|\| \{ echo "nothing to build"; exit 0; \}', build)   # the variables in any order (round 624 added ZMP and DCO)
check('MADEIRA_FIBER_TRACE_BUILD=0 skips it', '"${MADEIRA_FIBER_TRACE_BUILD:-1}" != 0' in build and
      bool(guard) and '$FBT' in re.findall(r'\$[A-Z]+', guard.group(1)))
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
blk = bridge[bridge.index('const char *fiberTrace = getenv("MADEIRA_FIBER_TRACE");'):]
blk = blk[:blk.index('\n            }\n')]
check('WineProcessBridge links it for MADEIRA_FIBER_TRACE=1: sysx64 in any session (a Steam game\'s '
      'x64 process in the Dock\'s aarch64 session loads it there), system32 too in an ARM64EC one',
      "if (fiberTrace && fiberTrace[0] == '1')" in blk and 'use_arm64ec && fiberTrace' not in blk and
      'stringByAppendingPathComponent:@"kernelbase-fibertrace.dll"' in blk and
      '[prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];' in blk and
      'if (use_arm64ec) [dirs addObject:sys32Dir];' in blk and
      'for (NSString *dir in dirs)' in blk and 'sys32Dir, [prefix' not in blk and
      'stringByAppendingPathComponent:@"kernelbase.dll"' in blk)
check('and says so either way',
      'MADEIRA_FIBER_TRACE=1: kernelbase.dll -> kernelbase-fibertrace.dll' in blk and
      'MADEIRA_FIBER_TRACE=1 but this build has no kernelbase-fibertrace.dll' in blk)
check('the switch ends with the session', 'unsetenv("MADEIRA_FIBER_TRACE");' in bridge)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check and verifies the shipped DLL',
      'run: python3 tests/host/check-kernelbase-fibertrace.py' in wf and
      'python3 tools/patch-wine-kernelbase-fibertrace.py --verify app/Madeira/arm64ec-windows/kernelbase-fibertrace.dll' in wf)
cat = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the catalog documents env.MADEIRA_FIBER_TRACE (default 0)',
      'ConfigOption(key: "env.MADEIRA_FIBER_TRACE", title: "Log the fiber calls (kernelbase)", kind: .bool, defaultValue: "0"' in cat)
check('.gitignore keeps the built DLL out of git',
      'app/Madeira/arm64ec-windows/kernelbase-fibertrace.dll' in (root / '.gitignore').read_text().split('\n'))

print('PASS: kernelbase fiber trace' if ok else 'FAIL: kernelbase fiber trace')
sys.exit(0 if ok else 1)
