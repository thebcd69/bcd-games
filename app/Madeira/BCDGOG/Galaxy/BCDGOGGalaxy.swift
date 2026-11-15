// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

// GOG Galaxy features for a GOG game BCD installed, engine side (docs/GOG.md, "Galaxy"; BCDGOGGalaxyCore.swift says what it is and
// holds the parts without the engine). Off by default, per game: `env.BCD_GOG_GALAXY = 1` in the game's own config (the game page's
// GOG Galaxy switch writes it) or in bcd.cfg. Without it nothing here runs: no socket is opened, GOG is not asked anything, and
// the game starts exactly as before. Engine-bound, and outside app/Madeira's top level, so the preview build never compiles it.

// MARK: - The switch and the listener's life

@MainActor final class BCDGOGGalaxy {
    static let shared = BCDGOGGalaxy()

    /// The switch: `env.BCD_GOG_GALAXY`.
    nonisolated static let switchName = "BCD_GOG_GALAXY"
    /// The longest a start waits for GOG's build manifest when the game's identity is not known yet (once per installed build).
    nonisolated static let lookupLimit: TimeInterval = 12

    /// The game's own line, else bcd.cfg's; off when none says.
    static func isOn(_ entry: LibraryEntry) -> Bool { BCDGOG.gameSwitch(switchName, for: entry) ?? false }

    // MARK: A start

    private var listener: GalaxyListener?
    /// The library entry the listener is up for.
    private var playing: UUID?
    /// The entry whose identity is being read from GOG; its start waits for it.
    private var resolving: UUID?
    /// The entry whose identity could not be read just now: its start goes on without the service.
    private var without: UUID?
    private var watcher: AnyCancellable?

    private init() {}

    /// Called by ContentView.startLibraryEntry before the launch is configured, while no session runs. True: the start goes on now.
    /// False, only for a GOG game with the switch on whose identity is not known yet: BCD reads it from GOG's build manifest first
    /// (at most `lookupLimit` seconds, once per installed build) and calls `retry`, the same start again. Any other start is not touched,
    /// except that a listener an earlier start left behind is closed.
    func preLaunch(_ entry: LibraryEntry, retry: @escaping () -> Void) -> Bool {
        // madeira-bcd (U6 counter-review L7, M2): a listener left from an earlier play is closed, but not the one of a game still running
        // (a second program started beside it); and the game's switch is read before its install record, so a game with the switch
        // off (every game, by default) reads no GOG record here.
        let idle = wineserver_is_running() == 0 && wine_process_is_running() == 0
        if idle { stop() }
        guard BCDGOG.enabled, entry.desktop != true, Self.isOn(entry), let record = BCDGOG.shared.record(forEntry: entry), idle else { return true }
        BCDContentLog.sink = { LogStore.shared.log($0) }
        switch BCDGOGGalaxyClients.known(productID: record.productID, buildID: record.buildID, base: BCDGOG.folder) {
        case .client(let client):
            start(client, entry: entry, record: record)
            return true
        case .none:
            BCDContentLog.write("[gog-galaxy] product=\(record.productID): this build names no Galaxy client; the game starts without the service")
            return true
        case .unknown:
            if without == entry.id {
                without = nil
                return true
            }
            if resolving != nil { return false }
            resolve(entry, record: record, retry: retry)
            return false
        }
    }

    private func resolve(_ entry: LibraryEntry, record: BCDGOGInstallRecord, retry: @escaping () -> Void) {
        resolving = entry.id
        let api = BCDGOG.shared.galaxyAPI
        let product = record.productID, build = record.buildID, base = BCDGOG.folder
        BCDContentLog.write("[gog-galaxy] product=\(product): reading the game's Galaxy client from its build manifest")
        let look = Task { () -> (found: Bool, client: GOGGalaxyClient?, exact: Bool) in
            let builds = try await api.json(BCDGOGContent.buildsURL(product), signedIn: true)
            guard let target = BCDGOGGalaxyClients.manifestLink(inBuilds: builds, buildID: build) else { return (false, nil, false) }
            let manifest = try await api.json(target.link, signedIn: false)
            return (true, BCDGOGGalaxyClients.client(inManifest: manifest), target.exact)
        }
        let limit = Task { try? await Task.sleep(for: .seconds(Self.lookupLimit)); look.cancel() }
        Task { @MainActor in
            var reason: String?
            do {
                let result = try await look.value
                if result.found {
                    try BCDGOGGalaxyClients.keep(result.client, productID: product, buildID: build, base: base)
                    BCDContentLog.write("[gog-galaxy] product=\(product): Galaxy client \(result.client == nil ? "none" : "read") from "
                                        + (result.exact ? "the installed build" : "the newest build (the installed one is no longer listed)"))
                } else {
                    reason = "no-build-listed"
                }
            } catch {
                reason = BCDGOG.reason(error)
            }
            limit.cancel()
            resolving = nil
            if let reason {
                without = entry.id
                BCDContentLog.write("[gog-galaxy] product=\(product): Galaxy client not read reason=\(reason); the game starts without the service")
            }
            guard LibraryModel.shared.current == nil, wine_process_is_running() == 0, wineserver_is_running() == 0 else {
                without = nil
                BCDContentLog.write("[gog-galaxy] start dropped reason=session-running")
                return
            }
            retry()
        }
    }

    private func start(_ client: GOGGalaxyClient, entry: LibraryEntry, record: BCDGOGInstallRecord) {
        let log = GalaxyLog { BCDContentLog.write($0) }
        let session = BCDGOG.shared.galaxySession
        let api = BCDGOG.shared.galaxyAPI
        let tokens = BCDGOGGalaxyTokens(hostRefreshToken: { await session.current()?.refreshToken },
                                        fetch: { try await api.http.data($0, limit: 64 << 10) })
        let language = record.language.isEmpty || record.language == "*" ? "en-US" : record.language
        let service = GalaxyService(client: client, tokens: tokens, log: log, language: language)
        let made = GalaxyListener(service: service, log: log)
        do {
            try made.start()
        } catch {
            // The port is taken, or iOS refused the socket: the game starts as it does without the switch.
            BCDContentLog.write("[gog-galaxy] product=\(record.productID): the service did not start (\(error)); the game starts without it")
            return
        }
        listener = made
        playing = entry.id
        let id = entry.id
        // The session's end: LibraryModel.current leaves this entry. (@Published sends before the value is stored: look on the next turn.)
        watcher = LibraryModel.shared.$current.dropFirst().receive(on: DispatchQueue.main).sink { [weak self] current in
            guard let self, self.playing == id, current != id else { return }
            self.stop()
        }
        // A start that never became a session (a later check refused it) leaves nothing listening.
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(60))
            guard let self, self.playing == id, LibraryModel.shared.current != id, wine_process_is_running() == 0 else { return }
            self.stop()
        }
    }

    /// Closes the listener, if one is up (the play ended, or another start begins).
    func stop() {
        watcher = nil
        playing = nil
        listener?.stop()
        listener = nil
    }
}
