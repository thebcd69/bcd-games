#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A Steam game's Launch arguments (2026-11-20, a Discord request: Steam launch options such as -dx9, -windowed, -vulkan).

Checks:
  - CI runs this check and applies tools/patch-dock-user-args.py in the Dock build step after the other launch.c patches and before
    build.sh, without failing the step; the app keeps the report field launch-user-args-length;
  - the app: MadeiraDock.configure sets MADEIRA_STEAM_HOST_ARGUMENTS from the game's Launch arguments and removes it when there are none
    (the host then passes "" as before), ContentView's Dock start hands the entry's arguments over, a Steam game started as "The game"
    gets Steam's arguments followed by the entry's, and the game page offers Launch arguments for Steam games;
  - Dock source (madeira-dock/ of this tree or ~/madeira-bcd/madeira-dock; skipped without it): on a copy of src/launch.c the patch reads
    the variable once after the launch option and passes it to both LaunchApp calls; a second run changes nothing; a moved anchor refuses
    it; the patched host compiles with build.sh's flags where llvm-mingw is at hand.
"""
from pathlib import Path
import importlib.util, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("userargs", root / "tools/patch-dock-user-args.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf[wf.index("      - name: Build Madeira Dock host\n"):]
step = step[:step.index("\n\n")]
check("CI runs this check and applies the patch after the other launch.c patches, before the Dock build, without failing the step",
      "python3 tests/host/check-dock-user-args.py\n" in step
      and 'python3 tools/patch-dock-user-args.py madeira-dock/src/launch.c || echo "::warning::' in step
      and step.index("patch-dock-ceg-already-prepared.py") < step.index("patch-dock-user-args.py") < step.index("bash build/madeira-dock/build.sh"))
dk = (root / "app/Madeira/MadeiraDock.swift").read_text()
allowed = dk[dk.index("static let reportAllowed: Set<String> = ["):]
allowed = allowed[:allowed.index("]")]
check("the app keeps the report field launch-user-args-length", '"launch-user-args-length"' in allowed)
check("MadeiraDock.configure sets MADEIRA_STEAM_HOST_ARGUMENTS from the arguments, and removes it when there are none",
      'static func configure(_ game: DockGame, launchOption: UInt32 = 0, arguments: String = "") {' in dk
      and 'if userArguments.isEmpty { unsetenv("MADEIRA_STEAM_HOST_ARGUMENTS") } else { setenv("MADEIRA_STEAM_HOST_ARGUMENTS", userArguments, 1) }' in dk)
cv = (root / "app/Madeira/ContentView.swift").read_text()
check("the Dock start hands the entry's Launch arguments over",
      'MadeiraDock.configure(game, launchOption: launchOption, arguments: profile?.arguments ?? "")' in cv)
lib = (root / "app/Madeira/Library.swift").read_text()
check("\"The game\": Steam's arguments for the program, then the entry's",
      'return [steamProgramArguments ?? "", arguments].map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }.joined(separator: " ")' in lib
      and "guard startsSteamGameDirectly else { return arguments }" in lib)
page = (root / "app/Madeira/BCDGamePageLive.swift").read_text()
check("the game page offers Launch arguments for Steam games too (not for the Wine desktop)",
      "if entry.desktop != true {\n" in page and "entry.desktop != true && entry.steamAppID == nil" not in page
      and "Steam hands them to the game when it starts it, like Steam's own Launch Options." in page)

dock = next((d for d in (root / "madeira-dock", Path.home() / "madeira-bcd/madeira-dock") if (d / "src/launch.c").is_file()), None)
if dock is None:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")
else:
    orig = (dock / "src/launch.c").read_text()
    if P.MARKER in orig:
        orig = orig.replace(P.READ_NEW, P.READ_OLD).replace(P.CALL_NEW, P.CALL_OLD)
    once = P.patch(orig)
    check("the patch reads the variable once after the launch option and passes it to both LaunchApp calls",
          once.count('GetEnvironmentVariableA("MADEIRA_STEAM_HOST_ARGUMENTS"') == 1 and once.count(P.CALL_NEW) == 2 and P.CALL_OLD not in once
          and once.index(P.READ_OLD) < once.index("static char user_args[4096];") < once.index(P.CALL_NEW))
    check("a second run changes nothing", P.patch(once) == once)
    try:
        P.patch(orig.replace(P.READ_OLD, P.READ_OLD.replace("launch-option-index", "launch-option-number")))
        check("a moved anchor refuses the patch", False)
    except ValueError:
        check("a moved anchor refuses the patch", True)
    mingw = next((m for m in (root / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin",
                              Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin")
                  if (m / "x86_64-w64-mingw32-clang").exists()), None)
    if mingw:
        with tempfile.TemporaryDirectory(prefix="bcd-dock-args-") as t:
            t = Path(t)
            shutil.copytree(dock / "src", t / "src")
            (t / "src/launch.c").write_text(once)
            b = subprocess.run([str(mingw / "x86_64-w64-mingw32-clang"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-cast-function-type",
                                "-o", str(t / "dockhost.exe")] + sorted(str(f) for f in (t / "src").glob("*.c")) + ["-ladvapi32"],
                               capture_output=True, text=True)
            check("the patched host compiles with build/madeira-dock/build.sh's flags", b.returncode == 0 and (t / "dockhost.exe").is_file(), b.stderr[-1500:])
    else:
        print("skip the Dock compile: no llvm-mingw at hand")

print("PASS: Dock user arguments" if ok else "FAILED: Dock user arguments")
sys.exit(0 if ok else 1)
