#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[int3-x86]: an OPT-IN report for a 32-bit guest's int3 (ios_int3_x86_diag.h); no Wine runs.

Battle.net (builds 594 and 596, the owner's iPhone18,2): the launcher's login window spins for ever because its Chromium 108 renderers
(`Battle.net.exe --type=renderer`, 32-bit, Blizzard's own libcef.dll) die 9 times in 12 on an int3 at guest 0x6de800e1 = libcef.dll
(base 0x6c7b0000) + 0x16d00e1, a few hundred milliseconds after GpuMemoryThread starts on CrRendererMain. A release Chromium CHECK is
`int3; ud2` (cc 0f 0b) and prints nothing, and the public CEF 108.4.13 libcef.dll (the only windows32 build of Chromium 108.0.5359.125) is
not Blizzard's: .text is 0x84cf874 bytes against Blizzard's 0x84f5ca4 (the logs' section ranges), the TLS callbacks sit 0x27800 apart. The
address names nothing without Blizzard's file or symbols. The report prints what names it offline: the stack, the module list, the code
around the int3 and before the return addresses, and libcef.dll's CodeView record.

What this checks (the Mac has no Battle.net and no device log runs here):
  1. Source shape (every host): one header, included once after ios_wow_null_diag.h and before the Mach exception thread; one call site, inside
     the [int3-guest] block, behind `if (ios_int3_x86_enabled())`; the header writes, protects and stops nothing, reads guest memory only through
     ios_av_args_read, is capped at 3 reports and only for a 32-bit guest.
  2. "Nothing changes when the switch is unset" (the rule of the owner for the Rockstar Games Launcher, whose 64-bit libcef.dll 143 must not
     change): the switch is env.MADEIRA_INT3_X86_DUMP and only the value 1 turns it on; the ONLY lines of signal_arm64_ios.c that mention the
     report are the include and the guarded call (taking them out leaves no reference), the other launcher entry, the common session lines,
     the game lists and every other source never set it; the Battle.net entry sets it exactly once; behaviour never depends on whether
     libcef.dll is loaded (the module name is only matched, after the int3, to print its PE identity).
  3. Compiled runs (macOS): the production header is compiled with a fake thread state over a 32-bit window built in real memory from the numbers
     of the 596 log (registers at the int3, libcef.dll at 0x6c7b0000 with a size of 0x9f84000, the stack at ESP 0xcff2a4): the report names
     libcef.dll+0x16d00e1 and each register and stack word that points into a module; prints libcef's PE identity with the RSDS record (GUID,
     age, symbol key, PDB path); the EBP chain with the code at each return address; the code bytes around the int3 (with `??` where a page is not
     there); a repeat is capped at 3; a 64-bit guest, a window-less thread state, no TEB, no registers and the unset switch print nothing (and the
     unset switch reads no memory at all).
  4. The catalog entry, the launcher entry and the CI step exist; the real signal_arm64_ios.c still compiles for iOS (when a wine tree is there).
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
signal_src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
header_path = root / 'build/ntdll-unix/ios_int3_x86_diag.h'
header = header_path.read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def strip_comments(t):
    t = re.sub(r'/\*.*?\*/', '', t, flags=re.S)
    return re.sub(r'//[^\n]*', '', t)


code = strip_comments(header)

# ---------------------------------------------------------------- 1. source shape
inc = '#include "ios_int3_x86_diag.h"'
check('the header is included exactly once, after ios_wow_null_diag.h and before the Mach exception thread',
      signal_src.count(inc) == 1 and signal_src.index('#include "ios_wow_null_diag.h"') < signal_src.index(inc)
      < signal_src.index('static void *ios_mach_exception_thread( void *arg )'))
call = 'ios_int3_x86_diag( &bs, i3x_teb, gregs, grip );'
check('one call site', signal_src.count(call) == 1)
site = signal_src.index(call)
guard = signal_src.rindex('if (ios_int3_x86_enabled())', 0, site)
check('the call is behind `if (ios_int3_x86_enabled())`, inside the [int3-guest] block after the BackupRefPtr probe and before the TEB lookup',
      signal_src.rindex('"[int3-guest] guest_rip=0x%llx', 0, guard) > 0
      and signal_src.rindex('[brp-contain] ml616', 0, guard) > signal_src.rindex('"[int3-guest] guest_rip=0x%llx', 0, guard)
      and guard < site < signal_src.index('/* Look up per-thread TEB and trampoline for the faulting thread */', site)
      and signal_src[guard:site].count('{') == 1 and signal_src[guard:site].count('}') == 0)
check('exactly one reference to ios_int3_x86_enabled and one to ios_int3_x86_diag in signal_arm64_ios.c',
      signal_src.count('ios_int3_x86_enabled(') == 1 and signal_src.count('ios_int3_x86_diag(') == 1)
forbidden = ('thread_set_state', 'thread_suspend', 'thread_terminate', 'vm_protect', 'mprotect(', 'mach_vm_write', 'vm_write(',
             'mmap(', 'free(', 'malloc(', 'abort(', 'exit(', 'sigaction', 'fopen(', 'open(', 'mach_vm_read_overwrite', 'raise(', 'kill(')
check('no write, protect, stop, exit, file or allocation call, and no direct memory read (ios_av_args_read is the one reader)',
      not [f for f in forbidden if f in code], str([f for f in forbidden if f in code]))
raw_deref = re.findall(r'\*\s*\(\s*(?:const\s+)?(?:volatile\s+)?(?:u?int\d+_t|void|char|unsigned\w*|uintptr_t)\s*\*\s*\)', code)
check('no raw dereference of a guest address', not raw_deref, str(raw_deref))
check('only for a 32-bit guest (rip below 4 GB, a 4 GB aligned window base in x19) and with a TEB (x18 when it lies in the window, else the lookup)',
      '!gregs || grip < 2 || grip > 0xffffffffULL' in code and 'if (!thread_teb) return;' in code
      and '(base & 0xffffffffULL) == 0 && base >= 0x100000000ULL' in code
      and 'st->__x[18] >= base && st->__x[18] - base < 0x100000000ULL) thread_teb = (uintptr_t)st->__x[18];' in code)
check('capped at 3 reports per session', 'IOS_I3X_MAX_REPORTS   3' in header and '> IOS_I3X_MAX_REPORTS) return;' in code
      and 'static volatile int reports;' in code)
check('the switch is MADEIRA_INT3_X86_DUMP, read once, and only the exact value 1 turns it on',
      code.count('getenv(') == 1 and 'getenv( "MADEIRA_INT3_X86_DUMP" )' in code and 'enabled = e && e[0] == \'1\' && !e[1];' in code
      and 'static int enabled = -1;' in code)
mapping_ok = all(m in signal_src for m in ('gn[16] = { "RAX","RCX","RDX","RBX","RSP","RBP","RSI","RDI",'))
check('the register order handed over is FEX\'s guest register file (RAX RCX RDX RBX RSP RBP RSI RDI), as the existing [int3-guest] line prints it',
      mapping_ok and 'eax = (uint32_t)gregs[0]; ecx = (uint32_t)gregs[1]; edx = (uint32_t)gregs[2]; ebx = (uint32_t)gregs[3];' in code
      and 'esp = (uint32_t)gregs[4]; ebp = (uint32_t)gregs[5]; esi = (uint32_t)gregs[6]; edi = (uint32_t)gregs[7];' in code)
check('the module list is the PEB32 loader list, 96 entries at most, walked once', 'nmods = ios_wn32_modules( base, peb32, mods, 96 );' in code
      and code.count('ios_wn32_modules(') == 1)

# ---------------------------------------------------------------- 2. nothing changes when the switch is unset
stripped = signal_src
stripped = re.sub(r'/\* ajan18-bnet-cef \(build 597\): \[int3-x86\].*?#include "ios_int3_x86_diag\.h"\n', '', stripped, flags=re.S)
stripped = re.sub(r'\n\n *\/\* ajan18-bnet-cef \(build 597\): the opt-in 32-bit report.*?ios_int3_x86_diag\( &bs, i3x_teb, gregs, grip \);\n *\}\n', '\n', stripped, flags=re.S)
leftovers = [m for m in re.findall(r'[^\n]*(?:i3x|int3_x86|INT3_X86)[^\n]*', stripped)]
check('taking out the include and the guarded call leaves no reference to the report in signal_arm64_ios.c', not leftovers, str(leftovers[:4]))

launchers = (root / 'app/Madeira/BCDLaunchers.swift').read_text()
bn = launchers[launchers.index('static let battlenet = BCDLauncherSpec('):launchers.index('static let eaApp = BCDLauncherSpec(')]
ea = launchers[launchers.index('static let eaApp = BCDLauncherSpec('):]
common = re.search(r'static let commonSessionConfig = \[(.*?)\]', launchers).group(1)
line = '"env.MADEIRA_INT3_X86_DUMP = 1"'
check('Battle.net\'s launcher entry sets env.MADEIRA_INT3_X86_DUMP = 1, once', launchers.count(line) == 1 and line in bn
      # round 630 (agent A2 S5): the 32-bit fault report follows on the next line of the same list
      and 'sessionConfig: Self.commonSessionConfig + ["env.MADEIRA_PROTECT_WRITECOPY_AS_RW = 1", ' + line + ',\n' in bn
      and '"env.MADEIRA_X86_FAULT_DUMP = 1"],' in bn)
check('the EA app\'s entry and the common session lines do not', 'MADEIRA_INT3_X86_DUMP' not in ea.split('\n}\n')[0] and 'MADEIRA_INT3_X86_DUMP' not in common)


def tracked(rootdir):
    try:
        out = subprocess.run(['git', '-C', str(rootdir), 'ls-files'], capture_output=True, text=True, check=True).stdout.split('\n')
        return [Path(rootdir) / f for f in out if f]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in Path(rootdir).rglob('*') if p.is_file() and '.git' not in p.parts]


allowed = {
    'build/ntdll-unix/ios_int3_x86_diag.h', 'build/ntdll-unix/signal_arm64_ios.c', 'app/Madeira/BCDLaunchers.swift',
    'build/tools/gen-config-catalog.py', 'app/Madeira/ConfigCatalog.generated.swift', 'tests/host/check-int3-x86-diag.py',
    '.github/workflows/build-ipa.yml', 'tools/int3x86-match.py',
    # the V8 flag-freeze fix (check-wc-old-prot.py) names the report in its comments and checks the entry that sets both switches
    'tests/host/check-wc-old-prot.py', 'build/ntdll-unix/ios_wc_old_prot.h', 'build/ntdll-unix/virtual_ios.c',
    # the 32-bit fault report of the 2026-10-24 log round (env.MADEIRA_X86_FAULT_DUMP) names this switch to tell the two apart
    'build/ntdll-unix/ios_x86_fault_diag.h', 'tests/host/check-x86-fault-diag.py',
    # round 630 (agent A2 S5): the launcher check pins Battle.net's session lines, this switch among them
    'tests/host/check-bcd-launchers.py',
}
users = []
for f in tracked(root):
    rel = str(f.relative_to(root))
    if rel in allowed or not f.is_file() or f.suffix in ('.png', '.jpg', '.ipa', '.dll', '.exe', '.a', '.dylib', '.zip', '.xz', '.gz', '.bz2', '.pdf', '.icns'):
        continue
    if rel.startswith('docs/') or rel.startswith('tests/fixtures/'):
        continue
    try:
        if 'MADEIRA_INT3_X86_DUMP' in f.read_text(errors='ignore'):
            users.append(rel)
    except OSError:
        pass
check('no other source, game list, recommendation, rule or test mentions the switch (docs and fixtures aside)', not users, str(users))
recs = (root / 'recommended/recommendations.json').read_text() if (root / 'recommended/recommendations.json').exists() else ''
check('the bundled game lists do not set it', 'INT3_X86' not in recs)
check('behaviour never depends on libcef.dll being loaded: the name is only compared (case-insensitively) after the int3, to print the PE identity',
      code.count('libcef.dll') <= 3 and re.search(r'!ios_i3x_icmp\( mods\[k\]\.name, "libcef\.dll" \)\) \{ ios_i3x_image', code) is not None)

# ---------------------------------------------------------------- 3. compiled runs
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

/* count every guest-memory read the reports make: the unset switch must make none */
static unsigned long g_reads;
static kern_return_t counting_read( vm_map_t t, mach_vm_address_t a, mach_vm_size_t n, mach_vm_address_t buf, mach_vm_size_t *got )
{
    g_reads++;
    return mach_vm_read_overwrite( t, a, n, buf, got );
}
#define mach_vm_read_overwrite counting_read
'''

# the window of the 596 log: Battle.net.exe 0x400000, ntdll 0xfff40000, libcef.dll 0x6c7b0000 (size 0x9f84000), TEB32 0x302000, PEB32 0x311000
MAIN = r'''
#define WIN 0x5000000000ULL
#define TEB64_G 0x300000u
#define TEB32_G 0x302000u
#define PEB32_G 0x311000u
#define LDR_G   0x350000u
#define LIBCEF  0x6c7b0000u
#define LIBCEF_SIZE 0x9f84000u
#define EIP_INT3 0x6de800e1u      /* libcef.dll + 0x16d00e1 */
#define DBG_RVA 0x96bf3bcu
#define RSDS_RVA 0x96bf3d8u

static uint64_t alloc_at(uint64_t a, size_t n)
{
    mach_vm_address_t m = a;
    if (mach_vm_allocate(mach_task_self(), &m, n, VM_FLAGS_FIXED) != KERN_SUCCESS) { puts("alloc-failed"); exit(3); }
    return a;
}
static void w32(uint64_t a, uint32_t v) { memcpy((void *)a, &v, 4); }
static void w16(uint64_t a, uint16_t v) { memcpy((void *)a, &v, 2); }

static void ldr_entry(uint32_t e, uint32_t next, uint32_t base, uint32_t size, const char *name, uint32_t namebuf)
{
    size_t n = strlen(name);
    w32(WIN + e, next);
    w32(WIN + e + 0x18, base);
    w32(WIN + e + 0x20, size);
    w16(WIN + e + 0x2c, (uint16_t)(n * 2)); w16(WIN + e + 0x2e, (uint16_t)(n * 2 + 2)); w32(WIN + e + 0x30, namebuf);
    for (size_t i = 0; i <= n; i++) w16(WIN + namebuf + i * 2, (uint16_t)name[i]);
}

/* a PE32 header page at guest address base: one .text section, a debug directory with one CodeView entry */
static void pe_header(uint32_t base, uint32_t size, uint32_t stamp, int with_debug)
{
    uint8_t *h = (uint8_t *)(WIN + base);
    uint32_t nt = 0x80, opt = nt + 24;
    h[0] = 'M'; h[1] = 'Z'; w32((uint64_t)h + 0x3c, nt);
    memcpy(h + nt, "PE\0\0", 4);
    w16((uint64_t)h + nt + 4, 0x14c); w16((uint64_t)h + nt + 6, 2); w32((uint64_t)h + nt + 8, stamp);
    w16((uint64_t)h + nt + 20, 0xe0); w16((uint64_t)h + nt + 22, 0x2122);
    w16((uint64_t)h + opt, 0x10b);
    w32((uint64_t)h + opt + 16, 0x3a254c0);                 /* AddressOfEntryPoint */
    w32((uint64_t)h + opt + 56, size);                      /* SizeOfImage */
    w32((uint64_t)h + opt + 64, 0);                         /* CheckSum */
    if (with_debug) { w32((uint64_t)h + opt + 96 + 6 * 8, DBG_RVA); w32((uint64_t)h + opt + 100 + 6 * 8, 28); }
    w32((uint64_t)h + opt + 96 + 9 * 8, 0x96bef90); w32((uint64_t)h + opt + 100 + 9 * 8, 0x18);   /* TLS directory */
    uint8_t *s = h + opt + 0xe0;
    memcpy(s, ".text\0\0\0", 8); w32((uint64_t)s + 8, 0x84f5ca4); w32((uint64_t)s + 12, 0x1000); w32((uint64_t)s + 16, 0x84f5e00);
    s += 40;
    memcpy(s, ".rdata\0\0", 8); w32((uint64_t)s + 8, 0x12d0000); w32((uint64_t)s + 12, 0x84f7000); w32((uint64_t)s + 16, 0x12d0000);
}

/* the debug directory and its RSDS record at the RVAs libcef.dll's own header uses */
static void debug_dir(uint32_t base)
{
    uint64_t d = WIN + base + DBG_RVA, r = WIN + base + RSDS_RVA;
    const char *pdb = "D:\\bnet\\cef\\out\\Release_x86\\libcef.dll.pdb";
    uint8_t guid[16] = { 0xf6, 0x60, 0x66, 0xc6, 0x62, 0x01, 0x88, 0xc6, 0x4c, 0x4c, 0x44, 0x20, 0x50, 0x44, 0x42, 0x2e };
    w32(d + 4, 0x63976dec);                     /* TimeDateStamp */
    w32(d + 12, 2);                             /* IMAGE_DEBUG_TYPE_CODEVIEW */
    w32(d + 16, 24 + (uint32_t)strlen(pdb) + 1);
    w32(d + 20, RSDS_RVA); w32(d + 24, 0x96be1d8);
    memcpy((void *)r, "RSDS", 4); memcpy((void *)(r + 4), guid, 16); w32(r + 20, 1); memcpy((void *)(r + 24), pdb, strlen(pdb) + 1);
}

struct world { fake_arm_thread_state64_t st; uint64_t teb64; uint64_t gregs[8]; uint64_t grip; };

static struct world make(int with_ldr)
{
    static int built;
    struct world w;
    memset(&w, 0, sizeof(w));
    if (!built)
    {
        built = 1;
        alloc_at(WIN + 0x300000, 0x20000);              /* TEB64 page(s), TEB32 at +0x2000, PEB32 at 0x311000 */
        alloc_at(WIN + 0x350000, 0x10000);              /* PEB_LDR_DATA32, entries, names */
        alloc_at(WIN + 0xcfc000, 0x4000);               /* the stack below 0xd00000 */
        alloc_at(WIN + 0x400000, 0x4000);               /* Battle.net.exe's header page */
        alloc_at(WIN + LIBCEF, 0x4000);                 /* libcef.dll's header page */
        alloc_at(WIN + LIBCEF + (DBG_RVA & ~0x3fffu), 0x8000);   /* its debug directory and the RSDS record */
        alloc_at(WIN + 0x6de7c000, 0x4000);             /* the code before the int3 ... */
        alloc_at(WIN + 0x6de80000, 0x4000);             /* ... and the page of the int3 (+0x100 after it is also in this page) */
        alloc_at(WIN + 0x6dd9c000, 0x4000);             /* the code at the first return address */
        alloc_at(WIN + 0x00474000, 0x4000);             /* the exe's code at the last return address */
        w32(WIN + TEB64_G + 0x40, 0x2000);              /* the fake TEB64's WowTebOffset */
        w32(WIN + TEB32_G + 0x18, TEB32_G);             /* NT_TIB32.Self */
        w32(WIN + TEB32_G + 0x30, PEB32_G);             /* Peb */
        w32(WIN + PEB32_G + 0x0c, LDR_G);               /* LdrData */
        w32(WIN + PEB32_G + 0x44, 0x000e0001);          /* TlsBitmapBits: 0, 17, 18, 19 */
        w32(WIN + TEB32_G + 0x04, 0xd00000);            /* StackBase */
        w32(WIN + TEB32_G + 0x08, 0xc00000);            /* StackLimit */
        w32(WIN + TEB32_G + 0xe0c, 0xbf0000);           /* DeallocationStack */
        w32(WIN + TEB32_G + 0x34, 5);                   /* LastErrorValue */
        w32(WIN + TEB32_G + 0xbf4, 0xc0000017);         /* LastStatusValue: STATUS_NO_MEMORY */
        w32(WIN + LDR_G + 0x0c, LDR_G + 0x100);
        w32(WIN + LDR_G + 0x10, LDR_G + 0x300);
        ldr_entry(LDR_G + 0x100, LDR_G + 0x200, 0x400000, 0x80000, "Battle.net.exe", LDR_G + 0x1000);
        ldr_entry(LDR_G + 0x200, LDR_G + 0x300, LIBCEF, LIBCEF_SIZE, "libcef.dll", LDR_G + 0x1100);
        ldr_entry(LDR_G + 0x300, LDR_G + 0x0c, 0xfff40000, 0xaa000, "ntdll.dll", LDR_G + 0x1200);
        pe_header(0x400000, 0x80000, 0x6504c0de, 0);
        pe_header(LIBCEF, LIBCEF_SIZE, 0x63976dec, 1);
        debug_dir(LIBCEF);
        /* the code before the int3: a recognisable ramp, the CHECK's own bytes at EIP_INT3: cc 0f 0b, then the next block's push $4 / push edi, as in the public libcef.dll */
        for (uint32_t a = EIP_INT3 - 0x100; a < EIP_INT3 + 0x20; a++) *(uint8_t *)(WIN + a) = (uint8_t)(a - (EIP_INT3 - 0x100));
        { const uint8_t chk[6] = { 0xcc, 0x0f, 0x0b, 0x6a, 0x04, 0x57 }; memcpy((void *)(WIN + EIP_INT3), chk, sizeof(chk)); }
        /* the call sites: e8 rel32 before the return addresses */
        for (uint32_t a = 0x6dd9c000 + 0xc00; a < 0x6dd9c000 + 0xc80; a++) *(uint8_t *)(WIN + a) = 0xaa;
        *(uint8_t *)(WIN + 0x6dd9c000 + 0xc4b) = 0xe8;
        for (uint32_t a = 0x474100; a < 0x474180; a++) *(uint8_t *)(WIN + a) = 0xbb;
        /* the stack (ESP 0xcff2a4): the frame chain 0xcff2ac -> 0xcff3b0 -> 0xcff4a0 -> 0, the registers' neighbours */
        w32(WIN + 0xcff2a4, 0x1);
        w32(WIN + 0xcff2a8, 0xcff2e0);
        w32(WIN + 0xcff2ac, 0xcff3b0);                   /* saved ebp */
        w32(WIN + 0xcff2b0, 0x6dd9c000 + 0xc50);        /* return address into libcef (RVA 0x15ecc50) */
        w32(WIN + 0xcff2b4, 0xcff280);
        w32(WIN + 0xcff3b0, 0xcff4a0);
        w32(WIN + 0xcff3b4, 0x474150);                  /* return address into Battle.net.exe */
        w32(WIN + 0xcff4a0, 0);
        w32(WIN + 0xcff4a4, 0xfff8e440 + 0x30);         /* into ntdll */
        w32(WIN + 0xcff280, 0xfff8e440);                /* the object ECX points at: its first dword is an ntdll address */
        w32(WIN + 0xcff284, 0x6dd9c000 + 0xc50);
    }
    ios_jit_rx_base_global = NULL; ios_jit_pool_size_global = 0;
    w.teb64 = WIN + TEB64_G;
    w.st.__x[19] = WIN;
    /* the registers of the 596 log at the int3, FEX order RAX RCX RDX RBX RSP RBP RSI RDI */
    w.gregs[0] = 1; w.gregs[1] = 0xcff280; w.gregs[2] = 0xfff8e440; w.gregs[3] = 0xcf0000;
    w.gregs[4] = 0xcff2a4; w.gregs[5] = 0xcff2ac; w.gregs[6] = 0xcff3b0; w.gregs[7] = 0xcff3e0;
    w.grip = EIP_INT3 + 1;
    (void)with_ldr;
    return w;
}
'''

SCENARIOS = r'''
static void guarded(struct world *w)
{
    /* the call site of signal_arm64_ios.c: nothing but the guard in front of the call */
    if (ios_int3_x86_enabled()) ios_int3_x86_diag(&w->st, w->teb64, w->gregs, w->grip);
}

int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    struct world w;

    if (!strcmp(s, "full")) {
        w = make(1);
        guarded(&w);
        printf("reads=%lu\n", g_reads);
        return 0;
    }
    if (!strcmp(s, "off")) {
        w = make(1);
        guarded(&w); guarded(&w);
        printf("enabled=%d reads=%lu\n", ios_int3_x86_enabled(), g_reads);
        return 0;
    }
    if (!strcmp(s, "enabled")) {
        printf("enabled=%d\n", ios_int3_x86_enabled());
        return 0;
    }
    if (!strcmp(s, "cap")) {
        w = make(1);
        for (int i = 0; i < 5; i++) guarded(&w);
        return 0;
    }
    if (!strcmp(s, "benign")) {
        w = make(1);
        w.grip = 0x140001001ULL; ios_int3_x86_diag(&w.st, w.teb64, w.gregs, w.grip);                /* a 64-bit guest */
        w.grip = EIP_INT3 + 1; w.st.__x[19] = 0x5000000010ULL; ios_int3_x86_diag(&w.st, w.teb64, w.gregs, w.grip);   /* x19 is no window base */
        w.st.__x[19] = 0; ios_int3_x86_diag(&w.st, w.teb64, w.gregs, w.grip);
        w.st.__x[19] = WIN; ios_int3_x86_diag(&w.st, 0, w.gregs, w.grip);                           /* no TEB */
        ios_int3_x86_diag(&w.st, w.teb64, NULL, w.grip);                                            /* no registers */
        w.grip = 1; ios_int3_x86_diag(&w.st, w.teb64, w.gregs, w.grip);                             /* no address */
        printf("benign-done reads=%lu\n", g_reads);
        return 0;
    }
    if (!strcmp(s, "x18")) {
        w = make(1);
        w.st.__x[18] = w.teb64;                        /* the TEB register in JIT code */
        ios_int3_x86_diag(&w.st, 0, w.gregs, w.grip);    /* the registry lookup found nothing */
        w.st.__x[18] = WIN + 0x7000000;                  /* a wrong x18 and a good lookup: the lookup is used only when x18 is outside the window */
        return 0;
    }
    if (!strcmp(s, "x18-other-window")) {
        w = make(1);
        w.st.__x[18] = 0x6000000000ULL + TEB64_G;        /* x18 in another window: the caller's TEB is used */
        ios_int3_x86_diag(&w.st, w.teb64, w.gregs, w.grip);
        return 0;
    }
    if (!strcmp(s, "noteb")) {
        w = make(1);
        w32(WIN + TEB32_G + 0x18, 0x999000);            /* Self does not match: no modules */
        guarded(&w);
        w32(WIN + TEB32_G + 0x18, TEB32_G);
        return 0;
    }
    if (!strcmp(s, "nostack")) {
        w = make(1);
        w.gregs[4] = 0x6000000; w.gregs[5] = 0x6000010;  /* ESP and EBP in unmapped memory */
        guarded(&w);
        return 0;
    }
    if (!strcmp(s, "edge")) {
        w = make(1);
        w.grip = 0x6de7c000 + 0x10 + 1;                  /* the int3 near the start of a mapped page: the code before it is not there */
        w.gregs[1] = 0x10;                                /* ECX below 0x10000: not an object */
        guarded(&w);
        return 0;
    }
    return 0;
}
'''

if sys.platform != 'darwin' or not shutil.which(cc):
    print('skip the compiled runs: macOS and a C compiler are needed')
else:
    av_header = (root / 'build/ntdll-unix/ios_av_args_diag.h').read_text()
    null_header = (root / 'build/ntdll-unix/ios_wow_null_diag.h').read_text()
    with tempfile.TemporaryDirectory(prefix='int3x86-') as d:
        d = Path(d)
        (d / 'h.c').write_text(PRELUDE + '\n' + av_header + '\n' + null_header + '\n' + header + '\n' + MAIN + SCENARIOS)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function', str(d / 'h.c'), '-o', str(d / 'h')],
                           capture_output=True, text=True)
        check('the production header compiles (host cc, -Wall -Wextra -Werror)', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            def run(scn, **env):
                e = {k: v for k, v in os.environ.items() if k != 'MADEIRA_INT3_X86_DUMP'}
                e.update(env)
                return subprocess.run([str(d / 'h'), scn], capture_output=True, text=True, env=e)

            def lines(r):
                return [l for l in r.stderr.splitlines() if l.startswith('[int3-x86]')]

            # the switch
            vals = {}
            for v in (None, '', '0', '11', 'true', ' 1', '1 ', 'yes', '2', '1'):
                r = run('enabled') if v is None else run('enabled', MADEIRA_INT3_X86_DUMP=v)
                vals[v] = r.stdout.strip()
            check('only the exact value 1 turns the report on (unset, "", 0, 11, true, " 1", "1 ", yes, 2: off)',
                  vals[None] == 'enabled=0' and vals[''] == 'enabled=0' and all(vals[v] == 'enabled=0' for v in ('0', '11', 'true', ' 1', '1 ', 'yes', '2'))
                  and vals['1'] == 'enabled=1', str(vals))
            r = run('off')
            check('with the switch unset the int3 path prints nothing and reads no guest memory at all (twice, with a window ready to be read)',
                  r.stderr == '' and r.stdout.strip() == 'enabled=0 reads=0', r.stderr + r.stdout)
            r = run('off', MADEIRA_INT3_X86_DUMP='0')
            check('...nor with the value 0', r.stderr == '' and r.stdout.strip() == 'enabled=0 reads=0', r.stderr + r.stdout)

            # the full report
            r = run('full', MADEIRA_INT3_X86_DUMP='1')
            ls = lines(r)
            text = '\n'.join(ls)
            check('the first line names the int3 as libcef.dll+0x16d00e1 with the registers of the 596 log and the module count',
                  ls and 'rev=ajan18-bnet-cef #1' in ls[0] and 'int3 at guest 0x6de800e1 (libcef.dll+0x16d00e1)' in ls[0] and '3 modules' in ls[0]
                  and 'EAX=0x1 ECX=0xcff280 EDX=0xfff8e440 EBX=0xcf0000 ESI=0xcff3b0 EDI=0xcff3e0 EBP=0xcff2ac ESP=0xcff2a4' in ls[0], r.stderr)
            check('registers that point into a module: EDX = ntdll.dll+0x4e440',
                  any('registers that point into a module:' in l and 'EDX=0xfff8e440(ntdll.dll+0x4e440)' in l and 'ECX' not in l.split('module:')[1] for l in ls), r.stderr)
            check('the thread facts: stack range and depth, the last error and status, the TLS bitmap (4 of 64 taken)',
                  any('thread TEB32=0x302000 PEB32=0x311000 StackBase=0xd00000 StackLimit=0xc00000 DeallocationStack=0xbf0000 (ESP is 0xd5c below the base) '
                      'LastErrorValue=0x5 LastStatusValue=0xc0000017 TlsBitmapBits=0x00000000000e0001 (4 of 64 taken) TlsExpansionSlots=0x0' in l for l in ls), r.stderr)
            check('the module list: the exe, libcef.dll with its loader size, ntdll.dll',
                  any('modules (name=base/size): Battle.net.exe=0x400000/0x80000 libcef.dll=0x6c7b0000/0x9f84000 ntdll.dll=0xfff40000/0xaa000' in l for l in ls), r.stderr)
            check('the PE identity of libcef.dll: stamp, SizeOfImage, entry point, debug and TLS directories, header hash',
                  any('image libcef.dll base=0x6c7b0000 ldr-size=0x9f84000: machine=0x14c magic=0x10b TimeDateStamp=0x63976dec SizeOfImage=0x9f84000 CheckSum=0x0 '
                      'EntryPoint=0x3a254c0 export=0x0+0x0 debug=0x96bf3bc+0x1c tls=0x96bef90+0x18 header-fnv=' in l for l in ls), r.stderr)
            check('the sections', any('image libcef.dll sections: .text@0x1000+0x84f5ca4(raw 0x84f5e00) .rdata@0x84f7000+0x12d0000(raw 0x12d0000)' in l for l in ls), r.stderr)
            check('the debug entry and the CodeView record: GUID (mixed-endian fields), age, the symbol server key and the PDB path',
                  any('debug[0]: type=2 size=0x' in l and 'rva=0x96bf3d8' in l and 'TimeDateStamp=0x63976dec' in l for l in ls)
                  and any('CodeView RSDS: guid={C66660F6-0162-C688-4C4C-44205044422E} age=1 ' in l
                          and 'symbol-key=C66660F60162C6884C4C44205044422E1 ' in l and 'pdb="D:\\bnet\\cef\\out\\Release_x86\\libcef.dll.pdb"' in l for l in ls), r.stderr)
            check('the exe\'s PE identity is not printed (the int3 is in libcef.dll)', not any('image Battle.net.exe' in l for l in ls), r.stderr)
            check('96 words of stack in 12 rows from ESP, the first being the saved ones of the log',
                  len([l for l in ls if ' stack ESP+0x' in l]) == 12 and any('stack ESP+0x000: 00000001 00cff2e0 00cff3b0 6dd9cc50 00cff280 ' in l for l in ls), r.stderr)
            check('the stack words that point into a module: +0x00c = libcef.dll+0x15ecc50 (the return address), +0x1ac... ',
                  any('stack words that point into a module (' in l and '+0x00c=6dd9cc50(libcef.dll+0x15ecc50)' in l for l in ls), r.stderr)
            check('the EBP chain: three frames, each return address with module+RVA, then it ends at the NULL saved ebp',
                  any('frame #0 ebp=0xcff2ac saved-ebp=0xcff3b0 return=0x6dd9cc50 (libcef.dll+0x15ecc50)' in l for l in ls)
                  and any('frame #1 ebp=0xcff3b0 saved-ebp=0xcff4a0 return=0x00474150 (Battle.net.exe+0x74150)' in l for l in ls)
                  and any('frame #2 ebp=0xcff4a0 saved-ebp=0x0 return=0xfff8e470 (ntdll.dll+0x4e470)' in l for l in ls)
                  and not any('frame #3' in l for l in ls), r.stderr)
            check('the code around the int3: 256 before and 32 after, rows of 32, the CHECK\'s own bytes cc 0f 0b at offset +0 and the ramp before',
                  any('code int3: 256 bytes before and 32 after guest 0x6de800e1 (libcef.dll+0x16d00e1) (288 of 288 readable)' in l and 'the first at 0x6de7ffe1' in l for l in ls)
                  and any('code int3 -256: 00 01 02 03 04 05 06 07' in l for l in ls) and any('code int3 +0: cc 0f 0b 6a 04 57 ' in l for l in ls), r.stderr)
            check('the code before the first two return addresses (64 before, 8 after), with the call opcode where the constructed bytes put it',
                  any('code ret#0: 64 bytes before and 8 after guest 0x6dd9cc50 (libcef.dll+0x15ecc50) (72 of 72 readable)' in l for l in ls)
                  and any('code ret#0 -64:' in l for l in ls) and any('code ret#1: 64 bytes before and 8 after guest 0x00474150 (Battle.net.exe+0x74150)' in l for l in ls), r.stderr)
            check('what ECX points at is printed with module+RVA for its words (the object on the stack); EBX, which points at unmapped memory, is left out',
                  any('[ECX=0xcff280]: fff8e440(ntdll.dll+0x4e440) 6dd9cc50(libcef.dll+0x15ecc50)' in l for l in ls)
                  and any('[ESI=0xcff3b0]:' in l for l in ls) and not any('[EBX=' in l for l in ls), r.stderr)
            check('the report ends with a marker and every line carries the prefix', ls and ls[-1].endswith('end of report #1') and all(l.startswith('[int3-x86]') for l in r.stderr.splitlines()), r.stderr)
            check('the number of lines is bounded (a report is under 80 lines)', 25 < len(ls) < 80, str(len(ls)))
            check('it did read guest memory (the switch is on)', int(re.search(r'reads=(\d+)', r.stdout).group(1)) > 20, r.stdout)

            r = run('cap', MADEIRA_INT3_X86_DUMP='1')
            heads = [l for l in lines(r) if 'rev=ajan18-bnet-cef #' in l]
            check('five int3s: three reports, then silence', len(heads) == 3 and heads[2].find('#3') > 0, r.stderr)
            r = run('benign', MADEIRA_INT3_X86_DUMP='1')
            check('a 64-bit guest, a thread state without a 4 GB window base, no TEB, no registers and no address print nothing and read nothing',
                  r.stderr == '' and r.stdout.strip() == 'benign-done reads=0', r.stderr + r.stdout)
            r = run('x18', MADEIRA_INT3_X86_DUMP='1')
            ls = lines(r)
            check('the TEB register x18 gives the TEB when the registry lookup found none: modules, PE identity and thread facts print',
                  ls and '3 modules' in ls[0] and any('image libcef.dll CodeView RSDS' in l for l in ls) and any('thread TEB32=0x302000' in l for l in ls), r.stderr)
            r = run('x18-other-window', MADEIRA_INT3_X86_DUMP='1')
            ls = lines(r)
            check('an x18 in another window is not trusted: the caller\'s TEB is used', ls and '3 modules' in ls[0], r.stderr)
            r = run('noteb', MADEIRA_INT3_X86_DUMP='1')
            ls = lines(r)
            check('a TEB32 whose Self does not match: no modules, but the registers, the stack and the code still print',
                  ls and '0 modules' in ls[0] and 'no module' in ls[0] and any(' stack ESP+0x000:' in l for l in ls) and any('code int3 +0: cc 0f 0b 6a 04 57' in l for l in ls)
                  and not any('image ' in l for l in ls), r.stderr)
            r = run('nostack', MADEIRA_INT3_X86_DUMP='1')
            ls = lines(r)
            check('an unreadable stack says so and the code still prints', any('guest stack at ESP=0x6000000 is not readable' in l for l in ls)
                  and any('code int3 +0:' in l for l in ls) and not any('frame #0' in l for l in ls), r.stderr)
            r = run('edge', MADEIRA_INT3_X86_DUMP='1')
            ls = lines(r)
            check('an int3 near the start of a mapped page: the unreadable bytes print as ?? and the count says so',
                  any('(' in l and 'readable)' in l and 'of 288 readable' in l and not '(288 of 288' in l for l in ls) and any('code int3 -256: ??' in l for l in ls), r.stderr)
            check('...and ECX below 0x10000 is not an object', not any('[ECX=' in l for l in ls), r.stderr)

# ---------------------------------------------------------------- 3b. the offline matcher that reads the report
r = subprocess.run([sys.executable, '-I', str(root / 'tools/int3x86-match.py'), '--selftest'], capture_output=True, text=True)
check('tools/int3x86-match.py (finds a report\'s code window in a PE file by voting 8-byte anchors, relocated operands aside) passes its self-test',
      r.returncode == 0 and 'selftest ok' in r.stdout, r.stdout + r.stderr)

# ---------------------------------------------------------------- 4. catalog, launcher, CI, iOS compile
wine0 = Path(os.environ.get('WINE_SRC', root / 'wine'))
if (wine0 / 'include/winternl.h').exists() and shutil.which('xcrun'):
    off_src = r'''
#include <stddef.h>
#include "windef.h"
#include "winternl.h"
_Static_assert(offsetof(TEB32, Tib.StackBase) == 0x04, "stack base");
_Static_assert(offsetof(TEB32, Tib.StackLimit) == 0x08, "stack limit");
_Static_assert(offsetof(TEB32, LastErrorValue) == 0x34, "last error");
_Static_assert(offsetof(TEB32, LastStatusValue) == 0xbf4, "last status");
_Static_assert(offsetof(TEB32, DeallocationStack) == 0xe0c, "deallocation stack");
int main(void) { return 0; }
'''
    wanted = {'IOS_I3X_TEB_STACKBASE': 0x04, 'IOS_I3X_TEB_STACKLIMIT': 0x08, 'IOS_I3X_TEB_LASTERROR': 0x34, 'IOS_I3X_TEB_LASTSTATUS': 0xbf4,
              'IOS_I3X_TEB_DEALLOC': 0xe0c}
    got = {m.group(1): int(m.group(2), 16) for m in re.finditer(r'#define (IOS_I3X_TEB_\w+)\s+(0x[0-9a-f]+)', header)}
    check('the header\'s TEB32 offsets are the ones the pinned headers assert below', all(got.get(k) == v for k, v in wanted.items()), str(got))
    with tempfile.TemporaryDirectory(prefix='i3x-off-') as dd:
        (Path(dd) / 'off.c').write_text(off_src)
        r = subprocess.run(['xcrun', '-sdk', 'macosx', 'clang', '-fsyntax-only', f'-I{wine0 / "include"}', f'-I{wine0 / "build-macos/include"}',
                            '-D__WINESRC__', str(Path(dd) / 'off.c')], capture_output=True, text=True)
        check('the pinned winternl.h has TEB32 at those offsets', r.returncode == 0, r.stderr[-2000:])
else:
    print('skip the offset check against the pinned headers: no wine/include/winternl.h')

gen_cat = (root / 'build/tools/gen-config-catalog.py').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the config catalog (generator and generated Swift) has the entry, default off, with its sources',
      '"env.MADEIRA_INT3_X86_DUMP"' in gen_cat and '"sources": ["build/ntdll-unix/ios_int3_x86_diag.h", "build/ntdll-unix/signal_arm64_ios.c"]' in gen_cat
      and 'ConfigOption(key: "env.MADEIRA_INT3_X86_DUMP", title: "Report a 32-bit program\'s int3 in full", kind: .bool, defaultValue: "0"' in catalog)
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('build-ipa.yml runs this check', 'python3 tests/host/check-int3-x86-diag.py' in ci and ci.count('check-int3-x86-diag.py') == 1)

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
        check('...and the new header adds no warning', 'ios_int3_x86' not in r.stderr and 'i3x' not in r.stderr, r.stderr[-2000:])
    else:
        print('skip the iOS syntax check: no iphoneos SDK')
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

print('PASS: [int3-x86]' if ok else 'FAILED: [int3-x86]')
sys.exit(0 if ok else 1)
