#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-dep-promote-stale.py: a freed DEP-promoted page is promoted again (night round 2026-11-11, A4 S4).

Need for Speed: Underground (C86TQF, 32-bit, DEP off) died 85 s into the run with `NoExec instruction in entry block` at a committed readable view
(`[noexec-who] ... now: a view ... (VirtualAlloc)`): the page had been promoted under DEP-off, freed and allocated again, and
InvalidationTracker::PromoteDEPRegionLocked found the old DEPPromotedIntervals record and returned without inserting the page into XIntervals.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned sources in the WOW64
build's order (tests/host/fex_chain.py), a second run says "already in" and changes nothing, a moved anchor refuses it (nothing written); the change is
exactly the two free-path removals (each under `if (DEPDisabled)`, between RWXIntervals.Remove and ForgetHotSMCPages) and the one stale test in
PromoteDEPRegionLocked; a model of the promote / free / re-allocate / promote sequence on the real FEXCore::IntervalList (the tracker's own container),
written as the patched code decides it: with the old decisions the second promotion reports the page not executable (the defect), with the patched
ones it is executable; a page that is still promoted and executable and a first-time page are answered as before. The WOW64 build applies it without
failing the build and restores InvalidationTracker.cpp; auto-tune's rule; CI runs this check.
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


NAME = "patch-fex-ios-dep-promote-stale.py"
MARK = "dep-promote-stale-v1 madeira-bcd"
FILE = "Source/Windows/Common/InvalidationTracker.cpp"
patch = root / "tools" / NAME
check("the patch script exists", patch.is_file())
w64 = (root / "tools/build-xtajit-wow64.sh").read_text()
restore = w64[w64.index("restore() {"):w64.index("trap restore EXIT")]
check("the WOW64 build applies it once, without failing the build, and restores InvalidationTracker.cpp",
      w64.count('python3 "$R/tools/' + NAME + '" "$R/FEX" || echo "::warning::') == 1 and w64.index(NAME) < w64.index("trap restore EXIT") and FILE in restore)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-dep-promote-stale.py" in wf)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names the fix (fex-dep-off-stale-promotion, fixedIn 652)",
      re.search(r'"id": "fex-dep-off-stale-promotion",\s*"lines": \[\],\s*"fixedIn": 652', rules) is not None)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
if not (fex / FILE).is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-fex-dep-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        res = fex_chain.run_chain(root, "wow", tree, before=NAME)
        check("the build's scripts ahead of it apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
        f = tree / FILE
        before = f.read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        once = f.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and f.read_text() == once and once != before, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        res = fex_chain.run_chain(root, "wow", tree, after=NAME)
        check("the scripts behind it still apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
        a, b = before.splitlines(), once.splitlines()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
        check("three places change: two 3-line insertions and one line replaced by three", len(ops) == 3 and [o[0] for o in ops] == ["insert", "insert", "replace"]
              and ops[0][4] - ops[0][3] == 3 and ops[1][4] - ops[1][3] == 3 and ops[2][2] - ops[2][1] == 1 and ops[2][4] - ops[2][3] == 3, ops)
        for var in ("SectionBase", "AlignedBase"):
            sz = "SectionSize" if var == "SectionBase" else "AlignedSize"
            pat = ("    XIntervals.Remove({%s, %s + %s});\n    RWXIntervals.Remove({%s, %s + %s});\n    if (DEPDisabled) {   // %s: a freed page is no longer \"promoted\"\n"
                   "      DEPPromotedIntervals.Remove({%s, %s + %s});\n    }\n    ForgetHotSMCPages(") % (var, var, sz, var, var, sz, MARK, var, var, sz)
            check("the free path of " + var + " forgets the DEP-promoted record between RWXIntervals.Remove and ForgetHotSMCPages", pat in once)
        check("PromoteDEPRegionLocked trusts a record only while XIntervals still holds the page",
              "if (DEPPromotedIntervals.Query(Address).Enclosed && XIntervals.Query(Address).Enclosed) {\n    return ProtInterval;\n  }" in once)

        # the decisions on the real IntervalList
        il = fex / "FEXCore/include/FEXCore/Utils/IntervalList.h"
        cc = shutil.which("clang++") or shutil.which("c++")
        if not il.is_file() or not cc:
            print("skip: no IntervalList.h or no host C++ compiler")
        else:
            inc = Path(tmp) / "inc"
            (inc / "FEXCore/Utils").mkdir(parents=True)
            (inc / "FEXCore/fextl").mkdir(parents=True)
            shutil.copy(il, inc / "FEXCore/Utils/IntervalList.h")
            (inc / "FEXCore/fextl/vector.h").write_text("#pragma once\n#include <vector>\nnamespace fextl { template<class T> using vector = std::vector<T>; }\n")
            src = Path(tmp) / "m.cpp"
            src.write_text(r'''
#include <FEXCore/Utils/IntervalList.h>
#include <cstdio>
using L = FEXCore::IntervalList<uint64_t>;
struct Tracker {
  L X, RWX, DEP; bool patched;
  // PromoteDEPRegionLocked's decision for a committed, readable, non-executable page (a first promotion or a re-promotion)
  bool Promote(uint64_t page) {
    L::Interval I {page, page + 0x1000};
    bool already = DEP.Query(page).Enclosed && (!patched || X.Query(page).Enclosed);
    if (already) return true;                // returned as "promoted" without touching XIntervals
    X.Insert(I); RWX.Insert(I); DEP.Insert(I);
    return true;
  }
  bool Executable(uint64_t page) { return X.Query(page).Enclosed; }
  // InvalidateAlignedInterval(..., Free = true)
  void Free(uint64_t page) {
    L::Interval I {page, page + 0x1000};
    X.Remove(I); RWX.Remove(I);
    if (patched) DEP.Remove(I);
  }
};
int main() {
  for (int patched = 0; patched < 2; patched++) {
    Tracker t {}; t.patched = patched;
    const uint64_t P = 0x1000000;
    bool first = t.Promote(P) && t.Executable(P);
    bool again = t.Promote(P) && t.Executable(P);            // still promoted and executable: as before
    t.Free(P);
    bool after_free_not_exec = !t.Executable(P);
    t.Promote(P);                                             // allocated again, jumped into
    bool second = t.Executable(P);
    std::printf("patched=%d first=%d still=%d freed-not-exec=%d re-promoted-exec=%d\n", patched, first, again, after_free_not_exec, second);
  }
}
''')
            exe = Path(tmp) / "m"
            c = subprocess.run([cc, "-std=c++20", "-I", str(inc), "-o", str(exe), str(src)], capture_output=True, text=True)
            check("the interval model compiles against the real IntervalList.h", c.returncode == 0, c.stdout + c.stderr)
            if c.returncode == 0:
                out = subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines()
                check("OLD decisions: first promotion and an unchanged page work, but a page freed and allocated again stays NOT executable (the defect)",
                      out[0] == "patched=0 first=1 still=1 freed-not-exec=1 re-promoted-exec=0", out)
                check("PATCHED decisions: the same page is executable again; first promotion and an unchanged page are answered as before",
                      out[1] == "patched=1 first=1 still=1 freed-not-exec=1 re-promoted-exec=1", out)

    with tempfile.TemporaryDirectory(prefix="madeira-fex-dep-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        f = tree / FILE
        f.write_text(f.read_text().replace("if (DEPPromotedIntervals.Query(Address).Enclosed) {\n    return ProtInterval;", "if (DEPPromotedIntervals.Query(Address).Enclosed || false) {\n    return ProtInterval;"))
        stale = f.read_text()
        r3 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and writes nothing", r3.returncode != 0 and "not found once" in r3.stderr and f.read_text() == stale, r3.stdout + r3.stderr)

print("PASS: FEX DEP-off stale promotion" if ok else "FAILED: FEX DEP-off stale promotion")
sys.exit(0 if ok else 1)
