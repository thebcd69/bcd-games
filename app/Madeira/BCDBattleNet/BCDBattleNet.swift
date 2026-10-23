// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

/// WoW account discovery and session cache. Game login remains the game's own login.
@MainActor final class BCDBattleNet: ObservableObject {
    static let shared = BCDBattleNet()
    @Published private(set) var signedIn = false
    @Published private(set) var name: String?
    @Published private(set) var products: [BCDBattleNetProduct] = []
    @Published private(set) var refreshing = false
    @Published private(set) var error: String?
    private var credential: BCDBattleNetCredential?
    private var started = false
    private var generation = 0
    private var updated: Date?
    private var task: Task<Void, Never>?
    private var signInOpen = false
    var downloadRegion: String {
        let prefix = credential?.origin.host?.split(separator: ".").first.map(String.init) ?? "us"
        return BCDBnetNGDP.regions.contains(prefix) ? prefix : "us"
    }

    func start() {
        guard !started else { return }
        started = true
        BCDBnetDownloads.shared.start()
        credential = BCDKeychain.shared.read(BCDBattleNetAccount.secretItem)
            .flatMap { try? JSONDecoder().decode(BCDBattleNetCredential.self, from: $0) }
        guard let credential, BCDBattleNetAccount.validOrigin(credential.origin) else { return }
        signedIn = true
        name = credential.cachedName
        products = credential.cachedProducts ?? []
        updated = credential.updated
        refresh(force: true)
    }

    func signIn() {
        guard !signInOpen else { return }
        start()
        signInOpen = true
        generation += 1
        task?.cancel(); task = nil; refreshing = false
        let expected = generation
        BCDBattleNetSignIn.present { [weak self] made in
            guard let self, self.generation == expected else { throw CancellationError() }
            let result = try await self.read(made)
            try Task.checkCancellation()
            guard self.generation == expected else { throw CancellationError() }
            var made = made
            made.cachedName = result.name; made.cachedProducts = result.products; made.updated = Date()
            try BCDKeychain.shared.write(BCDBattleNetAccount.secretItem, JSONEncoder().encode(made))
            // An older refresh must not overwrite this account, including its error state.
            self.generation += 1
            self.task?.cancel(); self.task = nil; self.refreshing = false
            self.credential = made
            self.signedIn = true; self.name = result.name; self.products = result.products
            self.error = nil; self.updated = Date()
        } closed: { [weak self] in self?.signInOpen = false }
    }

    func signOut() {
        generation += 1
        task?.cancel(); task = nil
        BCDBnetDownloads.shared.pauseAll()
        BCDKeychain.shared.delete(BCDBattleNetAccount.secretItem)
        credential = nil; signedIn = false; name = nil; products = []
        updated = nil; error = nil; refreshing = false
    }

    func refresh(force: Bool = false) {
        start()
        guard let credential, !refreshing else { return }
        if !force, let updated, Date().timeIntervalSince(updated) < 6 * 3600 { return }
        refreshing = true
        let expected = generation
        task = Task { [weak self] in
            guard let self else { return }
            defer { if generation == expected { refreshing = false; task = nil } }
            do {
                let result = try await read(credential)
                try Task.checkCancellation()
                guard generation == expected else { return }
                var fresh = credential
                fresh.cachedName = result.name ?? credential.cachedName
                fresh.cachedProducts = result.products; fresh.updated = Date()
                try BCDKeychain.shared.write(BCDBattleNetAccount.secretItem, JSONEncoder().encode(fresh))
                self.credential = fresh
                name = fresh.cachedName; products = result.products; updated = fresh.updated; error = nil
            } catch is CancellationError {
                return
            } catch {
                guard generation == expected else { return }
                if case BCDBattleNetError.signedOut = error { signOut() }
                self.error = (error as? LocalizedError)?.errorDescription ?? "Couldn't refresh Battle.net. Try again later."
            }
        }
    }

    private func read(_ credential: BCDBattleNetCredential) async throws -> (name: String?, products: [BCDBattleNetProduct]) {
        // Never follow an API redirect carrying an explicitly supplied Cookie header.
        // Session renewal goes through Blizzard's sign-in page instead.
        let http = BCDHTTPStream(redirectAllowed: { _ in false })
        defer { http.close() }
        do {
            let data = try await http.data(credential.request("/api/games-and-subs", now: Date()), limit: 8 << 20)
            let products = try BCDBattleNetAccount.products(data)
            let details = try? await http.data(credential.request("/api/details", now: Date()), limit: 256 << 10)
            return (details.flatMap(BCDBattleNetAccount.name), products)
        } catch BCDContentError.http(let status) {
            if status == 401 || (300..<400).contains(status) { throw BCDBattleNetError.signedOut }
            throw BCDBattleNetError.http(status)
        }
    }
}
