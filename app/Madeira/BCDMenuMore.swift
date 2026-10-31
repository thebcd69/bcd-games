// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI
import UIKit
import os

// The in-game menu's rows of round 604, in one place so that BCDSessionLive.swift needs a one-line hook per entry point:
//   Game      Pause the game, Windows (a page), Task Manager (a page), "Save these settings for this game"
//   Display   Picture filter, the screen's turn and flip, Magnifier, Frame generation, Cooling when hot
//   Controls  the touch controls' tick, hiding and colour, what a touch that misses them does, phone vibration, the controller chord, Gestures
//   Tools     the live log's pause / clear / export, Wine debug channels for the next start
// Every row's id starts with "mm." (BCDMenuMore.handles). BCDLibraryMenuSource calls `arrange` with the sections it built, and `act`, `toggle`,
// `choose`, `answer` and `page` for the ids that are ours. Nothing here is built until the menu is open; the pages' timers start when a page is
// shown and stop when it is not. Engine-bound: the preview build leaves this file out (tools/ui-preview.py, ENGINE_BOUND).

@MainActor enum BCDMenuMore {
    static func handles(_ row: String) -> Bool { row.hasPrefix("mm.") }

    private static var model: LibraryModel { LibraryModel.shared }
    private static var feel: BCDFeelLive { BCDFeelLive.shared }

    // MARK: Putting the rows among the tab's own

    /// The tab's sections with ours placed among them.
    static func arrange(_ base: [BCDMenuSection], tab: BCDSessionTab) -> [BCDMenuSection] {
        var out = base
        func insert(_ section: BCDMenuSection?, after id: String?) {
            guard let section else { return }
            if let id, let i = out.firstIndex(where: { $0.id == id }) { out.insert(section, at: i + 1) } else { out.append(section) }
        }
        switch tab {
        case .game:
            if BCDPauseLive.paused, let i = out.firstIndex(where: { $0.id == "resume" }), let j = out[i].rows.firstIndex(where: { $0.id == "resume" }) {
                out[i].rows[j].title = "Back to the paused game"   // the game stays paused until "Resume the game"
            }
            insert(sessionSection, after: "quick")
            if let i = out.firstIndex(where: { $0.id == "quit" }) {
                if let profile = profileSection { out.insert(profile, at: i) }
            } else {
                insert(profileSection, after: nil)
            }
        case .display:
            insert(pictureSection, after: "scale")
            insert(frameGenSection, after: nil)
            insert(heatSection, after: nil)
        case .controls:
            insert(touchFeelSection, after: "touch")
            insert(menuSection, after: nil)
        case .tools:
            out = out.map { section in
                guard section.id == "log" else { return section }
                var section = section
                section.rows.insert(BCDMenuRow(id: "mm.dbg",
                                               title: "Wine debug channels",
                                               detail: "Ask for more detail in the log at the next start, once.",
                                               symbol: "ladybug",
                                               kind: .page("mm.debug", value: BCDDebugLive.plan.isEmpty ? nil : "Armed")), at: min(1, section.rows.count))
                return section
            }
        }
        return out
    }

    // MARK: Game

    private static var sessionSection: BCDMenuSection {
        var rows: [BCDMenuRow] = []
        let paused = BCDPauseLive.paused
        let drawing = BCDPauseLive.gameIsDrawing()
        let waiting = BCDPauseLive.waiting
        let detail: String
        if paused {
            if waiting > 0 { detail = "The game stands still. The sound is off." }
            else if Date().timeIntervalSince(BCDPauseLive.pausedAt) > 3 { detail = "No frame has reached the gate in 3 seconds: this program does not draw with Direct3D, or it is loading. The sound is off; Resume lets it go." }
            else { detail = "Waiting for the game's next frame to stop it…" }
        } else if drawing {
            detail = "Stops the game at its next frame and silences it. The picture stays. Windows' clocks keep running, so a game may notice the wait."
        } else {
            detail = "Nothing is being drawn with Direct3D now, so there is no frame to stop the game at."
        }
        rows.append(BCDMenuRow(id: "mm.pause",
                               title: paused ? "Resume the game" : "Pause the game",
                               detail: detail,
                               symbol: paused ? "play.circle" : "pause.circle",
                               kind: .action(),
                               enabled: paused || drawing))
        rows.append(BCDMenuRow(id: "mm.windows",
                               title: "Windows",
                               detail: "What Windows has open: bring one to the front" + (BCDWindowsLive.shared.desktopSession ? ", or show the desktop." : "."),
                               symbol: "macwindow",
                               kind: .page("mm.windows", value: nil)))
        rows.append(BCDMenuRow(id: "mm.tasks",
                               title: "Task Manager",
                               detail: "The programs the game started, how long they ran, and how much memory BCD has left.",
                               symbol: "list.bullet.rectangle",
                               kind: .page("mm.tasks", value: nil)))
        return BCDMenuSection(id: "mm.session", rows: rows)
    }

    private static var profileSection: BCDMenuSection? {
        guard model.activeEntry != nil else { return nil }
        var rows = [BCDMenuRow(id: "mm.save",
                               title: "Save these settings for this game",
                               detail: "Keeps what you changed in this menu — touch controls, picture, pointer, eco — and uses it every time this game starts. The frame-rate limit, scaling, HUD and controls are kept for the game already.",
                               symbol: "square.and.arrow.down",
                               kind: .action())]
        if let note = saveNote { rows.append(BCDMenuRow(id: "mm.saved.now", title: note, symbol: "checkmark.circle", kind: .note(.good))) }
        let words = feel.saved.words
        if !words.isEmpty {
            rows.append(BCDMenuRow(id: "mm.saved", title: "Saved: " + words.joined(separator: ", ") + ".", kind: .note(.good)))
            rows.append(BCDMenuRow(id: "mm.forget",
                                   title: "Forget this game's saved settings",
                                   symbol: "arrow.uturn.backward",
                                   kind: .action(destructive: true),
                                   confirm: BCDConfirm(title: "Forget the saved settings?",
                                                       message: "This game goes back to the defaults of this menu the next time it starts, and now.",
                                                       answers: [.init(id: "forget", title: "Forget", destructive: true)])))
        }
        return BCDMenuSection(id: "mm.profile", title: "This game", rows: rows)
    }

    // MARK: Display

    private static var pictureSection: BCDMenuSection {
        let current = feel.current
        let turn = BCDOrientationLock.shared.choice
        var rows: [BCDMenuRow] = []
        rows.append(BCDMenuRow(id: "mm.filter",
                               title: "Picture filter",
                               detail: "Smooth blends the pixels when the picture is enlarged; Sharp keeps them square, best for old games drawn small.",
                               kind: .choice(BCDPictureFilter.choices.map { BCDChoice(id: $0.id, title: $0.title) }, selected: current.filter ?? "")))
        let turns = [BCDOrientationChoice.free, .landscape, .landscapeCameraLeft, .landscapeCameraRight]
        rows.append(BCDMenuRow(id: "mm.turn",
                               title: "Screen turn",
                               detail: "Whether the screen follows the way you hold the device, or stays in landscape with the camera on one side.",
                               kind: .choice(turns.map { BCDChoice(id: $0.rawValue, title: Self.turnWords($0)) }, selected: turn.rawValue)))
        rows.append(BCDMenuRow(id: "mm.flip",
                               title: "Flip the screen",
                               detail: "Turns the picture upside down: camera to the other side. Handy when the device lies flat.",
                               symbol: "arrow.triangle.2.circlepath",
                               kind: .action()))
        let zoom = BCDMagnifierState.nearestZoom(BCDMagnifierCurrent.state.zoom)
        rows.append(BCDMenuRow(id: "mm.mag",
                               title: "Magnifier",
                               detail: "Enlarges the picture to read small text. A bar over the game moves the view; touches still land on what is under the finger.",
                               kind: .choice(BCDMagnifierState.zooms.map { BCDChoice(id: String(format: "%g", $0), title: $0 == 1 ? "Off" : String(format: "%g×", $0)) },
                                             selected: String(format: "%g", zoom))))
        return BCDMenuSection(id: "mm.picture", title: "Picture", rows: rows)
    }

    private static func turnWords(_ choice: BCDOrientationChoice) -> String {
        switch choice {
        case .free: return "Follow the device"
        case .landscape: return "Landscape"
        case .landscapeCameraLeft: return "Camera left"
        case .landscapeCameraRight: return "Camera right"
        }
    }

    private static var frameGenSection: BCDMenuSection? {
        guard #available(iOS 26.0, *), let entry = model.activeEntry, entry.desktop != true else { return nil }
        let wanted = GameProfile(windowsPath: entry.windowsPath).get("env.MADEIRA_FRAMEGEN") == "1"
        let running = ProcessInfo.processInfo.environment["MADEIRA_FRAMEGEN"].map { $0 != "0" && !$0.isEmpty } ?? false
        var rows = [BCDMenuRow(id: "mm.fgen",
                               title: "Frame generation",
                               detail: "MetalFX makes up a frame between every two the game draws: twice the frames, for GPU time, a little delay and some smearing at edges and the HUD. It is read when the game starts. Turn it off if the game runs below about 40 FPS without it: it waits for screen buffers and can hold such a game at 20 FPS.",
                               kind: .choice([BCDChoice(id: "", title: "Off"), BCDChoice(id: "1", title: "MetalFX 2×")], selected: wanted ? "1" : ""))]
        if wanted != running {
            rows.append(BCDMenuRow(id: "mm.fgen.note", title: "It applies the next time this game starts.", symbol: "arrow.clockwise.circle", kind: .note(.warm)))
        } else if running {
            rows.append(BCDMenuRow(id: "mm.fgen.note", title: "On in this session. The frame-rate limit is ignored while it is on.", kind: .note(.soft)))
        }
        return BCDMenuSection(id: "mm.fgen", title: "Frame generation", rows: rows)
    }

    private static var heatSection: BCDMenuSection {
        let on = ThermalPacing.enabled
        var rows = [BCDMenuRow(id: "mm.heat", title: "Cool the game when the phone is hot",
                               detail: "While iOS says the phone is hot, the game is held at 40 frames a second (30 where 40 is not offered): an even rate runs cooler than a 60 that misses. It goes back to your limit when the phone is cool, and a limit you choose here takes over.",
                               kind: .choice([BCDChoice(id: "", title: "Off"), BCDChoice(id: "1", title: "When hot")], selected: on ? "1" : ""))]
        if let held = ThermalPacing.shared.heldMode {
            rows.append(BCDMenuRow(id: "mm.heat.note", title: "The phone is hot, so the game is held at \(held == 3 ? "30" : "40") frames a second for now.", symbol: "thermometer.medium", kind: .note(.warm)))
        }
        return BCDMenuSection(id: "mm.heat", title: "Heat", rows: rows)
    }

    // MARK: Controls

    private static var touchFeelSection: BCDMenuSection {
        let current = feel.current
        var rows: [BCDMenuRow] = []
        if TouchControlsModel.shared.visible {
            rows.append(BCDMenuRow(id: "mm.tick",
                                   title: "Tick on touch",
                                   detail: "The light tap you feel when you press a control.",
                                   kind: .toggle(current.touchHaptics ?? true)))
            rows.append(BCDMenuRow(id: "mm.hide",
                                   title: "Hide the controls after",
                                   detail: "With no finger on a control for this long, the controls go; a touch on the game brings them back.",
                                   kind: .choice(BCDHideAfter.choices.map { BCDChoice(id: $0.id, title: $0.title) }, selected: String(current.hideAfter ?? 0))))
            rows.append(BCDMenuRow(id: "mm.color",
                                                                                  title: "Colour",
                                                                                  kind: .choice(BCDControlColor.choices.map { BCDChoice(id: $0.id, title: $0.title) },
                                                                                  selected: current.controlColor ?? "")))
        }
        rows.append(BCDMenuRow(id: "mm.touchmouse",
                               title: "A touch that misses the controls",
                               detail: "Automatic: it moves the mouse, except while the on-screen controller is shown. Never keeps the fingers for the controls alone.",
                               kind: .choice(BCDTouchMouse.choices.map { BCDChoice(id: $0.id, title: $0.title) }, selected: current.touchMouse ?? "")))
        if bcd_phone_rumble_available() != 0 {
            rows.append(BCDMenuRow(id: "mm.vibe",
                                   title: "Phone vibration",
                                   detail: "The phone shakes with the game's rumble while the on-screen controller is player 1. A paired controller rumbles by itself.",
                                   kind: .toggle(current.phoneVibration == true)))
        }
        return BCDMenuSection(id: "mm.touchfeel", title: "Touch & rumble", rows: rows)
    }

    private static var menuSection: BCDMenuSection {
        let chord = BCDFeelLive.chord
        var rows = [BCDMenuRow(id: "mm.chord",
                               title: "Open this menu from a controller with",
                               detail: "Back + Start always works. Hold Start opens it after a moment; hold Home (the Guide button) takes the button from iOS for this session.",
                               kind: .choice(BCDMenuChord.choices.map { BCDChoice(id: $0.id, title: $0.title) }, selected: chord.rawValue))]
        if chord == .holdGuide && !SystemGestures.taken {
            rows.append(BCDMenuRow(id: "mm.chord.note",
                                   title: "iOS has not handed the Home button over (no controller, or the controller is not paired yet); it is handed over when one is.",
                                   symbol: "gamecontroller",
                                   kind: .note(.warm)))
        }
        rows.append(BCDMenuRow(id: "mm.gest",
                               title: "Gestures",
                               detail: "What a finger, two fingers and the controller do.",
                               symbol: "hand.tap",
                               kind: .page("mm.gestures", value: nil)))
        return BCDMenuSection(id: "mm.menu", title: "Menu & gestures", rows: rows)
    }

    // MARK: Pages

    static func page(_ id: String) -> BCDMenuPage? {
        switch id {
        case "mm.windows": return windowsPage
        case "mm.tasks": return tasksPage
        case "mm.gestures": return gesturesPage
        case "mm.debug": return debugPage
        default: return nil
        }
    }

    private static var windowsPage: BCDMenuPage {
        let live = BCDWindowsLive.shared
        let shown = BCDWindowRules.ordered(BCDWindowRules.shown(live.windows))
        var sections: [BCDMenuSection] = []
        if live.desktopSession {
            var rows = [BCDMenuRow(id: "mm.win.desktop",
                                   title: "Show the desktop",
                                   detail: "Minimizes the open windows. Bring windows back puts them up again.",
                                   symbol: "rectangle.on.rectangle.slash",
                                   kind: .action())]
            if live.hasMinimizedByUs {
                rows.append(BCDMenuRow(id: "mm.win.back", title: "Bring the windows back", symbol: "arrow.uturn.backward", kind: .action()))
            }
            sections.append(BCDMenuSection(id: "mm.win.actions", rows: rows))
        }
        if shown.isEmpty {
            sections.append(BCDMenuSection(id: "mm.win.none", rows: [BCDMenuRow(id: "mm.win.empty", title: "Looking for windows…", kind: .note(.soft))]))
        } else {
            sections.append(BCDMenuSection(id: "mm.win.list", title: "Open", rows: shown.map { window in
                BCDMenuRow(id: BCDWindowRules.rowID(window),
                           title: BCDWindowRules.name(window),
                           detail: BCDWindowRules.detail(window),
                           symbol: window.foreground ? "macwindow.badge.plus" : (window.minimized ? "macwindow.on.rectangle" : "macwindow"),
                           kind: .action(),
                           picture: live.thumbnails[window.hwnd])
            }))
        }
        sections.append(BCDMenuSection(id: "mm.win.note", rows: [
            BCDMenuRow(id: "mm.win.help", title: "Tap a window to bring it to the front. The list refreshes while this page is open; a picture shows for windows Windows draws itself (not for a game's Direct3D window).", kind: .note(.soft)),
        ]))
        return BCDMenuPage(id: "mm.windows", title: "Windows", sections: sections)
    }

    private static var tasksPage: BCDMenuPage {
        var buffer = [CChar](repeating: 0, count: 8192)
        _ = madeira_child_report(&buffer, Int32(buffer.count))
        let programs = BCDProgramsRules.parse(String(cString: buffer))
        let running = programs.filter(\.running), ended = programs.filter { !$0.running }
        var sections: [BCDMenuSection] = []
        let title = model.activeEntry.map { $0.desktop == true ? "Windows desktop" : $0.title } ?? "The game"
        sections.append(BCDMenuSection(id: "mm.task.main", title: "This session", rows: [
            BCDMenuRow(id: "mm.task.game",
                       title: title,
                       detail: "the program BCD started · running for " + BCDProgramsRules.duration(Date().timeIntervalSince(model.launchStartedAt)),
                       symbol: "gamecontroller",
                       kind: .note(.normal)),
        ]))
        if running.isEmpty {
            sections.append(BCDMenuSection(id: "mm.task.none", title: "Programs it started", rows: [
                BCDMenuRow(id: "mm.task.empty", title: "None running.", kind: .note(.soft))]))
        } else {
            sections.append(BCDMenuSection(id: "mm.task.run", title: "Programs it started", rows: running.map { program in
                BCDMenuRow(id: "mm.task.r." + program.path,
                           title: program.name.isEmpty ? "A program" : program.name,
                           detail: BCDProgramsRules.detail(program),
                           symbol: program.helper ? "wrench.and.screwdriver" : "app",
                           kind: .note(.normal))
            }))
        }
        if !ended.isEmpty {
            sections.append(BCDMenuSection(id: "mm.task.ended", title: "Ended lately", rows: ended.suffix(6).map { program in
                BCDMenuRow(id: "mm.task.e." + program.path + String(program.seconds),
                           title: program.name.isEmpty ? "A program" : program.name,
                           detail: BCDProgramsRules.detail(program),
                           symbol: "checkmark.circle",
                           kind: .note(program.status == 0 ? .soft : .warm))
            }))
        }
        var memory = [BCDMenuRow]()
        var used: UInt64 = 0, total: UInt64 = 0
        madeira_hud_jit_pool(&used, &total)
        if total > 0 {
            memory.append(BCDMenuRow(id: "mm.task.jit",
                                     title: String(format: "JIT pool: %d of %d MB", Int(used >> 20), Int(total >> 20)),
                                     detail: "The memory the emulator keeps for translated code.",
                                     symbol: "memorychip",
                                     kind: .note(.normal)))
        }
        let swap = madeira_hud_swap_bytes()
        if swap > 0 {
            memory.append(BCDMenuRow(id: "mm.task.swap",
                                     title: String(format: "Swapped out: %d MB", Int(swap >> 20)),
                                     detail: "Game data BCD moved to the device's storage to keep memory free.",
                                     symbol: "externaldrive",
                                     kind: .note(.normal)))
        }
        memory.append(BCDMenuRow(id: "mm.task.mem", title: "Memory: " + BCDMenuMore.memoryWords(), symbol: "gauge.with.dots.needle.33percent", kind: .note(.normal)))
        sections.append(BCDMenuSection(id: "mm.task.memory", title: "Memory", rows: memory))
        return BCDMenuPage(id: "mm.tasks", title: "Task Manager", sections: sections)
    }

    /// What iOS has left before it closes the app, in words.
    private static func memoryWords() -> String {
        let free = os_proc_available_memory()
        guard free > 0 else { return "not known" }
        return String(format: "%d MB left before iOS closes BCD", Int(free >> 20))
    }

    private static var gesturesPage: BCDMenuPage {
        let input = InputSettings.shared
        let pointer = input.touchMode ? "touch" : (input.relative ? "relative" : "absolute")
        let desktop = BCDWindowsLive.shared.desktopSession
        let sections = BCDGestureHelp.sections(pointer: pointer, desktop: desktop, touchControls: TouchControlsModel.shared.visible,
                                               chord: BCDFeelLive.chord)
        return BCDMenuPage(id: "mm.gestures", title: "Gestures", sections: sections.enumerated().map { index, section in
            BCDMenuSection(id: "mm.gest.\(index)", title: section.title, rows: section.lines.enumerated().map { line, text in
                BCDMenuRow(id: "mm.gest.\(index).\(line)", title: text, kind: .note(.soft))
            })
        })
    }

    private static var debugPage: BCDMenuPage {
        BCDDebugLive.reconcile()
        let plan = BCDDebugLive.plan
        var sections: [BCDMenuSection] = []
        var intro = [BCDMenuRow(id: "mm.dbg.intro",
                                title: "Pick what you are chasing. The next game you start writes that detail into its log, once; after that, starts are as always. Send Log then carries it.",
                                kind: .note(.soft))]
        if let running = BCDDebugLive.runningWithPlan {
            intro.append(BCDMenuRow(id: "mm.dbg.running", title: "This session runs with: " + running, kind: .note(.good)))
        }
        if !plan.isEmpty {
            intro.append(BCDMenuRow(id: "mm.dbg.armed",
                                    title: "Armed for the next start." + (BCDDebugChannels.anyHeavy(plan.presets) ? " This writes a lot; the log grows fast and the game may run slower." : ""),
                                    symbol: "bolt.badge.clock",
                                    kind: .note(.warm)))
        }
        sections.append(BCDMenuSection(id: "mm.dbg.top", rows: intro))
        sections.append(BCDMenuSection(id: "mm.dbg.presets", title: "What is wrong", rows: BCDDebugChannels.presets.map { preset in
            BCDMenuRow(id: "mm.dbg.p." + preset.id, title: preset.title, detail: preset.detail + (preset.heavy ? " (writes a lot)" : ""), kind: .toggle(plan.presets.contains(preset.id)))
        }))
        sections.append(BCDMenuSection(id: "mm.dbg.more", rows: [
            BCDMenuRow(id: "mm.dbg.timestamps", title: "Time stamps", detail: "Every detail line starts with the time since the start.", kind: .toggle(plan.timestamps)),
            BCDMenuRow(id: "mm.dbg.clear", title: "Clear", symbol: "xmark.circle", kind: .action(), enabled: !plan.isEmpty),
        ]))
        return BCDMenuPage(id: "mm.debug", title: "Wine debug channels", sections: sections)
    }

    /// A page was opened (its id) or closed (nil): the Windows page keeps a list while it is open, the Task Manager page a refresh.
    static func pageShown(_ id: String?, refresh: @escaping () -> Void) {
        let windows = BCDWindowsLive.shared
        if id == "mm.windows" {
            windows.changed = refresh
            windows.start()
        } else {
            windows.changed = nil
            windows.stop()
        }
        taskTimer?.invalidate()
        taskTimer = nil
        if id == "mm.tasks" {
            let t = Timer(timeInterval: 2, repeats: true) { _ in MainActor.assumeIsolated { refresh() } }
            RunLoop.main.add(t, forMode: .common)
            taskTimer = t
        }
    }
    private static var taskTimer: Timer?

    /// What the last "Save these settings" did, shown under it until the menu closes.
    private static var saveNote: String?

    /// The menu closed: the page timers stop.
    static func menuClosed() {
        pageShown(nil, refresh: {})
        saveNote = nil
        logFrozen = nil   // the log page starts live and whole the next time the menu opens
        logCleared = nil
    }

    // MARK: Doing

    static func act(_ row: String, refresh: @escaping () -> Void) {
        if let hwnd = BCDWindowRules.hwnd(ofRow: row) {
            BCDWindowsLive.shared.bringToFront(hwnd)
            return
        }
        switch row {
        case "mm.pause":
            if BCDPauseLive.paused {
                BCDPauseLive.resume()
                if model.sessionMessage.hasPrefix("Paused") { model.sessionMessage = "" }
            } else {
                BCDPauseLive.pause()
                model.sessionMessage = "Paused. Open the menu and choose Resume the game."
                // The row says when the gate has the game and when it has not: draw it again as that happens.
                for delay in [0.3, 1.0, 3.5] {
                    DispatchQueue.main.asyncAfter(deadline: .now() + delay) { if BCDPauseLive.paused { refresh() } }
                }
            }
        case "mm.flip": flipScreen()
        case "mm.save":
            let words = feel.saveForGame()
            saveNote = words.isEmpty ? "Nothing to save: every setting is as it always was." : "Saved for this game: " + words.joined(separator: ", ") + "."
        case "mm.win.desktop": BCDWindowsLive.shared.showDesktop()
        case "mm.win.back": BCDWindowsLive.shared.bringBack()
        case "mm.log.clear": logClear()
        case "mm.dbg.clear": BCDDebugLive.plan = BCDDebugPlan()
        default: break
        }
        refresh()
    }

    static func toggle(_ row: String, _ on: Bool, refresh: @escaping () -> Void) {
        switch row {
        case "mm.log.pause": logToggle(on)
        case "mm.tick": feel.update { $0.touchHaptics = on ? nil : false }
        case "mm.vibe": feel.update { $0.phoneVibration = on ? true : nil }
        case "mm.dbg.timestamps":
            var plan = BCDDebugLive.plan
            plan.timestamps = on
            BCDDebugLive.plan = plan
        default:
            if row.hasPrefix("mm.dbg.p.") {
                let id = String(row.dropFirst("mm.dbg.p.".count))
                guard BCDDebugChannels.preset(id) != nil else { return }
                var plan = BCDDebugLive.plan
                if on { plan.presets.insert(id) } else { plan.presets.remove(id) }
                BCDDebugLive.plan = plan
            }
        }
        refresh()
    }

    static func choose(_ row: String, _ value: String, refresh: @escaping () -> Void) {
        switch row {
        case "mm.filter": feel.update { $0.filter = value.isEmpty ? nil : value }
        case "mm.turn":
            if let choice = BCDOrientationChoice(rawValue: value) {
                BCDOrientationLock.shared.onLog = { LogStore.shared.log($0) }
                BCDOrientationLock.shared.apply(choice)
            }
        case "mm.mag":
            if let zoom = Double(value) { BCDMagnifierLive.shared.setZoom(zoom) }
        case "mm.fgen":
            if #available(iOS 26.0, *) {
                if let entry = model.activeEntry {
                    GameProfile(windowsPath: entry.windowsPath).set("env.MADEIRA_FRAMEGEN", value.isEmpty ? nil : value)
                    LogStore.shared.log("[feel] frame generation \(value.isEmpty ? "off" : "on") from the next start")
                }
            }
        case "mm.heat": setHeat(!value.isEmpty)
        case "mm.hide": feel.update { $0.hideAfter = Int(value).flatMap { $0 == 0 ? nil : $0 } }
        case "mm.color": feel.update { $0.controlColor = value.isEmpty ? nil : value }
        case "mm.touchmouse": feel.update { $0.touchMouse = value.isEmpty ? nil : value }
        case "mm.chord":
            if let chord = BCDMenuChord(rawValue: value) {
                BCDFeelLive.chord = chord
                // Hold Home takes the Home button from iOS for the rest of this session at once (SystemGestures gives it back when the session ends).
                if chord == .holdGuide && model.current != nil { SystemGestures.beginSession(enabled: GamepadInput.enabled) }
            }
        default: break
        }
        refresh()
    }

    static func answer(_ row: String, _ answer: String, refresh: @escaping () -> Void) {
        if row == "mm.forget", answer == "forget" { feel.forgetForGame() }
        refresh()
    }

    /// "Cool the game when the phone is hot": the game's own env.MADEIRA_THERMAL_CAP line (the switch ThermalPacing reads at a session's start, so
    /// the game keeps it) and the session's environment, so that it applies at once.
    private static func setHeat(_ on: Bool) {
        if let entry = model.activeEntry {
            GameProfile(windowsPath: entry.windowsPath).set("env.MADEIRA_THERMAL_CAP", on ? "1" : nil)
        }
        setenv("MADEIRA_THERMAL_CAP", on ? "1" : "0", 1)
        heatSetHere = true
        if on { ThermalPacing.shared.arm() } else { ThermalPacing.shared.disarm() }
    }
    /// This menu put a value in the session's environment: the session's end takes it out again (a game's own line is exported anew at its start).
    private static var heatSetHere = false

    /// Turns the picture upside down: the camera goes to the other side. The screen is locked to landscape for it (a flat device would
    /// otherwise turn back at once), and goes back to following the device when the session ends.
    private static func flipScreen() {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        let side: BCDInterfaceSide
        switch scene?.interfaceOrientation {
        case .landscapeRight?: side = .landscapeRight
        case .landscapeLeft?: side = .landscapeLeft
        default: side = .other
        }
        let next = BCDOrientationFlip.next(from: side)
        BCDOrientationLock.shared.onLog = { LogStore.shared.log($0) }
        BCDOrientationLock.shared.apply(next)
    }

    // MARK: The live log page

    /// The log page's rows: pause the view, clear it, and export the log.
    static func logPage() -> BCDMenuPage {
        BCDMenuPage(id: "log", title: "Live log", kind: .log, sections: [
            BCDMenuSection(id: "mm.log.actions", rows: [
                BCDMenuRow(id: "mm.log.pause", title: "Pause the view", detail: "Keeps the lines as they are; the log itself goes on.", kind: .toggle(logPaused)),
                BCDMenuRow(id: "mm.log.clear", title: "Clear the view", detail: "Shows only what comes next. The log file is not touched.", symbol: "xmark.circle", kind: .action()),
                BCDMenuRow(id: "mm.log.share", title: "Export the log", kind: .share(LogStore.shared.exportableLogs)),
            ]),
        ])
    }

    private static var logFrozen: [BCDLogLine]?
    private static var logCleared: Date?
    private static var logPaused: Bool { logFrozen != nil }

    static func logToggle(_ on: Bool) {
        logFrozen = on ? current(120) : nil
    }

    static func logClear() {
        logCleared = Date()
        if logFrozen != nil { logFrozen = [] }
    }

    /// The lines the log page shows: the frozen ones while paused, else the newest after the place the view was cleared.
    static func logLines() -> [BCDLogLine] {
        logFrozen ?? current(120)
    }

    private static func current(_ count: Int) -> [BCDLogLine] {
        let cleared = logCleared
        let entries = LogStore.shared.entries.filter { cleared == nil || $0.lastTimestamp > cleared! }
            .sorted { $0.lastTimestamp < $1.lastTimestamp }.suffix(count)
        return entries.enumerated().map { index, entry in
            BCDLogLine(id: index, text: entry.lastRaw.trimmingCharacters(in: .whitespacesAndNewlines), error: entry.level == .error)
        }
    }

    /// A session ended: the log page starts fresh.
    static func sessionEnded() {
        menuClosed()
        if heatSetHere { unsetenv("MADEIRA_THERMAL_CAP"); heatSetHere = false }
    }
}
