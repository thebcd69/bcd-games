#!/usr/bin/env python3
"""[rwx-fault]: failure-path-only diagnostic for a guest fault in a page that may have been rewritten under the
emulator (ios_rwx_fault_diag.h); no Wine runs.

Logs: Monster Hunter Stories 3 Trial (Steam 3470630, the owner's build 576, MHS3-prev) and Monster Hunter Rise
(MKPN2N, build 574). Both programs die in the first instructions of their protector, an unnamed RWX section whose
entry stub decrypts its own code with a byte-wise xor loop; in Rise the block after the loop was compiled from the
bytes before it ran, and the log holds no SMC event. Whether the pool copy (env.MADEIRA_X64_IMAGE_NOCOPY = 1) or the
16 KB host page (Wine ORs the protection of the four 4 KB pages) is the cause cannot be told from the log; the new
line prints the host page's real protection, Wine's byte for each 4 KB page of it, and the loaded image's bytes next
to the pool copy's.

Source checks (every host): one header, included once after ios_av_args_diag.h and before the Mach exception thread;
one call, inside the UNHANDLED report right after [av-args] and before [fault-full]; no write, protect, stop, exit or
file call; guest memory is read through mach_vm_read_overwrite only; gated on a bad access in JIT code with the exact
guest RIP in a page Wine holds executable; capped at 3 reports, a second report for the same 4 KB page is not one.

Compiled runs (macOS only: the helper reads real Mach memory): the production header is compiled with a fake thread
state, a fake CPUState and fake lookups over constructed memory. A host page that is writable while Wine's table says
the page is read-only reports "trap: OFF" (and names a neighbouring page with W); a read-only host page reports
"armed"; an image and a pool copy that differ say DIFFER, equal ones say equal; an address no pool-copied image holds
says so and has no pool copy; the section text of an RWX section is printed. A non-access exception, a host pc outside
the pool, a guest page Wine does not hold executable, an unreadable CPUState and a RIP below 0x10000 print nothing; the
same page twice is one report and the 4th distinct page is capped.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
signal_src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
av_header = (root / 'build/ntdll-unix/ios_av_args_diag.h').read_text()
header_path = root / 'build/ntdll-unix/ios_rwx_fault_diag.h'
header = header_path.read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- source shape
inc = '#include "ios_rwx_fault_diag.h"'
check('the header is included exactly once, after ios_av_args_diag.h and before the Mach exception thread',
      signal_src.count(inc) == 1
      and signal_src.index('#include "ios_av_args_diag.h"') < signal_src.index(inc)
      < signal_src.index('static void *ios_mach_exception_thread( void *arg )'))
call = 'ios_rwx_fault_diag( &state, req->exception );'
check('one call site', signal_src.count(call) == 1 and signal_src.count('ios_rwx_fault_diag(') == 1)
site = signal_src.index(call)
unhandled = signal_src.rindex('"[mach_exc] UNHANDLED #%d', 0, site)
check('the call is inside the UNHANDLED report, right after [av-args] and before [fault-full]',
      unhandled < signal_src.index('ios_av_args_diag( &state, req->exception, fault_addr );') < site
      < signal_src.index('madeira-bcd [fault-full]: every register', site))
check('the UNHANDLED report keeps its own rate limit around the call',
      'if (cnt <= 5 || terminal_pc ||' in signal_src[unhandled - 3000:unhandled])
forbidden = ('thread_set_state', 'thread_suspend', 'thread_terminate', 'vm_protect', 'mprotect(', 'mach_vm_write',
             'vm_write(', 'memset(', 'memcpy(', 'mmap(', 'free(', 'malloc(', 'abort(', 'exit(', 'sigaction', 'fopen(', 'open(')
check('no write, protect, stop, exit or file call', not [f for f in forbidden if f in header],
      str([f for f in forbidden if f in header]))
check('guest memory is read through ios_av_args_read (mach_vm_read_overwrite) only: no raw dereference of a guest address',
      'mach_vm_read_overwrite(' not in header and header.count('ios_av_args_read(') >= 4
      and 'ios_jit_translate_addr' in header)
check('gated on a bad access in JIT code and on a page Wine holds executable',
      'exception != EXC_BAD_ACCESS || !ios_av_args_pc_is_jit( pc )' in header and 'if (!(this_vp & 0x04)) return;' in header)
check('capped at 3 reports, a second report for the same 4 KB page is not one',
      '> 3) return;' in header and 'seen[i] == (rip & ~0xfffULL)' in header)

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
static uint64_t fake_mod_base, fake_mod_size, fake_pool, fake_pool_size;
static uint64_t fake_img, fake_copy, fake_exact;       /* the image (16 KB aligned, 0x10000), its pool copy, the exact rip */
static int fake_copy_on = 1, fake_section_on = 1;
static unsigned char fake_vp[16];                        /* Wine's byte per 4 KB page of the image */
unsigned long long ios_jit_module_base_for_va( unsigned long long va, unsigned long long *size_out )
{
    if (va >= fake_mod_base && va < fake_mod_base + fake_mod_size) { if (size_out) *size_out = fake_mod_size; return fake_mod_base; }
    return 0;
}
static const char *ios_pe_module_name( uint64_t base ) { return base == fake_mod_base ? "x.dll" : "?"; }
void *ios_jit_rx_base_global;
size_t ios_jit_pool_size_global;
int ios_jit_low_contains( uintptr_t a ) { (void)a; return 0; }
uint64_t ios_native_rip_from_hostpc( uint64_t bb, uint64_t pc, const char **why )
{
    (void)pc; if (why) *why = "ok";
    return bb ? fake_exact : 0;
}
int ios_image_section_describe( unsigned long long va, char *buf, size_t len, unsigned long long *img_base )
{
    if (!fake_section_on || va < fake_img || va >= fake_img + 0x10000) return 0;
    if (img_base) *img_base = fake_img;
    snprintf( buf, len, "rva %#llx, section #17 '' rva 0xd980000+0x604000 chars 0xe0000040 (RWX)", va - fake_img );
    return 1;
}
unsigned char ios_reclaim_page_vprot( unsigned long long va )
{
    if (va >= fake_img && va < fake_img + 0x10000) return fake_vp[(va - fake_img) >> 12];
    return 0;
}
void *ios_jit_translate_addr( void *addr )
{
    uint64_t a = (uint64_t)addr;
    if (fake_copy_on && a >= fake_img && a < fake_img + 0x10000) return (void *)(fake_copy + (a - fake_img));
    return addr;
}
'''

MAIN = r'''
static uint64_t alloc(size_t n)
{
    mach_vm_address_t a = 0;
    assert(mach_vm_allocate(mach_task_self(), &a, n, VM_FLAGS_ANYWHERE) == KERN_SUCCESS);
    memset((void *)a, 0, n);
    return a;
}
static void w64(uint64_t a, uint64_t v) { memcpy((void *)a, &v, 8); }
static const unsigned char plain[] = { 0x48, 0x89, 0xe9, 0x8b, 0x89, 0x3c, 0, 0, 0, 0x48, 0x81, 0xc1, 8, 1, 0, 0 };
static const unsigned char stale[] = { 0x8b, 0x84, 0xfd, 0xff, 0x4a, 0x76, 0, 0, 0, 0x48, 0x01, 0xc1, 8, 1, 0, 0 };

struct world { uint64_t pool, state, img; fake_arm_thread_state64_t st; };
static struct world make(void)
{
    struct world w;
    uint64_t raw;
    memset(&w, 0, sizeof(w));
    w.pool = alloc(0x10000);
    raw = alloc(0x30000);
    w.img = (raw + 0x3fff) & ~0x3fffULL;                 /* 16 KB aligned */
    fake_img = w.img; fake_copy = alloc(0x10000);
    w.state = alloc(0x1000);
    fake_pool = w.pool; fake_pool_size = 0x10000; ios_jit_rx_base_global = (void *)w.pool; ios_jit_pool_size_global = 0x10000;
    fake_exact = w.img + 0x1659;                          /* the second 4 KB page of the host page */
    w64(w.state + 0, w.pool + 0x100);                     /* BlockBegin */
    w64(w.state + 0x18, w.img + 0x161b);                  /* State.rip: the block entry */
    memcpy((void *)(w.img + 0x1659), plain, sizeof(plain));
    w.st.__pc = w.pool + 0xe70;
    w.st.__x[28] = w.state;
    for (int i = 0; i < 16; i++) fake_vp[i] = 0x25;       /* R | X | committed everywhere: armed */
    return w;
}

int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    struct world w = make();

    if (!strcmp(s, "off") || !strcmp(s, "armed") || !strcmp(s, "differ") || !strcmp(s, "nocopy") || !strcmp(s, "wcsib") || !strcmp(s, "wcself")) {
        memcpy((void *)fake_copy + 0x1659, !strcmp(s, "differ") ? stale : plain, sizeof(plain));
        if (!strcmp(s, "off") || !strcmp(s, "wcsib") || !strcmp(s, "wcself")) {
            /* "off": siblings still RWX; the rip's page armed (0x25). Round 630: "wcsib" siblings WRITECOPY (0x2d, FFRS), "wcself" the
             * rip's page WRITECOPY itself. */
            fake_vp[0] = fake_vp[2] = fake_vp[3] = !strcmp(s, "off") ? 0x27 : 0x2d;
            if (!strcmp(s, "wcself")) fake_vp[1] = 0x2d;
            assert(mach_vm_protect(mach_task_self(), w.img, 0x4000, FALSE, VM_PROT_READ | VM_PROT_WRITE) == KERN_SUCCESS);
        } else {
            assert(mach_vm_protect(mach_task_self(), w.img, 0x4000, FALSE, VM_PROT_READ) == KERN_SUCCESS);
        }
        if (!strcmp(s, "nocopy")) { fake_copy_on = 0; fake_section_on = 0; }
        ios_rwx_fault_diag(&w.st, EXC_BAD_ACCESS);
        fprintf(stdout, "img=%llx\n", (unsigned long long)w.img);
    } else if (!strcmp(s, "benign")) {
        ios_rwx_fault_diag(&w.st, EXC_BAD_INSTRUCTION);                    /* another exception kind */
        ios_rwx_fault_diag(&w.st, 3 /* EXC_ARITHMETIC */);
        w.st.__pc = 0x1000;                                                /* host pc outside the pool */
        ios_rwx_fault_diag(&w.st, EXC_BAD_ACCESS);
        w.st.__pc = w.pool + 0xe70;
        for (int i = 0; i < 16; i++) fake_vp[i] = 0x23;                    /* Wine does not hold the page executable */
        ios_rwx_fault_diag(&w.st, EXC_BAD_ACCESS);
        for (int i = 0; i < 16; i++) fake_vp[i] = 0x25;
        w.st.__x[28] = 0x20000;                                            /* unreadable CPUState */
        ios_rwx_fault_diag(&w.st, EXC_BAD_ACCESS);
        w.st.__x[28] = w.state;
        fake_exact = 0x8000;                                               /* a RIP below 0x10000 */
        w64(w.state + 0x18, 0x8000);
        ios_rwx_fault_diag(&w.st, EXC_BAD_ACCESS);
        fputs("benign-done\n", stdout);
    } else if (!strcmp(s, "cap")) {
        ios_rwx_fault_diag(&w.st, EXC_BAD_ACCESS);                         /* report #1 */
        w.st.__pc += 0x40; fake_exact += 0x10;                             /* same 4 KB page: not a report */
        ios_rwx_fault_diag(&w.st, EXC_BAD_ACCESS);
        for (int i = 0; i < 6; i++) { w.st.__pc += 0x40; fake_exact = w.img + 0x1000 * (i % 4) + 0x20; ios_rwx_fault_diag(&w.st, EXC_BAD_ACCESS); }
    }
    return 0;
}
'''

if sys.platform != 'darwin' or not shutil.which(cc):
    print('skip the compiled runs: macOS and a C compiler are needed (the helper reads real Mach memory)')
else:
    with tempfile.TemporaryDirectory(prefix='rwxfault-') as d:
        d = Path(d)
        (d / 'h.c').write_text(PRELUDE + av_header + header + MAIN)
        subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function',
                        str(d / 'h.c'), '-o', str(d / 'h')], check=True)

        def run(scenario):
            return subprocess.run([str(d / 'h'), scenario], capture_output=True, text=True)

        def lines(r):
            return [l for l in r.stderr.splitlines() if l.startswith('[rwx-fault]')]

        r = run('off')
        l = lines(r)
        img = r.stdout.split('img=')[1].strip() if 'img=' in r.stdout else ''
        check('one line for the Monster Hunter shape (writable host page, Wine says read-only)', len(l) == 1, r.stderr)
        t = l[0] if l else ''
        check('it names the report, the exact rip and the block entry',
              t.startswith('[rwx-fault] rev=ajan5-mhs3 #1 guest rip=0x') and f'guest rip=0x{int(img, 16) + 0x1659:x} (block entry 0x{int(img, 16) + 0x161b:x})' in t, t)
        check('it prints the RWX section text', "section #17 ''" in t and '(RWX)' in t, t)
        check('the host region reads rw- and Wine bytes are listed for the four pages, this page 25',
              'prot=rw-' in t and '27 25 27 27 (this page 25)' in t, t)
        check('trap: OFF, naming the neighbouring page with W', 'trap: OFF: Wine\'s table says this page is read-only, its host page is writable, '
              'and a neighbouring 4 KB page of it has W' in t, t)
        check('a pool copy exists and the 32 bytes of both views are printed and equal',
              'pool copy: 0x' in t and '48 89 e9 8b 89 3c 00 00 00 48 81 c1 08 01 00 00' in t and t.endswith('image and pool copy equal'), t)

        # Round 630 (agent G1 S15): a WRITECOPY page (0x08) is host-writable too; the line says so for a sibling and for the page itself.
        t = (lines(run('wcsib')) or [''])[0]
        check('trap: OFF, naming the WRITECOPY neighbour (FFRS: 0x2d)', '2d 25 2d 2d (this page 25)' in t and 'trap: OFF: Wine\'s table says this page '
              'is read-only, its host page is writable, and a neighbouring 4 KB page of it is WRITECOPY' in t, t)
        t = (lines(run('wcself')) or [''])[0]
        check('trap: OFF, the page itself WRITECOPY', '2d 2d 2d 2d (this page 2d)' in t
              and "trap: OFF: Wine's table says this page is WRITECOPY, which keeps its host page writable" in t, t)

        r = run('armed')
        t = (lines(r) or [''])[0]
        check('a read-only host page: trap armed, prot r--', 'prot=r--' in t and 'trap: armed (a guest store here faults)' in t
              and '25 25 25 25 (this page 25)' in t, t)

        r = run('differ')
        t = (lines(r) or [''])[0]
        check('image and pool copy that differ are said to differ, both byte strings printed',
              t.endswith('image and pool copy DIFFER') and '8b 84 fd ff 4a 76' in t and '48 89 e9 8b 89 3c' in t, t)

        r = run('nocopy')
        t = (lines(r) or [''])[0]
        check('an address no pool-copied image holds: says so, no pool copy, no byte comparison',
              'no pool-copied image holds it' in t and 'pool copy: none' in t and t.endswith('image and pool copy not compared'), t)

        r = run('benign')
        check('another exception kind, a pc outside the pool, a page Wine does not hold executable, an unreadable CPUState '
              'and a RIP below 0x10000 print nothing', r.stderr == '' and r.stdout.strip() == 'benign-done', r.stderr + r.stdout)

        r = run('cap')
        l = lines(r)
        check('the same 4 KB page twice is one report; distinct pages are capped at 3', len(l) == 3, r.stderr)
        check('the reports are numbered 1 to 3', [x.split(' #')[1].split(' ')[0] for x in l] == ['1', '2', '3'], r.stderr)

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
                 '-include', str(config), '-include', str(build / 'shims/wine_ios_exit.h'), f'-I{build / "shims"}',
                 f'-I{wine / "build-macos/dlls/ntdll"}', f'-I{wine / "dlls/ntdll"}', f'-I{wine / "dlls/ntdll/unix"}',
                 f'-I{wine / "build-macos/include"}', f'-I{wine / "include"}', '-D__WINESRC__', '-DLTC_NO_PROTOTYPES',
                 '-DLTC_SOURCE', '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', '-DWINE_UNIX_LIB',
                 '-DWINE_IOS=1', '-Dget_thread_context=ntdll_get_thread_context', '-Dset_thread_context=ntdll_set_thread_context']
        r = subprocess.run(['xcrun', '-sdk', 'iphoneos', 'clang', *flags, str(build / 'signal_arm64_ios.c')], capture_output=True, text=True)
        check('signal_arm64_ios.c compiles for iOS (syntax only, pinned wine headers)', r.returncode == 0, r.stderr[-2000:])
        check('...and the new header adds no warning', 'ios_rwx_fault' not in r.stderr, r.stderr[-2000:])
    else:
        print('skip the iOS syntax check: no iphoneos SDK')
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

sys.exit(0 if ok else 1)
