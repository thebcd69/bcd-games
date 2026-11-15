#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Settings › Storage › Find games: a crash handler or reporter never becomes a game's card (round 660, M011).

Timberborn from an external drive (5BM9HV, build 653) got UnityCrashHandler64.exe as its program: ExternalGameScan's prefix test let a
name that starts with "unity" through. The check compiles ExternalGameScan.walk's name test on the host and runs it on the names of
the round and on game programs, which must stay. (A program whose name starts with "crash" was skipped before by the prefix list;
that is unchanged.)
"""
import re, shutil, subprocess, sys, tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
src = (R / "app/Madeira/ExternalDrives.swift").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:600]) if detail and not cond else ""))
    ok &= bool(cond)


skip = re.search(r'private static let skipNames = (\[[^\]]*\])', src, re.S)
anywhere = re.search(r'static let skipAnywhere = (\[[^\]]*\])', src)
cond = re.search(r'!skipNames\.contains\(where: \{ name\.hasPrefix\(\$0\) \|\| name\.contains\("_" \+ \$0\) \|\| name\.contains\("-" \+ \$0\) \}\),\s*'
                 r'!skipAnywhere\.contains\(where: \{ name\.contains\(\$0\) \}\)', src)
check("the scan tests both lists for every program", skip and anywhere and cond)
swiftc = shutil.which("swiftc")
if not swiftc or not (skip and anywhere):
    print("skip: no swiftc for the compiled part")
else:
    prog = f'''import Foundation
let skipNames = {skip.group(1)}
let skipAnywhere = {anywhere.group(1)}
func taken(_ name: String) -> Bool {{
    !skipNames.contains(where: {{ name.hasPrefix($0) || name.contains("_" + $0) || name.contains("-" + $0) }}) && !skipAnywhere.contains(where: {{ name.contains($0) }})
}}
var bad = [String]()
for n in ["unitycrashhandler64.exe", "unitycrashhandler32.exe", "crashpad_handler.exe", "ue4-crashreportclient.exe", "gamecrashreporter.exe",
          "crashreportclient.exe"] where taken(n) {{ bad.append("taken: " + n) }}
for n in ["timberborn.exe", "fm.exe", "hollow_knight.exe", "witcher3.exe"] where !taken(n) {{ bad.append("skipped: " + n) }}
print(bad.isEmpty ? "ok: crash handlers and reporters skipped, the games kept" : "FAIL " + bad.joined(separator: ", "))
exit(bad.isEmpty ? 0 : 1)
'''
    with tempfile.TemporaryDirectory(prefix="bcd-extscan-") as tmp:
        f = Path(tmp) / "main.swift"
        f.write_text(prog)
        b = subprocess.run([swiftc, "-o", str(Path(tmp) / "t"), str(f)], capture_output=True, text=True)
        check("the name test compiles on the host", b.returncode == 0, b.stderr[-600:])
        if b.returncode == 0:
            r = subprocess.run([str(Path(tmp) / "t")], capture_output=True, text=True)
            print(r.stdout, end="")
            check("the names of round 660 skipped, game programs kept", r.returncode == 0, r.stdout)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-external-game-scan.py" in wf)
print("PASS: external game scan" if ok else "FAILED: external game scan")
sys.exit(0 if ok else 1)
