#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""GOG's owned DLC, updates and repair in BCD (build 632; app/Madeira/BCDGOG/BCDGOGUpdate.swift, BCDGOGContent.swift, BCDGOG.swift).

DLC the account owns installs with the game (its depots, in the game's language, its chunks served by its own product). An installed game's
page offers Update when GOG has a newer build or owned DLC the game lacks (found when the page opens), and Repair. An update plans only the
files that changed (the installed build's file lists against the new build's: the disk is not read), a repair reads every installed file
and compares it chunk by chunk with the build; the files download into the usual staging folder and go into the game's folder one rename
each, after the files the new build no longer has are removed. Nothing starts by itself: Update and Repair are buttons.

Synthetic data only; GOG is never contacted.

Source checks (every host): BCDGOGUpdate.swift has BCD's header and is in the Xcode project; the live store routes Update, Repair and
Resume; the page no longer hides Update and Repair for GOG and still offers them only when the backend says so; the update check only
records what it found (it enqueues nothing); an update or a repair stops without GOG's owned list; the job's kind survives the app (its
pending file); cancel leaves the installed game; Play waits for the short step that puts an update's files in place.
Behaviour (swiftc; --require-swift makes its absence a failure): tests/host/gog-update/bcd_main.swift.

  check-gog-update.py [--require-swift]
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


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
          "// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")


def section(text, start, end="\n    }\n"):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
update = (GOG / "BCDGOGUpdate.swift").read_text()
check(update.startswith(HEADER), "BCDGOGUpdate.swift: BCD's licence header, exactly")
ok = ("A1000C23 /* BCDGOG/BCDGOGUpdate.swift in Sources */ = {isa = PBXBuildFile; fileRef = A2000C23 /* BCDGOGUpdate.swift */; };" in pbx
      and 'A2000C23 /* BCDGOGUpdate.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "BCDGOG/BCDGOGUpdate.swift"; sourceTree = "<group>"; };' in pbx
      and pbx.count("A2000C23 /* BCDGOGUpdate.swift */,") == 1 and pbx.count("A1000C23 /* BCDGOG/BCDGOGUpdate.swift in Sources */,") == 1)
check(ok, "BCDGOGUpdate.swift: built by the Xcode project (content ID range, group and Sources phase)")
check("import Foundation" in update and "UIKit" not in update and "UserDefaults" not in update, "BCDGOGUpdate.swift is Foundation only")
check("removeItem" in update and update.count("removeItem") == 1 and "for path in remove" in update, "the only file BCDGOGUpdate deletes are the ones the caller lists as removed")

live = (GOG / "BCDLiveStoreGOG.swift").read_text()
check("case .resume:\n            gog.resume(product)" in live and "case .update:\n            gog.update(product)" in live
      and "case .verify:\n            gog.repair(product)" in live and "case .install:\n            gog.install(product)" in live
      and "not in this version" not in live, "the live store routes Install, Resume, Update and Repair (Verify is the page's Repair)")
check(".updateAvailable(bytes: 0)" in live and "queue.job(BCDGOG.shared.key(id)) == nil" in live
      and live.index("installed.play == nil") < live.index(".updateAvailable(bytes: 0)"), "Update is offered for a game that is not broken and has no job running")
page = (APP / "BCDGamePage.swift").read_text()
check("game.store != .gog" not in section(page, "        case .updateAvailable(let bytes):", "        case .broken"), "the page offers Update for a GOG game too")
check("func offersRepair(_ game: BCDGame?) -> Bool { canRepair }" in page, "Options offer Check and repair files whenever the backend says so")
pagelive = (APP / "BCDGamePageLive.swift").read_text()
check("BCDLiveStore.shared.gogOpened(game.id)" in pagelive and "d.canRepair = BCDGOG.enabled && BCDGOG.shared.signedIn && !d.updating" in pagelive
      and "d.updating = BCDLiveStore.shared.gogUpdating(game.id)" in pagelive, "the page looks for an update when it opens, offers Repair signed in and not during a job")

g = (GOG / "BCDGOG.swift").read_text()
check_fn = section(g, "    func checkForUpdate(_ id: String, force: Bool = false) {", "\n    }\n\n    // MARK: Starting")
check("enqueue" not in check_fn and "queue.enqueue" not in check_fn and "update(" not in check_fn.replace("checkForUpdate", "").replace("available(", "")
      and "6 * 3600" in check_fn and "queue.job(key(id)) == nil" in check_fn and "signedIn" in check_fn,
      "the update check only records what it found: nothing is enqueued, once in six hours, not while a job runs, signed in only")
plan = section(g, "    func plan(for key: BCDContentKey, stored: BCDContentPlan?) async throws -> BCDContentPlan {", "\n    }\n\n    func source")
check("try await BCDGOGContent.owned(fetch: fetch, required: kind != .install)" in plan and "Self.dlc ? owned : owned.intersection(record?.dlc ?? [])" in plan
      and "(info.kind ?? .install) == kind" in plan, "an update or a repair needs GOG's owned list; DLC off keeps the DLC the game has; a stored plan is reused only for the same kind")
blocker = section(g, "    func blocker(_ key: BCDContentKey) -> String? {")
check("requested[key.id] == nil, (pending(key.id)?.kind ?? .install) == .install" in blocker, "a game that is installed refuses to install again, but takes its update or repair (also after a restart)")
finish = section(g, "    func finish(_ plan: BCDContentPlan, stage: URL) async throws {", "\n    }\n\n    func cancelled")
check("BCDGOGInstall.finishUpdate(plan, info: info, stage: stage, record: old)" in finish and "requested[id] = nil" in finish and "GOGSetup.forget(" in finish, "finish: an update or a repair merges and records; the job is forgotten")
check("requested[key.id] = nil" in section(g, "    func cancelled(_ key: BCDContentKey) {"), "cancel forgets the job's kind and leaves the installed game as it is")
check("job.progress.phase == .finishing" in g and "BCD is finishing this game's update" in g, "Play waits for the short step that puts an update's files in place")
check("kind != .install, let record, let folder = record.hostFolder" in plan and "queue.planning(key, done: done, of: total)" in plan, "a repair reports how far it has read")
q = (CONTENT / "BCDContentQueue.swift").read_text()
check("func planning(_ key: BCDContentKey, done: Int64, of total: Int64) {\n        update(key, BCDContentProgress(phase: .verifying" in q, "the queue shows a driver's planning progress, nothing else changed")
gen = (R / ".github/workflows/build-ipa.yml").read_text()
check("python3 tests/host/check-gog-update.py --require-swift" in gen, "build-ipa.yml runs this check")
cfg = (R / "build/tools/gen-config-catalog.py").read_text()
check('MadeiraConfig.flag("BCD_GOG_DLC", fallback: true)' in g, "env.BCD_GOG_DLC is read where the catalog scan finds it, on by default")

swiftc = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not swiftc:
    print("check-gog-update: swiftc unavailable; compiled part skipped")
    if args.require_swift:
        failures += 1
else:
    work = Path(tempfile.mkdtemp(prefix="bcd-gog-update-"))
    try:
        main = work / "main.swift"
        shutil.copy(R / "tests/host/gog-update/bcd_main.swift", main)
        engine = [str(CONTENT / f) for f in ["BCDContentPlan.swift", "BCDContentCodec.swift", "BCDContentJournal.swift", "BCDContentFetch.swift",
                                              "BCDContentEngine.swift", "BCDContentQueue.swift", "BCDContentSecrets.swift"]]
        files = [str(GOG / f) for f in ["BCDGOGAuth.swift", "BCDGOGCatalog.swift", "BCDGOGContent.swift", "BCDGOGInstall.swift",
                                         "GOGInstallScript.swift", "GOGSetup.swift", "BCDGOGUpdate.swift"]]
        exe = work / "update"
        build = subprocess.run([swiftc, "-swift-version", "5", "-Onone", "-o", str(exe), str(main)] + engine + files, capture_output=True, text=True)
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300, env=dict(os.environ, SWIFT_BACKTRACE="enable=no")) if build.returncode == 0 else None
        if run is not None:
            sys.stdout.write("".join("  " + l + "\n" for l in run.stdout.splitlines() if l.startswith(("PASS", "FAIL"))))
        check(run is not None and run.returncode == 0 and "ALL OK" in run.stdout, "BCD's DLC, update and repair checks pass",
              (build.stderr[-3000:] if run is None else run.stdout[-1500:] + run.stderr[-800:]))
    finally:
        shutil.rmtree(work, ignore_errors=True)

print(f"check-gog-update: {failures} FAILED" if failures else "check-gog-update: all passed")
sys.exit(1 if failures else 0)
