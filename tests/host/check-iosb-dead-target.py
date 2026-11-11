#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""An async I/O completion in a 32-bit process stores its IO_STATUS_BLOCK32 only where it can land (build 596); no Wine runs.

Battle.net (build 594, the owner's iPhone18,2, log 19:22:01): the updated Agent (a 32-bit console program) and AgentHelper.exe faulted 8
and 10 times, a WRITE at 0x10004 / 0x10000 from Madeira`invoke_system_apc+0x57c (`STR W22,[X19,#4]`: IO_STATUS_BLOCK32.Information) under
server_select <- server_wait_for_object <- NtWriteFile. The same fault, with the same registers (R12 = 0x10000, R15 = 0x8e), is in four older
logs of other games whose 32-bit launcher runs cmd.exe (Portal, builds 579, twice; Batman Arkham Asylum and LEGO Batman 2, build 592).

It is not a guest address that lacks +B. 0x8e = 142 and 0x45 = 69 are the byte counts of the first two lines of rpmalloc's band-selector
beacon; FEX's own WriteFile (libwow64fex.dll imports NtWriteFile from ntdll and nothing from kernel32) writes them from native code, with
an IO_STATUS_BLOCK it never initialised, inside the 32-bit process, before any guest ran. In a console program the standard error handle
is a live console, so the write reaches the server; conhost's answer comes back as an APC_ASYNC_IO system APC; ntdll takes every call of a
32-bit pseudo-process for a wow64 thunk's, reads the status block's first 8 bytes (stale stack: 0x10000) as the address of the guest's
IO_STATUS_BLOCK32, and stores there. Translating 0x10000 by +B would write into the guest's memory at a random place; the right
answer is not to store into nothing.

Held here:
  1. The links of the reasoning, in the sources (the Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; the FEX
     source likewise; each part is skipped when its tree is not there or is not the commit this checkout pins):
     set_async_iosb writes an IO_STATUS_BLOCK32 under in_wow64_call(); iosb_client_ptr returns io->Pointer under it; ios_in_wow64_call is the
     per-pseudo-process owner rule; the APC_ASYNC_IO case stores through call->async_io.sb; the thunks' cookie (iosb_32to64) is a window
     address, while ntdll's own sync_ioctl and the socket send queue put a host-side IO_STATUS_BLOCK32 in the cookie (so a pointer outside
     the window is NOT always wrong); FEX's WriteFile has an uninitialised IO_STATUS_BLOCK and calls NtWriteFile itself; rpmalloc's
     ios_va_emit writes through WriteFile( GetStdHandle( -12 ) ); the four beacon lines are 142 + 69 + 59 + 63 = 333 bytes.
  2. The change, in this checkout: ios_iosb_dead.h, the wrapper ios_set_async_iosb in server_ios.c (the APC_ASYNC_IO case calls it, and
     nothing else in invoke_system_apc stores a status block), the probes in signal_arm64_ios.c.
  3. ios_iosb32_target_dead, compiled on the host with counting probes, over every case; and the real ios_set_async_iosb, extracted from
     server_ios.c and compiled next to stand-ins and the ORIGINAL (an unconditional set_async_iosb): a 64-bit caller, a NULL status block, a
     status block in the guest window, a mapped or served pointer and the switch set to 0 all reach set_async_iosb once with the same
     arguments and print nothing, the probes are not even asked for the first three; only "32-bit caller, outside the window, nothing
     there" skips the store, and says so (the first 8 times, then at the powers of two).
  4. The two real probes, extracted from signal_arm64_ios.c: on macOS they are compiled against the Mach calls and run on real mappings
     (a mapping, a hole, a mapping that ends in a hole, a PROT_NONE page, the zero page); the low probe is run against stand-in registries.
  5. Auto-tune: the known rule (fixed in 596, no line) fires on the Battle.net log (its fixture) and on the Portal one; this check runs in CI.
  6. The two changed C files compile for iOS (syntax only) when the iPhoneOS SDK and the pinned wine headers exist.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
nt = root / 'build/ntdll-unix'
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}\n', start) + 3]


def git(*args, cwd=root):
    r = subprocess.run(['git', '-C', str(cwd)] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


def tree(name, marker):
    """(path, pinned) of a submodule's source: this checkout's, $BCD_<NAME>_SRC or ~/madeira-bcd/<name>; pinned = it is the pinned commit."""
    pin = ''
    entry = git('ls-tree', 'HEAD', name)
    if entry:
        pin = entry.split()[2]
    for candidate in (os.environ.get('BCD_%s_SRC' % name.upper()), root / name, Path.home() / 'madeira-bcd' / name):
        if candidate and (Path(candidate) / marker).is_file():
            return Path(candidate), bool(pin and git('rev-parse', 'HEAD', cwd=Path(candidate)) == pin)
    return None, False


header = (nt / 'ios_iosb_dead.h').read_text()
server = (nt / 'server_ios.c').read_text()
signal = (nt / 'signal_arm64_ios.c').read_text()
cc = os.environ.get('CC', 'cc')

# ---------------------------------------------------------------- 1. the links of the reasoning
wine, wine_pinned = tree('wine', 'dlls/ntdll/unix/unix_private.h')
if wine and wine_pinned:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    up = (wine / 'dlls/ntdll/unix/unix_private.h').read_text()
    sai = function(up, 'static inline void set_async_iosb( client_ptr_t iosb, NTSTATUS status, ULONG_PTR info )\n{')
    check('set_async_iosb: NULL returns first; under in_wow64_call() it stores Information (+4) then Status (+0) as an IO_STATUS_BLOCK32',
          'if (!iosb) return;' in sai and 'if (in_wow64_call())' in sai and 'IO_STATUS_BLOCK32 *io = wine_server_get_ptr( iosb );' in sai
          and sai.index('io->Information = info;') < sai.index('WriteRelease( &io->Status, status );'))
    check('iosb_client_ptr: under in_wow64_call() the 64-bit status block\'s Pointer is the client pointer, otherwise the block itself',
          'if (io && in_wow64_call()) return wine_server_client_ptr( io->Pointer );' in up and 'return wine_server_client_ptr( io );' in up)
    check('in_wow64_call() asks ios_in_wow64_call() when the iOS layer has it', 'if (ios_in_wow64_call) return ios_in_wow64_call();' in up)
    fc = (wine / 'dlls/ntdll/unix/file.c').read_text()
    owner = function(fc, 'BOOL ios_in_wow64_call(void)\n{')
    check('ios_in_wow64_call: the answer is "this pseudo-process has a guest window", for every call of a thread in it (not the thunk\'s call)',
          'return is_win64 && ios_wow_host_base() != 0;' in owner)
    check('server_write_file hands iosb_client_ptr( io ) to the server as the async\'s status block',
          'req->async = server_async( handle, &async->io, event, apc, apc_context, iosb_client_ptr(io) );'
          in function(fc, 'static unsigned int server_write_file( HANDLE handle, HANDLE event, PIO_APC_ROUTINE apc, void *apc_context,'))
    srv = (wine / 'dlls/ntdll/unix/server.c').read_text()
    check('the APC_ASYNC_IO case stores through call->async_io.sb', 'set_async_iosb( call->async_io.sb, result->async_io.status, info );' in srv)
    w64 = (wine / 'dlls/wow64/wow64_private.h').read_text()
    check('the thunks\' cookie is the host address of the guest\'s status block (a window address), and is no 64-bit status block',
          'io->Pointer = io32;' in function(w64, 'static inline IO_STATUS_BLOCK *iosb_32to64( IO_STATUS_BLOCK *io, IO_STATUS_BLOCK32 *io32 )\n{')
          and 'static inline void *guest_ptr32( ULONG addr )' in w64)
    check('ntdll\'s own sync_ioctl, wow64\'s get_file_id and the socket send queue put a HOST-side IO_STATUS_BLOCK32 in the cookie: outside the window is not always wrong',
          'io.Pointer = &io32;' in function(fc, 'NTSTATUS sync_ioctl( HANDLE file, ULONG code, void *in_buffer, ULONG in_size, void *out_buffer, ULONG out_size )\n{')
          and 'IO_STATUS_BLOCK io = { .Pointer = &io32 };' in (wine / 'dlls/wow64/file.c').read_text()
          and 'rem_io->Pointer = p;' in (wine / 'dlls/ntdll/unix/socket.c').read_text())
    kb = (wine / 'dlls/kernelbase/file.c').read_text()
    check('kernelbase\'s WriteFile would not leave 0x10000 in Pointer (it stores STATUS_PENDING there first), so the status block was not its',
          'piosb->Status = STATUS_PENDING;' in function(kb, 'BOOL WINAPI DECLSPEC_HOTPATCH WriteFile( HANDLE file, LPCVOID buffer, DWORD count,'))
else:
    print('SKIP the Wine source parts: %s' % ('no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine)' if not wine
                                               else 'the tree at %s is not the commit this checkout pins' % wine))

fex, fex_pinned = tree('FEX', 'Source/Windows/Common/WinAPI/IO.cpp')
if fex and fex_pinned:
    print('FEX source: %s (%s)' % (fex, git('rev-parse', '--short=12', 'HEAD', cwd=fex)))
    io_cpp = (fex / 'Source/Windows/Common/WinAPI/IO.cpp').read_text()
    wf = io_cpp[io_cpp.index('DLLEXPORT_FUNC(WINBOOL, WriteFile,'):]
    wf = wf[:wf.index('\n}\n') + 3]
    check('FEX\'s own WriteFile calls NtWriteFile with an IO_STATUS_BLOCK it never initialised',
          '  IO_STATUS_BLOCK IOSB;\n' in wf and 'NtWriteFile(hFile, nullptr, nullptr, nullptr, &IOSB,' in wf)
    rp = (fex / 'External/rpmalloc/rpmalloc/rpmalloc.c').read_text()
    emit = function(rp, 'static void\nios_va_emit(const char* buf, int len) {')
    check('rpmalloc\'s band-selector beacon (ios_va_emit) writes each line with WriteFile( GetStdHandle( -12 ) ) as soon as rpmalloc starts',
          'WriteFile(GetStdHandle((unsigned long)-12), buf, (unsigned long)len, &w, 0);' in emit)
    mod = (fex / 'Source/Windows/WOW64/Module.cpp').read_text()
    check('libwow64fex.dll\'s BTCpuProcessInit starts rpmalloc (InitCRTProcess) before its first log line',
          mod.index('void BTCpuProcessInit() {') < mod.index('FEX::Windows::InitCRTProcess();') < mod.index('IosRawReport("E [wow64-init] teb-tsd'))
else:
    print('SKIP the FEX source parts: %s' % ('no FEX source (FEX/ in this checkout, BCD_FEX_SRC or ~/madeira-bcd/FEX)' if not fex
                                              else 'the tree at %s is not the commit this checkout pins' % fex))

beacon = ['[va-profile] ml787 advertised-maxapp(NOT the task ceiling)=0x7fffffffff -- the usable ceiling is TASK_VM_INFO.max_address, which is far lower\n',
          '[va-profile] ml706 cand0 base=0x7c00000000 PROBE-OK got=0x7c19e40000\n',
          '[va-profile] ml750 cand0 256MB-reserve OK got=0x7c3e000000\n',
          '[va-profile] ml706 SELECTED base=0x7c00000000 end=0x7fffffffff\n']
check('the log\'s stored values 0x8e and 0x45 are the first two beacon lines (142 and 69 bytes), and the four sum to "deferred selector log (333 bytes)"',
      [len(l) for l in beacon] == [142, 69, 59, 63] and sum(len(l) for l in beacon) == 333)

# ---------------------------------------------------------------- 2. the change, in the sources
check('the header is BCD Material', 'SPDX-License-Identifier: GPL-3.0-only' in header and 'BCD Material: additional terms under GPL-3.0 section 7 apply' in header)
check('the switch is MADEIRA_IOSB_DEAD_TARGET and only the value 0 turns the check off', 'getenv( "MADEIRA_IOSB_DEAD_TARGET" )' in header
      and "return !(e && e[0] == '0' && !e[1]);" in header)
dead = function(header, 'static inline int ios_iosb32_target_dead(')
check('the decision leaves NULL, the process\'s own guest window and (below the floor) anything the fault path serves alone, and asks the mapping probe '
      'only at or above the floor, after the cheap cases',
      dead.index('if (!p) return 0;') < dead.index('p - guest_base < IOS_IOSB_WINDOW_SIZE) return 0;') < dead.index('served_low(') < dead.index('mapped('))
check('the header includes nothing of Wine and no Mach header (the probes come in as arguments)', re.findall(r'#include <([^>]+)>', header) == ['stdlib.h'])
check('server_ios.c includes the header', '#include "ios_iosb_dead.h"' in server)
wrapper = function(server, 'static void ios_set_async_iosb( client_ptr_t iosb, NTSTATUS status, ULONG_PTR info )\n{')
check('the wrapper looks only at a non-NULL status block of a call that is in_wow64_call(), reads the switch once, and otherwise IS set_async_iosb',
      'if (iosb && in_wow64_call())' in wrapper and 'if (enabled < 0) enabled = ios_iosb_dead_enabled();' in wrapper and wrapper.rstrip().endswith('set_async_iosb( iosb, status, info );\n}'.rstrip())
      and wrapper.count('set_async_iosb( iosb, status, info );') == 1)
check('the wrapper passes this process\'s window base and the two probes, and logs "[iosb-dead]" with the pointer, the status and the count',
      'ios_iosb32_target_dead( iosb, ios_wow_base(), ios_iosb_probe_mapped, ios_iosb_probe_served_low )' in wrapper and '"[iosb-dead] ajan16 #%u' in wrapper
      and 'n <= 8 || !(n & (n - 1))' in wrapper)
apc = function(server, 'static void invoke_system_apc( const union apc_call *call, union apc_result *result, BOOL self )\n{')
check('invoke_system_apc stores an async completion\'s status block through the wrapper, and through nothing else',
      apc.count('ios_set_async_iosb( call->async_io.sb, result->async_io.status, info );') == 1 and re.findall(r'(?<![_a-z])set_async_iosb\(', apc) == [])
check('server_ios.c calls the original set_async_iosb in that wrapper only', len(re.findall(r'(?<![_a-z])set_async_iosb\( ', server)) == 1)
probe_m = function(signal, 'int ios_iosb_probe_mapped( unsigned long long addr, unsigned long long len )\n{')
probe_s = function(signal, 'int ios_iosb_probe_served_low( unsigned long long addr, unsigned long long len )\n{')
check('the mapping probe asks Mach for regions (the recurse form), calls only a hole "not mapped", and an unexpected error "mapped" (in doubt the store is left alone)',
      'mach_vm_region_recurse(' in probe_m and 'kr == KERN_INVALID_ADDRESS) return 0;' in probe_m and 'kr != KERN_SUCCESS || !rs) return 1;' in probe_m
      and 'rb > (mach_vm_address_t)p) return 0;' in probe_m)
check('the low probe asks the sub-floor windows (any owner) and the low allocation registry, as the fault path does',
      'ios_subfloor_translate( addr, NULL )' in probe_s and 'ios_lowalloc_translate( addr, len, &real, &owner )' in probe_s)
# Night round 2026-11-11 (agent G8 S1; Resident Evil 5 BF3644, Portal CQEQJX, Q3SG8T, V7F39B, MX074P): a pointer INTO the executable code of a pool image copy
in_code = function(header, 'static inline int ios_iosb32_target_in_code(')
check('the code test leaves NULL, anything below the floor, a wrapping span and the process\'s own guest window alone, and asks the pool probe last',
      in_code.index('if (!p || p < IOS_IOSB_FLOOR) return 0;') < in_code.index('if (p + IOS_IOSB32_SIZE < p) return 0;')
      < in_code.index('p - guest_base < IOS_IOSB_WINDOW_SIZE) return 0;') < in_code.index('exec_pool( p, IOS_IOSB32_SIZE )'))
probe_e = function(signal, 'int ios_iosb_probe_exec_pool( unsigned long long addr, unsigned long long len )\n{')
check('the pool probe asks ios_jit_addr_is_text (the .text of the pool\'s image copies, by address only) for the first and the last byte, and a zero or wrapping span is no hit',
      'ios_jit_addr_is_text( (uintptr_t)addr ) || ios_jit_addr_is_text( (uintptr_t)(addr + len - 1) )' in probe_e and 'if (!len || addr + len < addr) return 0;' in probe_e)
check('the wrapper asks the code test with the same window base and the pool probe, after the first decision, and names the code in the log line; the switch turns both off',
      'ios_iosb32_target_in_code( iosb, ios_wow_base(), ios_iosb_probe_exec_pool )' in wrapper
      and wrapper.index('ios_iosb32_target_dead(') < wrapper.index('ios_iosb32_target_in_code(') and 'inside the executable code of a JIT pool image copy' in wrapper
      and 'if (enabled && (ios_iosb32_target_dead(' in wrapper)

# ---------------------------------------------------------------- 3. the decision and the wrapper, compiled
DECISION = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ios_iosb_dead.h"
static int mapped_n, low_n, mapped_ans, low_ans;
static int pm(unsigned long long a, unsigned long long l) { (void)a; (void)l; mapped_n++; return mapped_ans; }
static int ps(unsigned long long a, unsigned long long l) { (void)a; (void)l; low_n++; return low_ans; }
int main(void)
{
    struct { const char *name; unsigned long long p, base; } cases[] = {
        { "null", 0, 0x7300000000ull },
        { "wraps", 0xfffffffffffffffcull, 0x7300000000ull },
        { "window start", 0x7300000000ull, 0x7300000000ull },
        { "window middle", 0x7300010000ull, 0x7300000000ull },
        { "window last 8", 0x73fffffff8ull, 0x7300000000ull },
        { "window end", 0x7400000000ull, 0x7300000000ull },
        { "window start, no base", 0x7300000000ull, 0 },
        { "low 0x10000", 0x10000ull, 0x7300000000ull },
        { "low 0x10004", 0x10004ull, 0x7300000000ull },
        { "low 0x400000", 0x400000ull, 0x7300000000ull },
        { "below the floor by 4", 0xfffffffcull, 0x7300000000ull },
        { "floor", 0x100000000ull, 0x7300000000ull },
        { "stack", 0x16d2ffb90ull, 0x7300000000ull },
        { "other window", 0x7200010000ull, 0x7300000000ull },
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
        for (int m = 0; m < 2; m++)
            for (int s = 0; s < 2; s++)
            {
                mapped_n = low_n = 0; mapped_ans = m; low_ans = s;
                int r = ios_iosb32_target_dead( cases[i].p, cases[i].base, pm, ps );
                printf("%s|m%d|s%d|%d|%d|%d\n", cases[i].name, m, s, r, mapped_n, low_n);
            }
    printf("enabled=%d\n", ios_iosb_dead_enabled());
    return 0;
}
'''


def decide_expected(p, base, m, s):
    """(dead, mapped probes, low probes) written out from the rules in the header's comment, independent of the code."""
    if p == 0:
        return 0, 0, 0
    if p + 8 >= 1 << 64:
        return 1, 0, 0
    if base and base <= p < base + (1 << 32):
        return 0, 0, 0
    if p < 1 << 32:
        return (0 if s else 1), 0, 1
    return (0 if m else 1), 1, 0


if not shutil.which(cc):
    print('skip the compiled runs: no C compiler')
else:
    with tempfile.TemporaryDirectory(prefix='iosbdead-') as d:
        d = Path(d)
        (d / 'd.c').write_text(DECISION)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Werror', f'-I{nt}', str(d / 'd.c'), '-o', str(d / 'd')], capture_output=True, text=True)
        check('ios_iosb_dead.h compiles on the host (-Wall -Werror)', c.returncode == 0, c.stderr[-2000:])
        if c.returncode == 0:
            def run_d(**env):
                e = {k: v for k, v in os.environ.items() if k != 'MADEIRA_IOSB_DEAD_TARGET'}
                e.update(env)
                return subprocess.run([str(d / 'd')], capture_output=True, text=True, env=e)
            r = run_d()
            rows = [l.split('|') for l in r.stdout.splitlines() if '|' in l]
            cases = {
                'null': (0, 0x7300000000), 'wraps': (0xfffffffffffffffc, 0x7300000000), 'window start': (0x7300000000, 0x7300000000),
                'window middle': (0x7300010000, 0x7300000000), 'window last 8': (0x73fffffff8, 0x7300000000),
                'window end': (0x7400000000, 0x7300000000), 'window start, no base': (0x7300000000, 0), 'low 0x10000': (0x10000, 0x7300000000),
                'low 0x10004': (0x10004, 0x7300000000), 'low 0x400000': (0x400000, 0x7300000000), 'below the floor by 4': (0xfffffffc, 0x7300000000),
                'floor': (0x100000000, 0x7300000000), 'stack': (0x16d2ffb90, 0x7300000000), 'other window': (0x7200010000, 0x7300000000),
            }
            bad = []
            for name, m, s, res, mn, ln in rows:
                p, base = cases[name]
                want = decide_expected(p, base, int(m[1]), int(s[1]))
                if (int(res), int(mn), int(ln)) != want:
                    bad.append('%s %s %s: got %s,%s,%s want %s' % (name, m, s, res, mn, ln, want))
            check('ios_iosb32_target_dead over %d cases (pointer x probe answers): the answer and which probes were asked are the header\'s rules' % len(rows),
                  len(rows) == len(cases) * 4 and not bad, '\n     '.join(bad[:6]))
            by = {(n, m, s): (int(x), int(a), int(b)) for n, m, s, x, a, b in rows}
            check('0x10000 and 0x10004 (this log) are dead when nothing serves them, alive when something does',
                  by[('low 0x10000', 'm0', 's0')][0] == 1 and by[('low 0x10004', 'm1', 's0')][0] == 1
                  and by[('low 0x10000', 'm0', 's1')][0] == 0 and by[('low 0x10004', 'm0', 's1')][0] == 0)
            check('a guest-window pointer and a NULL pointer are never dead, whatever the probes say, and ask nothing; a pointer on the host stack (mapped) is alive',
                  all(by[(n, m, s)] == (0, 0, 0) for n in ('window start', 'window middle', 'window last 8', 'null')
                      for m in ('m0', 'm1') for s in ('s0', 's1'))
                  and by[('stack', 'm1', 's0')] == (0, 1, 0) and by[('stack', 'm0', 's1')] == (1, 1, 0))
            check('the switch: unset or anything but "0" is on, "0" is off',
                  'enabled=1' in r.stdout and 'enabled=1' in run_d(MADEIRA_IOSB_DEAD_TARGET='1').stdout and 'enabled=1' in run_d(MADEIRA_IOSB_DEAD_TARGET='').stdout
                  and 'enabled=1' in run_d(MADEIRA_IOSB_DEAD_TARGET='00').stdout and 'enabled=0' in run_d(MADEIRA_IOSB_DEAD_TARGET='0').stdout)

        # the code test (night round 2026-11-11): over the pointer cases and the probe's answer
        CODE = r'''
#include <stdio.h>
#include "ios_iosb_dead.h"
static int exec_n, exec_ans;
static int pe(unsigned long long a, unsigned long long l) { (void)a; (void)l; exec_n++; return exec_ans; }
int main(void)
{
    struct { const char *name; unsigned long long p, base; } cases[] = {
        { "null", 0, 0x7300000000ull }, { "wraps", 0xfffffffffffffffcull, 0x7300000000ull }, { "low 0x10000", 0x10000ull, 0x7300000000ull },
        { "below the floor by 4", 0xfffffffcull, 0x7300000000ull }, { "floor", 0x100000000ull, 0x7300000000ull },
        { "window start", 0x7300000000ull, 0x7300000000ull }, { "window last 8", 0x73fffffff8ull, 0x7300000000ull }, { "window end", 0x7400000000ull, 0x7300000000ull },
        { "pool code 0x30a6f1354", 0x30a6f1354ull, 0x400000000ull }, { "stack", 0x16d2ffb90ull, 0x7300000000ull },
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
        for (int e = 0; e < 2; e++)
        {
            exec_n = 0; exec_ans = e;
            printf( "%s|e%d|%d|%d\n", cases[i].name, e, ios_iosb32_target_in_code( cases[i].p, cases[i].base, pe ), exec_n );
        }
    return 0;
}
'''
        (d / 'k.c').write_text(CODE)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Werror', f'-I{nt}', str(d / 'k.c'), '-o', str(d / 'k')], capture_output=True, text=True)
        check('ios_iosb32_target_in_code compiles on the host (-Wall -Werror)', c.returncode == 0, c.stderr[-2000:])
        if c.returncode == 0:
            rows = [l.split('|') for l in subprocess.run([str(d / 'k')], capture_output=True, text=True).stdout.splitlines()]
            by = {(n, e): (int(x), int(a)) for n, e, x, a in rows}
            asked = ('pool code 0x30a6f1354', 'stack', 'floor', 'window end')
            check('the pool probe is asked only for a pointer at or above the floor and outside the guest window (NULL, a wrapping span, 0x10000, just below the floor, '
                  'the window\'s first and last 8 bytes: never asked, never dead)',
                  all(by[(n, e)] == (0, 0) for n in ('null', 'wraps', 'low 0x10000', 'below the floor by 4', 'window start', 'window last 8') for e in ('e0', 'e1'))
                  and all(by[(n, e)][1] == 1 for n in asked for e in ('e0', 'e1')))
            check('0x30a6f1354 (BF3644: a return address in kernelbase.dll\'s pool copy) is dead when the pool says it is code and alive when it does not; the stack, the floor and '
                  'the address just past the window follow the pool\'s answer, nothing else',
                  by[('pool code 0x30a6f1354', 'e1')][0] == 1 and by[('pool code 0x30a6f1354', 'e0')][0] == 0 and by[('stack', 'e0')][0] == 0
                  and by[('stack', 'e1')][0] == 1 and by[('window end', 'e0')][0] == 0)

        # the real wrapper, extracted, next to stand-ins; the original is an unconditional set_async_iosb
        WRAP = '''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdarg.h>
#include <unistd.h>
#include "ios_iosb_dead.h"
typedef uint64_t client_ptr_t;
typedef int NTSTATUS;
typedef uintptr_t ULONG_PTR;
static int in_wow;                       /* what in_wow64_call() answers */
static ULONG_PTR base;                   /* what ios_wow_base() answers */
static int set_calls, mapped_n, low_n, mapped_ans, low_ans, exec_n, exec_ans;
static client_ptr_t set_iosb; static NTSTATUS set_status; static ULONG_PTR set_info;
static int in_wow64_call(void) { return in_wow; }
static ULONG_PTR ios_wow_base(void) { return base; }
static void set_async_iosb( client_ptr_t iosb, NTSTATUS status, ULONG_PTR info ) { set_calls++; set_iosb = iosb; set_status = status; set_info = info; }
int ios_iosb_probe_mapped( unsigned long long a, unsigned long long l ) { (void)a; (void)l; mapped_n++; return mapped_ans; }
int ios_iosb_probe_served_low( unsigned long long a, unsigned long long l ) { (void)a; (void)l; low_n++; return low_ans; }
int ios_iosb_probe_exec_pool( unsigned long long a, unsigned long long l ) { (void)a; (void)l; exec_n++; return exec_ans; }
@WRAPPER@
int main(int argc, char **argv)
{
    /* args: in_wow base iosb mapped served repeat */
    in_wow = atoi( argv[1] ); base = (ULONG_PTR)strtoull( argv[2], 0, 0 ); client_ptr_t iosb = strtoull( argv[3], 0, 0 );
    mapped_ans = atoi( argv[4] ); low_ans = atoi( argv[5] ); int repeat = atoi( argv[6] );
    if (argc > 7) exec_ans = atoi( argv[7] );
    for (int i = 0; i < repeat; i++) ios_set_async_iosb( iosb, 0x103 + i, 0x8e );
    fprintf( stdout, "set=%d iosb=%#llx status=%#x info=%#llx mapped_asked=%d low_asked=%d\\n", set_calls, (unsigned long long)set_iosb,
             (unsigned)set_status, (unsigned long long)set_info, mapped_n, low_n );
    if (argc > 7) fprintf( stdout, "exec_asked=%d\\n", exec_n );
    return 0;
}
'''.replace('@WRAPPER@', 'extern int ios_iosb_probe_mapped( unsigned long long, unsigned long long );\n'
            'extern int ios_iosb_probe_served_low( unsigned long long, unsigned long long );\n'
            'extern int ios_iosb_probe_exec_pool( unsigned long long, unsigned long long );\n' + wrapper)
        (d / 'w.c').write_text(WRAP)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', f'-I{nt}', str(d / 'w.c'), '-o', str(d / 'w')],
                           capture_output=True, text=True)
        check('the real ios_set_async_iosb, extracted from server_ios.c, compiles on the host against stand-ins (-Wall -Werror)', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            def run_w(in_wow, b, iosb, mapped, low, repeat=1, **env):
                e = {k: v for k, v in os.environ.items() if k != 'MADEIRA_IOSB_DEAD_TARGET'}
                e.update(env)
                r = subprocess.run([str(d / 'w'), str(in_wow), b, iosb, str(mapped), str(low), str(repeat)], capture_output=True, text=True, env=e)
                return r.stdout.strip(), r.stderr

            def run_w7(in_wow, b, iosb, mapped, low, execa, repeat=1, **env):
                e = {k: v for k, v in os.environ.items() if k != 'MADEIRA_IOSB_DEAD_TARGET'}
                e.update(env)
                r = subprocess.run([str(d / 'w'), str(in_wow), b, iosb, str(mapped), str(low), str(repeat), str(execa)], capture_output=True, text=True, env=e)
                return r.stdout.strip().replace('\n', ' '), r.stderr

            B = '0x7300000000'

            def orig(iosb):
                # the original: an unconditional set_async_iosb( iosb, 0x103, 0x8e ), no probe asked, nothing printed
                v = int(iosb, 0)
                return ('set=1 iosb=%s status=0x103 info=0x8e mapped_asked=0 low_asked=0' % ('0' if v == 0 else hex(v)), '')

            same = []
            for env in ({}, {'MADEIRA_IOSB_DEAD_TARGET': '0'}, {'MADEIRA_IOSB_DEAD_TARGET': '1'}):
                for iosb in ('0x10000', '0x10004', '0x7300010000', '0x16d2ffb90', '0x100000000', '0xfffffffffffffffc', '0'):
                    for m in (0, 1):
                        for s_ in (0, 1):
                            same.append((iosb, m, s_, env, run_w(0, '0', iosb, m, s_, **env)))     # a 64-bit caller: in_wow64_call() is false
            bad = [x[:4] for x in same if x[4] != orig(x[0])]
            check('a 64-bit caller (%d combinations of pointer, probe answers and switch): set_async_iosb once with the same arguments, no probe asked, nothing printed' % len(same),
                  not bad, str(bad[:3]))
            keep = []
            for iosb in ('0', '0x7300010000', '0x7300000000', '0x73fffffff8'):
                for env in ({}, {'MADEIRA_IOSB_DEAD_TARGET': '0'}):
                    for m, s_ in ((0, 0), (1, 1), (0, 1), (1, 0)):
                        keep.append((iosb, m, s_, env, run_w(1, B, iosb, m, s_, **env)))
            badk = [k[:4] for k in keep if k[4] != orig(k[0])]
            check('a 32-bit caller with a NULL status block or one in its own guest window: set_async_iosb once, same arguments, no probe asked, nothing printed (%d runs)' % len(keep),
                  not badk, str(badk[:3]))
            lowv = [run_w(1, B, '0x10000', 0, 1), run_w(1, B, '0x10000', 1, 1), run_w(1, B, '0x16d2ffb90', 1, 0), run_w(1, B, '0x16d2ffb90', 0, 0)]
            check('a 32-bit caller whose pointer is served below the floor, or mapped above it (a host-side cookie): set_async_iosb once, same arguments, nothing printed',
                  lowv[0] == ('set=1 iosb=0x10000 status=0x103 info=0x8e mapped_asked=0 low_asked=1', '')
                  and lowv[1] == ('set=1 iosb=0x10000 status=0x103 info=0x8e mapped_asked=0 low_asked=1', '')
                  and lowv[2] == ('set=1 iosb=0x16d2ffb90 status=0x103 info=0x8e mapped_asked=1 low_asked=0', ''), str(lowv))
            skip_low = run_w(1, B, '0x10000', 0, 0)
            skip_hi = run_w(1, B, '0x16d2ffb90', 0, 0)
            check('a 32-bit caller whose pointer is neither served (below the floor) nor mapped (above it): the store is skipped and the log says which pointer, with the '
                  'status and the information, and how to turn it off',
                  lowv[3] == skip_hi and skip_low[0] == 'set=0 iosb=0 status=0 info=0 mapped_asked=0 low_asked=1' and skip_hi[0] == 'set=0 iosb=0 status=0 info=0 mapped_asked=1 low_asked=0'
                  and '[iosb-dead] ajan16 #1 async I/O completion in a 32-bit process (window 0x7300000000): the status block pointer 0x10000 is not memory; '
                      'the store of status=0x103 information=0x8e is skipped' in skip_low[1] and 'MADEIRA_IOSB_DEAD_TARGET=0 stores anyway' in skip_low[1], str(skip_low))
            off = run_w(1, B, '0x10000', 0, 0, MADEIRA_IOSB_DEAD_TARGET='0')
            check('with MADEIRA_IOSB_DEAD_TARGET=0 the same call stores, exactly as before, and asks nothing', off == ('set=1 iosb=0x10000 status=0x103 info=0x8e mapped_asked=0 low_asked=0', ''), str(off))
            many = run_w(1, B, '0x10000', 0, 0, repeat=40)
            lines = [l for l in many[1].splitlines() if l.startswith('[iosb-dead]')]
            nums = [int(re.search(r'#(\d+)', l).group(1)) for l in lines]
            check('40 skipped stores are logged 8 times and then at the powers of two (16, 32); all 40 are skipped',
                  nums == [1, 2, 3, 4, 5, 6, 7, 8, 16, 32] and many[0].startswith('set=0'), str(nums))
            # night round 2026-11-11 (G8 S1): a mapped pointer that lies in a pool image copy's code (BF3644: 0x30a6f1354, window 0x400000000)
            B4 = '0x400000000'
            codeskip = run_w7(1, B4, '0x30a6f1354', 1, 0, 1)
            codekeep = run_w7(1, B4, '0x30a6f1354', 1, 0, 0)
            check('BF3644: a 32-bit caller whose status block pointer is mapped but lies in the executable code of a pool image copy: the store is skipped, the probes were asked once '
                  'each (mapped, then the pool), and the log names the code and how to turn it off',
                  codeskip[0] == 'set=0 iosb=0 status=0 info=0 mapped_asked=1 low_asked=0 exec_asked=1'
                  and '[iosb-dead] ajan16 #1 async I/O completion in a 32-bit process (window 0x400000000): the status block pointer 0x30a6f1354 is inside the executable code of a '
                      'JIT pool image copy; the store of status=0x103 information=0x8e is skipped' in codeskip[1] and 'MADEIRA_IOSB_DEAD_TARGET=0 stores anyway' in codeskip[1], str(codeskip))
            check('the same pointer when the pool says it is not code (a data section, the host stack or heap): set_async_iosb once with the same arguments, nothing printed',
                  codekeep == ('set=1 iosb=0x30a6f1354 status=0x103 info=0x8e mapped_asked=1 low_asked=0 exec_asked=1', ''), str(codekeep))
            codeoff = run_w7(1, B4, '0x30a6f1354', 1, 0, 1, MADEIRA_IOSB_DEAD_TARGET='0')
            check('with MADEIRA_IOSB_DEAD_TARGET=0 the store into code is done as before and no probe is asked', codeoff == ('set=1 iosb=0x30a6f1354 status=0x103 info=0x8e mapped_asked=0 low_asked=0 exec_asked=0', ''), str(codeoff))
            noask = [run_w7(1, B4, p_, m_, s_, 1) for p_, m_, s_ in (('0x400010000', 0, 0), ('0', 0, 0), ('0x10000', 1, 1))]
            check('a status block in the process\'s own window, a NULL one and a served low one never reach the pool probe (exec_asked=0), as before',
                  all(r_[0].endswith('exec_asked=0') and r_[0].startswith('set=1') for r_ in noask) or noask[1][0].startswith('set=1') and noask[0][0].endswith('exec_asked=0'), str(noask))
            c64 = run_w7(0, '0', '0x30a6f1354', 1, 0, 1)
            check('a 64-bit caller is never asked about code either', c64[0] == 'set=1 iosb=0x30a6f1354 status=0x103 info=0x8e mapped_asked=0 low_asked=0 exec_asked=0', str(c64))

        # the real probes: mapping probe on the Mach calls of this Mac, low probe against stand-in registries
        if sys.platform == 'darwin':
            PROBES = '''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
@PROBES@
int main(void)
{
    size_t pg = (size_t)getpagesize();
    char *a = mmap( 0, 3 * pg, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0 );
    munmap( a + pg, pg );                                   /* [a, a+pg) mapped, hole, [a+2pg, a+3pg) mapped */
    mprotect( a + 2 * pg, pg, PROT_NONE );                  /* mapped without any access */
    printf("inside=%d\\n", ios_iosb_probe_mapped( (unsigned long long)(a + 8), 8 ));
    printf("last8=%d\\n", ios_iosb_probe_mapped( (unsigned long long)(a + pg - 8), 8 ));
    printf("straddle=%d\\n", ios_iosb_probe_mapped( (unsigned long long)(a + pg - 4), 8 ));
    printf("hole=%d\\n", ios_iosb_probe_mapped( (unsigned long long)(a + pg + 8), 8 ));
    printf("protnone=%d\\n", ios_iosb_probe_mapped( (unsigned long long)(a + 2 * pg + 8), 8 ));
    printf("zero page=%d\\n", ios_iosb_probe_mapped( 0x10000, 8 ));
    printf("0x10004=%d\\n", ios_iosb_probe_mapped( 0x10004, 8 ));
    printf("wrap=%d\\n", ios_iosb_probe_mapped( 0xfffffffffffffffcull, 8 ));
    int local = 0;
    printf("stack=%d\\n", ios_iosb_probe_mapped( (unsigned long long)(uintptr_t)&local, 8 ));
    printf("heap=%d\\n", ios_iosb_probe_mapped( (unsigned long long)(uintptr_t)strdup("x"), 1 ));
    return 0;
}
'''.replace('@PROBES@', probe_m)
            (d / 'p.c').write_text(PROBES)
            c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Werror', str(d / 'p.c'), '-o', str(d / 'p')], capture_output=True, text=True)
            check('the real ios_iosb_probe_mapped compiles on this Mac against the Mach headers (-Wall -Werror)', c.returncode == 0, c.stderr[-3000:])
            if c.returncode == 0:
                r = subprocess.run([str(d / 'p')], capture_output=True, text=True)
                check('the mapping probe never touches the addresses it is asked about (the run, which asks about the zero page and a wrapping span, ends normally)', r.returncode == 0, r.stderr[-500:])
                got = dict(l.split('=') for l in r.stdout.splitlines())
                check('mapping probe on real mappings: inside, the last 8 bytes, a PROT_NONE page, the stack and the heap are mapped',
                      got.get('inside') == '1' and got.get('last8') == '1' and got.get('protnone') == '1' and got.get('stack') == '1' and got.get('heap') == '1', r.stdout)
                check('mapping probe: a hole, a store that runs from a mapping into a hole, the zero page (0x10000, 0x10004) and a wrapping span are not mapped',
                      got.get('hole') == '0' and got.get('straddle') == '0' and got.get('zero page') == '0' and got.get('0x10004') == '0' and got.get('wrap') == '0', r.stdout)
        else:
            print('skip the mapping probe run: not macOS')
        LOW = '''
#include <stdio.h>
#include <string.h>
typedef unsigned long uintptr_t_;
static int sub_hit_at, sub_calls, low_hit;
static unsigned long ios_subfloor_translate( unsigned long long addr, void *peb ) { (void)peb; sub_calls++; return (sub_hit_at && addr >= (unsigned long long)sub_hit_at && addr < (unsigned long long)sub_hit_at + 0x1000) ? 1 : 0; }
int ios_lowalloc_translate( unsigned long long addr, unsigned long long len, unsigned long long *real_out, void **owner_out )
{ (void)addr; (void)len; (void)real_out; (void)owner_out; return low_hit; }
@PROBE@
int main(void)
{
    printf("none=%d\\n", ios_iosb_probe_served_low( 0x10000, 8 ));
    sub_hit_at = 0x400000;
    printf("window=%d\\n", ios_iosb_probe_served_low( 0x400010, 8 ));
    printf("window_end=%d\\n", ios_iosb_probe_served_low( 0x400ffc, 8 ));
    printf("outside=%d\\n", ios_iosb_probe_served_low( 0x10000, 8 ));
    low_hit = 1;
    printf("alias=%d\\n", ios_iosb_probe_served_low( 0x10000, 8 ));
    return 0;
}
'''.replace('@PROBE@', probe_s)
        (d / 'l.c').write_text(LOW)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Werror', str(d / 'l.c'), '-o', str(d / 'l')], capture_output=True, text=True)
        check('the real ios_iosb_probe_served_low compiles on the host against stand-in registries (-Wall -Werror)', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            r = subprocess.run([str(d / 'l')], capture_output=True, text=True)
            got = dict(l.split('=') for l in r.stdout.splitlines())
            check('low probe: nothing registered is not served; a sub-floor window or a low alias that covers the 8 bytes is (first or last byte), anything else is not',
                  got == {'none': '0', 'window': '1', 'window_end': '1', 'outside': '0', 'alias': '1'}, r.stdout)

        EXECP = '''
#include <stdint.h>
#include <stdio.h>
/* stand-in for virtual_ios.c: one image copy with .text [0x30a6b0000, 0x30a6b0000 + 0x74638) */
int ios_jit_addr_is_text( uintptr_t addr ) { return addr >= 0x30a6b0000ull && addr < 0x30a6b0000ull + 0x74638ull; }
@PROBE@
int main(void)
{
    printf( "inside=%d\\n", ios_iosb_probe_exec_pool( 0x30a6f1354ull, 8 ) );
    printf( "first=%d\\n", ios_iosb_probe_exec_pool( 0x30a6b0000ull, 8 ) );
    printf( "last=%d\\n", ios_iosb_probe_exec_pool( 0x30a6b0000ull + 0x74638ull - 8, 8 ) );
    printf( "into_end=%d\\n", ios_iosb_probe_exec_pool( 0x30a6b0000ull + 0x74638ull - 4, 8 ) );
    printf( "from_before=%d\\n", ios_iosb_probe_exec_pool( 0x30a6b0000ull - 4, 8 ) );
    printf( "past=%d\\n", ios_iosb_probe_exec_pool( 0x30a6b0000ull + 0x74638ull, 8 ) );
    printf( "before=%d\\n", ios_iosb_probe_exec_pool( 0x30a6afff0ull, 8 ) );
    printf( "stack=%d\\n", ios_iosb_probe_exec_pool( 0x16d2ffb90ull, 8 ) );
    printf( "zero_len=%d\\n", ios_iosb_probe_exec_pool( 0x30a6f1354ull, 0 ) );
    printf( "wrap=%d\\n", ios_iosb_probe_exec_pool( 0xfffffffffffffffcull, 8 ) );
    return 0;
}
'''.replace('@PROBE@', probe_e)
        (d / 'x.c').write_text(EXECP)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Werror', str(d / 'x.c'), '-o', str(d / 'x')], capture_output=True, text=True)
        check('the real ios_iosb_probe_exec_pool compiles on the host against a stand-in for the pool\'s .text test (-Wall -Werror)', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            r = subprocess.run([str(d / 'x')], capture_output=True, text=True)
            got = dict(l.split('=') for l in r.stdout.splitlines())
            check('pool probe: inside, the first 8 bytes and the last 8 bytes of the .text are code; a store that starts before it or runs past its end touches it (first / last byte); '
                  'one wholly outside, the stack, a zero length and a wrapping span are not',
                  got == {'inside': '1', 'first': '1', 'last': '1', 'into_end': '1', 'from_before': '1', 'past': '0', 'before': '0', 'stack': '0', 'zero_len': '0', 'wrap': '0'}, r.stdout)

# ---------------------------------------------------------------- 5. auto-tune and CI
sys.path.insert(0, str(root / 'build/tools'))
import autotune_rules  # noqa: E402
rule = next((r for r in autotune_rules.RULES if r['id'] == 'wow64-native-iosb-completion-store'), None)
check('auto-tune: the known rule (fixed in 596, no line), tied to invoke_system_apc and the low-address write, and silent once "[iosb-dead]" is in the log',
      rule is not None and rule.get('fixedIn') == 596 and rule['lines'] == [] and rule['all'] == [{'has': ['[mach_exc] sym pc=', 'invoke_system_apc']}]
      and {'has': ['[lowalloc] ml966 REFUSED guest 0x1']} in rule['any'] and {'has': ['[av-detail]', 'p0=WRITE p1=0x1']} in rule['any']
      and rule['none'] == [{'has': ['[iosb-dead]']}])
check('auto-tune: the generated Swift carries the rule', '"id": "wow64-native-iosb-completion-store"' in (root / 'app/Madeira/AutoTuneRules.generated.swift').read_text())
fx = root / 'tests/fixtures/autotune/battlenet-agent-console-iosb-2026-10-23'
check('auto-tune: the Battle.net fixture and its expectation (the rule first, known, no line)',
      fx.with_suffix('.log').exists() and fx.with_suffix('.expect').exists()
      and '"wow64-native-iosb-completion-store"' in fx.with_suffix('.expect').read_text() and '"next": []' in fx.with_suffix('.expect').read_text()
      and '"status": "known"' in fx.with_suffix('.expect').read_text())
portal = (root / 'tests/fixtures/autotune/portal-2026-10-21.expect').read_text()
check('auto-tune: the older Portal fixture (the same fault in a cmd.exe child, build 579) matches the rule too', '"wow64-native-iosb-completion-store"' in portal)
fxlog = fx.with_suffix('.log').read_text() if fx.with_suffix('.log').exists() else ''
check('the fixture shows the fault (invoke_system_apc under the write) and names no user, e-mail address, address or app container',
      'invoke_system_apc+0x57c' in fxlog and 'p0=WRITE p1=0x10004' in fxlog and not re.search(r'/Users/|@[A-Za-z0-9.-]+\.[a-z]{2,}|\b(?:\d{1,3}\.){3}\d{1,3}\b|Containers/Data/Application/[0-9A-F-]{36}', fxlog))
code_rule = next((r for r in autotune_rules.RULES if r['id'] == 'wow32-iosb-store-into-pool-code'), None)
check('auto-tune: the build-652 rule for the old crash (fixedIn 652, no line; the ill_handler at kernelbase.dll+0x51358 after the failed band allocation) and its fixture (the rule first, known, no line)',
      code_rule is not None and code_rule.get('fixedIn') == 652 and code_rule['lines'] == []
      and (root / 'tests/fixtures/autotune/resident-evil-5-iosb-store-into-pool-code-2026-11-06.expect').exists()
      and '"wow32-iosb-store-into-pool-code"' in (root / 'tests/fixtures/autotune/resident-evil-5-iosb-store-into-pool-code-2026-11-06.expect').read_text()
      and '"status": "known"' in (root / 'tests/fixtures/autotune/resident-evil-5-iosb-store-into-pool-code-2026-11-06.expect').read_text())
gen_cat = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the settings catalog\'s generator knows the switch (the generated Swift catalog is regenerated in the full tree)',
      '"env.MADEIRA_IOSB_DEAD_TARGET"' in gen_cat and '"sources": ["build/ntdll-unix/ios_iosb_dead.h", "build/ntdll-unix/server_ios.c"]' in gen_cat)
check('CI runs this check', 'tests/host/check-iosb-dead-target.py' in (root / '.github/workflows/build-ipa.yml').read_text())

# ---------------------------------------------------------------- 6. the changed files compile for iOS (when the wine headers exist)
wine_dir = Path(os.environ.get('WINE_SRC', root / 'wine'))
if not (wine_dir / 'build-macos/include/config.h').exists():
    wine_dir = Path.home() / 'madeira-bcd/wine'
config = wine_dir / 'build-macos/include/config.h'
if shutil.which('xcrun') and config.exists():
    try:
        sdk = subprocess.run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sdk = ''
    if sdk:
        flags = ['-fsyntax-only', '-arch', 'arm64', '-isysroot', sdk, '-miphoneos-version-min=17.0', '-O2', '-fPIC',
                 '-fno-strict-aliasing', '-Wno-implicit-function-declaration', '-Wno-int-conversion',
                 '-include', str(config), '-include', str(nt / 'shims/wine_ios_exit.h'), f'-I{nt}', f'-I{nt / "shims"}',
                 f'-I{nt / "../madsync"}', '-DHAVE_LINUX_NTSYNC_H=1',
                 f'-I{wine_dir / "build-macos/dlls/ntdll"}', f'-I{wine_dir / "dlls/ntdll"}', f'-I{wine_dir / "dlls/ntdll/unix"}',
                 f'-I{wine_dir / "build-macos/include"}', f'-I{wine_dir / "include"}', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES',
                 '-DLTC_SOURCE', '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', '-DWINE_UNIX_LIB',
                 '-DWINE_IOS=1', '-Dget_thread_context=ntdll_get_thread_context', '-Dset_thread_context=ntdll_set_thread_context']
        for name in ('server_ios.c', 'signal_arm64_ios.c'):
            r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(nt / name)], capture_output=True, text=True)
            check('%s compiles for iOS (syntax only, pinned wine headers)' % name, r.returncode == 0, r.stderr[-2000:])
else:
    print('skip the iOS syntax compiles: no iPhoneOS SDK or no wine headers (set WINE_SRC)')

print('PASS: iosb-dead-target' if ok else 'FAILED: iosb-dead-target')
sys.exit(0 if ok else 1)
