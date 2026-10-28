#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's game page (app/Madeira/BCDGamePage.swift; docs/BCD-UI-ARCHITECTURE.md).

Checks:
  - BCD's licence header on the page, its live backend (BCDGamePageLive.swift) and its sample
    (app/BCDPreview/BCDGamePageSample.swift);
  - the page and the sample name no engine type; the live backend is in tools/ui-preview.py's
    ENGINE_BOUND; the Xcode project builds the page and the live backend under the page's IDs
    (A1000B40-A1000B5F, A2000B40-A2000B5F);
  - Home and Library open the page full screen (.bcdGamePage), and the first-version sheet is gone;
  - the live backend offers every per-game function of the old Game details page through the
    engine's own models and files, never one of that page's views (they go in the clean-up wave):
    display, Direct3D, compatibility, controls and controller binds, launch, Steam, library (Find on
    Steam, a cover picture), cloud saves with the saves that differ, recommended settings (and the
    online list), auto-tune, the game's config, shortcut, saves, report, uninstall, and the Wine
    desktop's page; frame generation only from iOS 26; the page closes for the engine's notices;
    nothing opens the old page any more; the Wine Mono note after a start that needed it;
  - the page's rules, compiled on this Mac with BCDStore.swift and run: the main action, the
    actions beside it, the progress and the lines about what stands in the way for every state;
    the facts; a setting's summary; the questions before what deletes, replaces or sends (a
    choice that removes something included), and none before the rest; Play, Install, Repair,
    JIT, sign-in and Uninstall close the page before BCD's live store shows them; Get the latest
    recommended settings (up to date, offline, changes offered and applied or left); the
    settings are built only while Options is open; an unanswered or refused question, an
    unchanged value or a locked setting runs nothing;
  - the controller: the page opens and closes its focus layer and sets A, X, Y and B; Options
    and the question have layers of their own; the page's controls carry their focus ids;
  - memory and cool running: the hero art decoded at its drawn width times the display scale; no
    animation that runs by itself (repeatForever, a spinner, a timeline or a timer), animations of
    0.25 s or less, no blur or material, no AsyncImage, art only through BCDArt;
  - no "Madeira" in the page's text;
  - the preview build compiles (tools/ui-preview.py --build-only);
  - CI runs this check.
The Swift parts need xcrun swiftc; --require-swift makes their absence a failure. --no-preview
leaves the preview build out (a quick run while working on the page; CI builds it).
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
PAGE = APP / "BCDGamePage.swift"
LIVE = APP / "BCDGamePageLive.swift"
SAMPLE = R / "app/BCDPreview/BCDGamePageSample.swift"
HEADER = ["// SPDX-License-Identifier: GPL-3.0-only",
          "// Copyright 2026 thebcd69 (BCD)",
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md."]
# The engine as screens must not name it (docs/BCD-UI-ARCHITECTURE.md, "Layers").
ENGINE = ["LibraryModel", "LibraryEntry", "LibraryDetail", "LibraryController", "LibraryPrefs", "SteamOwnedLibrary",
          "SteamGamesModel", "SteamGamesRules", "SteamDirectStart", "SteamSignIn", "SteamSignInModel", "SteamInstallPaths",
          "SteamCloudState", "MadeiraDock", "MadeiraDockModel", "DockGame", "DockInstallers", "DockOffline", "MadeiraConfig",
          "GameProfile", "GameRecommendations", "GameRecommendation", "AutoTune", "AutoTuneGameSection", "LogUpload",
          "LogStore", "SaveBackup", "ShortcutRouter", "ResolutionChoices", "DisplayMode", "ProMotionIntent", "JITCoordinator",
          "JITSetup", "StikJITHelper", "WineProcessBridge", "GameConfigEditor", "GamepadInput", "TouchControlsModel",
          "ContentView", "LaunchFlagChips", "SyncEngine", "BCDGamePageLive", "BCDLiveStore", "SteamCatalog", "PadBindings",
          "ControlAction", "RecommendationsSource", "BCDLibraryImport"]
# The old Game details page and its sub-pages, which the clean-up wave deletes: the live backend
# names none of them (not even their static helpers), so nothing of that interface stays.
OLD_PAGE = ["LibraryDetail", "LibraryGameConfigEditor", "GameConfigEditor", "SteamSearchView", "ControllerBindsPage",
            "SteamCloudSection", "SteamEntrySection", "SteamDownloadStatus", "AutoTuneGameSection", "AutoTuneHistoryView",
            "DockOfflineMark", "DockInstallRequestRows", "LaunchFlagChips", "FPSChoice", "ControllerModeChoice",
            "BCDGameValues", "ExecutableBrowser", "LibraryArtwork", "SteamGameArtwork", "LibraryBadges"]

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
    return "\n".join(re.split(r"(?<!:)//", line, maxsplit=1)[0] if not line.strip().startswith("///") else "" for line in text.split("\n"))


page, live, sample = PAGE.read_text(), LIVE.read_text(), SAMPLE.read_text()

# ---- files, layers, project --------------------------------------------------------------------
for f in (PAGE, LIVE, SAMPLE):
    check(f"{f.relative_to(R)} starts with BCD's licence header", f.read_text().split("\n")[:3] == HEADER)
for f, text in ((PAGE, page), (SAMPLE, sample)):
    # Words in quotes are text, not types (the page looks its live backend up by name).
    bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code(text))
    named = sorted({t for t in ENGINE if re.search(r"\b" + t + r"\b", bare)})
    check(f"{f.name} names no engine type", not named, named)
preview_tool = (R / "tools/ui-preview.py").read_text()
bound = re.search(r"^ENGINE_BOUND = \{(.*?)\}", preview_tool, re.M | re.S)
check("BCDGamePageLive.swift is in ui-preview's ENGINE_BOUND (the preview build leaves it out)",
      bound is not None and '"BCDGamePageLive.swift"' in bound.group(1))
check("the preview tool lists the game page among its screens", re.search(r'^SCREENS = \[.*"game".*\]', preview_tool, re.M))
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
for name in ("BCDGamePage.swift", "BCDGamePageLive.swift"):
    build = re.findall(r"(\w+) /\* " + re.escape(name) + r" in Sources \*/ = \{isa = PBXBuildFile; fileRef = (\w+) ", pbx)
    check(f"the Xcode project builds {name} (build file, file reference, group, Sources)",
          pbx.count(f"/* {name} in Sources */") == 2 and pbx.count(f"path = {name};") == 1
          and pbx.count(f"/* {name} */") == 3 and len(build) == 1)
    if build:
        b, f = build[0]
        check(f"{name} uses the page's project IDs", re.fullmatch(r"A1000B[45][0-9A-F]", b) and re.fullmatch(r"A2000B[45][0-9A-F]", f),
              f"{b} {f}")
home = (APP / "BCDHomeView.swift").read_text()
library = (APP / "BCDLibraryView.swift").read_text()
check("Home opens a game's page full screen", '.bcdGamePage($opened, from: "Home")' in home and ".sheet(item: $opened)" not in home)
check("Library opens a game's page full screen",
      '.bcdGamePage($opened, from: "Library")' in library and ".sheet(item: $opened)" not in library)
check("the first-version game sheet is gone", not any("BCDGameSheet" in p.read_text() for p in APP.glob("*.swift")))
check("the page opens as a full-screen cover with the store and the focus",
      "content.fullScreenCover(item: $game, onDismiss: BCDPresentation.didClose) { opened in" in page and ".environmentObject(store)" in page
      and ".environmentObject(focus)" in page)
check("BCDGameDetailSource finds the live backend by its Objective-C name",
      'NSClassFromString("BCDGamePageLive")' in page and "@objc(BCDGamePageLive)" in live
      and "BCDGameDetailSource.backend = BCDGamePageSample()" in (R / "app/BCDPreview/BCDPreviewApp.swift").read_text())
check("Play, Install and Update are exactly the store's actions",
      "case .play: store?.actions?.play(game)" in page and "case .install(let place): store?.actions?.install(game, place: place)" in page
      and "case .update: store?.actions?.update(game)" in page and page.count("actions?.play(") == 1)

# ---- the live backend: every classic setting, through the classic page's own functions ------------
for what, needle in [
    ("first sight gets the recommended settings", "if GameRecommendations.prepare(&entry, new: isNew) { changed = true }\n            if changed { LibraryModel.shared.save(entry) }"),
    ("Reset to Recommended is the engine's reset, saved", "change(game) { entry in _ = GameRecommendations.reset(&entry) }"),
    ("Get the latest recommended settings asks the online list", "let result = await RecommendationsSource.shared.refresh()"),
    ("... then compares the game's config with the newest list", "let differs = Self.changes(now, rec.fileText)"),
    ("... and applies it as Reset to Recommended does", "func applyRecommendedSettings(_ game: BCDGame) { resetToRecommended(game) }"),
    ("the list's version, day and last check are shown", "version: lists.version,"),
    ("settings save through the library", "LibraryModel.shared.save(entry)"),
    ("the game's own file is written only when a line changes", 'if (file.get(key) ?? "") != value { file.set(key, value.isEmpty ? nil : value) }'),
    ("Resolution offers this device's choices", "ResolutionChoices.groups()"),
    ("Scaling is the engine's display modes", "DisplayMode.allCases.map"),
    ("30 and 40 FPS only with the caps this device has", "if ProMotionIntent.has30Cap || entry.fpsMode == 3"),
    ("MetalFX upscaling", 'Self.line("metalfx-upscale", "MetalFX upscaling"'),
    ("Direct3D: tessellation, command encoding, GPU sync", 'Self.line("dxil-tess-max-factor"'),
    ("Direct3D 9 anisotropic filtering", "$0.anisotropyLimit = Int(value).flatMap"),
    ("an NVIDIA card through LibraryPrefs", "LibraryPrefs.setNvidia(on, for: $0.windowsPath)"),
    ("AVX through LibraryPrefs", "LibraryPrefs.setAVX(on, for: $0.windowsPath)"),
    ("Wine's C++ runtime through LibraryPrefs", "LibraryPrefs.setWineVCRT(on, for: $0.windowsPath)"),
    ("x87 precision, processors, fastsync and semaphores on the entry", 'case "semaphores": change(game) { $0.semaphoreFastPath = on }'),
    ("fastsync's switches locked outside Fastsync", "let fastsync = SyncEngine.current == .fastsync"),
    ("touch controls, their opacity and size, overlay and logs on the entry", '$0.controlSize = min(max(level, 0.5), 2)'),
    ("the controller API in the game's own file", "Self.line(GamepadInput.padModeKey"),
    ("the controller mode only where the engine offers it", "if GamepadInput.keyboardMouseAvailable {"),
    ("controller binds in keyboard-and-mouse mode", 'guard GamepadInput.keyboardMouseAvailable, entry.controllerMode == "keys" else { return nil }'),
    ("... each input's default is the engine's layout", "PadBindings.templateAction(input)"),
    ("... written to the entry's binds table", "entry.controllerBinds = table.isEmpty ? nil : table"),
    ("... the right stick's up and down speed", "$0.padMouseVertical = abs(speed - 1) < 0.01 ? nil : min(max(speed, 0.25), 1.5)"),
    ("... and every bind put back", "entry.controllerBinds = nil\n                entry.padMouseVertical = nil"),
    ("launch: on its own or in a Wine desktop, working folder, services", '$0.launchMode = value == "desktop" ? "desktop" : nil'),
    ("launch arguments and their flags (BCD's own rules)", "entry.arguments = BCDLaunchFlags.toggled(entry.arguments, word)"),
    ("what starts", "entry.commandPreview"),
    ("Steam: start with BCD Dock or the game's own program", '$0.steamStart = mode'),
    ("... the program from Steam's launch configuration", "SteamDirectStart.choose(options, installFolder: folder)"),
    ("... the smaller JIT pool", "MadeiraDockModel.shared.compactPool = on"),
    ("... one-time installs and their next-start request", "MadeiraDockModel.shared.setInstallRequest(id, DockInstallRequest(rawValue: value))"),
    ("... offline play and why", "DockOffline.mark(appID, preparedOnline: custom)"),
    ("... free space for updates and repairs", ".volumeAvailableCapacityForImportantUsage"),
    ("... the store page", 'd.storePage = URL(string: "https://store.steampowered.com/app/\\(id)/")'),
    ("library: the name and the store's pictures again", 'case "storeart": change(game) { $0.coverFile = nil }'),
    ("Find on Steam searches Steam's public store", "try await SteamCatalog.search(text)"),
    ("... and the match gives the game its store page, name and pictures", "entry.steamID = match.id\n            entry.title = match.name\n            entry.coverFile = nil"),
    ("a cover picture of one's own, where the library reads covers", 'LibraryModel.documents.appendingPathComponent("bcd-art", isDirectory: true)'),
    ("... at most 1200 pixels, never decoded whole", "kCGImageSourceThumbnailMaxPixelSize: 1200"),
    ("... and at most 20 MB", 'guard bytes <= 20_000_000 else { throw BCDGameError("Choose a picture under 20 MB.") }'),
    ("the game's config is its own file, edited as text", "if profile.text != text { profile.text = text }"),
    ("the Wine desktop has its page, before its first start too", "(id == LibraryEntry.desktopID ? LibraryEntry.desktopEntry : nil)"),
    ("... with the desktop's settings and none of a game's library rows", "if !desktop { d.groups.append(libraryGroup(entry, gog: game.store == .gog || game.store == .epic)) }"),
    ("an installed GOG game's settings are its library entry's", "guard BCDGOG.enabled, let id = BCDGOG.shared.installs[product]?.entryID else { return nil }"),
    ("... with GOG's note on what it may need (and Epic's)", "d.storeNote = BCDLiveStore.shared.gogNote(game.id) ?? BCDLiveStore.shared.epicNote(game.id)"),
    ("... and Uninstall, which deletes its files through the store", "} else if game.store == .gog || game.store == .epic {\n            // Uninstall deletes a GOG or Epic game's files through the store (BCDLiveStoreGOG, BCDLiveStoreEpic).\n            d.removal = .uninstall(frees: entry.folderBytes ?? game.size)"),
    ("an installed Epic game's settings are its library entry's", "if let id = BCDLiveStore.shared.epicEntryID(game.id) { return library.entries.first { $0.id == id } }"),
    ("an Epic game's page looks at its manifest when it opens, and reads its details again when the look answers", "BCDLiveStore.shared.epicOpened(game.id)\n        guard game.state.isInstalled else { return }"),
    ("... through the one hook line in the watched changes", "BCDLiveStore.shared.epicDetailChanges,"),
    ("an Epic game BCD cannot install says why before Install, and one that may not start says so", "let advice = BCDLiveStore.shared.epicInstallAdvice(game.id)\n        d.installBlocker = advice.blocker\n        d.installWarning = advice.warning"),
    ("the program's graphics API is read once, off the main thread", "return LibraryModel.graphicsImports(program)"),
    ("Steam's blockers before Play are the engine's", "SteamGamesRules.blocker(installed: installed, client: MadeiraDock.clientInstalled"),
    ("Uninstall is offered only for BCD's own downloads", "SteamInstallPaths.isManaged(library: $0.library)"),
    ("Steam Cloud syncs through the engine", "await SteamOwnedLibrary.shared.syncCloud(id)"),
    ("... the saves that differ are listed", "d.cloudConflicts = state.conflicts.map"),
    ("a cloud conflict is settled by the engine", "await SteamOwnedLibrary.shared.resolveCloud(id, useCloud: fromCloud)"),
    ("auto-tune only for a game it can tune", "AutoTune.enabled && AutoTune.eligible(entry)"),
    ("auto-tune's switch only while it is on", "if AutoTune.enabled { AutoTune.setOn(path, on) }"),
    ("the Wine Mono note: after a start that needed it, while none is installed (read with auto-tune's state, so only while it is on)",
     "d.needsWineMono = state.monoMissing == true && !WineMonoModel.available"),
    ("auto-tune's found lines go into the game's file", "if parts.count == 2 { profile.set(parts[0], parts[1]) }"),
    ("auto-tune's report, read only while it is on", "guard let state = AutoTune.enabled ? AutoTune.state(path) : nil else { return nil }"),
    ("Add to Home Screen is the existing shortcut link", "ShortcutRouter.link(for: $0.windowsPath)"),
    ("saves back up through SaveBackup", "try SaveBackup.backup()"),
    ("saves restore through SaveBackup", "try SaveBackup.restore(from: file)"),
    ("Report a problem is Send Log, with the sender's comment", "LogUpload.send(log.file, game: LogUpload.game(of: log.file), comment: comment, progress: progress)"),
    ("only the newest logs Send Log offers are looked at", "LogUpload.recentLogs()"),
    ("the sections are built only while Options is open", "if settings {\n            d.groups = [display(entry, profile)"),
    ("nothing of a closed page stays in the backend", "programs.removeAll()"),
    ("upstream's own fields move into the fork's storage on opening", "var changed = moveOldFields(&entry)"),
]:
    check(f"live: {what}", needle in live, needle)
bare_live = re.sub(r'"(?:[^"\\]|\\.)*"', '""', code(live))
old_named = sorted({t for t in OLD_PAGE if re.search(r"\b" + t + r"\b", bare_live)})
check("live: nothing of the old Game details page is used (its views and their helpers)", not old_named, old_named)
check("nothing opens the old page any more: no classic page, no AnyView from the backend, no \"More (classic)\"",
      all("classicPage" not in t and "configEditor" not in t for t in (page, live, sample))
      and "AnyView" not in code(live) and "More (classic)" not in page)
fg = live.split("\n")
fg_lines = [i for i, l in enumerate(fg) if "env.MADEIRA_FRAMEGEN" in l or 'Self.toggle("framegen"' in l]
check("live: frame generation is offered and written only from iOS 26",
      fg_lines and all(any("if #available(iOS 26.0, *) {" in a for a in fg[max(0, i - 2):i]) for i in fg_lines),
      [fg[i].strip() for i in fg_lines])
display = live[live.index("private func display("):live.index("// MARK: Direct3D")]
check("live: Auto HDR follows frame generation in Display and is available before iOS 26",
      display.index('Self.toggle("framegen"') < display.index('Self.toggle("auto-hdr"')
      and '}\n        let autoHDRDefault' in display)
check("live: Auto HDR follows the global default and saves explicit per-game on/off",
      'profile.get("env.DXMT_AUTO_HDR").map { $0 == "1" } ?? autoHDRDefault' in display
      and 'setLine(game, "env.DXMT_AUTO_HDR", on ? "1" : "0")' in live)
check("preview: Auto HDR follows frame generation too",
      sample.index('toggle("framegen"') < sample.index('toggle("auto-hdr"'))
check("live: the page closes for the engine's notices, as the classic library closed Game details",
      all(f"library.${n}.dropFirst().map {{ $0 != nil }}" in live for n in ("current", "error", "restartNotice", "jitNotice", "cloudNotice")))

# ---- the controller -------------------------------------------------------------------------------
views = page[page.index("// MARK: - Opening the page"):]
appeared = views[views.index("private func appeared() {"):]
appeared = appeared[:appeared.index("\n    }\n")]
check("the page opens its focus layer on appearing and closes it on leaving",
      "focus.openLayer(Self.layer)" in appeared and ".onDisappear {\n            model.leave()\n            focus.closeLayer(Self.layer)" in views)
buttons = views[views.index("private func installButtons() {"):]
buttons = buttons[:buttons.index("\n    }\n")]
for button, title in (("a", None), ("x", "Options"), ("y", "Update"), ("y", "Default"), ("b", "Back"), ("b", "Close")):
    check(f"the page sets {button.upper()}" + (f" ({title})" if title else ""),
          f"BCDHint(button: .{button}, title: " + (f'"{title}"' if title else "") in buttons)
check("Options has a layer of its own, closed by B, and reads the settings only while open",
      "focus.openLayer(Self.panelLayer)" in views and "focus.closeLayer(Self.panelLayer)" in views
      and "model.showSettings(true)" in views and "model.showSettings(false)" in views)
check("a question has a layer of its own; B cancels it, and the focus starts on Cancel when it deletes something",
      "focus.openLayer(Self.askLayer)" in views and "focus.closeLayer(Self.askLayer)" in views
      and '(BCDHint(button: .b, title: "Cancel"), { model.answer(false) })' in buttons
      and 'destructive == true ? "game.ask.no" : "game.ask.yes"' in views)
for focus_id in ('"game.main"', '"game.back"', '"game.options"', '"game.other.\\(index)"', '"game.fix.\\(index)"',
                 '"game.panel.close"', '"game.panel.back"', '"game.panel.row." + id', '"game.panel.choice.\\(index)"',
                 '"game.panel.save"', '"game.panel.cancel"', 'prefix + "yes"', 'prefix + "no"', '"game.panel.search"',
                 '"game.panel.row.match.\\(match.id)"', '"game.store"'):
    check(f"a control carries the focus id {focus_id}", focus_id in views)
check("the question's controls are \"game.ask.yes\" and \"game.ask.no\" on the page (the card menu's question gives its own prefix)",
      'var prefix = "game.ask."' in views and "BCDGameQuestionView(question: asked.question, answerAlternative: { model.answerAlternative() }) { model.answer($0) }" in views
      and 'prefix + "alt"' in views)
plain = code(views)
check("every button of the page can be reached with a controller",
      len(re.findall(r"\bButton\(", plain)) - len(re.findall(r"\.bcdFocusable\(", plain)) <= 2,
      "Button( %d, .bcdFocusable( %d" % (len(re.findall(r"\bButton\(", plain)), len(re.findall(r"\.bcdFocusable\(", plain))))
check("Find on Steam, the cover picture and Controller binds open BCD's own screens",
      "case .storeSearch: row.run = { show(.storeSearch, nil) }" in page and "case .coverPicture: row.run = { importFile(.cover) }" in page
      and "case .section(let group): row.run = { show(.group(group), nil) }" in page)
check("the game's config is edited on a page of Options (Save and Cancel)", "BCDGameConfigPage(game: game, model: model" in page
      and "model.request(.saveConfig(draft))" in page)
check("short choices (keys, percentages) are caps in a grid the controller can walk (no lazy grid)",
      "Grid(horizontalSpacing: 6, verticalSpacing: 6)" in page and "LazyVGrid" not in page)
check("Get the latest recommended settings sits with the recommended settings",
      'title: "Get the latest recommended settings"' in page and "run: { model.refreshRecommended() }" in page)

# ---- memory and cool running ------------------------------------------------------------------------
# BCDArtImage decodes for its own frame (points times the screen's scale; check-bcd-live pins that).
check("the hero art is decoded for the frame it fills, not at a fixed size",
      "BCDArtImage(urls: game.heroArt + game.headerArt + game.coverArt, shape: shape)" in page
      and "maxPixel" not in code(page) and "BCDGameArt(" not in code(page))
for f, text in ((PAGE, page), (SAMPLE, sample)):
    plain = code(text)
    for bad in ("repeatForever", "ProgressView(", "TimelineView", "Timer.", "AsyncImage", ".blur(", "Material", "CADisplayLink"):
        check(f"{f.name}: no {bad}", bad not in plain)
    long = [d for d in re.findall(r"duration: ([0-9.]+)", plain) if float(d) > 0.25]
    check(f"{f.name}: animations of 0.25 s or less", not long, long)
check("a store match's picture is decoded at the size drawn (its 92 x 35 frame)",
      "BCDArtImage(urls: match.picture.map { [$0] } ?? [], shape: 92.0 / 35.0)" in page
      and ".frame(width: 92, height: 35)" in page)
check("the page's art comes through BCDArt (BCDArtImage, BCDArtPlaceholder, BCDGameLogo)",
      "BCDArtImage(urls: game.heroArt + game.headerArt + game.coverArt" in page and "BCDGameLogo(game: game" in page
      and "Image(uiImage" not in code(page))

# ---- words ----------------------------------------------------------------------------------------
for f, text in ((PAGE, page), (LIVE, live), (SAMPLE, sample)):
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', code(text))
    shown = [s for s in literals if "madeira" in s.lower() and not s.startswith("env.MADEIRA_")]
    check(f"{f.name}: no \"Madeira\" in its text", not shown, shown)

# ---- dates ----------------------------------------------------------------------------------------------
# Every date text is made from numbers (BCDFormat in BCDStore.swift). An ICU date formatter, built while the interface draws,
# killed BCD itself in three tester logs (udat_open in BCDGamePageLive.day: KDY8E6, A7V9T2, DFSTWB); the first fix covered one
# helper only. No file of the app may build one: not the screens, not the engine's file names and log stamps.
ICU_DATE = re.compile(r"DateFormatter\(|\.formatted\(date:|\.formatted\(\)|ISO8601DateFormatter|RelativeDateTimeFormatter|DateIntervalFormatter"
                      r"|DateComponentsFormatter|NSDateFormatter|CFDateFormatter|\.dateFormat\b|\.dateStyle\b|\.timeStyle\b|Date\.FormatStyle"
                      r"|\.formatted\(\.(dateTime|iso8601|relative)|Text\([^\n]*style: *\.(date|time|relative|timer|offset)\b")
icu_hits = []
for f in sorted((R / "app").rglob("*.swift")):
    if "/licenses/" in f.as_posix():
        continue
    for number, line in enumerate(code(f.read_text()).split("\n"), 1):
        if ICU_DATE.search(line):
            icu_hits.append(f"{f.relative_to(R)}:{number}: {line.strip()[:100]}")
check("no file of the app builds an ICU date formatter (DateFormatter, Date.formatted, ISO8601DateFormatter ...)", not icu_hits, icu_hits[:8])
rules_text = (R / "build/tools/autotune_rules.py").read_text()
icu_rule = rules_text[rules_text.index('"id": "bcd-date-formatter-crash"'):]
icu_rule = icu_rule[:icu_rule.index("\n    },\n")]
check("auto-tune knows a log that shows BCD itself dying in udat_open (bcd-date-formatter-crash: no lines, fixedIn 580; tests/fixtures/autotune)",
      '"lines": []' in icu_rule and '"fixedIn": 580' in icu_rule and "udat_open" in icu_rule
      and (R / "tests/fixtures/autotune/synthetic-bcd-date-formatter-crash.expect").exists())
store_text = (APP / "BCDStore.swift").read_text()
check("BCDStore.swift keeps the date helpers between the markers the Steam and Dock reset host checks cut out",
      store_text.count("    // dates-begin") == 1 and store_text.count("    // dates-end") == 1
      and store_text.index("    // dates-begin") < store_text.index("static func fullDate") < store_text.index("    // dates-end"))

# ---- the page's rules, compiled and run -------------------------------------------------------------
TEST = (Path(__file__).resolve().parent / "bcd_game_page_checks.swift").read_text()

swiftc = shutil.which("xcrun")
if not swiftc:
    check("xcrun swiftc is here for the Swift parts", not args.require_swift)
else:
    start = page.index("// MARK: - The page's rules")
    end = page.index("// MARK: - Opening the page")
    with tempfile.TemporaryDirectory(prefix="bcd-game-page-") as tmp:
        t = Path(tmp)
        (t / "BCDStore.swift").write_text((APP / "BCDStore.swift").read_text())
        (t / "BCDGameDLC.swift").write_text((APP / "BCDGameDLC.swift").read_text())   # the question before a first install (the model asks it)
        (t / "Rules.swift").write_text("import Combine\nimport Foundation\nimport SwiftUI\n\n" + page[start:end])
        (t / "Main.swift").write_text(TEST)
        r = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(t / "rules"),
                            str(t / "BCDStore.swift"), str(t / "BCDGameDLC.swift"), str(t / "Rules.swift"), str(t / "Main.swift")],
                           capture_output=True, text=True)
        check("the page's rules compile on their own with BCDStore.swift", r.returncode == 0, (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            # The same words on every Mac: English, whatever this Mac's language.
            r = subprocess.run([str(t / "rules"), "-AppleLocale", "en_US", "-AppleLanguages", "(en)"],
                               capture_output=True, text=True, timeout=120)
            print("\n".join("     " + line for line in r.stdout.strip().split("\n")))
            check("the page's rules hold (states, questions, actions)", r.returncode == 0, r.stderr[-2000:])
            # The date helpers again in zones with other offsets and clock changes (the first run used this Mac's).
            for zone in ("Asia/Kolkata", "Asia/Kathmandu", "America/Los_Angeles", "Pacific/Auckland", "Australia/Lord_Howe", "Pacific/Kiritimati", "Pacific/Pago_Pago"):
                r = subprocess.run([str(t / "rules"), "-datesOnly"], capture_output=True, text=True, timeout=120,
                                   env=dict(os.environ, TZ=zone))
                check(f"the date helpers give Foundation's text in {zone}", r.returncode == 0, (r.stdout + r.stderr)[-1500:])

    # The preview build: every BCD screen file but the engine-bound ones, with the sample data.
    if args.no_preview:
        print("SKIP the preview build (--no-preview)")
    else:
        with tempfile.TemporaryDirectory(prefix="bcd-game-preview-") as tmp:
            r = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp],
                               capture_output=True, text=True, timeout=1800)
            check("the preview build compiles (tools/ui-preview.py --build-only)", r.returncode == 0, (r.stdout + r.stderr)[-3000:])

CI = R / ".github/workflows/build-ipa.yml"
if CI.exists():
    check("CI runs this check", "run: python3 tests/host/check-bcd-game-page.py --require-swift" in CI.read_text())

print("PASS: BCD game page" if ok else "FAIL: BCD game page")
sys.exit(0 if ok else 1)
