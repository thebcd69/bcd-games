#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's Library tab and Add a game (app/Madeira/BCDLibraryView.swift, BCDAddGame.swift;
docs/BCD-UI-ARCHITECTURE.md, docs/research/parity-2026-10-18.md B2, B6, B9, B12, B15, B17, B18,
A13, C20).

Checks:
  - BCD's licence header on the library view, Add a game, its live backend (BCDAddGameLive.swift)
    and its preview sample;
  - the screens name no engine type; the live backend is in tools/ui-preview.py's ENGINE_BOUND and
    the Xcode project builds both files under the game page's IDs (A1000B40-A1000B5F,
    A2000B40-A2000B5F);
  - Library: a search field that filters by name, opened by Y on the library tab; Refresh (a
    button and pull to refresh) calls the store's refreshLibrary; signed out of Steam a card offers
    the sign-in; an account with no Windows games says so; with no game added by hand a line says
    how games get into drive_c; the filter and the order are kept across games and launches; every
    button can be reached with a controller;
  - Add a game: drive_c's folders and programs only (links out of drive_c left out), the program
    read as the library reads one (LibraryModel.inspect), saved with its name (LibraryModel.save),
    one program never added twice, Add every game in drive_c (BCDLibraryImport); its focus layer, A
    and B; the game's page opens once the store lists the game, on its Library section;
  - BCDLiveFrontEnd shows Add a game and the game page full screen (Choose saves on Cloud saves),
    never the classic program browser or Game details;
  - the rules of Add a game, compiled on this Mac and run (paths, batch files, a program's line);
  - no "Madeira" in the text; no animation that runs by itself; animations of 0.25 s or less;
  - the preview build compiles (tools/ui-preview.py --build-only; --no-preview leaves it out);
  - CI runs this check.
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
LIBRARY = APP / "BCDLibraryView.swift"
ADD = APP / "BCDAddGame.swift"
ADD_LIVE = APP / "BCDAddGameLive.swift"
ADD_SAMPLE = R / "app/BCDPreview/BCDAddGameSample.swift"
SHELL = APP / "BCDFrontEnd.swift"
FRONT = APP / "BCDLiveFrontEnd.swift"
HEADER = ["// SPDX-License-Identifier: GPL-3.0-only",
          "// Copyright 2026 thebcd69 (BCD)",
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md."]
ENGINE = ["LibraryModel", "LibraryEntry", "LibraryDetail", "LibraryController", "LibraryPrefs", "SteamOwnedLibrary",
          "SteamGamesModel", "SteamGamesRules", "SteamSignIn", "SteamSignInModel", "MadeiraDock", "MadeiraDockModel",
          "MadeiraConfig", "GameProfile", "GameRecommendations", "GameLibrary", "LibraryGame", "BCDLibraryImport",
          "ExecutableBrowser", "LogStore", "ContentView", "BCDLiveStore", "BCDLiveID", "BCDAddGameLive", "SteamCatalog"]

ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
ap.add_argument("--no-preview", action="store_true")
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


def code(text):
    """The text without // comments (a "//" after ":" is an address's, not a comment)."""
    return "\n".join(re.split(r"(?<!:)//", line, maxsplit=1)[0] if not line.strip().startswith("///") else ""
                     for line in text.split("\n"))


def bare(text):
    """The code with its string literals emptied: words in quotes are text, not types."""
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', code(text))


library, add, add_live, sample = LIBRARY.read_text(), ADD.read_text(), ADD_LIVE.read_text(), ADD_SAMPLE.read_text()
shell, front = SHELL.read_text(), FRONT.read_text()
home = (APP / "BCDHomeView.swift").read_text()

# ---- files, layers, project --------------------------------------------------------------------
for f in (LIBRARY, ADD, ADD_LIVE, ADD_SAMPLE):
    check(f"{f.relative_to(R)} starts with BCD's licence header", f.read_text().split("\n")[:3] == HEADER)
for f, text in ((LIBRARY, library), (ADD, add), (ADD_SAMPLE, sample)):
    named = sorted({t for t in ENGINE if re.search(r"\b" + t + r"\b", bare(text))})
    check(f"{f.name} names no engine type", not named, named)
preview_tool = (R / "tools/ui-preview.py").read_text()
bound = re.search(r"^ENGINE_BOUND = \{(.*?)\}", preview_tool, re.M | re.S)
check("BCDAddGameLive.swift is in ui-preview's ENGINE_BOUND (the preview build leaves it out)",
      bound is not None and '"BCDAddGameLive.swift"' in bound.group(1))
check("the preview tool lists Add a game among its screens", re.search(r'^SCREENS = \[.*"add".*\]', preview_tool, re.M))
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
for name in ("BCDAddGame.swift", "BCDAddGameLive.swift"):
    build = re.findall(r"(\w+) /\* " + re.escape(name) + r" in Sources \*/ = \{isa = PBXBuildFile; fileRef = (\w+) ", pbx)
    check(f"the Xcode project builds {name} (build file, file reference, group, Sources)",
          pbx.count(f"/* {name} in Sources */") == 2 and pbx.count(f"path = {name};") == 1
          and pbx.count(f"/* {name} */") == 3 and len(build) == 1)
    if build:
        b, f = build[0]
        check(f"{name} uses the game page's project IDs",
              re.fullmatch(r"A1000B[45][0-9A-F]", b) and re.fullmatch(r"A2000B[45][0-9A-F]", f), f"{b} {f}")

# ---- the library ---------------------------------------------------------------------------------
check("Library: a search field that filters the games by name",
      'TextField("Find a game", text: $query)' in library
      and "list = list.filter { $0.title.localizedCaseInsensitiveContains(words) }" in library)
check("Library: Add a game, Search and Refresh stay in sight at the end of the filter row",
      'round("library.search", searching ? "xmark" : "magnifyingglass"' in library
      and 'rowButton("library.add", "Add a game", "plus") { store.actions?.addGameFromFiles() }' in library
      and "so turning the phone does not replace it" in library)
check("Library: Y on the library tab opens the search (and closes it again)",
      'if tab == .library { list.append((BCDHint(button: .y, title: "Search"), { librarySearch += 1 })) }' in shell
      and "case .library: BCDLibraryView(searchRequest: librarySearch)" in shell
      and ".onChange(of: searchRequest) { _, _ in toggleSearch() }" in library and "typing = searching" in library)
check("Library: Refresh, a button and pull to refresh, calls the store's refreshLibrary",
      'round("library.refresh", "arrow.clockwise", "Refresh the library") { store.actions?.refreshLibrary() }' in library
      and ".refreshable { store.actions?.refreshLibrary() }" in library)
check("Library: signed out of Steam, a card offers the sign-in",
      "if steamShown, let steam, steam.available, steam.name == nil {" in library
      and 'Text("Sign in to Steam")' in library
      and '.bcdFocusable("library.signin", cornerRadius: 18) { store.actions?.signIn(.steam) }' in library)
check("Library: a Steam account with no Windows games says so",
      "steam.name != nil, store.count(.steam) == 0" in library and "There are no Windows games on this Steam account" in library)
check("Library: with no game added by hand, how games get into drive_c",
      "store.count(.local) <= 1" in library and "BCD › wine › drive_c with the Files app" in library)
check("Library: the filter and the order are kept across games and launches (the old name-or-recent switch still counts until a sort is chosen)",
      '@AppStorage("bcd.library.filter") private var filterKey' in library
      and '@AppStorage("bcd.library.sort") private var sortKey = ""' in library
      and '@AppStorage("bcd.library.sortByName") private var sortByName = false' in library
      and "BCDLibrarySort(rawValue: sortKey) ?? (sortByName ? .name : .played)" in library
      and "run: { sortKey = item.rawValue }" in library
      and "@State private var filter" not in library and "@State private var sortKey" not in library)
check("Library: BCD's own sort menu offers every sort the classic library had (Library.swift's Sort by: played, name, added, size)",
      'case played, name, added, size' in library and 'return "Last played"' in library and 'return "Recently added"' in library
      and 'return "Folder size"' in library and 'case .name: return "Name"' in library
      and 'rowButton("library.sort", sort.title, "arrow.up.arrow.down", label: "Sort by: \\(sort.title)") { showSortMenu() }' in library
      and 'BCDMenuContent(title: "Sort by", items: BCDLibrarySort.allCases.map' in library
      and "return sort.sorted(list)" in library)
# Wave 3 (2026-10-19): the classic library's Sort by picker (played, name, added, size) is deleted; the four sorts
# are BCD's own, pinned in the assertion above.
check("a game carries its place in the library's list, which Recently added sorts by (the store fills it from the library's entries)",
      "var added: Int? = nil" in (APP / "BCDStore.swift").read_text() and "a.added == b.added" in (APP / "BCDStore.swift").read_text()
      and "for (position, entry) in library.entries.enumerated()" in (APP / "BCDLiveStore.swift").read_text()
      and "game.added = position" in (APP / "BCDLiveStore.swift").read_text()
      and "game.added = steamPositions[item.id]" in (APP / "BCDLiveStore.swift").read_text())
check("Home: a new install says how games get in, beside the Add a game tile",
      "private var noGamesYet: Bool { store.games.allSatisfy { $0.store == .local } && store.count(.local) <= 1 }" in home
      and "if noGamesYet {" in home and "BCD › wine › drive_c with the Files app, then tap + Add a game." in home)
check("Library: Add a game is the store's action", "store.actions?.addGameFromFiles()" in library)
check("Library: games whose Steam Cloud saves differ from the device's get a line each, opening the game's page",
      "store.savesDiffer.compactMap { id in store.games.first { $0.id == id } }" in library
      and "Steam Cloud and this device have different saves for" in library
      and "Button { opened = game } label: {" in library
      and '.bcdFocusable("library.saves.\\(game.id)", cornerRadius: 16) { opened = game }' in library
      and "@Published var savesDiffer: [String] = []" in (APP / "BCDStore.swift").read_text())
plain = code(library)
check("Library: every button can be reached with a controller",
      len(re.findall(r"\bButton\b[ ({]", plain)) <= len(re.findall(r"\.bcdFocusable\(", plain)) + 1,
      "Button %d, .bcdFocusable( %d" % (len(re.findall(r"\bButton\b[ ({]", plain)), len(re.findall(r"\.bcdFocusable\(", plain))))

# ---- Add a game ----------------------------------------------------------------------------------
check("Add a game finds its live backend by its Objective-C name",
      'NSClassFromString("BCDAddGameLive")' in add and "@objc(BCDAddGameLive)" in add_live
      and "BCDAddGameSource.backend = BCDAddGameSample()" in (R / "app/BCDPreview/BCDPreviewApp.swift").read_text())
for what, needle in [
    ("drive_c's folders and programs (.exe, .bat, .cmd)", "LibraryModel.programExtensions.contains(file.pathExtension.lowercased())"),
    ("... only drive_c and the authorized external games folder, links followed", 'ExternalGameStorage.shared.relativePath(for: url, drive: drive) != nil'),
    ("... folders first, then by name", "$0.folder != $1.folder ? $0.folder : $0.name.localizedStandardCompare($1.name) == .orderedAscending"),
    ("the program is read as the library reads one", "let entry = try LibraryModel.inspect(url(path))"),
    ("it is saved to the library with its name", "if !name.isEmpty { entry.title = name }\n        library.save(entry)"),
    ("a program the library holds is not added twice", "$0.relativePath.lowercased() == entry.relativePath.lowercased()"),
    ("the answer is the game's id in the store", "return BCDLiveID.local(entry.id)"),
    ("Add every game in drive_c is the library's import", "BCDLibraryImport.run { added in done.resume(returning: added) }"),
]:
    check(f"Add a game: {what}", needle in add_live, needle)
check("Add a game: its own focus layer, A and B (back a folder, then out)",
      "focus.openLayer(Self.layer)" in add and ".onDisappear { focus.closeLayer(Self.layer) }" in add
      and 'BCDHint(button: .a, title: "Select")' in add
      and 'BCDHint(button: .b, title: picked != nil || !folder.isEmpty ? "Back" : "Close")' in add)
check("Add a game: Add every game in drive_c, and where games come from",
      'title: busy ? "Adding…" : "Add every game in drive_c"' in add and "BCD › wine › drive_c with the Files app" in add)
check("Add a game: the game's page opens once the store lists the game",
      "if let waiting, ids.contains(waiting) { finish(waiting) }" in add
      and "if store.games.contains(where: { $0.id == id }) { finish(id) }" in add)
plain = code(add)
check("Add a game: every button can be reached with a controller",
      len(re.findall(r"\bButton\(", plain)) <= len(re.findall(r"\.bcdFocusable\(", plain)),
      "Button( %d, .bcdFocusable( %d" % (len(re.findall(r"\bButton\(", plain)), len(re.findall(r"\.bcdFocusable\(", plain))))
for focus_id in ('"add.close"', '"add.back"', '"add.every"', '"add.item." + item.path', '"add.confirm"', '"add.cancel"'):
    check(f"Add a game: a control carries the focus id {focus_id}", focus_id in add)

# ---- the app shows BCD's screens -----------------------------------------------------------------
check("the app shows Add a game and the game page full screen, never the classic browser or Game details",
      ".fullScreenCover(item: pageShown) { page in pageScreen(page) }" in front and "BCDAddGame(close:" in front
      and "BCDGamePage(gameID:" in front and "ExecutableBrowser" not in code(front) and "LibraryDetail" not in code(front))
check("a game just added opens on its Library section; Choose saves opens on Cloud saves",
      'opening = .group("library")' in front
      and "opening = entry.steamAppID != nil && SteamOwnedLibrary.cloudEnabled ? .cloud : nil" in front)

# ---- cool running and words ----------------------------------------------------------------------
for f, text in ((LIBRARY, library), (ADD, add), (ADD_SAMPLE, sample)):
    plain = code(text)
    for bad in ("repeatForever", "ProgressView(", "TimelineView", "Timer.", "AsyncImage", ".blur(", "Material", "CADisplayLink"):
        check(f"{f.name}: no {bad}", bad not in plain)
    long = [d for d in re.findall(r"duration: ([0-9.]+)", plain) if float(d) > 0.25]
    check(f"{f.name}: animations of 0.25 s or less", not long, long)
for f, text in ((LIBRARY, library), (ADD, add), (ADD_LIVE, add_live), (ADD_SAMPLE, sample)):
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', code(text))
    shown = [s for s in literals if "madeira" in s.lower()]
    check(f"{f.name}: no \"Madeira\" in its text", not shown, shown)

# ---- the rules of Add a game, compiled and run -----------------------------------------------------
TEST = r'''
import Foundation
@main struct Main {
    static func main() {
        var failures = 0
        func check(_ what: String, _ ok: Bool) { print((ok ? "ok   " : "FAIL ") + what); if !ok { failures += 1 } }
        check("a path's folder", BCDDrivePath.parent("Games/Warcraft III/war3.exe") == "Games/Warcraft III"
              && BCDDrivePath.parent("setup.exe") == "")
        check("a path as it reads", BCDDrivePath.trail("") == "drive_c" && BCDDrivePath.trail("Games/Warcraft III") == "drive_c › Games › Warcraft III")
        check("a batch file", BCDDriveItem(name: "start.BAT", path: "x/start.BAT", folder: false).batch
              && !BCDDriveItem(name: "war3.exe", path: "war3.exe", folder: false).batch
              && !BCDDriveItem(name: "a.bat", path: "a.bat", folder: true).batch)
        check("a program's line", BCDProgramInfo(title: "x", windowsPath: "C:\\x.exe", bits: 32, api: "D3D9").summary == "32-bit · Direct3D 9"
              && BCDProgramInfo(title: "x", windowsPath: "C:\\x.exe", bits: 64, api: nil).summary == "64-bit"
              && BCDProgramInfo(title: "x", windowsPath: "C:\\x.bat", bits: 0, api: nil).summary.hasPrefix("A batch file"))
        print(failures == 0 ? "add a game rules: all passed" : "add a game rules: \(failures) failed")
        exit(failures == 0 ? 0 : 1)
    }
}
'''
if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift parts", not args.require_swift)
else:
    rules = add[:add.index("// MARK: - The screen")]
    with tempfile.TemporaryDirectory(prefix="bcd-add-game-") as tmp:
        t = Path(tmp)
        (t / "Rules.swift").write_text(rules)
        (t / "Main.swift").write_text(TEST)
        r = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(t / "rules"),
                            str(t / "Rules.swift"), str(t / "Main.swift")], capture_output=True, text=True)
        check("the rules of Add a game compile on their own", r.returncode == 0, (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            r = subprocess.run([str(t / "rules")], capture_output=True, text=True, timeout=60)
            print("\n".join("     " + line for line in r.stdout.strip().split("\n")))
            check("the rules of Add a game hold", r.returncode == 0, r.stderr[-2000:])
    sort_start = library.index("/// The classic library's Sort by choices")
    sort_end = library.index("enum BCDLibraryFilter")
    with tempfile.TemporaryDirectory(prefix="bcd-sort-") as tmp:
        t = Path(tmp)
        (t / "BCDStore.swift").write_text((APP / "BCDStore.swift").read_text())
        (t / "Sort.swift").write_text("import SwiftUI\n\n" + library[sort_start:sort_end])
        (t / "Main.swift").write_text((Path(__file__).resolve().parent / "bcd_library_sort_checks.swift").read_text())
        r = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(t / "sort"),
                            str(t / "BCDStore.swift"), str(t / "Sort.swift"), str(t / "Main.swift")], capture_output=True, text=True)
        check("the library's sort rules compile on their own with BCDStore.swift", r.returncode == 0, (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            r = subprocess.run([str(t / "sort"), "-AppleLocale", "en_US", "-AppleLanguages", "(en)"], capture_output=True, text=True, timeout=60)
            print("\n".join("     " + line for line in r.stdout.strip().split("\n")))
            check("the library's sort rules hold", r.returncode == 0, r.stderr[-2000:])
    if args.no_preview:
        print("SKIP the preview build (--no-preview)")
    else:
        with tempfile.TemporaryDirectory(prefix="bcd-library-preview-") as tmp:
            r = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp],
                               capture_output=True, text=True, timeout=1800)
            check("the preview build compiles (tools/ui-preview.py --build-only)", r.returncode == 0, (r.stdout + r.stderr)[-3000:])

CI = R / ".github/workflows/build-ipa.yml"
if CI.exists():
    check("CI runs this check", "run: python3 tests/host/check-bcd-library.py --require-swift" in CI.read_text())

print("PASS: BCD library and Add a game" if ok else "FAIL: BCD library and Add a game")
sys.exit(0 if ok else 1)
