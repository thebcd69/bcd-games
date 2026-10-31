#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The JIT pool's range reserved at exec time (pool-bss) and the dropped mode-A floor (pool-floor = 0);
branch pool-reserve, 2026-10-18 (docs/research/open-fork-2026-10-18/playport.md §3 P1.2-P1.3).

Source contracts (every host):
  - JITAllocator.c: BCD_POOL_BSS_MB is 0 unless the build defines it, the reservation exists only
    under `#if BCD_POOL_BSS_MB > 0`, as a 16 KB-aligned zero-fill section of its own segment
    (__BCDPOOL), sized 1152-1408 MB; the image-load constructor asks it first and otherwise runs as
    before; without the define bcd_pool_reserve_at_load() is `return 0`;
  - StikJITHelper.allocatePool: without a reservation (the default build) and without pool-floor = 0
    every step it took before is unchanged (32 pin chunks, the 0x119000000 floor, the placeholder,
    the census in the same block, the same request loop); the slice is released right before the
    request and the rest given back right after it; every pool it returns passes floorAliasChecked,
    which does nothing unless pool-floor = 0; the split regions keep their guards (no gap holding
    the executable window, region C only above it);
  - build-ipa.yml passes BCD_POOL_BSS_MB to the compiler only when the variable is set and not 0,
    and runs this check.

Compiled (xcrun clang, iOS SDK): JITAllocator.c with and without the define; the default object has
no __BCDPOOL section or reservation symbol; 1152 gives a zero-fill section of exactly 1152 MB;
out-of-range sizes do not compile.

Arithmetic: for the slides of the alpha logs (iOS 27, 0.14-76.8 MB) and the largest one (80 MB), the
slice never touches the executable window, stays inside the reservation, keeps the floor when it is
on (624 MB), and gives 864 MB or more with pool-floor = 0; the parts given back are exactly the
reservation less the slice and the window (random cases too); the reservation covers the window at
every slide, and the image's end stays at least 64 MB under the lowest main-thread stack mapping.

Run on this Mac (C, cc): a harness built from JITAllocator.c's own pool-reserve functions with a real
__BCDPOOL segment declared as the app declares it. Untouched, it adds no footprint and no resident
page (compared with the same harness built without it). bcd.cfg pool-bss = 0 gives it back at load;
unset or 1 keeps it, PROT_NONE; the slice comes free and (Intel) a first-fit request lands at its
start; afterwards only the executable window is held. The RW-write/RX-read self-test passes on a
real dual mapping, fails on two unrelated mappings and puts the old bytes back.
"""
from pathlib import Path
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
app = root / "app/Madeira"
alloc = (app / "JITAllocator.c").read_text()
header = (app / "JITAllocator.h").read_text()
stik = (app / "StikJITHelper.swift").read_text()
workflow = (root / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(label, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + label + (("\n     " + detail) if detail and not cond else ""))
    ok = ok and bool(cond)


def braced(text, signature):
    start = text.index(signature)
    i = text.index("{", start)
    depth = 0
    while True:
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
        if depth == 0:
            return text[start:i]


def code(text):
    """Swift text without // comments (a // inside a string literal stays)."""
    out = []
    for line in text.splitlines():
        head = line.split("//")[0]
        out.append(line if head.count('"') % 2 else head)
    return "\n".join(out)


MB = 1 << 20
WIN, WIN_SIZE, FLOOR = 0x140000000, 0x8000000, 0x119000000

# ---- source contracts: JITAllocator.c ---------------------------------------------------------
check("BCD_POOL_BSS_MB is 0 unless the build defines it",
      "#ifndef BCD_POOL_BSS_MB\n#define BCD_POOL_BSS_MB 0\n#endif" in alloc)
gated = alloc[alloc.index("#if BCD_POOL_BSS_MB > 0"):]
gated = gated[:gated.index("#endif") + 6]
check("the reservation exists only under #if BCD_POOL_BSS_MB > 0, in a zero-fill section of its own segment, 16 KB aligned",
      '__attribute__((used, aligned(0x4000), section("__BCDPOOL,__reserve,zerofill")))' in gated
      and "unsigned char bcd_jit_pool_reservation[(unsigned long)BCD_POOL_BSS_MB << 20];" in gated
      and alloc.count("bcd_jit_pool_reservation[") == 1)
check("its size is bounded to 1152-1408 MB at compile time",
      "_Static_assert(BCD_POOL_BSS_MB >= 1152 && BCD_POOL_BSS_MB <= 1408," in gated)
check("without the define bcd_pool_reserve_at_load() returns 0",
      "#else\nstatic int bcd_pool_reserve_at_load(void) { return 0; }\n#endif" in gated)
claim = braced(alloc, "__attribute__((constructor(101), used)) static void madeira_early_va_claim(void)")
body = [l.strip() for l in claim.splitlines()[1:] if l.strip() and not l.strip().startswith(("{", "const ", "vm_address_t", "vm_size_t"))]
check("the image-load constructor asks it first, and otherwise runs as before",
      body and body[0].startswith("if (bcd_pool_reserve_at_load()) return;")
      and claim.count("bcd_pool_reserve_at_load") == 1
      and "vm_allocate(mach_task_self(), &a, winsz, VM_FLAGS_FIXED)" in claim)
keep = braced(alloc, "int bcd_pool_res_keep(unsigned long base, unsigned long size)")
check("at load: pool-bss = 0 gives it back, otherwise it is kept PROT_NONE with the window inside it (or given back when it is not)",
      'madeira_cfg_bool("pool-bss", 0)' in keep and "bcd_pool_res_load_setting == 0" in keep
      and "base + size < BCD_POOL_RES_WIN + BCD_POOL_RES_WIN_SIZE" in keep and "VM_PROT_NONE" in keep
      and "madeira_early_window_base = BCD_POOL_RES_WIN;" in keep)
check("JITAllocator.h declares the plan, rest, release, give-back, keep and self-test functions",
      all(s in header for s in ("int bcd_pool_res_plan(", "int bcd_pool_res_rest(", "int bcd_pool_res_release(",
                                "int bcd_pool_res_give_back(", "int bcd_pool_res_keep(", "int bcd_pool_rw_rx_selftest(")))

# ---- source contracts: StikJITHelper.allocatePool ---------------------------------------------
pool = code(braced(stik, "static func allocatePool("))
check("the plan is asked before the pin chunks, with the floor the census uses",
      "let reservePlan = PoolReserve.planForPool(wanted: poolSize, floor: floorOff ? 0x100000000 : 0x119000000)" in pool
      and pool.index("PoolReserve.planForPool(") < pool.index("for i in 0..<maxChunks"))
check("without pool-floor = 0: 32 pin chunks and the 0x119000000 floor, as before",
      "let maxChunks = floorOff ? 0 : 32" in pool and "let goodLow = floorOff ? 0x100000000 : 0x119000000" in pool
      and "let pinTarget: vm_address_t = 0x119000000" in pool)
check("without a plan the census block runs as it did (it only replaced `do {`)",
      "if reservePlan == nil {" in pool and pool.count("if reservePlan == nil {") == 1
      and "ml1036: free holes >=64MB in [0x%lx,0x7000000000) with the window held: " in pool)
check("the slice comes free right before the request and the rest goes back right after it",
      pool.index("if let plan = reservePlan { poolSize = PoolReserve.releaseSlice(plan) }")
      < pool.index("for attempt in 0..<3 {") < pool.index("requestRegion(poolSize, attempt: attempt)")
      < pool.index("if let plan = reservePlan { PoolReserve.finish(plan, rx: rxPtrOpt, size: poolSize) }"))
check("the request itself is unchanged (the same two requestRegion calls, placement checks and re-rolls; "
      "six in the file, as check-jit-no-debugger counts them)",
      "if a >= goodLow && !inGuestWindow && !hitsExeWindow {" in pool and pool.count("requestRegion(") == 2
      and code(stik).count("requestRegion(") == 6)
returns = re.findall(r"\n(\s*)return \(rx: rxPtr, rw: rwPtr, size: (\w+)\)", pool)
guards = re.findall(r"guard floorAliasChecked\(floorOff, rx: rxPtr, rw: rwPtr, size: (\w+)\) else \{ return nil \}[ \t]*\n\s*return \(rx: rxPtr, rw: rwPtr, size: (\w+)\)", pool)
check("every pool allocatePool returns passed floorAliasChecked first", len(returns) == 4 and len(guards) == 4
      and all(a == b for a, b in guards), str(returns))
fac = code(braced(stik, "private static func floorAliasChecked("))
check("floorAliasChecked does nothing unless pool-floor = 0; a failed self-test refuses the launch",
      "guard floorOff else { return true }" in fac and "bcd_pool_rw_rx_selftest(" in fac
      and "poolTaken = false" in fac and "poolFailure =" in fac)
plan_src = code(braced(stik, "static func planForPool("))
check("in a build without a reservation planForPool only says so (when pool-bss is set) and returns nil",
      plan_src.index("guard built != 0 else {") < plan_src.index("bcd_pool_res_give_back(")
      and re.search(r"guard built != 0 else \{\s*if on \{\s*LogStore\.shared\.log\([^}]*\}\s*return nil\s*\}", plan_src))
check("a held reservation not used by this game goes back (but the window) before anything is placed",
      "guard on else {" in plan_src and "bcd_pool_res_give_back(0, 0, BCD_POOL_RES_DROPPED)" in plan_src)
floor_src = code(braced(stik, "static func floorOff()"))
req_src = code(braced(stik, "static func requested()"))
check("pool-floor and pool-bss are read per game (the game's file wins over bcd.cfg); only 0 drops the floor, only 1 uses the reservation",
      'MadeiraConfig.gameValue("pool-floor") ?? MadeiraConfig.get("pool-floor")' in floor_src
      and '["0", "off", "no", "false"].contains(v)' in floor_src
      and 'MadeiraConfig.gameValue("pool-bss") ?? MadeiraConfig.get("pool-bss")' in req_src
      and '["1", "on", "true", "yes"].contains(v)' in req_src)
second = code(braced(stik, "private static func takeSecondRegion("))
low = code(braced(stik, "private static func takeLowRegion("))
check("the split regions keep their guards: no gap holding the window (B, M), region C only above it",
      "let gapHitsWindow = aEnd < exeWindow.base + exeWindow.size && best.base > exeWindow.base" in second
      and "guard poolRx >= exeWindow.base + exeWindow.size else {" in low)

# ---- the workflow --------------------------------------------------------------------------------
check("build-ipa.yml passes BCD_POOL_BSS_MB to the compiler only when it is set and not 0",
      'if [ -n "${BCD_POOL_BSS_MB:-}" ] && [ "${BCD_POOL_BSS_MB}" != "0" ]; then' in workflow
      and '"GCC_PREPROCESSOR_DEFINITIONS=\\$(inherited) BCD_POOL_BSS_MB=${BCD_POOL_BSS_MB}"' in workflow
      and '"${pool_bss[@]}"' in workflow)
check("build-ipa.yml runs this check", "python3 tests/host/check-pool-reserve.py" in workflow)

# ---- arithmetic: slides, the window, the stack ------------------------------------------------
# Build 571: __TEXT + __DATA_CONST + __DATA = 0x4914000 (73.1 MB) before __BCDPOOL, __LINKEDIT 0x13f0000.
# Alpha logs (iOS 27; ~/madeira-local/intake/2026-10-17): the map's lowest address (= image base) and
# the lowest mapping of the main thread's stack seen at the largest slide.
OFFSET, LINKEDIT, DYLD = 0x4918000, 0x13f0000, 6 * MB      # dyld: up to 4 MB of slide plus its own ~2 MB
SLIDES = [0x24000, 0x3dc000, 0x444000, 0x78c000, 0xb38000, 0x27e0000, 0x2940000, 0x2fec000, 0x4394000,
          0x44c0000, 0x4ccc000, 0x5000000 - 0x4000]
STACK_LOW_AT_MAX_SLIDE = 0x16b000000 - (0x5000000 - 0x4ccc000)    # 0x16b000000 at 76.8 MB, moved down to 80 MB
for size_mb in (1152, 1408):
    size = size_mb * MB
    ends = [0x100000000 + s + OFFSET + size + LINKEDIT + DYLD for s in SLIDES]
    check(f"{size_mb} MB: the window is inside the reservation at every slide and image size",
          0x100000000 + size >= WIN + WIN_SIZE and all(0x100000000 + s + OFFSET + 256 * MB <= WIN for s in SLIDES))
    check(f"{size_mb} MB: the image and dyld end at least 64 MB under the lowest main-thread stack mapping "
          f"(at 80 MB: 0x{max(ends):x} vs 0x{STACK_LOW_AT_MAX_SLIDE:x})", max(ends) + 64 * MB <= STACK_LOW_AT_MAX_SLIDE)

# ---- compiled: JITAllocator.c with and without the define (iOS SDK) ---------------------------
sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip() \
    if shutil.which("xcrun") else ""
cc = shutil.which("cc") or shutil.which("clang")
with tempfile.TemporaryDirectory() as tmpd:
    tmp = Path(tmpd)
    if sdk:
        def ios_obj(define):
            out = tmp / f"ja-{define}.o"
            r = subprocess.run(["xcrun", "clang", "-target", "arm64-apple-ios18.0", "-isysroot", sdk, "-Wall", "-Wextra",
                                "-Wno-unused-function", "-Werror", "-c", str(app / "JITAllocator.c"), "-o", str(out)]
                               + ([f"-DBCD_POOL_BSS_MB={define}"] if define is not None else []), capture_output=True, text=True)
            return r, out
        r, o = ios_obj(None)
        check("the default JITAllocator.c compiles for arm64-apple-ios18.0 (-Wall -Wextra -Werror)", r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            lc = subprocess.run(["otool", "-l", str(o)], capture_output=True, text=True).stdout
            sym = subprocess.run(["nm", str(o)], capture_output=True, text=True).stdout
            check("the default object has no __BCDPOOL section and no reservation symbol",
                  "__BCDPOOL" not in lc and "bcd_jit_pool_reservation" not in sym)
        r, o = ios_obj(1152)
        check("with BCD_POOL_BSS_MB=1152 it compiles (-Wall -Wextra -Werror)", r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            lc = subprocess.run(["otool", "-l", str(o)], capture_output=True, text=True).stdout
            m = re.search(r"sectname __reserve\s+segname __BCDPOOL\s+addr \S+\s+size (0x[0-9a-f]+)\s+offset \S*\s+align (\S+)[^\n]*\n"
                          r"\s+reloff \S+\s+nreloc \S+\s+flags (0x[0-9a-f]+)", lc)
            check("...as one zero-fill section (S_ZEROFILL) of 1152 MB, 16 KB aligned, in segment __BCDPOOL",
                  m is not None and int(m.group(1), 16) == 1152 * MB and m.group(2) == "2^14" and int(m.group(3), 16) & 0xff == 1,
                  lc[lc.find("__reserve") - 40:lc.find("__reserve") + 400] if m is None else str(m.groups()))
        # Linked for the device: the segment's place and alignment in a real arm64 iOS executable.
        stub = tmp / "stub.c"
        stub.write_text("int main(void) { return 0; }\n")

        def ios_link(obj, name):
            exe = tmp / name
            r = subprocess.run(["xcrun", "clang", "-target", "arm64-apple-ios18.0", "-isysroot", sdk, str(stub), str(obj),
                                "-framework", "CoreFoundation", "-o", str(exe)], capture_output=True, text=True)
            if r.returncode:
                return None, r.stderr
            lc = subprocess.run(["otool", "-l", str(exe)], capture_output=True, text=True).stdout
            return re.findall(r"segname (\S+)\n\s+vmaddr (0x[0-9a-f]+)\n\s+vmsize (0x[0-9a-f]+)\n\s+fileoff \S+\n\s+filesize (\d+)", lc), ""
        segs, err = ios_link(tmp / "ja-1152.o", "ios-1152")
        names = [s[0] for s in segs or []]
        pool_seg = [s for s in segs or [] if s[0] == "__BCDPOOL"]
        check("linked for arm64 iOS: __BCDPOOL right after __DATA, before __LINKEDIT, 16 KB aligned, 1152 MB, no file bytes",
              segs and "__BCDPOOL" in names and names.index("__BCDPOOL") == names.index("__DATA") + 1
              and names[names.index("__BCDPOOL") + 1] == "__LINKEDIT" and int(pool_seg[0][1], 16) % 0x4000 == 0
              and int(pool_seg[0][2], 16) == 1152 * MB and pool_seg[0][3] == "0", str(segs) + err[-1500:])
        segs, err = ios_link(tmp / "ja-None.o", "ios-default")
        check("linked for arm64 iOS without the define: no __BCDPOOL segment",
              segs and "__BCDPOOL" not in [s[0] for s in segs], str(segs) + err[-1500:])
        for bad in (1000, 1500):
            r, _ = ios_obj(bad)
            check(f"BCD_POOL_BSS_MB={bad} does not compile (out of 1152-1408)", r.returncode != 0 and "BCD_POOL_BSS_MB" in r.stderr)
    else:
        print("SKIP: no iOS SDK for the device compiles")

    # ---- run on this Mac: the harness ------------------------------------------------------------
    if not cc or sys.platform != "darwin":
        print("SKIP: the harness needs macOS and a C compiler")
    else:
        start = alloc.index("#ifndef BCD_POOL_BSS_MB")
        block = alloc[start:alloc.index("/* ml1249: METAL API VALIDATION ON DEMAND.")]
        harness = r'''
#include "JITAllocator.h"
#include "madeira_cfg.h"
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>
unsigned long madeira_early_window_base, madeira_early_window_size;
''' + block + r'''
static int failed;
#define EXPECT(c, ...) do { if (c) { printf("ok   "); } else { printf("FAIL "); failed++; } printf(__VA_ARGS__); printf("\n"); } while (0)
static uint64_t footprint(void)
{
    task_vm_info_data_t v; mach_msg_type_number_t n = TASK_VM_INFO_COUNT;
    return task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&v, &n) == KERN_SUCCESS ? v.phys_footprint : 0;
}
/* 1: [a, a+len) is free; 0: something maps a part of it */
static int is_free(unsigned long a, unsigned long len)
{
    mach_vm_address_t q = a; mach_vm_size_t s = 0; vm_region_basic_info_data_64_t i; mach_port_t o = MACH_PORT_NULL;
    mach_msg_type_number_t n = VM_REGION_BASIC_INFO_COUNT_64;
    if (mach_vm_region(mach_task_self(), &q, &s, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&i, &n, &o) != KERN_SUCCESS) return 1;
    return q >= a + len;
}
/* the protection of the mapping at a, or -1 when a is not mapped */
static int prot_at(unsigned long a)
{
    mach_vm_address_t q = a; mach_vm_size_t s = 0; vm_region_basic_info_data_64_t i; mach_port_t o = MACH_PORT_NULL;
    mach_msg_type_number_t n = VM_REGION_BASIC_INFO_COUNT_64;
    if (mach_vm_region(mach_task_self(), &q, &s, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&i, &n, &o) != KERN_SUCCESS || q > a) return -1;
    return i.protection;
}
int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "";
    setvbuf(stdout, NULL, _IONBF, 0);
    if (!strcmp(mode, "footprint")) {
#if BCD_POOL_BSS_MB > 0
        unsigned long resident = 0;
        static char vec[((unsigned long)BCD_POOL_BSS_MB << 20) / 4096];
        if (mincore(bcd_jit_pool_reservation, sizeof(bcd_jit_pool_reservation), vec) == 0)
            for (unsigned long i = 0; i < sizeof vec; i++) resident += vec[i] & 1;
        printf("reservation 0x%lx+%luMB resident_pages=%lu prot=%d\n", (unsigned long)bcd_jit_pool_reservation,
               (unsigned long)(sizeof(bcd_jit_pool_reservation) >> 20), resident, prot_at((unsigned long)bcd_jit_pool_reservation));
#endif
        printf("footprint=%llu\n", (unsigned long long)footprint());
        return 0;
    }
#if BCD_POOL_BSS_MB > 0
    unsigned long base = (unsigned long)bcd_jit_pool_reservation;
    const unsigned long size = sizeof(bcd_jit_pool_reservation);
    /* x86_64 caps a segment's alignment at 4 KB (ld: "reducing alignment"), while the compiler assumes the
     * declared 16 KB and would fold the plan's page rounding away; arm64 iOS keeps 16 KB (checked above). */
    __asm__ volatile("" : "+r"(base));
    int kept = bcd_pool_reserve_at_load();
    if (!strcmp(mode, "off")) {
        EXPECT(!kept && bcd_pool_res_state == BCD_POOL_RES_RELEASED && bcd_pool_res_load_setting == 0 && is_free(base, size)
               && madeira_early_window_base == 0,
               "bcd.cfg pool-bss = 0: given back at load, the range is free (state %d)", bcd_pool_res_state);
        return failed ? 1 : 0;
    }
    EXPECT(kept && bcd_pool_res_state == BCD_POOL_RES_HELD && prot_at(base) == VM_PROT_NONE && prot_at(WIN_ADDR) == VM_PROT_NONE
           && madeira_early_window_base == BCD_POOL_RES_WIN && madeira_early_window_size == BCD_POOL_RES_WIN_SIZE,
           "%s: kept at load, PROT_NONE, the window inside it is the one ml1040 would hold (0x%lx+%luMB)",
           mode, base, size >> 20);
    EXPECT(bcd_pool_res_stack_hi > bcd_pool_res_stack_lo && bcd_pool_res_image_end > base + size,
           "the layout notes: image end 0x%lx, main thread stack [0x%lx,0x%lx)", bcd_pool_res_image_end,
           bcd_pool_res_stack_lo, bcd_pool_res_stack_hi);
    unsigned long floor_addr = !strcmp(mode, "floor") ? 0x119000000ul : 0x100000000ul;
    bcd_pool_res_plan_t plan;
    int have = bcd_pool_res_plan(base, size, BCD_POOL_RES_WIN, BCD_POOL_RES_WIN_SIZE, floor_addr, 896ul << 20,
                                 64ul << 20, 256ul << 20, &plan);
    EXPECT(have && plan.slice_base >= base && plan.slice_base >= floor_addr && plan.slice_base + plan.slice_size <= BCD_POOL_RES_WIN
           && !(plan.slice_base & 0x3fff) && !(plan.slice_size & 0x3fff),
           "the plan: slice [0x%lx,0x%lx), pool %luMB", plan.slice_base, plan.slice_base + plan.slice_size, plan.pool_size >> 20);
    if (!have) return 1;
    EXPECT(bcd_pool_res_release(plan.slice_base, plan.slice_size) == 0 && is_free(plan.slice_base, plan.slice_size)
           && prot_at(WIN_ADDR) == VM_PROT_NONE, "the slice comes free; the rest and the window stay held");
    mach_vm_address_t got = 0;
    kern_return_t kr = mach_vm_allocate(mach_task_self(), &got, plan.pool_size, VM_FLAGS_ANYWHERE);
    int inside = kr == KERN_SUCCESS && got >= plan.slice_base && got + plan.pool_size <= plan.slice_base + plan.slice_size;
#if defined(__x86_64__)
    EXPECT(inside && got == plan.slice_base, "a first-fit request of the pool's size (as the debugger's _M) lands at the slice's start: 0x%llx",
           (unsigned long long)got);
#else
    printf("info first-fit request landed at 0x%llx (%s the slice)\n", (unsigned long long)got, inside ? "inside" : "outside");
#endif
    int bad = bcd_pool_res_give_back(plan.slice_base, plan.slice_size, BCD_POOL_RES_USED);
    int window_held = prot_at(WIN_ADDR) == VM_PROT_NONE && prot_at(WIN_ADDR + WIN_LEN - 0x4000) == VM_PROT_NONE;
    int rest_free = (plan.slice_base == base || is_free(base, plan.slice_base - base))
                    && is_free(WIN_ADDR + WIN_LEN, base + size - (WIN_ADDR + WIN_LEN));
    int pool_kept = kr == KERN_SUCCESS && prot_at(got) >= 0;
    EXPECT(!bad && bcd_pool_res_state == BCD_POOL_RES_USED && window_held && rest_free && pool_kept,
           "afterwards only the window is held of it; the pool region is untouched (bad=%d window=%d rest=%d pool=%d)",
           bad, window_held, rest_free, pool_kept);
    if (kr == KERN_SUCCESS) mach_vm_deallocate(mach_task_self(), got, plan.pool_size);
#endif
    if (!strcmp(mode, "selftest")) {
        char d[200];
        mach_port_t e = MACH_PORT_NULL; memory_object_size_t es = 0x10000;
        vm_address_t rx = 0, rw = 0, other = 0;
        kern_return_t k1 = mach_make_memory_entry_64(mach_task_self(), &es, 0, MAP_MEM_NAMED_CREATE | VM_PROT_READ | VM_PROT_WRITE, &e, MACH_PORT_NULL);
        kern_return_t k2 = vm_map(mach_task_self(), &rx, 0x10000, 0, VM_FLAGS_ANYWHERE, e, 0, FALSE, VM_PROT_READ, VM_PROT_READ | VM_PROT_WRITE, VM_INHERIT_NONE);
        kern_return_t k3 = vm_map(mach_task_self(), &rw, 0x10000, 0, VM_FLAGS_ANYWHERE, e, 0, FALSE, VM_PROT_READ | VM_PROT_WRITE, VM_PROT_READ | VM_PROT_WRITE, VM_INHERIT_NONE);
        if (k1 || k2 || k3) { EXPECT(0, "dual mapping for the self-test (%d %d %d)", k1, k2, k3); return 1; }
        ((volatile uint64_t *)rw)[1] = 0x1122334455667788ull;
        ((volatile uint64_t *)rw)[(0x10000 - 16) / 8] = 0x8877665544332211ull;
        EXPECT(bcd_pool_rw_rx_selftest((void *)rx, (void *)rw, 0x10000, d, sizeof d), "the self-test passes on a real RW/RX pair: %s", d);
        EXPECT(((volatile uint64_t *)rx)[1] == 0x1122334455667788ull && ((volatile uint64_t *)rx)[(0x10000 - 16) / 8] == 0x8877665544332211ull,
               "...and puts the old bytes back");
        vm_allocate(mach_task_self(), &other, 0x10000, VM_FLAGS_ANYWHERE);
        EXPECT(!bcd_pool_rw_rx_selftest((void *)rx, (void *)other, 0x10000, d, sizeof d) && strstr(d, "read"),
               "it fails when RW is not RX's alias: %s", d);
        EXPECT(!bcd_pool_rw_rx_selftest((void *)rx, (void *)rx, 0x10000, d, sizeof d), "it fails without an alias: %s", d);
    }
    return failed ? 1 : 0;
}
'''
        harness = harness.replace("WIN_ADDR", "0x140000000ul").replace("WIN_LEN", "0x8000000ul")
        src = tmp / "poolres.c"
        src.write_text(harness)
        (tmp / "JITAllocator.h").write_text(header)
        (tmp / "madeira_cfg.h").write_text((root / "build/madeira_cfg.h").read_text())

        def build(name, define):
            exe = tmp / name
            b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-O1", "-I", str(tmp),
                                f"-DBCD_POOL_BSS_MB={define}", str(src), "-o", str(exe)], capture_output=True, text=True)
            check(f"the harness builds for this Mac (BCD_POOL_BSS_MB={define}, -Wall -Wextra -Werror)", b.returncode == 0, b.stderr[-3000:])
            return exe if b.returncode == 0 else None

        with_res, without = build("with", 1152), build("without", 0)
        docs = tmp / "docs"
        docs.mkdir()

        def run(exe, mode, cfg):
            cfgfile = docs / "bcd.cfg"
            if cfg is None:
                cfgfile.unlink(missing_ok=True)
            else:
                cfgfile.write_text(cfg)
            env = dict(os.environ, MADEIRA_DOCS_DIR=str(docs))
            env.pop("MADEIRA_CFG_GAME", None)
            return subprocess.run([str(exe), mode], capture_output=True, text=True, timeout=120, env=env)

        if with_res and without:
            lc = subprocess.run(["otool", "-l", str(with_res)], capture_output=True, text=True).stdout
            segs = re.findall(r"segname (\S+)\n\s+vmaddr (0x[0-9a-f]+)\n\s+vmsize (0x[0-9a-f]+)\n\s+fileoff \S+\n\s+filesize (\d+)", lc)
            names = [s[0] for s in segs]
            pool_seg = [s for s in segs if s[0] == "__BCDPOOL"]
            check("in the linked harness __BCDPOOL sits right after __DATA and before __LINKEDIT, with no file bytes",
                  "__BCDPOOL" in names and names.index("__BCDPOOL") == names.index("__DATA") + 1
                  and names[names.index("__BCDPOOL") + 1] == "__LINKEDIT"
                  and pool_seg and int(pool_seg[0][2], 16) == 1152 * MB and pool_seg[0][3] == "0", str(segs))
            check("the harness file stays small (the zero fill takes no file space)", with_res.stat().st_size < 4 * MB,
                  str(with_res.stat().st_size))
            fp = {}
            for label, exe in (("with", with_res), ("without", without)):
                outs = [run(exe, "footprint", None).stdout for _ in range(3)]
                fp[label] = min(int(re.search(r"footprint=(\d+)", o).group(1)) for o in outs)
                if label == "with":
                    m = re.search(r"resident_pages=(\d+) prot=(-?\d+)", outs[0])
                    check("untouched, none of its pages is resident (mincore over all 1152 MB)", m and m.group(1) == "0", outs[0])
            check(f"untouched, it adds no footprint: {fp['with'] >> 10} KB with it, {fp['without'] >> 10} KB without",
                  abs(fp["with"] - fp["without"]) < 2 * MB)
            for mode, cfg, label in (("off", "pool-bss = 0\n", "pool-bss = 0"), ("unset", None, "no bcd.cfg"),
                                     ("unset", "pool-bss = 1\n", "pool-bss = 1"), ("floor", "pool-bss = 1\n", "pool-bss = 1, floor on")):
                r = run(with_res, mode, cfg)
                print("\n".join("     " + l for l in r.stdout.splitlines() if l.startswith(("ok", "FAIL", "info"))))
                check(f"on this Mac, {label}: as described", r.returncode == 0 and "FAIL" not in r.stdout, r.stdout[-2000:] + r.stderr[-500:])
            r = run(without, "selftest", None)
            print("\n".join("     " + l for l in r.stdout.splitlines() if l.startswith(("ok", "FAIL"))))
            check("the RW->RX self-test behaves on this Mac", r.returncode == 0 and "FAIL" not in r.stdout, r.stdout[-2000:] + r.stderr[-500:])

            # the pure arithmetic, compiled from the same functions
            arith = tmp / "arith.c"
            arith.write_text(harness.split("static int failed;")[0] + r'''
int main(void)
{
    unsigned long out[3][2]; bcd_pool_res_plan_t p; char line[256];
    while (fgets(line, sizeof line, stdin)) {
        unsigned long a[8];
        if (sscanf(line, "plan %lx %lx %lx %lx %lx %lx %lx %lx", &a[0], &a[1], &a[2], &a[3], &a[4], &a[5], &a[6], &a[7]) == 8) {
            int r = bcd_pool_res_plan(a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], &p);
            printf("%d %lx %lx %lx\n", r, p.slice_base, p.slice_size, p.pool_size);
        } else if (sscanf(line, "rest %lx %lx %lx %lx %lx %lx", &a[0], &a[1], &a[2], &a[3], &a[4], &a[5]) == 6) {
            int n = bcd_pool_res_rest(a[0], a[1], a[2], a[3], a[4], a[5], out);
            printf("%d", n);
            for (int i = 0; i < n; i++) printf(" %lx %lx", out[i][0], out[i][1]);
            printf("\n");
        }
        fflush(stdout);
    }
    return 0;
}
''')
            b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-I", str(tmp), "-DBCD_POOL_BSS_MB=0",
                                str(arith), "-o", str(tmp / "arith")], capture_output=True, text=True)
            check("the plan/rest arithmetic builds", b.returncode == 0, b.stderr[-2000:])
            if b.returncode == 0:
                queries, cases = [], []
                for s in SLIDES:
                    base = 0x100000000 + s + OFFSET
                    for floor_on in (True, False):
                        for want in (512 * MB, 896 * MB):
                            queries.append("plan %x %x %x %x %x %x %x %x" % (base, 1152 * MB, WIN, WIN_SIZE,
                                                                             FLOOR if floor_on else 0x100000000, want, 64 * MB, 256 * MB))
                            cases.append((s, base, floor_on, want))
                rnd = random.Random(1018)
                rests = []
                for _ in range(400):
                    rb = 0x100000000 + rnd.randrange(0, 0x8000000, 0x4000)
                    rs = rnd.randrange(64 * MB, 1400 * MB, 0x4000)
                    if rnd.random() < 0.8:
                        sb = rnd.randrange(rb, rb + rs, 0x4000)
                        ss = rnd.randrange(0, rb + rs - sb + 1, 0x4000)
                    else:
                        sb, ss = 0, 0
                    rests.append((rb, rs, sb, ss))
                    queries.append("rest %x %x %x %x %x %x" % (rb, rs, WIN, WIN_SIZE, sb, ss))
                r = subprocess.run([str(tmp / "arith")], input="\n".join(queries) + "\n", capture_output=True, text=True, timeout=60)
                lines = r.stdout.splitlines()
                bad = []
                for (s, base, floor_on, want), l in zip(cases, lines):
                    got, sb, ss, ps = [int(x, 16) for x in l.split()]
                    if not got:
                        bad.append(f"slide {s:#x} floor {floor_on} want {want >> 20}: no plan")
                        continue
                    se = sb + ss
                    if not (base <= sb and se <= WIN and ps <= ss and ps % 0x4000 == 0):
                        bad.append(f"slide {s:#x}: slice [{sb:#x},{se:#x}) outside the reservation or into the window")
                    if floor_on and (sb != FLOOR or ps != min(want, 624 * MB) or (want == 896 * MB and se != WIN)):
                        bad.append(f"slide {s:#x}: with the floor the slice is [{sb:#x},{se:#x}) pool {ps >> 20} MB")
                    if not floor_on and (sb != base or ps < min(want, 864 * MB) or ss > ps + 64 * MB):
                        bad.append(f"slide {s:#x}: pool-floor = 0 gives [{sb:#x},{se:#x}) pool {ps >> 20} MB")
                check("the slice for every slide of the alpha logs and 80 MB: inside the reservation, never the window; "
                      "624 MB from 0x119000000 with the floor, 864 MB or more from the reservation's start without it",
                      not bad and len(lines) >= len(cases), "; ".join(bad[:6]))
                bad = []
                for (rb, rs, sb, ss), l in zip(rests, lines[len(cases):]):
                    f = [int(x, 16) for x in l.split()]
                    parts = [(f[1 + 2 * i], f[2 + 2 * i]) for i in range(f[0])]
                    want = set()
                    for a in range(rb, rb + rs, 0x4000 * 64):
                        in_slice = ss and sb <= a < sb + ss
                        in_win = WIN <= a < WIN + WIN_SIZE
                        if not in_slice and not in_win:
                            want.add(a)
                    have = {a for a in range(rb, rb + rs, 0x4000 * 64) if any(p <= a < p + n for p, n in parts)}
                    overlap = any(p < q + m and q < p + n for i, (p, n) in enumerate(parts) for (q, m) in parts[i + 1:])
                    outside = any(p < rb or p + n > rb + rs or (ss and p < sb + ss and sb < p + n) or (p < WIN + WIN_SIZE and WIN < p + n)
                                  for p, n in parts)
                    if want != have or overlap or outside:
                        bad.append(f"res {rb:#x}+{rs >> 20}MB slice {sb:#x}+{ss >> 20}MB -> {parts}")
                check("what goes back is the reservation less the slice and the window, nothing else (400 random cases)",
                      not bad and len(lines) == len(queries), "; ".join(bad[:4]))

print("check-pool-reserve: PASS" if ok else "check-pool-reserve: FAILED")
sys.exit(0 if ok else 1)
