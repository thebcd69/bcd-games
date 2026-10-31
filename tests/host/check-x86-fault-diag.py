#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[x86-fault]: an OPT-IN report for a 32-bit guest's unhandled access violation or int3 (ios_x86_fault_diag.h); no Wine runs.

Need for Speed: Most Wanted (speed.exe, six logs of three builds and four phones) dies on a read at guest EIP 0x0081AB7A of 0xB514AB6A / 0xB449AB6A
/ 0xB471AB6A, with ECX - EDI = 0xAAAAAAAA in every one; Need for Speed: Underground 2 (SPEED2.EXE, five logs) on an int3 at 0x4405A9 after the menu's
Enter key. Neither log can show the code: the existing [rsp-trunc] line reads a guest address as a host address, and the Battle.net report of build 597
(env.MADEIRA_INT3_X86_DUMP) is for an int3 only and for one launcher entry. This report is the same evidence behind its own switch,
env.MADEIRA_X86_FAULT_DUMP = 1, which only those two games' recommended lists set.

What this checks (the Mac has no NFS and no device log runs here):
  1. Source shape (every host): one header, included once after the three earlier 32-bit diagnostics and before the Mach exception thread; two
     call sites in signal_arm64_ios.c, each behind `if (ios_x86_fault_enabled()`, the int3 one in the [int3-guest] block after the Battle.net call and
     the access-violation one in the [rsp-trunc] block, for EXC_BAD_ACCESS in JIT code only; the header writes, protects and stops nothing, reads
     guest memory only through the existing fault-safe readers, reads one environment variable and is capped at 3 reports.
  2. "Nothing changes when the switch is unset": the switch is env.MADEIRA_X86_FAULT_DUMP and only the value 1 turns it on; taking the include and
     the two guarded blocks out of signal_arm64_ios.c leaves no reference; no other source sets or reads it; the bundled lists that carry it are
     exactly Need for Speed: Most Wanted and Underground 2, each once and with minBuild 598.
  3. Compiled runs (macOS): the production header is compiled over real memory with a 32-bit window built from the numbers of the 592 log of
     Most Wanted (EAX 0xA6A0110, EDI 0xA6A00C0, ECX 0xB514AB6A, ESP 0x127FDDC, EBP 0x127FDF4, the block at 0x81AB75 in speed.exe): the headline names the
     block and the module+RVA; the fault address minus each register shows -EDI=0xaaaaaaaa; the registers' objects print (EAX and EDI, not the
     unreadable ECX); the code window holds the constructed bytes; the stack, the EBP chain and the thread facts print; the PE identity of the
     program prints; an int3 prints its own headline; the cap, the repeat of one fault, a 64-bit guest, an address outside the window, no TEB and
     no registers print nothing; the unset switch reads no memory.
  4. The catalog entry, the lists and the CI step exist; the real signal_arm64_ios.c still compiles for iOS (when a wine tree is there).
Round 624 (agents E1 S17 and D2 S8; Unreal Gold EQ6DMS/X3HYP5, Need for Speed: Underground 2 B1MR64): a second access-violation call in
bus_handler's "BUS->AV: unreadable target" branch (the Mach thread's call reaches only the first 12 events; Unreal Gold's wall came at about
17,400); a write to a readable page takes no report; the report adds the views around the fault and the pointers (virtual_ios.c, the lock
only tried; a busy lock is said), the WoW64 allocations and frees before the fault (a ring of 64 kept only with the switch; compiled and
driven here: Unreal Gold's shape, the wrap, a 64-bit process) and the places of the program's image that hold the fault address; the entry is
never inlined, so bus_handler's own frame on the signal stack stays as it was.
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
header_path = root / 'build/ntdll-unix/ios_x86_fault_diag.h'
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
inc = '#include "ios_x86_fault_diag.h"'
check('the header is included exactly once, after the earlier 32-bit diagnostics and before the Mach exception thread',
      signal_src.count(inc) == 1 and signal_src.index('#include "ios_int3_x86_diag.h"') < signal_src.index(inc)
      and signal_src.index('#include "ios_ill_guest_diag.h"') < signal_src.index(inc)
      < signal_src.index('static void *ios_mach_exception_thread( void *arg )'))
check('one call site for the int3 and two for the access violation (round 624: the signal path\'s too), each behind `if (ios_x86_fault_enabled()`',
      signal_src.count('ios_x86_fault_int3(') == 1 and signal_src.count('ios_x86_fault_av(') == 2 and signal_src.count('if (ios_x86_fault_enabled()') == 3)
i3 = signal_src.index('ios_x86_fault_int3(')
g3 = signal_src.rindex('if (ios_x86_fault_enabled()', 0, i3)
battle = signal_src.index('ios_int3_x86_diag( &bs, i3x_teb, gregs, grip );')
check('the int3 call is inside the [int3-guest] block, after the Battle.net call and before the TEB lookup of the access-violation path',
      battle < g3 < i3 < signal_src.index('/* Look up per-thread TEB and trampoline for the faulting thread */', i3)
      and signal_src[g3:i3].count('{') == 1 and signal_src[g3:i3].count('}') == 0)
av = signal_src.index('ios_x86_fault_av(')
ga = signal_src.rindex('if (ios_x86_fault_enabled()', 0, av)
# round 624 (agent E1 S17): the second access-violation call, in the signal path's "BUS->AV: unreadable target" branch
av2 = signal_src.index('ios_x86_fault_av(', av + 1)
ga2 = signal_src.rindex('if (ios_x86_fault_enabled()', 0, av2)
bus = signal_src[signal_src.index('static void bus_handler( int signal, siginfo_t *siginfo, void *sigcontext )'):]
check('round 624 (E1 S17 a1): the second call is in bus_handler\'s "BUS->AV: unreadable target" branch, after [wow-null32] and before the AV is '
      'delivered, for JIT code with FEX\'s state at x28, the guest rip read fault-safely from [x28 + 0x18]',
      signal_src.index('"BUS->AV: unreadable target addr=%p pc=%p rw=%d rev=ml420\\n"') < signal_src.index('ios_wow_null_diag_bus( &((ucontext_t *)sigcontext)->uc_mcontext->__ss, (uintptr_t)NtCurrentTeb(), (uintptr_t)siginfo->si_addr );')
      < ga2 < av2 < signal_src.index('goto bus_fatal;', av2) and 'ios_x86_fault_av(' in bus
      and 'ios_av_args_pc_is_jit( __darwin_arm_thread_state64_get_pc( *xf_ss ) ) && xf_ss->__x[28] > 0x100000' in signal_src[ga2:av2]
      and 'ios_av_args_read( xf_ss->__x[28] + 0x18, &xf_rip, 8 )' in signal_src[ga2:av2]
      and 'vrec.ExceptionInformation[0] == 1 ? 1 : vrec.ExceptionInformation[0] == 0 ? 0 : -1' in signal_src[av2:av2 + 300])
check('round 624: the report\'s entry is never inlined (bus_handler\'s own frame stays as it was on the 50 KB signal stack)',
      'static __attribute__((noinline)) void ios_x86_fault_av(' in code)
check('round 624 (E1 S17 a1): an access to a readable page is not counted as a report, and the test comes before the cap',
      code.index('if (ios_av_args_read( base + (uint64_t)fault_addr, &probe, 1 )) return;')
      < code.index('if ((num = __sync_add_and_fetch( &reports, 1 )) > IOS_X86F_MAX_REPORTS) return;')
      and code.index('if (!thread_teb) return;') < code.index('if (ios_av_args_read( base + (uint64_t)fault_addr, &probe, 1 )) return;'))
check('round 624 (E1 S17 a2, D2 S8 d2/d3): the views, the allocation ring and the image scan only for an access violation, after the objects',
      code.index('ios_x86f_object( base, "EBP", ebp, mods, nmods );') < code.index('if (fault_addr >= 0)\n    {\n        extern void ios_x86f_alloc_ring_dump( uint32_t fault );')
      < code.index('ios_x86f_scan_image( base, mods, nmods, (uint32_t)fault_addr );') < code.index('"[x86-fault]   end of report #%d\\n"')
      and 'uint32_t page[0x800 / 4]' in code and 'mods[0].size > 0x4000000u' in code)
vsrc = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
dfr = vsrc[vsrc.index('void ios_dump_fault_region( void *addr )\n{'):vsrc.index('static void ios_fault_region_lines( void *addr )\n{')]
check('round 624: ios_dump_fault_region is as before (storm gate, the lock, the lines, unlock); the report\'s variant has no gate and only tries the lock',
      dfr.index('if (!ios_storm_gate( &fr_storm )) return;') < dfr.index('mutex_lock( &virtual_mutex );') < dfr.index('ios_fault_region_lines( addr );')
      < dfr.index('mutex_unlock( &virtual_mutex );')
      and 'int ios_dump_fault_region_report( void *addr )\n{\n    if (pthread_mutex_trylock( &virtual_mutex )) return 0;\n    ios_fault_region_lines( addr );\n    pthread_mutex_unlock( &virtual_mutex );\n    return 1;\n}' in dfr)
note = vsrc[vsrc.index('static void ios_vm_note_alloc( void *base, SIZE_T size, ULONG type, ULONG protect,'):]
note = note[:note.index('\n}\n')]
wow = note[note.index('    if (!log_all && !covers && is_wow64())\n    {'):note.index('    if (b < 0x100000000ull && !viol && !zbits) return;')]
check('round 624 (E1 S17 a3): a WoW64 allocation without the switch returns as before; with it, the ring and a line (the first 400, then every 256th)',
      wow.index('if (!ios_x86_fault_switch()) return;') < wow.index('ios_x86f_ring_note( b, size, type, protect, 0 );')
      and 'if (k <= 400 || !(k % 256))' in wow and wow.rstrip().endswith('return;\n    }') and wow.count('return;') == 2)
free_note = vsrc[vsrc.index('static void ios_vm_note_free( void *base, SIZE_T size, ULONG type )'):]
free_note = free_note[:free_note.index('\n}\n')]
check('round 624 (D2 S8 d2): a WoW64 free goes into the ring only with the switch on', 'if (is_wow64() && ios_x86_fault_switch()) ios_x86f_ring_note( b, size, type, 0, 1 );' in free_note)
check('round 624: the switch reaches virtual_ios.c through one getter of signal_arm64_ios.c (the environment is read in one place)',
      'int ios_x86_fault_switch( void ) { return ios_x86_fault_enabled(); }' in signal_src and 'MADEIRA_X86_FAULT_DUMP' not in vsrc)
check('the access-violation call is inside the [rsp-trunc] block, for EXC_BAD_ACCESS in JIT code only',
      signal_src.rindex('"[rsp-trunc] x28=%p guest_rip=%p', 0, ga) > 0 and ga < av
      and 'req->exception == EXC_BAD_ACCESS && ios_av_args_pc_is_jit( __darwin_arm_thread_state64_get_pc( state ) )' in signal_src[ga:av]
      and signal_src[ga:av].count('{') == 1 and signal_src[ga:av].count('}') == 0
      and signal_src[ga:av + 200].count('cs[0]') >= 1)
forbidden = ('thread_set_state', 'thread_suspend', 'thread_terminate', 'vm_protect', 'mprotect(', 'mach_vm_write', 'vm_write(',
             'mmap(', 'free(', 'malloc(', 'abort(', 'exit(', 'sigaction', 'fopen(', 'open(', 'mach_vm_read_overwrite', 'raise(', 'kill(')
check('no write, protect, stop, exit, file or allocation call, and no direct memory read (the fault-safe readers are the only ones)',
      not [f for f in forbidden if f in code], str([f for f in forbidden if f in code]))
raw_deref = re.findall(r'\*\s*\(\s*(?:const\s+)?(?:volatile\s+)?(?:u?int\d+_t|void|char|unsigned\w*|uintptr_t)\s*\*\s*\)', code)
check('no raw dereference of a guest address', not raw_deref, str(raw_deref))
check('only for a 32-bit guest (rip below 4 GB, a 4 GB aligned window base in x19) and with a TEB (x18 when it lies in the window, else the lookup)',
      'if (!gregs || eip64 < 0x10000 || eip64 > 0xffffffffULL) return;' in code and 'if (!thread_teb) return;' in code
      and code.count('(base & 0xffffffffULL) == 0 && base >= 0x100000000ULL') == 2
      and 'st->__x[18] >= base && st->__x[18] - base < 0x100000000ULL) thread_teb = (uintptr_t)st->__x[18];' in code)
check('capped at 3 reports per session; the repeat of the fault just reported is not a report',
      'IOS_X86F_MAX_REPORTS  3' in header and '> IOS_X86F_MAX_REPORTS) return;' in code and 'static volatile int reports;' in code
      and 'if (block_rip == last_rip && host_addr == last_addr) return;' in code)
check('the switch is MADEIRA_X86_FAULT_DUMP, read once, and only the exact value 1 turns it on',
      code.count('getenv(') == 1 and 'getenv( "MADEIRA_X86_FAULT_DUMP" )' in code and 'enabled = e && e[0] == \'1\' && !e[1];' in code
      and 'static int enabled = -1;' in code)
check('round 624 (A-S12): the access-violation call asks for the exact EIP from FEX\'s block table; the int3 path and the 6-argument wrapper do not',
      'ios_x86_fault_report_ex( st, thread_teb, g, block_rip, guest_addr, write, 1 );' in code and 'ios_native_rip_from_hostpc' in code
      and 'ios_x86_fault_report_ex( st, thread_teb, gregs, eip64, fault_addr, write, 0 );' in code
      and code.index('if ((num = __sync_add_and_fetch( &reports, 1 )) > IOS_X86F_MAX_REPORTS) return;') < code.index('if (resolve && fault_addr >= 0)'))
check('the access-violation call hands over the x32 register mapping (EAX x4, ECX x7, EDX x5, EBX x6, ESP x8, EBP x9, ESI x10, EDI x11)',
      'g[0] = st->__x[4]; g[1] = st->__x[7]; g[2] = st->__x[5]; g[3] = st->__x[6];' in code
      and 'g[4] = st->__x[8]; g[5] = st->__x[9]; g[6] = st->__x[10]; g[7] = st->__x[11];' in code)

# ---------------------------------------------------------------- 2. nothing changes when the switch is unset
stripped = signal_src
stripped = re.sub(r'/\* ajan21-loglar \(build 598\): \[x86-fault\].*?#include "ios_x86_fault_diag\.h"\n', '', stripped, flags=re.S)
stripped = re.sub(r'\n\n *\/\* ajan21-loglar \(build 598\): the same evidence for a game\'s own int3.*?ios_x86_fault_int3\( &bs, xf_teb, gregs, grip \);\n *\}\n', '\n', stripped, flags=re.S)
stripped = re.sub(r'\n\n *\/\* ajan21-loglar \(build 598\): the opt-in report for a 32-bit guest\'s access violation.*?ios_x86_fault_av\( &state, xf_teb, cs\[0\], \(uint64_t\)fault_addr, -1 \);\n *\}\n', '\n', stripped, flags=re.S)
stripped = re.sub(r'/\* round 624 \(agents E1 S17, D2 S8\): virtual_ios\.c keeps.*?\nint ios_x86_fault_switch\( void \) \{ return ios_x86_fault_enabled\(\); \}\n', '', stripped, flags=re.S)
stripped = re.sub(r'\n *\/\* madeira-bcd \(round 624, agent E1 S17; Unreal Gold EQ6DMS, X3HYP5\): the opt-in \[x86-fault\] report on this path too\..*?vrec\.ExceptionInformation\[0\] == 0 \? 0 : -1 \);\n *\}\n', '\n', stripped, flags=re.S)
leftovers = re.findall(r'[^\n]*(?:x86_fault|X86_FAULT|x86-fault|xf_teb|xf_ss|xf_rip)[^\n]*', stripped)
check('taking out the include, the getter and the three guarded blocks leaves no reference to the report in signal_arm64_ios.c', not leftovers, str(leftovers[:4]))


def tracked(rootdir):
    try:
        out = subprocess.run(['git', '-C', str(rootdir), 'ls-files'], capture_output=True, text=True, check=True).stdout.split('\n')
        return [Path(rootdir) / f for f in out if f]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in Path(rootdir).rglob('*') if p.is_file() and '.git' not in p.parts]


allowed = {
    'build/ntdll-unix/ios_x86_fault_diag.h', 'build/ntdll-unix/signal_arm64_ios.c', 'tests/host/check-x86-fault-diag.py',
    'build/tools/gen-config-catalog.py', 'app/Madeira/ConfigCatalog.generated.swift', '.github/workflows/build-ipa.yml',
    'recommended/recommendations.json', 'recommended/recommendations.json.sig', 'app/Madeira/GameRecommendationsList.swift',
    'tests/host/check-game-recommendations.py',
    # the int3 report's check names this switch to tell the two apart (2026-10-24)
    'tests/host/check-int3-x86-diag.py',
    # round 624: auto-tune's lowest-priority rule x86-null-fault-diag offers the switch after a 32-bit NULL-page fault
    'build/tools/autotune_rules.py', 'app/Madeira/AutoTuneRules.generated.swift',
    # round 624 (agent A S4, 7NKYWT): the EA app's sessions carry it for their installer's custom-action hosts; its launcher check pins the line
    'app/Madeira/BCDLaunchers.swift', 'tests/host/check-bcd-launchers.py',
}
# The EA app's entry is the one launcher line: exactly one source line in BCDLaunchers.swift, inside the eaApp entry, not in Battle.net's.
launch_src = (root / 'app/Madeira/BCDLaunchers.swift').read_text()
ea_entry = launch_src[launch_src.index('static let eaApp'):]
bn_entry = launch_src[launch_src.index('static let battlenet'):launch_src.index('static let eaApp')]
check('the EA app entry (only) carries env.MADEIRA_X86_FAULT_DUMP = 1 in its sessionConfig (round 624, 7NKYWT)',
      launch_src.count('"env.MADEIRA_X86_FAULT_DUMP = 1"') == 1 and '"env.MADEIRA_X86_FAULT_DUMP = 1"' in ea_entry
      and 'MADEIRA_X86_FAULT_DUMP' not in bn_entry)
users = []
for f in tracked(root):
    rel = str(f.relative_to(root))
    if rel in allowed or not f.is_file() or f.suffix in ('.png', '.jpg', '.ipa', '.dll', '.exe', '.a', '.dylib', '.zip', '.xz', '.gz', '.bz2', '.pdf', '.icns'):
        continue
    if rel.startswith('docs/') or rel.startswith('tests/fixtures/'):
        continue
    try:
        if 'MADEIRA_X86_FAULT_DUMP' in f.read_text(errors='ignore'):
            users.append(rel)
    except OSError:
        pass
check('no other source, rule or test mentions the switch (docs and fixtures aside)', not users, str(users))
recs = json.loads((root / 'recommended/recommendations.json').read_text())
carriers = []
for r in recs['rules']:
    for ln in r['config']:
        text = ln['line'] if isinstance(ln, dict) else ln
        if 'MADEIRA_X86_FAULT_DUMP' in text:
            carriers.append((r['id'], text, ln.get('minBuild') if isinstance(ln, dict) else None))
# ajan22 (same round) added Chuchel and Botanicula (32-bit Adobe AIR page faults) with the same line.
# Round 624: four diagnostic lists more (Mafia II 052WJE and Unreal Gold EQ6DMS from build 624, Overlord II QJY0A4 and Ubisoft Connect P4KC3V
# from build 598), each to make the next log name its fault.
# Round 624 (agent E1 S7, TS8ME1): Doki Doki Literature Club's diagnostic list (Ren'Py's exit(-1)) from build 598.
check('the bundled lists that carry the switch are exactly Most Wanted, Underground 2, Chuchel, Botanicula and (round 624) Mafia II, osu!, Overlord II, '
      'Ubisoft Connect, Unreal Gold and Doki Doki Literature Club, once each, as env.MADEIRA_X86_FAULT_DUMP = 1 with minBuild 598 (Mafia II and Unreal Gold 624)',
      sorted(carriers) == [('botanicula', 'env.MADEIRA_X86_FAULT_DUMP = 1', 598), ('chuchel', 'env.MADEIRA_X86_FAULT_DUMP = 1', 598),
                           ('doki-doki-literature-club', 'env.MADEIRA_X86_FAULT_DUMP = 1', 598),
                           ('mafia-2', 'env.MADEIRA_X86_FAULT_DUMP = 1', 624),
                           ('nfs-most-wanted', 'env.MADEIRA_X86_FAULT_DUMP = 1', 598), ('nfs-underground-2', 'env.MADEIRA_X86_FAULT_DUMP = 1', 598),
                           ('osu', 'env.MADEIRA_X86_FAULT_DUMP = 1', 598), ('overlord-ii', 'env.MADEIRA_X86_FAULT_DUMP = 1', 598), ('ubisoft-connect', 'env.MADEIRA_X86_FAULT_DUMP = 1', 598),
                           ('unreal-gold', 'env.MADEIRA_X86_FAULT_DUMP = 1', 624)], str(carriers))

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
/* round 624 (A-S12): FEX's block table, faked: the exact EIP for the block at [x28] (0 = not resolved) */
static uint64_t fake_exact;
uint64_t ios_native_rip_from_hostpc( uint64_t bb, uint64_t pc, const char **why )
{
    (void)pc; if (why) *why = fake_exact ? "ok" : "hostPC outside block";
    return bb ? fake_exact : 0;
}
static uint64_t fake_cpustate[8];
/* round 624: virtual_ios.c's views (the memory lock only tried) and allocation ring, faked */
static int fake_lock_busy;
int ios_dump_fault_region_report( void *addr )
{
    if (fake_lock_busy) return 0;
    fprintf( stderr, "[fault-rgn] (fake) addr=%p\n", addr );
    return 1;
}
void ios_x86f_alloc_ring_dump( uint32_t fault ) { fprintf( stderr, "[x86-fault]   WoW64 allocations and frees before the fault (fake) 0x%08x\n", fault ); }
#define mach_vm_read_overwrite counting_read
'''

# the window of the 592 log of Most Wanted: speed.exe 0x400000 (block at 0x81ab75), ntdll 0xfff40000, TEB32 0x302000, PEB32 0x311000
MAIN = r'''
#define WIN 0x5000000000ULL
#define TEB64_G 0x300000u
#define TEB32_G 0x302000u
#define PEB32_G 0x311000u
#define LDR_G   0x350000u
#define EXE     0x400000u
#define EXE_SIZE 0xa00000u
#define BLOCK   0x81ab75u
#define INT3_AT 0x4405a9u

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

/* a PE32 header page at guest address base: two sections, no debug directory (the original executable has none) */
static void pe_header(uint32_t base, uint32_t size, uint32_t stamp)
{
    uint8_t *h = (uint8_t *)(WIN + base);
    uint32_t nt = 0x80, opt = nt + 24;
    h[0] = 'M'; h[1] = 'Z'; w32((uint64_t)h + 0x3c, nt);
    memcpy(h + nt, "PE\0\0", 4);
    w16((uint64_t)h + nt + 4, 0x14c); w16((uint64_t)h + nt + 6, 2); w32((uint64_t)h + nt + 8, stamp);
    w16((uint64_t)h + nt + 20, 0xe0); w16((uint64_t)h + nt + 22, 0x0102);
    w16((uint64_t)h + opt, 0x10b);
    w32((uint64_t)h + opt + 16, 0x3a0000);                  /* AddressOfEntryPoint */
    w32((uint64_t)h + opt + 56, size);                      /* SizeOfImage */
    w32((uint64_t)h + opt + 64, 0x5a5a5a);                  /* CheckSum */
    uint8_t *s = h + opt + 0xe0;
    memcpy(s, ".text\0\0\0", 8); w32((uint64_t)s + 8, 0x6a0000); w32((uint64_t)s + 12, 0x1000); w32((uint64_t)s + 16, 0x6a0000);
    s += 40;
    memcpy(s, ".data\0\0\0", 8); w32((uint64_t)s + 8, 0x300000); w32((uint64_t)s + 12, 0x6a1000); w32((uint64_t)s + 16, 0x200000);
}

struct world { fake_arm_thread_state64_t st; uint64_t teb64; uint64_t gregs[8]; };

static struct world make(void)
{
    static int built;
    struct world w;
    memset(&w, 0, sizeof(w));
    if (!built)
    {
        built = 1;
        alloc_at(WIN + 0x300000, 0x20000);              /* TEB64 page(s), TEB32 at +0x2000, PEB32 at 0x311000 */
        alloc_at(WIN + 0x350000, 0x10000);              /* PEB_LDR_DATA32, entries, names */
        alloc_at(WIN + 0x400000, 0x4000);               /* speed.exe's header page */
        alloc_at(WIN + 0x81a000, 0x4000);               /* the code page of the block (0x81ab75) */
        alloc_at(WIN + 0x430000, 0x4000);               /* the code at the return address 0x433210 */
        alloc_at(WIN + 0x440000, 0x4000);               /* the int3 page (0x4405a9) */
        alloc_at(WIN + 0x1270000, 0x20000);             /* the stack, ESP 0x127fddc, EBP 0x127fdf4 */
        alloc_at(WIN + 0xa6a0000, 0x4000);              /* the heap objects at EAX 0xa6a0110 and EDI 0xa6a00c0 */
        w32(WIN + TEB64_G + 0x40, 0x2000);              /* the fake TEB64's WowTebOffset */
        w32(WIN + TEB32_G + 0x18, TEB32_G);             /* NT_TIB32.Self */
        w32(WIN + TEB32_G + 0x30, PEB32_G);             /* Peb */
        w32(WIN + PEB32_G + 0x0c, LDR_G);               /* LdrData */
        w32(WIN + PEB32_G + 0x44, 0x00000007);          /* TlsBitmapBits: 0, 1, 2 */
        w32(WIN + TEB32_G + 0x04, 0x1280000);           /* StackBase */
        w32(WIN + TEB32_G + 0x08, 0x1200000);           /* StackLimit */
        w32(WIN + TEB32_G + 0xe0c, 0x1200000);          /* DeallocationStack */
        w32(WIN + TEB32_G + 0x34, 0);                   /* LastErrorValue */
        w32(WIN + TEB32_G + 0xbf4, 0xc0000034);         /* LastStatusValue: STATUS_OBJECT_NAME_NOT_FOUND */
        w32(WIN + LDR_G + 0x0c, LDR_G + 0x100);
        w32(WIN + LDR_G + 0x10, LDR_G + 0x200);
        ldr_entry(LDR_G + 0x100, LDR_G + 0x200, EXE, EXE_SIZE, "speed.exe", LDR_G + 0x1000);
        ldr_entry(LDR_G + 0x200, LDR_G + 0x0c, 0xfff40000, 0xaa000, "ntdll.dll", LDR_G + 0x1100);
        pe_header(EXE, EXE_SIZE, 0x4382e1d3);
        w32(WIN + EXE + 0x200, 0xb514ab6a);             /* round 624 (D2 S8 d3): the program holds the fault address there */
        /* the code of the block: a recognisable ramp, the faulting `mov eax,[ecx]` (8b 01) 5 bytes into the block at 0x81ab7a */
        for (uint32_t a = BLOCK - 0x100; a < BLOCK + 0xa0; a++) *(uint8_t *)(WIN + a) = (uint8_t)(a - (BLOCK - 0x100));
        { const uint8_t ins[8] = { 0x33, 0xf6, 0x85, 0xd2, 0x0f, 0x8b, 0x01, 0x90 }; memcpy((void *)(WIN + BLOCK), ins, sizeof(ins)); }
        /* the int3 page: a cc at 0x4405a9 with a ramp around it */
        for (uint32_t a = INT3_AT - 0x100; a < INT3_AT + 0x40; a++) *(uint8_t *)(WIN + a) = (uint8_t)(a - (INT3_AT - 0x100));
        *(uint8_t *)(WIN + INT3_AT) = 0xcc;
        /* the stack: ESP 0x127fddc, the frame chain 0x127fdf4 -> 0x127fe3c -> 0 */
        w32(WIN + 0x127fddc, 0x0a6a00c0);
        w32(WIN + 0x127fde0, 0x00817770);
        w32(WIN + 0x127fdf4, 0x127fe3c);                /* saved ebp */
        w32(WIN + 0x127fdf8, 0x00433210);               /* return address into speed.exe (0x33210) */
        w32(WIN + 0x127fe3c, 0);
        w32(WIN + 0x127fe40, 0xfff8e440 + 0x30);        /* into ntdll */
        /* the objects: EDI's entry (0x50 bytes: a vtable-like word into speed.exe, then fill patterns) and EAX's, the next entry */
        w32(WIN + 0xa6a00c0, 0x0069c4e0);               /* into speed.exe */
        for (uint32_t a = 0xa6a00c4; a < 0xa6a0160; a += 4) w32(WIN + a, 0xaaaaaaaa);
        w32(WIN + 0xa6a0110, 0x0069c4e0);
    }
    ios_jit_rx_base_global = NULL; ios_jit_pool_size_global = 0;
    /* round 624: FEX's CPU state at x28, its first word the faulting block's host address; a call (E8 rel32) ending at 0x433210 */
    fake_cpustate[0] = 0x16a937260; w.st.__x[28] = (uint64_t)(uintptr_t)fake_cpustate;
    *(uint8_t *)(WIN + 0x43320b) = 0xe8;
    w.teb64 = WIN + TEB64_G;
    w.st.__x[19] = WIN;
    w.st.__x[18] = w.teb64;
    /* the host registers of the 592 log: x4 EAX, x7 ECX, x5 EDX, x6 EBX, x8 ESP, x9 EBP, x10 ESI, x11 EDI */
    w.st.__x[4] = 0xa6a0110; w.st.__x[7] = 0xb514ab6a; w.st.__x[5] = 0; w.st.__x[6] = 0;
    w.st.__x[8] = 0x127fddc; w.st.__x[9] = 0x127fdf4; w.st.__x[10] = 0; w.st.__x[11] = 0xa6a00c0;
    /* FEX order RAX RCX RDX RBX RSP RBP RSI RDI, for the int3 call */
    w.gregs[0] = 0; w.gregs[1] = 0; w.gregs[2] = 0x82804c; w.gregs[3] = 6;
    w.gregs[4] = 0x127fddc; w.gregs[5] = 0x127fdf4; w.gregs[6] = 0xaa; w.gregs[7] = 0xa6a00c0;
    return w;
}

static void guarded_av(struct world *w, uint64_t block, uint64_t host_addr)
{
    /* the call site of signal_arm64_ios.c: nothing but the guard in front of the call */
    if (ios_x86_fault_enabled()) ios_x86_fault_av(&w->st, w->teb64, block, host_addr, -1);
}
static void guarded_int3(struct world *w)
{
    if (ios_x86_fault_enabled()) ios_x86_fault_int3(&w->st, w->teb64, w->gregs, INT3_AT + 1);
}

int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    struct world w;

    if (!strcmp(s, "enabled")) { printf("enabled=%d\n", ios_x86_fault_enabled()); return 0; }
    w = make();
    if (!strcmp(s, "av")) { guarded_av(&w, BLOCK, WIN + 0xb514ab6a); printf("reads=%lu\n", g_reads); return 0; }
    if (!strcmp(s, "off")) { guarded_av(&w, BLOCK, WIN + 0xb514ab6a); guarded_int3(&w); printf("enabled=%d reads=%lu\n", ios_x86_fault_enabled(), g_reads); return 0; }
    if (!strcmp(s, "int3")) { guarded_int3(&w); return 0; }
    if (!strcmp(s, "repeat")) {
        guarded_av(&w, BLOCK, WIN + 0xb514ab6a); guarded_av(&w, BLOCK, WIN + 0xb514ab6a);          /* the redelivery of the same fault */
        guarded_av(&w, BLOCK, WIN + 0xb449ab6a); guarded_av(&w, BLOCK + 8, WIN + 0xb449ab6a);       /* two other faults */
        guarded_av(&w, BLOCK, WIN + 0xb471ab6a); guarded_av(&w, BLOCK, WIN + 0xb472ab6a);          /* over the cap of 3 */
        return 0;
    }
    if (!strcmp(s, "benign")) {
        ios_x86_fault_av(&w.st, w.teb64, BLOCK, 0x7000000000ULL + 0x1000, -1);                  /* an address outside this window */
        ios_x86_fault_av(&w.st, w.teb64, 0x140001000ULL, WIN + 0xb514ab6a, -1);                   /* a 64-bit rip */
        w.st.__x[19] = 0x5000000010ULL; ios_x86_fault_av(&w.st, w.teb64, BLOCK, WIN + 0x1000, -1);   /* x19 is no window base */
        w.st.__x[19] = 0; ios_x86_fault_av(&w.st, w.teb64, BLOCK, 0x1000, -1);
        w.st.__x[19] = WIN; w.st.__x[18] = 0;
        ios_x86_fault_av(&w.st, 0, BLOCK, WIN + 0xb514ab6a, -1);                                  /* no TEB (x18 and the lookup both empty) */
        w.st.__x[18] = w.teb64;
        ios_x86_fault_report(&w.st, w.teb64, NULL, BLOCK, 0x1000, -1);                           /* no registers */
        ios_x86_fault_int3(&w.st, w.teb64, w.gregs, 1);                                          /* no address */
        printf("benign-done reads=%lu\n", g_reads);
        return 0;
    }
    if (!strcmp(s, "exact")) { fake_exact = BLOCK + 5; guarded_av(&w, BLOCK, WIN + 0xb514ab6a); guarded_av(&w, BLOCK, WIN + 0xb514ab6a); return 0; }
    if (!strcmp(s, "stk")) { fake_exact = 0; w.st.__x[9] = 0; guarded_av(&w, BLOCK, WIN + 0xb514ab6a); return 0; }
    if (!strcmp(s, "noteb")) { w32(WIN + TEB32_G + 0x18, 0x999000); guarded_av(&w, BLOCK, WIN + 0xb514ab6a); w32(WIN + TEB32_G + 0x18, TEB32_G); return 0; }
    if (!strcmp(s, "readable")) {
        /* round 624 (E1 S17 a1): a write to a readable page (speed.exe's header) is no report and takes none of the three */
        guarded_av(&w, BLOCK, WIN + EXE + 0x10); guarded_av(&w, BLOCK + 1, WIN + EXE + 0x20);
        guarded_av(&w, BLOCK, WIN + 0xb514ab6a);
        return 0;
    }
    if (!strcmp(s, "busy")) { fake_lock_busy = 1; guarded_av(&w, BLOCK, WIN + 0xb514ab6a); return 0; }
    if (!strcmp(s, "nostack")) { w.st.__x[8] = 0x6000000; w.st.__x[9] = 0x6000010; guarded_av(&w, BLOCK, WIN + 0xb514ab6a); return 0; }
    return 0;
}
'''

if sys.platform != 'darwin' or not shutil.which(cc):
    print('skip the compiled runs: macOS and a C compiler are needed')
else:
    av_header = (root / 'build/ntdll-unix/ios_av_args_diag.h').read_text()
    null_header = (root / 'build/ntdll-unix/ios_wow_null_diag.h').read_text()
    i3_header = (root / 'build/ntdll-unix/ios_int3_x86_diag.h').read_text()
    with tempfile.TemporaryDirectory(prefix='x86fault-') as d:
        d = Path(d)
        (d / 'h.c').write_text(PRELUDE + '\n' + av_header + '\n' + null_header + '\n' + i3_header + '\n' + header + '\n' + MAIN)
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function', str(d / 'h.c'), '-o', str(d / 'h')],
                           capture_output=True, text=True)
        check('the production header compiles (host cc, -Wall -Wextra -Werror)', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            def run(scn, **env):
                e = {k: v for k, v in os.environ.items() if k != 'MADEIRA_X86_FAULT_DUMP'}
                e.update(env)
                return subprocess.run([str(d / 'h'), scn], capture_output=True, text=True, env=e)

            def lines(r):
                return [l for l in r.stderr.splitlines() if l.startswith('[x86-fault]')]

            vals = {}
            for v in (None, '', '0', '11', 'true', ' 1', '1 ', 'yes', '2', '1'):
                r = run('enabled') if v is None else run('enabled', MADEIRA_X86_FAULT_DUMP=v)
                vals[v] = r.stdout.strip()
            check('only the exact value 1 turns the report on (unset, "", 0, 11, true, " 1", "1 ", yes, 2: off)',
                  vals[None] == 'enabled=0' and vals[''] == 'enabled=0' and all(vals[v] == 'enabled=0' for v in ('0', '11', 'true', ' 1', '1 ', 'yes', '2'))
                  and vals['1'] == 'enabled=1', str(vals))
            r = run('off')
            check('with the switch unset the access violation and the int3 print nothing and read no guest memory at all (with a window ready to be read)',
                  r.stderr == '' and r.stdout.strip() == 'enabled=0 reads=0', r.stderr + r.stdout)
            r = run('off', MADEIRA_X86_FAULT_DUMP='0')
            check('...nor with the value 0', r.stderr == '' and r.stdout.strip() == 'enabled=0 reads=0', r.stderr + r.stdout)

            r = run('av', MADEIRA_X86_FAULT_DUMP='1')
            ls = lines(r)
            allv = r.stderr.splitlines()
            if os.environ.get('X86F_DEBUG'):
                print(r.stderr)
            check('the headline: the fault address, the block and its module+RVA, the registers of the 592 log',
                  ls and ls[0].startswith('[x86-fault] #1 a 32-bit guest faulted at guest 0xb514ab6a in the block that starts at guest 0x0081ab75 (speed.exe+0x41ab75), '
                                          'window 0x5000000000, 2 modules in the PEB32 list;')
                  and 'EAX=0xa6a0110 ECX=0xb514ab6a EDX=0x0 EBX=0x0 ESI=0x0 EDI=0xa6a00c0 EBP=0x127fdf4 ESP=0x127fddc' in ls[0], r.stderr)
            check('the fault address minus each register shows the pattern: -EDI=0xaaaaaaaa, and -ECX=0x0',
                  any('fault address minus each register (mod 2^32):' in l and '-EDI=0xaaaaaaaa' in l and '-ECX=0x0' in l and '-EAX=0xaaaaaa5a' in l for l in ls), r.stderr)
            check('no register points into a module', any('registers that point into a module: none' in l for l in ls), r.stderr)
            check('the thread facts: stack range and depth, last error and status, the TLS bitmap (3 of 64 taken)',
                  any('thread TEB32=0x302000 PEB32=0x311000 StackBase=0x1280000 StackLimit=0x1200000 DeallocationStack=0x1200000 (ESP is 0x224 below the base) '
                      'LastErrorValue=0x0 LastStatusValue=0xc0000034 TlsBitmapBits=0x0000000000000007 (3 of 64 taken) TlsExpansionSlots=0x0' in l for l in ls), r.stderr)
            check('the module list: the program and ntdll.dll',
                  any('modules (name=base/size): speed.exe=0x400000/0xa00000 ntdll.dll=0xfff40000/0xaa000' in l for l in ls), r.stderr)
            check('the PE identity of the program: stamp, SizeOfImage, CheckSum, the sections (the helper\'s own prefix)',
                  any(l.startswith('[int3-x86]   image speed.exe base=0x400000 ldr-size=0xa00000: machine=0x14c magic=0x10b TimeDateStamp=0x4382e1d3 SizeOfImage=0xa00000 CheckSum=0x5a5a5a ')
                      for l in allv)
                  and any(l.startswith('[int3-x86]   image speed.exe sections: .text@0x1000+0x6a0000(raw 0x6a0000) .data@0x6a1000+0x300000(raw 0x200000)') for l in allv), r.stderr)
            check('96 words of stack in 12 rows from ESP, the first being the saved EDI and the return into the program',
                  len([l for l in ls if ' stack ESP+0x' in l]) == 12 and any('stack ESP+0x000: 0a6a00c0 00817770 ' in l for l in ls), r.stderr)
            check('the stack words that point into a module: +0x004 = speed.exe+0x417770, +0x01c = speed.exe+0x33210 (the return address)',
                  any('stack words that point into a module (' in l and '+0x004=00817770(speed.exe+0x417770)' in l and '+0x01c=00433210(speed.exe+0x33210)' in l for l in ls), r.stderr)
            check('the EBP chain: the frame, its return address with module+RVA, then it ends at the NULL saved ebp',
                  any('frame #0 ebp=0x127fdf4 saved-ebp=0x127fe3c return=0x00433210 (speed.exe+0x33210)' in l for l in ls)
                  and any('frame #1 ebp=0x127fe3c saved-ebp=0x0 return=0xfff8e470 (ntdll.dll+0x4e470)' in l for l in ls)
                  and not any('frame #2' in l for l in ls), r.stderr)
            check('the code of the block: 256 bytes before and 160 after, rows of 32, the constructed instruction bytes at +0 and the ramp before',
                  any(l.startswith('[x86-fault]   code block: 256 bytes before and 160 after guest 0x0081ab75 (speed.exe+0x41ab75) (416 of 416 readable)') for l in allv)
                  and any(l.startswith('[x86-fault]   code block -256: 00 01 02 03 04 05 06 07') for l in allv)
                  and any(l.startswith('[x86-fault]   code block +0: 33 f6 85 d2 0f 8b 01 90 ') for l in allv), r.stderr)
            check('the code before the first return address (64 before, 8 after)',
                  any(l.startswith('[x86-fault]   code ret#0: 64 bytes before and 8 after guest 0x00433210 (speed.exe+0x33210) (72 of 72 readable)') for l in allv), r.stderr)
            check('the objects: EAX and EDI (96 bytes, three rows each, the program\'s address with module+RVA, the fill pattern); ECX, which is unreadable, is left out',
                  any('[EDI=0xa6a00c0]+0x00: 69c4e0(speed.exe+0x29c4e0) aaaaaaaa aaaaaaaa aaaaaaaa' in l for l in ls)
                  and len([l for l in ls if '[EDI=0xa6a00c0]+0x' in l]) == 3 and any('[EAX=0xa6a0110]+0x00: 69c4e0(speed.exe+0x29c4e0)' in l for l in ls)
                  and any('[EBP=0x127fdf4]+0x00:' in l for l in ls) and not any('[ECX=' in l for l in ls) and not any('[EBX=' in l or '[ESI=' in l for l in ls), r.stderr)
            check('the report ends with a marker and every line of this report carries one of the two prefixes (round 624: and the views\' [fault-rgn])',
                  ls and ls[-1].endswith('end of report #1')
                  and all(l.startswith(('[x86-fault]', '[int3-x86]', '[fault-rgn]')) for l in allv if l and not l.startswith('reads=')), r.stderr)
            check('round 624 (E1 S17 a2): the views around the fault, EBP and EDI (one place a page: ECX is the fault\'s page; ESI, EBX, EDX are 0)',
                  [l for l in ls if l.startswith('[x86-fault]   views around ')] == ['[x86-fault]   views around fault=0xb514ab6a:',
                                                                                 '[x86-fault]   views around EBP=0x0127fdf4:',
                                                                                 '[x86-fault]   views around EDI=0x0a6a00c0:']
                  and len([l for l in allv if l.startswith('[fault-rgn] (fake) addr=0x50b514ab6a')]) == 1, r.stderr)
            check('round 624 (D2 S8 d2): the allocations before the fault, after the views', any(l.startswith('[x86-fault]   WoW64 allocations and frees before the fault (fake) 0xb514ab6a') for l in ls), r.stderr)
            check('round 624 (D2 S8 d3): the image holds the fault address at speed.exe+0x200, the one place',
                  any(l.startswith('[x86-fault]   the image speed.exe holds the fault address 0xb514ab6a as a dword at 1 place(s): speed.exe+0x200 (') for l in ls), r.stderr)
            check('the number of lines is bounded (a report is under 100 lines)', 25 < len(allv) < 100, str(len(allv)))
            check('round 624 (A-S12): an unresolved exact EIP keeps the old headline and adds one note that the block is FEX\'s State.rip',
                  len([l for l in ls if l.startswith("[x86-fault]   note: the block above is FEX's State.rip (0x0081ab75)")]) == 1
                  and any('note:' in l and '(hostPC outside block)' in l for l in ls), r.stderr)
            check('it did read guest memory (the switch is on)', int(re.search(r'reads=(\d+)', r.stdout).group(1)) > 20, r.stdout)

            r = run('exact', MADEIRA_X86_FAULT_DUMP='1')
            ls = lines(r)
            allv = r.stderr.splitlines()
            check('round 624 (A-S12): the exact EIP from FEX\'s block table names the faulting instruction, State.rip is said to be the last block entry',
                  ls and ls[0].startswith('[x86-fault] #1 a 32-bit guest faulted at guest 0xb514ab6a at EIP 0x0081ab7a (speed.exe+0x41ab7a) '
                                          '[exact: the block table of FEX for host pc 0x')
                  and 'State.rip=0x0081ab75 is only the last block entry], window 0x5000000000, 2 modules in the PEB32 list;' in ls[0]
                  and not any('note:' in l for l in ls), r.stderr)
            check('...the code window is the instruction\'s ("eip"), its bytes at +0 are the faulting mov eax,[ecx], and the PE identity is the program\'s',
                  any(l.startswith('[x86-fault]   code eip: 256 bytes before and 160 after guest 0x0081ab7a (speed.exe+0x41ab7a)') for l in allv)
                  and any(l.startswith('[x86-fault]   code eip +0: 8b 01 90 08 09 0a') for l in allv)
                  and any(l.startswith('[int3-x86]   image speed.exe base=0x400000') for l in allv), r.stderr)
            check('...and the repeat of the same fault is still no second report (the dedupe keys on State.rip)',
                  len([l for l in ls if ' a 32-bit guest faulted ' in l]) == 1, r.stderr)
            r = run('stk', MADEIRA_X86_FAULT_DUMP='1')
            ls = lines(r)
            allv = r.stderr.splitlines()
            check('round 624 (A-S12): with no EBP chain the code before the call-preceded return addresses on the stack is printed ("stk#N"), '
                  'a data word into the program is not',
                  not any('frame #' in l for l in ls)
                  and any(l.startswith('[x86-fault]   code stk#0: 64 bytes before and 8 after guest 0x00433210 (speed.exe+0x33210)') for l in allv)
                  and not any('code stk#' in l and '0x00817770' in l for l in allv)
                  and len([l for l in allv if '   code stk#' in l and 'bytes before' in l]) <= 8, r.stderr)

            r = run('int3', MADEIRA_X86_FAULT_DUMP='1')
            ls = lines(r)
            allv = r.stderr.splitlines()
            check('an int3: its own headline (not a fault), the registers of the Underground 2 log, and the code window labelled int3 with the cc at +0',
                  ls and ls[0].startswith('[x86-fault] #1 a 32-bit guest executed int3 at guest 0x004405a9 (speed.exe+0x405a9), window 0x5000000000, 2 modules')
                  and 'EAX=0x0 ECX=0x0 EDX=0x82804c EBX=0x6 ESI=0xaa EDI=0xa6a00c0 EBP=0x127fdf4 ESP=0x127fddc' in ls[0]
                  and not any('fault address minus' in l for l in ls)
                  and any(l.startswith('[x86-fault]   code int3 +0: cc ') for l in allv), r.stderr)
            check('round 624: an int3 has no views, no allocations and no image scan (they are for an access)',
                  not any('views around' in l or 'WoW64 allocations' in l or 'holds the fault address' in l for l in ls) and not any(l.startswith('[fault-rgn]') for l in allv), r.stderr)
            r = run('readable', MADEIRA_X86_FAULT_DUMP='1')
            heads = [l for l in lines(r) if ' a 32-bit guest faulted ' in l]
            check('round 624 (E1 S17 a1): two writes to a readable page take no report; the wild pointer after them is report #1',
                  len(heads) == 1 and heads[0].startswith('[x86-fault] #1 a 32-bit guest faulted at guest 0xb514ab6a'), r.stderr)
            r = run('busy', MADEIRA_X86_FAULT_DUMP='1')
            check('round 624 (E1 S17 a2): a busy memory lock is said, the report goes on', len([l for l in lines(r) if 'the memory lock was busy: not read' in l]) == 3
                  and any(l.endswith('end of report #1') for l in lines(r)), r.stderr)

            r = run('repeat', MADEIRA_X86_FAULT_DUMP='1')
            heads = [l for l in lines(r) if ' a 32-bit guest faulted ' in l]
            check('seven calls: the redelivery of one fault is not a report, three reports at most, then silence',
                  len(heads) == 3 and '0xb514ab6a' in heads[0] and '0xb449ab6a' in heads[1] and '0xb449ab6a' in heads[2] and '0x0081ab7d' in heads[2], r.stderr)
            r = run('benign', MADEIRA_X86_FAULT_DUMP='1')
            check('an address outside the window, a 64-bit rip, a thread state without a 4 GB window base, no TEB, no registers and no address print nothing and read nothing',
                  r.stderr == '' and r.stdout.strip() == 'benign-done reads=0', r.stderr + r.stdout)
            r = run('noteb', MADEIRA_X86_FAULT_DUMP='1')
            ls = lines(r)
            check('a TEB32 whose Self does not match: no modules, but the registers, the stack and the code still print',
                  ls and '0 modules' in ls[0] and 'no module' in ls[0] and any(' stack ESP+0x000:' in l for l in ls)
                  and any('code block +0: 33 f6 85 d2' in l for l in r.stderr.splitlines()), r.stderr)
            r = run('nostack', MADEIRA_X86_FAULT_DUMP='1')
            ls = lines(r)
            check('an unreadable stack says so, and the code and the objects still print', any('guest stack at ESP=0x6000000 is not readable' in l for l in ls)
                  and any('code block +0:' in l for l in r.stderr.splitlines()) and not any('frame #0' in l for l in ls), r.stderr)

# ---------------------------------------------------------------- 3b. virtual_ios.c's allocation ring (round 624, agents E1 S17 and D2 S8)
ring_src = vsrc[vsrc.index('#define IOS_X86F_RING 64'):vsrc.index("/* Guest band only: FEX's own arenas and the JIT pool would bury the signal. */")]
RING_MAIN = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef unsigned long long UINT64; typedef size_t SIZE_T; typedef unsigned int ULONG; typedef uintptr_t ULONG_PTR;
typedef struct { struct { void *UniqueProcess, *UniqueThread; } ClientId; } TEB;
static TEB teb = { { 0, (void *)0x9c } };
static TEB *NtCurrentTeb( void ) { return &teb; }
static ULONG_PTR win = 0x5000000000ULL;
ULONG_PTR ios_wow_base( void ) { return win; }
static unsigned int now_ms = 1000;
static unsigned int ios_vh_ms( void ) { return now_ms; }
@RING@
int main( void )
{
    ios_x86f_alloc_ring_dump( 0x1234 );                                         /* nothing yet */
    /* Unreal Gold EQ6DMS: the 64 KB texture view, a 756 KB view released before the storm; the fault one row past the end */
    ios_x86f_ring_note( win + 0x07a30000, 0x10000, 0x3000, 0x04, 0 );
    now_ms += 5; ios_x86f_ring_note( win + 0x07a40000, 0xbd000, 0x8000, 0, 1 );
    now_ms += 5; ios_x86f_ring_note( 0x7000000000ULL, 0x1000, 0x1000, 0x04, 0 );  /* outside the window: not kept */
    now_ms += 10;
    ios_x86f_alloc_ring_dump( 0x07a40010 );
    /* the ring keeps the last 64 */
    for (unsigned i = 0; i < 70; i++) ios_x86f_ring_note( win + 0x10000000 + i * 0x10000, 0x1000, 0x1000, 0x04, 0 );
    ios_x86f_alloc_ring_dump( 0x07a3fff0 );
    win = 0; ios_x86f_ring_note( 0x5000001000ULL, 0x1000, 0x1000, 0x04, 0 );     /* no window (a 64-bit process): not kept */
    ios_x86f_alloc_ring_dump( 0 );
    return 0;
}
"""
if sys.platform == 'darwin' and shutil.which(cc):
    with tempfile.TemporaryDirectory(prefix='x86ring-') as d:
        d = Path(d)
        (d / 'r.c').write_text(RING_MAIN.replace('@RING@', ring_src))
        c = subprocess.run([cc, '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined', str(d / 'r.c'), '-o', str(d / 'r')],
                           capture_output=True, text=True)
        check('round 624: the allocation ring of virtual_ios.c compiles on the host (ASan, UBSan, -Werror)', c.returncode == 0, c.stderr[-3000:])
        if c.returncode == 0:
            r = subprocess.run([str(d / 'r')], capture_output=True, text=True, env=dict(os.environ, ASAN_OPTIONS='symbolize=0'))
            out = [l for l in r.stderr.splitlines() if l.startswith('[x86-fault]')]
            check('an empty ring says so', out[0].endswith('(0 in all, the last 0, guest addresses): none recorded'), '\n'.join(out[:3]))
            check('Unreal Gold\'s shape: the fault is 16 bytes past the texture view\'s end and inside the view released after it; an address outside the window is not kept',
                  out[1].endswith('(2 in all, the last 2, guest addresses):')
                  and out[2] == '[x86-fault]     #0 alloc 0x07a30000+0x10000 type=0x3000 prot=0x4 tid=009c 20 ms ago  <== the fault is past its end'
                  and out[3] == '[x86-fault]     #1 free  0x07a40000+0xbd000 type=0x8000 prot=0x0 tid=009c 15 ms ago  <== the fault is inside', '\n'.join(out[:5]))
            last = [l for l in out if l.startswith('[x86-fault]   WoW64')][2]
            rows = out[out.index(last) + 1:out.index(last) + 65]
            check('the ring keeps the last 64 of 72, oldest first, and marks a fault just past an entry\'s end',
                  last.endswith('(72 in all, the last 64, guest addresses):') and rows[0].startswith('[x86-fault]     #8 alloc 0x10060000+0x1000')
                  and rows[-1].startswith('[x86-fault]     #71 alloc 0x10450000+0x1000') and len(rows) == 64, '\n'.join(rows[:2] + rows[-2:]))
            check('a process without a 4 GB window (64-bit) adds nothing', out[-1].endswith('#71 alloc 0x10450000+0x1000 type=0x1000 prot=0x4 tid=009c 0 ms ago')
                  and '(72 in all' in [l for l in out if l.startswith('[x86-fault]   WoW64')][-1], '\n'.join(out[-3:]))

# ---------------------------------------------------------------- 4. catalog, CI, iOS compile
gen_cat = (root / 'build/tools/gen-config-catalog.py').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the config catalog (generator and generated Swift) has the entry, default off, with its sources',
      '"env.MADEIRA_X86_FAULT_DUMP"' in gen_cat and '"sources": ["build/ntdll-unix/ios_x86_fault_diag.h", "build/ntdll-unix/signal_arm64_ios.c"]' in gen_cat
      and 'ConfigOption(key: "env.MADEIRA_X86_FAULT_DUMP"' in catalog and 'kind: .bool, defaultValue: "0"' in catalog.split('ConfigOption(key: "env.MADEIRA_X86_FAULT_DUMP"')[1].split('\n')[0])
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('the recommendations policy lets a list set the switch, 0 or 1 only',
      policy.count('"MADEIRA_X86_FAULT_DUMP"') == 2 and re.search(r'case "MADEIRA_X86_FAULT_DUMP":(?:\n\s*//[^\n]*)*\n\s*guard value == "0" \|\| value == "1"', policy) is not None)
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('build-ipa.yml runs this check', 'python3 tests/host/check-x86-fault-diag.py' in ci and ci.count('check-x86-fault-diag.py') == 1)

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
        check('...and the new header adds no warning', 'ios_x86_fault' not in r.stderr and 'x86f' not in r.stderr, r.stderr[-2000:])
    else:
        print('skip the iOS syntax check: no iphoneos SDK')
else:
    print('skip the iOS syntax check: no wine/build-macos/include/config.h (set WINE_SRC to a configured wine tree)')

print('PASS: [x86-fault]' if ok else 'FAILED: [x86-fault]')
sys.exit(0 if ok else 1)
