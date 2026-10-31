#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[term-stack] 32-bit: a 32-bit thread's registers and return addresses at the terminate call (round 624, X1; log only); no Wine runs.

Amnesia: The Dark Descent (W0BX37) and 45 more 32-bit logs of the archive end with "[term-stack] no FEX state on this thread": the dump reads
the 64-bit emulator's state (TEB.ChpeV2CpuAreaInfo), which a WoW64 thread does not have, so nothing named the code that ended the process.
process_ios.c's ios_wow32_stack_dump prints the 32-bit registers WoW64 saved in the thread's CPU area at its last system call (the terminate
call) and the stack dwords that are return addresses (a call instruction right before them), with module+RVA from the PEB32's module list.
thread_ios.c's [cxx-type] asks for the same dump for the first 4 throws of a 32-bit guest (tests/host/check-cxx-type-diag.py).

The production functions are cut out of process_ios.c and compiled on the host against a fake guest window: a TEB32 -> PEB32 -> loader list
with two modules, code with a direct call (E8), an indirect call through memory (FF 15) and through a register (FF D0) before three return
addresses, a module address with no call before it (left out), a value outside every module (left out), an unreadable stack, and a 64-bit
thread (nothing printed). Textually: the call site is NtTerminateProcess's "no FEX state" branch, after the old line, and the code only reads.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/process_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


f0 = src.index('static int ios_t32_read( uint64_t addr, void *buf, size_t len )')
f1 = src.index('\n}\n', src.index('void ios_wow32_stack_dump( const char *tag )')) + 3
fn = src[f0:f1]
check('the dump only reads (no write, protection change or stop)',
      not [t for t in ('mach_vm_write', 'NtWriteVirtualMemory', 'NtProtectVirtualMemory', 'mprotect', 'NtTerminate', 'memcpy(') if t in fn], fn[:200])
check('every guest read goes through the fault-safe Mach read', fn.count('mach_vm_read_overwrite') == 1 and fn.count('ios_t32_read(') >= 10)
term = src[src.index('NTSTATUS WINAPI NtTerminateProcess('):]
term = term[:term.index('\n}\n')]
old = term.index('"[term-stack] no FEX state on this thread (cpuarea=%p)\\n"')
check('NtTerminateProcess calls it in the "no FEX state" branch, after the old line (which stays for the tools that read it)',
      term.index('ios_wow32_stack_dump( "terminate" );') > old and term.count('ios_wow32_stack_dump(') == 1)
thr = (root / 'build/ntdll-unix/thread_ios.c').read_text()
check('[cxx-type] asks for it for the first 4 throws of a 32-bit guest only',
      'if (guest32)' in thr and 'if (InterlockedIncrement( &stacks ) <= 4) ios_wow32_stack_dump( "throw" );' in thr)

harness = r'''
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
typedef unsigned short USHORT; typedef int LONG; typedef unsigned long long ULONG_PTR;
typedef uint64_t mach_vm_address_t, mach_vm_size_t; typedef int kern_return_t;
#define KERN_SUCCESS 0
#define IMAGE_FILE_MACHINE_I386 0x14c
typedef struct { uint32_t ContextFlags, Dr[6], FloatSave[28], SegGs, SegFs, SegEs, SegDs, Edi, Esi, Ebx, Edx, Ecx, Eax, Ebp, Eip, SegCs, EFlags, Esp, SegSs; } I386_CONTEXT;
typedef struct { void *Peb; LONG WowTebOffset; } TEB;
static unsigned char *mem; static size_t mem_size;
static uint64_t mach_task_self(void) { return 0; }
static kern_return_t mach_vm_read_overwrite(uint64_t task, mach_vm_address_t a, mach_vm_size_t n, mach_vm_address_t to, mach_vm_size_t *got)
{
    (void)task;
    if (a < (uint64_t)mem || a + n > (uint64_t)mem + mem_size) return 1;
    memcpy((void *)to, (void *)a, n); *got = n; return KERN_SUCCESS;
}
static TEB *the_teb; static I386_CONTEXT *the_ctx; static uint64_t the_win;
static TEB *NtCurrentTeb(void) { return the_teb; }
void *get_cpu_area(USHORT m) { return m == IMAGE_FILE_MACHINE_I386 ? the_ctx : NULL; }
static uint64_t ios_wow_base_for_peb(void *peb) { (void)peb; return the_win; }
''' + fn + r'''
#define G(a) (mem + 0x1000 + ((a) - 0x400000))
static void p32(uint32_t a, uint32_t v) { memcpy(G(a), &v, 4); }
static void name(uint32_t a, const char *s) { size_t i; for (i = 0; s[i]; i++) { uint16_t c = (unsigned char)s[i]; memcpy(G(a) + i * 2, &c, 2); } }
static void mod(uint32_t e, uint32_t next, uint32_t base, uint32_t size, uint32_t nameaddr, const char *n)
{
    uint16_t len = (uint16_t)(strlen(n) * 2);
    p32(e, next); p32(e + 0x18, base); p32(e + 0x20, size); memcpy(G(e + 0x2c), &len, 2); p32(e + 0x2c + 4, nameaddr); name(nameaddr, n);
}
int main(int argc, char **argv)
{
    static I386_CONTEXT ctx;
    mem_size = 0x200000; mem = calloc(1, mem_size);
    the_teb = (TEB *)mem; the_teb->WowTebOffset = 0x1000;          /* TEB32 at guest 0x400000 */
    the_win = (uint64_t)(mem + 0x1000) - 0x400000;
    p32(0x400000 + 0x30, 0x401000);                                 /* TEB32.Peb */
    p32(0x401000 + 0x0c, 0x402000);                                 /* PEB32.Ldr */
    p32(0x402000 + 0x0c, 0x403000);                                 /* InLoadOrderModuleList.Flink */
    mod(0x403000, 0x403100, 0x410000, 0x10000, 0x404000, "game.exe");
    mod(0x403100, 0x40200c, 0x430000, 0x10000, 0x404100, "kernel32.dll");
    *G(0x410100) = 0xe8;                                             /* call rel32 -> returns to 0x410105 */
    *G(0x430200) = 0xff; *G(0x430201) = 0x15;                        /* call [disp32] -> returns to 0x430206 */
    *G(0x410300) = 0xff; *G(0x410301) = 0xd0;                        /* call eax -> returns to 0x410302 */
    ctx.Eip = 0x77001234; ctx.Esp = 0x480000; ctx.Ebp = 0x480040; ctx.Eax = 0x410000; ctx.Ecx = 1; ctx.Edx = 2; ctx.Ebx = 3; ctx.Esi = 4; ctx.Edi = 5;
    p32(0x480000, 0x12345678); p32(0x480004, 0x410105); p32(0x480008, 0x430206); p32(0x48000c, 0x410200); p32(0x480010, 0x410302);
    the_ctx = &ctx;
    if (argc > 1 && !strcmp(argv[1], "unreadable")) ctx.Esp = 0x7ff000;
    if (argc > 1 && !strcmp(argv[1], "64bit")) the_ctx = NULL;
    ios_wow32_stack_dump("terminate");
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the production functions compile on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-3000:])
    if cc.returncode == 0:
        def run(*a):
            return subprocess.run([str(exe), *a], capture_output=True, text=True).stderr
        out = run()
        check('the registers WoW64 saved, the TEB32 and the module count',
              '[term-stack] 32-bit terminate eip=77001234 esp=00480000 ebp=00480040 eax=00410000 ecx=00000001 edx=00000002 ebx=00000003 '
              'esi=00000004 edi=00000005 (WoW64\'s saved registers at this system call;' in out and 'teb32 00400000, 2 modules)' in out, out)
        check('a direct call, an indirect call through memory and one through a register name their return addresses with module+RVA',
              '[term-stack] 32-bit sp+004: ret 00410105 game.exe+0x105 (a direct call before it)' in out
              and '[term-stack] 32-bit sp+008: ret 00430206 kernel32.dll+0x206 (an indirect call before it)' in out
              and '[term-stack] 32-bit sp+010: ret 00410302 game.exe+0x302 (an indirect call before it)' in out, out)
        check('a module address with no call before it and a value outside every module are left out',
              'sp+000' not in out and 'sp+00c' not in out and out.count(': ret ') == 3, out)
        check('the summary counts what was read', '[term-stack] 32-bit terminate: 256 of 256 stack dwords read, 3 return address(es)' in out, out)
        un = run('unreadable')
        check('an unreadable stack prints the registers and says nothing was read',
              'esp=007ff000' in un and '0 of 256 stack dwords read, 0 return address(es)' in un, un)
        check('a 64-bit thread (no I386 CPU area) prints nothing', run('64bit') == '', run('64bit'))

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
