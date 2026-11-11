#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Steam DLC for the games BCD downloads (app/Madeira/BCDSteamDLC/, BCDGameDLC.swift; docs/STEAM_LIBRARY.md, "DLC").

Never contacts Steam. Part A reads the sources; Part B compiles the DLC rules with the production SteamAppInfo,
SteamKeyValues, SteamInstall and AppManifestWriter (and the VDF parser of SteamLibraryFetcher) on this Mac and runs
them on synthetic fixtures:

  - owned-DLC detection: a base game's product info (a DLC list, DLC depots, depots for other platforms, languages,
    low-violence copies, a depot another app owns, a DLC with no manifest) against license packages (binary VDF
    `appids` lists): owned with files, owned without files, not owned;
  - depot selection: with no DLC chosen the list is exactly today's (same depots, same order); with DLC the
    depots they add, nothing else; the 32-bit fallback is followed;
  - the job: a first install, an update, a DLC taken away, an installed DLC the account lost or the game dropped;
  - the install record: the DLC depots in InstalledDepots with manifest, size and `dlcappid`; with no DLC the
    record is today's byte for byte; the record read back; rewritten without a removed DLC;
  - taking a DLC's files away: only what no depot that stays holds, found without case, never through a link;
  - the player's choice file; the [steam-dlc] log lines (no account data).

Part A also pins that it is keyed (env.MADEIRA_STEAM_DLC = 0; the downloader's default request is none), that the
screens name no engine type and offer no purchase, that Valve's client plays no part, and that CI runs this check.
Run with python3; --require-swift fails when xcrun swiftc is missing (the macOS build runner).
"""
import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
DLC = APP / "BCDSteamDLC"
HEADER = ["// SPDX-License-Identifier: GPL-3.0-only",
          "// Copyright 2026 thebcd69 (BCD)",
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md."]
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)))
    ok &= bool(cond)


def read(path):
    return Path(path).read_text(encoding="utf-8")


def code(text):
    """The source without comments and string contents, for 'names no X' checks."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r'"(?:[^"\\]|\\.)*"', '""', line.split("//")[0]) for line in text.split("\n"))


# ------------------------------------------------------------------------------ Part A: the sources
new_files = [DLC / "BCDSteamDLC.swift", DLC / "BCDSteamDLCDownload.swift", DLC / "BCDSteamDLCService.swift",
             DLC / "BCDGamePageLiveDLC.swift", APP / "BCDGameDLC.swift", APP / "BCDGameDLCChooser.swift"]
project = read(R / "app/Madeira.xcodeproj/project.pbxproj")
for f in new_files:
    lines = read(f).split("\n")[:3]
    check(f"{f.name}: BCD's licence header, exactly", lines == HEADER)
    rel = f.relative_to(APP).as_posix()
    check(f"{f.name}: built by the Xcode project (build file, reference and group entry)",
          project.count(f"/* {rel} in Sources */") == 2 and project.count(f"/* {f.name} */") >= 3, rel)
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', read(f))
    check(f"{f.name}: no \"Madeira\" in a string it shows or logs (the env key MADEIRA_STEAM_DLC is the one name that stays)",
          not [x for x in literals if "madeira" in x.lower() and x != "MADEIRA_STEAM_DLC"])

# The project IDs: the range of this feature, unused by anything else.
ids = re.findall(r"\b(A[12]000DA[0-9A-F])\b", project)
check("project IDs A1000DA0-A5 / A2000DA0-A5 are this feature's, each used for exactly its file",
      sorted(set(ids)) == [f"A{n}000DA{i}" for n in (1, 2) for i in range(6)], sorted(set(ids)))

# Files of others' that BCD changed carry the dated notice.
for f in ["SwiftSteam/Library/SteamAppInfo.swift", "SwiftSteam/Library/SteamLibraryFetcher.swift",
          "SwiftSteam/Content/DepotDownloader.swift", "SteamOwnedLibrary.swift"]:
    head = read(APP / f).split("import ", 1)[0]
    check(f"{f}: marked as modified by BCD, with the date (GPL-3.0 section 5)", "Modified by BCD (thebcd69), 2026-10-22" in head)
    lines = head.split("\n")
    check(f"{f}: the original licence lines are untouched",
          lines[0] == "// SPDX-License-Identifier: GPL-3.0-or-later" and lines[1].startswith("// Copyright 2026 ")
          and lines[2] == "// Madeira Converter Exception: see LICENSE-EXCEPTION.md")

rules, download, service, live, page_rules = (read(DLC / "BCDSteamDLC.swift"), read(DLC / "BCDSteamDLCDownload.swift"),
                                              read(DLC / "BCDSteamDLCService.swift"), read(DLC / "BCDGamePageLiveDLC.swift"),
                                              read(APP / "BCDGameDLC.swift"))
dd = read(APP / "SwiftSteam/Content/DepotDownloader.swift")
fetcher = read(APP / "SwiftSteam/Library/SteamLibraryFetcher.swift")
owned = read(APP / "SteamOwnedLibrary.swift")
page = read(APP / "BCDGamePage.swift")
page_live = read(APP / "BCDGamePageLive.swift")

# Keyed: a game nothing was chosen for, with no DLC installed, downloads as before.
check("env.MADEIRA_STEAM_DLC = 0 turns it off (on by default)", 'SteamSignIn.flag("MADEIRA_STEAM_DLC", default: true)' in service)
check("the downloader's default request is none; install() is the same call without it",
      "dlc: SteamDLCRequest = .none," in dd and "app.installDepots(including: dlc.selected)" in dd)
check("no DLC is looked up for a game nothing was chosen for and no DLC is installed in",
      "if wanted == nil, installed.isEmpty { return .none }" in service and "if wanted == nil, installed.isEmpty { return .none }" in rules)
check("SteamOwnedLibrary asks the service for the request in run() and passes it to the downloader",
      "let dlcRequest = await dlc.request(for: info, steamApps: steamApps)" in owned and "dlc: dlcRequest)" in owned)
check("the installDepots() rules themselves are untouched (no DLC depot without a DLC choice)",
      "d.supports(os: os) && d.dlcAppID == nil && !d.isSharedInstall &&" in read(APP / "SwiftSteam/Library/SteamAppInfo.swift"))

# Steam decides ownership; a refused DLC depot never fails the game's install.
check("a DLC depot Steam gives no key for is left out, not an error",
      "dlcDepotKey(depotID: depot.depotID, appID: app.appID, dlcAppID: dlcID)" in dd and "dlcRefused.insert(dlcID)" in dd and "return nil" in download)
check("only a refusal is swallowed; a lost connection or timeout still throws (as for the game's own depots)",
      "catch SteamError.depotKeyNotFound" in download and "catch {" not in download.split("func dlcDepotKey")[1].split("private func recordedManifest")[0])
check("the install record names each DLC depot's dlcappid and keeps carried entries",
      "SteamDLCRecord.tag(" in dd and "SteamDLCRecord.installedDepots(" in dd and "carried: carried)" in dd)
check("a DLC taken away is removed after the depots that stay are fetched, and nothing else is deleted",
      "pruneDLC(app: app, installURL: installURL, request: dlc, kept: plans.map(\\.manifest)" in dd and "SteamDLCRemoval.exclusivePaths" in download)
check("a removal needs no download: the job is its own early return", "if dlc.removalOnly { return try await removeDLCOnly(" in dd)

# BCD alone: its own sign-in, license list and downloader; Valve's client plays no part.
for word in ["SteamTokenStore", "SecItem", "kSecClass", "UserDefaults", "refreshToken", "steam.exe", "steamwebhelper", "runWineFullSequence",
             "MADEIRA_EXE", "MadeiraDock", "wineserver", "WineProcessBridge", "Process(", "NSTask"]:
    for name, text in [("BCDSteamDLC.swift", rules), ("BCDSteamDLCDownload.swift", download), ("BCDSteamDLCService.swift", service)]:
        check(f"{name}: no {word} (BCD's own downloader does it; the client in Wine downloads nothing)", word not in code(text))
check("the service uses BCD's own license list and product-info lookups", "fetcher.dlcReport(" in service and "fetcher.ownedAppIDs()" in service)
check("no depot key, ticket or license is made up: keys come from Steam's reply only",
      "depotKeys[" not in rules + service + download and "eresult" not in rules + service)
check("no account data in a [steam-dlc] line (no sign-in name, e-mail, token, Steam ID)",
      not [l for l in (rules + service + download + live + dd + owned).split("\n") if "[steam-dlc]" in l
           and re.search(r"accountName|steamID|SteamID|email|token|password", l)])

# The page: owned DLC with a switch each; the others only as a number; no purchase.
check("the screens name no engine type (BCDGameDLC.swift, BCDGamePage.swift)",
      not re.findall(r"\b(SteamOwnedLibrary|SteamDLCService|SteamLibraryFetcher|DepotDownloader|SteamGamesModel|SteamSignIn|LibraryModel)\b", code(page_rules + page)))
check("BCDGameDLC.swift is a top-level BCD screen file (the preview build compiles it); the engine files are in BCDSteamDLC/",
      (APP / "BCDGameDLC.swift").is_file() and all((DLC / n).is_file() for n in ["BCDGamePageLiveDLC.swift", "BCDSteamDLCService.swift"]))
for bad in ["buy", "purchase", "Buy", "Purchase", "store.steampowered", "cart", "checkout", "price", "Price"]:
    check(f"BCDGameDLC.swift offers no purchase (no \"{bad}\")", bad not in "\n".join(re.findall(r'"((?:[^"\\]|\\.)*)"', page_rules)))
check("DLC the account does not own is a count only", '"dlc.notowned", title: "Other DLC", kind: .info' in page_rules)
check("only the owned DLC has a switch (rows are built from the owned list)", "state.rows.map" in page_rules and "report.owned.map" in live)
check("every Install of a game that is not installed asks (an earlier answer is not reused), and the answer made then is used",
      "service.choices.wanted(id) == nil" not in live and "guard SteamOwnedLibrary.shared.downloads[id] == nil else { return }" in live
      and "service.chooseForNewInstall(id, selected: chosen)" in live)
check("a cancelled first install forgets its DLC answer", "dlc.choices.forget(appID)" in read(APP / "SteamOwnedLibrary.swift"))
check("an uninstalled game forgets its DLC answer", "dlc.choices.forget(game.id)" in read(APP / "SteamOwnedLibrary.swift"))
check("the question's primary button opens the chooser, the alternative is the game alone",
      'confirm: "Choose DLC…"' in page_rules and 'alternative: "Game only"' in page_rules
      and "run(other, game)" in page and "backend?.set(BCDGameDLC.chooseID," in page)
check("an installed game's default is off: nothing is chosen until the player acts (the switches start at what is installed)",
      "service.desired(id)" in live and "pending[appID] ?? choices.wanted(appID) ?? installed(appID)" in service)
check("only a game BCD downloaded itself gets the section (BCD changes only its own library folder)",
      "SteamInstallPaths.isManaged(library: installedGame.library)" in live)
check("the page's live backend calls the DLC hooks (offer, section, set, opened, changes)",
      all(h in page_live for h in ["dlcOffer(game, into: &d)", "if let dlc = dlcGroup(game) { d.groups.append(dlc) }",
                                   "if dlcSet(setting, to: value, for: game) { return }", "dlcOpened(game)", "dlcChanges,"]))
check("Install, Update, Repair and Resume read the stored choice; the card menu keeps today's Install (no question)",
      "choices.wanted(appID)" in service and ".installChoosingDLC" in page and "BCDGameQuestionView(question: asked.question, prefix: prefix + \"ask.\"" in read(APP / "BCDMenu.swift"))

# Device round 1 (2026-10-22): the question waits for the lookup; DLC that declare their own depots.
check("the page's Install waits for the DLC lookup (a card, a timeout, the game alone on failure) and logs why it skipped",
      "startChecking(place, game)" in page and "checkTimeout = Task" in page and "BCDGameDLC.skipID" in page
      and 'SteamLog.event("[steam-dlc] question skipped app=\\(id) reason=\\(value)")' in live and "d.dlcCheck = service.isLooking(id) ? .checking : .failed" in live)
check("the card menu's Install opens the page for a game with owned DLC or a lookup still running",
      "now.dlcOffer != nil || now.dlcCheck == .checking { self?.openPage?(game); return }" in read(APP / "BCDMenu.swift")
      and "BCDGameDetailSource.backend?.opened(game)" in read(APP / "BCDMenu.swift"))
check("the lookup logs its start and its duration, and a lookup that cannot start says why",
      "[steam-dlc] lookup app=\\(appID) start" in service and "ms=\\(ms())" in service and "not started reason=no-connection-allowed" in service)
check("a failed lookup tells the page (it must not wait for a lookup that is over)", service.count("self.changed.send()") >= 3)
check("the lookup connects on demand like a download (SteamSession.ensureConnected through the fetcher) and never during a game session",
      "canConnect()" in service and "try await session.ensureConnected()" in fetcher and "!self.inSession" in owned)
check("the DLC apps' own product info supplies depots (fetcher cache, report, job), the downloader adds them to the game's",
      "dlcOwnDepotCache" in fetcher and "func dlcOwnDepots(for ids: [UInt32])" in fetcher and "dlcOwnDepots(for: lacking)" in service
      and "app.addingDLCDepots(dlc.extraDepots)" in dd and "report.viaOwnInfo = " in fetcher)

# Device round 2 (2026-10-22): the first install lets the player choose which DLC.
chooser = read(APP / "BCDGameDLCChooser.swift")
check("the chooser has a row per owned DLC with a switch, Select all / Select none, a total line, Cancel and Install",
      "ForEach(chooser.rows)" in chooser and "BCDGameSwitch(on: on)" in chooser and '"Select all"' in chooser and '"Select none"' in chooser
      and "chooser.summary" in chooser and '"Cancel"' in chooser and "chooser.installTitle" in chooser)
check("the list scrolls while the title, the chips, the total and the buttons stay (Install is always reachable)",
      "ScrollView(.vertical" in chooser and re.search(r"heading\s+chips\s+list\s+footer", chooser) is not None
      and re.search(r"controls\.frame\(width: 250\)\s+list", chooser) is not None and "private var controls" in chooser)
check("a controller walks every row and every button, and the list follows the ring",
      all(i in chooser for i in ['layer + "row.\\(id)"', '"game.dlc.all"', '"game.dlc.none"', '"game.dlc.cancel"', '"game.dlc.install"', "scroller.scrollTo(id, anchor: .center)"])
      and chooser.count(".bcdFocusable(") >= 4 and "BCDDLCChooserFocusScroll" in chooser)
check("the page opens the chooser's controller layer, starts on its first row, and its hints are A, X, Y, B",
      "static let dlcLayer = BCDDLCChooserView.layer" in page and "focus.openLayer(Self.dlcLayer)" in page
      and 'BCDHint(button: .x, title: "All")' in page and 'BCDHint(button: .y, title: "None")' in page and 'BCDHint(button: .a, title: "Switch")' in page
      and "!id.hasPrefix(Self.dlcLayer)" in page)
for bad in ["ProgressView(", "repeatForever", "TimelineView", "Timer.", "AsyncImage", ".blur(", "Material", "UIScreen"]:
    check(f"BCDGameDLCChooser.swift: no {bad} (cool running)", bad not in code(chooser))
long = [d for d in re.findall(r"duration: ([0-9.]+)", chooser) if float(d) > 0.25]
check("BCDGameDLCChooser.swift: animations of 0.25 s or less", not long, long)
check("the service stores exactly the chosen set and logs it with counts and bytes",
      "func chooseForNewInstall(_ appID: Int, selected: Set<UInt32>)" in service
      and '[steam-dlc] choice app=\\(appID) new-install selected=\\(valid.count) of=\\(owned.count) bytes=\\(bytes)' in service
      and "choices.set(appID, wanted: valid)" in service)
check("the live backend takes the page's list of App IDs (and all / none) and hands the set to the service",
      "BCDGameDLC.chosenSet(value)" in live and "service.chooseForNewInstall(id, selected: chosen)" in live)
check("a stored subset is what the download takes: the request is built from the stored set and nothing else",
      "wanted: wanted," in service and "let wish = wanted ?? Set(installed.keys)" in rules)

# CI and the other checks that compile the page rules.
ci = read(R / ".github/workflows/build-ipa.yml")
check("CI runs this check", "run: python3 tests/host/check-steam-dlc.py --require-swift" in ci)
check("the page-rules host checks compile BCDGameDLC.swift with the rules",
      '"BCDGameDLC.swift"' in read(R / "tests/host/check-bcd-game-page.py") and '"BCDGameDLC.swift"' in read(R / "tests/host/check-bcd-menu.py"))

# ------------------------------------------------------------------------------ Part B: the rules, compiled
def vdf_text(depots, listofdlc=None, appid=1000, name="Test Game", build=777):
    def depot(did, **kw):
        body = []
        cfg = []
        for k in ("oslist", "osarch", "language", "lowviolence"):
            if k in kw:
                cfg.append(f'"{k}" "{kw[k]}"')
        if cfg:
            body.append('"config" { ' + " ".join(cfg) + " }")
        for k in ("dlcappid", "depotfromapp"):
            if k in kw:
                body.append(f'"{k}" "{kw[k]}"')
        if "gid" in kw:
            body.append(f'"manifests" {{ "public" {{ "gid" "{kw["gid"]}" "size" "{kw.get("size", 0)}" "download" "{kw.get("download", 0)}" }} }}')
        return f'"{did}" {{ ' + " ".join(body) + " }"
    ext = f'"extended" {{ "listofdlc" "{listofdlc}" }}' if listofdlc is not None else ""
    return (f'"appinfo"\n{{\n"appid" "{appid}"\n"common" {{ "name" "{name}" "type" "game" "oslist" "windows" }}\n{ext}\n'
            f'"config" {{ "installdir" "{name}" }}\n"depots"\n{{\n' + "\n".join(depot(d, **kw) for d, kw in depots) +
            f'\n"branches" {{ "public" {{ "buildid" "{build}" }} }}\n}}\n}}\n')


def package(apps, depots=()):
    def entry(i, v):
        return b"\x02" + str(i).encode() + b"\0" + struct.pack("<I", v)
    body = b"\x00appids\0" + b"".join(entry(i, a) for i, a in enumerate(apps)) + b"\x08"
    body += b"\x00depotids\0" + b"".join(entry(i, d) for i, d in enumerate(depots)) + b"\x08"
    return b"\x00" + b"4321\0" + entry("packageid", 4321) + body + b"\x08\x08"


FIXTURES = {
    "base.vdf": vdf_text([
        (1001, dict(oslist="windows", osarch="64", gid=111, size=1000, download=500)),
        (1002, dict(oslist="windows", language="english", gid=112, size=100, download=50)),
        (1003, dict(oslist="windows", language="german", gid=113, size=100, download=50)),
        (1004, dict(oslist="macos", gid=114, size=100, download=50)),
        (2011, dict(oslist="windows", dlcappid=2001, gid=211, size=400, download=200)),
        (2012, dict(oslist="windows", osarch="64", dlcappid=2001, gid=212, size=150, download=90)),
        (2013, dict(oslist="windows", language="german", dlcappid=2001, gid=213, size=10, download=5)),
        (2021, dict(oslist="windows", dlcappid=2002, gid=221, size=60, download=30)),
        (2031, dict(oslist="macos", dlcappid=2003, gid=231, size=10, download=5)),
        (2041, dict(oslist="windows", dlcappid=2004, gid=241, size=70, download=35)),
        (2051, dict(oslist="windows", dlcappid=2005)),
        (2061, dict(oslist="windows", dlcappid=2001, depotfromapp=9999, gid=261, size=5, download=3)),
        (2071, dict(oslist="windows", dlcappid=2001, lowviolence=1, gid=271, size=5, download=3)),
        (2081, dict(oslist="windows", dlcappid=2007, gid=281, size=20, download=10)),
    ], listofdlc="2001,2002,2003,2004,2005,1000,2001"),
    "plain.vdf": vdf_text([(1001, dict(oslist="windows", osarch="64", gid=111, size=1000, download=500))]),
    "old32.vdf": vdf_text([
        (3001, dict(oslist="windows", osarch="32", gid=301, size=100, download=50)),
        (3111, dict(oslist="windows", osarch="32", dlcappid=3101, gid=311, size=10, download=5)),
        (3112, dict(oslist="windows", osarch="64", dlcappid=3101, gid=312, size=10, download=5)),
    ], listofdlc="3101", appid=3000, name="Old Game"),
}
# A game whose DLC mostly declare their depots themselves (X-Plane 12's maps): the game lists 4103's depot without a manifest and 4104's
# with one; 4101 and 4102 have no depot in the game's list at all.
FIXTURES.update({
    "xp.vdf": vdf_text([
        (4001, dict(oslist="windows", osarch="64", gid=401, size=9000, download=4000)),
        (4011, dict(oslist="windows", dlcappid=4103)),
        (4012, dict(oslist="windows", dlcappid=4104, gid=412, size=70, download=35)),
    ], listofdlc="4101,4102,4103,4104,4105", appid=4000, name="Flight Game"),
    # 4101: a Windows depot, a macOS depot, a German-language one, one without a manifest.
    "dlc4101.vdf": vdf_text([
        (4111, dict(oslist="windows", gid=4111, size=800, download=400)),
        (4112, dict(oslist="macos", gid=4112, size=800, download=400)),
        (4113, dict(oslist="windows", language="german", gid=4113, size=9, download=4)),
        (4114, dict(oslist="windows")),
    ], appid=4101, name="Scenery Europe").replace('"type" "game"', '"type" "DLC"'),
    # 4102: only a depot another app owns (the game lists it itself): nothing to install.
    "dlc4102.vdf": vdf_text([(4121, dict(oslist="windows", depotfromapp=4000, gid=4121, size=5, download=2))],
                            appid=4102, name="Scenery Asia").replace('"type" "game"', '"type" "DLC"'),
    # 4103: supplies the manifest of the depot the game lists without one; 4104: lists the depot the game lists with a manifest, too.
    "dlc4103.vdf": vdf_text([(4011, dict(oslist="windows", gid=4031, size=300, download=150))],
                            appid=4103, name="Airports").replace('"type" "game"', '"type" "DLC"'),
    "dlc4104.vdf": vdf_text([(4012, dict(oslist="windows", gid=9999, size=1, download=1))],
                            appid=4104, name="Aircraft Pack").replace('"type" "game"', '"type" "DLC"'),
})
BINARIES = {"pkg-a.bin": package([1000, 2001, 2002, 2003, 2005], [1001, 2011]), "pkg-b.bin": package([2006, 2007], [2081]),
            "pkg-x.bin": package([4000, 4101, 4102, 4103, 4104], [4001])}

if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift parts", not args.require_swift)
else:
    fetcher_text = read(APP / "SwiftSteam/Library/SteamLibraryFetcher.swift")
    vdf_parser = "import Foundation\n\n" + fetcher_text[fetcher_text.index("// MARK: - Simple VDF Binary Parser"):]
    with tempfile.TemporaryDirectory(prefix="steam-dlc-") as tmp:
        t = Path(tmp)
        (t / "fixtures").mkdir()
        for name, text in FIXTURES.items():
            (t / "fixtures" / name).write_text(text)
        for name, data in BINARIES.items():
            (t / "fixtures" / name).write_bytes(data)
        (t / "VDF.swift").write_text(vdf_parser)
        # SteamAppInfo.swift's SteamLanguage (the game language, English unless chosen) logs and reads bcd.cfg.
        (t / "stubs.swift").write_text("import Foundation\nenum SteamLog { static func event(_ m: String) {}; static func trace(_ m: @autoclosure () -> String) {} }\n"
                                       "enum MadeiraConfig { static func get(_ key: String) -> String? { nil }\n"
                                       "    @discardableResult static func set(_ key: String, _ value: String?) -> Bool { true } }\n")
        sources = [t / "VDF.swift", t / "stubs.swift", APP / "SwiftSteam/Library/SteamAppInfo.swift", APP / "SteamKeyValues.swift", APP / "SteamInstall.swift", APP / "ExternalStorage.swift",
                   APP / "SwiftSteam/Install/AppManifestWriter.swift", DLC / "BCDSteamDLC.swift"]
        shutil.copy(R / "tests/host/steam_dlc_checks.swift", t / "main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "rules"), str(t / "main.swift")] + [str(s) for s in sources],
                           capture_output=True, text=True)
        check("the DLC rules compile on their own with the production app info, record writer and VDF parser", r.returncode == 0,
              (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            r = subprocess.run([str(t / "rules"), str(t / "fixtures")], capture_output=True, text=True, timeout=120)
            print("\n".join("     " + line for line in r.stdout.strip().split("\n")))
            check("the DLC rules hold (detection, selection, the job, the record, removal, the choice, the log)", r.returncode == 0,
                  (r.stdout + r.stderr)[-2500:])

print("PASS: Steam DLC" if ok else "FAIL: Steam DLC")
sys.exit(0 if ok else 1)
