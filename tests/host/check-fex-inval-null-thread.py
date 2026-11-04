#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-inval-null-thread.py: the code-invalidation walk skips a thread state without its caches (round 630, agent O2 S7).

Outlast (Y09766, build 623): ContextImpl::InvalidateThreadCachedCodeRange faulted on a thread whose FrontendDecoder was NULL, with
FEX's CodeInvalidationMutex and the thread mutex held, and the whole process froze.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned
Core.cpp alone (the WOW64 build) and after the Mono and exit-runaway patches (the ARM64EC build's order), a second run says "already in"
and changes nothing, a moved anchor refuses it; the guard is the first thing after the mutex assertion and before the decoder's first
use, under FEX_IOS_HOST, returns for a NULL thread, decoder or lookup cache and logs at most 8 lines; nothing else changes. Both module
builds apply it without failing the build and restore Core.cpp; the auto-tune rule; CI.
"""
from pathlib import Path
import difflib, os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


patch = root / "tools/patch-fex-ios-inval-null-thread.py"
w64 = (root / "tools/build-xtajit-wow64.sh").read_text()
ec = (root / "tools/build-xtajit64.sh").read_text()
check("the WOW64 build applies it without failing the build and restores Core.cpp",
      'python3 "$R/tools/patch-fex-ios-inval-null-thread.py" "$R/FEX" || echo "::warning::' in w64
      and "FEXCore/Source/Interface/Core/Core.cpp" in w64[w64.index("restore() {"):w64.index("trap restore EXIT")]
      and w64.index("patch-fex-ios-inval-null-thread.py") < w64.index("trap restore EXIT"))
restore = ec[ec.index('git -C FEX checkout -- "$CPUF"'):]
restore = restore[:restore.index("\n")]
check("the ARM64EC build applies it after the exit-runaway stop, without failing the build, and restores Core.cpp",
      'python3 "$R/tools/patch-fex-ios-inval-null-thread.py" "$R/FEX" || echo "::warning::' in ec
      and ec.index("patch-fex-ios-exit-runaway.py\" \"$R/FEX\"") < ec.index("patch-fex-ios-inval-null-thread.py\" \"$R/FEX\"")
      and "FEXCore/Source/Interface/Core/Core.cpp" in restore)
rules = (root / "build/tools/autotune_rules.py").read_text()
check("auto-tune names it fixed in 630", '"id": "fex-inval-null-thread"' in rules)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-inval-null-thread.py" in wf)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
core = fex / "FEXCore/Source/Interface/Core/Core.cpp"
if not core.is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX)")
else:
    for order in (["inval"], ["mono", "runaway", "inval"]):
        with tempfile.TemporaryDirectory(prefix="madeira-fex-inval-") as tmp:
            tree = Path(tmp)
            (tree / "FEXCore/Source/Interface/Core").mkdir(parents=True)
            t = tree / "FEXCore/Source/Interface/Core/Core.cpp"
            shutil.copy(core, t)
            if "mono" in order:
                subprocess.run([sys.executable, str(root / "tools/patch-fex-ios-mono-bp-unlocked-miss.py"), str(t)], capture_output=True, text=True)
                subprocess.run([sys.executable, str(root / "tools/patch-fex-ios-exit-runaway.py"), str(tree)], capture_output=True, text=True)
            before = t.read_text()
            r1 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
            once = t.read_text()
            r2 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
            label = "+".join(order)
            check(label + ": it applies once; a second run changes nothing",
                  r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == once and once != before,
                  r1.stdout + r1.stderr + r2.stdout + r2.stderr)
            fn = once[once.index("void ContextImpl::InvalidateThreadCachedCodeRange("):]
            fn = fn[:fn.index("\n}\n")]
            check(label + ": the guard follows the mutex assertion and comes before the decoder's first use, under FEX_IOS_HOST",
                  fn.index("CodeInvalidationMutex needs to be unique_locked here") < fn.index("#ifdef FEX_IOS_HOST")
                  < fn.index("if (!Thread || !Thread->FrontendDecoder || !Thread->LookupCache) {") < fn.index("return;")
                  < fn.index("#endif") < fn.index("Thread->FrontendDecoder->ResetExecutableRangeCache();")
                  and "NullN.fetch_add(1, std::memory_order_relaxed) < 8" in fn)
            a, b = before.splitlines(), once.splitlines()
            ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
            check(label + ": nothing else changes (one insertion)", len(ops) == 1 and ops[0][0] == "insert", ops)
    with tempfile.TemporaryDirectory(prefix="madeira-fex-inval-") as tmp:
        tree = Path(tmp)
        (tree / "FEXCore/Source/Interface/Core").mkdir(parents=True)
        t = tree / "FEXCore/Source/Interface/Core/Core.cpp"
        t.write_text(core.read_text().replace('"CodeInvalidationMutex needs to be unique_locked here");', '"CodeInvalidationMutex must be held");'))
        r3 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a moved anchor refuses the patch", r3.returncode != 0 and "not found once" in r3.stderr)

print("PASS: the code-invalidation walk skips a thread state without its caches" if ok else "FAILED: FEX invalidation-walk guard")
sys.exit(0 if ok else 1)
