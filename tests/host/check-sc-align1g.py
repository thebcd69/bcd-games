#!/usr/bin/env python3
"""env.MADEIRA_SC_ALIGN1G (round 631, agent R1 S2): Social Club's 1 GB on a 4 GB boundary from a held slot.

GTA IV MCYQ0H / FM66KY (build 630) and 6M831K (build 574): libcef.dll asks for 1 GB on a 4 GB
boundary; with no free aligned slot in the furniture band the hinted-jumbo retry hands out an
unaligned 1 GB, Chromium frees it three times, over-reserves 5119 MB, finds no 5 GB hole
(helper memory's windows, GTA IV's 32-bit window placeholder) and aborts: "CEF browser crashed".

Checks, without Wine:
1. The constants: the slot is 4 GB aligned, sits right above libcef.dll's shortened metadata
   slot and ends at chrome_elf.dll's pools, so layout 2's hold total stays the same.
2. The wiring, textually: the key shortens libcef.dll's metadata slot only outside the RDR2
   geometry; the hold is taken after the slots; the call sits in the hinted-jumbo retry right
   before its final unhinted fall-back (after every walk that can give an aligned address),
   gated by layout 2's helper, the key, a 1 GB size and a 4 GB-aligned hint; ios_sc_rehold
   holds the 1 GB again.
3. The production key parser and ios_sc2_align1g / ios_sc_rehold / the grant table, compiled
   against a model Mach map and allocator: "1" alone turns it on, never beside the EA app's
   Chromium; the helper gets exactly [0x7700000000, +1 GB), once; a second ask of the same helper
   is refused with the hold untouched; a blocked slot puts the hold back and fails; a released
   grant (the guest's free or a dead helper's reap) is held again and serves the next helper.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()


def function(signature):
    start = native.index(signature)
    return native[start:native.index('\n}\n', start) + 3]


def define(name):
    return int(re.search(r'#define ' + name + r'\s+(0x[\da-fA-F]+)ULL', native)[1], 16)


# 1. Constants.
base, hold, size, j2l = (define(n) for n in ('IOS_SC2_A1G_BASE', 'IOS_SC2_A1G_HOLD', 'IOS_SC2_A1G_SIZE', 'IOS_SC2_A1G_J2L_REAL'))
assert base % (4 << 30) == 0 and size == 1 << 30 and size <= hold
assert define('IOS_SC2_J2L_BASE') + j2l == base, 'the slot must start where the shortened metadata slot ends'
assert base + hold == define('IOS_SC2_E_BASE'), 'the slot must end at chrome_elf.dll\'s pools'
assert j2l + hold == define('IOS_SC2_MD_REAL'), 'the hold total of the old metadata slot must not change'
assert define('IOS_SC2_A1G_BASE') not in (define('IOS_SC2_RW_ALIAS'), define('IOS_SC2_CAGE_BASE'))

# 2. Wiring.
boot = function('static void ios_sc2_boot_holds(void)')
assert ('    if (ios_sc2_rdr2_hold_enabled()) ios_sc2_rdr2_geometry();\n'
        '    else if (ios_sc2_align1g_key()) ios_sc2_slots[IOS_SC2_J2L].size = IOS_SC2_A1G_J2L_REAL;') in boot
assert boot.index('ios_sc2_align1g_key()') < boot.index('for (k = IOS_SC2_E; k <= IOS_SC2_OILPAN; k++)')
hold_block = boot.index('if (!ios_sc2_rdr2 && ios_sc2_slots[IOS_SC2_J2L].size == IOS_SC2_A1G_J2L_REAL)')
assert boot.index('ios_sc_layout_mode = 0;') < hold_block, 'the hold comes after every slot is held'
assert 'if (kr == KERN_SUCCESS) ios_sc2_a1g_on = ios_sc2_a1g_held = 1;' in boot[hold_block:]
retry = native[native.index('/* task#29 CEF plan C: a HINTED jumbo reserve that fails placement'):]
retry = retry[:retry.index('            sc_decided:')]
call = retry.index('st2 = ios_sc2_align1g( type, protect, &pick, &sz );')
gate = retry.rindex('if (', 0, call)
assert retry[gate:call].split() == ('if (st2 && sc2 && ios_sc2_a1g_on && *size_ptr == IOS_SC2_A1G_SIZE && '
                                    '!((ULONG_PTR)hint & 0xffffffffULL))').split(), retry[gate:call]
tail = retry[call:]
# the jumbo aligned walk (upstream ml2020, tests/host/check-jumbo-aligned.py) sits between the held slot and the unhinted fall-back: it
# is another way to an aligned address and comes after the held slot, so the held slot is still preferred
walk0 = tail.index("/* Upstream Madeira v0.2.0 (ml2020), BCD's form: a hint at a 4 GB boundary")
walk1 = tail.index('                if (st2)\n                {\n                    pick = NULL;', walk0)
tail = tail[:walk0].rstrip() + '\n                ' + tail[walk1:].lstrip()
assert re.fullmatch(r'st2 = ios_sc2_align1g\( type, protect, &pick, &sz \);\s*if \(st2\)\s*\{\s*pick = NULL;\s*'
                    r'sz = \*size_ptr;\s*st2 = allocate_virtual_memory\( &pick, &sz, type, protect, 0, 0, 0, 0 \);\s*\}\s*',
                    tail), 'the call must be the last step before the final unhinted fall-back'
for earlier in ('if (sc2 && ios_sc2_route(', 'if (st2 && off)', 'ios_jumbo_holdback_take( *size_ptr )',
                'st2 = ios_cage_grant(', 'cage4_slots'):
    assert retry.index(earlier) < gate, earlier
rehold = function('static void ios_sc_rehold( int kind )')
assert 'else if (kind == IOS_SC_K_A1G)' in rehold
assert 'enum { IOS_SC_K_V1 = 8, IOS_SC_K_CAGE, IOS_SC_K_OTHER, IOS_SC_K_A1G };' in native

# 3. The production code against a model.
pieces = '\n'.join(function(s) for s in (
    'static int ios_sc2_align1g_key(void)',
    'static void ios_sc_grant_add( uint64_t view, uint64_t report, uint64_t real, uint64_t asked, int kind, void *peb )',
    'static int ios_sc_grant_has( void *peb, int kind )',
    'static void ios_sc_rehold( int kind )',
    'static int ios_sc_grant_release( void *base, SIZE_T *size, int *rehold )',
    'static NTSTATUS ios_sc2_align1g( ULONG type, ULONG protect, void **pick, SIZE_T *sz )',
))
defines = '\n'.join(l for l in native.splitlines()
                    if re.match(r'#define (IOS_SC2_A1G_\w+|IOS_SC_GRANT_MAX)\b', l))
grants = native[native.index('static struct { uint64_t view, report, real, asked; void *peb; int kind, dead; } ios_sc_grants'):]
grants = grants[:grants.index('\n', grants.index('static pthread_mutex_t ios_sc_grant_lock'))]
program = r'''
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int NTSTATUS, ULONG;
typedef size_t SIZE_T;
typedef uintptr_t ULONG_PTR;
typedef uint64_t mach_vm_address_t, mach_vm_size_t;
typedef int kern_return_t;
#define STATUS_SUCCESS 0
#define STATUS_NO_MEMORY 0xc0000017u
#define STATUS_CONFLICTING_ADDRESSES 0xc0000018u
#define KERN_SUCCESS 0
#define KERN_NO_SPACE 3
#define VM_FLAGS_FIXED 0
#define MEMORY_OBJECT_NULL 0
#define PROT_NONE 0
#define VM_PROT_ALL 7
#define VM_INHERIT_COPY 1
#define MEM_RELEASE 0x8000
#define NtCurrentProcess() ((void *)-1)
enum { IOS_SC2_NONE, IOS_SC2_E, IOS_SC2_L, IOS_SC2_J2, IOS_SC2_J2L, IOS_SC2_OILPAN, IOS_SC2_REFUSE };
enum { IOS_SC_K_V1 = 8, IOS_SC_K_CAGE, IOS_SC_K_OTHER, IOS_SC_K_A1G };
#define IOS_SC_BRP_HOLD_BASE 0x7c00000000ULL
#define IOS_SC_BRP_HOLD_SIZE 0x100000000ULL
#define IOS_CAGE_BASE 0x7a00000000ULL
#define IOS_CAGE_REAL_SIZE 0x200000000ULL
static int ios_sc_layout_mode = 2, ios_sc_brp_held, ios_cage_holdback_live, ios_cage_window_tail_live;
static int ios_sc2_hold( int k ) { (void)k; return 1; }
#define dprintf(fd, ...) ((void)(fd), 0)
static void ios_va_release_note( void ) {}
/* The model: native holds and Wine views, as disjoint ranges. */
struct range { uint64_t lo, hi; int view; };
static struct range map[64];
static int nmap;
static int overlaps( uint64_t lo, uint64_t hi )
{
    for (int i = 0; i < nmap; i++) if (lo < map[i].hi && map[i].lo < hi) return 1;
    return 0;
}
static void add( uint64_t lo, uint64_t hi, int view ) { map[nmap].lo = lo; map[nmap].hi = hi; map[nmap].view = view; nmap++; }
static void cut( uint64_t lo, uint64_t hi, int view )
{
    for (int i = 0; i < nmap; i++)
    {
        if (map[i].view != view || !(lo < map[i].hi && map[i].lo < hi)) continue;
        struct range r = map[i];
        map[i] = map[--nmap]; i--;
        if (r.lo < lo) add( r.lo, lo, view );
        if (hi < r.hi) add( hi, r.hi, view );
    }
}
static int covered( uint64_t lo, uint64_t hi, int view )
{
    for (uint64_t a = lo; a < hi; a += 0x10000000ULL)
    {
        int hit = 0;
        for (int i = 0; i < nmap; i++) if (map[i].view == view && map[i].lo <= a && a < map[i].hi) hit = 1;
        if (!hit) return 0;
    }
    return 1;
}
static kern_return_t mach_vm_map( int task, mach_vm_address_t *a, mach_vm_size_t s, int mask, int flags, int obj,
                                  int off, int copy, int cur, int max, int inh )
{
    (void)task; (void)mask; (void)flags; (void)obj; (void)off; (void)copy; (void)cur; (void)max; (void)inh;
    if (overlaps( *a, *a + s )) return KERN_NO_SPACE;
    add( *a, *a + s, 0 );
    return KERN_SUCCESS;
}
static kern_return_t mach_vm_deallocate( int task, mach_vm_address_t a, mach_vm_size_t s ) { (void)task; cut( a, a + s, 0 ); return KERN_SUCCESS; }
static int mach_task_self( void ) { return 0; }
static int blocked;   /* something else took the slot between the release of the hold and the reserve */
static NTSTATUS allocate_virtual_memory( void **ret, SIZE_T *size, ULONG type, ULONG protect, int a, int b, int c, int d )
{
    uint64_t lo = (uint64_t)(ULONG_PTR)*ret;
    (void)type; (void)protect; (void)a; (void)b; (void)c; (void)d;
    if (blocked) add( lo, lo + *size, 2 );
    if (overlaps( lo, lo + *size )) return STATUS_CONFLICTING_ADDRESSES;
    add( lo, lo + *size, 1 );
    return STATUS_SUCCESS;
}
static NTSTATUS NtFreeVirtualMemory( void *proc, void **addr, SIZE_T *size, ULONG type )
{
    uint64_t lo = (uint64_t)(ULONG_PTR)*addr;
    (void)proc; (void)size; (void)type;
    for (int i = 0; i < nmap; i++) if (map[i].view == 1 && map[i].lo == lo) { cut( map[i].lo, map[i].hi, 1 ); return 0; }
    return 0xc000009fu;
}
static void *current_peb;
static void *ios_jit_current_peb( void ) { return current_peb; }
static int ios_sc2_a1g_on, ios_sc2_a1g_held;
''' + defines + '\n' + grants + '\n' + pieces + r'''
int main( int argc, char **argv )
{
    void *pick = NULL, *helper1 = (void *)0x1000, *helper2 = (void *)0x2000;
    SIZE_T sz = 0;
    NTSTATUS st;
    mach_vm_address_t h = IOS_SC2_A1G_BASE;
    int kind;

    if (argc > 1) { printf( "%d\n", ios_sc2_align1g_key() ); return 0; }
    /* boot: the hold, as ios_sc2_boot_holds takes it */
    assert( mach_vm_map( mach_task_self(), &h, IOS_SC2_A1G_HOLD, 0, VM_FLAGS_FIXED, MEMORY_OBJECT_NULL, 0, 0,
                         PROT_NONE, VM_PROT_ALL, VM_INHERIT_COPY ) == KERN_SUCCESS );
    ios_sc2_a1g_on = ios_sc2_a1g_held = 1;
    /* the helper's ask */
    current_peb = helper1;
    st = ios_sc2_align1g( 0x2000, 1, &pick, &sz );
    assert( st == STATUS_SUCCESS && (uint64_t)(ULONG_PTR)pick == IOS_SC2_A1G_BASE && sz == IOS_SC2_A1G_SIZE );
    assert( covered( IOS_SC2_A1G_BASE, IOS_SC2_A1G_BASE + IOS_SC2_A1G_SIZE, 1 ) );
    assert( covered( IOS_SC2_A1G_BASE + IOS_SC2_A1G_SIZE, IOS_SC2_A1G_BASE + IOS_SC2_A1G_HOLD, 0 ) );
    assert( !ios_sc2_a1g_held && ios_sc_grant_has( helper1, IOS_SC_K_A1G ) );
    /* a second ask of the same helper: refused, nothing moves */
    pick = NULL;
    assert( ios_sc2_align1g( 0x2000, 1, &pick, &sz ) == STATUS_NO_MEMORY && !pick );
    /* another helper while the first holds it: refused (no hold) */
    current_peb = helper2;
    assert( ios_sc2_align1g( 0x2000, 1, &pick, &sz ) == STATUS_NO_MEMORY );
    /* the guest frees it: the grant is found and the slot held again */
    current_peb = helper1;
    {
        void *b = (void *)(ULONG_PTR)IOS_SC2_A1G_BASE;
        SIZE_T s = 0;
        assert( !ios_sc_grant_release( b, &s, &kind ) && kind == IOS_SC_K_A1G );
        assert( !NtFreeVirtualMemory( NtCurrentProcess(), &b, &s, MEM_RELEASE ) );
        ios_sc_rehold( kind );
    }
    assert( ios_sc2_a1g_held && covered( IOS_SC2_A1G_BASE, IOS_SC2_A1G_BASE + IOS_SC2_A1G_HOLD, 0 ) );
    /* the next helper gets it */
    current_peb = helper2;
    assert( ios_sc2_align1g( 0x2000, 1, &pick, &sz ) == STATUS_SUCCESS && (uint64_t)(ULONG_PTR)pick == IOS_SC2_A1G_BASE );
    /* it dies; the reap frees the view and holds the slot again (ios_sc_reap_dead's two calls) */
    {
        void *b = (void *)(ULONG_PTR)IOS_SC2_A1G_BASE;
        SIZE_T s = 0;
        assert( !NtFreeVirtualMemory( NtCurrentProcess(), &b, &s, MEM_RELEASE ) );
        ios_sc_rehold( IOS_SC_K_A1G );
        assert( ios_sc2_a1g_held );
    }
    /* a slot another range took after the hold was released: the hold comes back, the ask fails */
    current_peb = (void *)0x3000;
    blocked = 1;
    pick = NULL;
    st = ios_sc2_align1g( 0x2000, 1, &pick, &sz );
    assert( st != STATUS_SUCCESS && !ios_sc_grant_has( current_peb, IOS_SC_K_A1G ) );
    /* the model's blocker sits where the hold was; ios_sc_rehold tried and could not take it back */
    assert( !ios_sc2_a1g_held );
    /* with the key off nothing is held again */
    ios_sc2_a1g_on = 0;
    ios_sc_rehold( IOS_SC_K_A1G );
    assert( !ios_sc2_a1g_held );
    puts( "model ok" );
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='sc-align1g-') as tmp:
    src, exe = Path(tmp) / 'model.c', Path(tmp) / 'model'
    src.write_text(program)
    r = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wno-unused-function', '-Werror', '-fsanitize=address,undefined',
                        '-o', str(exe), str(src)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-4000:]
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == 'model ok', out.stdout + out.stderr
    env0 = {k: v for k, v in os.environ.items() if k not in ('MADEIRA_SC_ALIGN1G', 'MADEIRA_EA_CEF')}
    for value, ea, expect in (('1', None, '1'), ('0', None, '0'), ('', None, '0'), ('11', None, '0'), (None, None, '0'),
                              ('1', '1', '0'), ('1', '0', '1')):
        env = dict(env0)
        if value is not None:
            env['MADEIRA_SC_ALIGN1G'] = value
        if ea is not None:
            env['MADEIRA_EA_CEF'] = ea
        got = subprocess.run([str(exe), 'key'], env=env, capture_output=True, text=True).stdout.strip()
        assert got == expect, (value, ea, got)

print('MADEIRA_SC_ALIGN1G: slot 4 GB aligned between the shortened libcef.dll metadata and chrome_elf.dll\'s pools '
      '(hold total unchanged); wired after the slots and as the last step before the unhinted fall-back, gated by '
      'helper/key/1 GB/4 GB hint; the model: one grant per helper at 0x7700000000, a blocked slot fails cleanly and the '
      'hold is retried, released or reaped grants are held again; key "1" only, never beside the EA app')
