// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Epic Games' sign-in, written from protocol facts only (docs/research/open-fork-2026-10-18/epic.md
// §2.1 and §6): Epic's own login page in a web view, whose redirect page answers with a JSON text
// holding an authorization code; that code, traded at the account service's token endpoint with the
// launcher's public client identity (the client table of EpicResearch's documentation), gives the
// tokens; refresh, verify and sign-out are the same service's documented endpoints. No code of
// Legendary, Heroic, Rare, Comet, gogdl or PlayPort was read or used while writing this file.
//
// The password goes to Epic's page and nowhere else. Tokens live in the Keychain (BCDKeychain,
// "bcd.epic.tokens") and in memory, and never in a log, a file, UserDefaults or the environment
// (BCDEpicSession below keeps them; tests/host/check-epic.py reads the rule). Foundation only: the
// host checks compile it.

enum BCDEpicError: Error, Equatable, LocalizedError {
    case signedOut
    /// Epic refused the refresh token (revoked, expired): the sign-in is over.
    case signInRefused
    /// Epic wants the player to finish a step on its own pages first (a privacy policy, a date of birth).
    case corrective(action: String, continuation: URL?)
    /// Epic answered with an error status and its own error code (never shown raw to the player).
    case rejected(status: Int, code: String)
    /// A server or rate-limit answer worth trying again later.
    case http(Int)
    case badReply(String)

    var errorDescription: String? {
        switch self {
        case .signedOut: return "Sign in to Epic Games first (Settings › Accounts)."
        case .signInRefused: return "Epic no longer accepts this sign-in. Sign in to Epic Games again."
        case .corrective: return "Epic wants you to finish a step on its website (an account or policy question) before it lets BCD in. Do that, then sign in again."
        case .rejected(let status, _): return "Epic didn't accept the request (HTTP \(status)). Try again, or sign in again."
        case .http(let status): return "Epic's servers answered \(status). Try again later."
        case .badReply: return "Epic's reply wasn't what BCD expects (Epic may have changed it). Try again later."
        }
    }

    /// A short reason for the log: letters, digits and dashes only.
    var logReason: String {
        switch self {
        case .signedOut: return "signed-out"
        case .signInRefused: return "sign-in-refused"
        case .corrective(let action, _): return "corrective-" + action
        case .rejected(let status, _): return "rejected-\(status)"
        case .http(let status): return "http-\(status)"
        case .badReply(let what): return "reply-" + what
        }
    }

    /// A refresh refused outright: the refresh token is no good, as opposed to Epic being unreachable.
    var refusesRefresh: Bool {
        if case .rejected(let status, _) = self { return [400, 401, 403].contains(status) }
        return false
    }
}

extension BCDEpicError: BCDLogReasonError {}

/// The signed-in account's tokens. Printing one shows nothing of them.
struct BCDEpicTokens: Codable, Equatable, CustomStringConvertible, CustomDebugStringConvertible {
    var accessToken: String
    var refreshToken: String
    var accessExpires: Date
    var refreshExpires: Date?
    var accountID: String?
    var displayName: String?

    /// Refreshed when under ten minutes are left.
    func needsRefresh(now: Date) -> Bool { now.addingTimeInterval(600).timeIntervalSinceReferenceDate >= accessExpires.timeIntervalSinceReferenceDate }

    var description: String { "BCDEpicTokens(<redacted>)" }
    var debugDescription: String { description }
}

/// What the sign-in's redirect page shows.
enum BCDEpicPage: Equatable {
    case code(String)
    case corrective(action: String, continuation: URL?)
    case other
}

enum BCDEpicAuth {
    // The launcher's public client (EpicResearch's documented client table), the same identity Epic's own
    // launcher signs in with. Epic tolerates third-party use of it today and could stop (epic.md §2.7).
    static let clientID = "34a02cf8f4414e29b15921876da36f9a"
    static let clientSecret = "daafbccc737745039dffe53d94fc76cf"
    static let accountHost = "account-public-service-prod03.ol.epicgames.com"
    /// The page Epic's login ends on for this client: a JSON text with the authorization code.
    static let redirectPage = "https://www.epicgames.com/id/api/redirect?clientId=" + clientID + "&responseType=code"
    /// The launcher-shaped user agent for API calls (epic.md §3.2 gives its shape; `epic-user-agent`
    /// in bcd.cfg replaces it, and the sign-in page's too).
    static let launcherUserAgent = "UELauncher/11.0.1 Windows/10.0.19041.1.256.64bit"

    // MARK: The sign-in page

    static var loginURL: URL {
        URL(string: "https://www.epicgames.com/id/login?redirectUrl=" + encode(redirectPage))!
    }

    /// Percent-encodes everything but the unreserved characters.
    static func encode(_ text: String) -> String {
        var allowed = CharacterSet.alphanumerics.intersection(CharacterSet(charactersIn: Unicode.Scalar(0)...Unicode.Scalar(127)))
        allowed.insert(charactersIn: "-._~")
        return text.addingPercentEncoding(withAllowedCharacters: allowed) ?? ""
    }

    /// The page Epic's login ends on: exactly https://www.epicgames.com/id/api/redirect, nowhere else.
    /// Only this page's text is ever read (BCDWebSignIn evaluates a script for no other address).
    static func isRedirectPage(_ url: URL) -> Bool {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return false }
        return parts.scheme?.lowercased() == "https" && parts.host?.lowercased() == "www.epicgames.com"
            && parts.user == nil && parts.password == nil && (parts.port == nil || parts.port == 443)
            && parts.path == "/id/api/redirect"
    }

    /// What the redirect page's text says: its authorization code, a step Epic wants finished first
    /// (with the page for it), or neither.
    static func page(from text: String) -> BCDEpicPage {
        guard let object = jsonObject(text) else { return .other }
        if let code = object["authorizationCode"] as? String, isCode(code) { return .code(code) }
        if let target = object["redirectUrl"] as? String, let url = URL(string: target), let code = code(from: url) { return .code(code) }
        let errorCode = (object["errorCode"] as? String) ?? ""
        let action = (object["correctiveAction"] as? String) ?? (object["corrective_action"] as? String) ?? ""
        if errorCode.hasSuffix("corrective_action_required") || !action.isEmpty {
            return .corrective(action: sanitizedAction(action), continuation: continuation(in: object))
        }
        return .other
    }

    /// Where a web view went, for the log: Epic's own host and the first three path segments (an odd one is
    /// a star), never a query or a fragment; any other host is just "other-host".
    static func pageLabel(_ url: URL) -> String {
        guard url.scheme == "https", let host = url.host?.lowercased() else { return "non-https" }
        guard host == "epicgames.com" || host.hasSuffix(".epicgames.com") else { return "other-host" }
        let parts = url.path.split(separator: "/").prefix(3).map { part -> String in
            part.utf8.count <= 24 && part.utf8.allSatisfy { isAlnum($0) || $0 == 0x2D || $0 == 0x5F } ? String(part) : "*"
        }
        return host + "/" + parts.joined(separator: "/")
    }

    /// The key names of the redirect page's JSON, for the log (never a value): "authorizationCode,sid".
    static func keys(of text: String) -> String {
        guard let object = jsonObject(text) else { return "not-json" }
        let names = object.keys.filter { $0.utf8.count <= 40 && $0.utf8.allSatisfy { isAlnum($0) || $0 == 0x5F } }.sorted().prefix(12)
        return names.isEmpty ? "none" : names.joined(separator: ",")
    }

    /// A code in an address the page went to: Epic's own redirect to a local address
    /// (http://localhost/...?code=...) carries one, and no other address does.
    static func code(from url: URL) -> String? {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let scheme = parts.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = parts.host?.lowercased(), host == "localhost" || host == "127.0.0.1",
              parts.user == nil, parts.password == nil else { return nil }
        let codes = (parts.queryItems ?? []).filter { $0.name == "code" }
        guard codes.count == 1, let code = codes[0].value, isCode(code) else { return nil }
        return code
    }

    /// What a person pastes after signing in with Safari: the page's whole text, the line with the
    /// code in it, or the bare code (32 hexadecimal digits).
    static func code(fromPasted text: String) -> String? {
        let straight = text.replacingOccurrences(of: "\u{201C}", with: "\"").replacingOccurrences(of: "\u{201D}", with: "\"")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !straight.isEmpty, straight.utf8.count <= 4096 else { return nil }
        if isBareCode(straight) { return straight }
        if case .code(let code) = page(from: straight) { return code }
        // The line with the code, copied without its braces.
        if let regex = try? NSRegularExpression(pattern: #""authorizationCode"\s*:\s*"([0-9A-Za-z]{16,64})""#),
           let match = regex.firstMatch(in: straight, range: NSRange(straight.startIndex..., in: straight)),
           let range = Range(match.range(at: 1), in: straight) {
            return String(straight[range])
        }
        if let url = URL(string: straight), let code = code(from: url) { return code }
        return nil
    }

    /// A step's page, kept only when it is https on one of Epic's own hosts.
    static func trustedContinuation(_ text: String?) -> URL? {
        guard let text, text.utf8.count < 2048, let url = URL(string: text), url.scheme == "https", url.user == nil,
              let host = url.host?.lowercased(), host == "epicgames.com" || host.hasSuffix(".epicgames.com") else { return nil }
        return url
    }

    private static func continuation(in object: [String: Any]) -> URL? {
        trustedContinuation((object["continuationUrl"] as? String) ?? (object["continuation_url"] as? String))
    }

    /// Epic's names for these steps are capital letters and underscores (PRIVACY_POLICY_ACCEPTANCE);
    /// anything else is not shown or logged.
    static func sanitizedAction(_ text: String) -> String {
        let ok = !text.isEmpty && text.utf8.count <= 48 && text.utf8.allSatisfy { ($0 >= 0x41 && $0 <= 0x5A) || $0 == 0x5F || ($0 >= 0x30 && $0 <= 0x39) }
        return ok ? text : "UNKNOWN"
    }

    static func jsonObject(_ text: String) -> [String: Any]? {
        var body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !body.hasPrefix("{"), let first = body.firstIndex(of: "{"), let last = body.lastIndex(of: "}"), first < last {
            body = String(body[first...last])
        }
        guard body.utf8.count <= 65_536, let data = body.data(using: .utf8) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    static func jsonObject(_ data: Data) -> [String: Any]? {
        guard data.count <= 16 << 20 else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    private static func isAlnum(_ c: UInt8) -> Bool { (c >= 0x30 && c <= 0x39) || (c >= 0x41 && c <= 0x5A) || (c >= 0x61 && c <= 0x7A) }

    /// An authorization code: letters and digits, 16 to 64 of them (Epic's are 32 hexadecimal digits).
    static func isCode(_ text: String) -> Bool { (16...64).contains(text.utf8.count) && text.utf8.allSatisfy(isAlnum) }

    /// 32 hexadecimal digits: the shape of the code on Epic's redirect page.
    static func isBareCode(_ text: String) -> Bool {
        text.utf8.count == 32 && text.utf8.allSatisfy { ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x46) || ($0 >= 0x61 && $0 <= 0x66) }
    }

    // MARK: Requests

    /// A form body: every name and value percent-encoded strictly.
    static func form(_ items: [(String, String)]) -> Data {
        Data(items.map { encode($0.0) + "=" + encode($0.1) }.joined(separator: "&").utf8)
    }

    static var basicAuthorization: String {
        "basic " + Data((clientID + ":" + clientSecret).utf8).base64EncodedString()
    }

    private static func tokenEndpoint(_ items: [(String, String)]) -> URLRequest {
        var request = URLRequest(url: URL(string: "https://\(accountHost)/account/api/oauth/token")!)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.setValue(basicAuthorization, forHTTPHeaderField: "Authorization")
        request.httpBody = form(items)
        return request
    }

    static func tokenRequest(code: String) -> URLRequest {
        tokenEndpoint([("grant_type", "authorization_code"), ("code", code), ("token_type", "eg1")])
    }

    static func refreshRequest(_ refreshToken: String) -> URLRequest {
        tokenEndpoint([("grant_type", "refresh_token"), ("refresh_token", refreshToken), ("token_type", "eg1")])
    }

    /// The session's identity (its account ID and display name).
    static func verifyRequest(accessToken: String) -> URLRequest {
        var request = URLRequest(url: URL(string: "https://\(accountHost)/account/api/oauth/verify")!)
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("bearer " + accessToken, forHTTPHeaderField: "Authorization")
        return request
    }

    /// Ends the session at Epic.
    static func killRequest(accessToken: String) -> URLRequest {
        var request = URLRequest(url: URL(string: "https://\(accountHost)/account/api/oauth/sessions/kill/" + encode(accessToken))!)
        request.httpMethod = "DELETE"
        request.timeoutInterval = 10
        request.setValue("bearer " + accessToken, forHTTPHeaderField: "Authorization")
        return request
    }

    // MARK: Replies

    /// Epic's token reply. A refresh that sends no new refresh token, account or name keeps the
    /// previous ones. The access token's lapse comes from `expires_in` (seconds), else `expires_at`;
    /// the refresh token's from `refresh_expires` or `refresh_expires_at`. Lifetimes are bounded.
    static func tokens(from data: Data, now: Date, previous: BCDEpicTokens? = nil) throws -> BCDEpicTokens {
        guard let reply = jsonObject(data) else { throw BCDEpicError.badReply("token") }
        func text(_ keys: String...) -> String? {
            for key in keys { if let s = reply[key] as? String, !s.isEmpty { return s } }
            return nil
        }
        func seconds(_ key: String) -> Double? {
            if let n = reply[key] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() { return n.doubleValue }
            if let s = reply[key] as? String { return Double(s) }
            return nil
        }
        guard let access = text("access_token"), isToken(access),
              let refresh = text("refresh_token") ?? previous?.refreshToken, isToken(refresh) else {
            throw BCDEpicError.badReply("token")
        }
        let at = now.timeIntervalSince1970
        var lifetime = seconds("expires_in") ?? text("expires_at").flatMap(epoch(iso:)).map { $0 - at } ?? 3600
        if !lifetime.isFinite { lifetime = 3600 }
        var refreshLifetime = seconds("refresh_expires") ?? text("refresh_expires_at").flatMap(epoch(iso:)).map { $0 - at }
        if let value = refreshLifetime, !value.isFinite { refreshLifetime = nil }
        let account = text("account_id", "accountId").flatMap { isAccountID($0) ? $0 : nil } ?? previous?.accountID
        let name = displayName(text("displayName", "display_name")) ?? previous?.displayName
        return BCDEpicTokens(accessToken: access, refreshToken: refresh,
                             accessExpires: now.addingTimeInterval(min(max(lifetime, 60), 7 * 86400)),
                             refreshExpires: refreshLifetime.map { now.addingTimeInterval(min(max($0, 60), 400 * 86400)) },
                             accountID: account, displayName: name)
    }

    /// The verify reply's account ID and display name.
    static func identity(from data: Data) -> (accountID: String?, displayName: String?) {
        guard let reply = jsonObject(data) else { return (nil, nil) }
        let account = ((reply["account_id"] as? String) ?? (reply["accountId"] as? String)).flatMap { isAccountID($0) ? $0 : nil }
        return (account, displayName((reply["display_name"] as? String) ?? (reply["displayName"] as? String)))
    }

    /// What an error answer says: its error code, and the step Epic wants finished, if it names one.
    static func failure(status: Int, body: Data) -> BCDEpicError {
        let object = jsonObject(body) ?? [:]
        let errorCode = sanitizedErrorCode(object["errorCode"] as? String)
        let action = (object["correctiveAction"] as? String) ?? (object["corrective_action"] as? String) ?? ""
        if errorCode.hasSuffix("corrective_action_required") || !action.isEmpty {
            return .corrective(action: sanitizedAction(action), continuation: continuation(in: object))
        }
        if status == 429 || (500...599).contains(status) { return .http(status) }
        return .rejected(status: status, code: errorCode)
    }

    /// Epic's error codes are dotted words ("errors.com.epicgames.account.invalid_account_credentials").
    static func sanitizedErrorCode(_ text: String?) -> String {
        guard let text, !text.isEmpty, text.utf8.count <= 120,
              text.utf8.allSatisfy({ isAlnum($0) || $0 == 0x2E || $0 == 0x5F || $0 == 0x2D }) else { return "" }
        return text
    }

    static func isToken(_ text: String) -> Bool { (8...8192).contains(text.utf8.count) && text.utf8.allSatisfy { $0 > 0x20 && $0 < 0x7F } }

    static func isAccountID(_ text: String) -> Bool { (8...64).contains(text.utf8.count) && text.utf8.allSatisfy(isAlnum) }

    static func displayName(_ text: String?) -> String? {
        guard let trimmed = text?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty, trimmed.count <= 64 else { return nil }
        return trimmed
    }

    // MARK: Dates, from numbers (no date formatter: they killed BCD in tester logs, see BCDFormat)

    /// "2026-10-22T20:11:41.158Z" as seconds since 1970; nil for any other shape.
    static func epoch(iso text: String) -> Double? {
        let b = Array(text.utf8)
        guard b.count >= 20, b[b.count - 1] == 0x5A, b[4] == 0x2D, b[7] == 0x2D, b[10] == 0x54, b[13] == 0x3A, b[16] == 0x3A else { return nil }
        func number(_ range: Range<Int>) -> Int? {
            var value = 0
            for byte in b[range] { guard byte >= 0x30, byte <= 0x39 else { return nil }; value = value * 10 + Int(byte - 0x30) }
            return value
        }
        guard let year = number(0..<4), let month = number(5..<7), let day = number(8..<10),
              let hour = number(11..<13), let minute = number(14..<16), let second = number(17..<19),
              (1970...9999).contains(year), (1...12).contains(month), (1...31).contains(day),
              hour < 24, minute < 60, second <= 60 else { return nil }
        var fraction = 0.0
        if b.count > 20 {
            guard b[19] == 0x2E, b.count - 21 >= 1, b.count - 21 <= 9, let digits = number(20..<(b.count - 1)) else { return nil }
            var scale = 1.0
            for _ in 0..<(b.count - 21) { scale *= 10 }
            fraction = Double(digits) / scale
        } else if b[19] != 0x5A {
            return nil
        }
        // Days since 1970 from the civil date (proleptic Gregorian).
        let y = month <= 2 ? year - 1 : year
        let era = y / 400
        let yoe = y - era * 400
        let doy = (153 * (month > 2 ? month - 3 : month + 9) + 2) / 5 + day - 1
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
        let days = era * 146_097 + doe - 719_468
        return Double(days * 86_400 + hour * 3600 + minute * 60 + second) + fraction
    }
}
