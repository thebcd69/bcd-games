#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""GOG's install script in BCD (build 632; app/Madeira/BCDGOG/GOGInstallScript.swift, BCDGOGRegistry.swift; docs/GOG.md "Install script").

GOG Galaxy runs a build's goggame-<id>.script after an install; its setRegistry values are what a game or its launcher reads to
find itself (The Witcher: Enhanced Edition's launcher quits after half a second without InstallFolder). BCD keeps the script
with the install and writes the values into the prefix before each start of a game it installed or repaired with the script in
mind. A game installed before starts exactly as it did (the record has no mark), unless env.BCD_GOG_SCRIPT = 1 asks.

Synthetic data only; GOG is never contacted.

Source checks (every host):
  - GOGInstallScript.swift is upstream Madeira's file as it came: its licence header and the Playport credit line stay; BCDGOGRegistry.swift
    has BCD's header; both are in the Xcode project (content ID range);
  - a start: ContentView.startLibraryEntry calls BCDGOG.prepareStart after Epic's pre-launch look and before the launch is configured; the
    method does nothing unless GOG is on, the game has a record, the record or the switch says so, and no Wine session runs; it makes no
    reg.exe call and keeps no UserDefaults;
  - the switch env.BCD_GOG_SCRIPT is read from the game's own lines, then bcd.cfg, and is in the settings catalog's overlay;
  - the install keeps the script and the support folder's app/ files (BCDGOGContent) and marks the record (BCDGOGInstall.finish).
Behaviour (swiftc; --require-swift makes its absence a failure): upstream's own script test (tests/host/gog-script/main.swift) and BCD's
(tests/host/gog-script/bcd_main.swift): the registry text (both views, a value that is there stays, a folder-following string is rewritten,
the default value, a sub-key is not the key), the registry files (backup once, atomic, not Wine's refused), the scripts in a folder, the log
line holding no key or path, when a start writes, the support files in a plan (the script at the root, app/ files never over the game's own),
the install record's mark.

  check-gog-script.py [--require-swift]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
GOG = APP / "BCDGOG"
CONTENT = APP / "BCDContent"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


BCD_HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
              "// Copyright 2026 thebcd69 (BCD)\n"
              "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
UPSTREAM_HEADER = ("// SPDX-License-Identifier: GPL-3.0-or-later\n"
                   "// Copyright 2026 Madeira contributors\n"
                   "// Madeira Converter Exception: see LICENSE-EXCEPTION.md\n"
                   "// Portions follow Playport's GOGInstallScript (github.com/playportdev/playport,\n"
                   "// app/PlayportKit/Sources/PlayportKit/GOGInstallScript.swift, GPL-3.0-or-later):\n"
                   "// the script's shape and which actions matter.\n")

# ---- source checks ------------------------------------------------------------------------
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
script = (GOG / "GOGInstallScript.swift").read_text()
registry = (GOG / "BCDGOGRegistry.swift").read_text()
check(script.startswith(UPSTREAM_HEADER), "GOGInstallScript.swift: upstream Madeira's licence header and the Playport credit, as they came")
check(registry.startswith(BCD_HEADER), "BCDGOGRegistry.swift: BCD's licence header, exactly")
for name, id_ in (("GOGInstallScript.swift", "C20"), ("BCDGOGRegistry.swift", "C21")):
    ok = (f"A1000{id_} /* BCDGOG/{name} in Sources */ = {{isa = PBXBuildFile; fileRef = A2000{id_} /* {name} */; }};" in pbx
          and f'A2000{id_} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "BCDGOG/{name}"; sourceTree = "<group>"; }};' in pbx
          and pbx.count(f"A2000{id_} /* {name} */,") == 1 and pbx.count(f"A1000{id_} /* BCDGOG/{name} in Sources */,") == 1)
    check(ok, f"{name}: built by the Xcode project (content ID range, group and Sources phase)")
check("UserDefaults" not in code(registry) and "reg.exe" not in registry and "wine_session" not in code(registry),
      "the registry writer keeps nothing in UserDefaults, runs no reg.exe and touches no session: it rewrites the prefix's text files")
check("import Foundation" in registry and "import UIKit" not in registry and "import SwiftUI" not in registry, "the registry writer is Foundation only (the host check compiles it)")

content = (APP / "ContentView.swift").read_text()
start = content[content.index("    private func startLibraryEntry(_ entry: LibraryEntry) {"):]
start = start[:start.index("        library.begin(entry")]
at = start.index("if BCDGOG.enabled { BCDGOG.shared.prepareStart(entry) }")
check(start.index("BCDEpic.shared.preLaunch(entry") < at < start.index("entry.configureLaunch()")
      and start.count("BCDGOG.shared.prepareStart(entry)") == 1,
      "ContentView.startLibraryEntry calls prepareStart once, after Epic's pre-launch look and before the launch is configured")
goggle = (GOG / "BCDGOG.swift").read_text()
prep = goggle[goggle.index("    func prepareStart(_ entry: LibraryEntry) {"):]
prep = prep[:prep.index("\n    }\n")]
check(all(t in prep for t in ["Self.enabled", "entry.desktop != true", "record(forEntry: entry)", "BCDGOGRegistry.applies(scripts: record.scripts, switch: Self.scriptSwitch(for: entry))",
                              "wineserver_is_running() == 0", "wine_process_is_running() == 0"]),
      "prepareStart acts only with GOG on, a GOG record, the record's mark or the switch, and no Wine session")
check("start()" not in prep and "refreshLibrary" not in prep, "prepareStart starts no store work: no network, no queue")
switch = goggle[goggle.index("    static func gameSwitch(_ name: String, for entry: LibraryEntry) -> Bool? {"):]
switch = switch[:switch.index("\n    }\n")]
check(switch.index("GameProfile(windowsPath: entry.windowsPath).get(key)") < switch.index("MadeiraConfig.parse(entry.gameConfigText") < switch.index("MadeiraConfig.get(key)")
      and 'let key = "env." + name' in switch and "return text != \"0\"" in switch
      and 'static func scriptSwitch(for entry: LibraryEntry) -> Bool? { gameSwitch("BCD_GOG_SCRIPT", for: entry) }' in goggle,
      "env.BCD_GOG_SCRIPT: the game's own line, then the entry's lines, then bcd.cfg; 0 is off, any other text on, unset follows the install")
gen = (R / "build/tools/gen-config-catalog.py").read_text()
check(re.search(r'"env\.BCD_GOG_SCRIPT": \{[^}]*"kind": "choice"', gen, re.S) is not None, "the catalog overlay lists env.BCD_GOG_SCRIPT")
install = (GOG / "BCDGOGInstall.swift").read_text()
finish = install[install.index("    static func finish("):]
finish = finish[:finish.index("\n    }\n")]
check(finish.count("scripts: true") == 1 and "var scripts: Bool?" in install, "the install marks the record (scripts: true); a record from before has none")
gcontent = (GOG / "BCDGOGContent.swift").read_text()
check("supportKept(listed)" in gcontent and "addSupportFiles(total.kept, to: &builder" in gcontent
      and gcontent.index("addSupportFiles(total.kept, to: &builder") > gcontent.index("for depot in chosen"),
      "the plan keeps the install script and the support folder's app/ files, added after every depot's own files")
ci = (R / ".github/workflows/build-ipa.yml").read_text()
check("python3 tests/host/check-gog-script.py --require-swift" in ci, "build-ipa.yml runs this check")

# ---- the Swift programs ---------------------------------------------------------------------
swiftc = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not swiftc:
    print("check-gog-script: swiftc unavailable; compiled part skipped")
    if args.require_swift:
        failures += 1
else:
    work = Path(tempfile.mkdtemp(prefix="bcd-gog-script-"))
    try:
        theirs = work / "theirs"
        build = subprocess.run([swiftc, str(GOG / "GOGInstallScript.swift"), str(R / "tests/host/gog-script/main.swift"), "-o", str(theirs)],
                               capture_output=True, text=True)
        run = subprocess.run([str(theirs)], capture_output=True, text=True) if build.returncode == 0 else None
        if run is not None:
            sys.stdout.write("".join("  " + l + "\n" for l in run.stdout.splitlines() if l.startswith(("PASS", "FAIL"))))
        check(run is not None and "ALL OK" in run.stdout, "upstream's GOGInstallScript test passes on the file as it came",
              (build.stderr[-3000:] if run is None else run.stdout[-1500:]))
        main = work / "main.swift"
        shutil.copy(R / "tests/host/gog-script/bcd_main.swift", main)
        engine = [str(CONTENT / f) for f in ["BCDContentPlan.swift", "BCDContentCodec.swift", "BCDContentJournal.swift", "BCDContentFetch.swift",
                                              "BCDContentEngine.swift", "BCDContentQueue.swift", "BCDContentSecrets.swift"]]
        files = [str(GOG / f) for f in ["BCDGOGAuth.swift", "BCDGOGCatalog.swift", "BCDGOGContent.swift", "BCDGOGInstall.swift",
                                         "GOGInstallScript.swift", "GOGSetup.swift", "BCDGOGUpdate.swift", "BCDGOGRegistry.swift"]]
        ours = work / "ours"
        build = subprocess.run([swiftc, "-swift-version", "5", "-Onone", "-o", str(ours), str(main)] + engine + files, capture_output=True, text=True)
        run = subprocess.run([str(ours)], capture_output=True, text=True, timeout=300, env=dict(os.environ, SWIFT_BACKTRACE="enable=no")) if build.returncode == 0 else None
        if run is not None:
            sys.stdout.write("".join("  " + l + "\n" for l in run.stdout.splitlines() if l.startswith(("PASS", "FAIL"))))
        check(run is not None and run.returncode == 0 and "ALL OK" in run.stdout, "BCD's registry writer and support-file checks pass",
              (build.stderr[-3000:] if run is None else run.stdout[-1500:] + run.stderr[-800:]))
    finally:
        shutil.rmtree(work, ignore_errors=True)

print(f"check-gog-script: {failures} FAILED" if failures else "check-gog-script: all passed")
sys.exit(1 if failures else 0)
