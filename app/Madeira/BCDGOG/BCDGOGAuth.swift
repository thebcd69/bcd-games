// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// GOG's sign-in, written from the protocol as GOG's community documentation (gogapidocs) and
// BCD's research describe it (docs/research/open-fork-2026-10-18/gog.md §2.1): the standard OAuth
// authorization-code flow with GOG Galaxy's public desktop-client identity. Tokens live in the
// Keychain (BCDKeychain, "bcd.gog.tokens") and in memory, and never in a log, a file or the
// environment. GOG has no revoke: signing out deletes them. Foundation only (the host checks
// compile it).

enum BCDGOGError: Error, Equatable, LocalizedError {
    case signedOut
    case signInRefused
    case badReply(String)
    case noWindowsBuild
    case olderBuildsOnly
    case noPlayTask
    case unknownFileSource

    var errorDescription: String? {
        switch self {
        case .signedOut: return "Sign in to GOG first (Settings › Accounts)."
        case .signInRefused: return "GOG no longer accepts this sign-in. Sign in to GOG again."
        case .badReply: return "GOG's reply wasn't what BCD expects (GOG may have changed it). Try again later."
        case .noWindowsBuild: return "GOG has no Windows download of this game."
        case .olderBuildsOnly: return "This game only has GOG's older kind of download, which BCD doesn't read yet."
        case .noPlayTask: return "The game's files name no program to start. Its folder is in C:\\GOG Games: add the program from there."
        case .unknownFileSource: return "This game's download uses a part BCD doesn't read yet."
        }
    }

    var logReason: String {
        switch self {
        case .signedOut: return "signed-out"
        case .signInRefused: return "sign-in-refused"
        case .badReply(let what): return "reply-" + what
        case .noWindowsBuild: return "no-windows-build"
        case .olderBuildsOnly: return "generation-1"
        case .noPlayTask: return "no-play-task"
        case .unknownFileSource: return "unknown-file-source"
        }
    }
}

extension BCDGOGError: BCDLogReasonError {}

/// The signed-in account's tokens. Printing one shows nothing of them.
struct BCDGOGTokens: Codable, Equatable, CustomStringConvertible, CustomDebugStringConvertible {
    var accessToken: String
    var refreshToken: String
    var expires: Date
    var userID: String
    var userName: String?

    /// Refreshed when under five minutes are left.
    func needsRefresh(now: Date) -> Bool { now.addingTimeInterval(300).timeIntervalSinceReferenceDate >= expires.timeIntervalSinceReferenceDate }

    var description: String { "BCDGOGTokens(<redacted>)" }
    var debugDescription: String { description }
}

enum BCDGOGAuth {
    static let clientID = "46899977096215655"
    static let clientSecret = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"
    static let redirect = "https://embed.gog.com/on_login_success?origin=client"

    /// GOG's sign-in page (it moves on to login.gog.com).
    static var loginURL: URL {
        URL(string: "https://auth.gog.com/auth?" + query([("client_id", clientID), ("redirect_uri", redirect),
                                                         ("response_type", "code"), ("layout", "client2")]))!
    }

    /// A query with every value percent-encoded strictly (the redirect address included).
    static func query(_ items: [(String, String)]) -> String {
        var allowed = CharacterSet.alphanumerics.intersection(CharacterSet(charactersIn: Unicode.Scalar(0)...Unicode.Scalar(127)))
        allowed.insert(charactersIn: "-._~")
        return items.map { $0.0 + "=" + ($0.1.addingPercentEncoding(withAllowedCharacters: allowed) ?? "") }.joined(separator: "&")
    }

    /// The code from the address GOG's page ends on once the sign-in is done, or nil for any other
    /// address: exactly https://embed.gog.com/on_login_success with one plain `code`.
    static func code(from url: URL) -> String? {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme?.lowercased() == "https", parts.host?.lowercased() == "embed.gog.com",
              parts.user == nil, parts.password == nil, parts.port == nil || parts.port == 443,
              parts.path == "/on_login_success" else { return nil }
        let codes = (parts.queryItems ?? []).filter { $0.name == "code" }
        guard codes.count == 1, let code = codes[0].value, (8...2048).contains(code.utf8.count),
              code.unicodeScalars.allSatisfy({ $0.isASCII && (CharacterSet.alphanumerics.contains($0) || "-._~".unicodeScalars.contains($0)) })
        else { return nil }
        return code
    }

    static func tokenRequest(code: String) -> URLRequest {
        request("https://auth.gog.com/token?" + query([("client_id", clientID), ("client_secret", clientSecret),
                                                       ("grant_type", "authorization_code"), ("code", code),
                                                       ("redirect_uri", redirect)]))
    }

    static func refreshRequest(_ refreshToken: String) -> URLRequest {
        request("https://auth.gog.com/token?" + query([("client_id", clientID), ("client_secret", clientSecret),
                                                       ("grant_type", "refresh_token"), ("refresh_token", refreshToken)]))
    }

    /// The account's public profile (its user name).
    static func userRequest(_ userID: String) -> URLRequest { request("https://users.gog.com/users/" + userID) }

    private static func request(_ address: String) -> URLRequest {
        var request = URLRequest(url: URL(string: address)!)
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    /// GOG's token reply. A refresh that sends no new refresh token or user keeps the previous ones.
    static func tokens(from data: Data, now: Date, previous: BCDGOGTokens? = nil) throws -> BCDGOGTokens {
        guard let reply = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw BCDGOGError.badReply("token") }
        func text(_ key: String) -> String? {
            if let s = reply[key] as? String, !s.isEmpty { return s }
            if let n = reply[key] as? NSNumber { return n.stringValue }
            return nil
        }
        guard let access = text("access_token"), let refresh = text("refresh_token") ?? previous?.refreshToken,
              let user = text("user_id") ?? previous?.userID, user.allSatisfy(\.isNumber) else {
            throw BCDGOGError.badReply("token")
        }
        let lifetime = min(max(Double(text("expires_in") ?? "") ?? 3600, 60), 86400)
        return BCDGOGTokens(accessToken: access, refreshToken: refresh, expires: now.addingTimeInterval(lifetime),
                            userID: user, userName: previous?.userName)
    }

    static func userName(from data: Data) -> String? {
        guard let reply = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let name = reply["username"] as? String else { return nil }
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty || trimmed.count > 64 ? nil : trimmed
    }
}

/// The signed-in session: the tokens in memory, kept in the store's secret item, refreshed five
/// minutes before they lapse (once, however many callers ask). A refresh GOG refuses signs out.
actor BCDGOGSession {
    static let itemName = "bcd.gog.tokens"
    private let secrets: BCDSecretStore
    private let http: BCDHTTPStream
    private let now: @Sendable () -> Date
    private var tokens: BCDGOGTokens?
    private var loaded = false
    private var refreshing: Task<BCDGOGTokens, Error>?

    init(secrets: BCDSecretStore, http: BCDHTTPStream, now: @escaping @Sendable () -> Date = { Date() }) {
        self.secrets = secrets
        self.http = http
        self.now = now
    }

    func current() -> BCDGOGTokens? {
        if !loaded {
            loaded = true
            tokens = secrets.read(Self.itemName).flatMap { try? JSONDecoder().decode(BCDGOGTokens.self, from: $0) }
        }
        return tokens
    }

    /// Exchanges the sign-in page's code for tokens and keeps them, with the account's public name.
    func signIn(code: String) async throws -> BCDGOGTokens {
        let data = try await http.data(BCDGOGAuth.tokenRequest(code: code), limit: 64 << 10)
        var made = try BCDGOGAuth.tokens(from: data, now: now())
        if let profile = try? await http.data(BCDGOGAuth.userRequest(made.userID), limit: 256 << 10) {
            made.userName = BCDGOGAuth.userName(from: profile)
        }
        try store(made)
        BCDContentLog.write("[gog-signin] signed in")
        return made
    }

    /// A current access token, refreshed first when it is about to lapse.
    func accessToken() async throws -> String {
        guard let held = current() else { throw BCDGOGError.signedOut }
        if !held.needsRefresh(now: now()) { return held.accessToken }
        return try await refresh().accessToken
    }

    /// A refresh now (an API call was refused with the current token).
    func refresh() async throws -> BCDGOGTokens {
        if let refreshing { return try await refreshing.value }
        guard let held = current() else { throw BCDGOGError.signedOut }
        let task = Task { () throws -> BCDGOGTokens in
            do {
                let data = try await http.data(BCDGOGAuth.refreshRequest(held.refreshToken), limit: 64 << 10)
                return try BCDGOGAuth.tokens(from: data, now: now(), previous: held)
            } catch BCDContentError.http(let status) where status == 400 || status == 401 || status == 403 {
                throw BCDGOGError.signInRefused
            }
        }
        refreshing = task
        defer { refreshing = nil }
        do {
            let fresh = try await task.value
            try store(fresh)
            BCDContentLog.write("[gog-signin] refreshed")
            return fresh
        } catch BCDGOGError.signInRefused {
            signOut()
            BCDContentLog.write("[gog-signin] refresh refused; signed out")
            throw BCDGOGError.signInRefused
        }
    }

    func signOut() {
        secrets.delete(Self.itemName)
        tokens = nil
        loaded = true
        refreshing?.cancel()
        refreshing = nil
    }

    private func store(_ fresh: BCDGOGTokens) throws {
        try secrets.write(Self.itemName, JSONEncoder().encode(fresh))
        tokens = fresh
        loaded = true
    }
}

/// GOG's web APIs with the session's token: a refused token is refreshed once and the call
/// tried again. Bodies are bounded. Never logs (addresses carry tokens).
final class BCDGOGAPI: @unchecked Sendable {
    let session: BCDGOGSession
    let http: BCDHTTPStream

    init(session: BCDGOGSession, http: BCDHTTPStream) {
        self.session = session
        self.http = http
    }

    func data(_ url: URL, signedIn: Bool, limit: Int) async throws -> Data {
        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        guard signedIn else { return try await http.data(request, limit: limit) }
        request.setValue("Bearer " + (try await session.accessToken()), forHTTPHeaderField: "Authorization")
        do {
            return try await http.data(request, limit: limit)
        } catch BCDContentError.http(401) {
            request.setValue("Bearer " + (try await session.refresh().accessToken), forHTTPHeaderField: "Authorization")
            return try await http.data(request, limit: limit)
        }
    }

    func json(_ url: URL, signedIn: Bool, limit: Int = 8 << 20) async throws -> [String: Any] {
        let data = try await self.data(url, signedIn: signedIn, limit: limit)
        guard let object = try? JSONSerialization.jsonObject(with: BCDZlib.jsonData(data, limit: 256 << 20)) as? [String: Any] else {
            // Why an install said "GOG's reply wasn't what BCD expects" (2026-10-20, build 574, two games): only the
            // size and the first byte, never the address or the text.
            BCDContentLog.write("[gog-content] reply is not a JSON object: bytes=\(data.count) first=" +
                                (data.first.map { String(format: "0x%02x", $0) } ?? "none"))
            throw BCDGOGError.badReply("json")
        }
        return object
    }

    /// What a reply looks like, for the log when a step of the install cannot read it: its keys with the kind of each
    /// value (s string, n number, b bool, N null, a array, o object), and for `lists` the count and the first entry's
    /// keys the same way. Never a value.
    static func shape(_ object: [String: Any], lists: [String]) -> String {
        func kind(_ value: Any) -> String {
            if value is NSNull { return "N" }
            if value is String { return "s" }
            if let number = value as? NSNumber { return CFGetTypeID(number) == CFBooleanGetTypeID() ? "b" : "n" }
            if value is [Any] { return "a" }
            return value is [String: Any] ? "o" : "?"
        }
        func keys(_ item: [String: Any]) -> String { item.keys.sorted().prefix(40).map { $0 + "=" + kind(item[$0]!) }.joined(separator: ",") }
        var text = "top[" + keys(object) + "]"
        for name in lists {
            guard let list = object[name] as? [Any] else { continue }
            text += " " + name + "=\(list.count)"
            if let first = list.first as? [String: Any] { text += " first[" + keys(first) + "]" }
        }
        return text
    }

    /// An address from one of GOG's replies that BCD follows: https on GOG's own hosts.
    static func trusted(_ url: URL?) -> Bool {
        guard let url, url.scheme == "https", let host = url.host?.lowercased(), url.user == nil else { return false }
        return host == "gog.com" || host.hasSuffix(".gog.com") || host.hasSuffix(".gog-statics.com")
    }
}
