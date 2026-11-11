#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""GOG's redistributables in BCD (build 632; app/Madeira/BCDGOG/GOGSetup.swift, BCDGOGContent.swift; docs/GOG.md "Redistributables").

A GOG build names redistributables ("openAL", "MSVC2010_x86"); GOG's dependency repository says where each one's files are and which
installer to run. BCD downloads their files into the game's folder with the game (one plan, one journal; the open link serves the
chunks, no sign-in) and runs each installer once per prefix at the game's first start, from the services batch that starts the game.
Only a game BCD installs or repairs from build 632 on has installers to run (its record lists them): a game installed before starts
exactly as it always did. Evidence: The Witcher 2 (GOG) stopped in its Launcher.exe on mfc100u.dll, which the Visual C++ 2010
redistributable the build names would have provided (log HSGX5P, build 622).

Synthetic data only; GOG is never contacted.

Source checks (every host):
  - GOGSetup.swift is upstream Madeira's file with BCD's changes marked in its header; both it and its Playport credit stay;
  - the services batch: LibraryEntry.servicesScript adds the installers between the services.exe line and the cd line, and only when
    BCDGOG.prepareStart set some; with none the batch is the one it was; BCDGameStartLaunch follows the batch;
  - prepareStart sets the installers for the game's own entry only, resets them at every start, and honours env.BCD_GOG_SETUP in the
    game's lines; a folder name outside ASCII runs none;
  - the plan asks for redistributables only when BCDGOG says so, and the chunk source uses GOG's open link for the "redist" group.
Behaviour (swiftc; --require-swift makes its absence a failure): tests/host/gog-setup/bcd_main.swift.

  check-gog-setup.py [--require-swift]
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


UPSTREAM_HEADER = ("// SPDX-License-Identifier: GPL-3.0-or-later\n"
                   "// Copyright 2026 Madeira contributors\n"
                   "// Madeira Converter Exception: see LICENSE-EXCEPTION.md\n"
                   "// The dependency repository's format and which redistributables are skipped follow\n"
                   "// Playport's GOGDependencies (Playport 412de5f, as carried in willfaust/Madeira #479;\n"
                   "// portions copyright the Playport authors, GPL-3.0-or-later).\n"
                   "// Modified by BCD (build 632):")

# ---- source checks ------------------------------------------------------------------------
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
setup = (GOG / "GOGSetup.swift").read_text()
check(setup.startswith(UPSTREAM_HEADER), "GOGSetup.swift: upstream Madeira's licence header and the Playport credit, then BCD's change note")
ok = ("A1000C22 /* BCDGOG/GOGSetup.swift in Sources */ = {isa = PBXBuildFile; fileRef = A2000C22 /* GOGSetup.swift */; };" in pbx
      and 'A2000C22 /* GOGSetup.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "BCDGOG/GOGSetup.swift"; sourceTree = "<group>"; };' in pbx
      and pbx.count("A2000C22 /* GOGSetup.swift */,") == 1 and pbx.count("A1000C22 /* BCDGOG/GOGSetup.swift in Sources */,") == 1)
check(ok, "GOGSetup.swift: built by the Xcode project (content ID range, group and Sources phase)")
body = "\n".join(l for l in setup.splitlines() if not l.strip().startswith("//") and not l.strip().startswith("///"))
check(not re.search(r"\b(GOGAPI|GOGCodec|LibraryEntry)\b", body), "GOGSetup.swift's code names none of upstream's app types (BCD's own helpers stand in)")
check('static let resultFolder = "C:\\\\bcd-games\\\\gog-setup"' in setup and "madeira-games" not in body, "the installers' results go in C:\\bcd-games\\gog-setup")

library = (APP / "Library.swift").read_text()
script = library[library.index("    var servicesScript: String? {"):]
script = script[:script.index("\n    }\n")]
check("guard usesLaunchOptions, wantsServicesBatch else { return nil }" in script
      and script.index('lines.append("start \\"\\" \\"C:\\\\windows\\\\system32\\\\services.exe\\"")') < script.index("if !setup.isEmpty { lines.append(setup) }") < script.index('lines.append(unicode ? "cd /d'),
      "the services batch: the installers go between the services.exe line and the cd line")
check("private var wantsServicesBatch: Bool {\n        startServices == true || wantsRockstarServices || !(Self.gogSetup(for: id) ?? \"\").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty\n    }" in library
      and library.count("wantsServicesBatch") == 3, "a batch is wanted by startServices as before, by a keyed local Rockstar start (R1 S5), or by installers to run; nothing else changes")
check("private static var gogSetup: [UUID: String] = [:]" in library and "never part of the saved entry" in library,
      "the installers are kept in memory (a static table), never in the saved entry")
launch = (APP / "BCDGameSetup/BCDGameStartLaunch.swift").read_text()
check("let main: BCDStartScript.Main = entry.servicesScript != nil" in launch and "entry.startServices == true\n            ? .servicesBatch" not in launch,
      "a start plan follows the services batch whenever there is one")

goggle = (GOG / "BCDGOG.swift").read_text()
prep = goggle[goggle.index("    func prepareStart(_ entry: LibraryEntry) {"):]
prep = prep[:prep.index("\n    /// The library entry Play starts")]
check("if record.entryID == entry.id { prepareSetup(entry, record) }" in prep, "installers are set for the game's own entry only (not for a program added from its folder)")
setup_fn = prep[prep.index("    private func prepareSetup("):]
check("LibraryEntry.setGOGSetup(lines.isEmpty ? nil : lines.joined(separator: \"\\r\\n\"), for: entry.id)" in setup_fn
      and "Self.gameSwitch(\"BCD_GOG_SETUP\", for: entry) ?? true" in setup_fn and "record.windowsFolder.allSatisfy(\\.isASCII)" in setup_fn
      and "GOGSetup.batchLines(programs, folder: record.windowsFolder, drive: LibraryEntry.hostDrive)" in setup_fn,
      "prepareSetup: set (or cleared) at every start, off with the game's env.BCD_GOG_SETUP = 0, none for a folder outside ASCII")
check('MadeiraConfig.flag("BCD_GOG_SETUP", fallback: true)' in goggle and "redistributables: Self.redistributables" in goggle
      and "hasMsiexec: DockInstallers.bundleHasMsiexec" in goggle, "the plan asks for redistributables when env.BCD_GOG_SETUP is not 0 in bcd.cfg")
check("product == BCDGOGContent.redistGroup" in goggle and "BCDGOGContent.openLinkURL, signedIn: false" in goggle, "the redistributables' chunks come from GOG's open link, no sign-in")
gcontent = (GOG / "BCDGOGContent.swift").read_text()
check("redistributables: Bool = false, hasMsiexec: Bool = false" in gcontent and "if redistributables, !manifest.dependencies.isEmpty" in gcontent,
      "a plan made without the flag is what it was (no repository call)")
ci = (R / ".github/workflows/build-ipa.yml").read_text()
check("python3 tests/host/check-gog-setup.py --require-swift" in ci, "build-ipa.yml runs this check")

# ---- the Swift program ----------------------------------------------------------------------
swiftc = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not swiftc:
    print("check-gog-setup: swiftc unavailable; compiled part skipped")
    if args.require_swift:
        failures += 1
else:
    work = Path(tempfile.mkdtemp(prefix="bcd-gog-setup-"))
    try:
        harness = work / "harness.swift"
        shutil.copy(R / "tests/host/gog-setup/bcd_main.swift", harness)
        engine = [str(CONTENT / f) for f in ["BCDContentPlan.swift", "BCDContentCodec.swift", "BCDContentJournal.swift", "BCDContentFetch.swift",
                                              "BCDContentEngine.swift", "BCDContentQueue.swift", "BCDContentSecrets.swift"]]
        files = [str(GOG / f) for f in ["BCDGOGAuth.swift", "BCDGOGCatalog.swift", "BCDGOGContent.swift", "BCDGOGInstall.swift",
                                         "GOGInstallScript.swift", "GOGSetup.swift", "BCDGOGUpdate.swift"]]
        exe = work / "setup"
        build = subprocess.run([swiftc, "-parse-as-library", "-swift-version", "5", "-Onone", "-o", str(exe), str(harness)] + engine + files,
                               capture_output=True, text=True)
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300, env=dict(os.environ, SWIFT_BACKTRACE="enable=no")) if build.returncode == 0 else None
        if run is not None:
            sys.stdout.write("".join("  " + l + "\n" for l in run.stdout.splitlines() if l.startswith(("PASS", "FAIL"))))
        check(run is not None and run.returncode == 0 and "ALL OK" in run.stdout, "BCD's redistributables checks pass",
              (build.stderr[-3000:] if run is None else run.stdout[-1500:] + run.stderr[-800:]))
    finally:
        shutil.rmtree(work, ignore_errors=True)

print(f"check-gog-setup: {failures} FAILED" if failures else "check-gog-setup: all passed")
sys.exit(1 if failures else 0)
