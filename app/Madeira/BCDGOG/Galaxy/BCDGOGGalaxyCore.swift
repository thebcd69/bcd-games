// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 the Playport authors: code taken from Playport's GOGClientKit
// (https://github.com/playportdev/playport/tree/412de5f8c0bdd26d84e0127eb5776ba992e3b152/app), as Madeira carries it
// (https://github.com/willfaust/Madeira branch playport/gog, commit 9d6dad8 by intraducine), under GPL-3.0-or-later with the Madeira
// Converter Exception as adopted by Playport, and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation

// GOG Galaxy features for a GOG game BCD installed (docs/GOG.md, "Galaxy"): achievements, statistics, leaderboards and play time,
// which a game's Galaxy SDK asks GOG's own client for on 127.0.0.1:9977. BCD has no GOG client in Wine; with the game's switch on,
// BCD answers on that port itself while the game runs (GalaxyListener, GalaxyService and GalaxyWire: Playport's, as Madeira carries
// them) and serves the requests from GOG's servers with a token minted for that game from BCD's own GOG sign-in.
//
// Off by default, per game: `env.BCD_GOG_GALAXY = 1` in the game's own config (the game page's GOG Galaxy switch writes it) or in
// bcd.cfg. Without it nothing here runs: no socket is opened, GOG is not asked anything, and the game starts exactly as before.
//
// This file is the part without the engine (the host checks compile it as it is):
// - the three small types Playport's files take from its ContentKit package, which BCD does not carry (GalaxySecret, GalaxyError,
//   GalaxyLog), and GOGGalaxyClient, the game's own Galaxy identity from its build manifest (Playport's GOGContent.swift);
// - BCDGOGGalaxyTokens: the game's token, from BCD's GOG session (the grant is Playport's GOGSession.gameGrant);
// - BCDGOGGalaxyClients: the game's identity, read once per installed build from GOG's build manifest and kept beside the install records.
// BCDGOGGalaxy.swift holds the switch and the listener's life: up from a start of the game until its session ends.
// Log tag [gog-galaxy]: request names, counts and status codes; never a token, the client secret, a user ID or a payload.

// MARK: - What Playport's files take from ContentKit

/// Playport's `Secret<String>`: a value that must not reach a log; printing it gives a fixed word.
/// (These four types are `public` only because Playport's files declare their own API public; nothing outside the app sees them.)
public struct GalaxySecret: Sendable, Equatable, CustomStringConvertible, CustomDebugStringConvertible {
    public let value: String
    public init(_ value: String) { self.value = value }
    public var description: String { "<redacted>" }
    public var debugDescription: String { "<redacted>" }
}

/// The failures the Galaxy files tell apart (Playport's ClientError has many more; these are the ones they use).
public enum GalaxyError: Error, Equatable {
    /// Nobody is signed in to GOG in BCD.
    case notLoggedOn
    /// GOG refused the game's token; the text is a status, never a reply's body.
    case refused(String)
    case protocolChanged(String)
    case unsafeContent(String)
}

/// Playport's `Logger`, as far as the Galaxy files use it: a category and a line. BCD's sink is BCDContentLog (its redaction pass
/// runs on every line); the host checks collect the lines.
public struct GalaxyLog: Sendable {
    private let sink: @Sendable (String) -> Void
    public init(_ sink: @escaping @Sendable (String) -> Void) { self.sink = sink }
    /// Nowhere (Playport's `Logger.silent`; its tests use it).
    public static let silent = GalaxyLog { _ in }
    public func info(_ category: String, _ message: @autoclosure () -> String) { sink("[gog-\(category)] " + message()) }
    public func warn(_ category: String, _ message: @autoclosure () -> String) { sink("[gog-\(category)] warning: " + message()) }
}

/// A game's own Galaxy client identity, from its build manifest: what the Galaxy SDK in the game sends to the local Galaxy service,
/// and what the service checks it against before a token is minted for the game. Public (every build manifest carries it), but a
/// game identity: the secret is never logged. (Playport's GOGGalaxyClient, GOGContent.swift.)
public struct GOGGalaxyClient: Codable, Equatable, Sendable {
    public var clientID: String
    public var clientSecret: GalaxySecret

    public init(clientID: String, clientSecret: GalaxySecret) {
        self.clientID = clientID
        self.clientSecret = clientSecret
    }

    /// GOG's client IDs are decimal and its secrets hex: anything else (a missing or placeholder field) is no client.
    public init?(id: String?, secret: String?) {
        guard let id, let secret, (1...24).contains(id.count), id.allSatisfy({ $0.isASCII && $0.isNumber }),
              (16...128).contains(secret.count), secret.allSatisfy({ $0.isASCII && $0.isHexDigit }) else { return nil }
        self.init(clientID: id, clientSecret: GalaxySecret(secret))
    }

    enum CodingKeys: String, CodingKey { case clientID = "clientId", clientSecret }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        clientID = try c.decode(String.self, forKey: .clientID)
        clientSecret = GalaxySecret(try c.decode(String.self, forKey: .clientSecret))
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(clientID, forKey: .clientID)
        try c.encode(clientSecret.value, forKey: .clientSecret)
    }
}

// MARK: - The game's token

/// The game-scoped token, minted from BCD's GOG sign-in. The grant is the refresh-token grant at the game's own client with
/// `without_new_session=1` (Playport's GOGSession.gameGrant): BCD's kept tokens are read, and neither used up nor replaced.
struct BCDGOGGalaxyTokens: GalaxyTokenSource {
    /// BCD's kept refresh token, or nil when nobody is signed in.
    let hostRefreshToken: @Sendable () async -> String?
    /// One request, a bounded reply; throws BCDContentError.http(status) for a refusal.
    let fetch: @Sendable (URLRequest) async throws -> Data
    var now: @Sendable () -> Date = { Date() }

    func gameToken(for client: GOGGalaxyClient) async throws -> GOGGameToken {
        guard let host = await hostRefreshToken() else { throw GalaxyError.notLoggedOn }
        return try await grant(host, client, label: "GOG game token")
    }

    func refreshGameToken(_ refreshToken: GalaxySecret, for client: GOGGalaxyClient) async throws -> GOGGameToken {
        try await grant(refreshToken.value, client, label: "GOG game token refresh")
    }

    static func grantRequest(_ refreshToken: String, _ client: GOGGalaxyClient) -> URLRequest {
        var request = URLRequest(url: URL(string: "https://auth.gog.com/token?" + BCDGOGAuth.query([
            ("client_id", client.clientID), ("client_secret", client.clientSecret.value), ("grant_type", "refresh_token"),
            ("refresh_token", refreshToken), ("without_new_session", "1")]))!)
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    /// GOG's reply to the grant: the game's tokens and the account's ID (a number).
    static func token(from data: Data, now: Date, label: String) throws -> GOGGameToken {
        guard let o = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let access = o["access_token"] as? String, !access.isEmpty, let refresh = o["refresh_token"] as? String, !refresh.isEmpty,
              let user = (o["user_id"] as? String) ?? (o["user_id"] as? NSNumber)?.stringValue, UInt64(user) != nil else {
            throw GalaxyError.protocolChanged("\(label): unexpected reply")
        }
        let life = (o["expires_in"] as? NSNumber)?.doubleValue ?? 3600
        return GOGGameToken(accessToken: GalaxySecret(access), refreshToken: GalaxySecret(refresh), expiresAt: now.addingTimeInterval(life), userID: user)
    }

    private func grant(_ refreshToken: String, _ client: GOGGalaxyClient, label: String) async throws -> GOGGameToken {
        let data: Data
        do {
            data = try await fetch(Self.grantRequest(refreshToken, client))
        } catch BCDContentError.http(let status) where status == 400 || status == 401 || status == 403 {
            throw GalaxyError.refused("\(label): HTTP \(status)")
        }
        return try Self.token(from: data, now: now(), label: label)
    }
}

// MARK: - The game's identity, kept beside the install records

/// A game's Galaxy client: read once per installed build from GOG's build manifest and kept in a file of its own
/// (`Application Support/BCD/GOG/galaxy/<id>.json`), so the install records stay as they are.
enum BCDGOGGalaxyClients {
    /// The client name of the service's own requests to GOG (Playport sends "GOGGalaxyClient/2.0 Playport").
    static let userAgent = "GOGGalaxyClient/2.0 BCD"

    /// What is known of an installed build's Galaxy identity.
    enum Known: Equatable {
        /// Not read yet for this build.
        case unknown
        /// The build's manifest names no usable client: the game has no Galaxy features to serve.
        case none
        case client(GOGGalaxyClient)
    }

    /// The file's shape: the build it was read for, and the client (absent: the build names none).
    struct Kept: Codable, Equatable {
        var buildID: String
        var client: GOGGalaxyClient?
    }

    static func keptURL(_ productID: String, base: URL) -> URL {
        base.appendingPathComponent("galaxy", isDirectory: true).appendingPathComponent(productID + ".json")
    }

    /// The identity kept for `productID`, if it was read for the build that is installed now (an update reads it again).
    static func known(productID: String, buildID: String, base: URL) -> Known {
        guard BCDGOGCatalog.validID(productID), let data = try? Data(contentsOf: keptURL(productID, base: base)), data.count <= 4096,
              let kept = try? JSONDecoder().decode(Kept.self, from: data), kept.buildID == buildID else { return .unknown }
        return kept.client.map(Known.client) ?? .none
    }

    static func keep(_ client: GOGGalaxyClient?, productID: String, buildID: String, base: URL) throws {
        let url = keptURL(productID, base: base)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(Kept(buildID: buildID, client: client)).write(to: url, options: .atomic)
    }

    /// The link of the installed build's manifest in GOG's builds list (`items`, newest first): the build with that ID, else the
    /// newest public generation-2 build (a game's Galaxy client does not change between its builds). Only a link on GOG's own
    /// hosts is followed (BCDGOGAPI.trusted, BCDGOGContent.gogHostLink), as for an install.
    static func manifestLink(inBuilds object: [String: Any], buildID: String) -> (link: URL, exact: Bool)? {
        guard let items = object["items"] as? [[String: Any]] else { return nil }
        var newest: URL?
        for item in items {
            guard (item["generation"] as? NSNumber)?.intValue == 2 else { continue }
            let id = (item["build_id"] as? String) ?? (item["build_id"] as? NSNumber)?.stringValue ?? ""
            guard let given = (item["link"] as? String).flatMap(URL.init(string:)),
                  let link = BCDGOGAPI.trusted(given) ? given : BCDGOGContent.gogHostLink(given) else { continue }
            if id == buildID { return (link, true) }
            if newest == nil, item["branch"] == nil || item["branch"] is NSNull { newest = link }
        }
        return newest.map { ($0, false) }
    }

    /// The Galaxy client a build manifest names (`clientId`, `clientSecret`), or nil.
    static func client(inManifest object: [String: Any]) -> GOGGalaxyClient? {
        func text(_ value: Any?) -> String? { (value as? String) ?? (value as? NSNumber)?.stringValue }
        return GOGGalaxyClient(id: text(object["clientId"]), secret: object["clientSecret"] as? String)
    }
}
