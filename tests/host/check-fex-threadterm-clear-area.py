#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-threadterm-clear-area.py: ThreadTerm clears the CPU area's emulator slots once the thread state is destroyed (night round 2026-11-11, X1 S5).

Splinter Cell Conviction (2QWXFW, build 634): after `[thr-term] rev=ml435 deinit` an exception on the same thread reached ResetToConsistentState, found
the CPU area still naming the destroyed InternalThreadState and called CPUBackend::IsAddressInCodeBuffer on it (`[fex-fault] pc=libarm64ecfex.dll+0x1d1e4
... fault-addr=0x97fff5daaa1303e8`, freed memory holding code bytes). The guard `if (!CPUArea.ThreadState()) return STATUS_SUCCESS;` is meant for a thread
without a state.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned sources in the build's order
(tests/host/fex_chain.py), a second run says "already in" and changes nothing, a moved anchor refuses it; exactly four lines are added inside ThreadTerm, right
after CTX->DestroyThread(ThreadState) and before the stack is freed, and ThreadTerm's own local copy of the state is still used afterwards; ThreadCPUArea (cut
from the patched file) compiled on the host with a fake CPU area: before the clearing the slot still names the destroyed state, after it the
ResetToConsistentState test `!CPUArea.ThreadState()` is true. The ARM64EC build applies it without failing the build and restores Module.cpp; the auto-tune
rule; CI runs this check.
"""
from pathlib import Path
import difflib, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fex_chain

ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


NAME = "patch-fex-ios-threadterm-clear-area.py"
MARK = "threadterm-clear-area-v1 madeira-bcd"
patch = root / "tools" / NAME
check("the patch script exists", patch.is_file())
build = (root / "tools/build-xtajit64.sh").read_text()
check("the ARM64EC build applies it once, after the pin's branches, without failing the build, before the build step, and restores Module.cpp",
      build.count('python3 "$R/tools/' + NAME + '" "$R/FEX" || echo "::warning::') == 1 and build.index(NAME) > build.index("patch-fex-ios-callret-small.py")
      and "\nbuild\n" in build[build.index(NAME):] and "Source/Windows/ARM64EC/Module.cpp" in build[build.index('git -C FEX checkout -- "$CPUF"'):])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-threadterm-clear-area.py" in wf)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names the fix (fex-threadterm-stale-state, fixedIn 652)", re.search(r'"id": "fex-threadterm-stale-state",\s*"lines": \[\],\s*"fixedIn": 652', rules) is not None)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
module_rel = "Source/Windows/ARM64EC/Module.cpp"
if not (fex / module_rel).is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-fex-ttclear-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        res = fex_chain.run_chain(root, "ec", tree, before=NAME)
        check("the build's scripts ahead of it apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
        m = tree / module_rel
        before = m.read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        once = m.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and m.read_text() == once and once != before, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        res = fex_chain.run_chain(root, "ec", tree, after=NAME)
        check("the scripts behind it still apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
        a, b = before.splitlines(), once.splitlines()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
        check("exactly four lines are added (two comment lines, two assignments), nothing else changes", len(ops) == 1 and ops[0][0] == "insert" and ops[0][4] - ops[0][3] == 4, ops)
        i = once.index("NTSTATUS ThreadTerm(HANDLE Thread, LONG ExitCode) {")
        fn = once[i:once.index("\n}\n", i)]
        k = fn.index("  CTX->DestroyThread(ThreadState);\n")
        tail = fn[k:]
        check("the clearing sits right after DestroyThread and before the emulator stack is freed; ThreadTerm keeps using its local ThreadState copy before it",
              tail.index("CPUArea.ThreadState() = nullptr;") < tail.index("CPUArea.StateFrame() = nullptr;") < tail.index("::VirtualFree(reinterpret_cast<void*>(CPUArea.EmulatorStackLimit())")
              and "CPUArea.ThreadState()->ThreadStats" in fn[:k]
              and "CPUArea.ThreadState()" not in "\n".join(l for l in tail.splitlines() if not l.strip().startswith("//")).replace("CPUArea.ThreadState() = nullptr;", ""))
        # the slots on a fake CPU area
        s0 = once.index("struct ThreadCPUArea {")
        s1 = once.index("\n};\n", s0) + 4
        harness = r'''
#include <cstdint>
#include <cstdio>
namespace FEXCore::Core { struct CpuStateFrame; struct InternalThreadState; }
struct ARM64EC_NT_CONTEXT {};
struct CHPE_V2_CPU_AREA_INFO { uint8_t InSyscallCallback; uint8_t InSimulation; uint64_t EmulatorStackLimit, EmulatorStackBase; ARM64EC_NT_CONTEXT* ContextAmd64; uint64_t EmulatorData[4]; };
struct _TEB { char pad[0x1788]; CHPE_V2_CPU_AREA_INFO* ChpeV2CpuAreaInfo; };
''' + once[s0:s1] + r'''
int main() {
  static _TEB teb; static CHPE_V2_CPU_AREA_INFO area;
  teb.ChpeV2CpuAreaInfo = &area;
  const ThreadCPUArea CPUArea(&teb);
  area.EmulatorData[1] = 0x7c0000a000;   // the thread state (destroyed by CTX->DestroyThread)
  area.EmulatorData[0] = 0x7c0000a140;
  std::printf("before guard-bails=%d\n", !CPUArea.ThreadState() ? 1 : 0);
  CPUArea.ThreadState() = nullptr;
  CPUArea.StateFrame() = nullptr;
  std::printf("after guard-bails=%d frame=%d\n", !CPUArea.ThreadState() ? 1 : 0, CPUArea.StateFrame() == nullptr ? 1 : 0);
  return 0;
}
'''
        cc = shutil.which("clang++") or shutil.which("c++")
        if not cc:
            print("skip: no host C++ compiler")
        else:
            src = Path(tmp) / "h.cpp"
            exe = Path(tmp) / "h"
            src.write_text(harness)
            c = subprocess.run([cc, "-std=c++20", "-o", str(exe), str(src)], capture_output=True, text=True)
            check("ThreadCPUArea (cut from the patched file) compiles on the host", c.returncode == 0, c.stdout + c.stderr)
            if c.returncode == 0:
                out = subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines()
                check("before the clearing the destroyed state is still named (the ResetToConsistentState guard does not bail: the defect); after it the guard bails",
                      out == ["before guard-bails=0", "after guard-bails=1 frame=1"], out)

    with tempfile.TemporaryDirectory(prefix="madeira-fex-ttclear-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        m = tree / module_rel
        m.write_text(m.read_text().replace("  CTX->DestroyThread(ThreadState);\n  ::VirtualFree(", "  CTX->DestroyThread(ThreadState);\n  ::VirtualFree2("))
        stale = m.read_text()
        r3 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and writes nothing", r3.returncode != 0 and "not found once" in r3.stderr and m.read_text() == stale, r3.stdout + r3.stderr)

print("PASS: FEX ThreadTerm clears the CPU area" if ok else "FAILED: FEX ThreadTerm CPU area")
sys.exit(0 if ok else 1)
