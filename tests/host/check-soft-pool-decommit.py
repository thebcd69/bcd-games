#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Decommit and recommit inside a soft pool (round 624, agent E1 S2; BeamNG.drive, Send Log PZFK31).

PartitionAlloc decommitted 8 KB of a soft-pool page BCD had materialised ("[vfree] base=0x780003c000 size=0x2000 type=0x4000"),
NtFreeVirtualMemory found no view and answered c00000a0 ("[free-refused]"), and PartitionAlloc crashed on purpose. Now each soft
pool marks the host pages it materialised itself; a decommit of such pages (no view there) zeroes them, a whole host page by a
fresh mapping without access, a part of one in place, and a recommit gives them the asked protection again.

Checks: ios_soft_mark, ios_soft_marked and ios_soft_decommit cut from build/ntdll-unix/virtual_ios.c and compiled on the host
(16 KB host pages over the real mmap and mach_vm_region): PZFK31's 8 KB inside a page is zeroed in place and the rest of the page
keeps its bytes; a whole page loses access; a page decommitted whole and decommitted in part again is left alone; a range with a
page the pool did not materialise, an address outside every soft pool, a soft cage, a pool without a bitmap: refused, nothing
touched. The wiring: the materialise branch marks, the recommit comes before the COLLISION branch and needs the marks and a
successful mprotect, NtFreeVirtualMemory tries it only for MEM_DECOMMIT where no view exists, under the memory lock, after the thin
reservation's fixup.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def fn(start):
    a = src.index(start)
    return src[a:src.index("\n}\n", a) + 3]


mat = src[src.index('dprintf(2, "[soft-pool] materialised 0x%llx+0x%lx in soft 0x%llx -> %p"'):]
mat = mat[:mat.index("return STATUS_SUCCESS;") + 30]
check("the materialise branch marks its pages before it returns", "ios_soft_mark( si, want, need );" in mat)
branch = src[src.index("if ((type & MEM_COMMIT) && ios_soft_n && *ret)"):]
branch = branch[:branch.index('dprintf(2, "[soft-pool] COLLISION: commit')]
check("the recommit: before the COLLISION branch, only for the pool's own pages and only when mprotect succeeded",
      "else if (occupied && ios_soft_marked( si, want, need ) && ({" in branch and "!mprotect( (void *)(uintptr_t)want, need," in branch
      and branch.index("else if (occupied && ios_soft_marked(") < branch.index("else if (occupied)\n"))
free = src[src.index("NTSTATUS WINAPI NtFreeVirtualMemory( HANDLE process, PVOID *addr_ptr, SIZE_T *size_ptr, ULONG type )"):]
free = free[:free.index("\n}\n")]
call = "if (ios_soft_n && type == MEM_DECOMMIT && base && size && !find_view( base, 0 ) && ios_soft_decommit( base, size ))"
check("NtFreeVirtualMemory: MEM_DECOMMIT where no view exists, under the memory lock, after the thin reservation's fixup",
      free.count(call) == 1 and free.index("server_enter_uninterrupted_section( &virtual_mutex, &sigset );") < free.index(call)
      and free.index("ios_thin_decommit_fixup( base, &size )") < free.index(call)
      < free.index("else if (!(view = find_view( base, 0 ))) status = STATUS_MEMORY_NOT_ALLOCATED;")
      and "server_leave_uninterrupted_section( &virtual_mutex, &sigset );\n        return STATUS_SUCCESS;" in free[free.index(call):free.index(call) + 400])

helpers = src[src.index("static uint64_t *ios_soft_bits[IOS_SOFT_MAX];"):src.index("static inline __attribute__((always_inline)) NTSTATUS ios_nt_impl_NtAllocateVirtualMemory(")]
find = fn("static int ios_soft_find( uint64_t addr )")
harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
typedef size_t SIZE_T;
#define IOS_SOFT_MAX 8
static struct { uint64_t base, size, committed; unsigned commits, collisions; unsigned cage; } ios_soft[IOS_SOFT_MAX];
static unsigned ios_soft_n;
/* <mach/mach.h> declares a function host_page_size(); the engine's variable of that name is renamed here */
#define host_page_size test_host_page_size
#define host_page_mask test_host_page_mask
static const uint64_t test_host_page_size = 0x4000, test_host_page_mask = 0x3fff;
void *anon_mmap_fixed( void *start, size_t size, int prot, int flags )
{ return mmap( start, size, prot, MAP_PRIVATE | MAP_ANON | MAP_FIXED | flags, -1, 0 ); }
@FIND@
@HELPERS@
static int prot_of( uint64_t a )
{
  mach_vm_address_t q = a; mach_vm_size_t sz = 0; vm_region_basic_info_data_64_t inf; mach_msg_type_number_t cnt = VM_REGION_BASIC_INFO_COUNT_64;
  mach_port_t obj = MACH_PORT_NULL;
  if (mach_vm_region( mach_task_self(), &q, &sz, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&inf, &cnt, &obj ) != KERN_SUCCESS || q > a) return -1;
  return inf.protection;
}
int main( void )
{
  /* a 1 MB soft pool, 16 KB aligned; the first 48 KB materialised (RW, 0xAA), the next 16 KB mapped but not ours */
  char *raw = mmap( NULL, 0x110000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0 );
  uint64_t pool = ((uint64_t)(uintptr_t)raw + 0x3fff) & ~0x3fffULL;
  ios_soft[0].base = pool; ios_soft[0].size = 0x100000; ios_soft_n = 1;
  memset( (void *)pool, 0xAA, 0x10000 );
  printf( "nobits %d\n", ios_soft_decommit( (char *)pool, 0x2000 ) );
  ios_soft_mark( 0, pool, 0xc000 );
  printf( "marked %d %d %d\n", ios_soft_marked( 0, pool, 0xc000 ), ios_soft_marked( 0, pool + 0x8000, 0x4000 ),
          ios_soft_marked( 0, pool + 0x8000, 0x8000 ) );
  /* PZFK31: 8 KB inside the first host page */
  printf( "part %d", ios_soft_decommit( (char *)(pool + 0x2000), 0x2000 ) );
  printf( " zero=%d kept=%d after=%d prot=%d\n", ((unsigned char *)pool)[0x2000] == 0 && ((unsigned char *)pool)[0x3fff] == 0,
          ((unsigned char *)pool)[0x1fff] == 0xAA, ((unsigned char *)pool)[0x4000] == 0xAA, prot_of( pool ) );
  /* the second host page whole */
  printf( "whole %d prot=%d\n", ios_soft_decommit( (char *)(pool + 0x4000), 0x4000 ), prot_of( pool + 0x4000 ) );
  /* part of that page again: no write to a page without access */
  printf( "again %d prot=%d\n", ios_soft_decommit( (char *)(pool + 0x5000), 0x1000 ), prot_of( pool + 0x4000 ) );
  /* a range that reaches the page the pool did not materialise */
  ((unsigned char *)pool)[0xc000] = 0x55;
  printf( "unmarked %d byte=%x third=%x\n", ios_soft_decommit( (char *)(pool + 0x8000), 0x8000 ), ((unsigned char *)pool)[0xc000],
          ((unsigned char *)pool)[0x8000] );
  /* outside every soft pool */
  printf( "outside %d\n", ios_soft_decommit( (char *)(pool + 0x200000), 0x2000 ) );
  /* a soft cage */
  ios_soft[0].cage = 1;
  printf( "cage %d\n", ios_soft_decommit( (char *)(pool + 0x8000), 0x2000 ) );
  ios_soft[0].cage = 0;
  /* a recommit's protection: mprotect on the page the whole decommit left without access */
  printf( "recommit %d prot=%d zero=%d\n", mprotect( (void *)(pool + 0x4000), 0x4000, PROT_READ | PROT_WRITE ), prot_of( pool + 0x4000 ),
          ((unsigned char *)pool)[0x4000] == 0 && ((unsigned char *)pool)[0x7fff] == 0 );
  return 0;
}
'''
cc = shutil.which("cc")
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    code = harness.replace("@FIND@", find).replace("@HELPERS@", helpers.replace("dprintf( 2, ", "printf( "))
    (t / "h.c").write_text(code)
    b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the three helpers compile on the host (real mmap and mach_vm_region, 16 KB host pages)", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        r = subprocess.run([str(t / "h")], capture_output=True, text=True)
        out = dict(l.split(" ", 1) for l in r.stdout.splitlines() if " " in l and not l.startswith("[soft-pool]"))
        logs = [l for l in r.stdout.splitlines() if l.startswith("[soft-pool]")]
        check("a pool without a bitmap (nothing materialised): refused", out.get("nobits") == "0", r.stdout)
        check("the marks: the materialised pages yes, a range reaching past them no", out.get("marked") == "1 1 0", r.stdout)
        check("PZFK31's 8 KB inside a page: zeroed in place, the rest of the page keeps its bytes, still writable",
              out.get("part") == "1 zero=1 kept=1 after=1 prot=3", r.stdout)
        check("a whole host page: no access afterwards (as a decommitted page)", out.get("whole") == "1 prot=0", r.stdout)
        check("part of a page decommitted whole before: done without a write", out.get("again") == "1 prot=0", r.stdout)
        check("a range with a page the pool did not materialise: refused, nothing touched",
              out.get("unmarked") == "0 byte=55 third=aa", r.stdout)
        check("outside every soft pool, or a soft cage: refused", out.get("outside") == "0" and out.get("cage") == "0", r.stdout)
        check("a recommit's mprotect gives the page back, zero", out.get("recommit") == "0 prot=3 zero=1", r.stdout)
        check("each decommit says so (8 lines at most)", len(logs) == 3 and all("(zeroed, kept; round 624)" in l for l in logs), logs)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-soft-pool-decommit.py" in wf)
print("PASS: soft-pool decommit" if ok else "FAIL: soft-pool decommit")
raise SystemExit(0 if ok else 1)
