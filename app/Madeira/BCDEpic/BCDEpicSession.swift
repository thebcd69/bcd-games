// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Epic's signed-in session: the tokens in memory and in the store's one secret item (the Keychain
// in the app), refreshed ten minutes before they lapse (once, however many callers ask), verified at
// start, ended at Epic on sign-out. Written from the protocol facts in docs/research/open-fork-2026-10-18/
// epic.md §2.1, with no code of any other Epic client. Foundation only (the host checks compile it).
// Every [epic-auth] line goes through BCDEpicLog: counts, statuses and timings, never a token.

/// How requests reach Epic: the app's URLSession below, or a stand-in in the host checks. Returns
/// the status and the body whatever the status is (Epic's error codes are in the body).
protocol BCDEpicTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (status: Int, body: Data)
}

/// Epic over URLSession: ephemeral (no cache, no cookies), a fixed user agent, redirects refused (a
/// token never follows one), bodies bounded by the callers.
final class BCDEpicURLSession: NSObject, BCDEpicTransport, URLSessionTaskDelegate, @unchecked Sendable {
    private var session: URLSession!
    private let userAgent: String

    /// `protocols`: URLProtocol classes answering before the network (the host checks' stand-in).
    init(userAgent: String = BCDEpicAuth.launcherUserAgent, protocols: [AnyClass] = []) {
        self.userAgent = userAgent
        super.init()
        let config = URLSessionConfiguration.ephemeral
        if !protocols.isEmpty { config.protocolClasses = protocols + (config.protocolClasses ?? []) }
        config.urlCache = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.httpCookieAcceptPolicy = .never
        config.httpShouldSetCookies = false
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 120
        config.waitsForConnectivity = false
        session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }

    func send(_ request: URLRequest) async throws -> (status: Int, body: Data) {
        var request = request
        // A request that names its own user agent keeps it (the save service wants the launcher's long one, BCDEpicCloudHTTP).
        if request.value(forHTTPHeaderField: "User-Agent") == nil { request.setValue(userAgent, forHTTPHeaderField: "User-Agent") }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        return (http.statusCode, data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

actor BCDEpicSession {
    static let itemName = "bcd.epic.tokens"
    private let secrets: BCDSecretStore
    private let transport: BCDEpicTransport
    private let now: @Sendable () -> Date
    private var tokens: BCDEpicTokens?
    private var loaded = false
    private var refreshing: Task<BCDEpicTokens, Error>?

    init(secrets: BCDSecretStore, transport: BCDEpicTransport, now: @escaping @Sendable () -> Date = { Date() }) {
        self.secrets = secrets
        self.transport = transport
        self.now = now
    }

    func current() -> BCDEpicTokens? {
        if !loaded {
            loaded = true
            tokens = secrets.read(Self.itemName).flatMap { try? JSONDecoder().decode(BCDEpicTokens.self, from: $0) }
        }
        return tokens
    }

    /// Trades the sign-in page's code for tokens, asks Epic who they belong to, and keeps them.
    func signIn(code: String) async throws -> BCDEpicTokens {
        let started = BCDEpicLog.now()
        let (status, body) = try await transport.send(BCDEpicAuth.tokenRequest(code: code))
        BCDEpicLog.write("[epic-auth] token exchange status=\(status) ms=\(BCDEpicLog.millis(since: started))")
        guard status == 200 else { throw BCDEpicAuth.failure(status: status, body: body) }
        var made = try BCDEpicAuth.tokens(from: body, now: now())
        let verifyStarted = BCDEpicLog.now()
        do {
            let (verifyStatus, verifyBody) = try await transport.send(BCDEpicAuth.verifyRequest(accessToken: made.accessToken))
            BCDEpicLog.write("[epic-auth] verify status=\(verifyStatus) ms=\(BCDEpicLog.millis(since: verifyStarted))")
            if verifyStatus == 200 {
                let who = BCDEpicAuth.identity(from: verifyBody)
                made.accountID = who.accountID ?? made.accountID
                made.displayName = who.displayName ?? made.displayName
            }
        } catch {
            BCDEpicLog.write("[epic-auth] verify failed reason=\(BCDEpicLog.reason(error))")
        }
        guard let account = made.accountID else { throw BCDEpicError.badReply("account") }
        try store(made)
        BCDEpicLog.write("[epic-auth] signed in account=\(BCDEpicLog.mask(account)) name=\(made.displayName != nil ? 1 : 0)")
        return made
    }

    /// A current access token, refreshed first when it is about to lapse.
    func accessToken() async throws -> String {
        guard let held = current() else { throw BCDEpicError.signedOut }
        if !held.needsRefresh(now: now()) { return held.accessToken }
        return try await refresh().accessToken
    }

    /// A refresh now (a call was refused with the current token). A refresh Epic refuses signs out.
    func refresh() async throws -> BCDEpicTokens {
        if let refreshing { return try await refreshing.value }
        guard let held = current() else { throw BCDEpicError.signedOut }
        let transport = self.transport, now = self.now
        let task = Task { () throws -> BCDEpicTokens in
            let started = BCDEpicLog.now()
            let (status, body) = try await transport.send(BCDEpicAuth.refreshRequest(held.refreshToken))
            BCDEpicLog.write("[epic-auth] refresh status=\(status) ms=\(BCDEpicLog.millis(since: started))")
            guard status == 200 else { throw BCDEpicAuth.failure(status: status, body: body) }
            return try BCDEpicAuth.tokens(from: body, now: now(), previous: held)
        }
        refreshing = task
        defer { refreshing = nil }
        do {
            let fresh = try await task.value
            try store(fresh)
            return fresh
        } catch let error as BCDEpicError where error.refusesRefresh {
            forget()
            BCDEpicLog.write("[epic-auth] refresh refused; signed out")
            throw BCDEpicError.signInRefused
        }
    }

    /// Asks Epic whether the session is good (refreshing it first when it is about to lapse, and once
    /// more when Epic says it has lapsed) and keeps the account's name it answers with.
    func verify() async throws -> BCDEpicTokens {
        guard current() != nil else { throw BCDEpicError.signedOut }
        var token = try await accessToken()
        var attempt = 0
        while true {
            let started = BCDEpicLog.now()
            let (status, body) = try await transport.send(BCDEpicAuth.verifyRequest(accessToken: token))
            BCDEpicLog.write("[epic-auth] verify status=\(status) ms=\(BCDEpicLog.millis(since: started))")
            if status == 401, attempt == 0 {
                attempt += 1
                token = try await refresh().accessToken
                continue
            }
            guard status == 200 else { throw BCDEpicAuth.failure(status: status, body: body) }
            guard var held = current() else { throw BCDEpicError.signedOut }
            let who = BCDEpicAuth.identity(from: body)
            if let name = who.displayName { held.displayName = name }
            if let account = who.accountID { held.accountID = account }
            try store(held)
            return held
        }
    }

    /// Forgets the tokens at once (the Keychain item goes), then ends the session at Epic. Returns
    /// whether Epic confirmed it; an unreachable Epic leaves the session to lapse by itself.
    func signOut() async -> Bool {
        let held = current()
        forget()
        guard let held else { return false }
        let started = BCDEpicLog.now()
        do {
            let (status, _) = try await transport.send(BCDEpicAuth.killRequest(accessToken: held.accessToken))
            BCDEpicLog.write("[epic-auth] signed out revoke status=\(status) ms=\(BCDEpicLog.millis(since: started))")
            return (200...299).contains(status)
        } catch {
            BCDEpicLog.write("[epic-auth] signed out revoke failed reason=\(BCDEpicLog.reason(error))")
            return false
        }
    }

    private func forget() {
        secrets.delete(Self.itemName)
        tokens = nil
        loaded = true
        refreshing?.cancel()
        refreshing = nil
    }

    private func store(_ fresh: BCDEpicTokens) throws {
        try secrets.write(Self.itemName, JSONEncoder().encode(fresh))
        tokens = fresh
        loaded = true
    }
}

/// Epic's web APIs with the session's token: a refused token is refreshed once and the call tried
/// again. Only https on Epic's own hosts gets the token. Bodies are bounded. Never logs (the
/// callers log counts and statuses).
final class BCDEpicAPI: @unchecked Sendable {
    let session: BCDEpicSession
    let transport: BCDEpicTransport

    init(session: BCDEpicSession, transport: BCDEpicTransport) {
        self.session = session
        self.transport = transport
    }

    /// An address the token may be sent to: https on one of Epic's hosts, no user name or port.
    static func trusted(_ url: URL) -> Bool {
        guard url.scheme == "https", url.user == nil, url.port == nil, let host = url.host?.lowercased() else { return false }
        return host.hasSuffix(".epicgames.com")
    }

    /// A GET with the token. Any status outside 200...299 throws (`BCDEpicAuth.failure`).
    func get(_ url: URL, limit: Int = 16 << 20) async throws -> (status: Int, data: Data) {
        guard Self.trusted(url) else { throw BCDEpicError.badReply("host") }
        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("bearer " + (try await session.accessToken()), forHTTPHeaderField: "Authorization")
        var (status, body) = try await transport.send(request)
        if status == 401 {
            request.setValue("bearer " + (try await session.refresh().accessToken), forHTTPHeaderField: "Authorization")
            (status, body) = try await transport.send(request)
        }
        guard (200...299).contains(status) else { throw BCDEpicAuth.failure(status: status, body: body) }
        guard body.count <= limit else { throw BCDEpicError.badReply("size") }
        return (status, body)
    }

    /// Any call with the token (a POST with a body too), on the same rules as `get`: only Epic's hosts get the token, a refused token
    /// is refreshed once. The request's own headers stay (a content type, a user agent). Any status outside 200...299 throws.
    func send(_ request: URLRequest, limit: Int = 16 << 20) async throws -> (status: Int, data: Data) {
        guard let url = request.url, Self.trusted(url) else { throw BCDEpicError.badReply("host") }
        var request = request
        request.setValue("bearer " + (try await session.accessToken()), forHTTPHeaderField: "Authorization")
        var (status, body) = try await transport.send(request)
        if status == 401 {
            request.setValue("bearer " + (try await session.refresh().accessToken), forHTTPHeaderField: "Authorization")
            (status, body) = try await transport.send(request)
        }
        guard (200...299).contains(status) else { throw BCDEpicAuth.failure(status: status, body: body) }
        guard body.count <= limit else { throw BCDEpicError.badReply("size") }
        return (status, body)
    }
}
