#!/usr/bin/env python3
"""[x87-int64]: failure-path-only diagnostic for a 32-bit guest's 64-bit integer read through FILD/FISTP (ios_x87_int64_diag.h);
no Wine runs.

Logs: Batman: Arkham City GOTY (Steam 200260, the same iPad16,5: arkham-city-skip on build 576, PTKASZ on 574) and Injustice: Gods
Among Us (Steam 242700, G5HGVV and ND8YNB on build 574), all Unreal Engine 3. The first unhandled fault is the same 4-byte load
(`ldapr w4,[x24]` = `mov eax,[edi]`) at a 64 KB aligned address that is the first byte of a hole (Batman, G5HGVV) or of a freed
view (ND8YNB), right after the function that reads an 8-byte lock-free list head with FILD qword / FISTP qword. The JIT code of
that function reads `scvtf d2,x20 / frinti.2d v2,v2 / fcvtzs.2d v2,v2` in all four logs, which is the emulator's 64-bit x87
(BCD defaults X87ReducedPrecision on for 32-bit guests): the pair becomes int64 -> double -> int64 and a value of 2^61 and more
(depth/sequence in the high half) loses its low 9 bits. In the Batman log x13 = 0xf07ff00 is the exact tie that rounds to the
fault address 0xf080000; the log cannot say whether the node was ever at the fault address.

Source checks (every host): one header, included once after the rwx diagnostic's and before the ajan2 block; one call site
inside the UNHANDLED report right after ios_rwx_fault_diag and before [fault-full]; no write/protect/exit/stop call; no raw
dereference of a guest address (reads go through ios_av_args_read); gated on a data fault in JIT code; capped at 3 reports,
a repeated pc is not a report; the x86 register mapping is FEX's x32::SRA (checked against the FEX tree when it is present).

Compiled runs (macOS only: the helpers read real Mach memory): the production headers are compiled with a fake thread state over
a constructed 32-bit guest window at a 4 GB aligned base, with the real host code of the four logs as the code before the fault.
  - the Batman shape (EDX:EAX = 0x389f00050f080000, a head whose memory holds 0x389f00050f07ff00) prints the head as
    Next 0xf080000 / depth 5 / sequence 0x389f, the rounding step 0x200 and the window [0xf07ff00, 0xf080100], the candidate whose 8
    bytes round to EDX:EAX with both values, and the verdict LOST;
  - the same registers over a head that holds EDX:EAX exactly print EXACT; a head that is nowhere in reach print "not found";
  - the Injustice shape (a negative 64-bit value, step 0x400) and the other Batman log's code are recognised;
  - a fault that is not a 32-bit window access prints the first line only; code without the conversion, other exception kinds, a pc
    outside the pool and a repeated pc print nothing; the fourth distinct report is capped.
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
header_path = root / 'build/ntdll-unix/ios_x87_int64_diag.h'
header = header_path.read_text()
av_header = (root / 'build/ntdll-unix/ios_av_args_diag.h').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- source shape
inc = '#include "ios_x87_int64_diag.h"'
check('the header is included exactly once, after the rwx diagnostic and before the ajan2 block and the Mach exception thread',
      signal_src.count(inc) == 1 and signal_src.index('#include "ios_rwx_fault_diag.h"') < signal_src.index(inc)
      < signal_src.index('/* ajan2-tani (2026-10-21): FAILURE-PATH DIAGNOSTICS') < signal_src.index('static void *ios_mach_exception_thread( void *arg )'))
call = 'ios_x87_int64_diag( &state, req->exception, fault_addr );'
check('one call site', signal_src.count(call) == 1)
site = signal_src.index(call)
unhandled = signal_src.rindex('"[mach_exc] UNHANDLED #%d', 0, site)
check('the call is inside the UNHANDLED report, right after ios_rwx_fault_diag and before [fault-full]',
      unhandled < signal_src.index('ios_rwx_fault_diag( &state, req->exception );') < site
      < signal_src.index('madeira-bcd [fault-full]: every register', site))

forbidden = ('thread_set_state', 'thread_suspend', 'thread_terminate', 'vm_protect', 'mprotect(', 'mach_vm_write',
             'vm_write(', 'memset(', 'memcpy(', 'mmap(', 'free(', 'malloc(', 'abort(', 'exit(', 'sigaction', 'fopen(', 'open(',
             'mach_vm_read_overwrite')
check('no write, protect, stop, exit or file call, and no direct memory read (ios_av_args_read is the one reader)',
      not [f for f in forbidden if f in header], str([f for f in forbidden if f in header]))
raw_deref = re.findall(r'\*\s*\(\s*(?:const\s+)?(?:volatile\s+)?(?:u?int\d+_t|void|char|unsigned\w*|uintptr_t)\s*\*\s*\)', header)
check('no raw dereference of a guest address', not raw_deref, str(raw_deref))
check('gated on a data fault in JIT code, and on the conversion being in the code before it',
      'exception != EXC_BAD_ACCESS || !ios_av_args_pc_is_jit( pc )' in header and 'if (!ios_x87i_find_pair( pc, &scvtf_back, &fcvtzs_back )) return;' in header)
check('capped at 3 reports, a repeated host pc is not a report',
      '> 3) return;' in header and 'if (seen[k] == pc) return;' in header and 'static volatile uint64_t seen[3];' in header)
check('the 32-bit window gate: a 4 GB aligned base in x19, the address temporary x24 equal to the fault address',
      '(base & 0xffffffffULL) == 0 && base >= 0x100000000ULL' in header and 'st->__x[24] == fault_addr' in header)
mapping = ('eax = (uint32_t)st->__x[4]; ecx = (uint32_t)st->__x[7]; edx = (uint32_t)st->__x[5]; ebx = (uint32_t)st->__x[6];',
           'esp = (uint32_t)st->__x[8]; ebp = (uint32_t)st->__x[9]; esi = (uint32_t)st->__x[10]; edi = (uint32_t)st->__x[11];')
check('the x86 register mapping is FEX x32::SRA: EAX x4, ECX x7, EDX x5, EBX x6, ESP x8, EBP x9, ESI x10, EDI x11',
      all(m in header for m in mapping))
fex = root / 'FEX/FEXCore/Source/Interface/Core/ArchHelpers/Arm64Emitter.cpp'
if fex.exists():
    text = fex.read_text()
    x32 = text[text.index('namespace x32 {'):]
    sra = re.search(r'SRA = \{(.*?)\};', x32, re.S).group(1)
    regs = re.findall(r'Reg::(r\d+)', sra)[:8]
    check('FEX tree: x32::SRA starts r4, r7, r5, r6, r8, r9, r10, r11', regs == ['r4', 'r7', 'r5', 'r6', 'r8', 'r9', 'r10', 'r11'], str(regs))
else:
    print('skip the FEX register-mapping check: no FEX/FEXCore tree in this checkout')

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
'''

# The host code before the fault, copied from the logs ([fault-host] rows, 8 words each, the last row ends at the faulting word).
BATMAN = [  # arkham-city-skip L11307-11315 (build 576); PTKASZ has the same words at 16b780a20
    '51001108 b8284a69 aa0803e9 121d7108 51007108 51002108 8b284278 29001b0a',
    '51001108 b8284a6b 91004118 b8384a67 720008ff 54000301 91004118 b8784a67',
    '8b274278 f8bfc314 d503201f 9e620282 6ee19842 4ee1b842 91006118 fc384a62',
    '91006118 b8784a64 91007118 b8784a65 39506f94 3952cb95 52800036 11001e94',
    '12000a94 1ad422d4 0a3402b4 3912cb94 14000012 529cc0a4 72a94fe4 52985305',
    '72a367a5 aa0703ea aa0403e6 aa0503e7 8b2a4278 aa0403f4 aa0503f5 0874ff06',
    'd53b4200 6b04029f 7a4502a0 ba0e040b 9a841284 9a8512a5 aa0403eb aa0403ea',
    '91009118 b8384a65 aa0503e7 91005118 b8384a6b 7100009f 540004c0 8b2b4278',
    'b8bfc304 d503201f 5280000b 529ffff4 00000000 00000000 00000000 00000000',
]
INJUSTICE = [  # G5HGVV L43069-43077 (build 574, 0x1602a5b40..0x1602a5c4c); ND8YNB has the same words
    'd503201f 8b274278 f8bfc314 d503201f 9e620282 6ee19842 4ee1b842 d1006138',
    'fc384a62 51006134 8b344278 b8bfc304 d503201f 51005134 8b344278 b8bfc305',
    'd503201f 39506f94 3952cb95 52800036 11001e94 12000a94 1ad422d4 0a3402b4',
    '3912cb94 14000016 529cc0a4 72a94fe4 52985305 72a367a5 aa0703ea 51001108',
    'b8284a66 aa0403e6 aa0503e7 8b2a4278 aa0403f4 aa0503f5 0874ff06 d53b4200',
    '6b04029f 7a4502a0 ba0e040b 9a841284 9a8512a5 b8684a66 11001108 aa0403eb',
    '51004134 8b344278 d503201f 889fff0b 51003134 8b344278 d503201f 889fff05',
    'aa0503e7 51002134 8b344278 d503201f 889fff04 7100009f 540007e0 8b244278',
    'b8bfc304 d503201f 5280000a aa0a03e5 00000000 00000000 00000000 00000000',
]


def words_c(rows):
    return ', '.join('0x' + w for row in rows for w in row.split())


MAIN = r'''
/* arm64 macOS reserves 0x1000000000-0x7000000000 in every process (the M5 Max, build 672): the window moves to
   0x15000000000 there; on the Intel host it stays at 0x5000000000. */
#if defined(__aarch64__)
#define WIN 0x15000000000ULL
#else
#define WIN 0x5000000000ULL
#endif
/* the host code pool and its neighbour: 0x300000000 on Intel; on arm64 macOS the system libraries reach past it */
#if defined(__aarch64__)
#define HOSTAREA 0x20000000000ULL
#else
#define HOSTAREA 0x300000000ULL
#endif
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
static void w64(uint64_t a, uint64_t v) { memcpy((void *)a, &v, 8); }

static const uint32_t batman[] = { BATMAN_WORDS };
static const uint32_t injustice[] = { INJUSTICE_WORDS };

struct world { uint64_t pool; fake_arm_thread_state64_t st; };

/* a 32-bit guest: the host code ends 0x1000 into the pool; the faulting word is the first of the last row of 8 (the logs' [fault-host]
 * dump ends there); EAX = the node, EDX = depth/sequence, ESI = EDI = EAX as in the Batman block */
static struct world make(const uint32_t *code, size_t words, uint32_t eax, uint32_t edx, uint32_t esp)
{
    static uint64_t pool;
    struct world w;
    memset(&w, 0, sizeof(w));
    if (!pool) pool = alloc_at(HOSTAREA + 0x1000000, 0x10000);
    w.pool = pool;
    ios_jit_rx_base_global = (void *)pool; ios_jit_pool_size_global = 0x10000;
    memcpy((void *)(pool + 0x1000 - words * 4), code, words * 4);
    w.st.__pc = pool + 0x1000 - words * 4 + (words - 8) * 4;
    w.st.__x[4] = eax; w.st.__x[5] = edx; w.st.__x[7] = edx; w.st.__x[6] = 0;
    w.st.__x[8] = esp; w.st.__x[9] = esp + 0x28; w.st.__x[10] = eax; w.st.__x[11] = eax;
    w.st.__x[19] = WIN; w.st.__x[24] = WIN + eax;
    return w;
}
'''


def build_main(scn):
    return MAIN.replace('BATMAN_WORDS', words_c(BATMAN)).replace('INJUSTICE_WORDS', words_c(INJUSTICE)) + scn


SCENARIOS = r'''
static void guest_pages(uint32_t esp, uint32_t head_ptr, uint64_t head_value, int slot)
{
    alloc_at(WIN + (esp & ~0x3fffu) - 0x4000, 0x8000);              /* the stack around ESP */
    alloc_at(WIN + (head_ptr & ~0x3fffu), 0x4000);                  /* the page the head lives in */
    w64(WIN + head_ptr, head_value);
    if (slot >= 0) w32(WIN + esp + slot, head_ptr);                 /* the head pointer saved in the frame */
}

int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    uint32_t esp = 0x254f818, head = 0x16b92a8;
    struct world w;

    if (!strcmp(s, "roundtrip")) {
        /* the emulator's FILD/FISTP on the numbers of the logs */
        printf("a=%llx\n", (unsigned long long)ios_x87i_roundtrip(0x389f00050f07ff00ULL));        /* the Batman tie */
        printf("b=%llx\n", (unsigned long long)ios_x87i_roundtrip(0x389f00050f07fe00ULL));        /* representable: unchanged */
        printf("c=%llx\n", (unsigned long long)ios_x87i_roundtrip(0x389f00050f080100ULL));        /* the other end of the window */
        printf("d=%llx\n", (unsigned long long)ios_x87i_roundtrip(0x389f00050f080101ULL));        /* just outside: the next step up */
        printf("e=%llx\n", (unsigned long long)ios_x87i_roundtrip(0x001fffffffffffffULL));        /* 2^53 - 1: exact */
        printf("f=%llx\n", (unsigned long long)ios_x87i_roundtrip(0x0020000000000001ULL));        /* 2^53 + 1: rounds to even */
        printf("g=%llx\n", (unsigned long long)ios_x87i_roundtrip(0x7fffffffffffffffULL));        /* saturates */
        printf("h=%llx\n", (unsigned long long)ios_x87i_roundtrip(0x8000000000000000ULL));
        printf("i=%llx\n", (unsigned long long)ios_x87i_roundtrip(0x92d500048cd60001ULL));        /* a negative head */
        printf("s1=%llx s2=%llx s3=%llx s4=%llx s5=%llx s6=%llx\n", (unsigned long long)ios_x87i_step(0x389f00050f080000ULL),
               (unsigned long long)ios_x87i_step(0x149400010f8f0000ULL), (unsigned long long)ios_x87i_step(0x92d500048cd60000ULL),
               (unsigned long long)ios_x87i_step(0x5e6e001028100000ULL), (unsigned long long)ios_x87i_step(0x1fffffffffffffULL),
               (unsigned long long)ios_x87i_step(0x8000000000000000ULL));
        printf("s7=%llx\n", (unsigned long long)ios_x87i_step(0x20000000000000ULL));
        return 0;
    }
    if (!strcmp(s, "pair")) {
        unsigned a = 0, b = 0;
        uint64_t base = alloc_at(HOSTAREA + 0x2000000, 0x4000);
        memcpy((void *)(base + 0x100), batman, sizeof(batman));
        printf("batman=%d\n", ios_x87i_find_pair(base + 0x100 + sizeof(batman) - 0x20, &a, &b));
        printf("a=%x b=%x\n", a, b);
        memset((void *)(base + 0x1000), 0, 0x800);
        printf("none=%d\n", ios_x87i_find_pair(base + 0x1400, &a, &b));
        return 0;
    }

    if (!strcmp(s, "lost") || !strcmp(s, "lostreg") || !strcmp(s, "exact") || !strcmp(s, "notfound") || !strcmp(s, "dedupe") || !strcmp(s, "cap")) {
        uint64_t v = !strcmp(s, "lost") || !strcmp(s, "lostreg") || !strcmp(s, "dedupe") || !strcmp(s, "cap") ? 0x389f00050f07ff00ULL
                   : !strcmp(s, "exact") ? 0x389f00050f080000ULL : 0x1111111122222222ULL;
        guest_pages(esp, head, v, 0x10);
        alloc_at(WIN + 0xf070000, 0x10000);                         /* the live block below the hole; 0xf080000 stays unmapped */
        w = make(batman, sizeof(batman) / 4, 0xf080000, 0x389f0005, esp);
        if (!strcmp(s, "lostreg")) { w.st.__x[6] = head; }                  /* the head pointer in EBX, as in the log */
        ios_x87_int64_diag(&w.st, EXC_BAD_ACCESS, WIN + 0xf080000);
        if (!strcmp(s, "dedupe")) {
            ios_x87_int64_diag(&w.st, EXC_BAD_ACCESS, WIN + 0xf080000);          /* the same pc: nothing */
        }
        if (!strcmp(s, "cap")) {
            for (int i = 1; i < 8; i++) { w.st.__pc += 4; ios_x87_int64_diag(&w.st, EXC_BAD_ACCESS, WIN + 0xf080000); }
        }
        puts("done");
        return 0;
    }
    if (!strcmp(s, "injustice")) {
        /* G5HGVV: EAX = 0x8cd60000, EDX = 0x92d50004 (a negative 64-bit value); the head holds a Next within the step of 0x400 */
        esp = 0x3e3f844; head = 0x3485168;
        guest_pages(esp, head, 0x92d500048cd60010ULL, 0x8);
        w = make(injustice, sizeof(injustice) / 4, 0x8cd60000, 0x92d50004, esp);
        ios_x87_int64_diag(&w.st, EXC_BAD_ACCESS, WIN + 0x8cd60000ULL);
        puts("done");
        return 0;
    }
    if (!strcmp(s, "not32")) {
        guest_pages(esp, head, 0x389f00050f07ff00ULL, 0x10);
        w = make(batman, sizeof(batman) / 4, 0xf080000, 0x389f0005, esp);
        w.st.__x[19] = WIN + 0x1000;                                 /* not a window base */
        ios_x87_int64_diag(&w.st, EXC_BAD_ACCESS, WIN + 0xf080000);
        puts("done");
        return 0;
    }
    if (!strcmp(s, "benign")) {
        uint32_t zero_code[72];
        guest_pages(esp, head, 0x389f00050f07ff00ULL, 0x10);
        w = make(batman, sizeof(batman) / 4, 0xf080000, 0x389f0005, esp);
        ios_x87_int64_diag(&w.st, EXC_BAD_INSTRUCTION, WIN + 0xf080000);          /* another exception kind */
        ios_x87_int64_diag(&w.st, 3 /* EXC_ARITHMETIC */, WIN + 0xf080000);
        w.st.__pc = 0x1000;                                                       /* a host pc outside the pool */
        ios_x87_int64_diag(&w.st, EXC_BAD_ACCESS, WIN + 0xf080000);
        memset(zero_code, 0, sizeof(zero_code));                                  /* code without the conversion */
        w = make(zero_code, 72, 0xf080000, 0x389f0005, esp);
        ios_x87_int64_diag(&w.st, EXC_BAD_ACCESS, WIN + 0xf080000);
        puts("benign-done");
        return 0;
    }
    return 0;
}
'''

if sys.platform != 'darwin' or not shutil.which(cc):
    print('skip the compiled runs: macOS and a C compiler are needed (the helpers read real Mach memory)')
else:
    with tempfile.TemporaryDirectory(prefix='x87int64-') as d:
        d = Path(d)
        (d / 'h.c').write_text(PRELUDE + av_header + header + build_main(SCENARIOS))
        subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function',
                        str(d / 'h.c'), '-o', str(d / 'h')], check=True)

        def run(scenario):
            return subprocess.run([str(d / 'h'), scenario], capture_output=True, text=True)

        r = run('roundtrip')
        kv = dict(t.split('=') for t in r.stdout.split())
        check('the double round trip: the Batman tie 0xf07ff00 comes back as 0xf080000 (round half to even)', kv.get('a') == '389f00050f080000', r.stdout)
        check('a value that a double holds is unchanged; the window ends at +0x100, one past it is the next step',
              kv.get('b') == '389f00050f07fe00' and kv.get('c') == '389f00050f080000' and kv.get('d') == '389f00050f080200', r.stdout)
        check('2^53 - 1 is exact, 2^53 + 1 rounds to even', kv.get('e') == '1fffffffffffff' and kv.get('f') == '20000000000000', r.stdout)
        check('the conversion saturates like FCVTZS', kv.get('g') == '7fffffffffffffff' and kv.get('h') == '8000000000000000', r.stdout)
        check('a negative head rounds by magnitude', kv.get('i') == '92d500048cd60000', r.stdout)
        check('the rounding step: 0x200 for the Batman head, 0x100 for 0x1494..., 0x400 for the negative and 0x5e6e... heads, 1 up to 2^53 - 1, 2 at 2^53, 0x800 at 2^63',
              kv.get('s1') == '200' and kv.get('s2') == '100' and kv.get('s3') == '400' and kv.get('s4') == '400' and kv.get('s5') == '1'
              and kv.get('s6') == '800' and kv.get('s7') == '2', r.stdout)

        r = run('pair')
        kv = dict(t.split('=') for t in r.stdout.split())
        check('the conversion is found in the Batman code: SCVTF 0xb4 bytes and FCVTZS 0xac bytes before the faulting word',
              kv.get('batman') == '1' and kv.get('a') == 'b4' and kv.get('b') == 'ac', r.stdout)
        check('...and not in zeros', kv.get('none') == '0', r.stdout)

        r = run('lost')
        lines = [l for l in r.stderr.splitlines() if l.startswith('[x87-int64]')]
        check('Batman 576: a header, one candidate and a verdict', len(lines) == 3 and r.stdout.strip() == 'done', r.stderr)
        head = lines[0] if lines else ''
        check('the header: the guest address, the value, the SLIST decoding, the step and the window of true values',
              head.startswith('[x87-int64] rev=ajan6-arkham #1 guest read of 0xf080000 at host pc=0x')
              and 'EDX:EAX = 0x389f00050f080000' in head and 'Next=0xf080000 depth=5 sequence=0x389f' in head
              and 'doubles are 0x200 apart' in head and '[0xf07ff00, 0xf080100] would have come out as 0xf080000' in head, head)
        check('the header names where the conversion is', 'SCVTF at pc-0xb4, FCVTZS at pc-0xac' in head, head)
        cand = lines[1] if len(lines) > 1 else ''
        check('the candidate head is named by where it was found, with both values and the difference of Next',
              cand.startswith('[x87-int64]   [ESP+0x10] = 0x16b92a8: its 8 bytes are 0x389f00050f07ff00')
              and '0x389f00050f080000 = EDX:EAX: LOST, Next 0xf07ff00 -> 0xf080000; the real node 0xf07ff00 is readable (its first 8 bytes 0x0000000000000000)' in cand, cand)
        check('the verdict is LOST and names the keyed attempt', len(lines) == 3 and 'verdict: LOST' in lines[2] and 'env.FEX_X87REDUCEDPRECISION = 0' in lines[2], r.stderr)

        r = run('lostreg')
        lines = [l for l in r.stderr.splitlines() if l.startswith('[x87-int64]')]
        check('the head pointer in EBX is named EBX (registers are tried before the frame) and found once',
              len(lines) == 3 and lines[1].startswith('[x87-int64]   EBX = 0x16b92a8: its 8 bytes are 0x389f00050f07ff00'), r.stderr)
        r = run('exact')
        lines = [l for l in r.stderr.splitlines() if l.startswith('[x87-int64]')]
        check('a head that holds EDX:EAX exactly: EXACT, and the line says nothing was lost',
              len(lines) == 3 and 'its 8 bytes are 0x389f00050f080000 = EDX:EAX exactly; nothing was lost here' in lines[1]
              and 'verdict: EXACT' in lines[2] and 'LOST' not in r.stderr, r.stderr)
        r = run('notfound')
        lines = [l for l in r.stderr.splitlines() if l.startswith('[x87-int64]')]
        check('a head that neither equals nor rounds to EDX:EAX: no candidate, the not-found verdict',
              len(lines) == 2 and 'verdict: head not found' in lines[1], r.stderr)
        r = run('injustice')
        lines = [l for l in r.stderr.splitlines() if l.startswith('[x87-int64]')]
        check('Injustice G5HGVV: the negative value, step 0x400, and the window around 0x8cd60000',
              len(lines) == 3 and 'EDX:EAX = 0x92d500048cd60000' in lines[0] and 'depth=4 sequence=0x92d5' in lines[0]
              and 'doubles are 0x400 apart' in lines[0] and '[0x8cd5fe00, 0x8cd60200]' in lines[0]
              and 'LOST, Next 0x8cd60010 -> 0x8cd60000; the real node 0x8cd60010 is not readable either' in lines[1] and 'verdict: LOST' in lines[2], r.stderr)
        r = run('not32')
        lines = [l for l in r.stderr.splitlines() if l.startswith('[x87-int64]')]
        check('a fault that is not a 32-bit window access prints the first line only, without guest values',
              len(lines) == 1 and 'this is not a 32-bit guest access' in lines[0] and 'EDX:EAX' not in lines[0], r.stderr)
        r = run('dedupe')
        n = len([l for l in r.stderr.splitlines() if l.startswith('[x87-int64] rev=')])
        check('the same host pc twice is one report', n == 1, r.stderr)
        r = run('cap')
        n = len([l for l in r.stderr.splitlines() if l.startswith('[x87-int64] rev=')])
        check('distinct pcs: 3 reports, then silence', n == 3, r.stderr)
        r = run('benign')
        check('other exception kinds, a pc outside the pool and code without the conversion print nothing',
              r.stderr == '' and r.stdout.strip() == 'benign-done', r.stderr + r.stdout)

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
                 '-fno-strict-aliasing', '-Wno-implicit-function-declaration', '-Wno-int-conversion',
                 '-include', str(config), '-include', str(build / 'shims/wine_ios_exit.h'), f'-I{build}', f'-I{build / "shims"}',
                 f'-I{wine / "build-macos/dlls/ntdll"}', f'-I{wine / "dlls/ntdll"}', f'-I{wine / "dlls/ntdll/unix"}',
                 f'-I{wine / "build-macos/include"}', f'-I{wine / "include"}', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES',
                 '-DLTC_SOURCE', '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', '-DWINE_UNIX_LIB',
                 '-DWINE_IOS=1', '-Dget_thread_context=ntdll_get_thread_context', '-Dset_thread_context=ntdll_set_thread_context']
        r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(build / 'signal_arm64_ios.c')], capture_output=True, text=True)
        check('signal_arm64_ios.c compiles for iOS (syntax only, pinned wine headers)', r.returncode == 0, r.stderr[-2000:])
        check('...and the new header adds no warning', 'ios_x87' not in r.stderr, r.stderr[-2000:])
    else:
        print('skip the iOS syntax check: no iphoneos SDK')
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

sys.exit(0 if ok else 1)
