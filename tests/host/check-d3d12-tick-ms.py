#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Direct3D 12 layer's own millisecond count (build 657; madeira-d3d12/src/pe/madeira_d3d12.c, mad_tick_ms).

kernel32's GetTickCount is three instructions that read the shared data page at its Windows address
(mov w8, #0x7ffe0320; ldr w0, [x8]). iOS maps nothing below 4 GB, so in BCD every call is an exception that Wine's exception
thread serves: "[subfloor] ml956 serviced N access(es) ... latest guest 0x7ffe0320 ... insn=0xb9400100". The layer's
indirect-argument probe called GetTickCount for every indirect draw and dispatch it replayed, before it looked at its own
switch (so ind-probe = 0 did not help), and pipeline creation called it once per pipeline.
The Last of Us Part I (the owner's log of 2026-11-12 19:48, build 656): 983,041 such exceptions in 222 s; 6,000 to 15,000 a
second in play, where the game replays 4,500 to 16,800 ExecuteIndirect calls a second; the exception thread at 16-28 % of a
core, and the thread inside ExecuteCommandLists stopped for each one. An earlier log of the same build (13:00) has the
handler's own count by caller: 913,610 of 1,048,576 inside ExecuteCommandLists, 86,039 and 28,350 at pipeline creation.
Checks:
  - the source: the probe looks at its switch and its other conditions first and reads the layer's count after them; the
    count comes from the CPU's counter (mad_tick), not from GetTickCount; a pipeline's creation time and the line that
    prints its age read the same count; no GetTickCount call is left on the replay or the pipeline-creation path;
  - on the host: the count, compiled from the source, follows a sleep, never goes back, and a difference stays right
    across the 32-bit wrap;
  - the whole runtime compiles (the full tree's toolchain); CI runs this check.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


SRC = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()

# --- the source ---------------------------------------------------------------
probe = SRC[SRC.index("static void exec_indirect_probe(struct mad_exec *e, const struct mad_cmd *c) {"):]
probe = probe[:probe.index("\n}\n") + 3]
first = SRC[SRC.index("static void mad_pso_first(struct mad_pso *p) {"):]
first = first[:first.index("\n}\n") + 3]
tick = SRC[SRC.index("static inline UINT64 mad_tick(void) {"):SRC.index("/* madeira-bcd ENCODE-BATCH (bcd.cfg encode-batch, default 1;")]

check("the probe has no GetTickCount call", "GetTickCount" not in probe.replace("no longer GetTickCount()", ""))
check("the probe reads its switch and its other conditions before the clock",
      probe.index('if (on < 0) on = (int)mad_cfg_int_pe("ind-probe", 1);')
      < probe.index("if (!on || !p || (c->u.ind.off & 3) || said >= 1500 || !mad_kernels_ready(d) || !d->k_probe_pso || !d->k_ring_cpu) return;")
      < probe.index("now = mad_tick_ms();")
      < probe.index("AcquireSRWLockExclusive(&g_iprobe_lock);"))
check("...and the clock is read once, not at the declaration", probe.count("mad_tick_ms()") == 1 and "DWORD now;" in probe)
check("the probe's interval and its line use that value as before (3 s per pipeline, the record's age)",
      "now - g_iprobe[slot].t < 3000" in probe and "(unsigned long)(now - g_iprobe[slot].t)" in probe
      and "g_iprobe[slot].t = now;" in probe)
check("mad_tick_ms is the CPU counter in milliseconds (mad_tick and its frequency), a DWORD like GetTickCount",
      "static DWORD mad_tick_ms(void) { return (DWORD)(mad_tick() * 1000 / mad_tick_freq()); }" in tick)
check("...and mad_tick reads the counter in place on ARM (no call, no shared data page)",
      'mrs %0, cntvct_el0' in tick and 'mrs %0, cntfrq_el0' in tick and "0x7ffe" not in tick.split("static DWORD mad_tick_ms")[1])
check("a pipeline's creation time is the same count (graphics and compute)",
      SRC.count("p->born_present = g_presents_now; p->born_tick = mad_tick_ms();") == 2 and "born_tick = GetTickCount()" not in SRC)
check("...and the line that prints a pipeline's age subtracts from the same count",
      first.count("(unsigned long)(mad_tick_ms() - p->born_tick)") == 2 and "GetTickCount" not in first)
born_readers = [ln for ln in SRC.splitlines() if "born_tick" in ln and "DWORD born_tick" not in ln and "LONG64 born_present" not in ln]
check("every use of born_tick is one of those four lines", len(born_readers) == 4, born_readers)

# what is left: two switches that are off unless set (present-min-ms: twice a present; skip-ps: GetTickCount64)
code = "\n".join(ln.split("/*")[0] for ln in SRC.splitlines() if not ln.lstrip().startswith("*") and not ln.lstrip().startswith("/*"))
left = [ln.strip()[:110] for ln in code.splitlines() if "GetTickCount()" in ln]
check("the GetTickCount calls left are present-min-ms's two (a switch that is off unless set, once a present)",
      len(left) == 2 and all("last" in ln for ln in left), left)
replay = SRC[SRC.index("static void exec_draw(struct mad_exec *e, const struct mad_cmd *c)"):SRC.index("static void exec_indirect_probe(")]
check("no GetTickCount in the draw replay before the probe either", "GetTickCount()" not in replay)

# --- on the host ---------------------------------------------------------------
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if cc:
    prog = r'''
#include <stdio.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
typedef uint64_t UINT64; typedef int64_t LONG64; typedef uint32_t DWORD;
typedef union { LONG64 QuadPart; } LARGE_INTEGER;
static void QueryPerformanceCounter(LARGE_INTEGER *t) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); t->QuadPart = (LONG64)ts.tv_sec * 1000000000ll + ts.tv_nsec; }
static void QueryPerformanceFrequency(LARGE_INTEGER *f) { f->QuadPart = 1000000000ll; }
static LONG64 mad_qpc(void) { LARGE_INTEGER t; QueryPerformanceCounter(&t); return t.QuadPart; }
static UINT64 g_es_freq;
''' + tick + r'''
int main(void) {
    DWORD a, b, c; int i, back = 0;
    a = mad_tick_ms();
    for (i = 0; i < 200000; i++) { c = mad_tick_ms(); if ((DWORD)(c - a) > 0x80000000u) back++; }
    usleep(120000);
    b = mad_tick_ms();
    printf("%u %d %llu\n", (unsigned)(b - a), back, (unsigned long long)mad_tick_freq());
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "t.c"
        src.write_text(prog)
        exe = pathlib.Path(td) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Wno-unused-function", "-o", str(exe), str(src)], capture_output=True, text=True)
        check("the count compiles on the host from the source", r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            out = subprocess.run([str(exe)], capture_output=True, text=True).stdout.split()
            check("120 ms of sleep reads as 100-400 ms; the count never goes back in 200,000 reads; the frequency is not zero",
                  len(out) == 3 and 100 <= int(out[0]) <= 400 and out[1] == "0" and int(out[2]) > 0, out)
else:
    print("note: no host C compiler; the count was not run")

# a difference across the wrap, as the probe and the pipeline line take it (unsigned 32-bit)
def diff(now, then):
    return (now - then) & 0xffffffff
check("a difference stays right across the 32-bit wrap (49.7 days), as with GetTickCount",
      diff(0x00000010, 0xfffffff0) == 0x20 and diff(5000, 2000) == 3000 and diff(0x00000bb8, 0xffffffff) == 3001)
check("the multiplication has room: a 24 MHz counter times 1000 fits 64 bits for 24 years of uptime",
      (24_000_000 * 86400 * 365 * 24) * 1000 < 2 ** 64)

# --- the whole runtime, for syntax, with the repo's toolchain -------------------------
mingw = R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
winemetal = R / "dxmt/src/winemetal"
if mingw.exists() and winemetal.exists():
    r = subprocess.run([str(mingw), "-fsyntax-only", "-O2", "-Wall", '-DMAD_SC_CONVERTER_ID="0000000000000000"',
                        "-I" + str(R / "madeira-d3d12/src/pe"), "-I" + str(R / "madeira-d3d12/src"), "-I" + str(winemetal),
                        str(R / "madeira-d3d12/src/pe/madeira_d3d12.c")], capture_output=True, text=True)
    check("madeira_d3d12.c compiles (arm64ec, -fsyntax-only)", r.returncode == 0, r.stderr[-3000:])
else:
    print("note: llvm-mingw / dxmt not in this tree; the syntax compile runs in the full tree")

ci = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-tick-ms.py" in ci)

print("PASS: d3d12-tick-ms" if ok else "FAILED: d3d12-tick-ms")
sys.exit(0 if ok else 1)
