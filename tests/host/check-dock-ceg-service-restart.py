#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dock-ceg-service-restart.py: Valve's client service is looked at once more after its 2 s settle and started again when it died (night round
2026-11-11, G2 S3; Bionic Commando R0GHX7, Mafia II HJZJ1P, 7XG9VK, WFRZ8A: the service reported running, died after the settle, and the host asked it for the job).

Checks: CI order (after patch-dock-ceg-service-start.py, before the Dock build, without failing the step); the app keeps the two report fields; the catalog lists
MADEIRA_DOCK_CEG_SERVICE_RESTART (default on); on a copy of src/scm.c after the service-start patch the patch applies once and only ADDS lines, a second run changes
nothing, a moved anchor and a missing service-start patch refuse it; the whole host compiles with build/madeira-dock/build.sh's flags where llvm-mingw is at hand.
"""
from pathlib import Path
import difflib, importlib.util, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("cegrestart", root / "tools/patch-dock-ceg-service-restart.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch after the service-start patch, before the Dock build, without failing the step",
      "python3 tests/host/check-dock-ceg-service-restart.py\n" in step
      and 'python3 tools/patch-dock-ceg-service-restart.py madeira-dock/src/scm.c || echo "::warning::' in step
      and step.index("patch-dock-ceg-service-start.py") < step.index("patch-dock-ceg-service-restart.py") < step.index("bash build/madeira-dock/build.sh"))
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app keeps the two report fields", '"ceg-service-alive-after-settle"' in allowed and '"ceg-service-restarts"' in allowed)
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_DOCK_CEG_SERVICE_RESTART, default on, from the patch",
      re.search(r'"env\.MADEIRA_DOCK_CEG_SERVICE_RESTART": \{[^}]*"default": "1"[^}]*"sources": \["tools/patch-dock-ceg-service-restart\.py"\]', cat, re.S) is not None)

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/scm.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = (dock / "src/scm.c").read_text()
    if "madeira-bcd: ceg-service-start-v1" in orig:
        print("note: the Dock tree is already patched; the source checks are skipped")
    else:
        with tempfile.TemporaryDirectory(prefix="madeira-dock-ceg-restart-") as td:
            t = Path(td)
            shutil.copytree(dock / "src", t / "src")
            sc = t / "src/scm.c"
            script = str(root / "tools/patch-dock-ceg-service-restart.py")
            r0 = subprocess.run([sys.executable, script, str(sc)], capture_output=True, text=True)
            check("without the service-start patch it refuses and leaves the file alone", r0.returncode != 0 and "service-start" in r0.stderr and sc.read_text() == orig, r0.stderr)
            r = subprocess.run([sys.executable, str(root / "tools/patch-dock-ceg-service-start.py"), str(sc)], capture_output=True, text=True)
            check("the service-start patch applies to the copy", r.returncode == 0, r.stdout + r.stderr)
            before = sc.read_text()
            r1 = subprocess.run([sys.executable, script, str(sc)], capture_output=True, text=True)
            once = sc.read_text()
            r2 = subprocess.run([sys.executable, script, str(sc)], capture_output=True, text=True)
            check("it applies once; a second run says so and changes nothing",
                  r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and sc.read_text() == once and P.MARKER in once, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
            ops = [o for o in difflib.SequenceMatcher(None, before.splitlines(), once.splitlines(), autojunk=False).get_opcodes() if o[0] != "equal"]
            check("nothing is removed or changed: the patch only adds lines", ops and all(o[0] == "insert" for o in ops), ops)
            bad = t / "bad.c"
            bad.write_text(before.replace("        if (!result) o->sleep_ms(SH_SERVICE_SETTLE_MS);\n", "        if (!result) o->sleep_ms(SH_SERVICE_SETTLE_MS * 2);\n"))
            r3 = subprocess.run([sys.executable, script, str(bad)], capture_output=True, text=True)
            check("a moved anchor refuses the patch and leaves the file alone", r3.returncode != 0 and "start_client_service" in r3.stderr and P.MARKER not in bad.read_text(), r3.stderr)
            mingw = next((m for m in (root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                                      Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                          if (m / "x86_64-w64-mingw32-clang").exists()), None)
            if mingw:
                b = subprocess.run([str(mingw / "x86_64-w64-mingw32-clang"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-cast-function-type",
                                    "-o", str(t / "dockhost.exe")] + sorted(str(f) for f in (t / "src").glob("*.c")) + ["-ladvapi32"], capture_output=True, text=True)
                check("the patched host compiles with build/madeira-dock/build.sh's flags", b.returncode == 0 and (t / "dockhost.exe").is_file(), b.stderr[-1500:])
            else:
                print("skip the Dock compile: no llvm-mingw at hand")
print("PASS: Dock CEG service restart" if ok else "FAILED: Dock CEG service restart")
sys.exit(0 if ok else 1)
