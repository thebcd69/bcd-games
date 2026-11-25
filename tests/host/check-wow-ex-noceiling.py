#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 32-bit program's VirtualAlloc2 whose address requirements name no ceiling gets the guest's (round 714, G1 S1).

The Binding of Isaac: Repentance+ (WVTJ39, EYBPCX on build 712 with the row's MADEIRA_WOW_ALLOC_INSIDE = 1; W8X8ZF, N0QS49 on 686; FWN9ZF,
ASYB2E): mimalloc asks VirtualAlloc2 for aligned memory with { Lowest 0, Highest 0, Alignment 4 MB }; wow64 passes a guest's own block on as
it is, so limit_high reached the unix side as 0 and round 707's branches (which need a ceiling) never ran: no log of the archive has a
[wow-inside] r708 or [jumbo-wow] r666 line. The 1 GB Lua reserve and the 4 MB segments were placed outside the window.

Checks: ios_wow_ex_noceiling_shape and ios_wow_ex_noceiling_on, cut from virtual_ios.c and compiled on the host: the shape is a thread with
a window, no address of its own, an address-requirements block, neither limit; FEX's requests (Lowest set, or a band), a request without a
block, a hinted one and a thread without a window are not; the keys: ALLOC_INSIDE = 1 any size, JUMBO_INSIDE = 2 from 1 GB. The call sits in
NtAllocateVirtualMemoryEx after the hinted-request rejection and before the placement, and sets the guest ceiling only with a key.
"""
import os, subprocess, sys, tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
SRC = (R / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:800]) if detail and not cond else ""))
    ok &= bool(cond)


def body(sig):
    i = SRC.find(sig)
    return "" if i < 0 else SRC[i:SRC.index("\n}\n", i) + 3]


shape = body("static int ios_wow_ex_noceiling_shape( const MEM_EXTENDED_PARAMETER *parameters, ULONG count, const void *req_addr,")
on = body("static int ios_wow_ex_noceiling_on( SIZE_T size )")
ai = body("static int ios_wow_alloc_inside_on( void )")
ji = body("static int ios_wow_jumbo_inside_on( void )")
check("the helpers are there", shape and on and ai and ji)
ex = SRC[SRC.index("ios_nt_impl_NtAllocateVirtualMemoryEx( HANDLE process"):]
ex = ex[:ex.index("\n}\n")]
rej = ex.find("if (*ret && (align || limit_low || limit_high)) return STATUS_INVALID_PARAMETER;")
call = ex.find("if (ios_wow_ex_noceiling_shape( parameters, count, *ret, limit_low, limit_high ) && get_wow_user_space_limit())")
place = ex.find("allocate_virtual_memory(")
check("the call sits after the hinted-request rejection and before the placement", 0 < rej < call < place, (rej, call, place))
blk = ex[call:ex.find("/* madeira-bcd (night round 2026-11-11, R1 S1)", call)]
check("the guest ceiling is set only with a key, and both cases say so",
      "if (ios_wow_ex_noceiling_on( *size_ptr ))\n        {\n            limit_high = get_wow_user_space_limit() - 1;" in blk
      and "[wow-inside] r714" in blk and "[wow-ex] r714" in blk)

prog = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef uintptr_t ULONG_PTR; typedef size_t SIZE_T; typedef unsigned int ULONG;
enum { MemExtendedParameterAddressRequirements = 1, MemExtendedParameterAttributeFlags = 5 };
typedef struct { int Type; } MEM_EXTENDED_PARAMETER;
static ULONG_PTR the_base = 0x7900000000ULL;
static ULONG_PTR ios_wow_base( void ) { return the_base; }
@FUNCS@
int main( int argc, char **argv )
{
    MEM_EXTENDED_PARAMETER req = { MemExtendedParameterAddressRequirements }, attr = { MemExtendedParameterAttributeFlags };
    MEM_EXTENDED_PARAMETER both[2] = { { MemExtendedParameterAttributeFlags }, { MemExtendedParameterAddressRequirements } };
    int hint;
    if (argc > 1) { printf( "%d%d\n", ios_wow_ex_noceiling_on( 0x400000 ), ios_wow_ex_noceiling_on( 0x40000000 ) ); return 0; }
    printf( "%d", ios_wow_ex_noceiling_shape( &req, 1, NULL, 0, 0 ) );                   /* mimalloc: the shape */
    printf( "%d", ios_wow_ex_noceiling_shape( both, 2, NULL, 0, 0 ) );                   /* with an attribute beside it */
    printf( "%d", ios_wow_ex_noceiling_shape( &req, 1, NULL, 0xffff0000, 0 ) );          /* FEX: Lowest = HighestUserAddress */
    printf( "%d", ios_wow_ex_noceiling_shape( &req, 1, NULL, 0x7c00000000ULL, 0x7fffffffffULL ) );   /* FEX: a band */
    printf( "%d", ios_wow_ex_noceiling_shape( &req, 1, NULL, 0, 0x7fffffff ) );          /* a ceiling of its own: the round-707 path */
    printf( "%d", ios_wow_ex_noceiling_shape( &attr, 1, NULL, 0, 0 ) );                  /* no block: the 64-bit half's */
    printf( "%d", ios_wow_ex_noceiling_shape( &req, 1, &hint, 0, 0 ) );                  /* hinted */
    the_base = 0;
    printf( "%d\n", ios_wow_ex_noceiling_shape( &req, 1, NULL, 0, 0 ) );                 /* no window: a 64-bit process */
    return 0;
}
'''
tmp = Path(tempfile.mkdtemp(prefix="bcd-wow-ex-"))
(tmp / "m.c").write_text(prog.replace("@FUNCS@", ai + "\n" + ji + "\n" + shape + "\n" + on))
cc = os.environ.get("CC", "cc")
b = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-Wno-unused-function", str(tmp / "m.c"), "-o", str(tmp / "m")], capture_output=True, text=True)
check("the helpers compile on the host", b.returncode == 0, b.stdout + b.stderr)
if b.returncode == 0:
    def run(*args, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
        e.update(env)
        return subprocess.run([str(tmp / "m")] + list(args), capture_output=True, text=True, env=e).stdout.strip()
    r = run()
    check("the shape: mimalloc's block (alone or beside an attribute) yes; FEX's, a ceiling of its own, no block, hinted, no window: no",
          r == "11000000", r)
    for env, want in (({}, "00"), ({"MADEIRA_WOW_ALLOC_INSIDE": "1"}, "11"), ({"MADEIRA_WOW_JUMBO_INSIDE": "2"}, "01"),
                      ({"MADEIRA_WOW_JUMBO_INSIDE": "1"}, "00"), ({"MADEIRA_WOW_ALLOC_INSIDE": "2"}, "00")):
        check("keys %s: 4 MB / 1 GB -> %s" % (env or "none", want), run("k", **env) == want, run("k", **env))

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-wow-ex-noceiling.py" in wf)
print("PASS: VirtualAlloc2 with no ceiling from a 32-bit program" if ok else "FAILED: VirtualAlloc2 no ceiling")
sys.exit(0 if ok else 1)
