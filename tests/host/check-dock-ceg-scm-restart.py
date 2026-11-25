#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dock-ceg-scm-restart.py: a Steam client service start of -1 in a service manager the Dock host started gets one fresh services.exe and one
more start (round 714, agent F1 S3; CEG class A: 066P0C, CSP7E6, CT98X6, NS4R70, EYCX54, PKTC7C).

Checks: CI order (after the service-start and service-restart patches, before the Dock build, without failing the step); the app keeps the three report
fields; the catalog lists MADEIRA_DOCK_CEG_SCM_RESTART (default on); on a pristine copy of the Dock's src (git HEAD of the submodule when the tree is already
patched) after the two earlier patches the patch applies once, a second run changes nothing, a moved anchor and a missing earlier patch refuse it; the new
block runs only after a -1, only for a manager this host started and only without the key's 0; the whole host compiles with build/madeira-dock/build.sh's
flags where llvm-mingw is at hand.
"""
from pathlib import Path
import importlib.util, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("cegscm", root / "tools/patch-dock-ceg-scm-restart.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch after the two service patches, before the Dock build, without failing the step",
      "python3 tests/host/check-dock-ceg-scm-restart.py\n" in step
      and 'python3 tools/patch-dock-ceg-scm-restart.py madeira-dock/src/scm.c || echo "::warning::' in step
      and step.index("patch-dock-ceg-service-start.py") < step.index("patch-dock-ceg-service-restart.py madeira-dock")
      < step.index("patch-dock-ceg-scm-restart.py madeira-dock") < step.index("bash build/madeira-dock/build.sh"))
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app keeps the three report fields",
      all('"%s"' % f in allowed for f in ("ceg-scm-restarted", "ceg-scm-restart-error", "ceg-service-start-after-restart")))
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog lists env.MADEIRA_DOCK_CEG_SCM_RESTART, default on, from the patch",
      re.search(r'"env\.MADEIRA_DOCK_CEG_SCM_RESTART": \{[^}]*"default": "1"[^}]*"sources": \["tools/patch-dock-ceg-scm-restart\.py"\]', cat, re.S) is not None)
check("the new block runs only after -1, for a manager this host started, without the key's 0",
      "if (started == -1 && manager_process && !wide_flag(L\"MADEIRA_DOCK_CEG_SCM_RESTART\", L'0')) {" in P.NEW
      and P.NEW.count("start_manager(") == 1 and P.NEW.count("start_client_service(") == 2
      and P.NEW.endswith("    CloseServiceHandle(manager);\n    return sh_ceg_scm_result(true, registered);\n"))

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/scm.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-dock-ceg-scm-") as td:
        t = Path(td)
        (t / "src").mkdir()
        patched = "madeira-bcd: ceg-service-start-v1" in (dock / "src/scm.c").read_text()
        for f in sorted((dock / "src").iterdir()):
            if f.suffix not in (".c", ".h"):
                continue
            if patched:
                r = subprocess.run(["git", "-C", str(dock), "show", "HEAD:src/" + f.name], capture_output=True, text=True)
                (t / "src" / f.name).write_text(r.stdout if r.returncode == 0 else f.read_text())
            else:
                shutil.copy(f, t / "src" / f.name)
        sc = t / "src/scm.c"
        orig = sc.read_text()
        script = str(root / "tools/patch-dock-ceg-scm-restart.py")
        r0 = subprocess.run([sys.executable, script, str(sc)], capture_output=True, text=True)
        check("without the earlier patches it refuses and leaves the file alone", r0.returncode != 0 and "service-start" in r0.stderr and sc.read_text() == orig, r0.stderr)
        for tool in ("patch-dock-ceg-service-start.py", "patch-dock-ceg-service-restart.py"):
            r = subprocess.run([sys.executable, str(root / "tools" / tool), str(sc)], capture_output=True, text=True)
            check("%s applies to the copy" % tool, r.returncode == 0, r.stdout + r.stderr)
        before = sc.read_text()
        r1 = subprocess.run([sys.executable, script, str(sc)], capture_output=True, text=True)
        once = sc.read_text()
        r2 = subprocess.run([sys.executable, script, str(sc)], capture_output=True, text=True)
        check("it applies once; a second run says so and changes nothing",
              r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and sc.read_text() == once and once.count(P.MARKER) == 1,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        check("the old two lines are replaced by the block and nothing else changed", once == before.replace(P.OLD, P.NEW, 1))
        bad = t / "bad.c"
        bad.write_text(before.replace('        o->event("ceg-service-start", start_client_service(o, manager));\n', '        o->event("ceg-service-start", start_client_service(o, manager) + 0);\n'))
        r3 = subprocess.run([sys.executable, script, str(bad)], capture_output=True, text=True)
        check("a moved anchor refuses the patch and leaves the file alone", r3.returncode != 0 and "sh_ceg_scm_prepare" in r3.stderr and P.MARKER not in bad.read_text(), r3.stderr)
        mingw = next((m for m in (root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                                  Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                      if (m / "x86_64-w64-mingw32-clang").exists()), None)
        if mingw:
            b = subprocess.run([str(mingw / "x86_64-w64-mingw32-clang"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-cast-function-type",
                                "-o", str(t / "dockhost.exe")] + sorted(str(f) for f in (t / "src").glob("*.c")) + ["-ladvapi32"], capture_output=True, text=True)
            check("the patched host compiles with build/madeira-dock/build.sh's flags", b.returncode == 0 and (t / "dockhost.exe").is_file(), b.stderr[-1500:])
        else:
            print("skip the Dock compile: no llvm-mingw at hand")
print("PASS: Dock CEG services.exe restart" if ok else "FAILED: Dock CEG services.exe restart")
sys.exit(0 if ok else 1)
