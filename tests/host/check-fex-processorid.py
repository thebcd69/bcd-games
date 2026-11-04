#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-fex-ios-processorid.py: RDTSCP/RDPID report processor 0 instead of ending the process (round 630, agent G3 S11).

Hearts of Iron IV's dowser.exe (TM3HCY): FEX's ProcessorID op died at translation time ("A <tid> Unsupported") on a host without a CPU
number in TPIDRRO_EL0.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned
MiscOps.cpp, a second run says "already patched" and changes nothing; the assertion is gone from DEF_OP(ProcessorID)'s Windows branch and
the branch loads 0 into the result and returns; the TPIDRRO path and the non-Windows path are untouched; a source where FEX handles it
itself is left alone; both module builds apply it without failing the build and restore MiscOps.cpp; CI runs this check.
"""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
patch = root / "tools/patch-fex-ios-processorid.py"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1200]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


ec = (root / "tools/build-xtajit64.sh").read_text()
w64 = (root / "tools/build-xtajit-wow64.sh").read_text()
check("the ARM64EC build applies it without failing the build and restores MiscOps.cpp",
      'python3 "$R/tools/patch-fex-ios-processorid.py" "$R/FEX/FEXCore/Source/Interface/Core/JIT/MiscOps.cpp" || echo "::warning::' in ec
      and [l for l in ec.splitlines() if l.startswith("git -C FEX checkout --")] != []
      and " FEXCore/Source/Interface/Core/JIT/MiscOps.cpp" in [l for l in ec.splitlines() if l.startswith("git -C FEX checkout --")][0])
check("the WOW64 build applies it without failing the build and restores MiscOps.cpp",
      'python3 "$R/tools/patch-fex-ios-processorid.py" "$R/FEX/FEXCore/Source/Interface/Core/JIT/MiscOps.cpp" || echo "::warning::' in w64
      and "FEXCore/Source/Interface/Core/JIT/MiscOps.cpp" in w64[w64.index("restore() {"):w64.index("trap restore EXIT")])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-fex-processorid.py" in wf)

fex = Path(os.environ.get("FEX_SRC", Path.home() / "madeira-bcd/FEX"))
src = fex / "FEXCore/Source/Interface/Core/JIT/MiscOps.cpp"
if not src.is_file():
    print("skip: no FEX tree here (FEX_SRC or ~/madeira-bcd/FEX)")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-fex-pid-") as tmp:
        t = Path(tmp) / "MiscOps.cpp"
        shutil.copy(src, t)
        before = t.read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        once = t.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              "applied" in r1.stdout and "already patched" in r2.stdout and t.read_text() == once and once != before, r1.stdout + r2.stdout)
        fn = once[once.index("DEF_OP(ProcessorID) {"):]
        fn = fn[:fn.index("\n}\n")]
        win = fn[fn.index("#ifdef _WIN32"):fn.index("#else")]
        check("the Windows branch loads 0 and returns; no assertion is left in the op",
              'ERROR_AND_DIE_FMT("Unsupported")' not in fn and "LoadConstant(ARMEmitter::Size::i32Bit, GetReg(Node), 0);\n    return;" in win)
        check("the TPIDRRO path and the non-Windows path are untouched",
              "mrs(GetReg(Node), ARMEmitter::SystemRegister::TPIDRRO_EL0);" in fn
              and before[before.index("DEF_OP(ProcessorID) {"):].split("#else", 1)[1][:1500] == fn.split("#else", 1)[1][:1500])
        t.write_text(before.replace('    ERROR_AND_DIE_FMT("Unsupported");\n', '    LoadConstant(ARMEmitter::Size::i32Bit, GetReg(Node), 1);\n', 1))
        r3 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        check("a FEX that handles it itself is left alone", r3.returncode == 0 and "FEX handles it itself" in r3.stdout)

print("PASS: RDTSCP/RDPID report processor 0" if ok else "FAILED: FEX ProcessorID")
sys.exit(0 if ok else 1)
