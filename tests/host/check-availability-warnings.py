#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Compiler availability warnings above the app's oldest iOS stop the build (2026-11-20; 0.1.682's pipe2 crash on iOS 26).

  1. CI: the step runs after the archive, reads the app's MinimumOSVersion and scans the build's diagnostics;
  2. the parser on fixture lines: pipe2 (iOS 27.0) caught with --min 18.0, os_sync (iOS 17.4) let through;
  3. on a Mac with Xcode: a real clang run calling pipe2 for an iOS 17 target writes the warning, and the script fails on it.
"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)[:1500]))
    ok = ok and bool(cond)


ci = (R / ".github/workflows/build-ipa.yml").read_text()
step = ci.find("      - name: Check compiler availability warnings above the app's oldest iOS\n")
check("CI: the step reads MinimumOSVersion and scans build/, the archive and the unix-side logs, after the archive and before packaging",
      step > 0 and ci.index("Madeira.xcarchive") < step < ci.index("      - name: Package Madeira and Madeira2 unsigned IPAs")
      and "plutil -extract MinimumOSVersion raw" in ci[step:step + 600]
      and 'python3 tools/check-availability-warnings.py --min "$MIN" build archive.log ntdll.log win32u.log' in ci[step:step + 600])
check("CI runs this check", "run: python3 tests/host/check-availability-warnings.py" in ci)

spec = importlib.util.spec_from_file_location("caw", R / "tools/check-availability-warnings.py")
caw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(caw)
text = """server_ios.c:5116:23: warning: 'pipe2' is only available on iOS 27.0 or newer [-Wunguarded-availability-new]
sync.c:88:5: warning: 'os_sync_wait_on_address' is only available on iOS 17.4 or newer [-Wunguarded-availability-new]
x.m:3:1: warning: 'windows' is deprecated: first deprecated in iOS 15.0
"""
check("the parser: pipe2 (27.0) above iOS 18.0 caught, os_sync (17.4) and deprecations let through",
      caw.findings(text, (18, 0)) == {("pipe2", "27.0")})

if sys.platform == "darwin" and shutil.which("xcrun"):
    with tempfile.TemporaryDirectory(prefix="bcd-avail-") as t:
        src = Path(t, "p.c")
        src.write_text("#include <unistd.h>\n#include <fcntl.h>\nint f(int *p) { return pipe2(p, O_CLOEXEC); }\n")
        c = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", "-arch", "arm64", "-miphoneos-version-min=17.0", "-c", str(src),
                            "-o", str(Path(t, "p.o"))], capture_output=True, text=True)
        Path(t, "p.err").write_text(c.stderr)
        r = subprocess.run([sys.executable, str(R / "tools/check-availability-warnings.py"), "--min", "18.0", t], capture_output=True, text=True)
        if "pipe2" not in c.stderr:
            print("skip this SDK does not mark pipe2 as newer than iOS 17 (an SDK before iOS 27)")
        else:
            check("a real clang run: pipe2 for an iOS 17 target warns, and the script fails on it", r.returncode == 1 and "pipe2" in r.stdout,
                  r.stdout + c.stderr)
else:
    print("skip the clang run needs macOS with Xcode")

print("PASS: availability warnings" if ok else "FAILED: availability warnings")
sys.exit(0 if ok else 1)
