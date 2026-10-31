#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[mem-burst]: name what a sudden footprint jump is made of (round 624, agent D2 S9; Metro 2033 Redux Y1H618, CTJQ9D: +1.5 GB in 0.35 s,
then a silent kill, and no line said whether it was guest memory or graphics).

build/ntdll-unix/virtual_ios.c: the fast footprint sampler (every 250 ms between the full cycles) prints [mem-burst] when the footprint
rose by 256 MB or more since the previous fast sample, 8 lines at most: the jump, the split (internal, compressed, other), the guest's
commits since that sample (counted by ios_vm_note_alloc: number, MB, the biggest and its thread), the swap file's backed MB and the
pool's head and tail. ios_vm_note_alloc also logs a single allocation of 16 MB or more past the [valloc] 3000-line budget (256 at most).

Checks: the sampler block and the counting block cut from the source and compiled on the host with a fake task_vm_info and clock, driven
(nothing under 256 MB, the line's fields at a jump, the counters reset each sample, 8 lines at most, reserves not counted); the wiring
(the block before the plain footprint line, the counters before ios_vm_note_alloc's early returns, the 16 MB rule after them).
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


burst = src[src.index("                    {\n                        static unsigned long long burst_prev_mb;"):]
burst = burst[:burst.index("                    if (mb > fast_peak + 16)")]
note = src[src.index("static void ios_vm_note_alloc( void *base, SIZE_T size, ULONG type, ULONG protect,"):]
note = note[:note.index("\n}\n")]
count = note[note.index("    if (type & MEM_COMMIT)   /* round 624 (agent D2 S9)"):note.index("    /* ml959: a result ABOVE")]
check("the burst block runs in the fast branch, before the plain footprint line",
      src.index("ios_last_footprint_mb = mb;") < src.index("static unsigned long long burst_prev_mb;") < src.index("if (mb > fast_peak + 16)"))
check("ios_vm_note_alloc counts commits before its early returns, and the 16 MB rule comes after them",
      note.index("if (type & MEM_COMMIT)   /* round 624") < note.index("if (!log_all && !covers && is_wow64())\n    {")   # round 624: the WoW64 branch (E1 S17 a3)
      < note.index("size >= 0x1000000 && ios_vm_big_logged < 256") and '(big ? "BIG" : "#")' in note)

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
typedef uintptr_t ULONG_PTR; typedef unsigned int ULONG; typedef size_t SIZE_T;
#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
typedef struct { struct { void *UniqueProcess, *UniqueThread; } ClientId; } TEB;
static TEB teb = { { 0, (void *)0xb8 } };
static TEB *NtCurrentTeb( void ) { return &teb; }
static unsigned long long ios_burst_commit_n, ios_burst_commit_bytes, ios_burst_commit_big;
static unsigned int ios_burst_commit_big_tid;
static unsigned long long ios_swap_live_mb( void ) { return 512; }
static size_t jit_pool_offset = 300ull << 20, ios_jit_tail_reserved = 32ull << 20;
struct fvmi_t { unsigned long long phys_footprint, internal, compressed; };
static void commit( SIZE_T size, ULONG type )
{
@COUNT@
}
static void sample( unsigned long long mb, unsigned long long internal_mb, unsigned long long compressed_mb, long ms )
{
    static struct timespec clock_now;
    struct fvmi_t fvmi = { mb << 20, internal_mb << 20, compressed_mb << 20 };
    struct timespec now_ts;
    clock_now.tv_nsec += ms * 1000000L;
    while (clock_now.tv_nsec >= 1000000000L) { clock_now.tv_sec++; clock_now.tv_nsec -= 1000000000L; }
    now_ts = clock_now;
@BURST@
}
int main( void )
{
    sample( 1716, 1500, 150, 250 );                    /* the first sample: nothing to compare with */
    commit( 64 << 20, MEM_COMMIT ); commit( 8 << 20, MEM_COMMIT ); commit( 1 << 30, MEM_RESERVE );
    sample( 1801, 1600, 160, 250 );                    /* +85 MB: nothing */
    printf( "after-small n=%llu\n", ios_burst_commit_n );
    teb.ClientId.UniqueThread = (void *)0xac;
    commit( 1024ull << 20, MEM_COMMIT ); commit( 16 << 20, MEM_COMMIT );
    sample( 3689, 1772, 694, 350 );                    /* Y1H618's last step: +1888 MB in 350 ms */
    for (int i = 0; i < 12; i++) sample( 3689 + (i + 1) * 300, 1800, 700, 250 );   /* more jumps: 8 lines in all */
    return 0;
}
'''
cc = shutil.which("cc")
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / "h.c").write_text(harness.replace("@COUNT@", count).replace("@BURST@", burst.replace("dprintf( 2, ", "printf( ")))
    b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-o", str(t / "h"), str(t / "h.c")],
                       capture_output=True, text=True)
    check("the sampler's burst block and the commit counter compile on the host", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        out = subprocess.run([str(t / "h")], capture_output=True, text=True).stdout.splitlines()
        lines = [l for l in out if l.startswith("[mem-burst]")]
        check("under 256 MB nothing is printed, and each sample starts the count again", "after-small n=0" in out and
              (not lines or "+1888 MB" in lines[0]), out[:4])
        check("Y1H618's step: the jump, the split, the guest's commits since the sample (reserves not counted), swap and pool",
              bool(lines) and lines[0] == "[mem-burst] rev=624 +1888 MB in 350 ms phys=3689 internal=1772 compressed=694 other=1223 | guest "
              "commits since the last sample: n=2, 1040 MB, biggest 1024 MB tid=00ac | swap backed=512 MB | pool head=300 MB tail=32 MB",
              lines[:1])
        check("8 lines at most in a run", len(lines) == 8, len(lines))

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-mem-burst.py" in wf)
print("PASS: mem-burst" if ok else "FAIL: mem-burst")
raise SystemExit(0 if ok else 1)
