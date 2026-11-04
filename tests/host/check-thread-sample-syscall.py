#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[thread-sample] last-syscall: where a thread parked in a system call entered it (round 630, agent U S5).

Bendy and the Ink Machine (4A181J), Schedule I (NGN052), Slay the Spire 2 (62ZBCW): the sampler printed "NATIVE (no FEX state)" and an
empty stack scan for the game's main thread, parked in a wineserver wait; its own sp is on the kernel stack. The user state of the call
is in the thread's syscall frame.

Checks:
  - signal_arm64_ios.c has ios_syscall_frame_user once: it reads the TEB's thread data (GdiTebBatch), its syscall frame, and the frame's
    pc, lr, sp and syscall_id, all through ios_exc_read64 (the kernel; nothing is written);
  - server_ios.c's sampler calls it only for a thread with no FEX state, while the thread is suspended, and prints a "last-syscall"
    part with the module names of pc and lr and the return addresses on the user stack, within the line's bounds;
  - the helper, cut from the source with the real struct syscall_frame and ios_exc_read64 and built on the host over memory laid out as
    the stand-ins say: the values of a frame come back; no TEB, a NULL frame or an unreadable frame give 0.
"""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
sig = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
srv = (root / "build/ntdll-unix/server_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


head = "int ios_syscall_frame_user( uintptr_t teb, uint64_t *pc, uint64_t *lr, uint64_t *sp, unsigned *id )\n{\n"
check("signal_arm64_ios.c defines the helper once", sig.count(head) == 1)
fn = sig[sig.index(head):]
fn = fn[:fn.index("\n}\n") + 3]
check("it reads the thread data's syscall frame and the frame's pc, lr, sp and syscall_id through ios_exc_read64 only",
      "const uint64_t base = (uint64_t)teb + offsetof( TEB, GdiTebBatch );" in fn
      and "offsetof( struct ntdll_thread_data, syscall_frame )" in fn
      and all("offsetof( struct syscall_frame, %s )" % m in fn for m in ("pc", "lr", "sp", "syscall_id"))
      and fn.count("ios_exc_read64(") == 5 and "mach_vm_write" not in fn and "memcpy" not in fn and "->" not in fn)
samp = srv[srv.index("static void ios_thread_sampler_pass(int burst)"):]
samp = samp[:samp.index("\n}\n")]
call = "have_user = x18 && ios_syscall_frame_user((uintptr_t)x18, &upc, &ulr, &usp, &sid) && usp > 0x10000;"
check("the sampler asks only for a thread with no FEX state, while it is suspended",
      samp.count(call) == 1 and samp.index("if (!have_bb) {") < samp.index(call)
      < samp.index("        thread_resume(tlist[k]);\n        if (have_bb) rip = ios_native_rip_from_hostpc(bb, pc, &why);")
      and samp.index("thread_suspend(tlist[k])") < samp.index(call))
check("it prints the last-syscall part with module names and the user stack's return addresses, within the line",
      '" last-syscall id=0x%x pc=%s+0x%llx"' in samp and '" user-sp=0x%llx user-cand=["' in samp
      and "if (have_user && n < (int)sizeof(line) - 300) {" in samp and "shown < 12 && n < (int)sizeof(line) - 80" in samp)

frame_struct = sig[sig.index("struct syscall_frame\n{"):]
frame_struct = frame_struct[:frame_struct.index("};") + 2]
read64 = sig[sig.index("static int ios_exc_read64( uint64_t addr, uint64_t *out )"):]
read64 = read64[:read64.index("\n}\n") + 3]
harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>
#include <string.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
typedef uint64_t ULONG64; typedef uint32_t ULONG; typedef struct { uint64_t lo, hi; } NEON128;
typedef struct { char pad[0x2f0]; unsigned char GdiTebBatch[0x4e8]; } TEB;
struct ntdll_thread_data { void *cpu_data[16]; void *jmp_buf; void *kernel_stack; void *syscall_frame; };
''' + frame_struct + "\n" + read64 + "\n" + fn + r'''
int main(void)
{
    static TEB teb;
    static struct syscall_frame frame;
    struct ntdll_thread_data *td = (struct ntdll_thread_data *)teb.GdiTebBatch;
    uint64_t pc = 0, lr = 0, sp = 0; unsigned id = 0; int r;
    frame.pc = 0x7ffe12345678ull; frame.lr = 0x7ffe12340000ull; frame.sp = 0x71fff00000ull; frame.syscall_id = 0x1e;
    td->syscall_frame = &frame;
    r = ios_syscall_frame_user((uintptr_t)&teb, &pc, &lr, &sp, &id);
    printf("%d %llx %llx %llx %x\n", r, (unsigned long long)pc, (unsigned long long)lr, (unsigned long long)sp, id);
    printf("%d", ios_syscall_frame_user(0, &pc, &lr, &sp, &id));
    td->syscall_frame = NULL;
    printf(" %d", ios_syscall_frame_user((uintptr_t)&teb, &pc, &lr, &sp, &id));
    td->syscall_frame = (void *)0x10;
    printf(" %d\n", ios_syscall_frame_user((uintptr_t)&teb, &pc, &lr, &sp, &id));
    return 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if sys.platform != "darwin" or not cc:
    print("skip the compiled part: macOS and a C compiler are needed")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-ts-syscall-") as d:
        d = Path(d)
        (d / "t.c").write_text(harness)
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", str(d / "t.c"), "-o", str(d / "t")],
                           capture_output=True, text=True)
        check("the helper builds on the host with the real syscall frame", b.returncode == 0, b.stderr[-1200:])
        if b.returncode == 0:
            out = subprocess.run([str(d / "t")], capture_output=True, text=True).stdout.split("\n")
            check("a frame's pc, lr, sp and call number come back", out[0] == "1 7ffe12345678 7ffe12340000 71fff00000 1e", out)
            check("no TEB, a NULL frame and an unreadable frame give 0", out[1] == "0 0 0", out)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-thread-sample-syscall.py" in wf)
print("PASS: [thread-sample] last-syscall" if ok else "FAILED: [thread-sample] last-syscall")
sys.exit(0 if ok else 1)
