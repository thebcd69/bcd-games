#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-notify-nocpuarea.py: FEX's ARM64EC hooks tolerate a thread with no CPU area (night round 2026-11-11, R1 S3 / X1 S1c).

A native ARM64 program of an ARM64EC session (services.exe, cmd.exe, conhost.exe) has TEB->ChpeV2CpuAreaInfo == NULL
(build/ntdll-unix/thread_ios.c: "NOT setting cpu_area"), but the ARM64EC ntdll still calls FEX's NotifyMemoryAlloc/Free/Protect, ...
ResetToConsistentState and ThreadInit for it. They read `Area->EmulatorData[1]` through NULL: `[fex-fault] pc=libarm64ecfex.dll+0x113c14
(NotifyMemoryAlloc+0x54) fault-addr=0x38` in QG7MT4, KT9K9S, SCHZWC, DABCAC, 4DRZ3H (Epic GTA V Enhanced), 4TXJW2 (The Escapists), 3012R0.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned sources in the
build's order (tests/host/fex_chain.py), a second run says "already in" and changes nothing, a moved anchor refuses it; the seven hooks test
HasEmulatorThreadState(), BTCpu64NotifyReadFile reads the thread state only when there is an area, ResetToConsistentState and ThreadInit begin
with the HasCPUArea() guard; no other line changes; the helper code cut from the patched file and compiled on the host with a fake TEB: the OLD
condition on a thread without an area dies with SIGSEGV (the defect), the new helpers say "no" without touching it, and a thread with an area
gets exactly the old answers. The ARM64EC build applies it without failing the build; the auto-tune rule; CI runs this check.
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


NAME = "patch-fex-ios-notify-nocpuarea.py"
MARK = "ec-no-cpu-area-v1 madeira-bcd"
patch = root / "tools" / NAME
check("the patch script exists", patch.is_file())

build = (root / "tools/build-xtajit64.sh").read_text()
check("the ARM64EC build applies it once, after the pin's branches, without failing the build, before the build step",
      build.count('python3 "$R/tools/' + NAME + '" "$R/FEX" || echo "::warning::') == 1
      and build.index(NAME) > build.index("patch-fex-ios-callret-small.py") and "\nbuild\n" in build[build.index(NAME):]
      and "Source/Windows/ARM64EC/Module.cpp" in build[build.index('git -C FEX checkout -- "$CPUF"'):])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-notify-nocpuarea.py" in wf)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names the two defects fixed in 652 (fex-notify-no-cpu-area)", '"id": "fex-notify-no-cpu-area"' in rules
      and re.search(r'"id": "fex-notify-no-cpu-area",\s*"lines": \[\],\s*"fixedIn": 652', rules) is not None)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
module_rel = "Source/Windows/ARM64EC/Module.cpp"
HOOKS = ["NotifyMemoryAlloc", "NotifyMemoryFree", "NotifyMemoryProtect", "NotifyUnmapViewOfSection", "FlushInstructionCacheHeavy",
         "BTCpu64FlushInstructionCache", "BTCpu64NotifyMemoryDirty"]


def function_text(text, sig_start):
    i = text.index(sig_start)
    return text[i:text.index("\n}\n", i)]


if not (fex / module_rel).is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-fex-nocpu-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        res = fex_chain.run_chain(root, "ec", tree, before=NAME)
        check("the build's scripts ahead of it apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
        m = tree / module_rel
        before = m.read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        once = m.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and m.read_text() == once and once != before,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        res = fex_chain.run_chain(root, "ec", tree, after=NAME)
        check("the scripts behind it still apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))

        a, b = before.splitlines(), once.splitlines()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
        added = sum(o[4] - o[3] for o in ops)
        removed = sum(o[2] - o[1] for o in ops)
        check("the change is the helper, seven hook conditions, the read-file read, two guards (3 lines each): removed 8 lines, added 33", removed == 8 and added == 33 and all(o[0] in ("insert", "replace") for o in ops), (removed, added))
        for h in HOOKS:
            f = function_text(once, [l for l in once.splitlines() if re.match(r"void " + h + r"\(", l)][0])
            check(h + " tests HasEmulatorThreadState() and no longer asks the area itself",
                  "if (!InvalidationTracker || !HasEmulatorThreadState()) {" in f and "GetCPUArea().ThreadState()" not in f)
        f = function_text(once, "void BTCpu64NotifyReadFile(")
        check("BTCpu64NotifyReadFile reads the thread state only when there is an area",
              "auto* ThreadState = HasCPUArea() ? GetCPUArea().ThreadState() : nullptr;" in f)
        for sig in ("NTSTATUS ResetToConsistentState(EXCEPTION_RECORD*", "NTSTATUS ThreadInit() {"):
            f = function_text(once, sig)
            check(sig.split("(")[0] + " begins with the guard",
                  f.split("\n")[1].startswith("  if (!HasCPUArea()) {   // " + MARK) and f.split("\n")[2].strip() == "return STATUS_SUCCESS;")
        check("the other `GetCPUArea().ThreadState()` uses (ThreadTerm, BTCpu64IosReleaseThreadHolds, the read-file one behind HasCPUArea()) are untouched: "
              "seven conditions gone, one in the new helper",
              once.count("GetCPUArea().ThreadState()") == before.count("GetCPUArea().ThreadState()") - 7 + 1)

        # the helpers, compiled on the host with a fake TEB
        h0 = once.index("// " + MARK + " (round")
        h1 = once.index("\n}\n", once.index("inline bool HasEmulatorThreadState()")) + 3
        helpers = once[h0:h1]
        s0 = once.index("struct ThreadCPUArea {")
        s1 = once.index("\n};\n", s0) + 4
        harness = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <unistd.h>
#include <sys/wait.h>
namespace FEXCore::Core { struct CpuStateFrame; struct InternalThreadState; }
struct ARM64EC_NT_CONTEXT {};
struct CHPE_V2_CPU_AREA_INFO { uint8_t InSyscallCallback; uint8_t InSimulation; uint64_t EmulatorStackLimit, EmulatorStackBase;
                               ARM64EC_NT_CONTEXT* ContextAmd64; uint64_t EmulatorData[4]; };
struct _TEB { char pad[0x1788]; CHPE_V2_CPU_AREA_INFO* ChpeV2CpuAreaInfo; };
''' + once[s0:s1] + r'''
static _TEB* g_teb;
static inline _TEB* IOSLoadTEB() { return g_teb; }
ThreadCPUArea GetCPUArea() { return ThreadCPUArea(IOSLoadTEB()); }
''' + helpers + r'''
static _TEB teb;
static CHPE_V2_CPU_AREA_INFO area;
static int old_cond_crashes() {
  pid_t p = fork();
  if (p == 0) { signal(SIGSEGV, SIG_DFL); signal(SIGBUS, SIG_DFL); volatile bool v = GetCPUArea().ThreadState() != nullptr; (void)v; _exit(0); }
  int st = 0; waitpid(p, &st, 0);
  return WIFSIGNALED(st);
}
int main() {
  g_teb = &teb;
  teb.ChpeV2CpuAreaInfo = nullptr;
  printf("noarea has=%d state=%d old_crashes=%d\n", HasCPUArea(), HasEmulatorThreadState(), old_cond_crashes());
  teb.ChpeV2CpuAreaInfo = &area;
  area.EmulatorData[1] = 0;
  printf("area-nostate has=%d state=%d old=%d\n", HasCPUArea(), HasEmulatorThreadState(), GetCPUArea().ThreadState() != nullptr);
  area.EmulatorData[1] = 0x1234;
  printf("area-state has=%d state=%d old=%d\n", HasCPUArea(), HasEmulatorThreadState(), GetCPUArea().ThreadState() != nullptr);
  g_teb = nullptr;
  printf("noteb has=%d\n", HasCPUArea());
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
            c = subprocess.run([cc, "-std=c++20", "-DFEX_IOS_HOST=1", "-o", str(exe), str(src)], capture_output=True, text=True)
            check("the helper code compiles on the host", c.returncode == 0, c.stdout + c.stderr)
            if c.returncode == 0:
                out = subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines()
                check("without a CPU area: the OLD condition dies with a signal (the defect); the helpers answer no", out[0] == "noarea has=0 state=0 old_crashes=1", out)
                check("with an area but no thread state: no, and the old answer is the same", out[1] == "area-nostate has=1 state=0 old=0", out)
                check("with an area and a thread state: yes, and the old answer is the same", out[2] == "area-state has=1 state=1 old=1", out)
                check("a TEB that cannot be read keeps the old path (has area = yes)", out[3] == "noteb has=1", out)

    # a moved anchor refuses the patch (nothing is written)
    with tempfile.TemporaryDirectory(prefix="madeira-fex-nocpu-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        m = tree / module_rel
        m.write_text(m.read_text().replace("void NotifyMemoryFree(void* Address, SIZE_T Size, ULONG FreeType, BOOL After, NTSTATUS Status) {",
                                           "void NotifyMemoryFree(void* Address, SIZE_T Size, ULONG FreeType, BOOL After, NTSTATUS Status, int X) {"))
        stale = m.read_text()
        r3 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and writes nothing", r3.returncode != 0 and "not found once" in r3.stderr and m.read_text() == stale, r3.stdout + r3.stderr)

print("PASS: FEX hooks tolerate a thread with no CPU area" if ok else "FAILED: FEX no-CPU-area guards")
sys.exit(0 if ok else 1)
