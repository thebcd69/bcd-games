#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Every way BCD's interface starts or downloads a game goes the classic library's way
(app/Madeira/BCDLiveStore.swift; docs/BCD-UI-ARCHITECTURE.md). The owner, 2026-10-18: a game
chosen on the home screen or in the library must start as before, whatever it is: a Steam game
through BCD Dock or started directly, a program added from Files, the Windows desktop; and Steam
downloads keep running in the background on iOS 26 as they do from the classic library.

Source checks, each pinned to the classic library's own code:
  - Play, every kind: BCD's only way into the engine is the Play ContentView hands both
    interfaces (launchLibraryEntry), with the entry the classic library passes: the stored entry
    (a program added from Files, the Windows desktop or its first-start stand-in), or for a Steam
    game LibraryModel.steamEntry, after the check the classic Game details page asked (SteamPlayCheck.blocker, moved
    unchanged from LibraryDetail.steamBlocker when that page was deleted; an update still
    downloading, then BCD Dock's or "The game"'s rules);
  - launchLibraryEntry, once, for both: the device gate, the recommended settings, BCD Dock or
    "The game", Steam Cloud, one session, JIT (Play turns it on and starts the game once it is),
    the profile's checks, the session; BCD Dock: JIT, Steam Cloud, the one-time installs, the
    session. BCD's interface runs with the library engine on (the JIT prompt is the library's);
  - GOG: its hook (BCDLiveStoreGOG.gogPlayEntry) hands the installed game's library entry, made
    from its program, arguments and working folder as a game added from Files, to the same Play;
    its download queue holds while a game runs. Epic (milestone 0): its hook (BCDLiveStoreEpic) lists the account's
    games as not installed; nothing starts them and Install only says that installing comes later;
  - downloads: Install, Update, Resume and Check files call SteamOwnedLibrary.install (repair) at
    once, as the classic download sheet and Game details do, so the queue's pump starts iOS 26's
    continued-processing task (SteamDownloadBackground) while BCD is in front; Pause and Stop are
    the classic calls; no BCD file touches the background task, and nothing BCD does when a screen
    closes or the app leaves the front pauses or cancels a download;
  - CI runs this check.
Behaviour (swiftc on the host; --require-swift makes its absence a failure):
  - SteamPlayCheck.blocker compiled from SteamGames.swift with the production SteamGamesRules
    and SteamDirectStart blockers: every state of a Steam game, BCD Dock and "The game";
  - BCDLiveID compiled from BCDLiveStore.swift: Steam and library ids round-trip; Epic, GOG (handled
    by GOG's hook first) and malformed ids are none of them.

  check-bcd-launch.py [--require-swift]
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
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + detail) if detail and not cond else ""))
    if not cond:
        failures += 1


def block(text, header):
    """The declaration starting at `header`, through its closing brace ("" when it is gone, so the
    checks that need it fail instead of the script stopping)."""
    p = text.find(header)
    if p < 0:
        print(f"MISSING: {header}")
        return ""
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


def code(text):
    """The text without comments (whole-line and trailing `//`, outside strings well enough here)."""
    out = []
    for line in text.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//\s.*$", "", line))
    return "\n".join(out)


def ordered(text, *parts):
    """Each part found in `text` after the one before it."""
    at = -1
    for part in parts:
        found = text.find(part, at + 1)
        if found < 0:
            return False
        at = found
    return True


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
          "// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
store = (APP / "BCDLiveStore.swift").read_text()
front = (APP / "BCDLiveFrontEnd.swift").read_text()
content = (APP / "ContentView.swift").read_text()
lib = (APP / "Library.swift").read_text()
games = (APP / "SteamGames.swift").read_text()
owned = (APP / "SteamOwnedLibrary.swift").read_text()
background = (APP / "SteamDownloadBackground.swift").read_text()
check(Path(__file__).read_text().startswith("#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-3.0-only\n"
                                            "# Copyright 2026 thebcd69 (BCD)\n"),
      "this check carries BCD's licence header")

# ---- Play: one way in, the classic library's ---------------------------------------------------
# Wave 3 (2026-10-19): the classic library (LibraryView) and the interface choice are deleted; BCD's interface is the
# only one and gets the one Play.
check(content.count("play: launchLibraryEntry, enableJIT: enableJIT,") == 1
      and "BCDLiveFrontEnd(play: launchLibraryEntry, enableJIT: enableJIT," in content
      and "LibraryView(" not in content,
      "ContentView hands BCD's interface the Play (launchLibraryEntry), Enable JIT and the Dock start; no other interface is left")
check("if library.current != nil {\n                    sessionBody" in content and "FrontendChoice" not in content + lib
      and "LibraryController.shared.configure(enabled: true, ownsInput: true)" in block(lib, "func refreshFlag()"),
      "BCD's interface runs with the library engine always on: its Play, JIT prompt and alerts are the library's")
start = code(block(store, "private func start(_ game: BCDGame)"))
# Build 622: every start goes through begin(), which shows a heavy game's one-time graphics tip first (BCDGraphicsTip) and
# otherwise hands the entry to the connected Play; the tip's OK hands it over too.
begin = code(block(store, "private func begin(_ entry: LibraryEntry, _ launch: (LibraryEntry) -> Void)"))
answered = code(block(store, "func graphicsTipAnswered()"))
check(start.count("begin(entry, launch)") == 6 and "launch(entry)" not in start.replace("begin(entry, launch)", "")
      and "guard let launch else { return }" in start
      and "if let entry = gogPlayEntry(game) { begin(entry, launch); return }" in start
      and "if let entry = epicPlayEntry(game) { begin(entry, launch); return }" in start
      and "if let entry = itchPlayEntry(game) { begin(entry, launch); return }" in start
      and "if let entry = eaPlayEntry(game) { begin(entry, launch); return }" in start
      and "if BCDGraphicsTip.due(entry) {" in begin and begin.count("launch(entry)") == 1
      and "launch?(entry)" in answered and "BCDGraphicsTip.markShown(entry)" in answered,
      "BCDLiveStore.start: the connected Play is its only way to start a game (GOG, Epic, itch.io, EA, a game from Files or the desktop, Steam), "
      "after a heavy game's one-time graphics tip")
check("library.entries.first(where: { $0.id == id })\n"
      "                    ?? (id == LibraryEntry.desktopID ? LibraryEntry.desktopEntry : nil)" in start,
      "a program added from Files and the Windows desktop: the stored entry, or the desktop's stand-in before its first start")
rebuild = code(block(store, "private func rebuild()"))
# (The classic Desktop button's own copy of this rule, in the deleted library screen, is no longer counted.)
check("if entry.desktop == true {\n                if desktop { continue }" in rebuild
      and "if !desktop { games.append(localGame(.desktopEntry)) }" in rebuild,
      "the desktop BCD lists is the first stored desktop entry, else the stand-in (the classic Desktop button's rule)")
check("library.steamEntry(installed, title: title)" in start
      and "let title = SteamOwnedLibrary.enabled ? SteamOwnedLibrary.shared.game(appID)?.name ?? installed.name : installed.name" in start
      and "func steamEntry(_ game: DockGame, title: String? = nil) -> LibraryEntry" in lib,
      "a Steam game: the entry the classic Steam card opens Game details with (LibraryModel.steamEntry, the item's name)")
check("if let blocker = SteamPlayCheck.blocker(entry) {\n                library.error = blocker\n                return\n            }" in start
      and ordered(start, "library.steamEntry(installed, title: title)", "SteamPlayCheck.blocker(entry)", "begin(entry, launch)"),
      "a Steam game: the Steam check (SteamPlayCheck.blocker) before Play, then Play")
# Wave 3 (2026-10-19): the classic Game details page, the other caller of this check, is deleted
# (its "asks the same function first" assertion is retired); the function itself moved, unchanged.
check("steamBlocker" not in lib, "the Steam check lives in SteamGames.swift only (the classic page no longer carries a copy)")
blocker_fn = block(games, "@MainActor static func blocker(_ entry: LibraryEntry) -> String? {")
check(ordered(code(blocker_fn), "guard let appID = entry.steamAppID else { return nil }",
              "if SteamOwnedLibrary.shared.downloads[appID] != nil {",
              '"This game is still updating. Resume the update and let it finish, then play."',
              "let installed = SteamGamesModel.shared.games.first { $0.id == appID }?.installed ?? false",
              "if entry.startsSteamGameDirectly {",
              "SteamDirectStart.blocker(installed: installed, program: entry.steamProgram)",
              "SteamGamesRules.blocker(installed: installed, client: MadeiraDock.clientInstalled, signedIn: SteamSignIn.isSignedIn)")
      and "updating:" not in blocker_fn and "updating:" not in start,
      "SteamPlayCheck.blocker: an update still downloading first, then BCD Dock's or \"The game\"'s rules, as Game details always asked")
gone = start[start.index("guard let installed"):]
check("refreshLibrary()" in gone[:gone.index("return")]
      and not any("Refresh the library" in (APP / f).read_text() for f in ("BCDLiveStore.swift", "BCDLiveFrontEnd.swift")),
      "a Steam game Steam no longer lists: BCD reads the installs again itself and says so (no control it lacks is named)")
system = code(block(store, "private func refreshSystem()"))
home = (APP / "BCDHomeView.swift").read_text()
check(start.index("startingGame = game.id") < start.index("begin(entry, launch)")
      and "if let game = self?.startingGame { self?.playing = (entry.id, game) }" in block(store, "func connect(play:")
      and "let waiting = LibraryModel.shared.startingJIT.flatMap(gameID)" in system
      and "if let playing, playing.entry == entry { return playing.game }" in block(store, "private func gameID(_ entry: UUID) -> String?")
      and "library.$startingJIT.map { _ in () }" in store
      and "store.startingJIT = nil" in block(store, "private func session(running: Bool)")
      and 'Label(waiting ? "Turning on JIT…" : "Play"' in home and "let waiting = store.startingJIT == game.id" in home,
      "Play that turns JIT on first says so on the game's own Play (the classic Play button's \"Enabling JIT\"), not only in the chip")
live_files = {f.name: f.read_text() for f in sorted(APP.glob("BCD*.swift"))}
# A Live file may call a start it was handed (BCDDockLive's injected `startDock`, BCDLiveStore's
# startDock that hands Settings' Dock start to ContentView's own) and read whether Windows runs (wine_process_is_running); a touch's own press?.begin()
# is no session. Anything else that starts a game, a session or BCD Dock is not BCD's to call.
# The live store's own `func startDock` hands the start it was given on (ContentView's, set by BCDLiveFrontEnd), and
# Settings' BCD Dock section calls that through BCDLiveStore.shared (BCDSettingsLive): neither starts anything itself.
starters = (r"\brunWineFullSequence\b",
            r"(?<!self\.)(?<!let )(?<!func )(?<!BCDLiveStore\.shared\.)\bstartDock\((?!game, dock\.compactPool\))",
            r"\bstartLibraryEntry\b", r"\blaunchLibraryEntry\b",
            r"requestLaunch", r"(?<!CATransaction)(?<!press\?)\.begin\(", r"configureLaunch", r"applyEnvironment", r"BCDLaunch\.",
            r"wine_process_(?!is_running\b)")
found = {name: [w for w in starters if re.search(w, code(text))] for name, text in live_files.items()}
check(not any(found.values()), "no BCD file starts a game, a session or BCD Dock itself", str({k: v for k, v in found.items() if v}))
check("func startDock(_ game: DockGame, _ compactPool: Bool) { settled { [weak self] in self?.dockStart?(game, compactPool) } }" in live_files["BCDLiveStore.swift"]
      and "live.dockStart = startDock" in live_files["BCDLiveFrontEnd.swift"]
      and "startDock: { BCDLiveStore.shared.startDock($0, $1) }" in live_files["BCDSettingsLive.swift"],
      "the Dock start is handed over: ContentView's own start, through the live store, for Settings' BCD Dock section")

# ---- launchLibraryEntry: what every Play goes through ------------------------------------------
launch = code(block(content, "private func launchLibraryEntry(_ entry: LibraryEntry)"))
check(ordered(launch, "DeviceCapability.libraryRefusal(entry)", "GameRecommendations.prepare(&entry, new: isNew)",
              "if let appID = entry.steamAppID, !entry.startsSteamGameDirectly {", "BCDLaunch.applyLibrary(entry, sessionLog: false)",
              "startDock(game, compactPool: MadeiraDockModel.shared.compactPool, profile: entry)",
              "cloudClear(appID, name: entry.title, retry: { launchLibraryEntry(entry) })",
              "guard entry.steamProgram?.isEmpty == false else {",
              "guard wine_process_is_running() == 0, wineserver_is_running() == 0, library.current == nil else {",
              'MadeiraConfig.flag("MADEIRA_ONE_SESSION_PER_RUN")',
              "jitReadyForLaunch(entry: entry.id, then: { startLibraryEntry(entry) })",
              "startLibraryEntry(entry)"),
      "Play: device gate, recommended settings, BCD Dock or \"The game\" (Steam Cloud, program), one session, JIT, start")
started = code(block(content, "private func startLibraryEntry(_ entry: LibraryEntry)"))
check(ordered(started, "try entry.validate()", "entry.configureLaunch()", "BCDLaunch.applyLibrary(entry, sessionLog: false)",
              "LogStore.shared.startSessionLog(program: program)", "library.begin(entry, remember: !BCDLaunchersLive.isSetup(entry))",
              "runWineFullSequence(profile: entry)"),   # a launcher's installer run (BCDLaunchersLive) is the one start the library does not remember
      "Play with JIT on: the profile's checks, the game's options, its log, the session")
dock = code(block(content, "private func startDock(_ game: DockGame, compactPool: Bool, profile: LibraryEntry? = nil)"))
check(ordered(dock, "jitReadyForLaunch(entry: profile?.id,",
              "cloudClear(game.id, name: game.name, retry:",
              "try MadeiraDock.validate(game, drive: MadeiraDock.drive)", "try profile?.validate()",
              "DockInstallers.prepare(game, drive: MadeiraDock.drive, prefix: MadeiraDock.prefix,",
              "if let profile { library.begin(profile, dock: game) }", "runWineFullSequence(profile: profile)"),
      "BCD Dock: JIT (the library's prompt), Steam Cloud, the checks, the one-time installs, the game's session")
jit = code(block(content, "private func jitReadyForLaunch("))
check(ordered(jit, "if StikJITHelper.ready { return true }", "if let launch {", "launchAfterJIT = launch",
              "library.startingJIT = entry", "if jitStatus != .testing { enableJIT() }"),
      "JIT off: Play turns JIT on itself and starts the game once it is (the library's JIT prompt)")
begin = code(block(lib, "func begin(_ entry: LibraryEntry, remember: Bool = true, dock: DockGame? = nil)"))
check("if AutoTune.enabled { AutoTune.sessionBegan(entry, remember: remember, dockWithoutEntry: dock != nil && !remember) }" in begin
      and "if remember { var played = entry; played.lastPlayed = Date(); save(played) }" in begin,
      "the session start: auto-tune's marker (when on) and the entry saved as played, for both interfaces")
check("library.$current.sink { [weak self] current in self?.session(running: current != nil) }" in store,
      "BCD's in-game state follows the session LibraryModel.begin starts")

# ---- GOG: its own hook, then the same Play; Epic (M0): listed as not installed by its hook, started by nothing --
gog_live = (APP / "BCDGOG/BCDLiveStoreGOG.swift").read_text()
gog_core = (APP / "BCDGOG/BCDGOG.swift").read_text()
queue_src = (APP / "BCDContent/BCDContentQueue.swift").read_text()
check(ordered(start, "guard let launch else { return }", "startingGame = game.id",
              "if let entry = gogPlayEntry(game) { begin(entry, launch); return }", "switch BCDLiveID.parse(game.id) {")
      and "guard BCDGOG.enabled, let id = Self.gogProduct(game.id) else { return nil }" in block(gog_live, "func gogPlayEntry(_ game: BCDGame) -> LibraryEntry?"),
      "a GOG game: its hook gives the installed game's library entry (nil for every other game), handed to the same Play")
make = code(block(gog_core, "private func makeEntry(_ record: inout BCDGOGInstallRecord)"))
check(all(s in make for s in ("entry.relativePath = launch.relativePath", "entry.arguments = launch.arguments",
                              "entry.workingDirectory = launch.workingDirectory", "LibraryModel.shared.save(entry)"))
      and "steamAppID" not in make and "LibraryModel.shared.entries.first { $0.id == id }" in block(gog_core, "func playEntry(_ id: String)"),
      "a GOG game's program, arguments and working folder are a stored library entry's, as for a game added from Files "
      "(no Steam App ID: launchLibraryEntry starts it as a local game)")
check('queue.sessionRunning = { LibraryModel.shared.current != nil || wine_process_is_running() != 0 || wineserver_is_running() != 0 }' in gog_core
      and "LibraryModel.shared.$current.receive(on: DispatchQueue.main)" in gog_core
      and ordered(code(block(queue_src, "func sessionChanged(active running: Bool)")), "if let current = active { held[current.key] = .session; current.task.cancel() }",
                  "for key in order { held[key] = .session; jobs[key]?.state = .paused }")
      and "guard active == nil, !inSession," in block(queue_src, "private func pump()"),
      "GOG's download queue holds while a game runs and starts nothing then")
check("store: .local" in code(block(store, "private func localGame(_ entry: LibraryEntry) -> BCDGame"))
      and "store: .steam" in code(block(store, "private func steamGame("))
      and not re.search(r"BCDGame\([^)]*store:\s*\.epic", code(store) + code(gog_live))
      and len(re.findall(r"BCDGame\(id:", code(store))) == 2 and "gogMerge(&games)" in store,
      "the live store lists library and Steam games, GOG's and Epic's through their hooks")
epic_live = (APP / "BCDEpic/BCDLiveStoreEpic.swift").read_text()
check("epicMerge(&games)" in store and "epicState(id, installed: epic.installs[id], queue: queue)" in code(block(epic_live, "func epicMerge(_ games: inout [BCDGame])"))
      and "state: .ready" not in code(epic_live) and "return installed == nil ? .notInstalled : .ready" in code(epic_live)
      and "launch" not in code(epic_live),
      "Epic's hook lists a game as installed only when its record says so, and starts nothing itself (it hands BCDLiveStore the library entry, as GOG's does)")
check("epicAccount()," in store and "gogAccount()," in store and "BCDAccount(store: .epic" not in store,
      "Epic's account and GOG's come from their hooks")
ids = block(store, "enum BCDLiveID {")
check('id.hasPrefix("steam.")' in ids and 'id.hasPrefix("local.")' in ids and "epic" not in ids
      and "case nil:\n" in start and "GOG's and Epic's games start through gogPlayEntry and epicPlayEntry" in block(store, "private func start(_ game: BCDGame)"),
      "BCDLiveID knows Steam and library ids (GOG's and Epic's go to their hooks first): any other id starts nothing, and where Play plugs in is marked")
check('guard case .steam(let appID)? = BCDLiveID.parse(game.id) else { return }' in block(store, "func install(_ game: BCDGame, place: BCDInstallPlace)")
      and "if gog(.install, game.id) { return }" in block(store, "func install(_ game: BCDGame, place: BCDInstallPlace)")
      and "if epic(.install, game.id, place: place) { return }" in block(store, "func install(_ game: BCDGame, place: BCDInstallPlace)")
      and "guard front == .steam else { return }" in block(store, "func signIn(_ front: BCDStoreFront)"),
      "Install and sign-in: GOG's and Epic's hooks first (Epic's Install only says it comes later), then Steam")

# ---- downloads: the classic calls, at once; the background task untouched ----------------------
download = code(block(store, "private func download(_ appID: Int)"))
check("if SteamOwnedLibrary.enabled && account.signedIn {\n            account.install(appID)\n        } else {\n"
      "            settled { account.install(appID) }" in download,
      "a download starts at once (SteamOwnedLibrary.install), while BCD is in front; only a start that can only fail waits for the page")
for fn in ("func install(_ game: BCDGame, place: BCDInstallPlace)", "func update(_ game: BCDGame)", "func resumeDownload(_ id: String)"):
    body = code(block(store, fn))
    check("download(appID)" in body and "settled" not in body and ".install(" not in body, f"{fn.split('(')[0]}: the download path above")
verify = code(block(store, "func verify(_ game: BCDGame)"))
check(verify.strip().endswith("SteamOwnedLibrary.shared.repair(appID)\n    }") and "SteamInstallPaths.isManaged(library: installed.library)" in verify
      and "install(appID)" in code(block(owned, "func repair(_ appID: Int)")),
      "Check files: SteamOwnedLibrary.repair at once (a download), for a game BCD downloaded, as Game details offers it")
check("SteamOwnedLibrary.shared.pause(appID)" in block(store, "func pauseDownload(_ id: String)")
      and "SteamOwnedLibrary.shared.cancelInstall(appID, installed: SteamGamesModel.shared.games.contains { $0.id == appID })"
      in block(store, "func cancelDownload(_ id: String)"),
      "Pause and Stop: the calls the classic download sheet made (the sheet is deleted, wave 3)")
# Wave 3: the classic download sheet's buttons (Install, Resume, Try again, Download the update, Check files) are
# deleted; BCDLiveStore's install, update, resume and verify (checked below) call the same SteamOwnedLibrary functions.
install = code(block(owned, "func install(_ appID: Int)"))
pump = code(block(owned, "private func pump()"))
run = code(block(owned, "private func run(_ appID: Int) async"))
check(ordered(install, "guard Self.enabled else { return }", "guard signedIn else {", "queue.append(appID)", "pump()")
      and "SteamDownloadBackground.shared.downloadStarted(appID: appID," in pump
      and "SteamDownloadBackground.shared.progress(progress)" in run and "SteamDownloadBackground.shared.downloadEnded(" in run,
      "SteamOwnedLibrary.install queues the game and its pump starts iOS 26's continued-processing task (SteamDownloadBackground)")
check("SteamDownloadBackground.shared.attach(self)" in block(owned, "func start()")
      and "SteamOwnedLibrary.shared.start(); SteamOwnedLibrary.shared.reconcileSession()" in block(store, "func libraryAppeared()"),
      "BCD's interface starts the Steam library as the classic section does (which attaches the background handling)")
untouchable = ["SteamDownloadBackground", "BGTaskScheduler", "BGContinuedProcessingTask", "beginBackgroundTask", "endBackgroundTask",
               "setTaskCompleted", "pauseForBackground", "resumeAfterBackground", "didEnterBackgroundNotification"]
# The controller hub lets go of the keys it holds when BCD leaves the front (it knows nothing of downloads).
hub_text = live_files["BCDControllerHub.swift"]
hub_leaves = ("for name in [UIApplication.willResignActiveNotification, UIApplication.didEnterBackgroundNotification] {" in hub_text
              and "MainActor.assumeIsolated { self?.router.releaseAll() }" in hub_text
              and not any(w in code(hub_text) for w in ("Steam", "Download", "BGTaskScheduler")))
check(hub_leaves, "the controller hub only releases its held keys when BCD leaves the front (no download task)")
# The session recorder notes when the app leaves the front (round 603): the open-session marker says "in the background", and one partial
# summary line goes into the log, which may be the last it hears of the session. It knows nothing of downloads either.
recorder_text = live_files["BCDSessionRecorderLive.swift"]
recorder_leaves = ("UIApplication.didEnterBackgroundNotification" in recorder_text and "self?.lifecycle(background: true)" in recorder_text
                   and not any(w in code(recorder_text) for w in ("Steam", "Download", "BGTaskScheduler", "beginBackgroundTask", "BGContinuedProcessingTask")))
check(recorder_leaves, "the session recorder only notes that the app left the front (the marker, a partial summary line); no download task")
touching = {name: [w for w in untouchable if w in code(text) and not (hub_leaves and name == "BCDControllerHub.swift"
                                                                      and w == "didEnterBackgroundNotification")
                   and not (recorder_leaves and name == "BCDSessionRecorderLive.swift" and w == "didEnterBackgroundNotification")]
            for name, text in live_files.items()}
check(not any(touching.values()), "no BCD file touches the background download task (only the engine does)",
      str({k: v for k, v in touching.items() if v}))
callers = {}
for name, text in live_files.items():
    stripped = code(text)
    for call in ("SteamOwnedLibrary.shared.pause(", "cancelInstall(", "SteamOwnedLibrary.shared.uninstall("):
        if call in stripped:
            callers.setdefault(call, []).append(name)
check(all(v == ["BCDLiveStore.swift"] for v in callers.values())
      and code(store).count("SteamOwnedLibrary.shared.pause(") == 1 and code(store).count("cancelInstall(") == 1,
      "only the player's Pause, Stop and Uninstall (BCDLiveStore's actions) stop a download", str(callers))
phase = block(front, ".onChange(of: scenePhase) { _, phase in")
check("guard phase == .active else { return }" in phase and "live.becameActive()" in phase
      and "SteamOwnedLibrary.shared.reconcileSession()" in block(store, "func becameActive()"),
      "BCD does nothing when the app leaves the front; back in front it reconciles Steam's session (as the classic library did)")
disappear = [m.group(0) for name, text in live_files.items() for m in re.finditer(r"\.onDisappear \{[^\n]*", code(text))]
check(not [d for d in disappear if re.search(r"Steam|download|install|pause|cancel", d, re.I)],
      "no screen stops a download when it closes", str(disappear))

ci = R / ".github/workflows/build-ipa.yml"
if ci.exists():
    check("run: python3 tests/host/check-bcd-launch.py --require-swift" in ci.read_text(), "CI runs this check")

# ---- behaviour: Game details' check, compiled ----------------------------------------------------
SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc")
rules_fn = block(games[games.index("enum SteamGamesRules {"):], "static func blocker(installed: Bool, client: Bool, signedIn: Bool, updating: Bool = false) -> String?")
direct_fn = block(games[games.index("enum SteamDirectStart {"):], "static func blocker(installed: Bool, program: String?, updating: Bool = false) -> String?")
steam_blocker = blocker_fn.replace("@MainActor ", "")
live_id = block(store, "enum BCDLiveID {")
PROGRAM = r'''
import Foundation
struct LibraryEntry {
    var steamAppID: Int?
    var steamStart: String?
    var steamProgram: String?
    var startsSteamGameDirectly: Bool { steamAppID != nil && steamStart == "game" }
}
struct DockGame { var id: Int; var installed: Bool }
final class SteamOwnedLibrary { static let shared = SteamOwnedLibrary(); var downloads: [Int: Int] = [:] }
final class SteamGamesModel { static let shared = SteamGamesModel(); var games: [DockGame] = [] }
enum MadeiraDock { static var clientInstalled = true }
enum SteamSignIn { static var isSignedIn = true }
enum SteamGamesRules {
RULES
}
enum SteamDirectStart {
DIRECT
}
enum SteamPlayCheck {
BLOCKER
}
LIVEID
var failed = 0
func expect(_ cond: Bool, _ what: String) { print((cond ? "PASS: " : "FAIL: ") + what); if !cond { failed += 1 } }
func reset() {
    SteamOwnedLibrary.shared.downloads = [:]
    SteamGamesModel.shared.games = [DockGame(id: 620, installed: true)]
    MadeiraDock.clientInstalled = true
    SteamSignIn.isSignedIn = true
}
let dock = LibraryEntry(steamAppID: 620, steamStart: nil, steamProgram: nil)
let direct = LibraryEntry(steamAppID: 620, steamStart: "game", steamProgram: "bin/game.exe")
let updating = "This game is still updating. Resume the update and let it finish, then play."
let partly = "Steam still lists this game as partly installed."
reset()
expect(SteamPlayCheck.blocker(LibraryEntry(steamAppID: nil)) == nil, "a game added from Files (or the desktop): no Steam check")
expect(SteamPlayCheck.blocker(dock) == nil && SteamPlayCheck.blocker(direct) == nil, "installed, client, signed in: Play")
SteamOwnedLibrary.shared.downloads = [620: 1]
expect(SteamPlayCheck.blocker(dock) == updating && SteamPlayCheck.blocker(direct) == updating, "an update downloading: wait for it")
SteamGamesModel.shared.games = [DockGame(id: 620, installed: false)]
expect(SteamPlayCheck.blocker(dock) == updating, "an update downloading is named before a partly installed record")
reset(); SteamGamesModel.shared.games = [DockGame(id: 620, installed: false)]
expect(SteamPlayCheck.blocker(dock) == partly && SteamPlayCheck.blocker(direct) == partly, "partly installed: no Play")
reset(); SteamGamesModel.shared.games = []
expect(SteamPlayCheck.blocker(dock) == partly, "a game Steam no longer lists counts as not installed")
reset(); MadeiraDock.clientInstalled = false
expect(SteamPlayCheck.blocker(dock)?.contains("Valve's client") == true && SteamPlayCheck.blocker(direct) == nil,
       "no client: BCD Dock waits, \"The game\" does not need it")
reset(); SteamSignIn.isSignedIn = false
expect(SteamPlayCheck.blocker(dock)?.contains("signed in") == true && SteamPlayCheck.blocker(direct) == nil,
       "signed out: BCD Dock waits, \"The game\" does not need a sign-in")
reset()
expect(SteamPlayCheck.blocker(LibraryEntry(steamAppID: 620, steamStart: "game", steamProgram: ""))?.contains("No program") == true,
       "\"The game\" without a program: pick one first")
let uuid = UUID()
expect(BCDLiveID.parse(BCDLiveID.steam(620)) == .steam(620) && BCDLiveID.parse(BCDLiveID.local(uuid)) == .local(uuid),
       "Steam and library ids round-trip")
expect(BCDLiveID.parse("epic.deathsdoor") == nil && BCDLiveID.parse("gog.moonscars") == nil && BCDLiveID.parse("steam.x") == nil,
       "BCDLiveID: Epic, GOG (its hook's) and malformed ids are none of Steam's or the library's")
exit(failed == 0 ? 0 : 1)
'''
if not SWIFTC:
    print("SKIP: no swiftc for the compiled checks")
    if args.require_swift:
        check(False, "swiftc is required (--require-swift)")
else:
    source = (PROGRAM.replace("RULES", rules_fn).replace("DIRECT", direct_fn)
              .replace("BLOCKER", steam_blocker).replace("LIVEID", live_id))
    with tempfile.TemporaryDirectory(prefix="bcd-launch-") as tmp:
        folder = Path(tmp)
        (folder / "main.swift").write_text(source)
        exe = folder / "check"
        build = subprocess.run([SWIFTC, "-swift-version", "5", "-o", str(exe), str(folder / "main.swift")],
                               capture_output=True, text=True)
        check(build.returncode == 0, "SteamPlayCheck.blocker and BCDLiveID compile on the host with stubs", build.stderr[-3000:])
        if build.returncode == 0:
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            sys.stdout.write(result.stdout)
            check(result.returncode == 0, "the Steam check Game details asked and BCD's ids behave as the classic library did", result.stderr[-2000:])

print("check-bcd-launch:", "FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)
