// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation
import SwiftUI

// Epic Games in BCD, milestone 2 (docs/research/open-fork-2026-10-18/epic.md §4.4 step "Play", §4.5, §4.9): an
// installed Epic game starts signed in. Right before the session BCD asks Epic for the one-time exchange code (and,
// for games whose copy protection asks, an ownership token), writes them where only BCD Host reads them, and
// starts bcdhost.exe in place of the game; the host starts the game with Epic's launcher arguments, waits for its
// whole process tree, deletes the token file and reports numbers. Engine-bound: ContentView reaches it through
// two one-line hooks (`preLaunch` before the start, `applyLaunch` after the launch profile is set) and the
// front end shows its question through BCDEpicPromptAlert.
//
// Only an Epic game BCD installed is ever touched: every entry point starts with the entry's install record, and
// a start of any other entry changes nothing (not the environment, not the arguments). Log tags: [epic-launch]
// (steps, statuses, timings and short reasons), [bcd-host] (the host's numeric report) and [epic-audit] (what the
// search after the session found). Never a code, an ID, a name or a token.

/// What the front end shows when a signed-in start could not be made.
@MainActor final class BCDEpicPromptModel: ObservableObject {
    static let shared = BCDEpicPromptModel()
    @Published var ask: BCDAsk?
    private init() {}
}

extension BCDEpic {
    static let hostProgram = "C:\\windows\\system32\\bcdhost.exe"

    /// A switch for one game: its own config line (its settings file, then the entry's own lines), else bcd.cfg's, else on.
    static func flag(_ name: String, entry: LibraryEntry, fallback: Bool = true) -> Bool {
        let key = "env." + name
        if let value = GameProfile(windowsPath: entry.windowsPath).get(key) { return value != "0" }
        if let value = MadeiraConfig.parse(entry.gameConfigText ?? "")[key], !value.isEmpty { return value != "0" }
        return MadeiraConfig.flag(name, fallback: fallback)
    }

    func record(forEntry id: UUID) -> BCDEpicInstallRecord? { installs.values.first { $0.entryID == id } }

    /// What decides how an entry starts (BCDEpicSignedLaunch.decide).
    func startFacts(_ entry: LibraryEntry, record: BCDEpicInstallRecord, offlineChosen: Bool) -> BCDEpicSignedLaunch.Facts {
        var facts = BCDEpicSignedLaunch.Facts()
        facts.signInSwitch = Self.flag("BCD_EPIC_SIGNIN", entry: entry)
        facts.hostSwitch = Self.flag("BCD_EPIC_HOST", entry: entry)
        facts.hostAvailable = Self.hostAvailable
        facts.signedIn = signedIn
        facts.hostCannotStart = entry.desktop == true || entry.isBatch || entry.steamAppID != nil || !entry.launchRelativePath.lowercased().hasSuffix(".exe")
        facts.needsOnline = needsOwnershipToken(record) || (item(record.app)?.canRunOffline ?? record.canRunOffline) == false
        facts.offlineChosen = offlineChosen
        return facts
    }

    func needsOwnershipToken(_ record: BCDEpicInstallRecord) -> Bool { (item(record.app)?.ownershipToken ?? record.ownershipToken) == true }

    // MARK: Once per run

    /// Called from `start()`: leftovers of a run that ended before its clean-up (a request, a token file, a report) go, and the
    /// end of a session is watched.
    func startSignedStarts() {
        let drive = LibraryModel.drive
        if let report = BCDEpicHostFiles.readReport(drive: drive) { BCDEpicLog.write("[bcd-host] last run \(report.logText)") }
        let removed = BCDEpicHostFiles.cleanup(base: Self.folder, drive: drive, report: true)
        if removed > 0 { BCDEpicLog.write("[epic-launch] leftovers removed files=\(removed)") }
        unsetenv("BCD_HOST_REQUEST")
        sessionWatch = LibraryModel.shared.$current.removeDuplicates().sink { [weak self] current in
            guard let self, current == nil, self.hostRan else { return }
            // Next turn: `finish()` is still setting the session's state when this fires.
            DispatchQueue.main.async { self.sessionEnded() }
        }
    }

    /// Sign-out: nothing of a start stays.
    func endSignedStarts() {
        ticket = nil
        offlineOnce = nil
        auditSecrets = nil
        BCDEpicHostFiles.cleanup(base: Self.folder, drive: LibraryModel.drive, report: false)
        unsetenv("BCD_HOST_REQUEST")
    }

    // MARK: Before the start (hook A)

    /// True when the start may go on now. For an Epic game that starts signed in, false: BCD asks Epic for the code first and
    /// calls `retry` (the same start again) when it has it, or asks the player what to do when it cannot.
    func preLaunch(_ entry: LibraryEntry, retry: @escaping () -> Void) -> Bool {
        guard let record = record(forEntry: entry.id) else { return true }
        if let held = ticket, held.entry == entry.id, held.credentials.valid(now: Date()) { return true }
        if starting != nil { return false }
        switch BCDEpicSignedLaunch.decide(startFacts(entry, record: record, offlineChosen: offlineOnce == entry.id)) {
        case .publicOnly:
            return true
        case .needsSignIn:
            BCDEpicLog.write("[epic-launch] start refused app=\(BCDEpicLog.app(record.app)) reason=signed-out")
            ask(.needsSignIn(game: record.title), entry: entry, retry: retry)
            return false
        case .signed:
            sign(entry, record: record, retry: retry)
            return false
        }
    }

    private func sign(_ entry: LibraryEntry, record: BCDEpicInstallRecord, retry: @escaping () -> Void) {
        starting = entry.id
        let token = needsOwnershipToken(record)
        let api = self.api
        let namespace = record.namespace, catalogItem = record.catalogItemId
        BCDEpicLog.write("[epic-launch] signing in app=\(BCDEpicLog.app(record.app)) ownership=\(token ? 1 : 0)")
        Task { @MainActor in
            do {
                let credentials = try await api.credentials(namespace: namespace, catalogItemId: catalogItem, needsOwnership: token)
                starting = nil
                guard LibraryModel.shared.current == nil, wine_process_is_running() == 0, wineserver_is_running() == 0 else {
                    BCDEpicLog.write("[epic-launch] start dropped reason=session-running")
                    return
                }
                ticket = (entry.id, credentials)
                BCDEpicLog.write("[epic-launch] signed in app=\(BCDEpicLog.app(record.app)) ownership=\(credentials.ownership == nil ? 0 : 1)")
                retry()
            } catch {
                starting = nil
                let failure = BCDEpicAPI.failure(from: error)
                BCDEpicLog.write("[epic-launch] sign-in failed app=\(BCDEpicLog.app(record.app)) reason=\(failure.logReason)")
                if failure == .signedOut, await session.current() == nil { clearAccount() }
                let offlineOK = BCDEpicSignedLaunch.offlineAllowed(canRunOffline: item(record.app)?.canRunOffline ?? record.canRunOffline, ownershipToken: token)
                ask(.make(for: failure, offlineAllowed: offlineOK, game: record.title), entry: entry, retry: retry)
            }
        }
    }

    // MARK: The question

    private func ask(_ prompt: BCDEpicPrompt, entry: LibraryEntry, retry: @escaping () -> Void) {
        promptRetry = retry
        promptEntry = entry.id
        BCDEpicPromptModel.shared.ask = BCDAsk(title: prompt.title, message: prompt.message,
                                               answers: prompt.answers.map { BCDAsk.Answer(id: $0.id, title: $0.title, cancel: $0.cancel) })
    }

    /// The player's answer to the question: another try, a start without Epic's sign-in, or nothing.
    func answer(_ id: String) {
        let retry = promptRetry, entry = promptEntry
        promptRetry = nil
        promptEntry = nil
        BCDEpicPromptModel.shared.ask = nil
        guard let retry, let entry, id == "retry" || id == "offline" else { return }
        guard LibraryModel.shared.current == nil, wine_process_is_running() == 0, wineserver_is_running() == 0 else { return }
        if id == "offline" {
            offlineOnce = entry
            BCDEpicLog.write("[epic-launch] play offline chosen")
        }
        retry()
    }

    // MARK: After the launch profile is set (hook B)

    /// Right after `LibraryEntry.configureLaunch`: for an Epic game with a ticket, the program the session starts becomes BCD Host, which
    /// reads the request written here; for any other start nothing changes (and a request path left in the environment goes).
    func applyLaunch(_ entry: LibraryEntry) {
        unsetenv("BCD_HOST_REQUEST")
        LibraryEntry.clearEpicHostStart()   // a hold of an earlier start never reaches this one
        guard let record = record(forEntry: entry.id) else { return }
        let held = ticket
        ticket = nil
        let offline = offlineOnce == entry.id
        offlineOnce = nil
        let name = BCDEpicLog.app(record.app)
        guard let held, held.entry == entry.id, held.credentials.valid(now: Date()) else {
            var reason = "no-ticket"
            if case .publicOnly(let why) = BCDEpicSignedLaunch.decide(startFacts(entry, record: record, offlineChosen: offline)) { reason = why }
            BCDEpicLog.write("[epic-launch] start app=\(name) signed-in=0 reason=\(BCDEpicRules.sanitized(reason))")
            return
        }
        do {
            try startThroughHost(entry, record: record, credentials: held.credentials)
        } catch {
            BCDEpicHostFiles.cleanup(base: Self.folder, drive: LibraryModel.drive, report: false)
            unsetenv("BCD_HOST_REQUEST")
            hostRan = false
            auditSecrets = nil
            hostGame = nil
            entry.configureLaunch()   // the game's own start again, with Epic's public arguments
            BCDEpicLog.write("[epic-launch] start app=\(name) signed-in=0 reason=files")
        }
    }

    private func startThroughHost(_ entry: LibraryEntry, record: BCDEpicInstallRecord, credentials: BCDEpicCredentials) throws {
        let drive = LibraryModel.drive
        var ovtWindows: String?
        var tokenText: String?
        if let reply = credentials.ownership {
            guard let name = BCDEpicHostFiles.ovtName(namespace: record.namespace, catalogItemId: record.catalogItemId) else { throw BCDEpicHostFiles.Problem.file }
            ovtWindows = try BCDEpicHostFiles.writeOwnership(reply, name: name, drive: drive).windows
            tokenText = (BCDEpicAuth.jsonObject(reply)?["token"] as? String)
        }
        guard let epic = BCDEpicSignedLaunch.epicArguments(app: record.app, namespace: record.namespace, deploymentID: record.deploymentID,
                                                           credentials: credentials, ovtWindowsPath: ovtWindows,
                                                           preferredLanguages: Locale.preferredLanguages) else { throw BCDEpicHostFiles.Problem.file }
        // BCD's own arguments are rebuilt from the record (so the public Epic set is not repeated); a player's own text keeps its other words.
        let own = entry.arguments == record.arguments
        let game = own ? BCDEpicLaunch.gameParts(manifestCommand: record.launchCommand, extraCommand: record.extraCommand,
                                                 exceptionArgument: record.exception?.argument).parts.joined(separator: " ")
                       : BCDEpicSignedLaunch.withoutEpicArguments(entry.arguments)
        var flags: BCDEpicHostRequest.Flags = []
        if Self.flag("BCD_EPIC_TREE", entry: entry) { flags.insert(.tree) }
        // madeira-bcd (round 624, agent R S24): Epic's GTA V needs Windows' service manager for Rockstar's launcher; env.BCD_EPIC_SERVICES = 0 in the
        // game's config turns it off, = 1 turns it on for any game (as "Start Windows services first" does).
        if Self.flag("BCD_EPIC_SERVICES", entry: entry, fallback: entry.startServices == true || BCDEpicRules.startsRockstarLauncher(record.exceptionID)) {
            flags.insert(.services)
        }
        let request = BCDEpicHostRequest(program: entry.launchWindowsPath, workingFolder: entry.launchDirectory,
                                         arguments: BCDEpicSignedLaunch.tail(gameArguments: game, epic: epic), ovt: ovtWindows,
                                         root: record.root.windowsPath(), flags: flags)
        let file = try BCDEpicHostFiles.writeRequest(try request.encoded(), base: Self.folder)
        setenv("BCD_HOST_REQUEST", BCDEpicHostFiles.guestPath(file), 1)
        // The session's first program is BCD Host (inside the Wine desktop when the entry runs there).
        let desktop = entry.runsInDesktop
        if desktop {
            setenv("MADEIRA_EXE", "explorer.exe", 1)
            setenv("MADEIRA_ARGS", "/desktop=shell,\(entry.resolution) \"\(Self.hostProgram)\" --epic", 1)
        } else {
            setenv("MADEIRA_EXE", Self.hostProgram, 1)
            setenv("MADEIRA_ARGS", "--epic", 1)
        }
        // The second configureLaunch (LibraryEntry.applyEnvironment, on the launch worker) put the game's program back in builds 590 to 597; with
        // env.BCD_EPIC_HOST_START = 1 in the game's config the host's values are held for it to set again. Off, nothing changes.
        if Self.flag("BCD_EPIC_HOST_START", entry: entry, fallback: Self.hostStartDefault) {
            LibraryEntry.holdEpicHostStart(entry: entry.id, exe: desktop ? "explorer.exe" : Self.hostProgram,
                                           args: desktop ? "/desktop=shell,\(entry.resolution) \"\(Self.hostProgram)\" --epic" : "--epic")
            BCDEpicLog.write("[epic-launch] host start held (env.BCD_EPIC_HOST_START = 1)")
        }
        hostRan = true
        hostGame = record.hostFolder
        auditSecrets = (credentials.exchangeCode, credentials.accountID, tokenText)
        BCDEpicLog.write("[epic-launch] start app=\(BCDEpicLog.app(record.app)) signed-in=1 ovt=\(ovtWindows == nil ? 0 : 1) "
                         + "deployment=\(record.deploymentID == nil ? 0 : 1) host=1 tree=\(flags.contains(.tree) ? 1 : 0) "
                         + "services=\(flags.contains(.services) ? 1 : 0) desktop=\(desktop ? 1 : 0) arguments=\(own ? "epic" : "yours")")
    }

    // MARK: After the session

    /// The session ended: the host's report is read and logged (numbers), what the start left is removed, and the container is searched for
    /// the start's code, ID and token (BCDEpicAudit).
    func sessionEnded() {
        hostRan = false
        let drive = LibraryModel.drive
        let report = BCDEpicHostFiles.readReport(drive: drive)
        if let report { BCDEpicLog.write("[bcd-host] \(report.logText)") } else { BCDEpicLog.write("[bcd-host] no report") }
        let removed = BCDEpicHostFiles.cleanup(base: Self.folder, drive: drive, report: true)
        unsetenv("BCD_HOST_REQUEST")
        BCDEpicLog.write("[epic-launch] session ended cleaned files=\(removed)")
        // A report that says the host failed before it started the game, or none at all after a session that ended at once (the host did not run).
        let short = Date().timeIntervalSince(LibraryModel.shared.launchStartedAt) < 20
        if report?.failedBeforeStart == true || (report == nil && short), LibraryModel.shared.error == nil {
            tell(BCDEpicHostFiles.notStartedWords)   // round 624 (Discord forum item 7): not about signing in to Epic
        }
        guard let secrets = auditSecrets else { return }
        auditSecrets = nil
        let docs = LibraryModel.documents
        let support = Self.folder.deletingLastPathComponent()
        let game = hostGame
        hostGame = nil
        Task.detached(priority: .utility) {
            let needles = BCDEpicAudit.needles(code: secrets.code, accountID: secrets.id, token: secrets.token)
            let heavy: Set<String> = ["program files", "program files (x86)", "system32", "syswow64", "sysx64", "sysaa64", "winsxs", "fonts", "mono", "microsoft.net", ".bcd-stage", "bcd-art"]
            var roots = [BCDEpicAudit.Root(name: "docs", url: docs, skip: heavy), BCDEpicAudit.Root(name: "support", url: support),
                         BCDEpicAudit.Root(name: "tmp", url: URL(fileURLWithPath: NSTemporaryDirectory()))]
            if let game {
                roots.append(BCDEpicAudit.Root(name: "game", url: game, skip: [], suffixes: [".log", ".txt", ".json", ".ini", ".cfg", ".xml"], maxFiles: 3000))
            }
            let result = BCDEpicAudit.run(roots: roots, needles: needles)
            await MainActor.run {
                BCDEpicLog.write("[epic-audit] " + result.summary)
                for hit in result.hits { BCDEpicLog.write("[epic-audit] hit kind=\(BCDEpicRules.sanitized(hit.label)) file=\(hit.file)") }
            }
        }
    }
}

/// The front end's alert for the question above (one modifier in BCDLiveFrontEnd's alert chain). It shows nothing until a start asks.
struct BCDEpicPromptAlert: ViewModifier {
    @ObservedObject private var model = BCDEpicPromptModel.shared

    func body(content: Content) -> some View {
        content.bcdAsk($model.ask) { BCDEpic.shared.answer($0) }
    }
}
