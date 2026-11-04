#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The EA app's Chromium (ajan32/ea-cef, 2026-10-24): EADesktop.exe's four PartitionAlloc pools get their
four slots, in one process, only with env.MADEIRA_EA_CEF = 1; no Wine runs.

EA app 13.813.1.6327 (CEF 109), owner's iPhone18,2, build 598, log 13:54: PartitionAlloc runs in two copies
(chrome_elf.dll, libcef.dll), each asks for a plain 16 GB reserve (jumbo#1, #3) and a 16 GB + 64 KB guard-style
one (jumbo#2, #4). The old path gave #1/#2 real 16 GB views at 0x7800000000 / 0x73ffff0000 and #3/#4 soft grants
on those very views (0x7400000000, 0x77ffff0000): two live pools on the same pages, a commit of size 0 at
0x780020d000 (c000000d) and PartitionAlloc's int3; EACefSubProcess.exe found no slot for its own four (c0000018).

Source (textual) checks: where the EA code is hooked into shared code, and that every hook is gated by
env.MADEIRA_EA_CEF = 1 (read before anything else) -- the only lines outside the EA block that mention it are the
five listed here, so Social Club (layout 1 and 2), Steam's webhelper, Battle.net and every game run the code they ran
before; nothing in the recommended lists, the policy for downloaded lists or the other launcher sets the switch.
Behaviour (a C harness that compiles the PRODUCTION functions; AddressSanitizer/UBSan):
  - ios_ea_classify: the four asks of the 13:54 log become R1, B1, R2, B2 in that order; a fifth is refused; a
    process that finds a slot taken is refused whole (no half-formed second browser); the unhinted 32 GB - 64 KB
    over-reserve, the 8 GB and 4 GB cages, Social Club's asks and a plain ask at a hint that is not on a 16 GB
    boundary are left to the generic path;
  - ios_ea_route on a model of layout 2's band (boot holds taken with the production ios_sc2_hold, the app's hold of
    0x7000000000 whole and split in two): the four pools get 0x7800000000, 0x73ffff0000, 0x7000000000 and 0x7bffff0000;
    as PartitionAlloc sees them (a guard ask's pool starts 64 KB above its base) they are 16 GB-aligned and tile
    [0x7000000000, 0x8000000000) with no overlap; the real views overlap nothing in the boot map (RW alias, V8 cage
    holdback, FEX arena, furniture); a blocked slot puts the holds back and a retry works; a second live browser is
    refused; after the first exits the next one gets the same layout again (views released, holds restored);
    without layout 2 (or with the RDR2 geometry) the route declines and changes nothing;
  - ios_ea_cef_app: env.MADEIRA_EA_CEF unset, 0, 2 or empty -> 0 without reading the process (the PEB getter is never
    called); 1 -> EADesktop.exe is 1, EACefSubProcess.exe 2, every other image (Social Club's helper, Steam's
    webhelper, Battle.net, the Rockstar Launcher and its service, games) 0;
  - the soft-grant loop's own text (the production loop, compiled) with the EA flag off grants exactly what the
    original loop (kept here, from main 28cd4631) grants, over every combination of taken slots, sizes and offsets; on the
    state of the 13:54 log at jumbo#4 the old loop grants 0x7800000000 (base 0x77ffff0000, the log's value) and
    with the flag on it refuses: every slot holds a view or the arena;
  - process_ios.c: ea_cef_kind (EADesktop.exe without --type= is the browser; either image with --type= is a child
    to refuse; nothing else is EA), and the browser's rewritten command line.
Needs python3 and a C compiler.
"""
from pathlib import Path
import hashlib
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
proc = (root / 'build/ntdll-unix/process_ios.c').read_text()
failures = 0


def check(cond, what, detail=''):
    global failures
    print(('PASS: ' if cond else 'FAIL: ') + what + (('\n      ' + str(detail)) if detail and not cond else ''))
    if not cond:
        failures += 1


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


def defines(prefix):
    return ''.join(m.group(0) + '\n' for m in re.finditer(r'^#define ' + prefix + r'\w+\s+\S+', native, re.M))


# ---- the EA block and the lines that hook it into shared code ---------------------------------------------
begin = native.index("/***********************************************************************\n *           the EA app's Chromium")
end = native.index('static int ios_ea_slot_busy(')
end = native.index('\n}\n', end) + 3
ea_block = native[begin:end]
rest = native[:begin] + native[end:]
# BCD modified 2026-10-26: thread retirement has its own isolated EA block;
# check-ea-thread-reap.py runs it against stale/unmapped/reused TEBs and joins.
reap_begin = rest.index('/* BCD modified 2026-10-26: EA 608,')
reap_end = rest.index('/* ios-ea-thread-reap end */', reap_begin) + len('/* ios-ea-thread-reap end */')
rest = rest[:reap_begin] + rest[reap_end:]

hook_lines = []
for line in rest.split('\n'):
    code = re.sub(r'\s*/\*.*?\*/\s*$', '', line.strip())
    if code.startswith('/*') or code.startswith('*') or code.startswith('//'):
        continue
    if re.search(r'\bios_ea_|MADEIRA_EA_CEF|\bea (==|&&)|\bint ea\b', code):
        hook_lines.append(code)
check(hook_lines == [
    'static void ios_ea_grants_owner_died( void *peb );',
    'ios_ea_grants_owner_died( peb );',
    'int ea = ios_ea_cef_app();',
    'if (ea == 1 && ios_ea_route( hint, *size_ptr, type, protect, &pick, &sz, &st2 ))',
    'if (ea && ios_ea_slot_busy( slot, off )) continue;',
], 'outside the EA block, virtual_ios.c has exactly five lines that name the EA code, all of them gated', hook_lines)

nt = native[native.index('static inline __attribute__((always_inline)) NTSTATUS ios_nt_impl_NtAllocateVirtualMemory( HANDLE process'):]
nt = nt[:nt.index('static inline __attribute__((always_inline)) NTSTATUS ios_nt_impl_NtAllocateVirtualMemoryEx(')]
order = [nt.index(x) for x in (
    'int sc_helper = ios_sc_cef_enabled() && ios_sc_current_is_helper();',
    'int sc2 = sc_helper && ios_sc_layout_mode == 2;',
    'int ea = ios_ea_cef_app();',
    'if (sc2 && ios_sc2_route( hint, *size_ptr, type, protect, &pick, &sz, &st2 ))\n                    goto sc_decided;',
    'if (ea == 1 && ios_ea_route( hint, *size_ptr, type, protect, &pick, &sz, &st2 ))\n                    goto sc_decided;',
    'if (!sc2 && sc_helper && *size_ptr == 0x800000000ULL',
    'sc_decided:')]
check(order == sorted(order), 'the EA route comes right after Social Club\'s, before every generic try; Social Club\'s lines are as they were')
check('else if (ios_soft_n < IOS_SOFT_MAX && *size_ptr >= 0x400000000ULL && !sc_helper)' in nt
      and "*size_ptr == 0x100000000ULL && !sc_helper)" in nt and 'st2 = ios_cage_grant( type, protect, *size_ptr, &pick, &sz, "ml433" );' in nt,
      'the soft grant, the 4 GB cage and the 8 GB cage keep their conditions')
soft_pos = nt.index('if (slot == ios_steer_slot) continue;')
gate_pos = nt.index('if (ea && ios_ea_slot_busy( slot, off )) continue;')
check(soft_pos < gate_pos < nt.index('ios_soft[ios_soft_n].base = slot;'),
      'the EA test is in the soft loop, after the steer slot, before the grant is recorded')
reclaim = function(native, 'void ios_jit_reclaim_process( void *peb )')
check(reclaim.index('ios_sc_grants_owner_died( peb );') < reclaim.index('ios_ea_grants_owner_died( peb );') < reclaim.index('if (!peb || !rx_base) return;'),
      'ios_jit_reclaim_process: the EA grants are marked right after Social Club\'s, before anything is reclaimed')
check('if (!peb || !ios_ea_grant_n) return;' in function(ea_block, 'static void ios_ea_grants_owner_died('),
      'with no EA grant ios_ea_grants_owner_died returns at once')
app = function(ea_block, 'static int ios_ea_cef_app(void)')
check(app.index('if (!ios_ea_cef_enabled()) return 0;') < app.index('ios_ea_current_image_is('),
      'ios_ea_cef_app reads the switch before it looks at the process')
en = function(ea_block, 'static int ios_ea_cef_enabled(void)')
check('getenv( "MADEIRA_EA_CEF" )' in en and "e && e[0] == '1' && !e[1]" in en, 'the switch is exactly "1"')
route = function(ea_block, 'static int ios_ea_route(')
check(route.index('if (ios_sc_layout_mode != 2 || ios_sc2_rdr2)') < route.index('pthread_mutex_lock( &ios_ea_route_lock );')
      and 'return 0;' in route[:route.index('pthread_mutex_lock( &ios_ea_route_lock );')],
      'the route declines before it touches anything when layout 2 is not up')

spawn = proc[proc.index('/* madeira-bcd: the EA app\'s Chromium -- see ea_cef_kind.'):]
spawn = spawn[:spawn.index('#endif')]
check(spawn.index('getenv( "MADEIRA_EA_CEF" )') < spawn.index('ea_cef_kind(')
      and "(ea && ea[0] == '1' && !ea[1])" in spawn and ': EA_NOT_CEF;' in spawn
      and 'return STATUS_ACCESS_DENIED;' in spawn and 'kind == EA_CHILD' in spawn and 'kind == EA_BROWSER' in spawn,
      'the spawn block reads env.MADEIRA_EA_CEF first and looks at the image only when it is exactly 1')
sc_spawn = proc[proc.index('/* madeira-bcd: Social Club\'s Chromium -- see sc_helper_kind. */'):proc.index('/* madeira-bcd: the EA app\'s Chromium -- see ea_cef_kind.')]
check('MADEIRA_EA' not in sc_spawn and 'ea_cef' not in sc_spawn, 'Social Club\'s spawn block is untouched by it')
kind_start = proc.index("/* madeira-bcd: the EA app's Chromium (env.MADEIRA_EA_CEF = 1")
kind_block = proc[kind_start:proc.index('\n}\n', proc.index('static int ea_cef_kind('))+3]
proc_wo = proc.replace(kind_block, '').replace(spawn, '')
check('MADEIRA_EA_CEF' not in proc_wo and 'ea_cef' not in proc_wo and 'EA_BROWSER' not in proc_wo,
      'process_ios.c: the switch, ea_cef_kind and the EA kinds appear only in ea_cef_kind\'s block and the spawn block')

# no list, policy or other launcher sets or may set the switch
recs = (root / 'recommended/recommendations.json').read_text()
check('MADEIRA_EA_CEF' not in recs, 'no recommended list sets env.MADEIRA_EA_CEF')
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('MADEIRA_EA_CEF' not in policy, 'a downloaded list may not set env.MADEIRA_EA_CEF (RecommendationsPolicy.allowedEnvNames)')
launchers = (root / 'app/Madeira/BCDLaunchers.swift').read_text()
swift_users = sorted(p.name for p in (root / 'app/Madeira').glob('*.swift')
                     if 'MADEIRA_EA_CEF' in p.read_text() and not p.name.startswith(('ConfigCatalog', 'AutoTuneRules')))
check(swift_users == ['BCDLaunchers.swift'], 'in the app only the launcher table names env.MADEIRA_EA_CEF', swift_users)
bn = launchers[launchers.index('static let battlenet = BCDLauncherSpec('):launchers.index('/// Electronic Arts\' EA app.')]
check('MADEIRA_EA_CEF' not in bn and 'MADEIRA_SC_PA_POOLS' not in bn, 'Battle.net\'s entry has neither line')
# Round 630 (agent A2 S9; BPAGVS, H22TJN): the installed launcher also drops a released view's alias records (MADEIRA_ALIAS_RELEASE).
check(launchers.count('launcherOnlyConfig: ["env.MADEIRA_SC_PA_POOLS = 2", "env.MADEIRA_EA_CEF = 1", "env.MADEIRA_ALIAS_RELEASE = 1"]') == 1,
      'the EA app\'s entry sets layout 2 and the switch, in the lines only its installed launcher gets')
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
check(all('"env.%s"' % k in gen for k in ('MADEIRA_EA_CEF', 'MADEIRA_EA_CEF_FLAGS', 'MADEIRA_EA_CEF_CMDLINE')),
      'the settings catalog documents env.MADEIRA_EA_CEF, _FLAGS and _CMDLINE')

workflow = (root / '.github/workflows/build-ipa.yml').read_text()
check('run: python3 tests/host/check-ea-cef.py' in workflow, 'CI runs this check')

# ---- the compiled checks -----------------------------------------------------------------------------------
slots = native[native.index('static struct { uint64_t base, size; const char *what; } ios_sc2_slots[] ='):]
slots = slots[:slots.index('};') + 2] + '\n'
enum = native[native.index('enum { IOS_SC2_NONE,'):]
enum = enum[:enum.index(';') + 1] + '\n'
soft = native[native.index('#define IOS_SOFT_MAX'):]
soft = soft[:soft.index('static unsigned ios_soft_n;') + len('static unsigned ios_soft_n;')] + '\n'
soft += function(native, 'static int ios_soft_slot_taken(')
hold_fns = function(native, 'static int ios_sc2_hold(') + function(native, 'static void ios_sc2_unhold(')

# the production loop and the original one (main 28cd4631)
loop_start = nt.index('uint64_t slot;\n                    for (slot = 0x7000000000ULL; slot < 0x8000000000ULL; slot += align_unit)')
loop_end = nt.index('\n                    }\n', nt.index('break;', loop_start)) + len('\n                    }\n')
new_loop = nt[loop_start:loop_end]
OLD_LOOP = r'''uint64_t slot;
                    for (slot = 0x7000000000ULL; slot < 0x8000000000ULL; slot += align_unit)
                    {
                        void *probe_addr = (void *)(uintptr_t)slot;
                        SIZE_T probe_sz = 0x10000;
                        NTSTATUS pst;

                        if (ios_soft_slot_taken( slot )) continue;
                        if (slot == ios_steer_slot) continue;
                        if (slot < ios_furniture_ceiling
                            && slot + (uint64_t)*size_ptr > 0x7038000000ULL)
                            continue;
                        probe_addr = NULL; probe_sz = 0;
                        (void)probe_addr; (void)probe_sz; (void)pst;

                        ios_soft[ios_soft_n].base = slot;
                        ios_soft[ios_soft_n].size = *size_ptr;
                        ios_soft[ios_soft_n].committed = 0;
                        ios_soft[ios_soft_n].commits = 0;
                        ios_soft[ios_soft_n].collisions = 0;
                        ios_soft_n++;
                        *ret = (void *)(uintptr_t)(off ? slot + off - align_unit : slot);
                        st = STATUS_SUCCESS;
                        break;
                    }
'''
# the old loop has comments and a log call; compare it without them
def bare(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'dprintf\(2,.*?\);', '', text, flags=re.S)
    return re.sub(r'\s+', ' ', text).strip()


ea_lines = 'if (ea && ios_ea_slot_busy( slot, off )) continue;'
check(bare(new_loop.replace(ea_lines, '')) == bare(OLD_LOOP),
      'the production soft loop is the original loop plus the one EA line (comments and the log call aside)',
      bare(new_loop.replace(ea_lines, ''))[:300])

proc_enum = proc[proc.index('enum { EA_NOT_CEF = 0'):]
proc_enum = proc_enum[:proc_enum.index(';') + 1] + '\n'
sc_enum = proc[proc.index('enum { SC_NOT_HELPER = 0'):]
sc_enum = sc_enum[:sc_enum.index('\n\n')] + '\n'
proc_helpers = sc_enum + proc_enum + ''.join(function(proc, sig) for sig in (
    'static int sc_switch_end(',
    'static int ios_image_name_is(',
    'static int sc_helper_kind(',
    'static int ea_cef_kind(',
    'static int sc_extra_js_flags(',
    'static int sc_browser_cmdline(',
))

harness = r'''
#define mach_port_t ea_mach_port_t
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>
#ifndef __APPLE__
typedef unsigned int ea_mach_port_t;
#endif
#define MACH_PORT_NULL 0
typedef uint16_t WCHAR;
typedef unsigned int NTSTATUS, ULONG;
typedef size_t SIZE_T;
typedef uintptr_t ULONG_PTR;
typedef void *HANDLE;
#define STATUS_SUCCESS 0
#define STATUS_NO_MEMORY 0xc0000017
#define STATUS_CONFLICTING_ADDRESSES 0xc0000018
#define STATUS_MEMORY_NOT_ALLOCATED 0xc00000a0
#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
#define MEM_RELEASE 0x8000
#define MEM_FREE 0x10000
#define PAGE_NOACCESS 0x01
#define dprintf(fd, ...) fprintf( stderr, __VA_ARGS__ )
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)
#define GB (1ull << 30)
''' + defines('IOS_SC2_') + defines('IOS_SC_') + enum + slots + r'''
static unsigned ios_sc2_held;
static int ios_sc_layout_mode = 2, ios_sc2_rdr2;
static void ios_va_release_note( void ) {}

/* --- the process --- */
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING ImagePathName; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { RTL_USER_PROCESS_PARAMETERS *ProcessParameters; } PEB;
static PEB *cur_peb;
static int peb_reads;
static void *ios_jit_current_peb( void ) { peb_reads++; return cur_peb; }
static RTL_USER_PROCESS_PARAMETERS pp_store[8];
static PEB peb_store[8];
static WCHAR path_store[8][300];
static PEB *make_peb( int i, const char *path )
{
    size_t n = strlen( path ), k;
    for (k = 0; k < n; k++) path_store[i][k] = (WCHAR)path[k];
    pp_store[i].ImagePathName.Buffer = path_store[i];
    pp_store[i].ImagePathName.Length = (unsigned short)(n * 2);
    peb_store[i].ProcessParameters = &pp_store[i];
    return &peb_store[i];
}

/* --- a model of the address space: native holds and Wine views --- */
typedef uint64_t mach_vm_address_t, mach_vm_size_t;
typedef int kern_return_t;
typedef unsigned mach_msg_type_number_t;
typedef int *vm_region_info_t;
typedef struct { int protection; } vm_region_basic_info_data_64_t;
#define KERN_SUCCESS 0
#define KERN_NO_SPACE 3
#define KERN_INVALID_ADDRESS 1
#define VM_FLAGS_FIXED 0
#define MEMORY_OBJECT_NULL 0
#define PROT_NONE 0
#define VM_PROT_NONE 0
#define VM_PROT_ALL 7
#define VM_INHERIT_COPY 1
#define VM_REGION_BASIC_INFO_64 9
#define VM_REGION_BASIC_INFO_COUNT_64 9
struct reg { uint64_t base, size; int hold; };
static struct reg rg[64];
static int nrg;
static int mach_task_self( void ) { return 1; }
static int rg_overlaps( uint64_t b, uint64_t s )
{
    int i;
    for (i = 0; i < nrg; i++) if (b < rg[i].base + rg[i].size && b + s > rg[i].base) return 1;
    return 0;
}
static void rg_add( uint64_t b, uint64_t s, int hold ) { if (nrg >= 64) FAIL("model full\n"); rg[nrg].base = b; rg[nrg].size = s; rg[nrg].hold = hold; nrg++; }
static kern_return_t mach_vm_map( int task, mach_vm_address_t *a, mach_vm_size_t size, mach_vm_size_t mask, int flags, int obj,
                                  uint64_t off, int copy, int cur, int max, int inh )
{
    (void)task; (void)mask; (void)flags; (void)obj; (void)off; (void)copy; (void)cur; (void)max; (void)inh;
    if (rg_overlaps( *a, size )) return KERN_NO_SPACE;
    rg_add( *a, size, 1 );
    return KERN_SUCCESS;
}
static kern_return_t mach_vm_deallocate( int task, mach_vm_address_t a, mach_vm_size_t s )
{
    int i;
    (void)task;
    for (i = 0; i < nrg; )
        if (rg[i].base >= a && rg[i].base + rg[i].size <= a + s) rg[i] = rg[--nrg]; else i++;
    return KERN_SUCCESS;
}
static kern_return_t mach_vm_region( int task, mach_vm_address_t *a, mach_vm_size_t *s, int flavor, vm_region_info_t info,
                                     mach_msg_type_number_t *cnt, mach_port_t *obj )
{
    int i, best = -1;
    vm_region_basic_info_data_64_t *inf = (vm_region_basic_info_data_64_t *)info;
    (void)task; (void)flavor; (void)cnt; (void)obj;
    for (i = 0; i < nrg; i++)
        if (rg[i].base + rg[i].size > *a && (best < 0 || rg[i].base < rg[best].base)) best = i;
    if (best < 0) return KERN_INVALID_ADDRESS;
    if (rg[best].base > *a) *a = rg[best].base;
    *s = rg[best].base + rg[best].size - *a;
    inf->protection = rg[best].hold ? VM_PROT_NONE : 3;
    return KERN_SUCCESS;
}
typedef struct { void *AllocationBase; unsigned State; } MEMORY_BASIC_INFORMATION;
enum { MemoryBasicInformation };
#define NtCurrentProcess() ((HANDLE)-1)
static NTSTATUS NtQueryVirtualMemory( HANDLE h, const void *addr, int cls, MEMORY_BASIC_INFORMATION *mbi, SIZE_T len, SIZE_T *rl )
{
    uint64_t a = (uint64_t)(uintptr_t)addr;
    int i;
    (void)h; (void)cls; (void)len; (void)rl;
    for (i = 0; i < nrg; i++)
        if (a >= rg[i].base && a < rg[i].base + rg[i].size && !rg[i].hold)
        { mbi->AllocationBase = (void *)(uintptr_t)rg[i].base; mbi->State = MEM_RESERVE; return 0; }
    mbi->AllocationBase = NULL; mbi->State = MEM_FREE;
    return 0;
}
static NTSTATUS NtFreeVirtualMemory( HANDLE h, void **addr, SIZE_T *size, ULONG type )
{
    uint64_t a = (uint64_t)(uintptr_t)*addr;
    int i;
    (void)h; (void)size; (void)type;
    for (i = 0; i < nrg; i++) if (rg[i].base == a && !rg[i].hold) { rg[i] = rg[--nrg]; return 0; }
    return STATUS_MEMORY_NOT_ALLOCATED;
}
static NTSTATUS allocate_virtual_memory( void **ret, SIZE_T *size, ULONG type, ULONG protect, ULONG_PTR lo, ULONG_PTR hi,
                                         ULONG_PTR align, ULONG attr )
{
    uint64_t b = (uint64_t)(uintptr_t)*ret;
    (void)type; (void)protect; (void)lo; (void)hi; (void)align; (void)attr;
    if (!b) return STATUS_NO_MEMORY;
    if (b + *size > 0x8000000000ull) return 0xc000000d;
    if (rg_overlaps( b, *size )) return STATUS_CONFLICTING_ADDRESSES;   /* a view or a native hold */
    rg_add( b, *size, 0 );
    return 0;
}
''' + soft + hold_fns + '\n' + ea_block + r'''

/* --- layout 2's band as it is when the EA app asks (ios_sc2_boot_holds, StikJITHelper, virtual_init) --- */
static void boot( int split_app_hold )
{
    int k;
    nrg = 0; ios_sc2_held = 0; ios_soft_n = 0; ios_ea_grant_n = ios_ea_grant_dead_n = 0;
    rg_add( 0x10000, 0x7000000000ull - 0x10000, 0 );                 /* below the band: carveout, images, the rest */
    if (split_app_hold)                                              /* iOS may split the app's hold into several regions */
    { rg_add( IOS_SC2_L_BASE, 0x80000000ull, 1 ); rg_add( IOS_SC2_L_BASE + 0x80000000ull, 0x80000000ull, 1 ); }
    else rg_add( IOS_SC2_L_BASE, 0x100000000ull, 1 );                /* the app's hold [0x7000000000,+4 GB) */
    for (k = IOS_SC2_E; k <= IOS_SC2_OILPAN; k++) if (!ios_sc2_hold( k )) FAIL("boot hold %d failed\n", k);
    rg_add( IOS_SC2_RW_ALIAS, 0x38000000ull, 0 );                    /* the JIT pool's RW alias */
    rg_add( IOS_SC2_CAGE_BASE, 0x1ffff0000ull, 1 );                  /* the V8 cage holdback (IOS_CAGE_REAL_SIZE) */
    rg_add( IOS_SC_ARENA_BASE, 0x300000000ull, 0 );                  /* FEX's arena */
    rg_add( 0x7100000000ull, 0x2c0000000ull, 0 );                    /* furniture */
    rg_add( 0x73e0000000ull, 0x1fff0000ull, 0 );                     /* furniture up to the ceiling */
}

static int route( void *peb, uint64_t size, uint64_t hint, uint64_t *pick_out, uint64_t *sz_out, NTSTATUS *st_out )
{
    void *pick = NULL;
    SIZE_T sz = 0;
    NTSTATUS st = 99;
    int d;
    cur_peb = peb;
    d = ios_ea_route( (void *)(uintptr_t)hint, size, MEM_RESERVE, PAGE_NOACCESS, &pick, &sz, &st );
    *pick_out = (uint64_t)(uintptr_t)pick; *sz_out = sz; *st_out = st;
    return d;
}

#define EXPECT_ROUTE(peb, size, hint, want_pick, want_real) do { \
    uint64_t p_, z_; NTSTATUS s_; \
    if (!route( peb, size, hint, &p_, &z_, &s_ ) || s_ || p_ != (want_pick) || z_ != (size)) \
        FAIL("%s:%d: ask 0x%llx hint 0x%llx -> pick 0x%llx size 0x%llx st 0x%x, wanted 0x%llx\n", __FILE__, __LINE__, \
             (unsigned long long)(size), (unsigned long long)(hint), (unsigned long long)p_, (unsigned long long)z_, s_, \
             (unsigned long long)(want_pick)); \
    if (!view_is( (want_pick), (want_real) )) FAIL("%s:%d: no view of 0x%llx at 0x%llx\n", __FILE__, __LINE__, \
             (unsigned long long)(want_real), (unsigned long long)(want_pick)); } while (0)

static int view_is( uint64_t b, uint64_t s )
{
    int i;
    for (i = 0; i < nrg; i++) if (rg[i].base == b && rg[i].size == s && !rg[i].hold) return 1;
    return 0;
}
static int hold_is( uint64_t b, uint64_t s )
{
    int i;
    for (i = 0; i < nrg; i++) if (rg[i].base == b && rg[i].size == s && rg[i].hold) return 1;
    return 0;
}

/* the asks of the 13:54 log, EADesktop.exe (browser): jumbo#1..4 */
static const struct { uint64_t size, hint; } log_asks[4] = {
    { 0x400000000ull, 0x47d400000000ull }, { 0x400010000ull, 0x48c3ffff0000ull },
    { 0x400000000ull, 0x1b8800000000ull }, { 0x400010000ull, 0x30fffff0000ull } };

static int cmp_u64( const void *a, const void *b ) { uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b; return x < y ? -1 : x > y; }

/* as PartitionAlloc sees the four pools: a plain pool starts at the returned base, a guard-style one 64 KB above it */
static void tile_check( const uint64_t *ret, const uint64_t *size )
{
    uint64_t pool[4];
    int i;
    for (i = 0; i < 4; i++)
    {
        int guard = size[i] == 0x400010000ull;
        pool[i] = ret[i] + (guard ? 0x10000 : 0);
        if (pool[i] % (16 * GB)) FAIL("pool %d at 0x%llx is not on a 16 GB boundary\n", i, (unsigned long long)pool[i]);
    }
    qsort( pool, 4, sizeof(pool[0]), cmp_u64 );
    if (pool[0] != 0x7000000000ull || pool[1] != 0x7400000000ull || pool[2] != 0x7800000000ull || pool[3] != 0x7c00000000ull)
        FAIL("the four pools do not tile [0x7000000000,0x8000000000): %llx %llx %llx %llx\n", (unsigned long long)pool[0],
             (unsigned long long)pool[1], (unsigned long long)pool[2], (unsigned long long)pool[3]);
}

static void classify_cases( void )
{
    unsigned all = (1u << IOS_SC2_E) | (1u << IOS_SC2_L) | (1u << IOS_SC2_J2) | (1u << IOS_SC2_J2L) | (1u << IOS_SC2_OILPAN);
    unsigned mine = 0;
    int i, k;
    int want[4] = { IOS_EA_R1, IOS_EA_B1, IOS_EA_R2, IOS_EA_B2 };

    for (i = 0; i < 4; i++)
    {
        k = ios_ea_classify( log_asks[i].size, log_asks[i].hint, all, mine );
        if (k != want[i]) FAIL("ask %d of the log -> %d, wanted %d\n", i + 1, k, want[i]);
        mine |= 1u << k;
    }
    if (ios_ea_classify( 0x400000000ull, 0x2a8000000000ull, all, mine ) != IOS_EA_REFUSE) FAIL("a fifth plain pool\n");
    if (ios_ea_classify( 0x400010000ull, 0x2a7ffff0000ull + 0x400000000ull * 0, all, mine ) != IOS_EA_REFUSE) FAIL("a fifth guard pool\n");
    /* the unhinted over-reserve of PartitionAlloc's fallback, the cages, Social Club's asks */
    if (ios_ea_classify( 0x7ffff0000ull, 0, all, 0 ) != IOS_EA_NONE) FAIL("32 GB - 64 KB unhinted\n");
    if (ios_ea_classify( 0x200000000ull, 0x2000000000ull, all, 0 ) != IOS_EA_NONE) FAIL("the 8 GB cage\n");
    if (ios_ea_classify( 0x100000000ull, 0x1000000000ull, all, 0 ) != IOS_EA_NONE) FAIL("the 4 GB cage\n");
    if (ios_ea_classify( 0x800000000ull, 0x2be400000000ull, all, 0 ) != IOS_EA_NONE) FAIL("Social Club's 32 GB ask\n");
    if (ios_ea_classify( IOS_SC2_MD_ASK, 0x3a5c12340000ull, all, 0 ) != IOS_EA_NONE) FAIL("Social Club's metadata region (16 GB, a hint off the boundary)\n");
    if (ios_ea_classify( 0x400000000ull, 0x3a5c12340000ull, all, 0 ) != IOS_EA_NONE) FAIL("a plain ask off a 16 GB boundary\n");
    if (ios_ea_classify( 0x400010000ull, 0x48c400000000ull, all, 0 ) != IOS_EA_NONE) FAIL("a guard-sized ask on a boundary\n");
    /* a process that finds a slot taken is refused whole */
    for (i = IOS_SC2_E; i <= IOS_SC2_OILPAN; i++)
        if (ios_ea_classify( 0x400000000ull, 0x47d400000000ull, all & ~(1u << i), 0 ) != IOS_EA_REFUSE)
            FAIL("a first plain ask took a slot with slot %d missing\n", i);
    if (ios_ea_classify( 0x400000000ull, 0x47d400000000ull, 0, 0 ) != IOS_EA_REFUSE) FAIL("no slot held\n");
    printf("PASS: ios_ea_classify: the log's four asks are R1, B1, R2, B2; a fifth, a missing slot and a second browser are refused; "
           "the over-reserve, both cages and Social Club's asks are left alone\n");
}

static void route_cases( void )
{
    uint64_t ret[4], size[4], p, z;
    NTSTATUS s;
    int i, split;
    PEB *a = make_peb( 0, "C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EADesktop.exe" );
    PEB *b = make_peb( 1, "C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EADesktop.exe" );

    for (split = 0; split < 2; split++)
    {
        boot( split );
        if (ios_sc2_held != ((1u << IOS_SC2_E) | (1u << IOS_SC2_L) | (1u << IOS_SC2_J2) | (1u << IOS_SC2_J2L) | (1u << IOS_SC2_OILPAN)))
            FAIL("boot holds 0x%x\n", ios_sc2_held);
        EXPECT_ROUTE( a, log_asks[0].size, log_asks[0].hint, 0x7800000000ull, 0x100000000ull );
        EXPECT_ROUTE( a, log_asks[1].size, log_asks[1].hint, 0x73ffff0000ull, 0x400010000ull );
        EXPECT_ROUTE( a, log_asks[2].size, log_asks[2].hint, 0x7000000000ull, 0x100000000ull );
        EXPECT_ROUTE( a, log_asks[3].size, log_asks[3].hint, 0x7bffff0000ull, 0x100010000ull );
        ret[0] = 0x7800000000ull; ret[1] = 0x73ffff0000ull; ret[2] = 0x7000000000ull; ret[3] = 0x7bffff0000ull;
        for (i = 0; i < 4; i++) size[i] = log_asks[i].size;
        tile_check( ret, size );
        if (ios_sc2_held) FAIL("holds left after the four pools: 0x%x\n", ios_sc2_held);
        if (!route( a, 0x400000000ull, 0x2a8000000000ull, &p, &z, &s ) || s != STATUS_NO_MEMORY) FAIL("a fifth pool\n");
        if (route( a, 0x7ffff0000ull, 0, &p, &z, &s ) != 0) FAIL("the unhinted over-reserve was decided\n");
        if (route( a, 0x200000000ull, 0x2000000000ull, &p, &z, &s ) != 0) FAIL("the 8 GB cage was decided\n");
        if (ios_ea_grant_n != 4) FAIL("grants %d\n", ios_ea_grant_n);
        /* a second live browser: refused whole, takes nothing */
        if (!route( b, 0x400000000ull, 0x47d400000000ull, &p, &z, &s ) || s != STATUS_NO_MEMORY) FAIL("a second browser got a pool\n");
        if (ios_ea_grant_n != 4) FAIL("a refused ask left a grant\n");
        /* the first one exits: the next browser gets the layout it booted with */
        ios_ea_grants_owner_died( a );
        if (ios_ea_grant_dead_n != 4) FAIL("dead grants %d\n", ios_ea_grant_dead_n);
        EXPECT_ROUTE( b, log_asks[0].size, log_asks[0].hint, 0x7800000000ull, 0x100000000ull );
        if (ios_ea_grant_dead_n != 0 || ios_ea_grant_n != 1) FAIL("reap: dead %d grants %d\n", ios_ea_grant_dead_n, ios_ea_grant_n);
        EXPECT_ROUTE( b, log_asks[1].size, log_asks[1].hint, 0x73ffff0000ull, 0x400010000ull );
        EXPECT_ROUTE( b, log_asks[2].size, log_asks[2].hint, 0x7000000000ull, 0x100000000ull );
        EXPECT_ROUTE( b, log_asks[3].size, log_asks[3].hint, 0x7bffff0000ull, 0x100010000ull );
    }
    printf("PASS: ios_ea_route: R1 0x7800000000, B1 0x73ffff0000, R2 0x7000000000, B2 0x7bffff0000; the pools tile the band; "
           "a fifth ask and a second live browser are refused; a restarted browser gets the same layout (app hold whole or split)\n");

    /* a slot blocked: the holds come back, nothing is granted, a retry works once it is free */
    boot( 0 );
    rg_add( 0x73ffff0000ull, 0x10000, 0 );   /* something in the 64 KB below B1's slot */
    if (!route( a, log_asks[0].size, log_asks[0].hint, &p, &z, &s ) || s) FAIL("R1\n");
    if (!route( a, log_asks[1].size, log_asks[1].hint, &p, &z, &s ) || s != STATUS_CONFLICTING_ADDRESSES) FAIL("B1 over a mapping: 0x%x\n", s);
    if (!hold_is( IOS_SC2_J2_BASE, 0x200000000ull ) || !hold_is( IOS_SC2_J2L_BASE, 0x200000000ull ) || ios_ea_grant_n != 1
        || (ios_sc2_held & ((1u << IOS_SC2_J2) | (1u << IOS_SC2_J2L))) != ((1u << IOS_SC2_J2) | (1u << IOS_SC2_J2L)))
        FAIL("B1 failed but its holds are not back\n");
    mach_vm_deallocate( 1, 0x73ffff0000ull, 0x10000 );
    EXPECT_ROUTE( a, log_asks[1].size, log_asks[1].hint, 0x73ffff0000ull, 0x400010000ull );
    printf("PASS: a blocked slot gives the holds back and fails the ask; the retry works\n");

    /* layout 2 is not up, or the RDR2 geometry: decline, change nothing */
    boot( 0 );
    ios_sc_layout_mode = 1;
    if (route( a, log_asks[0].size, log_asks[0].hint, &p, &z, &s ) != 0 || ios_ea_grant_n || !hold_is( IOS_SC2_E_BASE, 0x100000000ull)) FAIL("layout 1\n");
    ios_sc_layout_mode = 0;
    if (route( a, log_asks[0].size, log_asks[0].hint, &p, &z, &s ) != 0 || ios_ea_grant_n) FAIL("layout 0\n");
    ios_sc_layout_mode = 2; ios_sc2_rdr2 = 1;
    if (route( a, log_asks[0].size, log_asks[0].hint, &p, &z, &s ) != 0 || ios_ea_grant_n) FAIL("RDR2 geometry\n");
    ios_sc2_rdr2 = 0;
    printf("PASS: without layout 2 (or with the RDR2 geometry) the route declines and takes nothing\n");
}

/* ---- the soft-grant loop, production and original --------------------------------------------------------- */
static const ULONG_PTR ios_steer_slot = 0x7C00000000;
static const ULONG_PTR ios_furniture_ceiling = 0x73ffff0000;
static int soft_prod( int ea, uint64_t size, uint64_t off, uint64_t *ret_out )
{
    SIZE_T size_val = size, *size_ptr = &size_val;
    void *retv = NULL, **ret = &retv;
    NTSTATUS st = 1;
    ULONG_PTR align_unit = 0x400000000ULL;
    (void)ea;
    ''' + new_loop + r'''
    *ret_out = (uint64_t)(uintptr_t)retv;
    return st == 0;
}
static int soft_old( uint64_t size, uint64_t off, uint64_t *ret_out )
{
    SIZE_T size_val = size, *size_ptr = &size_val;
    void *retv = NULL, **ret = &retv;
    NTSTATUS st = 1;
    ULONG_PTR align_unit = 0x400000000ULL;
    ''' + OLD_LOOP + r'''
    *ret_out = (uint64_t)(uintptr_t)retv;
    return st == 0;
}

static void soft_cases( void )
{
    static const uint64_t sizes[] = { 0x400000000ull, 0x400010000ull, 0x800000000ull, 0x500000000ull };
    static const uint64_t offs[] = { 0, 0x3ffff0000ull, 0x7ffff0000ull };
    unsigned taken, si, oi, n = 0;
    uint64_t r1, r2;

    /* ea off: the production loop and the original agree on every state */
    for (taken = 0; taken < 16; taken++)
        for (si = 0; si < 4; si++)
            for (oi = 0; oi < 3; oi++)
            {
                unsigned b;
                uint64_t base1 = 0, base2 = 0;
                int g1, g2;
                nrg = 0;
                if (taken & 1) rg_add( 0x7000000000ull, 0x10000, 0 );
                if (taken & 2) rg_add( 0x73ffff0000ull, 0x400010000ull, 0 );   /* a view: slots 0x74 busy */
                if (taken & 4) rg_add( 0x7800000000ull, 0x100000000ull, 0 );
                if (taken & 8) rg_add( 0x7c00000000ull, 0x100000000ull, 1 );
                for (b = 0; b < 8; b++)   /* soft slots taken before: every subset of the three */
                    {
                        ios_soft_n = 0;
                        if (b & 1) { ios_soft[ios_soft_n].base = 0x7400000000ull; ios_soft_n++; }
                        if (b & 2) { ios_soft[ios_soft_n].base = 0x7800000000ull; ios_soft_n++; }
                        if (b & 4) { ios_soft[ios_soft_n].base = 0x7c00000000ull; ios_soft_n++; }
                        g1 = soft_prod( 0, sizes[si], offs[oi], &r1 );
                        base1 = ios_soft_n ? ios_soft[ios_soft_n - 1].base : 0;
                        ios_soft_n = 0;
                        if (b & 1) { ios_soft[ios_soft_n].base = 0x7400000000ull; ios_soft_n++; }
                        if (b & 2) { ios_soft[ios_soft_n].base = 0x7800000000ull; ios_soft_n++; }
                        if (b & 4) { ios_soft[ios_soft_n].base = 0x7c00000000ull; ios_soft_n++; }
                        g2 = soft_old( sizes[si], offs[oi], &r2 );
                        base2 = ios_soft_n ? ios_soft[ios_soft_n - 1].base : 0;
                        if (g1 != g2 || r1 != r2 || base1 != base2)
                            FAIL("ea off: production %d 0x%llx 0x%llx, original %d 0x%llx 0x%llx (taken %u, soft %u, size 0x%llx off 0x%llx)\n",
                                 g1, (unsigned long long)r1, (unsigned long long)base1, g2, (unsigned long long)r2,
                                 (unsigned long long)base2, taken, b, (unsigned long long)sizes[si], (unsigned long long)offs[oi]);
                        n++;
                    }
            }
    printf("PASS: with the EA flag off the production soft loop grants what the original loop grants (%u states)\n", n);

    /* the state of the 13:54 log at jumbo#4: R1 and B1 real 16 GB views, the third pool soft at 0x7400000000, FEX's arena at 0x7c.. */
    nrg = 0; ios_soft_n = 0;
    rg_add( 0x7038000000ull, 0x3c7ff0000ull, 0 );                        /* furniture */
    rg_add( 0x73ffff0000ull, 0x400010000ull, 0 );                        /* jumbo#2, B1 (real) */
    rg_add( 0x7800000000ull, 0x400000000ull, 0 );                        /* jumbo#1, R1 (real) */
    rg_add( 0x7c00000000ull, 0x400000000ull, 0 );                        /* FEX's arena */
    ios_soft[0].base = 0x7400000000ull; ios_soft[0].size = 0x400000000ull; ios_soft_n = 1;   /* jumbo#3 */
    if (!soft_prod( 0, 0x400010000ull, 0x3ffff0000ull, &r1 ) || r1 != 0x77ffff0000ull || ios_soft[ios_soft_n - 1].base != 0x7800000000ull)
        FAIL("the log's jumbo#4 is not reproduced: 0x%llx\n", (unsigned long long)r1);
    ios_soft_n = 1;
    if (soft_prod( 1, 0x400010000ull, 0x3ffff0000ull, &r1 ) || ios_soft_n != 1)
        FAIL("with the flag on jumbo#4 still got a soft grant: 0x%llx\n", (unsigned long long)r1);
    /* and the third ask with only the first two given: no soft slot on a view either */
    ios_soft_n = 0;
    if (!soft_prod( 0, 0x400000000ull, 0, &r1 ) || r1 != 0x7400000000ull) FAIL("jumbo#3 of the log\n");
    ios_soft_n = 0;
    if (soft_prod( 1, 0x400000000ull, 0, &r1 )) FAIL("with the flag on jumbo#3 got a soft grant on B1's view\n");
    /* an ask the route refused gets no soft slot either, even where every slot is free */
    nrg = 0; ios_soft_n = 0; ios_sc2_held = 0; ios_ea_grant_n = ios_ea_grant_dead_n = 0;
    {
        uint64_t p_, z_;
        NTSTATUS s_;
        PEB *pe = make_peb( 3, "C:\\Program Files\\Electronic Arts\\EA Desktop\\EADesktop.exe" );
        if (!route( pe, 0x400000000ull, 0x47d400000000ull, &p_, &z_, &s_ ) || s_ != STATUS_NO_MEMORY) FAIL("no refusal with no slot held\n");
        if (soft_prod( 1, 0x400000000ull, 0, &r1 )) FAIL("a refused ask got a soft slot: 0x%llx\n", (unsigned long long)r1);
        if (route( pe, 0x7ffff0000ull, 0, &p_, &z_, &s_ )) FAIL("the unhinted over-reserve was decided\n");   /* resets the refusal */
        ios_soft_n = 0;
        if (!soft_prod( 1, 0x400000000ull, 0, &r1 ) || r1 != 0x7400000000ull) FAIL("the refusal outlived the next ask\n");
    }
    /* a free slot is still granted with the flag on: nothing is mapped at 0x7400000000 */
    nrg = 0; ios_soft_n = 0;
    rg_add( 0x7800000000ull, 0x400000000ull, 0 );
    if (!soft_prod( 1, 0x400000000ull, 0, &r1 ) || r1 != 0x7400000000ull) FAIL("a free slot was refused with the flag on\n");
    printf("PASS: on the 13:54 log's state the original loop gives jumbo#4 the slot of R1's view (base 0x77ffff0000); with the flag on "
           "no slot that holds a view, a hold or the arena is given, a free one still is\n");
}

/* ---- ios_ea_cef_app ----------------------------------------------------------------------------------------- */
static void app_cases( const char *mode )
{
    static const struct { const char *path; int want; } imgs[] = {
        { "C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EADesktop.exe", 1 },
        { "\\??\\C:\\program files\\electronic arts\\ea desktop\\13.813.1.6327\\ea desktop\\EADESKTOP.EXE", 1 },
        { "C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EACefSubProcess.exe", 2 },
        { "C:\\Program Files\\Electronic Arts\\EA Desktop\\EAGEPlugin.exe", 0 },
        { "C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe", 0 },
        { "C:\\Program Files (x86)\\Steam\\bin\\cef\\cef.win7x64\\steamwebhelper.exe", 0 },
        { "C:\\Program Files (x86)\\Battle.net\\Battle.net.exe", 0 },
        { "C:\\Program Files\\Rockstar Games\\Launcher\\Launcher.exe", 0 },
        { "C:\\Program Files\\Rockstar Games\\Launcher\\RockstarService.exe", 0 },
        { "C:\\Games\\GTA5.exe", 0 }, { "C:\\Games\\RDR2.exe", 0 }, { "C:\\x\\NotEADesktop.exe", 0 }, { "C:\\x\\EADesktop.exe.bak", 0 },
        { "C:\\x\\EADesktop.ex", 0 }, { "", 0 } };
    int i, on = !strcmp( mode, "app-on" );

    for (i = 0; i < (int)(sizeof(imgs) / sizeof(imgs[0])); i++)
    {
        int want = on ? imgs[i].want : 0, got, before = peb_reads;
        cur_peb = make_peb( 2, imgs[i].path );
        got = ios_ea_cef_app();
        if (got != want) FAIL("%s: [%s] -> %d, wanted %d\n", mode, imgs[i].path, got, want);
        if (!on && peb_reads != before) FAIL("%s: the process was read although the switch is not 1\n", mode);
    }
    printf("PASS: ios_ea_cef_app with the switch %s\n", on ? "1: EADesktop.exe 1, EACefSubProcess.exe 2, every other image 0"
                                                           : "unset/0/2/empty: 0 for every image, the process is never read");
}

/* ---- process_ios.c --------------------------------------------------------------------------------------------- */
''' + proc_helpers + r'''
static WCHAR wbuf[4][2048];
static WCHAR *w( int i, const char *s, int *len )
{
    int k;
    for (k = 0; s[k]; k++) wbuf[i][k] = (WCHAR)(unsigned char)s[k];
    wbuf[i][k] = 0;
    *len = k;
    return wbuf[i];
}
static char outbuf[4096];
static const char *a( WCHAR *p, int n ) { int k; for (k = 0; k < n; k++) outbuf[k] = (char)p[k]; outbuf[n] = 0; return outbuf; }
static int ekind( const char *image, const char *cl )
{
    int il, cl_len;
    WCHAR *wi = w( 0, image, &il ), *wc = w( 1, cl, &cl_len );
    return ea_cef_kind( wi, il, wc, cl_len );
}
static void process_cases( void )
{
    const char *ead = "C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EADesktop.exe";
    const char *sub = "C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EACefSubProcess.exe";
    const char *cl = "\"C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EADesktop.exe\" ";   /* the 13:54 log */
    const char *gpu = "\"C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EACefSubProcess.exe\" --type=gpu-process "
                      "--no-sandbox --log-severity=warning --lang=en-US";
    int n, how = 0;
    WCHAR out[4096], *wc;
    const char *want;

    if (ekind( ead, cl ) != EA_BROWSER) FAIL("EADesktop.exe is not the browser\n");
    if (ekind( "\\??\\c:\\program files\\electronic arts\\ea desktop\\eadesktop.exe", cl ) != EA_BROWSER) FAIL("NT path, lower case\n");
    if (ekind( ead, "x.exe --type=renderer --lang=en" ) != EA_CHILD) FAIL("EADesktop.exe --type=\n");
    if (ekind( sub, gpu ) != EA_CHILD || ekind( sub, "x --type=utility" ) != EA_CHILD) FAIL("EACefSubProcess.exe --type=\n");
    if (ekind( sub, "x" ) != EA_NOT_CEF) FAIL("EACefSubProcess.exe without --type= is left alone\n");
    if (ekind( ead, "x.exe --no-type=renderer" ) != EA_BROWSER) FAIL("--no-type= taken for --type=\n");
    if (ekind( "C:\\x\\NotEADesktop.exe", cl ) != EA_NOT_CEF || ekind( "C:\\x\\EADesktop.exe.bak", cl ) != EA_NOT_CEF) FAIL("look-alikes\n");
    if (ekind( "C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe", "a --type=renderer" ) != EA_NOT_CEF) FAIL("Social Club\n");
    if (ekind( "C:\\Program Files (x86)\\Steam\\bin\\cef\\cef.win7x64\\steamwebhelper.exe", "a --type=renderer" ) != EA_NOT_CEF) FAIL("steamwebhelper\n");
    if (ekind( "C:\\Program Files (x86)\\Battle.net\\Battle.net.exe", "a --type=renderer" ) != EA_NOT_CEF) FAIL("Battle.net\n");
    if (ekind( "C:\\Program Files\\Rockstar Games\\Launcher\\Launcher.exe", "a --type=renderer" ) != EA_NOT_CEF) FAIL("Rockstar Games Launcher\n");
    if (ekind( "C:\\Games\\GTA5.exe", "a --type=renderer" ) != EA_NOT_CEF) FAIL("a game\n");
    /* Social Club's kind function is the one it was */
    if (sc_helper_kind( w( 0, ead, &n ), n, w( 1, cl, &how ), how ) != SC_NOT_HELPER) FAIL("EADesktop.exe taken for Social Club's helper\n");
    wc = w( 2, cl, &n );
    n = sc_browser_cmdline( wc, n, 1, NULL, out, 4096, &how );
    want = "\"C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EADesktop.exe\"  --disable-features=PartitionAllocBackupRefPtr "
           "--single-process --js-flags=--jitless --no-proxy-server";
    if (n < 0 || strcmp( a( out, n ), want )) FAIL("the browser's command line: [%s]\nwanted [%s]\n", n < 0 ? "-1" : a( out, n ), want);
    wc = w( 2, cl, &n );
    n = sc_browser_cmdline( wc, n, 1, "--disable-gpu", out, 4096, &how );
    if (n < 0 || !strstr( a( out, n ), "--no-proxy-server --disable-gpu")) FAIL("env.MADEIRA_EA_CEF_FLAGS is not appended\n");
    printf("PASS: only EADesktop.exe without --type= is the browser; --type= children of either image are refused; "
           "the browser's command line gets --single-process, BackupRefPtr off, --jitless, --no-proxy-server (+ the flags)\n");
}

int main( int argc, char **argv )
{
    const char *mode = argc > 1 ? argv[1] : "";
    if (!strcmp( mode, "classify" )) classify_cases();
    else if (!strcmp( mode, "route" )) route_cases();
    else if (!strcmp( mode, "soft" )) soft_cases();
    else if (!strcmp( mode, "app-on" ) || !strcmp( mode, "app-off" )) app_cases( mode );
    else if (!strcmp( mode, "process" )) process_cases();
    else FAIL("mode?\n");
    return 0;
}
'''

with tempfile.TemporaryDirectory() as t:
    c = Path(t) / 'ea.c'
    c.write_text(harness)
    if os.environ.get('EA_CEF_KEEP'):   # for a look at the generated harness
        Path(os.environ['EA_CEF_KEEP']).write_text(harness)
    exe = Path(t) / 'ea'
    r = subprocess.run(['cc', '-std=gnu11', '-O1', '-Wall', '-Wno-unused-function', '-Wno-unused-variable',
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all', str(c), '-o', str(exe)],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stderr)
        check(False, 'the EA code compiles on the host')
    else:
        check(True, 'the EA code compiles on the host')
        base_env = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
        env_cases = (('classify', None), ('route', None), ('soft', None), ('process', None), ('app-off', None),
                     ('app-off', '0'), ('app-off', '2'), ('app-off', ''), ('app-off', '11'), ('app-on', '1'))
        for mode, ea in env_cases:
            env = dict(base_env)
            if ea is not None:
                env['MADEIRA_EA_CEF'] = ea
            env['ASAN_OPTIONS'] = 'symbolize=0'
            out = subprocess.run([str(exe), mode], capture_output=True, text=True, env=env)
            print(out.stdout, end='')
            check(out.returncode == 0, 'harness %s (MADEIRA_EA_CEF=%r)' % (mode, ea), out.stderr[-600:])

print('PASS: check-ea-cef' if not failures else 'FAIL: %d check(s) failed' % failures)
sys.exit(1 if failures else 0)
