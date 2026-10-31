#!/usr/bin/env python3
"""[entry-fail] / [badrip] / [wow-initblock]: failure-path-only guest diagnostics; no Wine runs.

Logs: Age of Empires IV (34WSPP, 7DXVSK, 9Y8TCC, 22VWKA, EG43GA: "Invalid instruction in entry block:
127DE24F0", host `hlt #0`), Garry's Mod (T14SYR: "NoExec instruction in entry block: FFEDE3E8", host
`mov w1,#0; ldr x1,[x1]`), Vampire Survivors (MSQJWT) and art of rally (6TGCE6: the ARM64EC code-bitmap
check faults on the non-canonical guest RIPs 0xb800000071eafb37 / 0xa20000007037fe6d).

build/ntdll-unix/signal_arm64_ios.c gets three log-only helpers (ios_ajan2_*) called from the UNHANDLED
branch of the Mach exception thread; build/ntdll-unix/loader_ios.c logs when an i386 boot replaces a
different ntdll base in the session's LdrSystemDllInitBlock. Nothing here changes behaviour.

Source checks (every host): one call site, inside the UNHANDLED branch; no write/protect call in the helpers;
every guest read goes through mach_vm_read_overwrite; each diagnostic is gated on its own failure signature
(hlt #0, the exact two-instruction NoExec thunk, a non-canonical RIP, a pc inside the JIT pool) and is capped
(2 reports, a repeated RIP is not a report, at most 5 lines per report); the init-block line never writes.

Compiled runs (macOS only for the signal helpers, they read real Mach memory; any cc for the loader block):
the production helper source is compiled with a fake thread state and run on constructed memory. A hlt
thunk fault in a 64-bit guest names the guest RIP, the return address and their module + RVA and dumps the
bytes before the target and before the return address; a 32-bit guest is read through its window; the NoExec
thunk also compares the RIP with the session init block; a non-canonical RIP prints LR, the 8 host
instructions before it, x0-x3/x16/x17 and the producing load with its effective address (odd vtable word:
MISALIGNED, aligned words shown); a canonical RIP, any other instruction, a pc outside the pool and an
unreadable frame print nothing; the cap and the repeat rule hold. The A64 load decoder is checked against
the assembler's own encodings when clang can target arm64.

Round 579 (Vampire Survivors RJHZRT, build 576; the same registers as MSQJWT, build 573): the [badrip] report is widened on its own
failure path (ios_ajan2_badrip_wide, called at the end of the same report, so the same signature and the same cap of 2): 64 host
instructions before LR and every load among them that wrote a register equal to the RIP (immediate and register-offset forms), the
x86 RAX / RCX / RSP, the return address on the x86 stack with its module and the call bytes before it, the object at RCX (0x100
bytes), RAX's table, and where the RIP's 8 bytes sit in them (an unaligned read through an odd RAX). The older lines are untouched.
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
loader_src = (root / 'build/ntdll-unix/loader_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(text, signature):
    start = text.index(signature)
    end = text.index('\n}\n', start) + 3
    return text[start:end]


# ---------------------------------------------------------------- signal_arm64_ios.c: source shape
marker = '/* ajan2-tani (2026-10-21): FAILURE-PATH DIAGNOSTICS for guest control flow that dies in the JIT.'
check('the helper block exists exactly once', signal_src.count(marker) == 1)
begin = signal_src.index(marker)
end_ = signal_src.index('static void *ios_mach_exception_thread( void *arg )')
block = signal_src[begin:end_]
check('the helpers sit before the Mach exception thread', begin < end_)

call = 'ios_ajan2_fault_diag( &state, thread_teb, req->exception, fault_addr );'
# round 624 (agent N S9): ios_noexec_note (in this block) hands the same NoExec signature to the shared report too
noexec = function(block, 'static void ios_noexec_note(')
check('one call site, plus the NoExec note of round 624', signal_src.count(call) == 1 and signal_src.count('ios_ajan2_fault_diag(') == 3
      and noexec.count('ios_ajan2_fault_diag( st, teb, EXC_BAD_ACCESS, 0 );') == 1)
site = signal_src.index(call)
unhandled = signal_src.rindex('"[mach_exc] UNHANDLED #%d', 0, site)
check('the call is inside the UNHANDLED report, after [ec-fault-regs] and before [fault-full]',
      unhandled < signal_src.rindex('[ec-fault-regs]', 0, site) < site
      < signal_src.index('madeira-bcd [fault-full]: every register', site))
check('the UNHANDLED report keeps its own rate limit around the call',
      'if (cnt <= 5 || terminal_pc ||' in signal_src[unhandled - 3000:unhandled])

forbidden = ('thread_set_state', 'thread_suspend', 'thread_terminate', 'vm_protect', 'mprotect(', 'mach_vm_write',
             'vm_write(', 'memset(', 'memcpy(', 'mmap(', 'free(', 'malloc(', 'abort(', 'exit(', 'sigaction')
check('no helper writes memory, protects pages, stops threads or exits',
      not [f for f in forbidden if f in block], str([f for f in forbidden if f in block]))
# the only reads of guest or host memory are the Mach ones (no raw dereference of a computed address)
raw_deref = re.findall(r'\*\s*\(\s*(?:const\s+)?(?:volatile\s+)?(?:u?int\d+_t|void|char|unsigned\w*|uintptr_t)\s*\*\s*\)', block)
check('no raw dereference of a guest or host address (reads are mach_vm_read_overwrite only)',
      not raw_deref and block.count('mach_vm_read_overwrite(') == 2, str(raw_deref))
check('only the failure signatures are reported',
      'fault_addr == 0xd4400000UL' in block and 'prev == 0x52800001' in block and 'insn == 0xf9400021' in block
      and 'if (rip < 0x0000800000000000ULL) return;' in block and block.count('ios_ajan2_pc_is_jit( pc )') == 4
      and noexec.count('ios_ajan2_pc_is_jit( pc )') == 1)   # round 624: the NoExec note's own test

entry = function(block, 'static void ios_ajan2_entry_fail(')
badrip = function(block, 'static void ios_ajan2_badrip(')
check('entry-fail: capped at 2 reports, a repeated RIP is not a report',
      'if (reports >= 2 ||' in entry and '(n = __sync_add_and_fetch( &reports, 1 )) > 2) return;' in entry
      and 'if (rip == last_rip) return;' in entry)
check('badrip: capped at 2 reports, at most 2 candidate loads',
      'if (reports >= 2 ||' in badrip and '(n = __sync_add_and_fetch( &reports, 1 )) > 2) return;' in badrip
      and 'found < 2' in badrip)


wide = function(block, 'static void ios_ajan2_badrip_wide(')
check('badrip wide: called only from ios_ajan2_badrip (two paths: LR unreadable, and the end of the report), after its own lines',
      block.count('ios_ajan2_badrip_wide(') == 3 and badrip.count('ios_ajan2_badrip_wide( st, rip, lr );') == 2
      and badrip.index('candidate producer at LR-') < badrip.rindex('ios_ajan2_badrip_wide( st, rip, lr );'))
check('badrip wide: reads only through ios_ajan2_read / the hexdump helper, writes nothing, allocates nothing',
      'mach_vm_read_overwrite(' not in wide and not re.search(r'memset|memcpy|malloc|free\(|mmap|thread_set_state|thread_suspend|thread_terminate|vm_write', wide)
      and wide.count('ios_ajan2_read(') >= 5 and 'ios_ajan2_hexdump(' in wide)
check('badrip wide: at most 25 lines on any path (8 per scenario below)', len(re.findall(r'\\n"', wide)) <= 26, str(len(re.findall(r'\\n"', wide))))
check('badrip wide: the 64-instruction window, at most 4 producers, the 0x100-byte object window and the 0x400-byte scans',
      'ios_ajan2_read( lr - 256, w, sizeof(w) )' in wide and 'shown < 4' in wide and 'rcx - 0x40 + i * 32' in wide
      and 'unsigned char tbl[0x400];' in wide)


def lines_on_longest_path(src):
    """Newlines in dprintf format strings; alternatives (else / early return) counted pessimistically except
    for the ones the code makes exclusive, which the compiled run below measures exactly."""
    return len(re.findall(r'\\n"', src))


check('entry-fail and badrip each have at most 8 line-ending format strings in all (5 on any one path, run below)',
      lines_on_longest_path(entry) <= 8 and lines_on_longest_path(badrip) <= 8,
      f'{lines_on_longest_path(entry)} / {lines_on_longest_path(badrip)}')

# ---------------------------------------------------------------- loader_ios.c: the init-block line
lmark = '/* ajan2-tani (2026-10-21): LOG ONLY.'
check('loader: the init-block diagnostic exists exactly once', loader_src.count(lmark) == 1)
lbegin = loader_src.index(lmark)
lblock_start = loader_src.index('    {\n        static LONG overwrites;', lbegin)
lblock_end = loader_src.index('\n    }\n', lblock_start) + 7
lblock = loader_src[lblock_start:lblock_end]
first_write = loader_src.index('pLdrSystemDllInitBlock->ntdll_handle = TO_GUEST( (ULONG_PTR)module );', lbegin)
check('loader: it runs before the first write to the block and never writes it',
      lblock_end <= first_write and not re.search(r'pLdrSystemDllInitBlock->\w+\s*=[^=]', lblock)
      and 'memcpy' not in lblock)
check('loader: silent for a first boot (old base 0) and for the same base, capped at 4 lines',
      'old_handle && old_handle != TO_GUEST( (ULONG_PTR)module ) && InterlockedIncrement( &overwrites ) <= 4' in lblock)

# ---------------------------------------------------------------- compiled runs
cc = os.environ.get('CC', 'cc')


def compile_and_run_loader():
    harness = r'''
#include <stdint.h>
#include <stdio.h>
typedef long LONG;
typedef unsigned long ULONG_PTR;
typedef uint64_t ULONG64;
#define ERR(...) fprintf(stderr, __VA_ARGS__)
#define InterlockedIncrement(p) __sync_add_and_fetch((p), 1)
typedef struct { ULONG64 ntdll_handle, pKiUserExceptionDispatcher; } SYSTEM_DLL_INIT_BLOCK;
static SYSTEM_DLL_INIT_BLOCK blk, *pLdrSystemDllInitBlock = &blk;
static void boot(ULONG_PTR module, ULONG_PTR wow_base)
{
#define TO_GUEST(v) ((v) ? (ULONG_PTR)(v) - wow_base : 0)
''' + lblock + r'''
    pLdrSystemDllInitBlock->ntdll_handle = TO_GUEST( module );
    pLdrSystemDllInitBlock->pKiUserExceptionDispatcher = TO_GUEST( module ) + 0x4e3e8;
}
int main(void)
{
    ULONG_PTR base = 0x7900000000UL;
    boot(base + 0xfff40000UL, base);                 /* first i386 boot: silent */
    boot(base + 0xfff40000UL, base);                 /* same base: silent */
    fputs("--silent-done\n", stderr);
    for (int i = 0; i < 6; i++) boot(base + (i % 2 ? 0xfff40000UL : 0xffe90000UL), base);   /* alternating: replaced each time */
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='initblock-') as d:
        d = Path(d)
        (d / 't.c').write_text(harness)
        subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', str(d / 't.c'), '-o', str(d / 't')],
                       check=True)
        r = subprocess.run([str(d / 't')], capture_output=True, text=True)
    pre, _, post = r.stderr.partition('--silent-done\n')
    check('loader: a first boot and a repeated base print nothing', pre == '', pre)
    lines = [l for l in post.splitlines() if l.startswith('[wow-initblock]')]
    check('loader: a different base prints, capped at 4 lines', len(lines) == 4, post)
    check('loader: the line names the old and the new base',
          'ntdll_handle=0xfff40000' in lines[0] and 'replaces it with ntdll_handle=0xffe90000' in lines[0], lines[0] if lines else '')


compile_and_run_loader()

HARNESS_PRELUDE = r'''
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
#undef __darwin_arm_thread_state64_get_sp
#define __darwin_arm_thread_state64_get_pc(ts) ((ts).__pc)
#define __darwin_arm_thread_state64_get_sp(ts) ((ts).__sp)
/* round 624 (agent N S9): ios_noexec_note sits in this block and reads TEB.ClientId; the real layout keeps ClientId at 0x40, Peb at 0x60 */
typedef struct { char pad0[0x40]; struct { void *UniqueProcess, *UniqueThread; } ClientId; char pad1[0x60 - 0x50]; void *Peb; } TEB;
typedef unsigned long ULONG_PTR;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
typedef struct { uint64_t pLdrInitializeThunk, pKiUserExceptionDispatcher, pKiUserApcDispatcher,
                 pKiUserCallbackDispatcher, pRtlUserThreadStart, ntdll_handle; } SYSTEM_DLL_INIT_BLOCK;
SYSTEM_DLL_INIT_BLOCK *pLdrSystemDllInitBlock;
void *ios_jit_rx_base_global;
size_t ios_jit_pool_size_global;
static uint64_t fake_mod_base, fake_mod_size, fake_window;
int ios_jit_low_contains( uintptr_t a ) { (void)a; return 0; }
unsigned long long ios_jit_module_base_for_va( unsigned long long va, unsigned long long *size_out )
{
    if (va >= fake_mod_base && va < fake_mod_base + fake_mod_size) { if (size_out) *size_out = fake_mod_size; return fake_mod_base; }
    return 0;
}
static const char *ios_pe_module_name( uint64_t base ) { return base == fake_mod_base ? "Fake.exe" : "?"; }
static uint64_t ios_wow_base_for_peb( void *peb ) { return peb ? fake_window : 0; }
'''

HARNESS_MAIN = r'''
static uint64_t alloc(size_t n)
{
    mach_vm_address_t a = 0;
    assert(mach_vm_allocate(mach_task_self(), &a, n, VM_FLAGS_ANYWHERE) == KERN_SUCCESS);
    memset((void *)a, 0, n);
    return a;
}
static void w64(uint64_t a, uint64_t v) { memcpy((void *)a, &v, 8); }
static void w32(uint64_t a, uint32_t v) { memcpy((void *)a, &v, 4); }

struct world { uint64_t pool, frame, stack, mod; fake_arm_thread_state64_t st; };
static struct world make(uint64_t rip, uint64_t rsp, uint64_t pc_off, uint64_t lr_off)
{
    struct world w;
    w.pool = alloc(0x10000); w.frame = alloc(0x1000); w.stack = alloc(0x1000); w.mod = alloc(0x10000);
    ios_jit_rx_base_global = (void *)w.pool; ios_jit_pool_size_global = 0x10000;
    fake_mod_base = w.mod; fake_mod_size = 0x10000;
    memset(&w.st, 0, sizeof(w.st));
    w.st.__x[28] = w.frame; w.st.__pc = w.pool + pc_off; w.st.__lr = w.pool + lr_off;
    w64(w.frame + 0x18, rip); w64(w.frame + 0x40, rsp);
    return w;
}

int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    if (!strcmp(s, "hlt") || !strcmp(s, "cap") || !strcmp(s, "dedupe")) {
        struct world w = make(0, 0, 0x4e8, 0x274);
        uint64_t rip = w.mod + 0x4f0, ret = w.mod + 0x192, rsp = w.stack + 0x100;
        static const unsigned char junk[] = { 0xd5,0xdc,0xca,0xa3,0xbc,0x48,0x89,0x7c,0x24,0x10,0x55,0x48,0x8b,0xec,0x48,0x83 };
        static const unsigned char callsite[] = { 0x48,0x8b,0x05,0x11,0x22,0x33,0x44,0xff,0xd0 };
        memcpy((void *)rip, junk, sizeof(junk));
        memset((void *)(rip - 32), 0xcc, 32);
        memcpy((void *)(ret - sizeof(callsite)), callsite, sizeof(callsite));
        w64(rsp, ret);
        w64(w.frame + 0x18, rip); w64(w.frame + 0x40, rsp);
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_INSTRUCTION, 0xd4400000UL);
        if (!strcmp(s, "dedupe")) {
            ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_INSTRUCTION, 0xd4400000UL);          /* same RIP: nothing */
            w64(w.frame + 0x18, rip + 0x10);
            ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_INSTRUCTION, 0xd4400000UL);          /* new RIP: report #2 */
            w64(w.frame + 0x18, rip + 0x20);
            ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_INSTRUCTION, 0xd4400000UL);          /* cap: nothing */
        }
        if (!strcmp(s, "cap")) {
            for (int i = 1; i < 50; i++) { w64(w.frame + 0x18, rip + 0x10 * i); ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_INSTRUCTION, 0xd4400000UL); }
        }
        fprintf(stdout, "mod=%llx rip=%llx ret=%llx rsp=%llx\n", (unsigned long long)w.mod, (unsigned long long)rip,
                (unsigned long long)ret, (unsigned long long)rsp);
    } else if (!strcmp(s, "benign")) {
        struct world w = make(0x71f5687050ULL, 0, 0x4e8, 0x274);
        w64(w.frame + 0x40, w.stack + 0x100);
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_INSTRUCTION, 0xe7ffdefeUL);              /* another undefined insn */
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_ACCESS, 0x10);                           /* canonical RIP, ordinary fault */
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_ACCESS, 0);                              /* NULL read, but not the NoExec thunk */
        w32(w.pool + 0x4e4, 0x52800001); w32(w.pool + 0x4e8, 0xf9400020);               /* mov w1,#0; ldr x1,[x1,..]: not the thunk */
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_ACCESS, 0);
        w64(w.frame + 0x18, w.mod + 0x4f0);
        w.st.__pc = 0x1000;                                                             /* a host pc outside the pool */
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_INSTRUCTION, 0xd4400000UL);
        w.st.__pc = w.pool + 0x4e8; w.st.__x[28] = 0;                                   /* no frame */
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_INSTRUCTION, 0xd4400000UL);
        w.st.__x[28] = 0x20000;                                                         /* unreadable frame */
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_INSTRUCTION, 0xd4400000UL);
        ios_ajan2_fault_diag(&w.st, 0, 3 /* EXC_ARITHMETIC */, 0);
        fputs("benign-done\n", stdout);
    } else if (!strcmp(s, "noexec32")) {
        struct world w = make(0x00401000UL, 0x00300ff0UL, 0x354, 0x1d8);
        uint64_t win = alloc(0x500000);
        static SYSTEM_DLL_INIT_BLOCK blk;
        static TEB teb;
        fake_window = win;
        teb.Peb = (void *)0x1234;
        blk.pKiUserExceptionDispatcher = 0x00401000UL; blk.ntdll_handle = 0xffe90000UL; blk.pLdrInitializeThunk = 0xffedeed0UL;
        pLdrSystemDllInitBlock = &blk;
        w32(w.pool + 0x350, 0x52800001); w32(w.pool + 0x354, 0xf9400021);               /* the NoExec thunk */
        memcpy((void *)(win + 0x401000), "\x8b\xff\x55\x8b\xec\x83\xec\x08\x53\x56\x57\x33\xdb\x89\x5d\xfc", 16);
        w32(win + 0x300ff0, 0x00402ab5);                                                /* [RSP] = return address */
        memcpy((void *)(win + 0x402ab5 - 7), "\xff\x15\x10\x20\x40\x00\x59", 7);
        w64(w.frame + 0x18, 0x00401000UL); w64(w.frame + 0x40, 0x00300ff0UL);
        ios_ajan2_fault_diag(&w.st, (uintptr_t)&teb, EXC_BAD_ACCESS, 0);
        fprintf(stdout, "win=%llx\n", (unsigned long long)win);
    } else if (!strcmp(s, "badrip") || !strcmp(s, "badrip-noload") || !strcmp(s, "badrip-lr")) {
        uint64_t rip = 0xb800000071eafb37ULL;
        struct world w = make(rip, 0, 0x15c, 0x138);
        uint64_t vt = alloc(0x1000);
        uint32_t words[8] = { 0xaa0003e0, 0xd503201f, 0xd503201f, 0xd503201f, 0xf9401d06 /* ldr x6,[x8,#0x38] */,
                              0xaa0603ec /* mov x12,x6 */, 0xf9000f86, 0xd63f0160 /* blr x11 */ };
        /* slot_k = (rip & 0x00ffffffffffffff) << 8 | 0xaa, slot_k+1 low byte = rip >> 56: an 8-byte load at vt + 0x39 gives rip */
        w64(vt + 0x38, ((rip & 0x00ffffffffffffffULL) << 8) | 0xaa);
        w64(vt + 0x40, 0x00000071eafb38b8ULL);
        if (!strcmp(s, "badrip-noload")) words[4] = 0xd503201f;
        for (int i = 0; i < 8; i++) w32(w.pool + 0x138 - 32 + 4 * i, words[i]);
        if (!strcmp(s, "badrip-lr")) w.st.__lr = 0x30;
        w.st.__x[0] = 0x1111; w.st.__x[1] = 0x2222; w.st.__x[2] = 0x3333; w.st.__x[3] = 0x4444;
        w.st.__x[16] = 0x16dff8108ULL; w.st.__x[17] = 0x724db40000ULL;
        w.st.__x[6] = rip; w.st.__x[12] = rip; w.st.__x[8] = vt + 1;
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_ACCESS, 0x17071fd61e3d0ULL);
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_ACCESS, 0x17071fd61e3d0ULL);              /* the second fault is the next report */
        fprintf(stdout, "vt=%llx\n", (unsigned long long)vt);
    } else if (!strcmp(s, "badrip-wide")) {
        uint64_t rip = 0xb800000071eafb37ULL;
        struct world w = make(rip, 0, 0x15c, 0x138);
        uint64_t vt = alloc(0x1000), obj = alloc(0x1000), table = alloc(0x2000), tbl2 = alloc(0x1000), rsp = w.stack + 0x100;
        uint64_t ret = w.mod + 0x2c3c;
        static const unsigned char call_bytes[] = { 0x48, 0x8b, 0x01, 0xff, 0x50, 0x38 };            /* mov rax,[rcx]; call [rax+0x38] */
        uint32_t words[8] = { 0xf8686aa6 /* ldr x6,[x21,x8] */, 0xd503201f, 0xd503201f, 0xd503201f, 0xf9401d06 /* ldr x6,[x8,#0x38] */,
                              0xaa0603ec, 0xf9000f86, 0xd63f0160 };
        (void)table; (void)tbl2;
        w64(vt + 0x38, ((rip & 0x00ffffffffffffffULL) << 8) | 0xaa);
        w64(vt + 0x40, 0x00000071eafb38b8ULL);
        for (int i = 0; i < 8; i++) w32(w.pool + 0x138 - 32 + 4 * i, words[i]);
        /* an older load (register offset, uxtw #3) of the RIP, far before the window of 8 */
        w32(w.pool + 0x138 - 200, 0xf8685aa6u);               /* ldr x6,[x21,w8,uxtw #3] */
        for (int i = 0; i < 4; i++) w64(obj + 0x40 + i * 8, 0x1111000000000000ULL + i);
        w64(obj + 0x40, vt + 1);                              /* the odd klass word */
        w64(obj + 0x50, 0x00000072490a4c90ULL);
        memcpy((void *)(w.mod + 0x2c3c - sizeof(call_bytes)), call_bytes, sizeof(call_bytes));
        w64(rsp, ret);
        w.st.__x[0] = obj + 0x40; w.st.__x[8] = vt + 1; w.st.__x[23] = rsp; w.st.__x[21] = 0;
        w.st.__x[6] = rip; w.st.__x[12] = rip;
        ios_ajan2_fault_diag(&w.st, 0, EXC_BAD_ACCESS, 0x17071fd61e3d0ULL);
        fprintf(stdout, "vt=%llx obj=%llx mod=%llx ret=%llx\n", (unsigned long long)vt, (unsigned long long)(obj + 0x40),
                (unsigned long long)w.mod, (unsigned long long)ret);
    } else if (!strcmp(s, "decode")) {
        uint32_t insn; unsigned rt, rt2, rn; int64_t imm;
        while (scanf("%x", &insn) == 1) {
            const char *n = ios_ajan2_load_decode(insn, &rt, &rt2, &rn, &imm);
            if (n) printf("%08x %s %u %u %u %lld\n", insn, n, rt, rt2, rn, (long long)imm);
            else printf("%08x none\n", insn);
        }
    }
    return 0;
}
'''


def build_signal_harness(d):
    src = HARNESS_PRELUDE + block + HARNESS_MAIN
    (d / 'h.c').write_text(src)
    exe = d / 'h'
    subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function',
                    '-Wno-unused-variable', str(d / 'h.c'), '-o', str(exe)], check=True)
    return exe


def run(exe, scenario, stdin=None):
    r = subprocess.run([str(exe), scenario], capture_output=True, text=True, input=stdin)
    return r


def kv(text):
    out = {}
    for token in text.split():
        if '=' in token:
            k, v = token.split('=', 1)
            out[k] = v
    return out


if sys.platform != 'darwin':
    print('skip the signal-helper runs: they read real Mach memory (macOS only)')
    sys.exit(0 if ok else 1)

with tempfile.TemporaryDirectory(prefix='guest-fail-diag-') as directory:
    d = Path(directory)
    exe = build_signal_harness(d)

    # --- hlt thunk, 64-bit guest (Age of Empires IV shape)
    r = run(exe, 'hlt')
    info = kv(r.stdout)
    err = r.stderr.splitlines()
    mod, rip, ret, rsp = (info.get(k, '') for k in ('mod', 'rip', 'ret', 'rsp'))
    check('hlt: exactly 3 lines, all [entry-fail]', len(err) == 3 and all(l.startswith('[entry-fail]') for l in err), r.stderr)
    check('hlt: the header says invalid instruction and names the RIP with module + RVA',
          err and 'invalid instruction (hlt thunk)' in err[0] and f'guest RIP=0x{rip} Fake.exe+0x4f0 (base 0x{mod})' in err[0]
          and f'RSP=0x{rsp}' in err[0], err[0] if err else '')
    check('hlt: 32 bytes before and 16 at the target',
          len(err) > 1 and ('cc ' * 32) + '| +0..+15: d5 dc ca a3 bc 48 89 7c 24 10 55 48 8b ec 48 83 ' in err[1], err[1] if len(err) > 1 else '')
    check('hlt: [RSP] with module + RVA and the call instruction before it',
          len(err) > 2 and f'[RSP]=0x{ret} Fake.exe+0x192 (base 0x{mod})' in err[2]
          and '48 8b 05 11 22 33 44 ff d0 ' in err[2], err[2] if len(err) > 2 else '')

    r = run(exe, 'dedupe')
    n = r.stderr.count('[entry-fail] rev=ajan2-tani #')
    check('dedupe: the same RIP twice is one report, a new RIP the second, the third nothing', n == 2, r.stderr)
    r = run(exe, 'cap')
    check('cap: 50 different RIPs print 2 reports (6 lines at most)',
          r.stderr.count('[entry-fail] rev=ajan2-tani #') == 2 and len(r.stderr.splitlines()) <= 6, r.stderr)

    r = run(exe, 'benign')
    check('benign: a canonical RIP, other instructions, a pc outside the pool and an unreadable frame print nothing',
          r.stderr == '' and 'benign-done' in r.stdout, r.stderr)

    # --- NoExec thunk, 32-bit guest through its window (Garry's Mod shape)
    r = run(exe, 'noexec32')
    info = kv(r.stdout)
    err = r.stderr.splitlines()
    win = info.get('win', '')
    check('noexec32: header says NoExec and names the 32-bit window',
          err and 'NoExec block (null-read thunk)' in err[0] and 'guest RIP=0x401000 (32-bit guest, window base 0x' + win + ')' in err[0],
          err[0] if err else '')
    check('noexec32: guest bytes are read through the window',
          len(err) > 1 and '| +0..+15: 8b ff 55 8b ec 83 ec 08 53 56 57 33 db 89 5d fc ' in err[1], err[1] if len(err) > 1 else '')
    check('noexec32: the return address and the call bytes (ff 15 ...) are read through the window',
          len(err) > 2 and '[RSP]=0x402ab5' in err[2] and 'ff 15 10 20 40 00 59 ' in err[2], err[2] if len(err) > 2 else '')
    check('noexec32: the RIP is compared with the session init block',
          len(err) > 3 and 'ntdll_handle=0xffe90000' in err[3] and 'RIP equals pKiUserExceptionDispatcher' in err[3],
          err[3] if len(err) > 3 else '')
    check('noexec32: 4 lines', len(err) == 4, r.stderr)

    # --- non-canonical RIP (Vampire Survivors / art of rally shape)
    r = run(exe, 'badrip')
    info = kv(r.stdout)
    err = r.stderr.splitlines()
    check('badrip: 2 reports (the second fault); each report is the 4 older lines, then the wider look (round 579, at most 30 lines)',
          r.stderr.count('[badrip] rev=ajan2-tani #') == 2 and len(err) <= 60, r.stderr)
    check('badrip: header names the non-canonical RIP, x0-x3, x16, x17',
          'guest RIP=0xb800000071eafb37 is not a canonical user address' in err[0]
          and 'x0=0x1111 x1=0x2222 x2=0x3333 x3=0x4444 x16=0x16dff8108 x17=0x724db40000' in err[1], '\n'.join(err[:2]))
    check('badrip: the 8 host instructions before LR (LR-32..LR-4)',
          err[2].startswith('[badrip]   host insns LR-32..LR-4: aa0003e0 d503201f d503201f d503201f f9401d06 aa0603ec f9000f86 d63f0160'),
          err[2])
    cand = err[3] if len(err) > 3 else ''
    vt = int(info.get('vt', '0'), 16)
    check('badrip: the producing load is decoded with its misaligned effective address and the aligned words',
          'candidate producer at LR-16: f9401d06 = ldr x6,[x8,#56]' in cand
          and f'= 0x{vt + 0x39:x} (mod 8 = 1) MISALIGNED' in cand
          and '; aligned words there: 00000071eafb37aa' in cand and '00000071eafb38b8' in cand, cand)
    check('badrip: the word that is not a load of the RIP is not reported (mov x12,x6 / str)', cand.count('candidate producer') == 1, cand)

    r = run(exe, 'badrip-noload')
    check('badrip: without a load of the RIP in those 8 insns it says so',
          'no load among those 8 insns wrote a register that equals the RIP' in r.stderr, r.stderr)
    r = run(exe, 'badrip-lr')
    check('badrip: an unreadable LR is reported, not dereferenced', 'LR 0x30 not readable' in r.stderr, r.stderr)

    r = run(exe, 'badrip-wide')
    info = kv(r.stdout)
    err = r.stderr.splitlines()
    vt, obj, mod, ret = (int(info.get(k, '0'), 16) for k in ('vt', 'obj', 'mod', 'ret'))
    text = r.stderr
    check('badrip wide: the older lines come first and unchanged (header, x0-x3, 8 insns, the candidate producer)',
          err[2].startswith('[badrip]   host insns LR-32..LR-4: ') and err[3].startswith('[badrip]   candidate producer at LR-16: f9401d06 = ldr x6,[x8,#56]'), '\n'.join(err[:5]))
    check('badrip wide: 64 host instructions in four rows, LR-256 to LR-4',
          text.count('[badrip]   host insns LR-') == 5 and '[badrip]   host insns LR-256..LR-196: ' in text and '[badrip]   host insns LR-64..LR-4: ' in text, text[:1200])
    check('badrip wide: the register-offset load far before the window is a producer too, newest first',
          'producer at LR-16: f9401d06 = ldr x6,[x8,#0x' in text.replace('#56', '#0x') or 'producer at LR-16: f9401d06 = ldr x6,[x8,#56]' in text, text)
    check('badrip wide: ... and so is the uxtw #3 register-offset form (LR-200)',
          'producer at LR-200: f8685aa6 = ldr x6,[x21,x8,uxtw #3]' in text, text)
    check('badrip wide: the x86 registers, and which host registers hold the RIP',
          f'x86 RAX=0x{vt + 1:x} RCX=0x{obj:x} RSP=0x' in text and 'host x6=yes x12=yes x7=no' in text, text)
    check('badrip wide: the return address on the x86 stack with module + RVA, and the call bytes before it',
          f'x86 return address [RSP]=0x{ret:x} Fake.exe+0x2c3c (base 0x{mod:x})' in text
          and '48 8b 01 ff 50 38 ' in text and 'ret-32..ret+7' in text, text)
    check('badrip wide: the object at RCX (0x100 bytes from RCX-0x40, 8 rows) with the odd klass word',
          text.count('[badrip]   RCX') == 8 and f'[badrip]   RCX+0: {vt + 1:016x} ' in text, text)
    check('badrip wide: RAX\'s table from RAX&~7 - 0x10 (3 rows)', text.count('[badrip]   RAX&~7 ') == 3, text)
    check('badrip wide: the RIP\'s 8 bytes are found at RAX+0x38 (misaligned): the vtable slot of the call',
          'the RIP\'s 8 bytes sit at RAX+0x38 (misaligned): a call through [RAX+0x38] reads it' in text, text)
    check('badrip wide: about 25 lines per report, 2 reports', 20 <= len([l for l in err if l.startswith('[badrip]')]) <= 60,
          str(len(err)))

    # --- the A64 load decoder against the assembler's encodings
    asm = d / 'loads.s'
    asm.write_text('.text\nldr x6,[x8,#0x38]\nldur x6,[x8,#-1]\nldp x6,x7,[x8,#16]\nldr x6,[sp,#8]\nldp x6,x7,[x8,#-16]\n'
                   'ldr x30,[x28,#24]\nldur x5,[x9,#255]\nldur x5,[x9,#-256]\nldr w6,[x8,#4]\nstr x6,[x8,#8]\nldrb w8,[x1,#1]\n')
    try:
        subprocess.run(['xcrun', 'clang', '-arch', 'arm64', '-c', str(asm), '-o', str(d / 'loads.o')], check=True, capture_output=True)
        dump = subprocess.run(['xcrun', 'otool', '-arch', 'arm64', '-t', str(d / 'loads.o')], check=True, capture_output=True, text=True).stdout
        words = [w for line in dump.splitlines()[2:] for w in line.split()[1:]]
    except (OSError, subprocess.CalledProcessError):
        words = []
        print('skip the decoder run against the assembler: clang cannot target arm64 here')
    if words:
        r = run(exe, 'decode', ' '.join(words))
        got = [l.split() for l in r.stdout.splitlines()]
        expect = [('ldr', 6, 32, 8, 56), ('ldur', 6, 32, 8, -1), ('ldp', 6, 7, 8, 16), ('ldr', 6, 32, 31, 8),
                  ('ldp', 6, 7, 8, -16), ('ldr', 30, 32, 28, 24), ('ldur', 5, 32, 9, 255), ('ldur', 5, 32, 9, -256),
                  None, None, None]
        okd = len(got) == len(expect)
        for g, e in zip(got, expect):
            if e is None:
                okd = okd and g[1] == 'none'
            else:
                okd = okd and (g[1], int(g[2]), int(g[3]), int(g[4]), int(g[5])) == e
        check('the A64 load decoder agrees with the assembler (ldr/ldur/ldp; ldr w, str, ldrb are not decoded)', okd, r.stdout)

# ---------------------------------------------------------------- the real files still compile for iOS (when the wine headers exist)
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
                 '-fno-strict-aliasing', '-Wno-implicit-function-declaration', '-Wno-int-conversion',
                 '-include', str(config), '-include', str(build / 'shims/wine_ios_exit.h'), f'-I{build / "shims"}',
                 f'-I{wine / "build-macos/dlls/ntdll"}', f'-I{wine / "dlls/ntdll"}', f'-I{wine / "dlls/ntdll/unix"}',
                 f'-I{wine / "build-macos/include"}', f'-I{wine / "include"}', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES',
                 '-DLTC_SOURCE', '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', '-DWINE_UNIX_LIB',
                 '-DWINE_IOS=1', '-Dget_thread_context=ntdll_get_thread_context', '-Dset_thread_context=ntdll_set_thread_context']
        for name in ('signal_arm64_ios.c', 'loader_ios.c'):
            r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(build / name)], capture_output=True, text=True)
            check(f'{name} compiles for iOS (syntax only, pinned wine headers)', r.returncode == 0, r.stderr[-2000:])
    else:
        print('skip the iOS syntax check: no iphoneos SDK')
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

sys.exit(0 if ok else 1)
