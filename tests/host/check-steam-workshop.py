#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Steam Workshop in BCD (app/Madeira/BCDWorkshop.swift, BCDWorkshopView.swift, BCDSteamWorkshop/; docs/STEAM_LIBRARY.md, "Steam Workshop").

Never contacts Steam. Part A reads the sources: the Workshop does nothing until the player opens a page and presses a button, what changes the
Steam account asks first, the pages name no engine type, the files are registered and carry the right notices. Part B compiles and runs:
SteamWorkshopFiles.swift (items, requests, Valve's record) under AddressSanitizer, and the pages' rules (BCDWorkshop.swift) with the page's
rules. (A whole item download runs in tests/host/check-steam-library.py, through a local content server.)
Run with python3; --require-swift fails when xcrun swiftc is missing (the macOS build runner).
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
SHOP = APP / "BCDSteamWorkshop"
HEADER = ["// SPDX-License-Identifier: GPL-3.0-only",
          "// Copyright 2026 thebcd69 (BCD)",
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md."]
UPSTREAM_HEADER = ["// SPDX-License-Identifier: GPL-3.0-or-later",
                   "// Copyright 2026 ft7t8drgwf-wq",
                   "// Madeira Converter Exception: see LICENSE-EXCEPTION.md"]
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


def literals(text):
    return re.findall(r'"((?:[^"\\]|\\.)*)"', text)


# ------------------------------------------------------------------------------ Part A: the sources
rules, view = read(APP / "BCDWorkshop.swift"), read(APP / "BCDWorkshopView.swift")
live, files, model = read(SHOP / "BCDWorkshopLive.swift"), read(SHOP / "SteamWorkshopFiles.swift"), read(SHOP / "SteamWorkshopModel.swift")
page, page_live, library = read(APP / "BCDGamePage.swift"), read(APP / "BCDGamePageLive.swift"), read(APP / "BCDLibraryView.swift")
owned, downloader = read(APP / "SteamOwnedLibrary.swift"), read(APP / "SwiftSteam/Content/DepotDownloader.swift")
protocol, session = read(APP / "SwiftSteam/Core/SteamProtocol.swift"), read(APP / "SwiftSteam/Core/SteamSession.swift")
cmsession, appinfo = read(APP / "SwiftSteam/Core/SteamCMSession.swift"), read(APP / "SwiftSteam/Library/SteamAppInfo.swift")
project, ci = read(R / "app/Madeira.xcodeproj/project.pbxproj"), read(R / ".github/workflows/build-ipa.yml")
preview_py = read(R / "tools/ui-preview.py")

for f in [APP / "BCDWorkshop.swift", APP / "BCDWorkshopView.swift", SHOP / "BCDWorkshopLive.swift", R / "app/BCDPreview/BCDWorkshopSample.swift",
          R / "tests/host/steam_workshop_ui_checks.swift"]:
    check(f"{f.name}: BCD's licence header, exactly", read(f).split("\n")[:3] == HEADER)
# steam_workshop_files_checks.swift is mostly Madeira's (#491): BCD's copyright, no "BCD Material" line (LICENSE-BCD.md section 1),
# and the credit (owner 2026-11-06).
wf = read(R / "tests/host/steam_workshop_files_checks.swift")
check("steam_workshop_files_checks.swift: BCD's copyright and the Madeira credit, no BCD Material line",
      wf.split("\n")[:2] == HEADER[:2] and "BCD Material" not in wf[:800] and "Copyright 2026 ft7t8drgwf-wq. This file was taken from" in wf[:1500])
check("check-steam-workshop.py: BCD's licence header, exactly", [l.replace("# ", "// ", 1) for l in read(__file__).split("\n")[1:4]] == HEADER)
for f in [SHOP / "SteamWorkshopFiles.swift", SHOP / "SteamWorkshopModel.swift"]:
    lines = read(f).split("\n")
    check(f"{f.name}: the contributor's licence lines are untouched", lines[:3] == UPSTREAM_HEADER)
    check(f"{f.name}: marked as modified by BCD, with the date (GPL-3.0 section 5)", "Modified by BCD (thebcd69), 2026-11-05" in "\n".join(lines[:8]))
for f in ["SwiftSteam/Core/SteamProtocol.swift", "SwiftSteam/Core/SteamSession.swift", "SwiftSteam/Core/SteamCMSession.swift",
          "SwiftSteam/Content/DepotDownloader.swift", "SteamOwnedLibrary.swift", "SwiftSteam/Library/SteamAppInfo.swift"]:
    head = read(APP / f).split("import ", 1)[0]
    check(f"{f}: marked as modified by BCD for the Workshop, with the date", re.search(r"Modified by BCD \(thebcd69\), 2026-11-05: [^\n]*(Workshop|callServiceMethodWithResult)", head) is not None)
    check(f"{f}: the original licence lines are untouched",
          head.split("\n")[0] == "// SPDX-License-Identifier: GPL-3.0-or-later" and head.split("\n")[1].startswith("// Copyright 2026 ")
          and head.split("\n")[2] == "// Madeira Converter Exception: see LICENSE-EXCEPTION.md")
for f in [APP / "BCDWorkshop.swift", APP / "BCDWorkshopView.swift", SHOP / "BCDWorkshopLive.swift", SHOP / "SteamWorkshopFiles.swift", SHOP / "SteamWorkshopModel.swift"]:
    rel = f.relative_to(APP).as_posix()
    check(f"{f.name}: built by the Xcode project (build file, reference and group entry)",
          project.count(f"/* {rel} in Sources */") == 2 and project.count(f"/* {f.name} */") >= 3, rel)
    check(f"{f.name}: no \"Madeira\" in a string it shows or logs (the env key MADEIRA_STEAM_WORKSHOP is the one name that stays)",
          not [x for x in literals(read(f)) if "madeira" in x.lower() and x != "MADEIRA_STEAM_WORKSHOP"])
ids = sorted(set(re.findall(r"\b(A[12]000C8[0-9A-F])\b", project)))
check("project IDs A1000C80-C84 and A2000C80-C84 are this feature's, each used for exactly its file",
      ids == [f"A{n}000C8{i}" for n in (1, 2) for i in range(5)], ids)

# Keyed, and nothing by itself.
check("env.MADEIRA_STEAM_WORKSHOP = 0 turns it off (on by default; the pages open only on the player's action)",
      'static var workshopEnabled: Bool { enabled && SteamSignIn.flag("MADEIRA_STEAM_WORKSHOP", default: true) }' in owned)
check("Workshop calls only while signed in and no game session holds the account",
      "var workshopReady: Bool { Self.workshopEnabled && signedIn && !inSession }" in owned
      and owned.count("guard workshopReady else { throw SteamError.disconnected }") == 2)
check("a game session stops Workshop downloads", "SteamWorkshopModel.shared.stopForSession()" in owned)
check("the model starts nothing when it is made: no init, no timer, no task at the top of the class",
      "init(" not in model.split("// MARK: Steam")[0] and "Timer" not in code(model) and "Task {" not in model.split("// MARK: Steam")[0])
calls = [(m.group(1), model[:m.start()].rfind("func ")) for m in re.finditer(r"workshopCall\((\.\w+)\)", model)]
by_method = {}
for method, at in calls:
    name = re.match(r"func (\w+)", model[at:]).group(1)
    by_method.setdefault(method, set()).add(name)
check("only subscribe changes the account's subscriptions (.workshopSubscribe), only unsubscribe removes one",
      by_method.get(".workshopSubscribe") == {"subscribe"} and by_method.get(".workshopUnsubscribe") == {"unsubscribe"}, by_method)
check("the reads are the subscribed list, the details of installed items and Browse",
      by_method.get(".workshopGetUserFiles") == {"refresh"} and by_method.get(".workshopGetDetails") == {"refresh"}
      and by_method.get(".workshopQueryFiles") == {"browse"}, by_method)
check("opening a page only reads: the live backend's opened() calls refresh and nothing else",
      re.search(r"func opened\(_ appID: Int\) \{.*?model\.refresh\(id\) \}\n    \}", live, re.S) is not None
      and live.count("model.subscribe(") == 1 and live.count("model.unsubscribe(") == 1)
check("the live backend subscribes and unsubscribes only inside its subscribe/unsubscribe, which the page calls after the question",
      "func subscribe(_ item: BCDWorkshopListing, appID: Int) {\n        guard SteamOwnedLibrary.workshopEnabled" in live
      and "func unsubscribe(_ item: BCDWorkshopListing, appID: Int, removeFiles: Bool) {\n        guard SteamOwnedLibrary.workshopEnabled" in live
      and re.search(r"if let question = BCDWorkshop\.question\(action, item\) \{\s+asking = BCDWorkshopAsk", view) is not None
      and "case .subscribe: backend?.subscribe(item, appID: appID)" in view and view.count("backend?.subscribe(") == 1)
check("subscribing, both unsubscribes and removing files each ask first; a download and a stop do not",
      all(f"case .{a}: return" in rules for a in ["subscribe", "unsubscribeAndRemove", "unsubscribeKeep", "remove"])
      and "case .download, .update, .stop: return nil" in rules)
check("a download is the model's own queue; a subscribe also downloads, which the question says",
      "download(item, appID: appID)" in model and "BCD then downloads it" in rules)
check("older single-file items download only from Steam's hosts over https",
      'host.hasSuffix(".steamusercontent.com")' in model and 'url.scheme == "https"' in model and 'text = "https://"' in model)
check("an item is a manifest of the app's own Workshop depot, through the downloader's chunk path",
      "func installWorkshopItem(" in downloader and "depotKey(depotID: appID, appID: appID)" in downloader
      and "fetchManifest(depotID: appID, appID: appID, manifestGID: manifestGID" in downloader
      and "Self.prepare(plans: [plan], installURL: folder, journalDir: journalDir)" in downloader)
check("the item's record is written after its files", "try record.write(steamApps: steamApps)" in model)
check("downloads, subscriptions and installs are logged, with App IDs and item IDs only",
      all(t in (model + downloader) for t in ["[steam-workshop] subscribed", "[steam-workshop] unsubscribed", "[steam-workshop] installed",
                                              "[steam-workshop] download begin", "[steam-workshop] download complete"])
      and not [l for l in (model + downloader + live).split("\n") if "[steam-workshop]" in l and re.search(r"accountName|SteamID|steamID|token|password|email", l)])
for method in ["PublishedFile.GetUserFiles#1", "PublishedFile.QueryFiles#1", "PublishedFile.GetDetails#1", "PublishedFile.Subscribe#1", "PublishedFile.Unsubscribe#1"]:
    check(f"service method {method}", f'"{method}"' in protocol)
check("service calls can read the response EResult, and callServiceMethod keeps its behaviour",
      "func callServiceMethodWithResult(" in session and "return (response.header.eresult, response.body)" in session
      and "try await callServiceMethodWithResult(method: method, body: body, timeout: timeout).body" in session
      and "func callServiceMethodWithResult(" in cmsession)
check("the owned library records which games have a Workshop",
      'info.workshopVisible = (common["workshop_visible"] as? String) == "1"' in appinfo and "workshop = info.workshopVisible" in owned)

# The pages: BCD's own screens, no engine type, the live side in a folder the preview build does not read.
check("the screens name no engine type (BCDWorkshop.swift, BCDWorkshopView.swift, BCDGamePage.swift, BCDLibraryView.swift)",
      not re.findall(r"\b(SteamOwnedLibrary|SteamWorkshopModel|SteamWorkshopItem|SteamWorkshopRecord|SteamGamesModel|SteamSignIn|DepotDownloader|LibraryModel|SteamLibraryFetcher)\b",
                     code(rules + view + page + library)))
check("BCDWorkshop.swift and BCDWorkshopView.swift are top-level BCD screen files (the preview build compiles them); the engine side is in BCDSteamWorkshop/",
      (APP / "BCDWorkshop.swift").is_file() and (APP / "BCDWorkshopView.swift").is_file() and (SHOP / "BCDWorkshopLive.swift").is_file()
      and "BCDWorkshop" not in preview_py)
check("the live backend is found by its Objective-C name", '@objc(BCDWorkshopLive)' in live and 'NSClassFromString("BCDWorkshopLive")' in rules)
check("Options › Steam gets a Steam Workshop row, only for an installed game Steam has not said has no Workshop",
      'kind: .opens(.workshop)' in page_live and "steam.game(appID)?.workshop != false || items > 0" in page_live
      and "SteamOwnedLibrary.workshopEnabled, steam.signedIn" in page_live and "case .workshop: row.run = { openWorkshop() }" in page)
check("the game page opens the Workshop page over itself and gives it the controller's buttons",
      ".bcdWorkshop($workshop)" in page and "guard workshop == nil else { return }" in page and "workshop = .game(id)" in page)
check("the Library's Workshop button is under the Steam chip only, when BCD is signed in and a Steam game is installed",
      "filter == .store(.steam) && BCDWorkshopSource.backend?.available == true" in library and ".bcdWorkshop($workshop)" in library
      and '"library.workshop", "Workshop", "square.stack.3d.up"' in library)
check("the pages say the account is changed by subscribing, and where items go", "change your Steam account" in rules and "Workshop folder beside the game" in rules)
for bad in ["ProgressView(", "repeatForever", "TimelineView", "Timer.", "AsyncImage", ".blur(", "Material", "UIScreen"]:
    check(f"BCDWorkshopView.swift: no {bad} (cool running; pictures go through BCDArt)", bad not in code(view))
long = [d for d in re.findall(r"duration: ([0-9.]+)", view) if float(d) > 0.25]
check("BCDWorkshopView.swift: animations of 0.25 s or less", not long, long)
check("a controller walks every control: the rows, the buttons, the card and the question",
      all(i in view for i in ['"workshop.back"', '"workshop.close"', '"workshop.browse"', '"workshop.all"', '"workshop.safari"', '"workshop.search"',
                              'Self.cardLayer + "first"', "BCDGameQuestionView(question: ask.question, prefix: Self.askLayer)", "focus.openLayer(Self.layer)"])
      and view.count(".bcdFocusable(") >= 10)
check("Browse lists a game's Workshop with a read only (a query), nothing is subscribed by looking",
      "func browse(" in live and "model.browse(id, order: sort, text: text, page: UInt32(clamping: page))" in live)
check("CI runs this check", "run: python3 tests/host/check-steam-workshop.py --require-swift" in ci)

# ------------------------------------------------------------------------------ Part B: compiled
if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift parts", not args.require_swift)
else:
    swiftc = ["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5"]
    # B1: the items, the requests and Valve's record, under AddressSanitizer.
    with tempfile.TemporaryDirectory(prefix="steam-workshop-files-") as tmp:
        t = Path(tmp)
        shutil.copy(R / "tests/host/steam_workshop_files_checks.swift", t / "checks.swift")
        build = subprocess.run(swiftc + ["-sanitize=address", "-o", str(t / "check"), str(t / "checks.swift"), str(SHOP / "SteamWorkshopFiles.swift"),
                                         str(APP / "SwiftSteam/Proto/SteamProtoMessages.swift"), str(APP / "SwiftSteam/Core/SteamError.swift"),
                                         str(APP / "SteamKeyValues.swift"), str(APP / "SteamInstall.swift"), str(APP / "ExternalStorage.swift")],
                               capture_output=True, text=True)
        check("SteamWorkshopFiles.swift compiles on the host with the protobuf helpers", build.returncode == 0, (build.stderr or build.stdout)[-3000:])
        if build.returncode == 0:
            run = subprocess.run([str(t / "check")], env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0"), capture_output=True, text=True, timeout=300)
            print("\n".join("     " + line for line in run.stdout.strip().split("\n")))
            check("the Workshop's items, requests and record hold under AddressSanitizer", run.returncode == 0, (run.stdout + run.stderr)[-2500:])
    # B2: the pages' rules.
    start = page.index("// MARK: - The page's rules")
    end = page.index("// MARK: - Opening the page")
    with tempfile.TemporaryDirectory(prefix="steam-workshop-ui-") as tmp:
        t = Path(tmp)
        (t / "BCDStore.swift").write_text(read(APP / "BCDStore.swift"))
        (t / "BCDGameDLC.swift").write_text(read(APP / "BCDGameDLC.swift"))
        (t / "BCDWorkshop.swift").write_text(rules)
        (t / "Rules.swift").write_text("import Combine\nimport Foundation\nimport SwiftUI\n\n" + page[start:end])
        shutil.copy(R / "tests/host/steam_workshop_ui_checks.swift", t / "Main.swift")
        r = subprocess.run(swiftc + ["-o", str(t / "rules"), str(t / "BCDStore.swift"), str(t / "BCDGameDLC.swift"), str(t / "BCDWorkshop.swift"),
                                     str(t / "Rules.swift"), str(t / "Main.swift")], capture_output=True, text=True)
        check("the pages' rules compile on their own with the page's rules and BCDStore.swift", r.returncode == 0, (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            r = subprocess.run([str(t / "rules"), "-AppleLocale", "en_US", "-AppleLanguages", "(en)"], capture_output=True, text=True, timeout=120)
            print("\n".join("     " + line for line in r.stdout.strip().split("\n")))
            check("the pages' rules hold (captions, the card's actions, the questions, the words)", r.returncode == 0, (r.stdout + r.stderr)[-2500:])

print("PASS: Steam Workshop" if ok else "FAIL: Steam Workshop")
sys.exit(0 if ok else 1)
