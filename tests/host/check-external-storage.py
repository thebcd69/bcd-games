#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
# Adapted by BCD (build 635) from upstream Madeira's tests/host/check-external-storage.py: BCD's Xcode ids, BCD's source rules (part 1)
# and BCD's own cases (the earlier External SSD folder, its games' move) beside Madeira's native harness.
# Copyright 2026 thebcd69 (BCD) for the adaptation.
# Portions copyright 2026 intraducine and David Brookes: code taken from https://github.com/willfaust/Madeira (pull request #464)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""External drives (Settings › Storage › External drives; ExternalDrives.swift and ExternalStorage.swift, from Madeira #464;
docs/EXTERNAL-STORAGE.md).

Part 1 reads the sources (any host):
  - Both files are in the Xcode project once; the bookmarks are full ones (no .minimalBookmark anywhere: a minimal bookmark is
    what iOS refused with -2001 after a replug in builds 600 to 634), and they resolve with the default options.
  - Each saved drive is opened and linked at the app's start and again right before the wineserver starts (Windows lists
    its drive letters then).
  - BCD's earlier single-folder class is gone; the folder it saved becomes a drive once (migrateLegacyFolder), which removes
    only BCD's own link and moves the library's games only then.
  - The library, the program browser, BCD Dock's Steam list, Steam downloads, uninstall, the game page and Settings use the
    drives as Madeira does; a GOG, Epic, itch.io or EA game is never moved.
Part 2 (macOS with swift; --require-swift makes it mandatory, as CI does) compiles production code against temporary folders
under AddressSanitizer: Madeira's guards (registrations, mappings, identity markers, paths, libraryfolders.vdf, install
targets, uninstall), BCD's legacy decision (legacyPlan) and the library's move of the earlier folder's games (rebaseExternal).
Host checks do not prove a physical drive: the device list in docs/EXTERNAL-STORAGE.md does.
Run: python3 tests/host/check-external-storage.py [--require-swift]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
app = root / "app/Madeira"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()

failed = []


def check(what, ok):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failed.append(what)


def read(path):
    return (root / path).read_text(encoding="utf-8")


def body(src, start, end="\n    }\n"):
    """The text from `start` to the first `end` after it (a function's body at one indentation)."""
    i = src.index(start)
    return src[i:src.index(end, i) + len(end)]


def slice_(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


drives = read("app/Madeira/ExternalDrives.swift")
storage = read("app/Madeira/ExternalStorage.swift")
project = read("app/Madeira.xcodeproj/project.pbxproj")
library = read("app/Madeira/Library.swift")

# ---------------------------------------------------------------- part 1: the sources

for object_id in ["A1E46401", "A2E46401", "A1E46402", "A2E46402"]:
    check(f"one Xcode definition for {object_id}",
          len(re.findall(r"^\s*" + object_id + r" /\*.*?\*/ = ", project, re.M)) == 1)
check("both files are in the app target's sources",
      "A1E46401 /* ExternalDrives.swift in Sources */,\n" in project and "A1E46402 /* ExternalStorage.swift in Sources */,\n" in project)
check("ExternalStorage.swift keeps Madeira's header (David Brookes)",
      storage.startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright 2026 David Brookes\n"))
check("ExternalDrives.swift credits #464's authors and #501's",
      drives.startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright 2026 Connor Gow, intraducine, David Brookes, Jelly-RayTian\n"))

sources = {p: p.read_text(encoding="utf-8") for p in app.rglob("*.swift")}
check("no minimal bookmark anywhere in the app (iOS refused them with -2001 after a replug)",
      not any(".minimalBookmark" in text for text in sources.values()))
check("bookmarks are made with the default options", drives.count("url.bookmarkData()") >= 2 and "bookmarkData(options:" not in drives)
check("bookmarks resolve with the default options (no .withoutUI)",
      "URL(resolvingBookmarkData: folder.bookmark, bookmarkDataIsStale: &stale)" in drives and ".withoutUI" not in drives)
check("the earlier single-folder class is gone", not any("ExternalGameStorage" in text for text in sources.values()))

main_app = read("app/Madeira/MadeiraApp.swift")
check("the app opens the saved drives when it appears", main_app.count("ExternalDrives.shared.mountAll()") == 1)
content = read("app/Madeira/ContentView.swift")
sequence = content[content.index("    private func runWineFullSequence("):content.index("    private func startWineserver() {")]
check("the drives are linked on the main thread before the launch sequence goes to the background and starts the wineserver",
      re.search(r"ExternalDrives\.shared\.mountAll\(\)\n\s+DispatchQueue\.global\(qos: \.userInitiated\)\.async \{", sequence) is not None
      and sequence.index("ExternalDrives.shared.mountAll()") < sequence.index("self.startWineserver()")
      and "mountAll" not in body(content, "    private func startWineserver() {"))
mount = body(drives, "    @MainActor func mountAll() {")
check("mountAll does nothing while a game runs", "guard loadFailure == nil, wine_process_is_running() == 0 else { return }" in mount)
check("mountAll first takes the earlier folder over (until it opens)", mount.index("migrateLegacyFolder()") < mount.index("var next = folders"))
check("a drive that does not open is unlinked (only its links)", "unlink(folder)" in mount
      and "if (try? FileManager.default.destinationOfSymbolicLink(atPath: path)) != nil { try? FileManager.default.removeItem(atPath: path) }" in drives)

migrate = body(drives, "    @MainActor private func migrateLegacyFolder() {")
check("the migration reads the earlier key and forgets it once the drive is registered (or was added by hand)",
      'private static let legacyKey = "bcd.external-games.bookmark"' in drives
      and 'for key in [Self.legacyKey, Self.legacyKey + ".path", Self.legacyNameKey, Self.legacyMovesKey] { defaults.removeObject(forKey: key) }' in migrate
      and migrate.index("_ = try register(url, bookmark: try url.bookmarkData(), owned: false, name: name)") < migrate.rindex("forget()")
      and "if folders.contains(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame }) { forget(); return }" in migrate)
check("the migration is written down before anything changes, and every later step can run again (an interrupted start resumes)",
      migrate.index("defaults.set(plan.moveGames ? LibraryModel.shared.legacyExternalEntries() : [:], forKey: Self.legacyMovesKey)")
      < migrate.index("defaults.set(name, forKey: Self.legacyNameKey)") < migrate.index("try fm.removeItem(at: link)")
      < migrate.index("LibraryModel.shared.rebaseExternal(moves, to: name)")
      and "if let savedPath, let destination = try? fm.destinationOfSymbolicLink(atPath: link.path), destination == savedPath {" in migrate
      and "guard let moved = LibraryModel.shared.rebaseExternal(moves, to: name) else { return }\n            defaults.removeObject(forKey: Self.legacyMovesKey)" in migrate)
check("the migration removes only BCD's own link (it leads to the saved folder), never a folder",
      re.findall(r"removeItem\(at: (\w+)\)", migrate) == ["link"]
      and "if let savedPath, let destination = try? fm.destinationOfSymbolicLink(atPath: link.path), destination == savedPath {" in migrate)
check("the games move only when C:\\External was BCD's own link", "plan.moveGames ? LibraryModel.shared.legacyExternalEntries() : [:]" in migrate)
check("nothing else reads the earlier keys", not any("bcd.external-games" in text for path, text in sources.items() if path.name != "ExternalDrives.swift"))

owned = read("app/Madeira/SteamOwnedLibrary.swift")
mover_src = slice_(drives, "@MainActor final class GameMover", "extension URL {")
check("Library: relativeToDrive maps the opened drives, their own folder included (#536)",
      'if path == root.path { return "External/" + root.name }' in library and "for root in ExternalDrives.openedRoots {" in library)
check("Library: a program is started and added through relativeToDrive",
      "guard relativeToDrive(url) != nil, programExtensions.contains(url.pathExtension.lowercased())," in library
      and "guard let relative = relativeToDrive(url) else {" in library)
check("Play: a game on a drive is checked by what it needs (a Dock-started Steam game its install folder), the drive reopened once",
      "if !ExternalDrives.reachable(entry) { ExternalDrives.shared.mountAll() }\n            if !ExternalDrives.reachable(entry) {" in content
      and "if entry.steamAppID != nil, !entry.startsSteamGameDirectly {" in body(drives, "    static func reachable(_ entry: LibraryEntry) -> Bool {"))
check("a game whose path changes keeps its settings: a move and the earlier folder's games carry them before saving",
      mover_src.index("BCDGameRekey.carry(from: oldWindowsPath, to: entry.windowsPath)") < mover_src.index("LibraryModel.shared.save(entry)")
      and "for (old, new) in moved { BCDGameRekey.carry(from: old, to: new) }" in library)
rekey = read("app/Madeira/LibraryBCD.swift")
rekey = rekey[rekey.index("enum BCDGameRekey {"):]
owners = {
    '"madeira.recommended.applied"': ("app/Madeira/GameRecommendationsApply.swift", 'private static let appliedKey = "madeira.recommended.applied"'),
    '"bcd.lowGraphicsTip.shown"': ("app/Madeira/GameRecommendationsApply.swift", 'private static let shownKey = "bcd.lowGraphicsTip.shown"'),
    '"bcd.saves.lastSession"': ("app/Madeira/BCDSaveBackups/GameSaveWork.swift", 'forKey: "bcd.saves.lastSession"'),
    '"bcd.gyro"': ("app/Madeira/BCDGyroLive.swift", 'private static let key = "bcd.gyro"'),
}
for name in ["inDesktop", "avx", "wineVCRT", "nvidia", "safeSync", "screen"]:
    owners[f'"madeira.library.{name}"'] = ("app/Madeira/LibraryPrefs.swift", f'= "madeira.library.{name}"')
for literal, (path, needle) in owners.items():
    check(f"BCDGameRekey's key {literal} is still its owner's ({path.split('/')[-1]})", literal in rekey and needle in read(path))
check("BCDGameRekey moves the game's config file and Auto-tune's state, and overwrites nothing",
      "(GameProfile(windowsPath: old).url, GameProfile(windowsPath: new).url)" in rekey and "(AutoTune.stateURL(old), AutoTune.stateURL(new))" in rekey
      and "!fm.fileExists(atPath: to.path)" in rekey and "all[new] == nil" in rekey and "AutoTune.gamesKey" in rekey)
check("a move takes the game's Workshop items and record, its working folder and arguments",
      'for part in ["workshop/content/\\(appID)", "workshop/appworkshop_\\(appID).acf"] {' in mover_src
      and "entry.workingDirectory = to + folder.dropFirst(from.count)" in mover_src
      and "entry.arguments = entry.arguments.replacingOccurrences(of: from, with: to, options: .caseInsensitive)" in mover_src)
check("a removed drive's download places are forgotten; a stop while its drive is unplugged forgets its place",
      "SteamOwnedLibrary.shared.forgetTargets(onDrive: folder.name)" in drives and "} catch ExternalStorageError.unavailable {" in owned
      and "func forgetTargets(onDrive name: String) {" in owned and "|| !SteamWorkshopModel.shared.jobs.isEmpty" in owned)
check("a folder in or around BCD's Windows drive, and iOS's temporary copy, are refused",
      "guard chosen != local, !chosen.hasPrefix(local + \"/\"), !local.hasPrefix(chosen + \"/\") else {" in drives
      and '$0.hasSuffix("-Inbox")' in drives)
check("a drive without an identity file is reopened by name only on a read-only volume",
      "return readOnly && folder.name.caseInsensitiveCompare(name) == .orderedSame" in drives)
check("nothing is created while no drive was ever added", "guard !folders.isEmpty else { return }" in mount
      and mount.index("guard !folders.isEmpty else { return }") < mount.index("try? fm.createDirectory(at: Self.linkRoot"))
check("Library: a drive itself is never a game's installation (its size is not the drive's)",
      library.count("ExternalDrives.isDriveRoot(LibraryModel.relativeToDrive(") == 2)
add_game = read("app/Madeira/BCDAddGameLive.swift")
check("Add a game lists what relativeToDrive accepts, the drives by path",
      "private func inside(_ url: URL) -> Bool { LibraryModel.relativeToDrive(url) != nil }" in add_game
      and "FileManager.default.contentsOfDirectory(atPath: base.path)" in add_game)
dock = read("app/Madeira/MadeiraDock.swift")
check("BCD Dock's Steam list reads the drives' libraries", "let external = ExternalDrives.steamLibraries" in dock
      and 'guard isExternal || folder.resolvingSymlinksInPath().path.hasPrefix(drive.resolvingSymlinksInPath().path + "/"),' in dock)
check("the drives' libraries are listed for Valve's client", "ExternalSteamLibraries.add(paths: paths, to: existing)" in drives)
owned = read("app/Madeira/SteamOwnedLibrary.swift")
run = body(owned, "    private func run(_ appID: Int) async {")
check("a download goes to its own library and fails rather than fill the device when the drive is gone",
      "throw SteamDriveMissing(name: missing)" in run and "let steamApps = try ExternalDrives.nativeURL(library, write: true)" in run
      and "library: library, installed: true," in run)
check("a download keeps BCD's DLC and version steps", "await dlc.request(for: info, steamApps: steamApps)" in run
      and "fetcher.applyBranch(&info, branch: branchToDownload(appID)" in run)
check("install picks the paused, installed, chosen, then default library",
      "ExternalInstallTarget.select(existing: targets[appID], installed: installed, requested: library, fallback: Self.installLibrary)" in owned)
check("uninstall deletes through the drive's checked path and keeps the record when it fails",
      "try SteamInstallFiles.delete(appID: game.id, folderName: game.installDir, steamApps: apps)" in owned
      and owned.count('"The install could not be removed: "') == 1)
live_store = read("app/Madeira/BCDLiveStore.swift")
check("the game page's External drive install goes to the drive's SteamLibrary",
      "guard let target = ExternalDrives.shared.externalInstallTarget else {" in live_store
      and "download(appID, library: target.library)" in live_store and "download(appID, library: SteamInstallPaths.libraryRelative)" in live_store)
page = read("app/Madeira/BCDGamePage.swift")
check("only a Steam game offers the drive", "let drive = game.store == .steam ? system.externalDriveName : nil" in page)
page_live = read("app/Madeira/BCDGamePageLive.swift")
location = body(page_live, "    private func locationGroup(")
check("Move to is offered for Steam games and games from Files only (a store's records name its folder)",
      "guard game.store == .local || (steam && game.store == .steam) else { return nil }" in location)
check("Move to asks first", 'question: BCDGameQuestion(title: "Move \\(entry.title) to \\(target.name)?"' in location)
check("a move runs through GameMover",
      "GameMover.shared.move(entry, toSteamLibrary: steam ? target : nil, toRoot: steam ? nil : target)" in page_live)
mover = slice_(drives, "@MainActor final class GameMover", "extension URL {")
check("a move deletes the original only after the whole copy is checked",
      mover.index("// Check the whole copy before anything is deleted.") < mover.index("do { try fm.removeItem(at: item.from) }"))
check("a failed move removes only its copy", "for item in items { try? fm.removeItem(at: item.to) }" in mover)

# Settings.
sections = read("app/Madeira/BCDSettingsSections.swift")
group = slice_(sections, "    private var externalGames: some View {", "    private var games: [BCDGame] {")
check("the folder picker is attached to the External drives group",
      ".fileImporter(isPresented: $choosingExternalGames, allowedContentTypes: [.folder])" in group)
check("the picker asks for folders only", group.count("allowedContentTypes:") == 1)
check("the saves backup's importer is still alone on the page",
      sections.count(".fileImporter(isPresented: $choosingBackup, allowedContentTypes: [.zip])") == 1 and sections.count(".fileImporter(") == 2)
check("the group is shown only where the backend supports it", "if backend.supportsExternalGames { externalGames }" in sections)
check("the picker's open, answer and close are logged (a silent Open shows in the log)",
      'noteExternalPicker("opened")' in group and 'noteExternalPicker("answered")' in group and 'noteExternalPicker("closed")' in group)
check("Reconnect tries the saved bookmark before the picker", "backend.reconnectExternalDrive(drive.id) {" in group)
check("Remove asks first and says the drive's files stay", "The files on the drive are kept." in group)
code_lines = "\n".join(line for line in sections.splitlines() if not line.lstrip().startswith("//"))
check("the settings screen (also built in the preview) names no engine type",
      not re.search(r"\bExternalDrives\b|\bGameMover\b", code_lines))
live = read("app/Madeira/BCDSettingsLive.swift")
check("drive changes wait for a running game and a move; removing a drive also for downloads, cloud syncs and Workshop jobs",
      "wine_process_is_running() == 0 && LibraryModel.shared.current == nil && GameMover.shared.moving == nil" in live
      and "!SteamOwnedLibrary.shared.hasStorageWork else {" in body(drives, "    @MainActor func remove(_ id: UUID) {"))
check("the picker's in-place identity fix is still armed around the picker",
      "BCDExternalPickerFix.shared.beginSession()" in live and "BCDExternalPickerFix.shared.endSession()" in live)
check("picking a drive again opens its registration (its games keep their place)",
      "if let earlier = try reopen(url, bookmark: bookmark) {" in drives
      and "if let id = folder.libraryID { return (try? ExternalStorageIdentity.verify(id, root: url)) != nil }" in drives)

# Docs and CI.
doc = read("docs/EXTERNAL-STORAGE.md")
check("docs/EXTERNAL-STORAGE.md credits Madeira #464 and the earlier PR",
      "#464" in doc and "Pardoxum" in doc and "thebcd69/bcd-games#12" in doc)
ci = read(".github/workflows/build-ipa.yml")
check("CI runs this check", "run: python3 tests/host/check-external-storage.py --require-swift" in ci)

# ---------------------------------------------------------------- part 2: production code against temporary folders

native_main = r'''
var failures = 0
func check(_ ok: Bool, _ label: String) {
    print((ok ? "PASS: " : "FAIL: ") + label)
    if !ok { failures += 1 }
}
func rejects(_ label: String, _ body: () throws -> Void) {
    do { try body(); check(false, label) } catch { check(true, label) }
}
let fm = FileManager.default
let tmp = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try fm.createDirectory(at: tmp, withIntermediateDirectories: true)
defer { try? fm.removeItem(at: tmp) }
let root = tmp.appendingPathComponent("ssd")
let other = tmp.appendingPathComponent("other")
let registration = ExternalFolder(name: "SSD", letter: "e", bookmark: Data([1]))
try RegistrationFixture.validate([registration])
var second = ExternalFolder(name: "Other", letter: "f", bookmark: Data([2]))
try RegistrationFixture.validate([registration, second])
check(true, "valid legacy registrations remain readable without a marker")
second.name = "ssd"
rejects("duplicate folder names are rejected") { try RegistrationFixture.validate([registration, second]) }
second.name = "Other"; second.letter = "e"
rejects("duplicate drive letters are rejected") { try RegistrationFixture.validate([registration, second]) }
second.letter = "c"
rejects("the internal drive letter cannot be registered") { try RegistrationFixture.validate([second]) }
second.letter = "f"; second.name = "../outside"
rejects("a registration name cannot escape its mapping") { try RegistrationFixture.validate([second]) }
second.name = "Other"; second.libraryID = "bad-id"
rejects("a malformed folder identity is rejected") { try RegistrationFixture.validate([second]) }
second.libraryID = nil; second.bookmark = Data()
rejects("an empty bookmark is rejected") { try RegistrationFixture.validate([second]) }
try fm.createDirectory(at: root, withIntermediateDirectories: true)
try fm.createDirectory(at: other, withIntermediateDirectories: true)
let link = tmp.appendingPathComponent("e:")
try fm.createDirectory(at: link, withIntermediateDirectories: true)
let keep = link.appendingPathComponent("keep.txt")
try Data("keep".utf8).write(to: keep)
rejects("a real mapping directory is kept") { try ExternalStorageMapping.replace(link, root: root, owned: true) }
check(fm.fileExists(atPath: keep.path), "mapping collision preserves directory contents")
try fm.removeItem(at: link)
try Data("keep".utf8).write(to: link)
rejects("a real mapping file is kept") { try ExternalStorageMapping.replace(link, root: root, owned: true) }
check(try String(contentsOf: link, encoding: .utf8) == "keep", "mapping collision preserves file bytes")
try fm.removeItem(at: link)
try fm.createSymbolicLink(at: link, withDestinationURL: other)
rejects("new registration cannot replace a foreign link") { try ExternalStorageMapping.replace(link, root: root, owned: false) }
check(try fm.destinationOfSymbolicLink(atPath: link.path) == other.path, "foreign link target is unchanged")
try ExternalStorageMapping.replace(link, root: root, owned: true)
check(try fm.destinationOfSymbolicLink(atPath: link.path) == root.path, "an owned mapping can be refreshed")
try fm.removeItem(at: link)
try fm.createSymbolicLink(at: link, withDestinationURL: tmp.appendingPathComponent("missing"))
rejects("new registration cannot replace a dangling link") { try ExternalStorageMapping.replace(link, root: root, owned: false) }

let id = try ExternalStorageIdentity.register(root: root)
try ExternalStorageIdentity.verify(id, root: root)
check(try ExternalStorageIdentity.register(root: root) == id, "repeat registration keeps the folder identity")
rejects("another identity cannot use the folder") { try ExternalStorageIdentity.verify(UUID().uuidString, root: root) }
let marker = root.appendingPathComponent(ExternalStorageIdentity.marker)
try Data("invalid".utf8).write(to: marker)
rejects("a bad marker is not overwritten") { _ = try ExternalStorageIdentity.register(root: root) }
check(try String(contentsOf: marker, encoding: .utf8) == "invalid", "bad marker bytes are kept")
try fm.removeItem(at: marker)
rejects("a missing marker does not prove the drive identity") { try ExternalStorageIdentity.verify(id, root: root) }
try fm.createSymbolicLink(at: marker, withDestinationURL: other.appendingPathComponent("missing"))
rejects("a dangling marker is rejected") { _ = try ExternalStorageIdentity.register(root: root) }

for path in ["../outside", "/absolute", "a/../../outside", "a//b", "C:/other", "a/./b", "a\u{0}b"] {
    rejects("unsafe path rejected: \(path.debugDescription)") { _ = try ExternalStoragePath.native(path, root: root) }
}
try fm.createSymbolicLink(at: root.appendingPathComponent("escape"), withDestinationURL: other)
rejects("a nested link cannot reach an outside file") { _ = try ExternalStoragePath.native("escape/new/save.dat", root: root) }
try fm.createSymbolicLink(at: root.appendingPathComponent("dangling"), withDestinationURL: other.appendingPathComponent("missing"))
rejects("a dangling ancestor cannot create an outside file") { _ = try ExternalStoragePath.native("dangling/new/save.dat", root: root) }
check(try ExternalStoragePath.native("SteamLibrary/steamapps", root: root).path == root.appendingPathComponent("SteamLibrary/steamapps").path,
      "a normal missing destination can be created")

let old = Data(#"""
"LibraryFolders" {
    "TimeNextStatsReport" "123"
    "0" { "path" "C:\\Steam" "label" "A \"quoted\" label" "apps" { "9000" "42" } }
    "2" "D:\\OldLibrary"
}
"OtherMetadata" "keep"
"""#.utf8)
let paths = ["External/SSD/SteamLibrary", "external/ssd/steamlibrary"]
let updated = try ExternalSteamLibraries.add(paths: paths, to: old)
var parser = try SteamKeyValues(updated)
let value = try parser.read()
let folders = value["libraryfolders"]!
check(folders.fields.count == 4, "one external library is added, without a case duplicate")
check(folders["0"]?["apps"]?["9000"]?.string == "42" && folders["0"]?["label"]?.string == "A \"quoted\" label",
      "library update keeps app data and escaped labels")
check(folders["2"]?.string == "D:\\OldLibrary" && value["othermetadata"]?.string == "keep",
      "legacy paths and unrelated metadata are kept")
check(try ExternalSteamLibraries.add(paths: paths, to: updated) == updated, "repeat updates do not duplicate paths")
for bad in ["\"libraryfolders\" {", "\"libraryfolders\" \"bad\"", "\"unrelated\" \"x\""] {
    rejects("invalid library metadata is not replaced") { _ = try ExternalSteamLibraries.add(paths: paths, to: Data(bad.utf8)) }
}
check(ExternalInstallTarget.select(existing: "SSD-A", installed: "SSD-B", requested: "SSD-C", fallback: "internal") == "SSD-A",
      "a paused install keeps its first destination")
check(ExternalInstallTarget.select(existing: nil, installed: "SSD-B", requested: "SSD-C", fallback: "internal") == "SSD-B",
      "a repair keeps the installed game's destination")
check(ExternalInstallTarget.select(existing: nil, installed: nil, requested: "SSD-C", fallback: "internal") == "SSD-C",
      "a new install uses the selected destination")
check(ExternalInstallTarget.select(existing: nil, installed: nil, requested: nil, fallback: "offline-SSD") == "offline-SSD",
      "a disconnected default does not fall back to internal storage")
check(SteamInstallPaths.isManaged(library: "External/SSD/SteamLibrary/steamapps") && SteamInstallPaths.isManaged(library: SteamInstallPaths.libraryRelative)
      && !SteamInstallPaths.isManaged(library: "External/../steamapps") && !SteamInstallPaths.isManaged(library: "Games/SteamLibrary/steamapps"),
      "BCD manages its own library and the drives' steamapps folders only")

let apps = tmp.appendingPathComponent("steamapps")
try fm.createDirectory(at: apps.appendingPathComponent("common/Game"), withIntermediateDirectories: true)
let manifest = apps.appendingPathComponent("appmanifest_9000.acf")
try Data("record".utf8).write(to: manifest)
try fm.createSymbolicLink(at: apps.appendingPathComponent("downloading"), withDestinationURL: other)
rejects("uninstall refuses a linked journal directory") { try SteamInstallFiles.delete(appID: 9000, folderName: "Game", steamApps: apps) }
check(fm.fileExists(atPath: manifest.path) && fm.fileExists(atPath: apps.appendingPathComponent("common/Game").path),
      "failed uninstall keeps the install record and payload")
try fm.removeItem(at: apps.appendingPathComponent("downloading"))
try SteamInstallFiles.delete(appID: 9000, folderName: "Game", steamApps: apps)
check(!fm.fileExists(atPath: manifest.path) && !fm.fileExists(atPath: apps.appendingPathComponent("common/Game").path),
      "successful uninstall removes payload and record")
rejects("uninstall rejects an unavailable library") { try SteamInstallFiles.delete(appID: 9000, folderName: "Game", steamApps: tmp.appendingPathComponent("offline")) }
rejects("uninstall rejects a folder traversal") { try SteamInstallFiles.delete(appID: 9000, folderName: "../other", steamApps: apps) }

// BCD: the earlier External SSD folder (migrateLegacyFolder's decision).
let ours = LegacyFixture.legacyPlan(linkDestination: "/private/var/mobile/SSD/Games", savedPath: "/private/var/mobile/SSD/Games")
check(ours.removeLink && ours.moveGames && ours.register, "BCD's own link goes, its games move, the folder is registered")
let none = LegacyFixture.legacyPlan(linkDestination: nil, savedPath: "/private/var/mobile/SSD/Games")
check(!none.removeLink && !none.moveGames && none.register, "no link (nothing, or the player's real C:\\External): nothing on this device changes")
let foreign = LegacyFixture.legacyPlan(linkDestination: "/somewhere/else", savedPath: "/private/var/mobile/SSD/Games")
check(!foreign.removeLink && !foreign.moveGames && !foreign.register, "a link BCD did not make is left alone")
let unsaved = LegacyFixture.legacyPlan(linkDestination: "/private/var/mobile/SSD/Games", savedPath: nil)
check(!unsaved.removeLink && !unsaved.moveGames, "without the saved path no link counts as BCD's")
check(ExternalFolderName.folderName(URL(fileURLWithPath: "/x/My: SSD*?")) == "My SSD", "a folder's name loses the characters Windows refuses")

// BCD: the library's games of the earlier folder (LibraryModel.legacyExternalEntries, rebaseExternal).
let library = LibraryFixture()
library.entries = [
    LibraryEntry(relativePath: "External/Game/game.exe", workingDirectory: "C:\\External\\Game\\bin"),
    LibraryEntry(relativePath: "external/Other/other.exe"),
    LibraryEntry(relativePath: "Games/Local/local.exe", workingDirectory: "C:\\Games\\Local"),
    LibraryEntry(relativePath: "windows/system32/explorer.exe", desktop: true),
    LibraryEntry(relativePath: "External/Game/Game/inner.exe"),
]
let moves = library.legacyExternalEntries()
check(moves.count == 3, "the earlier folder's three games are listed before anything changes")
check(library.rebaseExternal(moves, to: "Game") == 3, "three games of the earlier folder move")
check(library.entries[0].relativePath == "External/Game/Game/game.exe" && library.entries[0].workingDirectory == "C:\\External\\Game\\Game\\bin",
      "a game and its working folder move under the drive's name")
check(library.entries[1].relativePath == "External/Game/Other/other.exe", "a path spelled in another case moves too")
check(library.entries[4].relativePath == "External/Game/Game/Game/inner.exe", "a game in a folder named like the drive moves once, like the others")
check(library.entries[2].relativePath == "Games/Local/local.exe" && library.entries[2].workingDirectory == "C:\\Games\\Local",
      "a game on this device is left alone")
check(library.entries[3].relativePath == "windows/system32/explorer.exe", "the desktop entry is left alone")
check(BCDGameRekey.carried.count == 3 && BCDGameRekey.carried[0] == ("C:\\External\\Game\\game.exe", "C:\\External\\Game\\Game\\game.exe"),
      "each moved game's settings follow it, from its old Windows path to its new one")
check(library.rebaseExternal(moves, to: "Game") == 0 && library.entries[0].relativePath == "External/Game/Game/game.exe"
      && library.entries[4].relativePath == "External/Game/Game/Game/inner.exe", "a second run (an interrupted start) moves nothing twice")
library.readOnly = true
check(library.rebaseExternal(library.legacyExternalEntries(), to: "Again") == nil, "a library that cannot be written reports it (the moves are tried again)")
exit(failures == 0 ? 0 : 1)
'''

folder = slice_(drives, "struct ExternalFolder:", "final class ExternalDrives:")
validate = slice_(drives, "    private static func validate(", "\n    private func save(").replace("private static func", "static func", 1)
legacy = slice_(drives, "    struct LegacyPlan: Equatable {", "\n    @MainActor private func migrateLegacyFolder() {")
name = slice_(drives, "    static func folderName(_ url: URL) -> String {", "\n    private func uniqueName(")
rebase = slice_(library, "    func legacyExternalEntries() -> [String: String] {", "\n    @discardableResult\n    private func persist(")
check("the harness found the production pieces", all(len(x) > 50 for x in [folder, validate, legacy, name, rebase]))
fixtures = (
    "import Foundation\n" + folder
    + "\nenum RegistrationFixture {\n" + validate + "\n}\n"
    + "enum LegacyFixture {\n" + legacy + "\n}\n"
    + "enum ExternalFolderName {\n" + name + "\n}\n"
    + "struct LibraryEntry { var id = UUID(); var relativePath: String; var workingDirectory: String? = nil; var desktop: Bool? = nil\n"
    + "    var windowsPath: String { \"C:\\\\\" + relativePath.replacingOccurrences(of: \"/\", with: \"\\\\\") } }\n"
    + "enum BCDGameRekey { static var carried: [(String, String)] = []; static func carry(from old: String, to new: String) { carried.append((old, new)) } }\n"
    + "final class LibraryFixture {\n    var entries: [LibraryEntry] = []\n    var readOnly = false\n"
    + "    @discardableResult func persist(_ next: [LibraryEntry]) -> Bool { guard !readOnly else { return false }; entries = next; return true }\n"
    + rebase + "\n}\n"
)

swiftc = os.environ.get("SWIFTC") or shutil.which("swiftc")
if sys.platform != "darwin" or not swiftc:
    check("macOS with swift is here for the native part", not args.require_swift)
else:
    with tempfile.TemporaryDirectory(prefix="bcd-external-") as tmp:
        d = Path(tmp)
        (d / "main.swift").write_text(fixtures + native_main)
        compiled = subprocess.run([swiftc, "-swift-version", "5", "-sanitize=address", str(d / "main.swift"),
                                   *[str(app / n) for n in ["ExternalStorage.swift", "SteamInstall.swift", "SteamKeyValues.swift"]],
                                   "-o", str(d / "check")], capture_output=True, text=True)
        check("the production pieces compile on the host", compiled.returncode == 0)
        if compiled.returncode != 0:
            print(compiled.stderr[-4000:])
        else:
            ran = subprocess.run([str(d / "check")], capture_output=True, text=True,
                                 env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0"), timeout=300)
            print(ran.stdout, end="")
            check("every native case passes", ran.returncode == 0)
            if ran.returncode != 0:
                print(ran.stderr[-4000:])

if failed:
    print(f"\n{len(failed)} check(s) failed")
    sys.exit(1)
print("\ncheck-external-storage: all checks passed")
