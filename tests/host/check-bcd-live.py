#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's interface as the app's real interface (app/Madeira/BCDLiveStore.swift and
BCDLiveFrontEnd.swift; docs/BCD-UI-ARCHITECTURE.md).

Source checks (every host):
  - BCD's licence header, exactly, on the live files;
  - tools/ui-preview.py keeps the engine-bound files out of the preview build, and no other
    BCD*.swift names an engine type;
  - the Xcode project builds every app/Madeira/BCD*.swift;
  - ContentView shows BCDLiveFrontEnd whenever no session runs (wave 3, 2026-10-19: the only
    interface; the classic library, the developer interface and the interface choice are
    deleted), with Play (launchLibraryEntry), Enable JIT and the Dock start; a session replaces it;
  - Play goes through that same function: the live store calls only the connected Play, with
    the stored entry, or for a Steam game LibraryModel.steamEntry after the Game details page's
    checks; the live files start nothing themselves;
  - inGame follows LibraryModel.current (set by begin, cleared when the session ends), drops
    the pictures and stops the metadata reads, and nothing rebuilds while it is set;
  - first-run setup opens over BCD's interface as over the classic library;
  - the settings button and Menu open BCD's own Settings; there is no interface picker;
  - a controller reaches BCD through LibraryController (LB and RB apart), never through
    GameController handlers of BCD's own;
  - store pictures come as the old grid's candidates, then the store's header, hero and logo;
  - memory (owner, 2026-10-18: "even 1 MB"): pictures are decoded for their frame (points times
    the screen's scale), held by a view only while it is on screen, kept beyond that up to about
    one and a half screens of pixels, and dropped with the interface's copies of the lists when
    a game starts;
  - CI runs this check.
Behaviour (swiftc on the host; --require-swift makes its absence a failure):
  - BCDArtFiles.longSide, compiled from BCDArt.swift: the decode size of covers, heroes and logos
    in the frames BCD shows them in;
Build (xcrun; --require-swift makes its absence a failure):
  - the preview build compiles (tools/ui-preview.py --build-only).

  check-bcd-live.py [--require-swift] [--skip-preview]
"""
import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
ap.add_argument("--skip-preview", action="store_true", help="leave out the preview build (it takes the simulator lock)")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + detail) if detail and not cond else ""))
    if not cond:
        failures += 1


def block(text, header):
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


def code(text):
    """The text without whole-line comments."""
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
          "// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
LIVE = ["BCDLiveStore.swift", "BCDLiveFrontEnd.swift"]
store = (APP / "BCDLiveStore.swift").read_text()
front = (APP / "BCDLiveFrontEnd.swift").read_text()
shell = (APP / "BCDFrontEnd.swift").read_text()
content = (APP / "ContentView.swift").read_text()
lib = (APP / "Library.swift").read_text()
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()

# ---- files, licence, preview, project ------------------------------------------------------
for name in LIVE + ["BCDFrontEnd.swift"]:
    check((APP / name).read_text().startswith(HEADER), f"{name}: BCD's licence header, exactly")

spec = importlib.util.spec_from_file_location("ui_preview", R / "tools/ui-preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)
check(set(LIVE) <= preview.ENGINE_BOUND and "BCDSetupView.swift" in preview.ENGINE_BOUND,
      "tools/ui-preview.py: ENGINE_BOUND keeps BCDLiveStore, BCDLiveFrontEnd and BCDSetupView out of the preview",
      str(sorted(preview.ENGINE_BOUND)))
# The screens never name an engine type: only the engine-bound files may (the preview build,
# which leaves those out, would not compile a screen that did).
ENGINE = ["LibraryModel", "LibraryEntry", "LibraryView", "LibraryController", "LibraryJITState", "LibraryDetail",
          "SteamOwnedLibrary", "SteamGamesModel", "SteamGamesRules", "SteamSignInModel", "SteamSignIn",
          "MadeiraDock", "MadeiraDockModel", "DockGame", "JITCoordinator", "WineProcessBridge", "MadeiraConfig",
          "LogStore", "OnboardingModel", "GameRecommendations", "AutoTune", "GamepadInput",
          "ContentView", "FrontendChoice", "StikJITHelper"]
named = {}
for f in sorted(APP.glob("BCD*.swift")):
    if f.name in preview.ENGINE_BOUND:
        continue
    found = sorted({t for t in ENGINE if re.search(r"\b" + t + r"\b", code(f.read_text()))})
    if found:
        named[f.name] = found
check(not named, "no screen file names an engine type", str(named))
unbuilt = [f.name for f in sorted(APP.glob("BCD*.swift"))
           if pbx.count(f"/* {f.name} in Sources */") != 2 or pbx.count(f"path = {f.name};") != 1]
check(not unbuilt, "the Xcode project builds every app/Madeira/BCD*.swift", str(unbuilt))

# ---- ContentView: BCD's interface in the library's place -----------------------------------
session_at = content.index("if library.current != nil {")
bcd_at = content.index("} else {", session_at)
bcd_branch = content[bcd_at:content.index("BCDLiveFrontEnd(", bcd_at) + 400]
check(session_at < bcd_at and "LibraryView" not in content and "FrontendChoice" not in content and "developerBody" not in content,
      "ContentView: a session first, otherwise BCD's interface; no classic library, interface choice or developer interface is left")
check("BCDLiveFrontEnd(play: launchLibraryEntry, enableJIT: enableJIT,\n"
      "                                    startDock: { startDock($0, compactPool: $1) })" in bcd_branch,
      "ContentView: BCD's interface gets Play, Enable JIT and the Dock start")
check("sessionBody" in content[session_at:bcd_at], "a game's session replaces BCD's interface")
check("BCDFrontEnd(store: live.store, focus: focus)" in front and "settingsOverride" not in front
      and "@ObservedObject private var live = BCDLiveStore.shared" in front,
      "BCDLiveFrontEnd shows BCD's shell on the live store")

# ---- the interface choice is gone: the library engine is always on ----------------------------
# Wave 3 (2026-10-19): FrontendChoice (the stored choice between BCD, the classic library and the developer
# interface, its one-time move and its log line), BCDInterfacePicker and RootView (the old home screen's start) are
# deleted; their compiled behaviour tests go with them. BCD's interface is the only one.
check("FrontendChoice" not in lib and "BCDInterfacePicker" not in (APP / "LibraryBCD.swift").read_text()
      and "RootView" not in (APP / "MadeiraApp.swift").read_text() and "ContentView()" in (APP / "MadeiraApp.swift").read_text(),
      "no interface choice and no old home screen: the app starts ContentView, which shows BCD's interface")
check("LibraryController.shared.configure(enabled: true, ownsInput: true)" in block(lib, "func refreshFlag()"),
      "the library engine is always on and owns the controller's navigation")

# ---- play ----------------------------------------------------------------------------------
start = block(store, "private func start(_ game: BCDGame)")
# Six starts, all through the connected Play: a GOG game's entry (BCDLiveStoreGOG's hook, which returns nil for
# every other game), an Epic game's entry (BCDLiveStoreEpic's, the same), an itch.io game's and an EA game's (the same), a game from
# Files or the desktop, and a Steam game.
# Build 622: through begin(), which shows a heavy game's one-time graphics tip first (BCDGraphicsTip, check-bcd-launch.py).
check(start.count("begin(entry, launch)") == 6 and "launch(entry)" not in start.replace("begin(entry, launch)", "")
      and "guard let launch else { return }" in start
      and "if let entry = gogPlayEntry(game) { begin(entry, launch); return }" in start
      and "if let entry = epicPlayEntry(game) { begin(entry, launch); return }" in start
      and "if let entry = itchPlayEntry(game) { begin(entry, launch); return }" in start
      and "if let entry = eaPlayEntry(game) { begin(entry, launch); return }" in start,
      "Play: the live store starts a game only through the connected Play (GOG, Epic, itch.io, EA, local, Steam)")
# Wave 3 (2026-10-19): the classic Game details page, which asked this check too, is deleted; the check moved
# unchanged to SteamGames.swift (SteamPlayCheck.blocker) and BCD's Play is the only caller.
check("library.steamEntry(installed, title: title)" in start
      and "if let blocker = SteamPlayCheck.blocker(entry) {" in start,
      "Play: a Steam game's entry as the classic library makes it, after the Steam check "
      "(SteamPlayCheck.blocker; tests/host/check-bcd-launch.py pins every kind)")
check("library.entries.first(where: { $0.id == id })" in start and "LibraryEntry.desktopEntry" in start,
      "Play: a game from Files (or the desktop) with its stored entry")
connect = block(store, "func connect(play: @escaping (LibraryEntry) -> Void, enableJIT: @escaping () -> Void)")
check("live.connect(play: play, enableJIT: enableJIT)" in front
      and "launch = { [weak self] entry in" in connect and connect.count("play(entry)") == 1
      and code(connect).count("launch") == 1,
      "the Play BCD's interface uses is ContentView's, handed over by BCDLiveFrontEnd")
# BCDLiveStore.startDock hands Settings › BCD Dock's start to ContentView's own (dockStart); its
# declaration is no start.
starts = {f: [w for w in (r"\brunWineFullSequence\b", r"(?<!func )\bstartDock\(", r"\bsetenv\(", r"\.begin\(", r"requestLaunch",
                          r"configureLaunch", r"applyEnvironment", r"launchLibraryEntry")
              if re.search(w, code((APP / f).read_text()))] for f in LIVE}
check(not any(starts.values()), "the live files start nothing themselves", str(starts))

# ---- in game -------------------------------------------------------------------------------
session = block(store, "private func session(running: Bool)")
check("library.$current.sink { [weak self] current in self?.session(running: current != nil) }" in store,
      "inGame follows LibraryModel.current: set by begin, cleared when the session ends or the launch fails")
check("store.inGame = running" in session and "BCDArt.shared.purge()" in session and "metadata?.cancel()" in session,
      "a game starting drops the pictures and stops the metadata reads")
check(all(s in session for s in ("art.removeAll()", "store.games = []", "store.downloads = []", "store.finishedToday = []"))
      and session.index("store.games = []") < session.index("} else {"),
      "a game starting drops the interface's copies of the lists too (made again when it ends)")
for fn in ("private func rebuild()", "private func refreshSystem()", "private func refreshMetadata()"):
    check(code(block(store, fn)).split("\n")[1].strip().startswith("guard !store.inGame"), f"{fn}: nothing while a game runs")
check("store.inGame" in shell and "focus.enabled = !playing" in shell and "BCDArt.shared.purge()" in shell,
      "BCDFrontEnd draws nothing and reads no controller while a game runs")
# Saves that differ (the classic Steam section's line, from SteamOwnedLibrary.cloudUndecided).
differ = block(store, "private func noteSavesDiffer()")
check("SteamOwnedLibrary.cloudEnabled ? SteamOwnedLibrary.shared.cloudUndecided.map(BCDLiveID.steam) : []" in differ
      and "if store.savesDiffer != ids { store.savesDiffer = ids }" in differ
      and code(differ).split("\n")[1].strip().startswith("guard !store.inGame"),
      "savesDiffer: the games Steam Cloud and the device disagree on (cloudUndecided), only with Steam Cloud on, nothing while a game runs")
check("account.$cloud.map { _ in () }" in store and "self?.noteSavesDiffer()" in store
      and "noteSavesDiffer()\n        refreshStorage()" in store and "store.savesDiffer = []" in session,
      "savesDiffer is read as the cloud state moves and with every rebuild, and dropped while a game runs")

# ---- around the shell: setup, settings, errors, controller ---------------------------------
check(".fullScreenCover(isPresented: $onboarding.presented, onDismiss: BCDPresentation.didClose) { OnboardingView() }" in front
      and "onboarding.presentIfNeeded()" in front and 'EndedSessionSurface.hide(reason: "library-appeared")' in front,
      "first-run setup opens over BCD's interface as over the classic library")
# Wave 3 (2026-10-19): the classic Settings page (BCDOpenClassicSettings, LibrarySettingsForm) is deleted; the
# settings button and Menu open BCD's own Settings through one function.
check("OpenClassicSettings" not in front and "LibrarySettingsForm" not in front and "GlassSkin" not in front
      and "@State private var settings" not in front,
      "BCDLiveFrontEnd has no classic Settings page: the settings button and Menu open BCD's own Settings")
check("settingsOverride" not in shell and "showSettings = true" in block(shell, "private func openSettings()")
      and "(BCDHint(button: .menu, title: \"Settings\"), { openSettings() })" in shell
      and "Button { openSettings() } label:" in shell, "BCDFrontEnd: the settings button and Menu share one openSettings")
check("LibrarySettingsForm" not in lib and "BCDInterfacePicker" not in lib,
      "the classic Settings form and its interface picker are gone")
check("if let jitProblem { jitConnectionActions(jitProblem, retry: enableJIT) { library.error = nil } }" in front
      and ('isPresented: Binding(get: { steam.error != nil }' in front or 'isPresented: Binding<Bool>(get: { steam.error != nil }' in front),
      "the library's and Steam's errors show over BCD's interface")
check("LogUploadSettingsSection" not in front and "sendLog" not in front and "BCDSteamSignInSheet()" in front
      and "SteamSignInView()" not in front,
      "no old Send Log sheet (Send Log lives in Settings › Logs and on the game page); Steam sign-in is BCD's own page")
# Add a game and a game's page (after Add a game, and the Play question's Choose saves) are BCD's
# own screens, full screen (ui-game-page, 2026-10-18): the classic browser and page never open.
check("BCDAddGame(close:" in front and "BCDGamePage(gameID:" in front and ".fullScreenCover(item: pageShown, onDismiss: BCDPresentation.didClose)" in front
      and "ExecutableBrowser" not in front and "LibraryDetail" not in front,
      "Add a game and a game's page are BCD's own screens, never the classic ones")
check("@StateObject private var focus = BCDFocus(watchControllers: false)" in front
      and ".onReceive(LibraryController.shared.commands)" in front
      and 'case "tab-previous": focus.press(.leftShoulder)' in front and 'case "tab-next": focus.press(.rightShoulder)' in front,
      "a controller drives BCD through LibraryController, never through handlers of its own")
controller = block(lib, "final class LibraryController: ObservableObject, @unchecked Sendable")
check('(0x100, "tab-previous"), (0x200, "tab-next")' in controller,
      "LB and RB stay apart for BCD")

# ---- pictures ------------------------------------------------------------------------------
check("SteamGamesRules.artwork(appID: appID, owned: owned)" in store and 'cdn + "header.jpg"' in store
      and 'cdn + "library_hero.jpg"' in store and 'cdn + "logo.png"' in store
      and "owned?.headerImage" in store and "owned?.libraryHero" in store and "AsyncImage" not in store + front,
      "store pictures: the old grid's candidates, then the store's header, hero and logo, through BCDArt")

chosen = block(store, "static func chosenCover(_ name: String?) -> URL?")
check("game.coverArt = (Self.chosenCover(entry?.coverFile).map { [$0] } ?? []) + pictures.cover" in store
      and "if let cover = Self.chosenCover(entry.coverFile) {" in store
      and '"bcd-art/" + URL(fileURLWithPath: name).lastPathComponent' in chosen and ".contentModificationDateKey" in chosen
      and 'URLQueryItem(name: "changed"' in chosen,
      "a cover the player chose shows first, for a Steam game too, and a new one under the same name is read again")

# ---- memory: pictures at the size shown, held only on screen, dropped for a game -------------
art = (APP / "BCDArt.swift").read_text()
parts = (APP / "BCDComponents.swift").read_text()
check("96 << 20" not in art and "memory.totalCostLimit = Self.budget" in art
      and "$0.nativeBounds.width * $0.nativeBounds.height" in art and "return Int(pixels * 4 * 1.5)" in art,
      "BCDArt keeps about one and a half screens of decoded pixels beyond what is on screen (no fixed 96 MB)")
check("maxPixel" not in code(art) + code(parts),
      "no fixed decode sizes: every picture is decoded for the frame that shows it")
image = block(art, "struct BCDArtImage<Placeholder: View>: View")
logo = block(parts, "private struct BCDLogoImage<Fallback: View>: View")
for name, view, fit in (("BCDArtImage", image, ".fill"), ("BCDLogoImage", logo, ".fit")):
    check("@Environment(\\.displayScale) private var scale" in view
          and ".onGeometryChange(for: CGSize.self) { $0.size } action: { size = $0 }" in view
          and f"BCDArt.shared.load(urls, pixels: pixels, fit: {fit}, glow:" in view
          and "CGSize(width: size.width.rounded(.up) * scale, height: size.height.rounded(.up) * scale)" in view,
          f"{name}: decoded for its frame (its points times the screen's scale)")
    check(".onDisappear { loaded = nil }" in view and "Task.isCancelled" in view,
          f"{name}: lets go of its picture when it leaves the screen (a lazy grid keeps its views)")
check("if let hit = sharp(key, pixels, fit, glow) { return hit }" in art and "func covers(_ pixels: CGSize, _ fit: Fit) -> Bool" in art
      and "private static func key(_ urls: [URL]) -> String" in art,
      "one decoded copy per picture: a smaller frame reuses a larger copy, a larger frame replaces it")
card = block(parts, "struct BCDCoverCard: View")
check(".overlay(BCDHairline(radius: radius, top: lit ? 0.6 : 0.32, bottom: 0.04)" in card and "LinearGradient" not in card
      and "override class var layerClass: AnyClass { CAGradientLayer.self }" in parts and "layer.mask = shape" in parts
      and "CATransaction.setDisableActions(true)" in parts,
      "a grid card's fading hairline is drawn by layers: a gradient-filled SwiftUI stroke made each card a card-sized bitmap")
check("kCGImageSourceThumbnailMaxPixelSize: long" in art and "let long = longSide(of: size, in: pixels, fit: fit)" in art
      and "kCGImageSourceShouldCache: false" in art,
      "pictures decode in one pass at that size, never at full size first")

ci = R / ".github/workflows/build-ipa.yml"
if ci.exists():
    check("run: python3 tests/host/check-bcd-live.py" in ci.read_text(), "CI runs this check")

# ---- behaviour: BCDArtFiles.longSide ---------------------------------------------------------
SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc")
LONGSIDE = r'''
import CoreGraphics
import Foundation
enum BCDArt { enum Fit { case fill, fit } }
enum BCDArtFiles {
FUNCTION
}
var failed = 0
func expect(_ cond: Bool, _ what: String) { print((cond ? "PASS: " : "FAIL: ") + what); if !cond { failed += 1 } }
func long(_ w: CGFloat, _ h: CGFloat, _ fw: CGFloat, _ fh: CGFloat, _ fit: BCDArt.Fit = .fill) -> Int {
    BCDArtFiles.longSide(of: CGSize(width: w, height: h), in: CGSize(width: fw, height: fh), fit: fit)
}
// A library card on an iPhone 13 Pro on its side: 107 x 161 points at 3x.
expect(long(300, 450, 321, 483) == 450, "Steam's 300x450 cover in a library card: its own size (\(long(300, 450, 321, 483)))")
expect(long(600, 900, 321, 483) == 496, "a 600x900 cover in the same card: 496 pixels, not 720 (\(long(600, 900, 321, 483)))")
// Home's hero beside the side card, 460 x 141 points at 3x; a game's page across the screen.
expect(long(1920, 620, 1380, 423) == 1392, "Home's hero: 1392 pixels wide, not 1920 (\(long(1920, 620, 1380, 423)))")
expect(long(1920, 620, 2532, 1170) == 1920, "a hero filling the screen: its own size, never more")
// The logo over the hero, fitted in 230 x 58 points at 3x.
expect(long(640, 360, 690, 174, .fit) == 320, "a 640x360 logo fitted in 230x58 points: 320 pixels (\(long(640, 360, 690, 174, .fit)))")
// The Downloads card's 42 x 63-point thumbnail at 3x.
expect(long(300, 450, 126, 189) == 192, "a cover in the Downloads card: 192 pixels (\(long(300, 450, 126, 189)))")
expect(long(460, 215, 321, 483) == 460, "a wide header in a cover's place (shown whole over a filled copy): its own size")
expect(long(1920, 620, 0, 0) == 1920 && long(0, 0, 300, 300) == 0, "a frame not laid out yet: the picture's own size")
expect(long(300, 450, 321, 483) == long(300, 450, 322, 484), "frames a point apart share one copy")
exit(failed == 0 ? 0 : 1)
'''
if SWIFTC:
    with tempfile.TemporaryDirectory(prefix="bcd-art-") as tmp:
        folder = Path(tmp)
        function = block(art, "static func longSide(of size: CGSize, in pixels: CGSize, fit: BCDArt.Fit) -> Int")
        (folder / "main.swift").write_text(LONGSIDE.replace("FUNCTION", function))
        exe = folder / "longside"
        build = subprocess.run([SWIFTC, "-swift-version", "5", "-o", str(exe), str(folder / "main.swift")],
                               capture_output=True, text=True)
        check(build.returncode == 0, "BCDArtFiles.longSide compiles on the host", build.stderr[-3000:])
        if build.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            sys.stdout.write(run.stdout)
            check(run.returncode == 0, "decode sizes: each picture at the size its frame shows", run.stderr[-2000:])

if not SWIFTC:
    print("SKIP: no swiftc for the behaviour checks")
    if args.require_swift:
        check(False, "swiftc is required (--require-swift)")

# ---- build: the preview ------------------------------------------------------------------
if args.skip_preview:
    print("SKIP: the preview build (--skip-preview)")
elif not shutil.which("xcrun"):
    print("SKIP: no xcrun for the preview build")
    if args.require_swift:
        check(False, "xcrun is required for the preview build (--require-swift)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-live-preview-") as tmp:
        run = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp],
                             capture_output=True, text=True)
        check(run.returncode == 0, "the preview build compiles (tools/ui-preview.py --build-only)",
              (run.stdout + run.stderr)[-3000:])

print("check-bcd-live:", "FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)
