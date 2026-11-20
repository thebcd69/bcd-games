#!/usr/bin/env python3
"""[wow-null32]: failure-path-only diagnostic for a 32-bit guest's use of the NULL page (ios_wow_null_diag.h); no Wine runs.

Batman: Arkham City GOTY (Steam 200260, build 578, iPad16,5, C7DGSN): BatmanAC.exe starts BmLauncher.exe, a .NET Framework program run
by Wine Mono, and its main thread dies in libmono (guest EIP 0x778635C9 = libmono-2.0-x86.dll+0x2135C9) on `mov dword [ebx+0x10c], 1`
with EBX = 0: Mono's SGen allocator marks the thread's critical region through the thread info it reads from a TLS slot (the inlined
TlsGetValue: TEB32->TlsSlots[key]), and the slot read NULL. The log has the host registers and the stale context of the last
syscall, but nothing about the guest's stack or its TLS cells, so neither the function nor the state of the slot can be named.

Source checks (every host): one header, included once after ios_x87_int64_diag.h and before the ajan2 block; one call site inside the
UNHANDLED report right after ios_x87_int64_diag and before [fault-full]; no write/protect/exit/stop call; every read goes through
ios_av_args_read (no raw dereference of a guest address); gated on a data fault in JIT code, on a 32-bit window (a 4 GB aligned base in
x19, the address temporary x24 equal to the fault address) and on the guest address being below 0x10000; capped at 3 reports; the 32-bit
structure offsets equal the pinned wine headers' (include/winternl.h) when they are present.

Compiled runs (macOS only: the helpers read real Mach memory): the production header is compiled with a fake thread state over a
constructed 32-bit window (TEB32, PEB32, PEB_LDR_DATA32 with libmono and the exe, a guest stack with a return address into libmono, the TLS
bitmap and cells) built from the numbers of C7DGSN, and:
  - the Batman shape prints the registers, the stack with libmono-2.0-x86.dll+0x2135d9 for the return address, the TLS cells that hold a
    value (with module+RVA, and "(NOT allocated)" for an index the bitmap does not hold) and the allocated indices whose cell is NULL;
  - a fault that is not a 32-bit NULL-page access, a pc outside the pool, other exception kinds and a repeated pc print nothing; the
    fourth distinct report is capped; a TEB whose Self does not match, an unreadable stack and unreadable TLS cells print what they can.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
signal_src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
header_path = root / 'build/ntdll-unix/ios_wow_null_diag.h'
header = header_path.read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- source shape
inc = '#include "ios_wow_null_diag.h"'
check('the header is included exactly once, after ios_x87_int64_diag.h and before the ajan2 block and the Mach exception thread',
      signal_src.count(inc) == 1 and signal_src.index('#include "ios_x87_int64_diag.h"') < signal_src.index(inc)
      < signal_src.index('/* ajan2-tani (2026-10-21): FAILURE-PATH DIAGNOSTICS') < signal_src.index('static void *ios_mach_exception_thread( void *arg )'))
call = 'ios_wow_null_diag( &state, thread_teb, req->exception, fault_addr );'
check('one call site', signal_src.count(call) == 1)
site = signal_src.index(call)
unhandled = signal_src.rindex('"[mach_exc] UNHANDLED #%d', 0, site)
check('the call is inside the UNHANDLED report, right after ios_x87_int64_diag and before [fault-full]',
      unhandled < signal_src.index('ios_x87_int64_diag( &state, req->exception, fault_addr );') < site
      < signal_src.index('madeira-bcd [fault-full]: every register', site))

bus_call = 'ios_wow_null_diag_bus( &((ucontext_t *)sigcontext)->uc_mcontext->__ss, (uintptr_t)NtCurrentTeb(), (uintptr_t)siginfo->si_addr );'
check('build 591: one call from the BSD path, inside "BUS->AV: unreadable target", after the [bus-regs] block and before bus_fatal',
      signal_src.count(bus_call) == 1 and signal_src.rindex('BUS->AV: unreadable target', 0, signal_src.index(bus_call)) > 0
      and signal_src.index('[bus-regs] ml1043 x0=') < signal_src.index(bus_call) < signal_src.index('goto bus_fatal;', signal_src.index(bus_call))
      and 'goto bus_fatal;' not in signal_src[signal_src.index(bus_call) - 400:signal_src.index(bus_call)].replace('goto bus_fatal;\n            }', ''))
forbidden = ('thread_set_state', 'thread_suspend', 'thread_terminate', 'vm_protect', 'mprotect(', 'mach_vm_write', 'vm_write(', 'memset(',
             'memcpy(', 'mmap(', 'free(', 'malloc(', 'abort(', 'exit(', 'sigaction', 'fopen(', 'open(', 'mach_vm_read_overwrite')
check('no write, protect, stop, exit or file call, and no direct memory read (ios_av_args_read is the one reader)',
      not [f for f in forbidden if f in header], str([f for f in forbidden if f in header]))
raw_deref = re.findall(r'\*\s*\(\s*(?:const\s+)?(?:volatile\s+)?(?:u?int\d+_t|void|char|unsigned\w*|uintptr_t)\s*\*\s*\)', header)
check('no raw dereference of a guest address', not raw_deref, str(raw_deref))
check('gated on a data fault in JIT code, on a 32-bit window access and on the NULL page',
      'exception != EXC_BAD_ACCESS || !thread_teb || !ios_av_args_pc_is_jit( pc )' in header
      and '(base & 0xffffffffULL) == 0 && base >= 0x100000000ULL && fault_addr >= base && fault_addr - base < 0x10000' in header
      and 'st->__x[24] == fault_addr' in header)
check('capped at 3 reports, a repeated host pc is not a report',
      '> 3) return;' in header and 'if (seen[k] == pc) return;' in header and 'static volatile uint64_t seen[3];' in header)
mapping = ('eax = (uint32_t)st->__x[4]; ecx = (uint32_t)st->__x[7]; edx = (uint32_t)st->__x[5]; ebx = (uint32_t)st->__x[6];',
           'esp = (uint32_t)st->__x[8]; ebp = (uint32_t)st->__x[9]; esi = (uint32_t)st->__x[10]; edi = (uint32_t)st->__x[11];')
check('the x86 register mapping is FEX x32::SRA: EAX x4, ECX x7, EDX x5, EBX x6, ESP x8, EBP x9, ESI x10, EDI x11', all(m in header for m in mapping))
check('the module list is bounded (96 entries) and walked once', 'walked++ < 96' in header and 'nmods = ios_wn32_modules( base, peb32, mods, 96 );' in header
      and header.count('ios_wn32_modules( base') == 1)

wine = Path(os.environ.get('WINE_SRC', root / 'wine'))
if (wine / 'include/winternl.h').exists() and shutil.which('xcrun'):
    src = r'''
#include <stddef.h>
#include "windef.h"
#include "winternl.h"
_Static_assert(offsetof(TEB32, Tib.Self) == 0x18, "teb self");
_Static_assert(offsetof(TEB32, Peb) == 0x30, "teb peb");
_Static_assert(offsetof(TEB32, TlsSlots) == 0xe10, "teb tls slots");
_Static_assert(offsetof(TEB32, TlsExpansionSlots) == 0xf94, "teb tls expansion");
_Static_assert(offsetof(PEB32, LdrData) == 0x0c, "peb ldr");
_Static_assert(offsetof(PEB32, TlsBitmapBits) == 0x44, "peb tls bits");
_Static_assert(offsetof(PEB_LDR_DATA32, InLoadOrderModuleList) == 0x0c, "ldr load order");
int main(void) { return 0; }
'''
    wanted = {'IOS_WN32_TEB_SELF': 0x18, 'IOS_WN32_TEB_PEB': 0x30, 'IOS_WN32_TEB_TLSSLOTS': 0xe10, 'IOS_WN32_TEB_TLSEXP': 0xf94,
              'IOS_WN32_PEB_LDR': 0x0c, 'IOS_WN32_PEB_TLSBITS': 0x44, 'IOS_WN32_LDR_LOADORDER': 0x0c}
    got = {m.group(1): int(m.group(2), 16) for m in re.finditer(r'#define (IOS_WN32_\w+)\s+(0x[0-9a-f]+)', header)}
    check('the header\'s offsets are the ones the pinned headers assert below', all(got.get(k) == v for k, v in wanted.items()), str(got))
    with tempfile.TemporaryDirectory(prefix='wn32-off-') as d:
        (Path(d) / 'off.c').write_text(src)
        # -arch x86_64 (the M5 Max, build 672): the 32-bit structs are fixed-width, so the offsets are the same on any
        # host; wine's headers find va_list on the x86_64 path the Intel VM had (on arm64, winternl.h line 5030 has none).
        r = subprocess.run(['xcrun', '-sdk', 'macosx', 'clang', '-arch', 'x86_64', '-fsyntax-only', f'-I{wine / "include"}', f'-I{wine / "build-macos/include"}',
                            '-D__WINESRC__', str(Path(d) / 'off.c')], capture_output=True, text=True)
        check('the pinned winternl.h has TEB32/PEB32/PEB_LDR_DATA32 at those offsets', r.returncode == 0, r.stderr[-2000:])
    # LDR_DATA_TABLE_ENTRY32 is not in winternl.h: its layout is the 64-bit struct's with 4-byte pointers (3 list entries, DllBase,
    # EntryPoint, SizeOfImage, FullDllName, BaseDllName).
    text = (wine / 'include/winternl.h').read_text()
    block = text[text.index('typedef struct _LDR_DATA_TABLE_ENTRY\n'):]
    block = block[:block.index('} LDR_DATA_TABLE_ENTRY')]
    order = [m.group(0) for m in re.finditer(r'InLoadOrderLinks|InMemoryOrderLinks|InInitializationOrderLinks|DllBase|EntryPoint|SizeOfImage|FullDllName|BaseDllName', block)]
    check('LDR_DATA_TABLE_ENTRY order gives DllBase 0x18, SizeOfImage 0x20, BaseDllName 0x2c with 4-byte pointers',
          order[:8] == ['InLoadOrderLinks', 'InMemoryOrderLinks', 'InInitializationOrderLinks', 'DllBase', 'EntryPoint', 'SizeOfImage', 'FullDllName', 'BaseDllName']
          and '#define IOS_WN32_LDRE_DLLBASE  0x18' in header and '#define IOS_WN32_LDRE_SIZE     0x20' in header and '#define IOS_WN32_LDRE_NAME     0x2c' in header, str(order))
else:
    print('skip the offset check against the pinned headers: no wine/include/winternl.h')

# ---------------------------------------------------------------- compiled runs
cc = os.environ.get('CC', 'cc')
PRELUDE = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <unistd.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>

#define arm_thread_state64_t fake_arm_thread_state64_t
typedef struct { uint64_t __x[29]; uint64_t __fp, __lr, __sp, __pc; } fake_arm_thread_state64_t;
#undef __darwin_arm_thread_state64_get_pc
#define __darwin_arm_thread_state64_get_pc(ts) ((ts).__pc)
unsigned long long ios_jit_module_base_for_va( unsigned long long va, unsigned long long *size_out ) { (void)va; (void)size_out; return 0; }
static const char *ios_pe_module_name( uint64_t base ) { (void)base; return "?"; }
void *ios_jit_rx_base_global;
size_t ios_jit_pool_size_global;
int ios_jit_low_contains( uintptr_t a ) { (void)a; return 0; }
#define IOS_WN32_TEB_WOWOFF 0x40   /* the fake TEB64's WowTebOffset */
'''

MAIN = r'''
/* arm64 macOS reserves 0x1000000000-0x7000000000 in every process (the M5 Max, build 672): the window moves to
   0x15000000000 there; on the Intel host it stays at 0x5000000000. */
#if defined(__aarch64__)
#define WIN 0x15000000000ULL
#else
#define WIN 0x5000000000ULL
#endif
/* the host code pool: 0x300000000 on Intel; on arm64 macOS the system libraries reach past it */
#if defined(__aarch64__)
#define HOSTAREA 0x20000000000ULL
#else
#define HOSTAREA 0x300000000ULL
#endif
#define TEB64_G 0x300000u      /* the 64-bit TEB, guest-relative: the host TEB64 is WIN + this */
#define TEB32_G 0x302000u
#define PEB32_G 0x311000u
#define LDR_G   0x350000u
static uint64_t alloc_at(uint64_t a, size_t n)
{
    /* Host page by host page (M5 Max, build 672: 16 KB pages there, 4 KB on the Intel VM): the window's pieces sit at
       4 KB offsets, so two may share a host page; a page an earlier piece already mapped is fine. */
    const uint64_t pg = vm_page_size, lo = a & ~(pg - 1), hi = (a + n + pg - 1) & ~(pg - 1);
    for (uint64_t p = lo; p < hi; p += pg)
    {
        mach_vm_address_t m = p;
        kern_return_t k = mach_vm_allocate(mach_task_self(), &m, pg, VM_FLAGS_FIXED);
        if (k == KERN_NO_SPACE)
        {   /* taken as already there only when it is a read-write page (one an earlier piece of this program mapped) */
            mach_vm_address_t q = p; mach_vm_size_t qs = 0; vm_region_basic_info_data_64_t ri;
            mach_msg_type_number_t rc = VM_REGION_BASIC_INFO_COUNT_64; mach_port_t ro;
            if (mach_vm_region(mach_task_self(), &q, &qs, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&ri, &rc, &ro) == KERN_SUCCESS
                && q <= p && (ri.protection & 3) == 3) continue;
        }
        if (k != KERN_SUCCESS) { puts("alloc-failed"); exit(3); }
    }
    return a;
}
static void w32(uint64_t a, uint32_t v) { memcpy((void *)a, &v, 4); }
static void w16(uint64_t a, uint16_t v) { memcpy((void *)a, &v, 2); }

/* one LDR_DATA_TABLE_ENTRY32 at guest address e: links, DllBase, SizeOfImage, BaseDllName (UTF-16 at namebuf) */
static void ldr_entry(uint32_t e, uint32_t next, uint32_t base, uint32_t size, const char *name, uint32_t namebuf)
{
    size_t n = strlen(name);
    w32(WIN + e, next);
    w32(WIN + e + 0x18, base);
    w32(WIN + e + 0x20, size);
    w16(WIN + e + 0x2c, (uint16_t)(n * 2)); w16(WIN + e + 0x2e, (uint16_t)(n * 2 + 2)); w32(WIN + e + 0x30, namebuf);
    for (size_t i = 0; i <= n; i++) w16(WIN + namebuf + i * 2, (uint16_t)name[i]);
}

struct world { uint64_t pool; fake_arm_thread_state64_t st; uint64_t teb64; };

/* the window of C7DGSN: BmLauncher.exe at 0x400000, libmono-2.0-x86.dll at 0x77650000 (+0x2a1000), TEB32 0x302000 (TEB64 0x300000), PEB32 0x311000 */
static struct world make(int with_ldr, int with_stack, int with_tls)
{
    static int built;
    static uint64_t pool;
    struct world w;
    memset(&w, 0, sizeof(w));
    if (!built)
    {
        built = 1;
        pool = alloc_at(HOSTAREA + 0x1000000, 0x10000);
        alloc_at(WIN + 0x300000, 0x20000);          /* TEB64 page(s), TEB32 at +0x2000, PEB32 at 0x311000 */
        alloc_at(WIN + 0x350000, 0x10000);          /* PEB_LDR_DATA32, entries, names */
        alloc_at(WIN + 0x14fc000, 0x8000);          /* the stack around ESP 0x14ff0a0 */
        w32(WIN + TEB64_G + 0x40, 0x2000);          /* the fake TEB64's WowTebOffset */
        w32(WIN + TEB32_G + 0x18, TEB32_G);         /* NT_TIB32.Self */
        w32(WIN + TEB32_G + 0x30, PEB32_G);         /* Peb */
        w32(WIN + PEB32_G + 0x0c, LDR_G);           /* LdrData */
        w32(WIN + PEB32_G + 0x44, 0x000e0001);      /* TlsBitmapBits: 0, 17, 18, 19 */
        w32(WIN + PEB32_G + 0x48, 0);
        w32(WIN + LDR_G + 0x0c, LDR_G + 0x100);     /* InLoadOrderModuleList.Flink -> entry 1 */
        w32(WIN + LDR_G + 0x10, LDR_G + 0x200);
        ldr_entry(LDR_G + 0x100, LDR_G + 0x200, 0x400000, 0x8d9c34, "BmLauncher.exe", LDR_G + 0x1000);
        ldr_entry(LDR_G + 0x200, LDR_G + 0x0c, 0x77650000, 0x2a1000, "libmono-2.0-x86.dll", LDR_G + 0x1100);
        w32(WIN + TEB32_G + 0xe10 + 4 * 17, 0x1e12430);                 /* index 17: a heap pointer */
        w32(WIN + TEB32_G + 0xe10 + 4 * 18, 0x77650000 + 0x2a0b04);     /* index 18: an address inside libmono's data */
        w32(WIN + TEB32_G + 0xe10 + 4 * 40, 0x1234);                    /* index 40: not allocated in the bitmap */
        /* index 19 is allocated and NULL: the cell the code read */
        /* the stack at ESP 0x14ff0a0: the four saved registers, the return address into libmono, the three arguments */
        {
            uint32_t stack[16] = { 0x18, 5, 0, 0x1e1b628, 0x778636d9, 0x1e1b628, 0x18, 5, 0x1e12430, 0, 0x778a0000, 0x7ffdf000, 0, 0, 0x400123, 0 };
            memcpy((void *)(WIN + 0x14ff0a0), stack, sizeof(stack));
        }
    }
    w.pool = pool;
    ios_jit_rx_base_global = (void *)pool; ios_jit_pool_size_global = 0x10000;
    w.st.__pc = pool + 0x1000 + 4 * (uint64_t)(with_ldr + 2 * with_stack + 4 * with_tls);   /* a distinct pc per scenario */
    w.teb64 = WIN + TEB64_G;
    /* the registers of C7DGSN: EAX 0, ECX = TEB32, EDX 1, EBX 0, ESI 0x18, EDI 5, EBP 0x1e1b628, ESP 0x14ff0a0; fault at guest 0x10c */
    w.st.__x[4] = 0; w.st.__x[7] = TEB32_G; w.st.__x[5] = 1; w.st.__x[6] = 0;
    w.st.__x[8] = 0x14ff0a0; w.st.__x[9] = 0x1e1b628; w.st.__x[10] = 0x18; w.st.__x[11] = 5;
    w.st.__x[19] = WIN; w.st.__x[24] = WIN + 0x10c;
    return w;
}
'''

SCENARIOS = r'''
int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    struct world w;

    if (!strcmp(s, "batman")) {
        w = make(0, 0, 0);
        w.st.__x[5] = 0x77650000 + 0x1234;                 /* EDX into libmono for the register line */
        ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x10c);
        return 0;
    }
    if (!strcmp(s, "repeat")) {
        w = make(0, 0, 0);
        ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x10c);
        ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x10c);   /* the same pc: nothing */
        return 0;
    }
    if (!strcmp(s, "cap")) {
        for (int i = 0; i < 6; i++) {
            w = make(i & 1, (i >> 1) & 1, (i >> 2) & 1);
            ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x10c);
        }
        return 0;
    }
    if (!strcmp(s, "benign")) {
        w = make(0, 0, 0);
        ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_INSTRUCTION, WIN + 0x10c);          /* another exception kind */
        ios_wow_null_diag(&w.st, w.teb64, 3, WIN + 0x10c);
        w.st.__x[24] = WIN + 0x20000; ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x20000);   /* not the NULL page */
        w.st.__x[24] = WIN + 0x10c;
        ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x10d);               /* x24 does not match the fault address */
        w.st.__x[19] = 0x5000000010ULL; ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, w.st.__x[19] + 0x10c); /* x19 not a window base */
        w.st.__x[19] = WIN; w.st.__x[24] = WIN + 0x10c;
        w.st.__pc = 0x10000000ULL;                                                       /* a pc outside the pool */
        ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x10c);
        w.st.__pc = (uint64_t)ios_jit_rx_base_global + 0x2000;
        ios_wow_null_diag(&w.st, 0, EXC_BAD_ACCESS, WIN + 0x10c);                        /* no TEB */
        printf("benign-done\n");
        return 0;
    }
    if (!strcmp(s, "bus")) {
        w = make(0, 0, 0);
        w.st.__x[24] = WIN + 0x20;                                                        /* the temporary does not hold the address (an LDAR address register) */
        ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x10c);                   /* the Mach path's gate: nothing */
        ios_wow_null_diag_bus(&w.st, w.teb64, WIN + 0x10c);                               /* the BSD path: reports */
        w.st.__pc += 4;
        ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x10c);                   /* strict again: nothing */
        w.st.__pc += 4; w.st.__x[19] = 0x5000000010ULL;
        ios_wow_null_diag_bus(&w.st, w.teb64, w.st.__x[19] + 0x10c);                      /* the BSD path still needs a window base */
        w.st.__pc += 4; w.st.__x[19] = WIN;
        ios_wow_null_diag_bus(&w.st, w.teb64, WIN + 0x20000);                             /* ... and the NULL page */
        printf("bus-done\n");
        return 0;
    }
    if (!strcmp(s, "noteb")) {
        w = make(0, 0, 0);
        w32(WIN + TEB32_G + 0x18, 0x999000);                                              /* Self does not match: not this TEB */
        ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x10c);
        w32(WIN + TEB32_G + 0x18, TEB32_G);
        return 0;
    }
    if (!strcmp(s, "nostack")) {
        w = make(1, 0, 0);
        w.st.__x[8] = 0x6000000;                                                          /* ESP in unmapped memory */
        ios_wow_null_diag(&w.st, w.teb64, EXC_BAD_ACCESS, WIN + 0x10c);
        return 0;
    }
    return 0;
}
'''

if sys.platform != 'darwin' or not shutil.which(cc):
    print('skip the compiled runs: macOS and a C compiler are needed')
else:
    av_header = (root / 'build/ntdll-unix/ios_av_args_diag.h').read_text()
    with tempfile.TemporaryDirectory(prefix='wownull-') as d:
        d = Path(d)
        (d / 'h.c').write_text(PRELUDE + '\n' + av_header + '\n' + header + '\n' + MAIN + SCENARIOS)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function', str(d / 'h.c'), '-o', str(d / 'h')],
                           capture_output=True, text=True)
        check('the production header compiles (host cc, -Wall -Wextra -Werror)', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            def run(scn):
                return subprocess.run([str(d / 'h'), scn], capture_output=True, text=True)

            r = run('batman')
            lines = [l for l in r.stderr.splitlines() if l.startswith('[wow-null32]')]
            check('the Batman shape: the first line names the NULL-page address, the registers of the log and the module count',
                  len(lines) >= 4 and 'rev=ajan7-arkham #1' in lines[0] and 'guest address 0x10c' in lines[0] and 'EBX=0x0' in lines[0] and 'ESI=0x18' in lines[0]
                  and 'EDI=0x5' in lines[0] and 'EBP=0x1e1b628' in lines[0] and 'ESP=0x14ff0a0' in lines[0] and '(2 modules' in lines[0], r.stderr)
            check('registers that point into a module are named (EDX = libmono+0x1234)',
                  any('registers that point into a module:' in l and 'EDX=0x77651234(libmono-2.0-x86.dll+0x1234)' in l and 'EBX' not in l.split('module:')[1] for l in lines), r.stderr)
            check('the stack: the return address is libmono-2.0-x86.dll+0x2136d9 and the arguments are the three of the log',
                  any('guest stack from ESP' in l and '+10:778636d9(libmono-2.0-x86.dll+0x2136d9)' in l and '+14:1e1b628' in l and '+18:18' in l and '+1c:5' in l
                      and '+3c:0' in l for l in lines), r.stderr)
            check('the TLS cells: the set ones with their value and module+RVA, the unallocated one marked',
                  any('TEB32=0x302000 PEB32=0x311000 TlsBitmapBits=0x00000000000e0001' in l and '3 cell(s) hold a value:' in l and '[17]=0x1e12430' in l
                      and '[18]=0x77650000+0x2a0b04' not in l and '[18]=0x77650b04' not in l and '(libmono-2.0-x86.dll+0x2a0b04)' in l
                      and '[40]=0x1234(NOT allocated)' in l for l in lines), r.stderr)
            check('the allocated indices whose cell is NULL are listed (0 and 19)',
                  any('2 allocated index(es) whose cell is NULL in this thread: 0 19' in l for l in lines), r.stderr)
            check('five lines for one report', len(lines) == 5, r.stderr)

            r = run('repeat')
            check('the same host pc twice is one report', len([l for l in r.stderr.splitlines() if 'rev=ajan7-arkham #' in l]) == 1, r.stderr)
            r = run('cap')
            n = len([l for l in r.stderr.splitlines() if 'rev=ajan7-arkham #' in l])
            check('distinct pcs: 3 reports, then silence', n == 3, r.stderr)
            r = run('benign')
            check('other exception kinds, an address beyond the NULL page, a mismatched x24, a non-window x19, a pc outside the pool and no TEB print nothing',
                  r.stderr == '' and r.stdout.strip() == 'benign-done', r.stderr + r.stdout)
            r = run('bus')
            n = len([l for l in r.stderr.splitlines() if 'rev=ajan7-arkham #' in l])
            check('build 591, the BSD path: with x24 not holding the address the Mach gate prints nothing, the bus wrapper prints one report, and the '
                  'strict gate is strict again afterwards; the bus wrapper still wants a 4 GB window base and the NULL page',
                  n == 1 and 'bus-done' in r.stdout, r.stderr + r.stdout)
            r = run('noteb')
            lines = [l for l in r.stderr.splitlines() if l.startswith('[wow-null32]')]
            check('a TEB32 whose Self does not match: the registers and stack print, the TLS is not read',
                  any('no TEB32' in l for l in lines) and not any('TlsBitmapBits' in l for l in lines) and any('guest stack from ESP' in l for l in lines), r.stderr)
            r = run('nostack')
            lines = [l for l in r.stderr.splitlines() if l.startswith('[wow-null32]')]
            check('an unreadable stack says so and the TLS still prints', any('is not readable' in l for l in lines) and any('TlsBitmapBits' in l for l in lines), r.stderr)

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
        r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(build / 'signal_arm64_ios.c')], capture_output=True, text=True)
        check('signal_arm64_ios.c compiles for iOS (syntax only, pinned wine headers)', r.returncode == 0, r.stderr[-2000:])
        check('...and the new header adds no warning', 'ios_wow_null' not in r.stderr, r.stderr[-2000:])
    else:
        print('skip the iOS syntax check: no iphoneos SDK')
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

sys.exit(0 if ok else 1)
