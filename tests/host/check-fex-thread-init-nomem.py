#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-thread-init-nomem.py: a 32-bit thread without an emulator state ends alone (round 630, agent A2 S3).

The 32-bit Steam client on a 63 GB map (9PB1B5, W2J15H, R883EG) filled the emulator's window; FEXCore's CreateThread returned
NULL and BTCpuThreadInit read through it (libwow64fex.dll+0x102af4, fault-addr 0x0), which ended the whole client.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned
WOW64/Module.cpp after the wow64-ctor guard (the build's order), a second run says "already in" and changes nothing; the NULL test
comes right after CreateThread and before the first use of the state; the lock is a unique_lock on the same mutex and is released
before NtTerminateThread, which comes before the return (declared at file scope: FEX's winternl.h lacks it); nothing else in the file changes; both WOW64 builds apply it without
failing the build and restore Module.cpp; CI runs this check.
"""
from pathlib import Path
import difflib, os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


patch = root / "tools/patch-fex-ios-thread-init-nomem.py"
ctor = root / "tools/patch-fex-ios-wow-threads-ctor.py"
fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
module = fex / "Source/Windows/WOW64/Module.cpp"

ci = (root / "tools/build-xtajit-wow64.sh").read_text()
local = (root / "build/fex-wow64/build.sh").read_text()
check("the CI build applies it after the thread-map guard, without failing the build, and restores Module.cpp",
      'python3 "$R/tools/patch-fex-ios-thread-init-nomem.py" "$R/FEX" || echo "::warning::' in ci
      and ci.index("patch-fex-ios-wow-threads-ctor.py\" \"$R/FEX\"") < ci.index("patch-fex-ios-thread-init-nomem.py\" \"$R/FEX\"")
      < ci.index("trap restore EXIT") and "Source/Windows/WOW64/Module.cpp" in ci[ci.index("restore() {"):ci.index("trap restore EXIT")])
check("the local WOW64 build applies it the same way, before its restore trap",
      'python3 "$R/tools/patch-fex-ios-thread-init-nomem.py" "$R/FEX" || echo' in local
      and local.index("patch-fex-ios-thread-init-nomem.py") < local.index("trap 'git -C \"$R/FEX\" checkout -- Source/Windows/WOW64/Module.cpp"))
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-thread-init-nomem.py" in wf)

if not module.is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX); the CI and the Mac run the source part")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-fex-nomem-") as tmp:
        tree = Path(tmp)
        (tree / "Source/Windows/WOW64").mkdir(parents=True)
        t = tree / "Source/Windows/WOW64/Module.cpp"
        shutil.copy(module, t)
        rc = subprocess.run([sys.executable, str(ctor), str(tree)], capture_output=True, text=True)
        check("the thread-map guard applies first, as in the build", rc.returncode == 0, rc.stdout + rc.stderr)
        before = t.read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        once = t.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and t.read_text() == once and once != before,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        fn = once[once.index("void BTCpuThreadInit() {"):]
        fn = fn[:fn.index("\n}\n")]
        create = fn.index("auto* Thread = CTX->CreateThread(0, 0);")
        test = fn.index("  if (!Thread) {")
        first_use = fn.index("auto Frame = Thread->CurrentFrame;")
        check("the NULL test is right after CreateThread, inside FEX_IOS_HOST, before the first use of the state",
              create < fn.index("#ifdef FEX_IOS_HOST") < test < fn.index("#endif") < first_use)
        guard = fn[test:fn.index("#endif")]
        check("the lock is a unique_lock on the creation mutex, released before NtTerminateThread, which comes before the return",
              "  std::unique_lock Lock(ThreadCreationMutex);" in fn and "std::scoped_lock" not in fn[:create]
              and guard.index("Lock.unlock();") < guard.index("NtTerminateThread(NtCurrentThread(), STATUS_NO_MEMORY);") < guard.index("return;")
              and "[wow64-tls] tid={:#x} CreateThread returned null" in guard)
        a, b = before.splitlines(), once.splitlines()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
        check("nothing else in the file changes: the declaration is inserted before the function, the lock line is replaced, the guard is inserted",
              len(ops) == 3 and ops[0][0] == "insert" and b[ops[0][4]] == "void BTCpuThreadInit() {"
              and 'extern "C" NTSTATUS WINAPI NtTerminateThread(HANDLE, LONG);' in b[ops[0][3]:ops[0][4]]
              and ops[1][0] == "replace" and a[ops[1][1]:ops[1][2]] == ["  std::scoped_lock Lock(ThreadCreationMutex);"]
              and ops[2][0] == "insert" and b[ops[2][3] - 1] == "  auto* Thread = CTX->CreateThread(0, 0);", ops)
        # A changed pin refuses the patch (the build then goes on without it).
        t.write_text(before.replace("auto* Thread = CTX->CreateThread(0, 0);", "auto* Thread = CTX->CreateThread(0, 1);"))
        r3 = subprocess.run([sys.executable, str(patch), str(tree)], capture_output=True, text=True)
        check("a changed anchor refuses the patch and leaves the file alone",
              r3.returncode != 0 and "anchor changed" in r3.stderr and "thread-init-nomem" not in t.read_text())

print("PASS: a 32-bit thread without an emulator state ends alone" if ok else "FAILED: FEX thread-init NULL guard")
sys.exit(0 if ok else 1)
