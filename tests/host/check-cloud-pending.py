#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The cloud page lists the saves a sync would move, before anything moves, and can check again without moving anything
(app/Madeira/BCDGamePage.swift, BCDGamePageLive.swift, SteamCloud.swift, SteamOwnedLibrary.swift).

1. The list's rows and words (tests/host/bcd_cloud_pending_checks.swift, with the two types cut out of BCDGamePage.swift).
2. The comparison records what its plan would move; a sync still works from its own fresh plan.
3. The page shows the two lists only when something waits, and Check again only where the backend says a check that moves nothing
   exists (Steam's cloud); the live side runs Steam's comparison for it and nothing else.
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


page = (APP / "BCDGamePage.swift").read_text()
live = (APP / "BCDGamePageLive.swift").read_text()
state = (APP / "SteamCloud.swift").read_text()
steam = (APP / "SteamOwnedLibrary.swift").read_text()

# ---- 1. the list
types = page[page.index("// cloud-pending-begin"):page.index("// cloud-pending-end")]
check("the two types need nothing of the page (they compile on their own)", "struct BCDCloudPending: Identifiable, Equatable {" in types and "enum BCDCloudPendingList {" in types)
if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift part", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "types.swift").write_text("import Foundation\n" + types)
        shutil.copy(R / "tests/host/bcd_cloud_pending_checks.swift", t / "main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "run"), str(t / "main.swift"), str(t / "types.swift")], capture_output=True, text=True)
        if check("the list's types and their harness compile on the host", r.returncode == 0):
            r = subprocess.run([str(t / "run")], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print(line)
            check(f"the Swift harness: {sum(1 for l in r.stdout.splitlines() if l.startswith('PASS'))} checks, none failing", r.returncode == 0 and "ALL PASS" in r.stdout)
        else:
            print((r.stdout + r.stderr)[-2500:])

# ---- 2. the comparison records its plan
check("the cloud state keeps what the last comparison's plan would move, both ways",
      "var pendingUp: [SteamCloudEntry] = []" in state and "var pendingDown: [SteamCloudEntry] = []" in state
      and "state.pendingUp = plan.upload; state.pendingDown = plan.download" in steam)
sync = steam[steam.index("    func syncCloud(_ appID: Int) async {"):]
sync = sync[:sync.index("\n    }\n")]
check("a sync works from its own fresh plan, not from the recorded lists", "pendingUp" not in sync and "pendingDown" not in sync
      and "guard let plan = await checkCloud(appID), Self.cloudAutomatic else { return }" in sync and steam.count("pendingUp") == 1)

# ---- 3. the page and the live side
cloud_page = page[page.index("    private func cloudPage(_ cloud: BCDCloudSaves) -> some View {"):page.index("    // MARK: Recommended settings")]
check("the page shows a direction's heading and rows only when something waits that way",
      "if !list.rows.isEmpty {" in cloud_page and "BCDEyebrow(text: BCDCloudPendingList.heading(up: up, cloud: names.cloud))" in cloud_page
      and "BCDCloudPendingList.rows(d.cloudPending, up: true, size: BCDFormat.bytes)" in cloud_page)
check("Check again is offered only where a check that moves nothing exists, and not while a check or a sync runs, signed out, off or unsupported",
      "if d.cloudCanCheck, !working, cloud != .signedOut, cloud != .off, cloud != .unsupported {" in cloud_page
      and "model.request(.set(BCDCloudPendingList.checkAction, \"run\"))" in cloud_page and "var cloudCanCheck = false" in page)
check("the rows that were on the page are as they were (Sync now, the two choices for saves that differ, the saves that differ)",
      'rows.append(BCDGameRow(id: "sync", title: title, symbol: "arrow.triangle.2.circlepath", enabled: !working,' in cloud_page
      and 'BCDEyebrow(text: "Saves that differ")' in cloud_page and 'id: "cloudwins"' in cloud_page and 'id: "devicewins"' in cloud_page)
fill = live[live.index("    private func cloud(_ appID: Int, into d: inout BCDGameDetails) {"):live.index("    // MARK: Auto-tune")]
check("the live side says Steam's cloud can be checked (after the signed-out test) and fills the list from the recorded plan",
      fill.index("guard steam.signedIn else { d.cloud = .signedOut; return }") < fill.index("d.cloudCanCheck = true")
      and "state.pendingUp.map { BCDCloudPending(name: $0.name, bytes: Int64(clamping: $0.localSize), up: true) }" in fill
      and "state.pendingDown.map { BCDCloudPending(name: $0.name, bytes: Int64(clamping: $0.cloudSize), up: false) }" in fill)
setter = live[live.index("    func set(_ setting: String, to value: String, for game: BCDGame) {"):]
setter = setter[:1200]
check("Check again runs Steam's comparison (which only reads) for a Steam game, and nothing for another store's game",
      "if setting == BCDCloudPendingList.checkAction {" in setter
      and "if !storeCloudOwns(game), let id = appID(game) { Task { await SteamOwnedLibrary.shared.checkCloud(id) } }" in setter
      and "func storeCloudOwns(_ game: BCDGame) -> Bool { storeCloudApp(game) != nil }" in (APP / "BCDEpic/BCDGamePageLiveCloud.swift").read_text()
      and "/// about the differences. Reads only (it records files found identical)." in steam)
check("the workflow runs this check", "run: python3 tests/host/check-cloud-pending.py" in (R / ".github/workflows/build-ipa.yml").read_text())

print("FAILED: cloud pending list" if failures else "PASS: cloud pending list")
sys.exit(1 if failures else 0)
