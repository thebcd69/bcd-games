#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_DOCK_INSTALL_SKIP (per game, round 626, agent A1 S7): one-time installs a game's list marks as provided.

Six Steam games run DirectX's runtime setup (DXSETUP.exe) as a one-time install, and it fails at the first start (Dark Souls: Prepare
to Die Edition DMWDNW, Batman: Arkham City GOTY V31CH8, Saints Row: The Third T3P8HK, Sleeping Dogs: Definitive Edition SXH6T6,
Call of Duty: Black Ops QJYC1M, Fallout: New Vegas CY5HAV): the session ran only the installs and the game started after a restart.
BCD ships what the setup installs, so the game's list names the install-script entry and BCD records it as done (as 579's
MADEIRA_DOCK_DIRECTX_PROVIDED does after one failure). Batman's "amd" (the AMD Dual-Core Optimizer) never closes.

Checks: the parser (DockInstallScripts.installSkipNames, cut from app/Madeira/DockInstallers.swift and run on the host): the game's
last env.MADEIRA_DOCK_INSTALL_SKIP line wins, names split on commas, semicolons and spaces, lower-cased, comments and other keys
ignored, an empty or missing line is the empty set; the plan's `provided` closure takes the listed entries by their logged name and
the log line says why; the list policy (names only); the catalog; exactly the six rows carry the line from build 626; CI.
No auto-tune rule: a session that runs only the one-time installs is never judged.
"""
from pathlib import Path
import json, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
SRC = (root / "app/Madeira/DockInstallers.swift").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


i = SRC.index("nonisolated static func installSkipNames(text: String) -> Set<String> {")
j = SRC.index("\n    }\n", i) + 6
parser = SRC[i:j]
check("the plan's provided closure takes the listed entries by their logged name",
      "|| skip.contains(DockInstallScripts.label($0.run).lowercased())" in SRC
      and "let skip = DockInstallScripts.installSkipNames()" in SRC)
check("the log line for such an entry names the game's setting",
      "skipped: the game's settings list it in \" +\n" in SRC and '"env.MADEIRA_DOCK_INSTALL_SKIP (BCD provides what it installs)' in SRC)
HARNESS = "import Foundation\nenum DockInstallScripts {\n    " + parser.replace("nonisolated ", "") + "\n}\n" + r'''
var fails = 0
func T(_ c: Bool, _ what: String) { if !c { print("FAIL " + what); fails += 1 } }
T(DockInstallScripts.installSkipNames(text: "") == [], "empty")
T(DockInstallScripts.installSkipNames(text: "env.MADEIRA_EC_HOOK_TRACE = 0\n") == [], "no line")
T(DockInstallScripts.installSkipNames(text: "env.MADEIRA_DOCK_INSTALL_SKIP = directx\n") == ["directx"], "one name")
T(DockInstallScripts.installSkipNames(text: "env.MADEIRA_DOCK_INSTALL_SKIP = DirectX, AMD;vcredist\n") == ["directx", "amd", "vcredist"], "three names")
T(DockInstallScripts.installSkipNames(text: "env.MADEIRA_DOCK_INSTALL_SKIP = directx\nenv.MADEIRA_DOCK_INSTALL_SKIP = amd\n") == ["amd"], "the last line wins")
T(DockInstallScripts.installSkipNames(text: "# env.MADEIRA_DOCK_INSTALL_SKIP = directx\n") == [], "a comment")
T(DockInstallScripts.installSkipNames(text: "env.MADEIRA_DOCK_INSTALL_SKIP =\n") == [], "an empty value")
T(DockInstallScripts.installSkipNames(text: "  env.MADEIRA_DOCK_INSTALL_SKIP=directx  \n") == ["directx"], "spaces around")
print(fails == 0 ? "ok: last line wins, names split and lower-cased, comments and empty values ignored" : "FAILED")
exit(fails == 0 ? 0 : 1)
'''
swiftc = shutil.which("swiftc")
cmd = [swiftc] if swiftc else (["xcrun", "swiftc"] if shutil.which("xcrun") else None)
if cmd:
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "main.swift"
        f.write_text(HARNESS)
        r = subprocess.run(cmd + ["-O", str(f), "-o", str(Path(tmp) / "t")], capture_output=True, text=True)
        check("the parser compiles on the host", r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            run = subprocess.run([str(Path(tmp) / "t")], capture_output=True, text=True)
            print("     " + run.stdout.strip().replace("\n", "\n     "))
            check("the parser behaves as documented", run.returncode == 0, run.stdout)
else:
    print("note: no swiftc; the parser was not run")

pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a list may set the key, names only", '"MADEIRA_DOCK_INSTALL_SKIP",' in pol and 'case "MADEIRA_DOCK_INSTALL_SKIP":' in pol)
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents it (empty by default)", re.search(r'key: "env\.MADEIRA_DOCK_INSTALL_SKIP"[^\n]*defaultValue: ""', cat) is not None)
recs = json.loads((root / "recommended/recommendations.json").read_text())
carriers = {r["id"]: next((c for c in r["config"] if isinstance(c, dict) and "MADEIRA_DOCK_INSTALL_SKIP" in c["line"]), None)
            for r in recs["rules"] if "MADEIRA_DOCK_INSTALL_SKIP" in json.dumps(r["config"])}
want = {"dark-souls-ptde": "directx", "saints-row-the-third": "directx", "sleeping-dogs-definitive-edition": "directx",
        "call-of-duty-black-ops": "directx", "fallout-new-vegas": "directx", "batman-arkham-city-goty": "directx,amd"}
check("exactly the six rows carry it, from build 626", set(carriers) == set(want) and all(
      carriers[k] == {"line": "env.MADEIRA_DOCK_INSTALL_SKIP = " + v, "minBuild": 626} for k, v in want.items()), carriers)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-dock-install-skip.py" in wf)
print("PASS: Dock install skip" if ok else "FAILED: Dock install skip")
sys.exit(0 if ok else 1)
