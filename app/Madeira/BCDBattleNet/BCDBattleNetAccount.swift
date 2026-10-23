// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Account portal session, not an OAuth game-login token. Never passed to a Windows game.
struct BCDBattleNetCredential: Codable, CustomStringConvertible, CustomDebugStringConvertible {
    struct Cookie: Codable {
        var name: String
        var value: String
        var domain: String
        var path: String
        var expires: Date?

        init?(_ cookie: HTTPCookie, origin: URL, now: Date) {
            guard cookie.isSecure, BCDBattleNetAccount.applies(cookie.domain, to: origin.host ?? ""),
                  cookie.expiresDate.map({ $0 > now }) ?? true,
                  !cookie.name.isEmpty, cookie.name.utf8.count < 256, cookie.value.utf8.count < 16384,
                  !cookie.name.contains(where: { $0.isNewline || $0 == ";" }),
                  !cookie.value.contains(where: { $0.isNewline || $0 == ";" }) else { return nil }
            name = cookie.name; value = cookie.value; domain = cookie.domain
            path = cookie.path; expires = cookie.expiresDate
        }
    }
    var origin: URL
    var cookies: [Cookie]
    var cachedName: String? = nil
    var cachedProducts: [BCDBattleNetProduct]? = nil
    var updated: Date? = nil
    var description: String { "BCDBattleNetCredential(<redacted>)" }
    var debugDescription: String { description }

    func request(_ path: String, now: Date) throws -> URLRequest {
        guard BCDBattleNetAccount.validOrigin(origin), ["/api/games-and-subs", "/api/details"].contains(path),
              let url = URL(string: path, relativeTo: origin)?.absoluteURL else { throw BCDBattleNetError.reply }
        let live = cookies.filter {
            BCDBattleNetAccount.applies($0.domain, to: origin.host ?? "") && ($0.expires.map { $0 > now } ?? true)
                && path.hasPrefix($0.path) && !$0.name.isEmpty
                && !$0.name.contains(where: { $0.isNewline || $0 == ";" })
                && !$0.value.contains(where: { $0.isNewline || $0 == ";" })
        }
        guard !live.isEmpty else { throw BCDBattleNetError.signedOut }
        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(origin.appendingPathComponent("games").absoluteString, forHTTPHeaderField: "Referer")
        request.setValue(live.map { $0.name + "=" + $0.value }.joined(separator: "; "), forHTTPHeaderField: "Cookie")
        return request
    }
}

enum BCDBattleNetError: Error, LocalizedError {
    case signedOut, reply, http(Int), format(String), unsupported(String)
    var errorDescription: String? {
        switch self {
        case .signedOut: return "Sign in to Battle.net again to refresh your games."
        case .reply: return "BCD couldn't read Battle.net's account reply. Try again later."
        case .http: return "Battle.net couldn't be reached. Try again later."
        case .format: return "Battle.net's download metadata failed its checks. Try again later."
        case .unsupported(let feature): return "This Battle.net build needs an unsupported download feature: \(feature)."
        }
    }
}

struct BCDBattleNetProduct: Codable, Equatable, Identifiable {
    var id: String
    var title: String
}

enum BCDBattleNetAccount {
    static let loginURL = URL(string: "https://account.battle.net/games")!
    static let secretItem = "bcd.battlenet.session"

    static func validOrigin(_ url: URL) -> Bool {
        guard url.scheme == "https", url.user == nil, url.password == nil,
              url.port == nil || url.port == 443, let host = url.host?.lowercased() else { return false }
        return ["account.battle.net", "account.blizzard.com", "us.account.blizzard.com",
                "eu.account.blizzard.com", "kr.account.blizzard.com"].contains(host)
    }

    static func applies(_ domain: String, to host: String) -> Bool {
        let domain = domain.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return !domain.isEmpty && (host == domain || host.hasSuffix("." + domain))
    }

    /// Account entries are licences, not evidence that any particular WoW client can run.
    /// Several WoW subscriptions on one account produce one library row per client flavour.
    static func products(_ data: Data) throws -> [BCDBattleNetProduct] {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let accounts = object["gameAccounts"] as? [[String: Any]], accounts.count <= 10000 else {
            throw BCDBattleNetError.reply
        }
        let ownsWoW = accounts.contains { account in
            let id = (account["titleId"] as? NSNumber)?.stringValue ?? account["titleId"] as? String
            return id == "5730135"
        }
        guard ownsWoW else { return [] }
        return [.init(id: "wow", title: "World of Warcraft"),
                .init(id: "wow_classic", title: "World of Warcraft Classic"),
                .init(id: "wow_classic_era", title: "World of Warcraft Classic Era")]
    }

    static func name(_ data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        let name = (object["battleTag"] as? String ?? object["battletag"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let name, !name.isEmpty, name.count <= 128 else { return nil }
        return name
    }

    static func installedProduct(_ path: String) -> String? {
        let parts = path.replacingOccurrences(of: "\\", with: "/").lowercased().split(separator: "/")
        guard parts.count >= 2 else { return nil }
        switch (parts[parts.count - 2], parts[parts.count - 1]) {
        case ("_retail_", "wow.exe"): return "wow"
        case ("_classic_", "wowclassic.exe"): return "wow_classic"
        case ("_classic_era_", "wowclassic.exe"): return "wow_classic_era"
        default: return nil
        }
    }
}
