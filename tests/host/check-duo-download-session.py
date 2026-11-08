#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Exercise the production desktop-state subscriber and Steam hold release offline."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
live = (root / "app/Madeira/BCDLiveStore.swift").read_text()
steam = (root / "app/Madeira/SteamOwnedLibrary.swift").read_text()
view = (root / "app/Madeira/BCDDownloadsView.swift").read_text()

def method(source, signature):
    start = source.index(signature)
    brace = source.index("{", start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]

subscriber = live[live.index("Publishers.CombineLatest4(library.$current"):]
subscriber = subscriber[:subscriber.index(".store(in: &watchers)")] + ".store(in: &watchers)"
stub = r'''
import Foundation
import Combine
enum BCDDuoFeatures { static var active = true }
enum SteamLog { static func event(_ message: String) {} }
@MainActor final class SteamWorkshopModel {
 static let shared = SteamWorkshopModel()
 var stops = 0
 func stopForSession() { stops += 1 }
}
var guestRunning = false
func wine_process_is_running() -> Int32 { guestRunning ? 1 : 0 }
func wineserver_is_running() -> Int32 { guestRunning ? 1 : 0 }
struct Entry { var desktop = true }
@MainActor final class LibraryModel {
 static let shared = LibraryModel()
 static var sessionsThisRun = 0
 @Published var current: UUID?
 @Published var desktopHome = true
 @Published var sessionMessage = ""
 @Published var launching = false
 var activeEntry: Entry? = Entry()
 var desktopClosing = false
 var desktopCloseError: String?
}
@MainActor final class Gate {
 var open = true
 var heldForDock = false
 func close() { open = false }
 func reopen(sessionRunning: Bool) -> Bool {
  guard !open, !sessionRunning, !heldForDock else { return false }
  open = true; return true
 }
}
@MainActor final class SteamOwnedLibrary {
 static let shared = SteamOwnedLibrary()
 static var enabled = true
 var signedIn = true
 var inSession = false
 var gate = Gate()
 var resumeAfterSession = Set<Int>()
 var active: (id: Int, task: Task<Void, Never>)?
 var queue: [Int] = []
 enum State { case paused }
 struct Download { var state = State.paused }
 var downloads: [Int: Download] = [:]
 var started: [Int] = []
 func install(_ id: Int) {
  if inSession { resumeAfterSession.insert(id); downloads[id] = Download() }
  else { started.append(id) }
 }
 func refreshPlaytime() async {}
'''
stub += method(steam, "func sessionChanged(active running:") + "\n"
stub += method(steam, "private var sessionRunning:") + "\n"
stub += method(steam, "func reconcileSession()") + "\n}\n"
stub += r'''
@MainActor final class Store {
 var windowsRunning = false
 var windowsStarting = false
 var windowsClosing = false
 var windowsError: String?
 var windowsNeedsRestart = false
 var actions: Actions? = Actions()
 var stopWindows: (() -> Void)?
}
@MainActor final class Live {
 let store = Store()
 var watchers = Set<AnyCancellable>()
 var inGame = false
 init() {
  let library = LibraryModel.shared, account = SteamOwnedLibrary.shared
'''
stub += subscriber + "\n}\n"
stub += "func session(running: Bool) { inGame = running }\n"
stub += "func settled(_ action: () -> Void) { action() }\n"
stub += method(live, "private func download(_ appID:") + "\n"
stub += "func resume(_ id: Int) { download(id) }\n}\n"
stub += r'''
struct BCDDownload { var id: String; var paused = true; var failed: String? }
struct BCDGameQuestion {
 var title: String; var message: String; var confirm: String
 var destructive: Bool; var defaultCancel = false
}
@MainActor final class Actions {
 var events: [String] = []
 func resumeDownload(_ id: String) { events.append("resume") }
 func pauseDownload(_ id: String) { events.append("pause") }
}
@MainActor final class Menu {
 var question: BCDGameQuestion?
 var run: (() -> Void)?
 func confirm(_ question: BCDGameQuestion, _ run: @escaping () -> Void) {
  self.question = question; self.run = run
 }
 func cancel() { question = nil; run = nil }
}
'''
stub += method(view, "enum BCDDownloadButton:") + "\n"
stub += r'''
@MainActor struct Row {
 let store = Store()
 let menu: Menu? = Menu()
 var nativeControllerNavigation = true
 func press() { primary(BCDDownload(id: "steam.11")) }
'''
stub += method(view, "private func primary(_ download:") + "\n"
stub += method(view, "private var waitingForWindows:") + "\n}\n"
checks = r'''
@main struct Checks {
 @MainActor static func main() {
  let library = LibraryModel.shared, account = SteamOwnedLibrary.shared
  let live = Live()
  func settle() { RunLoop.main.run(until: Date().addingTimeInterval(0.05)) }
  settle()
  guestRunning = true; library.current = UUID(); settle()
  precondition(live.store.windowsRunning && !live.inGame)
  live.resume(11); live.resume(22)
  precondition(account.inSession && account.started.isEmpty)
  precondition(SteamWorkshopModel.shared.stops > 0, "desktop session also pauses Workshop downloads")
  precondition(account.resumeAfterSession == Set([11, 22]))
  // The front end stays mounted and inGame stays false throughout this stop.
  guestRunning = false; library.current = nil; settle()
  precondition(!live.inGame && !live.store.windowsRunning)
  precondition(!account.inSession && account.gate.open)
  precondition(account.started == [11, 22], "desktop stop releases queued jobs without foregrounding")
  library.sessionMessage = "another update"; settle()
  precondition(account.started == [11, 22], "reconciliation cannot restart jobs twice")
  guestRunning = true; library.current = UUID(); settle()
  live.resume(33)
  guestRunning = false; library.current = nil
  live.resume(44) // Before the queued Combine delivery: Resume repairs a stale hold.
  precondition(account.started == [11, 22, 33, 44])
  settle()
  guestRunning = true; library.current = UUID(); settle()
  live.resume(55)
  library.current = nil; settle() // A native guest still running must keep the hold.
  precondition(account.inSession && !account.started.contains(55))
  guestRunning = false; account.reconcileSession()
  precondition(account.started == [11, 22, 33, 44, 55])
  var row = Row()
  row.store.windowsRunning = true
  row.store.windowsStarting = true
  row.store.stopWindows = { row.store.actions?.events.append("stop") }
  row.press()
  precondition(row.menu?.question == nil, "controller cannot request stop during startup")
  row.store.windowsStarting = false
  row.store.windowsClosing = true
  row.press()
  precondition(row.menu?.question == nil, "controller cannot request duplicate shutdown")
  row.store.windowsClosing = false
  row.press()
  precondition(row.menu?.question?.defaultCancel == true)
  precondition(row.store.actions?.events.isEmpty == true, "confirmation does not act before approval")
  row.menu?.cancel()
  precondition(row.store.actions?.events.isEmpty == true, "Cancel leaves Windows and downloads alone")
  row.press(); row.menu?.run?(); row.menu?.cancel()
  precondition(row.store.actions?.events == ["resume", "stop"], "queue before requesting shutdown")
  row.nativeControllerNavigation = false
  row.press()
  precondition(row.menu?.question == nil && row.store.actions?.events == ["resume", "stop", "resume"], "fallback retains original Resume")
  print("PASS: background desktop hold, automatic resume, stale Resume, idempotence and native-guest guard")
  print("PASS: startup/closing guards, Cancel default, cancellation, queue-before-stop and fallback Resume")
 }
}
'''
with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    source, binary = folder / "checks.swift", folder / "checks"
    source.write_text(stub + checks)
    subprocess.run(["swiftc", "-parse-as-library", "-o", str(binary), str(source)], check=True)
    subprocess.run([str(binary)], check=True)
