#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The drive speed test and gentle writes for a slow drive (app/Madeira/BCDDriveSpeed.swift; bcd.cfg drive-gentle-writes). No iPhone, no
drive: the measurement runs in a temporary folder of this Mac.

1. The measurement and the decision (tests/host/bcd_drive_speed_checks.swift): the quick and the full test give speeds and leave
   nothing behind; a download is written with four chunks at a time only with the key, only on another volume than the device's own,
   and only under 40 MB/s.
2. Off path, on the source: the Steam downloader asks only with the key (its first line), and without an answer both chunk loops use
   the number they always used.
3. Settings' action, the catalog, the project, the credit, the workflow.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)
    return cond


plain = (APP / "BCDDriveSpeed.swift").read_text()
depot = (APP / "SwiftSteam/Content/DepotDownloader.swift").read_text()

# ---- 1. the measurement and the decision
if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift part", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        shutil.copy(R / "tests/host/bcd_drive_speed_checks.swift", t / "main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "run"), str(t / "main.swift"), str(APP / "BCDDriveSpeed.swift")],
                           capture_output=True, text=True)
        if check("BCDDriveSpeed.swift and its harness compile on the host", r.returncode == 0):
            r = subprocess.run([str(t / "run")], capture_output=True, text=True, timeout=300)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print(line)
            check(f"the Swift harness: {sum(1 for l in r.stdout.splitlines() if l.startswith('PASS'))} checks, none failing",
                  r.returncode == 0 and "ALL PASS" in r.stdout)
        else:
            print((r.stdout + r.stderr)[-2500:])

# ---- 2. off path
app = (APP / "MadeiraApp.swift").read_text()
fn = app[app.index("        DepotDownloader.gentleChunks = { folder in"):]
fn = fn[:fn.index("\n        }\n") + 11]
check("the app's rule answers nil at its first line without drive-gentle-writes = 1, before anything is measured; the test runs off the main thread",
      fn.splitlines()[1].strip() == 'guard MadeiraConfig.get(BCDDriveGentle.key) == "1" else { return nil }'
      and fn.index("return nil }") < fn.index("BCDDriveGentle.decision(") and "Task.detached(priority: .utility)" in fn and app.count("DepotDownloader.gentleChunks = ") == 1)
check("the downloader knows only a rule it may be given (nil until then), and without an answer both chunk loops use the number they always used (8, or 16 from a PC on the network)",
      "static var gentleChunks: (@Sendable (URL) async -> Int?)?" in depot and "BCDDriveGentle" not in depot.replace("BCDDriveGentle's answer", "") and "MadeiraConfig.get(BCDDrive" not in depot
      and "let maximum = peer?.serves(plan.depotID) == true ? 16 : maxConcurrentChunks, attempts = attemptsPerChunk" in depot
      and "for _ in 0..<min(gentleChunks ?? maximum, work.count) { enqueue() }" in depot
      and "let maximum = await Self.gentleChunks?(folder) ?? maxConcurrentChunks" in depot
      and "private let maxConcurrentChunks = 8" in depot)
check("the plain file knows nothing of the app's drives, settings or log",
      all(x not in plain for x in ("MadeiraConfig", "ExternalDrives", "LogStore", "SteamLog", "import UIKit", "import SwiftUI")))
check("the key is off unless it is 1, and four chunks is the gentle number", 'static let key = "drive-gentle-writes"' in plain and "static let chunks = 4" in plain
      and "static let gentleBelowMBps = 40.0" in plain)

# ---- 3. the app around it
sections = (APP / "BCDSettingsSections.swift").read_text()
live = (APP / "BCDSettingsLive.swift").read_text()
check("Settings > Storage > External drives: a connected drive's choices have Test speed, which runs off the main thread while no game runs",
      'choices.append(.init(title: "Test speed") { backend.testSpeed(ofDrive: drive.id) })' in sections
      and sections.index('.init(title: "Find games")') < sections.index('.init(title: "Test speed")') < sections.index('.init(title: "Reconnect")')
      and "func testSpeed(ofDrive id: String)" in sections
      and re.search(r"func testSpeed\(ofDrive id: String\) \{\n\s+guard let folder = folder\(id\), let root = ExternalDrives\.shared\.mounted\[folder\.id\], beginExternalWork\(\) else \{ return \}", live) is not None
      and "Task.detached(priority: .utility) { Result { try BCDDriveSpeedTest.run(in: root) } }" in live)
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
check("the catalog has the key, off by default", re.search(r'key: "drive-gentle-writes", title: "[^"]*", kind: \.bool, defaultValue: "0"', catalog) is not None)
check("the file is in the project", (R / "app/Madeira.xcodeproj/project.pbxproj").read_text().count("BCDDriveSpeed.swift in Sources") >= 1)
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
check("the measurement is credited: Porthole's file in the header, and its row in the notices",
      "Copyright (C) 2026 The Porthole authors." in plain.split("import Foundation")[0] and "app/Sources/Core/DriveSpeedTest.swift" in plain.split("import Foundation")[0]
      and "BCD Material" not in plain and "| `app/Madeira/BCDDriveSpeed.swift` |" in notices)
check("the workflow runs this check", "run: python3 tests/host/check-drive-speed.py" in (R / ".github/workflows/build-ipa.yml").read_text())

print("FAILED: drive speed" if failures else "PASS: drive speed")
sys.exit(1 if failures else 0)
