#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A fault a system call's own __TRY answers is never counted as a livelock (round 630, agent O2 S2).

Mighty No. 9 (DT9961, build 626), Outlast (92JTXE 614, KT2K8F 623) and Satisfactory (GEZ8HT 614) call ReadProcessMemory on a bad
source again and again; NtReadVirtualMemory's __TRY makes each call fail (STATUS_PARTIAL_COPY, as on Windows), but BCD's two loop
guards counted the repeats: the Mach thread's "[fault-stuck] BREAKING LOOP" (50 repeats) diverted the game's thread to abort_thread,
and segv_handler's "SEGV LOOP FATAL" (5 repeats) skipped the instruction and returned 0xDEAD0001, a corrupted success.

Checks, source-level (build/ntdll-unix/signal_arm64_ios.c; the iOS syntax build is tests/host/check-rwx-fault-diag.py's): the Mach
helper reads jmp_buf, kernel_stack and syscall_frame of the faulting thread's TEB through the kernel at their offsetof() positions
(no hand-written offsets) and answers only for jmp_buf set and kernel_stack <= sp <= syscall_frame; the Mach breaker asks it before
any counting and then only starts a new run; segv_handler asks the same question (NtCurrentTeb, jmp_buf, is_inside_syscall) before its
repeat count and then resets the run; both breakers still exist for every other fault; the auto-tune rule; CI.
"""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1200]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


h = src[src.index("static int ios_mach_syscall_try_active( uintptr_t teb, uint64_t sp )"):]
h = h[:h.index("\n}\n")]
check("the Mach helper reads the three fields at their offsetof() positions through the kernel",
      "const uint64_t base = (uint64_t)teb + offsetof( TEB, GdiTebBatch );" in h
      and all("ios_exc_read64( base + offsetof( struct ntdll_thread_data, %s )" % f in h for f in ("jmp_buf", "kernel_stack", "syscall_frame"))
      and "return ks < frame && sp >= ks && sp <= frame;" in h and "|| !jb) return 0;" in h)
check("the helper sits before the Mach exception thread", src.index("static int ios_mach_syscall_try_active(") < src.index("static void *ios_mach_exception_thread( void *arg )"))
brk = src[src.index("if (ios_mach_syscall_try_active( thread_teb, __darwin_arm_thread_state64_get_sp(state) ))"):]
brk = brk[:brk.index("[fault-stuck] BREAKING LOOP")]
check("the Mach breaker asks it before counting, and then only starts a new run",
      brk.index("stuck_page = 0;") < brk.index("if (fpage == stuck_page && fault_pc_check == stuck_pc &&")
      and brk.count("uint32_t n = __sync_add_and_fetch(&stuck_n, 1);") == 1)
seg = src[src.index("static uintptr_t last_fault_pc = 0;"):]
seg = seg[:seg.index("SEGV LOOP FATAL: pc=%p")]
check("segv_handler asks the same question before its repeat count and resets the run",
      "if (NtCurrentTeb() && ntdll_get_thread_data()->jmp_buf && is_inside_syscall( SP_sig(context) ))\n        {\n            *lastp = 0;\n            *cntp = 0;\n        }\n        else if (this_pc == *lastp)" in seg)
check("both breakers still exist for every other fault", "[fault-stuck] BREAKING LOOP: page 0x%llx pc 0x%llx repeated %u times" in src
      and 'ERR("SEGV LOOP FATAL: pc=%p addr=%p after %d repeats, forcing thread exit\\n",' in src)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names it fixed in 630", '"id": "syscall-fault-loop-guard"' in rules)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-syscall-fault-loop.py" in wf)
print("PASS: a fault a system call's __TRY answers is never a livelock" if ok else "FAILED: syscall fault loop guards")
sys.exit(0 if ok else 1)
