#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""What the classic interface did for a person, kept in BCD's interface, and no "Madeira" in
what a person reads (docs/research/parity-2026-10-18.md, the parity audit of 2026-10-18).

Source checks (every host):
  1. ContentView's alerts and sheets reach BCD's interface: the restart notice, auto-tune's
     result, the Enable JIT notice, the JIT setup sheet and the Steam Cloud question hang off the
     Group that holds a session or BCD's interface (wave 3: the only interface).
  2. BCDLiveFrontEnd does what the classic library did around itself: the library's error (with
     the JIT connection fixes), Steam's error, first-run setup, the ended desktop's surface, a
     refresh when BCD comes back; its page closes on the same notices. Added by the audit: the
     note while a start waits for its Steam Cloud saves (static: nothing animates on its own).
  3. Steam downloads go on in the background (iOS 26+) and notify in BCD's interface too:
     BCDLiveStore starts SteamOwnedLibrary, which attaches SteamDownloadBackground; Info.plist
     permits the download and pairing continued-processing tasks.
  4. Downloads tab: every download has buttons (Pause, Resume or Try again, and Cancel, which
     asks first, in the row, so a controller can answer), all reachable by the controller; a
     failed download's main button asks for it again; the note on leaving BCD follows the iOS
     version (no "Keep BCD open").
  5. Links: the bcd scheme, Home Screen links built with it, the JIT shortcut's callbacks.
  6. Messages the engine shows in BCD's interface name no place only the classic interface
     has (Settings › Steam, Game details ›).
  7. No "Madeira" in a string a person reads: the app's name (bcd_brand's rule) and the fork's
     old name "madeira-bcd" in Swift string literals of app/Madeira (generated files aside:
     their generators use bcd_brand), except credits and licence phrases and the items routed
     to their owners, listed below with the owner; the display names in the plists.
Behaviour (swiftc on the host; --require-swift makes its absence a failure):
  - BCDDownloadButton and BCDDownloadNotes (BCDDownloadsView.swift) compiled alone.

  check-parity.py [--require-swift]
"""
import argparse
import importlib.util
import os
import plistlib
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
    """The text from `header` to the brace that closes its first opening brace."""
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


def literals(src):
    """Swift string literals with their line numbers: comments skipped, interpolations (and the
    strings inside them) followed, multi-line literals whole."""
    out, i, n, line = [], 0, len(src), 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1; i += 1; continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += src.count("\n", i, j); i = j
            continue
        if src.startswith('"""', i):
            j = src.find('"""', i + 3)
            j = n if j < 0 else j + 3
            out.append((line, src[i:j]))
            line += src.count("\n", i, j); i = j
            continue
        if c == '"':
            start, i, depth = i, i + 1, 0
            while i < n:
                ch = src[i]
                if ch == "\\":
                    if i + 1 < n and src[i + 1] == "(":
                        depth += 1; i += 2; continue
                    i += 2; continue
                if depth:
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    elif ch == '"':
                        k = i + 1
                        while k < n and src[k] != '"':
                            k += 2 if src[k] == "\\" else 1
                        i = k
                    i += 1
                    continue
                if ch in '"\n':
                    break
                i += 1
            out.append((line, src[start:i + 1]))
            i += 1
            continue
        i += 1
    return out


content = (APP / "ContentView.swift").read_text()
front = (APP / "BCDLiveFrontEnd.swift").read_text()
store = (APP / "BCDLiveStore.swift").read_text()
downloads = (APP / "BCDDownloadsView.swift").read_text()
steam = (APP / "SteamOwnedLibrary.swift").read_text()
background = (APP / "SteamDownloadBackground.swift").read_text()
games = (APP / "SteamGames.swift").read_text()
autotune = (APP / "AutoTune.swift").read_text()
info = plistlib.loads((APP / "Info.plist").read_bytes())

# ---- 1. ContentView's alerts reach every interface -----------------------------------------
# The root (a session or BCD's interface) and the app's alerts and observers (appChrome) are two members now: the original
# path still applies them in the original order (root, navigation title and bar, alerts), the native Duo path without the stack.
fronts = block(content, "    private var rootContent: some View {")
body_chrome = block(content, "    private func appChrome<Root: View>(_ root: Root) -> some View {")
# Wave 3 (2026-10-19): the classic library and the developer interface are deleted; the Group holds a session or BCD's interface.
check("BCDLiveFrontEnd(play: launchLibraryEntry" in fronts and "LibraryView(" not in fronts
      and "developerBody" not in fronts, "ContentView: one Group holds a session or BCD's interface, the app's only interface")
after = body_chrome
check("if BCDDuoFeatures.active {\n            appChrome(rootContent)" in content
      and 'NavigationStack {\n                appChrome(rootContent\n                    .navigationTitle("BCD")' in content
      and content.count("appChrome(rootContent") == 2,
      "ContentView: both paths apply the alerts and observers to the root; the original path keeps the order root, navigation title, bar, alerts")
for what, needle in [
        ("the restart notice (one game per run)", '.alert("BCD needs a restart", isPresented: Binding(get: { !BCDDuoFeatures.active && library.restartNotice != nil }'),
        ("auto-tune's result and \"Close BCD\" for the next attempt", ".modifier(AutoTuneAlert())"),
        ("the Enable JIT notice (JIT on without a debugger)", '.alert("Enable JIT", isPresented: Binding(get: { !BCDDuoFeatures.active && library.jitNotice != nil }'),
        ("the JIT setup sheet", ".sheet(isPresented: $jitCoordinator.showSetup) { BCDJITSetupSheet(enableJIT: { enableJIT() }) }"),
        ("the Steam Cloud question before a start", '.alert(library.cloudNotice?.title ?? "Steam Cloud"'),
        ("Home Screen links once the library is up", ".onReceive(ShortcutRouter.shared.$pendingExe) { _ in launchPendingShortcut() }")]:
    check(needle in after, f"ContentView: {what} hangs off the Group, so BCD's interface shows it")
for button in ('Button("Sync first")', 'Button("Try again")', 'Button("Choose saves")', 'Button("Play anyway")'):
    check(button in after, f"the Steam Cloud question offers {button[8:-2]}")

# ---- 2. BCDLiveFrontEnd: what LibraryView does around the library --------------------------
check("if let jitProblem { jitConnectionActions(jitProblem, retry: enableJIT) { library.error = nil } }" in front
      and ('isPresented: Binding(get: { library.error != nil }' in front or 'isPresented: Binding<Bool>(get: { library.error != nil }' in front
           or 'isPresented: Binding<Bool>(get: { !BCDDuoFeatures.active && library.error != nil }' in front),
      "BCD's interface shows the library's error, with the JIT connection fixes")
check(('.alert("Steam", isPresented: Binding(get: { steam.error != nil }' in front or '.alert("Steam", isPresented: Binding<Bool>(get: { steam.error != nil }' in front
           or '.alert("Steam", isPresented: Binding<Bool>(get: { !BCDDuoFeatures.active && steam.error != nil }' in front), "BCD's interface shows Steam's error")
check(".fullScreenCover(isPresented: $onboarding.presented, onDismiss: BCDPresentation.didClose) { OnboardingView() }" in front
      and "onboarding.presentIfNeeded()" in front, "first-run setup opens over BCD's interface")
check("EndedSessionSurface.install()" in front and 'EndedSessionSurface.hide(reason: "library-appeared")' in front,
      "an ended desktop session's surface never stays over BCD's interface")
check("library.refreshFlag()" in front and "live.becameActive()" in front
      and "SteamGamesModel.shared.refresh()" in block(store, "func becameActive()"),
      "BCD coming back to the front reads the library and Steam's installs again")
for notice in ("library.current", "library.error", "library.restartNotice", "library.jitNotice",
               "library.cloudNotice", "steam.error", "jit.showSetup"):
    check(re.search(r"\.onChange\(of: " + re.escape(notice) + r"\) \{[^\n]*live\.sheet = nil", front) is not None,
          f"an open page closes for {notice}, as LibraryView closes its details page")
check("live.sheet = .details(entry)" in block(front, ".onChange(of: library.showDetail)"),
      "\"Choose saves\" opens the game's page over BCD's interface")
wait = block(front, "@ViewBuilder private var cloudWaitNote: some View")
check(".overlay(alignment: .bottom) { cloudWaitNote }" in front and "steam.cloudWaitingFor" in wait
      and "Steam Cloud before it starts" in wait and "ProgressView" not in wait and "repeatForever" not in wait,
      "a start waiting for its Steam Cloud saves says so, with nothing animating")
# Wave 3: the shell's Send Log sheet (with Share logs beside it) is deleted; the messages that say to export the diagnostic
# log point to Settings › Logs, whose export shares the same logs, and the in-game menu shares them too.
check("shareLogs" not in front and "LogStore.shared.exportableLogs" in (APP / "BCDSettingsLive.swift").read_text()
      and "LogStore.shared.exportableLogs" in (APP / "BCDSessionLive.swift").read_text(),
      "Share logs (the logs the messages say to export) is in Settings › Logs and the in-game menu")

# ---- 3. background downloads and notifications ---------------------------------------------
appeared = block(store, "func libraryAppeared()")
check("SteamOwnedLibrary.shared.start()" in appeared and "SteamDownloadBackground.shared.attach(self)" in block(steam, "func start()"),
      "BCD's interface starts the Steam library, which attaches the background downloads")
permitted = info.get("BGTaskSchedulerPermittedIdentifiers", [])
check("$(PRODUCT_BUNDLE_IDENTIFIER).download.*" in permitted and "$(PRODUCT_BUNDLE_IDENTIFIER).pairing.*" in permitted,
      "Info.plist permits the download and pairing continued-processing tasks", str(permitted))
check("BGContinuedProcessingTaskRequest(identifier: identifier, title: \"Downloading \\(currentName)\"" in background
      and 'subtitle: "From Steam, in BCD")' in background and 'notify("\\(name) is ready"' in background,
      "a Steam download goes on in the background (iOS 26+), shown and announced in BCD's words")

# ---- 4. the Downloads tab ------------------------------------------------------------------
controls = block(downloads, "@ViewBuilder private func controls(_ download: BCDDownload) -> some View")
check("BCDDownloadButton(paused: download.paused, failed: download.failed != nil)" in controls
      and 'chip("Cancel", symbol: "xmark"' in controls and 'chip("Stop download"' in controls
      and 'chip("Keep downloading"' in controls and "store.actions?.cancelDownload(download.id)" in controls,
      "Downloads: every download has its main button and Cancel, and Cancel asks first in the row")
check(controls.index("if stopping == download.id") < controls.index("cancelDownload"),
      "Downloads: Cancel stops the download only after Stop download")
chip = block(downloads, "private func chip(")
check("Button(action: action)" in chip and ".bcdFocusable(id, cornerRadius: 22, action: action)" in chip,
      "Downloads: each button works by touch and by the controller")
primary = block(downloads, "private func primary(_ download: BCDDownload)")
check(".resumes" in primary and "store.actions?.resumeDownload(download.id)" in primary
      and "store.actions?.pauseDownload(download.id)" in primary,
      "Downloads: the main button resumes or tries again, or pauses")
# Resume and Try again go through the live store's download(), which calls SteamOwnedLibrary.install
# at once (tests/host/check-bcd-launch.py pins it; ui-memory-launch, 2026-10-18).
check("download(appID)" in block(store, "func resumeDownload(_ id: String)")
      and "let account = SteamOwnedLibrary.shared" in block(store, "private func download(_ appID: Int, library: String? = nil)")
      and "account.install(appID, library: library)" in block(store, "private func download(_ appID: Int, library: String? = nil)")
      and "SteamOwnedLibrary.shared.cancelInstall(appID, installed:" in block(store, "func cancelDownload(_ id: String)"),
      "the live store resumes, retries and cancels through the classic download sheet's functions")
check("if #available(iOS 26.0, *) { return BCDDownloadNotes.background(continues: true) }" in downloads
      and "Keep BCD open" not in downloads, "Downloads: the note on leaving BCD follows the iOS version")

# ---- 5. links ------------------------------------------------------------------------------
schemes = [s for t in info.get("CFBundleURLTypes", []) for s in t.get("CFBundleURLSchemes", [])]
check(schemes == ["bcd"], "the app answers bcd:// links", str(schemes))
shortcuts = (APP / "SavesAndShortcuts.swift").read_text()
check('c.scheme = "bcd"' in block(shortcuts, "static func link(for windowsPath: String) -> String"),
      "Home Screen links are bcd://play links")
app = (APP / "MadeiraApp.swift").read_text()
check(".onOpenURL { url in if !JITNetworkShortcut.shared.handle(url) { ShortcutRouter.shared.handle(url) } }" in app,
      "links reach the JIT shortcut's callback first, then Home Screen shortcuts")
net = (APP / "JITNetwork.swift").read_text()
check(all(f'"bcd://jit-network/{w}"' in net for w in ("success", "error", "cancel")),
      "the JIT shortcut comes back through bcd://jit-network")

# ---- 6. messages shown in BCD's interface name no classic-only place -----------------------
shown = {"SteamGamesRules.blocker": block(games, "static func blocker(installed: Bool, client: Bool"),
         "SteamDirectStart.blocker": block(games, "static func blocker(installed: Bool, program: String?"),
         "SteamOwnedLibrary's expired sign-in": block(steam, "private func handleSessionError("),
         "auto-tune's result": block(autotune, "var message: String {")}
for name, text in shown.items():
    places = [p for p in ("Settings › Steam", "Game details ›") if p in text]
    check(not places, f"{name}: names no place only the classic interface has", str(places))

# ---- 7. no "Madeira" in what a person reads ------------------------------------------------
spec = importlib.util.spec_from_file_location("bcd_brand", R / "build/tools/bcd_brand.py")
brand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brand)
# Routed: (file, text) -> who changes it. A text that is gone needs no entry; a new one fails.
ROUTED = {
    ("JITNetwork.swift", '"Madeira JIT"'):
        "owner: the Shortcuts shortcut's own name; a renamed one needs a new signed export and iCloud link "
        "(check-jit-network.py holds the three together)",
    ("GameRecommendations.swift", "# Madeira's recommended settings"): "remote-recommendations (the lists' text)",
    ("ContentView.swift", '"madeira-bcd Home"'): "wave 3 (the developer interface's button to the old home screen)",
    ("SteamOwnedLibrary.swift", '"Madeira"'): "never shown: the Application Support folder's name",
    ("EAAPI.swift", '"Madeira (EA client)"'): "never shown: the User-Agent EA's servers see, one of the EA client constants kept as Madeira sends them (check-ea.py)",
    ("AutoTune.swift", "# madeira-bcd auto-tune"): "bcd-file-names (# BCD auto-tune there)",
    ("MadeiraConfig.swift", "# madeira-bcd: the game's own file"): "bcd-file-names (# BCD: there)",
}
found, unrouted = [], []
for f in sorted(APP.rglob("*.swift")):
    if f.name.endswith(".generated.swift"):
        continue
    for line, lit in literals(f.read_text()):
        if "(?" in lit:          # a pattern, never shown
            continue
        product = brand._WORD.search(lit) and not any(k in lit for k in brand._KEEP)
        fork = "madeira-bcd" in lit and " " in lit and not lit.lstrip('"').startswith("[")
        if not (product or fork):
            continue
        routed = [owner for (name, text), owner in ROUTED.items() if name == f.name and text in lit]
        shown = " ".join(lit.split())[:110]
        (found if routed else unrouted).append(f"{f.relative_to(R)}:{line}: {shown}" + (f"  -> {routed[0]}" if routed else ""))
print("Routed, still to change:\n  " + "\n  ".join(found) if found else "Routed: none left")
check(not unrouted, "no \"Madeira\" in a Swift string a person reads (credits and routed items aside)", "\n      ".join(unrouted))
names = {"app": info.get("CFBundleDisplayName"), "app bundle name": info.get("CFBundleName"),
         "local network": info.get("NSLocalNetworkUsageDescription")}
for ext in ("MadeiraJITHelper", "MadeiraMemoryHost"):
    names[ext] = plistlib.loads((R / "app" / ext / "Info.plist").read_bytes()).get("CFBundleDisplayName")
check(all(v and "madeira" not in v.lower() for v in names.values()), "display names and usage texts say BCD", str(names))
bridge = [lit for _, lit in literals((APP / "WineProcessBridge.m").read_text())]
check(any("[steam-env] BCD Dock session" in lit for lit in bridge)
      and not any(brand._WORD.search(lit) for lit in bridge),
      "the bridge's log lines (the live log shows them) say BCD, never Madeira")

ci = R / ".github/workflows/build-ipa.yml"
if ci.exists():
    check("run: python3 tests/host/check-parity.py" in ci.read_text(), "CI runs this check")

# ---- behaviour: a download's main button and the note on leaving BCD -----------------------
SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc")
TESTS = r'''
var failed = 0
func expect(_ cond: Bool, _ what: String) { print((cond ? "PASS: " : "FAIL: ") + what); if !cond { failed += 1 } }
expect(BCDDownloadButton(paused: false, failed: false) == .pause && !BCDDownloadButton.pause.resumes,
       "a running or waiting download pauses")
expect(BCDDownloadButton(paused: true, failed: false) == .resume && BCDDownloadButton.resume.resumes,
       "a paused download resumes")
expect(BCDDownloadButton(paused: false, failed: true) == .tryAgain && BCDDownloadButton(paused: true, failed: true) == .tryAgain
       && BCDDownloadButton.tryAgain.resumes, "a failed download tries again (it goes on where it stopped)")
expect(BCDDownloadButton.pause.title == "Pause" && BCDDownloadButton.resume.title == "Resume"
       && BCDDownloadButton.tryAgain.title == "Try again", "the buttons say Pause, Resume and Try again")
let on = BCDDownloadNotes.background(continues: true), off = BCDDownloadNotes.background(continues: false)
expect(on.contains("in the background") && on.contains("iOS shows its progress") && !on.contains("Keep BCD open"),
       "iOS 26+: a download goes on in the background")
expect(off.contains("pauses") && off.contains("picks up again") && !off.contains("in the background"),
       "before iOS 26: a download pauses after BCD leaves, and picks up again")
expect(on.contains("while a game runs") && off.contains("while a game runs"), "either way, downloads wait while a game runs")
exit(failed == 0 ? 0 : 1)
'''
if not SWIFTC:
    print("SKIP: no swiftc for the Downloads checks")
    if args.require_swift:
        check(False, "swiftc is required (--require-swift)")
else:
    pure = block(downloads, "enum BCDDownloadButton: Equatable") + "\n" + block(downloads, "enum BCDDownloadNotes")
    with tempfile.TemporaryDirectory(prefix="bcd-parity-") as tmp:
        folder = Path(tmp)
        (folder / "main.swift").write_text("import Foundation\n" + pure + "\n" + TESTS)
        exe = folder / "check"
        build = subprocess.run([SWIFTC, "-swift-version", "5", "-o", str(exe), str(folder / "main.swift")],
                               capture_output=True, text=True)
        check(build.returncode == 0, "BCDDownloadButton and BCDDownloadNotes compile alone on the host", build.stderr[-3000:])
        if build.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            sys.stdout.write(run.stdout)
            check(run.returncode == 0, "Downloads: the main button and the note on leaving BCD", run.stderr[-2000:])

print("check-parity:", "FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)
