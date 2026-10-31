#!/usr/bin/env python3
"""env.MADEIRA_JUMBO_HALF: a too-large probe reservation gets half its size.

Ratchet & Clank: Rift Apart (build 549, log RiftApart.exe 2026-10-15 22:07:15)
makes each 4 GB arena with an 8 GB probe it releases at once. After four
arenas the window below the emulator's arena held only 4 GB holes, the fifth
probe failed and the game called a NULL (c0000005 at rip 0). In
build/ntdll-unix/virtual_ios.c's failed kernel-pick reserve, after the
holdback was tried, an opt-in block gives such a request half its size.

Checks:
  - the block, cut out of virtual_ios.c and compiled on the host with a stub
    allocator over a model of the VA window: unset or not '1', a commit, a
    size below 4 GB or not a power of two leave the request alone (the old
    failure path runs, the allocator is not called); otherwise the first free
    half-aligned slot from the usable floor is given, never one that ends
    above 0x7c00000000, and with no such slot the old path runs;
  - the block sits after the holdback and before the band census;
  - CI runs this check; the settings catalog documents the switch.
The host compile is skipped when no C compiler is found.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


start = src.index('                {\n                    const char *jh = getenv( "MADEIRA_JUMBO_HALF" );')
end = src.index('                ios_band_census( "jumbo reserve failed" );', start)
block = src[start:end]
check("the block is read once in virtual_ios.c", src.count('getenv( "MADEIRA_JUMBO_HALF" )') == 1)

cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP host compile: no C compiler")
else:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned long SIZE_T, ULONG_PTR;
typedef unsigned int ULONG, NTSTATUS;
#define MEM_COMMIT  0x1000
#define MEM_RESERVE 0x2000
#define STATUS_SUCCESS 0
#define STATUS_CONFLICTING_ADDRESSES 0xc0000018
#define OLD_PATH 0xdead
#define G 0x40000000ul
static ULONG_PTR ios_usable_va_floor = 0x7038000000ul;
static struct { ULONG_PTR lo, hi; } used[64];
static int nused, calls;
/* round 624: the holes an unhinted request may be placed in (a 63 GB map's free runs); none: an unhinted request is refused */
static struct { ULONG_PTR lo, hi; } holes[8];
static int nholes;
static NTSTATUS allocate_virtual_memory( void **ret, SIZE_T *size_ptr, ULONG type, ULONG protect,
                                         ULONG_PTR limit_low, ULONG_PTR limit_high,
                                         ULONG_PTR align, ULONG attributes )
{
    ULONG_PTR a = (ULONG_PTR)*ret, e = a + *size_ptr;
    calls++;
    if (limit_low || limit_high || align || attributes) return 0xbad;
    if (!a)
    {
        if (!nholes) return 0xbad;
        for (int i = 0; i < nholes; i++)
            if (holes[i].hi - holes[i].lo >= *size_ptr)
            {
                *ret = (void *)holes[i].lo;
                used[nused].lo = holes[i].lo; used[nused++].hi = holes[i].lo + *size_ptr;
                holes[i].lo += *size_ptr;
                return STATUS_SUCCESS;
            }
        return 0xc0000017;
    }
    for (int i = 0; i < nused; i++)
        if (a < used[i].hi && used[i].lo < e) return STATUS_CONFLICTING_ADDRESSES;
    used[nused].lo = a; used[nused++].hi = e;
    return STATUS_SUCCESS;
}
static NTSTATUS half_try( void **ret, SIZE_T *size_ptr, ULONG type, ULONG protect )
{
''' + block + r'''
    return OLD_PATH;
}
static int fails;
static void occupy( ULONG_PTR lo, ULONG_PTR hi ) { used[nused].lo = lo; used[nused++].hi = hi; }
static void expect( const char *what, const char *env, SIZE_T size, ULONG type,
                    NTSTATUS want_st, ULONG_PTR want_at, SIZE_T want_size, int want_calls )
{
    void *p = NULL; SIZE_T s = size; NTSTATUS st;
    if (env) setenv( "MADEIRA_JUMBO_HALF", env, 1 ); else unsetenv( "MADEIRA_JUMBO_HALF" );
    calls = 0;
    st = half_try( &p, &s, type, 4 );
    if (st != want_st || (ULONG_PTR)p != want_at || s != want_size || (want_calls >= 0 && calls != want_calls))
    {
        printf( "%s: st=%x at=%p size=%#lx calls=%d\n", what, st, p, s, calls );
        fails++;
    }
}
int main( void )
{
    /* the four arenas of the log fill every 8 GB-aligned 8 GB slot; 4 GB holes stay */
    occupy( 0x7000000000ul, 0x7100000000ul );
    occupy( 0x7100000000ul, 0x7200000000ul );
    occupy( 0x7300000000ul, 0x7400000000ul );
    occupy( 0x7500000000ul, 0x7600000000ul );
    occupy( 0x7700000000ul, 0x7800000000ul );
    occupy( 0x7900000000ul, 0x7a00000000ul );
    occupy( 0x7b00000000ul, 0x7c00000000ul );
    int base = nused;
    expect( "unset", NULL, 8 * G, MEM_RESERVE, OLD_PATH, 0, 8 * G, 0 );
    expect( "0", "0", 8 * G, MEM_RESERVE, OLD_PATH, 0, 8 * G, 0 );
    expect( "empty", "", 8 * G, MEM_RESERVE, OLD_PATH, 0, 8 * G, 0 );
    expect( "commit", "1", 8 * G, MEM_RESERVE | MEM_COMMIT, OLD_PATH, 0, 8 * G, 0 );
    expect( "2 GB", "1", 2 * G, MEM_RESERVE, OLD_PATH, 0, 2 * G, 0 );
    expect( "6 GB", "1", 6 * G, MEM_RESERVE, OLD_PATH, 0, 6 * G, 0 );
    expect( "12 GB", "1", 12 * G, MEM_RESERVE, OLD_PATH, 0, 12 * G, 0 );
    expect( "8 GB -> first 4 GB hole", "1", 8 * G, MEM_RESERVE, STATUS_SUCCESS, 0x7200000000ul, 4 * G, -1 );
    expect( "8 GB -> next 4 GB hole", "1", 8 * G, MEM_RESERVE, STATUS_SUCCESS, 0x7400000000ul, 4 * G, -1 );
    expect( "4 GB -> 2 GB slot", "1", 4 * G, MEM_RESERVE, STATUS_SUCCESS, 0x7600000000ul, 2 * G, -1 );
    expect( "4 GB -> 2 GB slot, aligned", "1", 4 * G, MEM_RESERVE, STATUS_SUCCESS, 0x7680000000ul, 2 * G, -1 );
    /* nothing free below 0x7c00000000: the old path */
    nused = base;
    occupy( 0x7200000000ul, 0x7300000000ul ); occupy( 0x7400000000ul, 0x7500000000ul );
    occupy( 0x7600000000ul, 0x7700000000ul ); occupy( 0x7800000000ul, 0x7900000000ul );
    occupy( 0x7a00000000ul, 0x7b00000000ul );
    expect( "full window", "1", 8 * G, MEM_RESERVE, OLD_PATH, 0, 8 * G, -1 );
    /* a slot above 0x7c00000000 is never given */
    nused = 0;
    occupy( 0x7000000000ul, 0x7c00000000ul );
    expect( "only above the window", "1", 8 * G, MEM_RESERVE, OLD_PATH, 0, 8 * G, -1 );
    /* 16 GB -> 8 GB-aligned 8 GB slot, from the floor */
    nused = 0;
    occupy( 0x7000000000ul, 0x7400000000ul );
    expect( "16 GB -> 8 GB slot", "1", 16 * G, MEM_RESERVE, STATUS_SUCCESS, 0x7400000000ul, 8 * G, 2 );
    /* round 624, step 2 (The Witcher 3 VCBB6X, a 63 GB map): no slot from the 512 GB layout's floor exists, the map's largest hole is
     * 23.5 GB at 0x570000000: 32 GB is refused, the half is placed unhinted in that hole */
    nused = 0;
    occupy( 0x7000000000ul, 0x7c00000000ul );
    holes[0].lo = 0x570000000ul; holes[0].hi = 0xb30000000ul; nholes = 1;
    expect( "off: no unhinted half either", NULL, 32 * G, MEM_RESERVE, OLD_PATH, 0, 32 * G, 0 );
    expect( "32 GB on a 63 GB map -> 16 GB unhinted", "1", 32 * G, MEM_RESERVE, STATUS_SUCCESS, 0x570000000ul, 16 * G, -1 );
    /* only a 12 GB hole left: the half does not fit either, the old path */
    holes[0].lo = 0x970000000ul; holes[0].hi = 0xc70000000ul;
    expect( "no hole for the half: the old path", "1", 32 * G, MEM_RESERVE, OLD_PATH, 0, 32 * G, -1 );
    expect( "a commit is never served", "1", 32 * G, MEM_RESERVE | MEM_COMMIT, OLD_PATH, 0, 32 * G, 0 );
    nholes = 0;
    printf( "%s\n", fails ? "HARNESS FAILED" : "harness ok" );
    return fails != 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        open(c, "w").write(harness)
        flags = [cc, "-std=gnu99", "-Wall", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter"]
        b = subprocess.run(flags + ["-fsanitize=address,undefined", c, "-o", exe], capture_output=True, text=True)
        if b.returncode:
            b = subprocess.run(flags + [c, "-o", exe], capture_output=True, text=True)
        check("the block compiles on the host", b.returncode == 0)
        if b.returncode == 0:
            r = subprocess.run([exe], capture_output=True, text=True)
            print(r.stdout.strip())
            check("off, a commit or a size that is not a power of two of 4 GB or more: the old path; "
                  "on: the first free half-aligned slot from the floor, never above 0x7c00000000",
                  r.returncode == 0)
        else:
            print(b.stderr[-2000:])

fail = src[src.rindex('ios_jumbo_holdback_take( *size_ptr );', 0, start):end + 200]
check("the block runs after the holdback was tried and before the band census",
      fail.index('ios_jumbo_holdback_take( *size_ptr );') < fail.index('getenv( "MADEIRA_JUMBO_HALF" )') <
      fail.index('ios_band_census( "jumbo reserve failed" );'))
check("it logs [jumbo-half] at most 32 times",
      block.count("if (half_logs++ < 32)") == 3 and block.count('"[jumbo-half] madeira-bcd') == 3)
step2 = block[block.index("[jumbo-half] step 2"):]
check("step 2 (round 624) runs after the aligned scan, once: its own request is not halved again (a thread-local guard)",
      block.index("for (; a + half <= 0x7c00000000ull; a += half)") < block.index("[jumbo-half] step 2")
      and "static __thread int jumbo_half_nested;" in block and "!jumbo_half_nested &&" in block
      and step2.index("jumbo_half_nested = 1;") < step2.index("allocate_virtual_memory( &hp2, &hs2, type, protect, 0, 0, 0, 0 );")
      < step2.index("jumbo_half_nested = 0;"))
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "run: python3 tests/host/check-jumbo-half.py" in wf)
cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents env.MADEIRA_JUMBO_HALF (default 0)",
      'ConfigOption(key: "env.MADEIRA_JUMBO_HALF", title: "Give a too-large probe reservation half its size", '
      'kind: .bool, defaultValue: "0"' in cat)

print("PASS: jumbo half" if ok else "FAILED: jumbo half")
sys.exit(0 if ok else 1)
