#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""GOG Galaxy features in BCD (build 660; app/Madeira/BCDGOG/Galaxy/; docs/GOG.md "Galaxy").

A GOG game's Galaxy SDK asks GOG's own client, on 127.0.0.1:9977, for the game's sign-in, achievements, statistics and leaderboards.
With the game's switch on (env.BCD_GOG_GALAXY = 1; off by default) BCD answers there while the game runs. The service is the Playport
authors' (GalaxyWire, GalaxyService, GalaxyListener), as upstream Madeira's branch playport/gog carries it; the token source, the game's
identity and the listener's life are BCD's, on BCD's own GOG sign-in.

Synthetic data only; GOG is never contacted.

Source checks (every host):
  - the three Playport files keep their licence and credit; GalaxyWire.swift's body is as it came (its SHA-256); the other two say what
    BCD changed; none of them names ContentKit's types any more; all are in the Xcode project;
  - off by default: the switch is read from the game's own lines, then bcd.cfg, and is false when none says; a start calls
    BCDGOGGalaxy.preLaunch once, after the entry is validated and before the launch is configured; with the switch off, or for a game
    that is not a GOG game BCD installed, preLaunch opens nothing and asks GOG nothing;
  - the install records are not changed (no Galaxy field in BCDGOGInstallRecord): the game's Galaxy client is kept in a file of its own;
  - nothing logs a token, the client secret or a user ID; the game page has the switch only for a GOG game; the settings catalog has it.
Behaviour (swiftc; --require-swift makes its absence a failure): Playport's own Galaxy tests (tests/host/gog-galaxy/GalaxyTests.swift: the
wire, the service against fakes, the socket on a free local port) run against BCD's copy, and BCD's parts (tests/host/gog-galaxy/bcd_main.swift:
the game's identity, the kept file, the builds list's link, the token grant against a canned GOG, the log).

  check-gog-galaxy.py [--require-swift]
"""
import argparse
import hashlib
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
GALAXY = GOG / "Galaxy"   # the six files of this feature, in a folder of their own (check-gog.py holds BCDGOG's own list)
CONTENT = APP / "BCDContent"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[:1500]) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


PLAYPORT = "https://github.com/playportdev/playport/tree/412de5f8c0bdd26d84e0127eb5776ba992e3b152/app"
wire = (GALAXY / "GalaxyWire.swift").read_text()
service = (GALAXY / "GalaxyService.swift").read_text()
listener = (GALAXY / "GalaxyListener.swift").read_text()
core = (GALAXY / "BCDGOGGalaxyCore.swift").read_text()
live = (GALAXY / "BCDGOGGalaxy.swift").read_text()
pagex = (GALAXY / "BCDGamePageLiveGalaxy.swift").read_text()
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()

# ---------------------------------------------------------------- the Playport files
for name, text in (("GalaxyWire.swift", wire), ("GalaxyService.swift", service), ("GalaxyListener.swift", listener)):
    head = text.split("\nimport Foundation\n")[0]
    check(text.startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright 2026 the Playport authors\n")
          and PLAYPORT in head and "https://github.com/willfaust/Madeira branch playport/gog, commit 9d6dad8 by intraducine" in head
          and "Madeira Converter Exception" in head and "app/Madeira/legal/PLAYPORT.txt" in head and "BCD Material" not in text,
          f"{name} keeps the Playport authors' licence and says where it came from")
    check(f'/* {name} */ = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "BCDGOG/Galaxy/{name}"; sourceTree = "<group>"; }};' in pbx
          and f"/* BCDGOG/Galaxy/{name} in Sources */," in pbx, f"{name} is in the Xcode project")
body = wire[wire.index("// The Galaxy SDK's local protocol (decision 0063)"):]
check("// Not modified by BCD.\n" in wire and hashlib.sha256(body.encode()).hexdigest() == "afa0b921ee5692c19bb354b986f8362a25c6f5785f6fd81eed74068a7e0a1515",
      "GalaxyWire.swift's body is Playport's as it came")
check("// Modified by BCD (thebcd69), 2026-11-14:" in service and "// Modified by BCD (thebcd69), 2026-11-14:" in listener,
      "the service and the listener say what BCD changed")
check(re.search(r"DateFormatter\(|ISO8601DateFormatter|\.dateFormat\b|\.formatted\(", code(service) + code(listener) + code(wire) + code(core) + code(live)) is None
      and "static func daysFromCivil(" in service, "GOG's dates are read and written from numbers: no ICU date formatter (BCD's rule for every file of the app)")
for name, text in (("GalaxyService.swift", service), ("GalaxyListener.swift", listener), ("GalaxyWire.swift", wire)):
    c = code(text)
    check(re.search(r"\b(import ContentKit|ClientError|Logger|GOGAPI|GOGSession)\b", c) is None and re.search(r"\bSecret[<(]", c) is None,
          f"{name} names none of Playport's package types BCD does not carry")
check("extension GOGSession" not in service and "struct BCDGOGGalaxyTokens: GalaxyTokenSource {" in core,
      "the token source is BCD's own (BCD's GOG session), not Playport's GOGSession")
for name in ("BCDGOGGalaxyCore.swift", "BCDGOGGalaxy.swift", "BCDGamePageLiveGalaxy.swift"):
    check(f'path = "BCDGOG/Galaxy/{name}"; sourceTree = "<group>"; }};' in pbx and f"/* BCDGOG/Galaxy/{name} in Sources */," in pbx, f"{name} is in the Xcode project")
check(core.startswith("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n// Portions copyright 2026 the Playport authors:")
      and "BCD Material" not in core, "BCDGOGGalaxyCore.swift: BCD's header with the Playport credit (it holds the client type and the grant)")

# ---------------------------------------------------------------- off by default
check("nonisolated static let switchName = \"BCD_GOG_GALAXY\"" in live
      and "static func isOn(_ entry: LibraryEntry) -> Bool { BCDGOG.gameSwitch(switchName, for: entry) ?? false }" in live,
      "the switch is env.BCD_GOG_GALAXY, read from the game's own lines then bcd.cfg, and off when none says")
pre = live[live.index("    func preLaunch(_ entry: LibraryEntry, retry: @escaping () -> Void) -> Bool {"):]
pre = pre[:pre.index("\n    }\n")]
guard = "guard BCDGOG.enabled, entry.desktop != true, Self.isOn(entry), let record = BCDGOG.shared.record(forEntry: entry), idle else { return true }"
check(pre.index("        let idle = wineserver_is_running() == 0 && wine_process_is_running() == 0\n        if idle { stop() }\n") < pre.index(guard) < pre.index("BCDGOGGalaxyClients.known(")
      and pre.count("return true") >= 4 and pre.count("return false") == 2,
      "preLaunch: a leftover listener is closed when no session runs (not a running game's), then nothing happens unless GOG is on, the game's switch is on (read first), it has a record (the look prepareStart already makes) and no session runs", pre)
before = pre[:pre.index(guard)]
check("GalaxyListener(" not in before and "galaxyAPI" not in before and "BCDContentLog" not in before,
      "...before that guard nothing is opened, asked or logged")
cv = (APP / "ContentView.swift").read_text()
start = cv[cv.index("    private func startLibraryEntry(_ entry: LibraryEntry) {"):]
start = start[:start.index("\n    }\n")]
hook = "guard BCDGOGGalaxy.shared.preLaunch(galaxyEntry, retry: { startLibraryEntry(galaxyEntry) }) else { return }"
check(cv.count("BCDGOGGalaxy.shared.preLaunch(") == 1 and start.index("try entry.validate()") < start.index(hook) < start.index("        entry.configureLaunch()\n")
      and start.index("BCDGOG.shared.prepareStart(entry)") < start.index(hook),
      "a start calls it once: after GOG's prepareStart and the entry's validation, before the launch is configured")
check("LibraryModel.shared.$current.dropFirst().receive(on: DispatchQueue.main).sink" in live and "guard let self, self.playing == id, current != id else { return }" in live
      and "try? await Task.sleep(for: .seconds(60))" in live and "listener?.stop()" in live,
      "the listener goes when the game's session ends, and a start that never became a session leaves nothing listening")
check("// The port is taken, or iOS refused the socket: the game starts as it does without the switch." in live
      and "library.error" not in live and "LibraryModel.shared.launchFailed" not in live,
      "a service that cannot start never stops the game's start")

# ---------------------------------------------------------------- the records stay as they are
install = (GOG / "BCDGOGInstall.swift").read_text()
record = install[install.index("struct BCDGOGInstallRecord: Codable, Equatable {"):]
record = record[:record.index("\n}\n")]
check("galaxy" not in record.lower() and "Galaxy" not in code((GOG / "BCDGOGUpdate.swift").read_text()),
      "BCDGOGInstallRecord has no Galaxy field: an installed game's record is not rewritten")
g = (GOG / "BCDGOG.swift").read_text()
check("    var galaxySession: BCDGOGSession { session }\n    var galaxyAPI: BCDGOGAPI { api }\n" in g and g.count("galaxy") == 2,
      "BCDGOG.swift only hands out its session and API (two read-only properties)")
check('base.appendingPathComponent("galaxy", isDirectory: true).appendingPathComponent(productID + ".json")' in core
      and "kept.buildID == buildID" in core and "BCDGOGCatalog.validID(productID)" in core,
      "the game's Galaxy client is kept in its own file, for the installed build only")

# ---------------------------------------------------------------- nothing private in the log
for name, text in (("BCDGOGGalaxyCore.swift", core), ("BCDGOGGalaxy.swift", live)):
    for line in text.splitlines():
        if "BCDContentLog.write(" in line or "log.info(" in line or "log.warn(" in line:
            check(re.search(r"\\\((?:[^)]*\.)?(clientSecret|clientID|refreshToken|accessToken|token|userID|value|link|url)\b", line) is None,
                  f'{name}: no token, secret, client or address logged in "{line.strip()[:70]}"')
check("LogStore.shared.log" not in code(core) and code(live).count("LogStore.shared.log") == 1,
      "the lines go through BCDContentLog (its redaction pass), like the rest of BCD's GOG code")
check('nonisolated static let lookupLimit: TimeInterval = 12' in live and "Task.sleep(for: .seconds(Self.lookupLimit)); look.cancel()" in live,
      "reading the game's client from GOG is bounded (12 s), then the game starts without the service")

# ---------------------------------------------------------------- the game page and the catalog
page = (APP / "BCDGamePageLive.swift").read_text()
check(page.count("galaxyGroup(game, entry)") == 1 and page.count("if galaxySet(setting, to: value, for: game) { return }") == 1
      and "guard game.store == .gog, BCDGOG.enabled, BCDGOG.shared.record(forEntry: entry) != nil else { return nil }" in pagex
      and 'value: on ? "1" : "0", defaultValue: "0"' in pagex,
      "the game page has the GOG Galaxy switch only for a GOG game BCD installed, off by default")
check('setLine(game, "env." + BCDGOGGalaxy.switchName, value == "1" ? "1" : (everywhere ? "0" : ""))' in pagex,
      "...it writes the game's own line and takes it out again when off")
gen = (R / "build/tools/gen-config-catalog.py").read_text()
check('"env.BCD_GOG_GALAXY": {"category": "App & front end"' in gen and '"choices": [("", "Default (off)"), ("1", "On"), ("0", "Off")]' in gen,
      "the settings catalog has the switch, default off")
policy = (APP / "GameRecommendationsList.swift").read_text()
check("BCD_GOG_GALAXY" not in policy, "a downloaded list cannot turn it on (the switch is the player's)")
legal = (APP / "legal/PLAYPORT.txt").read_text()
check("GalaxyWire.swift" in legal and "GalaxyService.swift" in legal and "GalaxyListener.swift" in legal, "the bundled Playport notice names the three files")

# ---------------------------------------------------------------- behaviour
swiftc = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not swiftc:
    print("check-gog-galaxy: swiftc unavailable; compiled part skipped")
    if args.require_swift:
        failures += 1
else:
    tests = (R / "tests/host/gog-galaxy/GalaxyTests.swift").read_text()
    methods = re.findall(r"^    func (test\w+)\(\) (async )?(throws )?\{", tests, re.M)
    check(len(methods) == 15 and len(methods) == len(re.findall(r"^    func test", tests, re.M)), "Playport's 15 Galaxy tests are all found", methods)
    run = ["let playport = GalaxyTests()"]
    for name, is_async, throws in methods:
        call = ("try " if throws else "") + ("await " if is_async else "") + f"playport.{name}()"
        run.append(f'do {{ {call}; ran += 1 }} catch {{ fail("{name} threw \\(error)") }}' if throws else f"{call}; ran += 1")
    run.append(f'expect(ran == {len(methods)}, "every Playport test ran")')
    run.append('if failures == 0 { print("PASS: Playport\'s Galaxy tests (wire, service, socket) on BCD\'s copy; BCD\'s identity, kept file, manifest link, token grant and log") }')
    run.append("exit(failures == 0 ? 0 : 1)")
    with tempfile.TemporaryDirectory(prefix="bcd-gog-galaxy-") as tmp:
        work = Path(tmp)
        main = work / "main.swift"
        main.write_text((R / "tests/host/gog-galaxy/bcd_main.swift").read_text() + "var ran = 0\n" + "\n".join(run) + "\n")
        engine = [str(CONTENT / f) for f in ["BCDContentPlan.swift", "BCDContentCodec.swift", "BCDContentJournal.swift", "BCDContentFetch.swift",
                                              "BCDContentEngine.swift", "BCDContentQueue.swift", "BCDContentSecrets.swift"]]
        files = [str(GOG / f) for f in ["BCDGOGAuth.swift", "BCDGOGCatalog.swift", "BCDGOGContent.swift", "BCDGOGContentV1.swift", "BCDGOGInstall.swift",
                                         "GOGInstallScript.swift", "GOGSetup.swift", "BCDGOGUpdate.swift"]]
        files += [str(GALAXY / f) for f in ["GalaxyWire.swift", "GalaxyService.swift", "GalaxyListener.swift", "BCDGOGGalaxyCore.swift"]]
        extra = [str(R / "tests/host/gog-galaxy/xct.swift"), str(R / "tests/host/gog-galaxy/GalaxyTests.swift")]
        exe = work / "check"
        build = subprocess.run([swiftc, "-swift-version", "5", "-Onone", "-o", str(exe), str(main)] + engine + files + extra, capture_output=True, text=True)
        check(build.returncode == 0, "BCD's copy of the Galaxy files compiles on the host with BCD's GOG code and Playport's tests", build.stderr[-3000:])
        if build.returncode == 0:
            ran = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
            print(ran.stdout.rstrip())
            check(ran.returncode == 0, "the Galaxy checks pass", (ran.stdout + ran.stderr)[-3000:])

print("FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)
