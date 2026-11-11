#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-wow-recursive-tcm.py: FEX WOW64's thread-creation lock can be taken again by its owner (night round 2026-11-11, G5 S3 / G7 S13).

FTL (PHE15B, YSDHV6, VT69D9) and Peggle Deluxe (SF5ZDS, METSK8) never drew a frame: BASS.dll passes VirtualProtect an OldProtect pointer into its own
SMC-armed RWX page; wow64.dll's thunk holds ThreadCreationMutex across the unix call, the store into the armed page faults in host code, and the
exception handler locks the same std::mutex on the same thread (`[srw-hang] ml2250 ... waits 30s for an SRW lock`). The patch replaces the
std::mutex by a wrapper (the same std::mutex, plus owner and depth; constinit, zero state, nothing built at start-up).

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned sources in the WOW64
build's order (tests/host/fex_chain.py), a second run says "already in" and changes nothing, a moved anchor refuses it, only the one declaration
line changes; every user of the lock in the patched file is a scoped_lock, unique_lock or lock()/unlock() pair; the class cut from the patched file
and compiled on the host: a nested lock by the owner does not block (with a plain std::mutex the same nesting does: try_lock fails), another
thread is kept out until the LAST unlock, 8 threads x 20000 increments with nested locking lose none, and the object is constant-initialised
(an all-zero image works). The WOW64 build applies it (tools/build-xtajit-wow64.sh and build/fex-wow64/build.sh); CI runs this check.
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


NAME = "patch-fex-ios-wow-recursive-tcm.py"
MARK = "wow64-tcm-recursive v1 madeira-bcd"
patch = root / "tools" / NAME
check("the patch script exists", patch.is_file())
w64 = (root / "tools/build-xtajit-wow64.sh").read_text()
restore = w64[w64.index("restore() {"):w64.index("trap restore EXIT")]
check("the WOW64 build applies it once, without failing the build, ahead of the build, and restores Module.cpp",
      w64.count('python3 "$R/tools/' + NAME + '" "$R/FEX" || echo "::warning::') == 1 and w64.index(NAME) < w64.index("trap restore EXIT")
      and "Source/Windows/WOW64/Module.cpp" in restore)
manual = (root / "build/fex-wow64/build.sh").read_text()
check("the manual WOW64 build script applies it too and restores Module.cpp",
      NAME in manual and "Source/Windows/WOW64/Module.cpp" in manual[manual.index("trap "):])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-wow-recursive-tcm.py" in wf)
rules = (root / "build/tools/autotune_rules.py").read_text()
# The rule needs the verdict fact for the stuck SRW wait (AutoTuneCore.swift `srwHangs`, W-AT's file): without it the session is scored inconclusive and no rule is
# evaluated, so no fixture can prove one yet. When the rule is added it must be a fixedIn-652 rule.
check("auto-tune: if the rule wow64-protect-old-prot-self-deadlock exists it is a fixedIn-652 rule",
      '"id": "wow64-protect-old-prot-self-deadlock"' not in rules
      or re.search(r'"id": "wow64-protect-old-prot-self-deadlock",\s*"lines": \[\],\s*"fixedIn": 652', rules) is not None)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
module_rel = "Source/Windows/WOW64/Module.cpp"
if not (fex / module_rel).is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-fex-tcm-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        res = fex_chain.run_chain(root, "wow", tree, before=NAME)
        check("the build's scripts ahead of it apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
        m = tree / module_rel
        before = m.read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        once = m.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and m.read_text() == once and once != before,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        res = fex_chain.run_chain(root, "wow", tree, after=NAME)
        check("the scripts behind it still apply (" + str(len(res)) + ")", not fex_chain.failures(res), fex_chain.failures(res))
        a, b = before.splitlines(), once.splitlines()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
        check("exactly one line is replaced (`std::mutex ThreadCreationMutex;`), nothing else changes",
              len(ops) == 1 and ops[0][0] == "replace" and a[ops[0][1]:ops[0][2]] == ["std::mutex ThreadCreationMutex;"], ops)
        check("no plain std::mutex ThreadCreationMutex is left", "std::mutex ThreadCreationMutex;" not in once)
        uses = [l.strip() for l in once.splitlines() if "ThreadCreationMutex" in l and not l.strip().startswith("//")
                and not l.startswith("class RecursiveThreadCreationMutex")]
        bad = [l for l in uses if not (re.match(r"std::(scoped_lock|unique_lock) Lock\(ThreadCreationMutex\);", l)
                                       or re.match(r"ThreadCreationMutex\.(lock|unlock)\(\);", l)
                                       or l == "constinit RecursiveThreadCreationMutex ThreadCreationMutex;")]
        check("every user of the lock (" + str(len(uses)) + " lines) needs only lock()/unlock()", not bad, bad)

        c0 = once.index("class RecursiveThreadCreationMutex {")
        c1 = once.index("constinit RecursiveThreadCreationMutex ThreadCreationMutex;\n") + len("constinit RecursiveThreadCreationMutex ThreadCreationMutex;\n")
        cls = once[c0:c1]
        harness = r'''
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <future>
#include <mutex>
#include <thread>
#include <vector>
struct _TEB {};
static inline _TEB* CurrentTEB() { static thread_local char id; return reinterpret_cast<_TEB*>(&id); }
namespace {
''' + cls + r'''
}
static long counter;
int main() {
  // 1. nested lock by the owner does not block; the plain std::mutex it replaces would
  { std::mutex plain; plain.lock(); bool again = plain.try_lock(); printf("plain-nested-try-lock=%d\n", again ? 1 : 0); if (again) plain.unlock(); plain.unlock(); }
  {
    auto f = std::async(std::launch::async, [] { ThreadCreationMutex.lock(); ThreadCreationMutex.lock(); ThreadCreationMutex.lock();
                                                  ThreadCreationMutex.unlock(); ThreadCreationMutex.unlock(); ThreadCreationMutex.unlock(); return 1; });
    printf("nested-lock-finishes=%d\n", f.wait_for(std::chrono::seconds(5)) == std::future_status::ready ? 1 : 0);
  }
  // 2. another thread is kept out until the LAST unlock
  {
    ThreadCreationMutex.lock(); ThreadCreationMutex.lock(); ThreadCreationMutex.unlock();
    std::atomic<int> got {0};
    std::thread t([&] { ThreadCreationMutex.lock(); got = 1; ThreadCreationMutex.unlock(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
    printf("other-thread-in-while-one-hold-left=%d\n", got.load());
    ThreadCreationMutex.unlock();
    t.join();
    printf("other-thread-in-after-last-unlock=%d\n", got.load());
  }
  // 3. exclusion with nested locking
  {
    std::vector<std::thread> ts;
    for (int i = 0; i < 8; i++) ts.emplace_back([] { for (int k = 0; k < 20000; k++) { std::scoped_lock a(ThreadCreationMutex); { std::scoped_lock b(ThreadCreationMutex); counter++; } } });
    for (auto& t : ts) t.join();
    printf("counter=%ld\n", counter);
  }
  // 4. constant initialisation: the object is all zero before its first use
  printf("zero-state-size=%zu\n", sizeof(ThreadCreationMutex));
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
            c = subprocess.run([cc, "-std=c++20", "-O1", "-pthread", "-o", str(exe), str(src)], capture_output=True, text=True)
            check("the class (cut from the patched file) compiles on the host, constinit included", c.returncode == 0, c.stdout + c.stderr)
            if c.returncode == 0:
                out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120).stdout.splitlines()
                d = dict(l.split("=", 1) for l in out if "=" in l)
                check("a plain std::mutex locked twice by one thread would block (the defect: try_lock fails)", d.get("plain-nested-try-lock") == "0", out)
                check("the new lock taken three times by its owner does not block", d.get("nested-lock-finishes") == "1", out)
                check("another thread stays out while one hold is left and gets in after the last unlock",
                      d.get("other-thread-in-while-one-hold-left") == "0" and d.get("other-thread-in-after-last-unlock") == "1", out)
                check("8 threads x 20000 nested increments lose none", d.get("counter") == "160000", out)

    with tempfile.TemporaryDirectory(prefix="madeira-fex-tcm-") as tmp:
        tree = fex_chain.copy_sources(fex, Path(tmp) / "t")
        m = tree / module_rel
        m.write_text(m.read_text().replace("std::mutex ThreadCreationMutex;", "std::mutex ThreadCreationMutex2;"))
        stale = m.read_text()
        r3 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and writes nothing", r3.returncode != 0 and "not found once" in r3.stderr and m.read_text() == stale, r3.stdout + r3.stderr)

print("PASS: FEX WOW64 thread-creation lock" if ok else "FAILED: FEX WOW64 thread-creation lock")
sys.exit(0 if ok else 1)
