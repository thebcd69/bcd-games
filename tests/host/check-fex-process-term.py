#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-process-term.py: after NtTerminateProcess(0) the FEX ARM64EC memory hooks no longer use the tracker (night round 2026-11-11, X1 S3).

A normal exit (RtlExitUserProcess: NtTerminateProcess(0), LdrShutdownProcess, frees) faulted in InvalidateAlignedInterval at address 0xf0 in
about 3 % of the exits of build 634 (the emulator's context object was already gone): 06A8JF, TSGVTP (a normal quit turned into
`[exit-report] status=0xc0000005`), 66HYF7, BS6583, JCW9Q0, Z40K5X, and QTDFHP, RBCWQH, A3ZNFE (where it hid a memory kill from auto-tune).

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned sources in the
build's order (tests/host/fex_chain.py), in either order with the no-CPU-area patch, a second run says "already in" and changes nothing, a moved anchor
refuses it; the six hooks begin with the gate, NotifyUnmapViewOfSection and BTCpu64NotifyReadFile (lock pairs) have none; the flag is defined before
the first hook; ProcessTerm and NotifyMemoryFree cut from the patched file and compiled on the host with a tracker that faults once the context is
torn down: before ProcessTerm the hook calls the tracker; after ProcessTerm(handle, FALSE) it does not (without the gate the same call dies with a
signal: the defect); a failed NtTerminateProcess(0) clears the flag; the [fex-term] line is printed once. The ARM64EC build applies it without failing
the build; auto-tune's fex-exit-teardown-noise and the fex-runtime-fault exclusion; CI runs this check.
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


NAME = "patch-fex-ios-process-term.py"
OTHER = "patch-fex-ios-notify-nocpuarea.py"
MARK = "process-term-v1 madeira-bcd"
patch = root / "tools" / NAME
check("the patch script exists", patch.is_file())
build = (root / "tools/build-xtajit64.sh").read_text()
check("the ARM64EC build applies it once, after the pin's branches, without failing the build, before the build step, and restores Module.cpp",
      build.count('python3 "$R/tools/' + NAME + '" "$R/FEX" || echo "::warning::') == 1 and build.index(NAME) > build.index("patch-fex-ios-callret-small.py")
      and "\nbuild\n" in build[build.index(NAME):] and "Source/Windows/ARM64EC/Module.cpp" in build[build.index('git -C FEX checkout -- "$CPUF"'):])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-process-term.py" in wf)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names the fix (fex-exit-teardown-noise, fixedIn 652) and fex-runtime-fault leaves the 0xf0 teardown fault to the session's own verdict",
      re.search(r'"id": "fex-exit-teardown-noise",\s*"lines": \[\],\s*"fixedIn": 652', rules) is not None
      and 's("[fex-fault] pc=", "lr=ntdll.dll+0x5b9a0", "fault-addr=0xf0")' in rules)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
module_rel = "Source/Windows/ARM64EC/Module.cpp"
HOOKS = ["NotifyMemoryAlloc", "NotifyMemoryFree", "NotifyMemoryProtect", "FlushInstructionCacheHeavy", "BTCpu64FlushInstructionCache",
         "BTCpu64NotifyMemoryDirty"]


def function_text(text, start_re):
    i = re.search(start_re, text, re.M).start()
    return text[i:text.index("\n}\n", i)]


if not (fex / module_rel).is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-fex-pterm-") as tmp:
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
        check("ProcessTerm's empty body is replaced and six three-line gates are added, nothing else changes",
              sum(o[2] - o[1] for o in ops) == 1 and sum(o[4] - o[3] for o in ops) - 1 == 6 * 3 + (len(b) - len(a) - 6 * 3), (len(a), len(b), ops))
        for h in HOOKS:
            f = function_text(once, r"^void " + h + r"\(")
            lines = f.split("\n")
            check(h + " begins with the process-terminating gate",
                  lines[1] == "  if (FexProcessTerminating.load(std::memory_order_relaxed)) {   // " + MARK and lines[2].strip() == "return;")
        for h in ("NotifyUnmapViewOfSection", "BTCpu64NotifyReadFile"):
            check(h + " has no gate (its lock pair must stay symmetric)", "FexProcessTerminating" not in function_text(once, r"^void " + h + r"\("))
        check("the flag is defined before the first hook", once.index("std::atomic<bool> FexProcessTerminating") < once.index("void NotifyMemoryAlloc("))

        # either order with the no-CPU-area patch
        with tempfile.TemporaryDirectory(prefix="madeira-fex-pterm2-") as tmp2:
            t2 = fex_chain.copy_sources(fex, Path(tmp2) / "t")
            fex_chain.run_chain(root, "ec", t2, before=OTHER if (root / "tools" / OTHER).is_file() else NAME)
            ra = subprocess.run([sys.executable, str(patch), str(t2)], capture_output=True, text=True)
            rb = subprocess.run([sys.executable, str(root / "tools" / OTHER), str(t2)], capture_output=True, text=True)
            check("it also applies when it runs BEFORE the no-CPU-area patch", ra.returncode == 0 and rb.returncode == 0, ra.stdout + ra.stderr + rb.stdout + rb.stderr)

        # behaviour: ProcessTerm + one hook, compiled on the host
        p0 = once.index("// " + MARK + " (night round")
        p1 = once.index("\n}\n", once.index("void ProcessTerm(")) + 3
        pt = once[p0:p1]
        hook = function_text(once, r"^void NotifyMemoryFree\(") + "\n}\n"
        harness_tpl = r'''
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <memory>
#include <optional>
#include <unistd.h>
#include <sys/wait.h>
typedef void* HANDLE; typedef int BOOL; typedef int NTSTATUS; typedef unsigned long SIZE_T; typedef unsigned long ULONG;
static int printed;
namespace LogMan::Msg { template<class... A> void EFmt(const char*, A&&...) { printed++; } }
struct Ctx { void* vptr; };
static Ctx RealCtx {reinterpret_cast<void*>(0x1234)};
static std::unique_ptr<Ctx, void (*)(Ctx*)> CTX {&RealCtx, [](Ctx*) {}};
static bool torn_down;       // the emulator's context was torn down: the tracker faults (the defect)
static int tracker_calls;
struct Tracker { void InvalidateAlignedInterval(uint64_t, uint64_t, bool) { if (torn_down) { volatile int* p = nullptr; *p = 1; } tracker_calls++; } };
static std::optional<Tracker> InvalidationTracker {Tracker {}};
static uintptr_t ios_fex_band_base, ios_fex_band_end;
static bool HasEmulatorThreadState() { return true; }
''' + "%PT%" + r'''
%HOOK%
static int crashes(NTSTATUS st) {
  pid_t p = fork();
  if (p == 0) { signal(SIGSEGV, SIG_DFL); signal(SIGBUS, SIG_DFL); NotifyMemoryFree(reinterpret_cast<void*>(0x1000), 0x1000, 0x8000, 1, st); _exit(0); }
  int s = 0; waitpid(p, &s, 0); return WIFSIGNALED(s);
}
int main() {
  NotifyMemoryFree(reinterpret_cast<void*>(0x1000), 0x1000, 0x8000, 1, 0);
  printf("before-term calls=%d\n", tracker_calls);
  ProcessTerm(reinterpret_cast<HANDLE>(0), 0, 0);
  printf("after-term-before calls=%d printed=%d\n", tracker_calls, printed);
  NotifyMemoryFree(reinterpret_cast<void*>(0x1000), 0x1000, 0x8000, 1, 0);
  torn_down = true;
  printf("terminating-and-torn-down crashes=%d calls=%d\n", crashes(0), tracker_calls);
  ProcessTerm(reinterpret_cast<HANDLE>(0), 0, 0);
  printf("second-term printed=%d\n", printed);
  ProcessTerm(reinterpret_cast<HANDLE>(0), 1, 0);
  printf("after-ok crashes=%d\n", crashes(0));
  ProcessTerm(reinterpret_cast<HANDLE>(0), 1, (NTSTATUS)0xC0000022);
  printf("failed-term crashes=%d\n", crashes(0));
  return 0;
}
'''
        cc = shutil.which("clang++") or shutil.which("c++")
        if not cc:
            print("skip: no host C++ compiler")
        else:
            def run(pt_text, hook_text, tag):
                src = Path(tmp) / (tag + ".cpp")
                src.write_text(harness_tpl.replace("%PT%", pt_text).replace("%HOOK%", hook_text))
                exe = Path(tmp) / tag
                c = subprocess.run([cc, "-std=c++20", "-o", str(exe), str(src)], capture_output=True, text=True)
                if c.returncode != 0:
                    return None, c.stdout + c.stderr
                return subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines(), ""
            out, err = run(pt, hook, "new")
            check("ProcessTerm and NotifyMemoryFree (cut from the patched file) compile on the host", out is not None, err)
            if out:
                check("before ProcessTerm the hook calls the tracker (a working game's frees are tracked as before)", out[0] == "before-term calls=1", out)
                check("ProcessTerm(handle, FALSE) logs [fex-term] once and the hook stops calling the tracker",
                      out[1] == "after-term-before calls=1 printed=1", out)
                check("with the process terminating and the context torn down the hook returns: no fault (the defect) and no call",
                      out[2] == "terminating-and-torn-down crashes=0 calls=1", out)
                check("a second ProcessTerm does not log again", out[3] == "second-term printed=1", out)
                check("ProcessTerm(handle, TRUE, success) keeps the gate", out[4] == "after-ok crashes=0", out)
                check("ProcessTerm(handle, TRUE, failure) lifts it (the process is not ending): the torn-down tracker is reached again",
                      out[5] == "failed-term crashes=1", out)
            ungated = hook.replace("  if (FexProcessTerminating.load(std::memory_order_relaxed)) {   // " + MARK + "\n    return;\n  }\n", "")
            out2, err2 = run(pt, ungated, "old")
            check("WITHOUT the gate the same exit faults (the defect reproduced)", out2 is not None and out2[2].startswith("terminating-and-torn-down crashes=1"), (out2, err2))

    with tempfile.TemporaryDirectory(prefix="madeira-fex-pterm-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        m = tree / module_rel
        m.write_text(m.read_text().replace("void ProcessTerm(HANDLE Handle, BOOL After, NTSTATUS Status) {}", "void ProcessTerm(HANDLE Handle, BOOL After, NTSTATUS Status) { }"))
        stale = m.read_text()
        r3 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and writes nothing", r3.returncode != 0 and "not found once" in r3.stderr and m.read_text() == stale, r3.stdout + r3.stderr)

print("PASS: FEX process-terminating gate" if ok else "FAILED: FEX process-terminating gate")
sys.exit(0 if ok else 1)
