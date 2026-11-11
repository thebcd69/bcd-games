#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dock-keep-alive-tree.py: MADEIRA_DOCK_KEEP_ALIVE = * holds a Dock session while a program of the game's install folder runs (night round
2026-11-11, X2 S2; Control G2CRAR: the starter Control.exe ended, the host put Steam's entries back 15 s before Control_DX11.exe asked for Steam).

Checks: CI order (after patch-dock-keep-alive.py, before the Dock build, without failing the step; the catalog says the key takes *); on a copy of
src/launch.c after the earlier patches the patch applies once and only adds lines, a second run changes nothing, a moved anchor and a missing keep-alive
patch refuse it; the resident-helper deny-list built on the host (service, crash reporter, anti-cheat, uninstaller, setup and redistributable names are
denied, a game's own program is not); the whole host compiles with build/madeira-dock/build.sh's flags where llvm-mingw is at hand.
"""
from pathlib import Path
import difflib, importlib.util, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("keepalivetree", root / "tools/patch-dock-keep-alive-tree.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch after the keep-alive patch, before the Dock build, without failing the step",
      "python3 tests/host/check-dock-keep-alive-tree.py\n" in step
      and 'python3 tools/patch-dock-keep-alive-tree.py madeira-dock/src/launch.c || echo "::warning::' in step
      and step.index("patch-dock-keep-alive.py") < step.index("patch-dock-keep-alive-tree.py") < step.index("bash build/madeira-dock/build.sh"))
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("the catalog entry of MADEIRA_DOCK_KEEP_ALIVE names the * form", re.search(r'"env\.MADEIRA_DOCK_KEEP_ALIVE": \{[^}]*A single \*', cat, re.S) is not None)

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/launch.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = (dock / "src/launch.c").read_text()
    if "MADEIRA_DOCK_KEEP_ALIVE" in orig:
        print("note: the Dock tree is already patched; the source checks are skipped")
    else:
        with tempfile.TemporaryDirectory(prefix="madeira-dock-keep-alive-tree-") as td:
            t = Path(td)
            shutil.copytree(dock / "src", t / "src")
            lc = t / "src/launch.c"
            script = str(root / "tools/patch-dock-keep-alive-tree.py")
            r0 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
            check("without the keep-alive patch it refuses and leaves the file alone", r0.returncode != 0 and "keep-alive" in r0.stderr and lc.read_text() == orig, r0.stderr)
            r = subprocess.run([sys.executable, str(root / "tools/patch-dock-keep-alive.py"), str(lc)], capture_output=True, text=True)
            check("the keep-alive patch applies to the copy", r.returncode == 0, r.stdout + r.stderr)
            before = lc.read_text()
            r1 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
            once = lc.read_text()
            r2 = subprocess.run([sys.executable, script, str(lc)], capture_output=True, text=True)
            check("it applies once; a second run says so and changes nothing",
                  r1.returncode == 0 and r2.returncode == 0 and "already in" in r2.stdout and lc.read_text() == once and P.MARKER in once, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
            ops = [o for o in difflib.SequenceMatcher(None, before.splitlines(), once.splitlines(), autojunk=False).get_opcodes() if o[0] != "equal"]
            check("nothing is removed or changed: the patch only adds lines", ops and all(o[0] == "insert" for o in ops), ops)
            bad = t / "bad.c"
            bad.write_text(before.replace("    if (!n || n >= 1024) return false;\n", "    if (!n || n >= 1000) return false;\n"))
            r3 = subprocess.run([sys.executable, script, str(bad)], capture_output=True, text=True)
            check("a moved anchor refuses the patch and leaves the file alone", r3.returncode != 0 and "list test" in r3.stderr and P.MARKER not in bad.read_text(), r3.stderr)
            fn = once[once.index("static bool keep_alive_name_denied"):once.index("static bool install_tree_running")]
            harness = ("#include <stdbool.h>\n#include <stddef.h>\n#include <stdio.h>\n#include <wchar.h>\n#include <wctype.h>\n" + fn +
                       "int main(void){ const wchar_t *n[] = {L\"SteamService.exe\", L\"CrashReporter.exe\", L\"easyanticheat.exe\", L\"unins000.exe\", "
                       "L\"DXSETUP.exe\", L\"vc_redist.x64.exe\", L\"Control_DX11.exe\", L\"Control.exe\", L\"Game.exe\"};\n"
                       "for (size_t i = 0; i < sizeof n / sizeof n[0]; ++i) printf(\"%d\", keep_alive_name_denied(n[i]) ? 1 : 0); printf(\"\\n\"); return 0; }\n")
            cc = shutil.which("cc") or shutil.which("clang")
            if cc:
                (t / "h.c").write_text(harness)
                b = subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", str(t / "h.c"), "-o", str(t / "h")], capture_output=True, text=True)
                check("the deny-list builds on the host", b.returncode == 0, b.stderr[-1000:])
                if b.returncode == 0:
                    out = subprocess.run([str(t / "h")], capture_output=True, text=True).stdout.strip()
                    check("services, reporters, anti-cheat, uninstallers, setups and redistributables are denied; the game's own programs are not", out == "111111000", out)
            mingw = next((m for m in (root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                                      Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                          if (m / "x86_64-w64-mingw32-clang").exists()), None)
            if mingw:
                b = subprocess.run([str(mingw / "x86_64-w64-mingw32-clang"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-cast-function-type",
                                    "-o", str(t / "dockhost.exe")] + sorted(str(f) for f in (t / "src").glob("*.c")) + ["-ladvapi32"], capture_output=True, text=True)
                check("the patched host compiles with build/madeira-dock/build.sh's flags", b.returncode == 0 and (t / "dockhost.exe").is_file(), b.stderr[-1500:])
            else:
                print("skip the Dock compile: no llvm-mingw at hand")
print("PASS: Dock keep-alive tree" if ok else "FAILED: Dock keep-alive tree")
sys.exit(0 if ok else 1)
