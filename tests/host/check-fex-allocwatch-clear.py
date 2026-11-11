#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-allocwatch-clear.py: AllocWatch::Clear returns at once when nothing is watched (night round 2026-11-11, X1 S6).

GTA V Legacy's child GTA5.exe (ZDWTFF, build 634): the first compile of the child's first thread ended its dead-flag pass with AllocWatch::Clear(), whose
`CurrentThreadId()` is implicit TLS in the ARM64EC module; the thread had no TLS pointer yet (`TEB[0x58]=0x0`): `[fex-fault] pc=libarm64ecfex.dll+0x108244`.
Nothing registers a watch any more (ml621), so the call and its 512-slot scan are dead work.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned sources in the ARM64EC build's order
(tests/host/fex_chain.py), a second run says "already in" and changes nothing, a moved anchor refuses it; exactly five lines are added, at the start of Clear(), before
CurrentThreadId(); the pristine and the patched AllocWatch.cpp are both compiled on the host with a stand-in LogMan and driven through the same sequences (watch / clear
from the same and from another thread / unwatch / report): the observable state (the armed flag and the report's census line) is identical after every step; with
nothing watched the patched Clear() reads no thread id (the pristine one does: it is the hazard). The ARM64EC build applies it without failing the build and
restores AllocWatch.cpp; the auto-tune rule; CI runs this check.
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


NAME = "patch-fex-ios-allocwatch-clear.py"
MARK = "allocwatch-clear-v1 madeira-bcd"
FILE = "FEXCore/Source/Utils/AllocWatch.cpp"
patch = root / "tools" / NAME
check("the patch script exists", patch.is_file())
build = (root / "tools/build-xtajit64.sh").read_text()
check("the ARM64EC build applies it once, after the pin's branches, without failing the build, before the build step, and restores AllocWatch.cpp",
      build.count('python3 "$R/tools/' + NAME + '" "$R/FEX" || echo "::warning::') == 1 and build.index(NAME) > build.index("patch-fex-ios-callret-small.py")
      and "\nbuild\n" in build[build.index(NAME):] and FILE in build[build.index('git -C FEX checkout -- "$CPUF"'):])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-allocwatch-clear.py" in wf)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names the fix (fex-allocwatch-clear-tls, fixedIn 652)", re.search(r'"id": "fex-allocwatch-clear-tls",\s*"lines": \[\],\s*"fixedIn": 652', rules) is not None)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))

HARNESS = r'''
#include "Utils/AllocWatch.h"
#include <cstdio>
#include <string>
#include <thread>
extern std::string last_census;
static int dummy[8];
using namespace FEXCore::Utils::AllocWatch;
static void state(const char* tag) {
  ReportAndDisarm("t");    // prints the census, disarms and clears
  std::printf("%s census=%s\n", tag, last_census.c_str());
}
int main() {
  Clear();   // nothing watched
  std::printf("empty armed=%d\n", (int)FEX_AllocWatch_Armed);
  Watch(&dummy[0], 8, 1);
  std::printf("watched armed=%d\n", (int)FEX_AllocWatch_Armed);
  Clear();   // the same thread retires it
  std::printf("cleared-by-owner armed=%d\n", (int)FEX_AllocWatch_Armed);
  Watch(&dummy[2], 8, 2);
  std::thread([] { Clear(); }).join();    // another thread: its Clear() leaves the watch (and the armed flag) alone
  std::printf("cleared-by-other armed=%d\n", (int)FEX_AllocWatch_Armed);
  state("report-live");
  Watch(&dummy[4], 8, 3);
  Unwatch(&dummy[4]);
  Clear();
  std::printf("unwatched armed=%d\n", (int)FEX_AllocWatch_Armed);
  return 0;
}
'''
STUB_LOG = '''#pragma once
#include <string>
#include <cstdint>
namespace LogMan::Msg { void EFmtCapture(const char* fmt, int site_events, int live); }
'''


def run_variant(src_text, tmp, tag):
    d = Path(tmp) / tag
    (d / "FEXCore/Utils").mkdir(parents=True)
    (d / "Utils").mkdir(parents=True)
    # the stand-in LogMan captures the census arguments (events, live_watches)
    (d / "FEXCore/Utils/LogManager.h").write_text('''#pragma once
#include <string>
extern std::string last_census;
namespace LogMan::Msg { template<class S, class A, class B, class C, class D, class E> void EFmt(const S&, const A&, const B& ev, const C& live, const D&, const E&) { last_census = "events=" + std::to_string(ev) + " live=" + std::to_string(live); } }
''')
    (d / "Utils/AllocWatch.h").write_text(Path(fex / "FEXCore/Source/Utils/AllocWatch.h").read_text())
    (d / "AllocWatch.cpp").write_text(src_text.replace('#include "Utils/AllocWatch.h"', '#include "Utils/AllocWatch.h"'))
    (d / "h.cpp").write_text(HARNESS)
    exe = d / "h"
    cc = shutil.which("clang++") or shutil.which("c++")
    c = subprocess.run([cc, "-std=c++20", "-O1", "-pthread", "-I", str(d), "-o", str(exe), str(d / "AllocWatch.cpp"), str(d / "h.cpp"), "-x", "c++", "-"],
                       input="#include <string>\nstd::string last_census;\n", capture_output=True, text=True)
    if c.returncode != 0:
        return None, c.stdout + c.stderr
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60).stdout.splitlines(), ""


if not (fex / FILE).is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-fex-awc-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        res = fex_chain.run_chain(root, "ec", tree, before=NAME)
        check("the build's scripts ahead of it apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
        f = tree / FILE
        before = f.read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        once = f.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and f.read_text() == once and once != before, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        res = fex_chain.run_chain(root, "ec", tree, after=NAME)
        check("the scripts behind it still apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
        a, b = before.splitlines(), once.splitlines()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
        check("exactly five lines are added, right after `void Clear() {` and before CurrentThreadId()", len(ops) == 1 and ops[0][0] == "insert" and ops[0][4] - ops[0][3] == 5
              and b[ops[0][3] - 1] == "void Clear() {" and b[ops[0][4]].strip() == "const uint64_t Self = CurrentThreadId();", ops)
        if not (shutil.which("clang++") or shutil.which("c++")):
            print("skip: no host C++ compiler")
        else:
            old, e1 = run_variant(before, tmp, "old")
            new, e2 = run_variant(once, tmp, "new")
            check("both AllocWatch.cpp variants compile and run on the host", old is not None and new is not None, e1 + e2)
            if old is not None and new is not None:
                check("the observable state (armed flag, census) is identical after every step: the early return changes nothing that works", old == new and len(new) == 6, (old, new))
                check("sanity: an owner's Clear retires its watch, another thread's does not", "cleared-by-owner armed=0" in new and "cleared-by-other armed=1" in new, new)

    with tempfile.TemporaryDirectory(prefix="madeira-fex-awc-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        f = tree / FILE
        f.write_text(f.read_text().replace("void Clear() {\n  const uint64_t Self = CurrentThreadId();", "void Clear() {\n  const uint64_t Self2 = CurrentThreadId();"))
        stale = f.read_text()
        r3 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and writes nothing", r3.returncode != 0 and "not found once" in r3.stderr and f.read_text() == stale, r3.stdout + r3.stderr)

print("PASS: FEX AllocWatch::Clear" if ok else "FAILED: FEX AllocWatch::Clear")
sys.exit(0 if ok else 1)
