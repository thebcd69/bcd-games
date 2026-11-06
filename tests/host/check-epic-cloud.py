#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Epic's cloud saves in BCD (build 632; app/Madeira/BCDEpic/EpicFormats.swift, EpicCloudSaves.swift, BCDEpicCloudCore.swift, BCDEpicCloud.swift).

A cloud save is a snapshot (a manifest and the 1 MiB chunks it names) in the account's savesync folder for the game; an upload is a new snapshot and
Epic's older ones stay. The formats and the sync decision are upstream Madeira's two files (Legendary-derived); the transport, the record store, the
page's state and the sync's steps are BCD's. It is OFF until the player turns it on for a game (it writes to the player's Epic account): nothing is read
from or sent to Epic's save service before that.

Synthetic data only; Epic is never contacted.

Source checks (every host):
  - headers: EpicFormats.swift as upstream's file came, EpicCloudSaves.swift with BCD's change marked (no EpicCloudHTTP, no date formatter), BCDEpicCloud.swift
    (adapted from upstream's EpicCloud.swift: upstream's header and the adaptation noted), BCDEpicCloudCore.swift BCD's; all four built by the Xcode project;
  - no file of the four builds an ICU date formatter or uses URLSession.shared; the cloud files never name a token (BCDEpicAPI.send adds it);
  - off by default: the switch is read from the game's own lines, then bcd.cfg, and is false unless one says 1; check() does nothing before the switch (no state,
    no network); startup, the page's opening and the page's rows all go through it;
  - the page: Epic's cloud row is Off until turned on, turning on asks first and writes the game's own line; Play waits for a sync; uninstall and sign-out forget;
  - the catalog overlay lists both keys; build-ipa.yml runs this check.
Behaviour (swiftc; --require-swift makes its absence a failure): upstream's own driver (tests/host/epic-cloud/main.swift: the decision table, filters, save
folder variables, chunk basics, a pack and a fetch through a directory standing in for the service) and BCD's (tests/host/epic-cloud/bcd_main.swift: the
transport over BCD's session, the save rule, the record store, the page's differences, the build version's date from numbers, a whole sync in memory).
With LEGENDARY_SRC set, tests/host/check-epic-cloud-legendary.py (upstream's) also reads and writes snapshots with Legendary itself.

  check-epic-cloud.py [--require-swift]
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
EPIC = APP / "BCDEpic"
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


def section(text, start, end="\n    }\n"):
    i = text.index(start)
    return text[i:text.index(end, i)]


BCD_HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
              "// Copyright 2026 thebcd69 (BCD)\n"
              "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
# BCDEpicCloudCore.swift holds Madeira code too (#443), so it is not "BCD Material" (LICENSE-BCD.md section 1): BCD's copyright
# line, no section 7 line, and the "Portions copyright" credit (owner 2026-11-06).
MIXED_HEADER = "// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n// Portions copyright"
FILES = {"EpicFormats.swift": "F5A", "EpicCloudSaves.swift": "F5B", "BCDEpicCloudCore.swift": "F5C", "BCDEpicCloud.swift": "F5D", "BCDGamePageLiveCloud.swift": "F5E"}
text = {name: (EPIC / name).read_text() for name in FILES}
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()

check(text["EpicFormats.swift"].startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright 2026 Akshay CM\n// Madeira Converter Exception: see LICENSE-EXCEPTION.md\n"),
      "EpicFormats.swift: upstream Madeira's file as it came (its licence header and its author's line)")
check("// Modified by BCD (thebcd69), 2026-11-05: the one message a person reads says BCD." in text["EpicFormats.swift"]
      and "a format BCD cannot read yet" in text["EpicFormats.swift"] and "Madeira cannot" not in text["EpicFormats.swift"],
      "EpicFormats.swift: BCD's one change (the message a person reads) is marked in its header")
saves = text["EpicCloudSaves.swift"]
check(saves.startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n// Madeira Converter Exception: see LICENSE-EXCEPTION.md\n// Modified by BCD (build 632):")
      and "Ported from Legendary (GPL-3.0-or-later, https://github.com/derrod/legendary):" in saves and "struct EpicCloudHTTP" not in saves,
      "EpicCloudSaves.swift: upstream's header and its Legendary credit, BCD's change marked, the URLSession transport not carried")
check(text["BCDEpicCloud.swift"].startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n// Madeira Converter Exception: see LICENSE-EXCEPTION.md\n"
                                            "// Adapted by BCD (build 632) from upstream Madeira's EpicCloud.swift")
      and "// Copyright 2026 thebcd69 (BCD) for the adaptation.\n" in text["BCDEpicCloud.swift"][:700], "BCDEpicCloud.swift: upstream's header, the adaptation and BCD's copyright for it")
check(text["BCDEpicCloudCore.swift"].startswith(MIXED_HEADER) and "Portions copyright 2026 Tabîrcă Nicolae-Eduard" in text["BCDEpicCloudCore.swift"][:600] and text["BCDGamePageLiveCloud.swift"].startswith(BCD_HEADER), "BCDEpicCloudCore.swift and BCDGamePageLiveCloud.swift: BCD's licence header, exactly")
for name, id_ in FILES.items():
    ok = (f"A1000{id_} /* BCDEpic/{name} in Sources */ = {{isa = PBXBuildFile; fileRef = A2000{id_} /* {name} */; }};" in pbx
          and f'A2000{id_} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "BCDEpic/{name}"; sourceTree = "<group>"; }};' in pbx
          and pbx.count(f"A2000{id_} /* {name} */,") == 1 and pbx.count(f"A1000{id_} /* BCDEpic/{name} in Sources */,") == 1)
    check(ok, f"{name}: built by the Xcode project (Epic's F5x range, group and Sources phase)")

ICU = re.compile(r"DateFormatter\(|\.formatted\(|ISO8601DateFormatter|\.dateFormat\b|\.dateStyle\b|NSDateFormatter|DateComponentsFormatter|Calendar\(|DateComponents\(")
for name, body in text.items():
    c = code(body)
    check(not ICU.search(c), f"{name}: no ICU date formatter, no calendar object (a device log shows one killing BCD)")
    check("URLSession.shared" not in c and "UserDefaults" not in c and "NSLog" not in c and "print(" not in c, f"{name}: no shared URLSession, no UserDefaults, no console")
    if name not in ("EpicFormats.swift", "EpicCloudSaves.swift"):
        check(not re.search(r"\b(accessToken|refreshToken)\b", c), f"{name}: names no token (BCDEpicAPI.send adds it)")
session = (EPIC / "BCDEpicSession.swift").read_text()
send = section(session, "    func send(_ request: URLRequest, limit: Int = 16 << 20) async throws -> (status: Int, data: Data) {")
check("Self.trusted(url)" in send and 'request.setValue("bearer " + (try await session.accessToken())' in send and "session.refresh()" in send,
      "BCDEpicAPI.send: the token goes only to Epic's hosts, refreshed once when refused")
check('if request.value(forHTTPHeaderField: "User-Agent") == nil { request.setValue(userAgent' in session, "the transport keeps a user agent the request names (the save service's long one)")
core = text["BCDEpicCloudCore.swift"]
check("api.transport.send(" in core and core.count("Authorization") == 0 and 'url.scheme == "https", url.user == nil' in core,
      "the pre-signed links go through the transport without a token, https only")

cloud = text["BCDEpicCloud.swift"]
check('BCDEpic.flag("BCD_EPIC_CLOUD", entry: entry, fallback: false)' in cloud and 'BCDEpic.flag("BCD_EPIC_CLOUD_AUTO_UPLOAD", entry: entry, fallback: false)' in cloud,
      "both switches are off unless the game's own lines (then bcd.cfg's) say 1")
check_fn = section(cloud, "    func check(_ app: String, automatic: Bool = true) async {", "\n    }\n\n    /// The Sync now")
check(check_fn.index("Self.isOn(entry)") < check_fn.index("update(app)") and check_fn.index("Self.isOn(entry)") < check_fn.index("look(app)"),
      "check(): nothing before the switch: no state, no network for a game whose cloud saves are off")
check("guard let record = epic.installs[app], let entry = entry(of: record), Self.isOn(entry) else { return nil }" in cloud, "the context (every sync's start) is nil for a game whose switch is off")
check("guard let record = epic.installs[app], let entry = entry(of: record), Self.isOn(entry) else { continue }" in cloud, "the startup check skips every game whose switch is off")
check(cloud.count("transport.writeTargets") == 0 and cloud.count("EpicCloudSaves.upload(") == 1, "the only write to Epic is one upload call")
upload_users = [m.start() for m in re.finditer(r"await upload\(", cloud)]
check(len(upload_users) == 3 and "case .upload where Self.autoUpload(looked.context.entry): await upload(app, looked)" in cloud
      and "case .upload: await upload(app, looked)" in cloud and "await upload(app, looked)\n            }" in cloud,
      "an upload happens only on the page's Sync now, on a conflict answered 'keep this device's', or with the game's auto-upload line")
check("sessionRunning" in cloud and "context.generation == generation" in cloud and "func accountChanged()" in cloud and "func forget(_ app: String)" in cloud,
      "a sync never runs with a game session, and stops before it writes when the account changed")
driver = (EPIC / "BCDEpicDriver.swift").read_text()
check("BCDEpicCloud.shared.isSyncing(app)" in section(driver, "    func playEntry(_ app: String) -> Result<LibraryEntry, BCDEpicPlayRefusal> {", "\n    }\n")
      and "BCDEpicCloud.shared.forget(app)" in section(driver, "    func uninstall(_ app: String) {"), "Play waits for a sync; uninstall forgets the remembered rule")
facade = (EPIC / "BCDEpic.swift").read_text()
check(facade.count("BCDEpicCloud.shared.accountChanged()") == 2 and facade.count("BCDEpicCloud.shared.startupCheck()") == 2, "a sign-out and a sign-in drop what a sync knew; BCD's start and a sign-in run the startup check")
live = (EPIC / "BCDLiveStoreEpic.swift").read_text()
check("Task { await BCDEpicCloud.shared.check(app) }" in section(live, "    func epicOpened(_ id: String) {"), "the game's page opening checks (a no-op for a game whose cloud saves are off)")

page = (APP / "BCDGamePage.swift").read_text()
pagelive = (APP / "BCDGamePageLive.swift").read_text()
hooks = text["BCDGamePageLiveCloud.swift"]
epic_fn = section(hooks, "    func storeCloudDetails(_ game: BCDGame, _ entry: LibraryEntry, into d: inout BCDGameDetails) {")
check("guard BCDEpicCloud.isOn(entry) else { d.cloud = .off; return }" in epic_fn and epic_fn.index(".off") < epic_fn.index("BCDEpicCloud.shared.state(app)"),
      "the page: Epic's cloud row says Off, and reads no state, until the game's switch is on")
check('guard setting == BCDCloudSetting.enabled, let app = storeCloudApp(game) else { return false }' in hooks and 'setLine(game, "env.BCD_EPIC_CLOUD", value == "1" ? "1" : "")' in hooks,
      "turning on or off writes the game's own env.BCD_EPIC_CLOUD line")
check("game.store == .epic ? BCDEpicRules.claimedApp(game.id, enabled: BCDEpic.enabled) : nil" in hooks, "every hook does nothing for a game that is not an Epic game BCD installed")
for hook in ("if storeCloudSync(game) { return }", "if storeCloudKeep(game, fromCloud: fromCloud) { return }", "if storeCloudSet(setting, to: value, for: game) { return }",
             "storeCloudDetails(game, entry, into: &d)", "storeCloudChanges,"):
    check(pagelive.count(hook) == 1 and not re.search(r"(?<![A-Za-z])[eE]pic|BCDEpic", [l for l in pagelive.splitlines() if hook in l][0]), f"the live page backend reaches Epic's cloud saves through one store-neutral line: {hook}")
check("case .turnOnCloud:\n            let names = details.cloudNames" in page and "This writes to your \\(names.store) account." in page
      and "case .turnOnCloud: backend?.set(BCDCloudSetting.enabled, to: \"1\", for: game)" in page, "turning on asks first and says it writes to the player's account")
check(not re.search(r"(?i)epic", re.sub(r"(?m)^\s*//.*$", "", page).replace("details.installBlocker", "")), "the page names no store: Epic's words come from the live backend (BCDCloudNames.epic)")
check('static let epic = BCDCloudNames(store: "Epic Games", cloud: "Epic Cloud", backups: "Epic Cloud Backups", front: .epic, optIn: true, keepsOlder: true)' in hooks
      and 'var summary: String { summary(.init()) }' in page and "var optIn = false" in page and "var keepsOlder = false" in page, "Steam's wording is the default of the generalised page")

gen = (R / "build/tools/gen-config-catalog.py").read_text()
check(re.search(r'"env\.BCD_EPIC_CLOUD": \{[^}]*"kind": "choice"', gen, re.S) is not None and re.search(r'"env\.BCD_EPIC_CLOUD_AUTO_UPLOAD": \{[^}]*"kind": "choice"', gen, re.S) is not None,
      "the catalog overlay lists both keys")
ci = (R / ".github/workflows/build-ipa.yml").read_text()
check("python3 tests/host/check-epic-cloud.py --require-swift" in ci, "build-ipa.yml runs this check")

# ---- the Swift programs ---------------------------------------------------------------------
swiftc = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not swiftc:
    print("check-epic-cloud: swiftc unavailable; compiled part skipped")
    if args.require_swift:
        failures += 1
else:
    work = Path(tempfile.mkdtemp(prefix="bcd-epic-cloud-"))
    try:
        # upstream's driver: its self-test, then a Swift pack and a Swift fetch through a directory standing in for the service
        driver_exe = work / "driver"
        build = subprocess.run([swiftc, "-O", "-swift-version", "5", "-o", str(driver_exe), str(R / "tests/host/epic-cloud/main.swift"),
                                str(EPIC / "EpicFormats.swift"), str(EPIC / "EpicCloudSaves.swift")], capture_output=True, text=True)
        selftest = subprocess.run([str(driver_exe), "selftest"], capture_output=True, text=True) if build.returncode == 0 else None
        check(selftest is not None and selftest.returncode == 0 and "ALL OK" in selftest.stdout, "upstream's self-test passes on the two files (decision table, filters, save folders, chunks)",
              build.stderr[-2000:] if selftest is None else selftest.stdout[-1000:])
        if selftest is not None:
            src, cloud, dest, backup = work / "device", work / "cloud", work / "restored", work / "backup"
            (src / "Profiles").mkdir(parents=True)
            (src / "slot1.sav").write_bytes(os.urandom(5000))
            (src / "Profiles/big.dat").write_bytes(os.urandom(2_300_000))
            (src / "empty.bin").write_bytes(b"")
            pack = subprocess.run([str(driver_exe), "pack", str(src), str(cloud), "TestApp", "0123456789abcdef0123456789abcdef", "{AppData}/Test/Saved", "2026.10.15-12.30.45"],
                                  capture_output=True, text=True)
            fetch = subprocess.run([str(driver_exe), "fetch", str(cloud), str(dest), str(backup), "TestApp", "0123456789abcdef0123456789abcdef"], capture_output=True, text=True)
            same = all((dest / rel).read_bytes() == (src / rel).read_bytes() for rel in ("slot1.sav", "Profiles/big.dat", "empty.bin")) if fetch.returncode == 0 else False
            check(pack.returncode == 0 and fetch.returncode == 0 and same, "a snapshot packed by the Swift writer is restored by the Swift reader, byte for byte", pack.stderr[-500:] + fetch.stderr[-500:])
        # BCD's
        harness = work / "harness.swift"
        shutil.copy(R / "tests/host/epic-cloud/bcd_main.swift", harness)
        engine = [str(CONTENT / f) for f in ["BCDContentPlan.swift", "BCDContentCodec.swift", "BCDContentJournal.swift", "BCDContentFetch.swift",
                                              "BCDContentEngine.swift", "BCDContentQueue.swift", "BCDContentSecrets.swift"]]
        epic = [str(EPIC / f) for f in ["BCDEpicLog.swift", "BCDEpicAuth.swift", "BCDEpicSession.swift", "BCDEpicCatalog.swift", "EpicFormats.swift",
                                         "EpicCloudSaves.swift", "BCDEpicCloudCore.swift"]]
        exe = work / "bcd-cloud-test"
        build = subprocess.run([swiftc, "-parse-as-library", "-swift-version", "5", "-Onone", "-o", str(exe), str(harness)] + engine + epic, capture_output=True, text=True)
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300, env=dict(os.environ, SWIFT_BACKTRACE="enable=no")) if build.returncode == 0 else None
        if run is not None:
            sys.stdout.write("".join("  " + l + "\n" for l in run.stdout.splitlines() if l.startswith(("PASS", "FAIL"))))
        check(run is not None and run.returncode == 0 and "ALL OK" in run.stdout, "BCD's Epic cloud checks pass",
              (build.stderr[-3000:] if run is None else run.stdout[-1500:] + run.stderr[-800:]))
    finally:
        shutil.rmtree(work, ignore_errors=True)

print(f"check-epic-cloud: {failures} FAILED" if failures else "check-epic-cloud: all passed")
sys.exit(1 if failures else 0)
