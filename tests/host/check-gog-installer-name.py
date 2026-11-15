#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 660 (M126; Send Log HSQDK4): the folder scan of drive C (GameLibrary.swift) never lists a GOG offline
installer, setup_<name>_<version>.exe, as a game. The function is compiled on the host as it is in the app and
asked about installer names and about game programs that only look a little like them."""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
src = (R / "app/Madeira/GameLibrary.swift").read_text(encoding="utf-8")
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


m = re.search(r"\n    static func isGOGInstallerName\(_ lower: String\) -> Bool \{\n.*?\n    \}\n", src, re.S)
check("GameLibrary.swift has isGOGInstallerName", m is not None)
check("the folder scan skips such a name next to the noise names",
      "if noiseNames.contains(entry.lastPathComponent.lowercased()) { continue }\n"
      "                    if isGOGInstallerName(entry.lastPathComponent.lowercased()) { continue }" in src)
swiftc = shutil.which("swiftc")
if m and swiftc:
    cases = {
        "setup_medal_of_honor_2.0.0.21.exe": True,     # HSQDK4
        "setup_the_witcher_3_4.04.exe": True,
        "setup_a_1.exe": True,
        "setup.exe": False,                           # a game's own setup without a version: left to the player
        "setup_medal_of_honor.exe": False,            # no version after the last "_"
        "setup__2.0.exe": False,                      # no name
        "setup_game_v2.0.exe": False,                 # a version that does not start with a digit
        "setup_witcher_3_4.04_(64bit)_(70442).exe": False,   # other GOG forms are not matched (the narrow shape only)
        "mohaa.exe": False,
        "game_setup_1.0.exe": False,
        "setup_game_1.0.dll": False,
    }
    body = "enum L {\n" + m.group(0) + "}\n"
    tests = "\n".join(f'check({name!r}.debugDescription, L.isGOGInstallerName("{name}") == {str(want).lower()})'
                      for name, want in cases.items())
    prog = body + """
var failed = 0
func check(_ what: String, _ cond: Bool) { print((cond ? "ok   " : "FAIL ") + what); if !cond { failed += 1 } }
""" + tests.replace("'", '"') + "\nexit(failed == 0 ? 0 : 1)\n"
    with tempfile.TemporaryDirectory(prefix="bcd-gog-installer-") as tmp:
        f = Path(tmp) / "main.swift"
        f.write_text("import Foundation\n" + prog)
        b = subprocess.run([swiftc, "-o", str(Path(tmp) / "t"), str(f)], capture_output=True, text=True)
        check("the function compiles on the host", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-2000:])
        else:
            r = subprocess.run([str(Path(tmp) / "t")], capture_output=True, text=True)
            print(r.stdout, end="")
            check("every name gets the expected answer", r.returncode == 0)
elif not swiftc:
    print("skip the compiled cases: no swiftc")
print("PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
