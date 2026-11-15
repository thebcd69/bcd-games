// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// check-gog-galaxy.py's compiled part: BCD's own parts of the GOG Galaxy features (BCDGOGGalaxyCore.swift): the game's identity, the
// kept file, the build manifest's link, and the token grant against a canned GOG. Synthetic data only; GOG is never contacted.
// The Python driver appends the calls of the test methods of Playport's GalaxyTests.swift, in that file's order, after the line "// RUN"
// (xct.swift has the few XCTest names that file calls).
import Foundation

// MARK: - BCD's parts

let secret = String(repeating: "ab", count: 32)
let client = GOGGalaxyClient(clientID: "51234567890123456", clientSecret: GalaxySecret(secret))

// A value that must not reach a log prints a fixed word, however it is printed.
expect("\(GalaxySecret("s3cret"))" == "<redacted>" && String(reflecting: GalaxySecret("s3cret")) == "<redacted>", "a GalaxySecret never prints its value")
expect(!"\(client)".contains(secret), "a client's description does not hold its secret")

// The game's identity: decimal ID, hex secret; anything else is no client. It is kept as GOG's manifest names the two fields.
expect(GOGGalaxyClient(id: "51234567890123456", secret: secret) == client, "a decimal ID and a hex secret")
for (id, s) in [(nil, secret), ("51234567890123456", nil), ("", secret), ("12ab", secret), ("51234567890123456", "zz" + secret.dropFirst(2)),
                ("51234567890123456", "abcd"), (String(repeating: "1", count: 25), secret)] as [(String?, String?)] {
    expect(GOGGalaxyClient(id: id, secret: s) == nil, "not a client: \(id ?? "nil") / \(s?.count ?? -1) characters")
}
let coded = try! JSONEncoder().encode(client)
let object = try! JSONSerialization.jsonObject(with: coded) as! [String: String]
expect(object == ["clientId": "51234567890123456", "clientSecret": secret], "kept under the build manifest's field names")
expect(try JSONDecoder().decode(GOGGalaxyClient.self, from: coded) == client, "and read back")
expect(BCDGOGGalaxyClients.client(inManifest: ["clientId": "51234567890123456", "clientSecret": secret, "version": 2]) == client, "from a build manifest")
expect(BCDGOGGalaxyClients.client(inManifest: ["clientId": NSNumber(value: 51234567890123456 as Int64), "clientSecret": secret]) == client, "an ID GOG wrote as a number")
expect(BCDGOGGalaxyClients.client(inManifest: ["clientId": "51234567890123456"]) == nil && BCDGOGGalaxyClients.client(inManifest: [:]) == nil, "a manifest without a client")

// The kept file: per product, for one build; another build, a damaged file or a bad ID is "not read yet".
let base = FileManager.default.temporaryDirectory.appendingPathComponent("bcd-gog-galaxy-" + UUID().uuidString, isDirectory: true)
defer { try? FileManager.default.removeItem(at: base) }
expect(BCDGOGGalaxyClients.known(productID: "1207658924", buildID: "100", base: base) == .unknown, "nothing kept yet")
try! BCDGOGGalaxyClients.keep(client, productID: "1207658924", buildID: "100", base: base)
expect(BCDGOGGalaxyClients.keptURL("1207658924", base: base).path.hasSuffix("/galaxy/1207658924.json"), "one file per product in the galaxy folder")
expect(BCDGOGGalaxyClients.known(productID: "1207658924", buildID: "100", base: base) == .client(client), "kept for the installed build")
expect(BCDGOGGalaxyClients.known(productID: "1207658924", buildID: "101", base: base) == .unknown, "an update reads it again")
expect(BCDGOGGalaxyClients.known(productID: "1207658925", buildID: "100", base: base) == .unknown, "another product")
expect(BCDGOGGalaxyClients.known(productID: "../1207658924", buildID: "100", base: base) == .unknown, "an ID that is not GOG's is never a path")
try! BCDGOGGalaxyClients.keep(nil, productID: "1207658924", buildID: "101", base: base)
expect(BCDGOGGalaxyClients.known(productID: "1207658924", buildID: "101", base: base) == .none, "a build that names no client is remembered too")
try! Data("{".utf8).write(to: BCDGOGGalaxyClients.keptURL("1207658924", base: base))
expect(BCDGOGGalaxyClients.known(productID: "1207658924", buildID: "101", base: base) == .unknown, "a damaged file")

// The installed build's manifest in GOG's builds list: that build, else the newest public generation-2 one; only GOG's own hosts.
let hash = "0123456789abcdef0123456789abcdef"
func item(_ id: String, generation: Int = 2, branch: Any = NSNull(), link: String) -> [String: Any] {
    ["build_id": id, "generation": generation, "branch": branch, "link": link]
}
let gogLink = "https://cdn.gog.com/content-system/v2/meta/01/23/\(hash)"
let builds: [String: Any] = ["items": [
    item("300", branch: "beta", link: "https://cdn.gog.com/content-system/v2/meta/aa/aa/a"),
    item("200", link: gogLink),
    item("150", generation: 1, link: "https://cdn.gog.com/content-system/v1/manifests/x"),
    item("100", link: "https://gog-cdn-fastly.gog.com/content-system/v2/meta/01/23/\(hash)"),
    item("90", link: "https://example.com/content-system/v2/meta/01/23/\(hash)"),
    item("80", link: "http://cdn.gog.com/content-system/v2/meta/01/23/\(hash)"),
]]
let exact = BCDGOGGalaxyClients.manifestLink(inBuilds: builds, buildID: "100")
expect(exact?.exact == true && exact?.link.absoluteString == "https://gog-cdn-fastly.gog.com/content-system/v2/meta/01/23/\(hash)", "the installed build's own manifest")
let newest = BCDGOGGalaxyClients.manifestLink(inBuilds: builds, buildID: "7")
expect(newest?.exact == false && newest?.link.absoluteString == gogLink, "a build no longer listed: the newest public generation-2 build (not a branch's)")
let moved = BCDGOGGalaxyClients.manifestLink(inBuilds: ["items": [item("90", link: "https://example.com/content-system/v2/meta/01/23/\(hash)")]], buildID: "90")
expect(moved?.link.absoluteString == "https://cdn.gog.com/content-system/v2/meta/01/23/\(hash)", "a link of that exact form on another host is read from cdn.gog.com, as an install does")
expect(BCDGOGGalaxyClients.manifestLink(inBuilds: ["items": [item("80", link: "http://cdn.gog.com/x"), item("70", link: "https://example.com/other")]], buildID: "80") == nil,
       "a link that is not GOG's is not followed")
expect(BCDGOGGalaxyClients.manifestLink(inBuilds: [:], buildID: "100") == nil && BCDGOGGalaxyClients.manifestLink(inBuilds: ["items": []], buildID: "100") == nil, "no builds")

// The game's token: the refresh-token grant at the game's own client, without a new session; BCD's kept refresh token is only read.
final class CannedGOG: @unchecked Sendable {
    var requests: [URLRequest] = []
    var reply: Result<Data, Error> = .success(Data())
    func fetch(_ request: URLRequest) async throws -> Data {
        requests.append(request)
        return try reply.get()
    }
}
func query(_ request: URLRequest) -> [String: String] {
    Dictionary(uniqueKeysWithValues: (URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []).map { ($0.name, $0.value ?? "") })
}
let hostToken = "host-refresh-" + String(repeating: "9", count: 40)
let gameRefresh = "game-refresh-" + String(repeating: "7", count: 40)
let gameAccess = "game-access-" + String(repeating: "5", count: 40)
let fixed = Date(timeIntervalSince1970: 1_800_000_000)
let gog = CannedGOG()
var tokens = BCDGOGGalaxyTokens(hostRefreshToken: { hostToken }, fetch: { try await gog.fetch($0) })
tokens.now = { fixed }
gog.reply = .success(Data(#"{"access_token":"\#(gameAccess)","refresh_token":"\#(gameRefresh)","expires_in":3600,"user_id":"48123456789012345"}"#.utf8))
let minted = try await tokens.gameToken(for: client)
expect(minted.accessToken.value == gameAccess && minted.refreshToken.value == gameRefresh && minted.userID == "48123456789012345"
       && minted.expiresAt == fixed.addingTimeInterval(3600), "GOG's reply becomes the game's token")
let first = gog.requests[0]
expect(first.url?.host == "auth.gog.com" && first.url?.path == "/token" && first.url?.scheme == "https", "the grant goes to GOG's token address")
expect(query(first) == ["client_id": "51234567890123456", "client_secret": secret, "grant_type": "refresh_token", "refresh_token": hostToken,
                        "without_new_session": "1"], "the game's client, BCD's refresh token, and no new session")
gog.reply = .success(Data(#"{"access_token":"a2","refresh_token":"r2","user_id":48123456789012345}"#.utf8))
let renewed = try await tokens.refreshGameToken(GalaxySecret(gameRefresh), for: client)
expect(query(gog.requests[1])["refresh_token"] == gameRefresh && renewed.accessToken.value == "a2" && renewed.userID == "48123456789012345"
       && renewed.expiresAt == fixed.addingTimeInterval(3600), "a refresh uses the game's own token; a numeric user ID and a missing lifetime are read")
for status in [400, 401, 403] {
    gog.reply = .failure(BCDContentError.http(status))
    do { _ = try await tokens.gameToken(for: client); fail("HTTP \(status) is a refusal") } catch let error as GalaxyError {
        expect(error == .refused("GOG game token: HTTP \(status)"), "HTTP \(status) is GOG refusing the game's token")
    } catch { fail("HTTP \(status): \(error)") }
}
gog.reply = .failure(BCDContentError.http(500))
do { _ = try await tokens.gameToken(for: client); fail("HTTP 500 is not a token") } catch { expect(!(error is GalaxyError), "another failure stays what it was (the service answers 'unavailable')") }
for body in [#"{}"#, #"{"access_token":"a","refresh_token":"r"}"#, #"{"access_token":"a","refresh_token":"r","user_id":"not-a-number"}"#,
             #"{"access_token":"","refresh_token":"r","user_id":"1"}"#, "not json"] {
    gog.reply = .success(Data(body.utf8))
    do { _ = try await tokens.gameToken(for: client); fail("a reply that is not a token: \(body)") } catch let error as GalaxyError {
        expect(error == .protocolChanged("GOG game token: unexpected reply"), "an unexpected reply names no part of the reply")
    } catch { fail("\(error)") }
}
let signedOut = BCDGOGGalaxyTokens(hostRefreshToken: { nil }, fetch: { try await gog.fetch($0) })
let asked = gog.requests.count
do { _ = try await signedOut.gameToken(for: client); fail("signed out") } catch let error as GalaxyError {
    expect(error == .notLoggedOn && gog.requests.count == asked, "signed out of GOG: no request is made")
} catch { fail("\(error)") }

// The log's lines carry BCD's tag; what the service writes about a play holds no token and no secret (BCD's redaction pass on top).
final class Lines: @unchecked Sendable {
    private let lock = NSLock()
    private var all: [String] = []
    func add(_ line: String) { lock.lock(); all.append(line); lock.unlock() }
    var text: String { lock.lock(); defer { lock.unlock() }; return all.joined(separator: "\n") }
}
let lines = Lines()
let log = GalaxyLog { lines.add(BCDContentLog.redact($0)) }
log.info("galaxy", "the Galaxy service listens")
log.warn("galaxy", "AUTH_INFO refused")
expect(lines.text == "[gog-galaxy] the Galaxy service listens\n[gog-galaxy] warning: AUTH_INFO refused", "the lines' tag")
gog.reply = .success(Data(#"{"access_token":"\#(gameAccess)","refresh_token":"\#(gameRefresh)","expires_in":3600,"user_id":"48123456789012345"}"#.utf8))
/// GOG's servers, absent: every request is answered 404 here.
struct NoGOG: GalaxyHTTP {
    func send(_ method: String, _ url: URL, bearer: GalaxySecret?, json: [UInt8]?, language: String?) async throws -> (status: Int, body: [UInt8]) { (404, []) }
}
let service = GalaxyService(client: client, tokens: tokens, http: NoGOG(), log: log)
var auth = GalaxyProto.Writer()
auth.string(1, client.clientID)
auth.string(2, secret)
// AUTH_INFO with BCD's token source: the reply's bytes hold the game's refresh token; the log holds neither token nor secret.
// (The account's public name is asked of users.gog.com without a token; here that answer is 404 and the name is left out.)
let replies = await service.handle(GalaxyFrame(sort: 1, type: 3, oseq: 1, payload: auth.bytes))
expect(replies.count == 2 && replies[1].type == 4 && replies[1].status == nil, "AUTH_INFO for the build's client is answered")
if replies.count == 2, let fields = try? GalaxyFields(replies[1].payload) {
    expect(fields.string(1) == gameRefresh && fields.fixed64(3) == (2 << 56 | 48123456789012345), "the game gets its own refresh token and the account's ID")
}
for hidden in [secret, hostToken, gameRefresh, gameAccess, "48123456789012345"] {
    expect(!lines.text.contains(hidden), "the log holds no token, secret or user ID")
}
expect(lines.text.contains("AUTH_INFO"), "the log names the request")

// A failing call (BCD, U6 counter-review M1): GOG answers 404, or the connection fails with a URLError that names the URL. The log names the
// request, the status or the error's code, and the path with the user ID masked; the user ID's digits are in none of the lines.
let user = "48123456789012345"
let statsReply = await service.handle(GalaxyFrame(sort: 1, type: GalaxyMessage.getUserStatsRequest.rawValue, oseq: 2, payload: []))
let achievementsReply = await service.handle(GalaxyFrame(sort: 1, type: GalaxyMessage.getUserAchievementsRequest.rawValue, oseq: 3, payload: []))
expect(statsReply.last?.status == GalaxyFrame.Status.notFound.rawValue && achievementsReply.last?.status == GalaxyFrame.Status.notFound.rawValue, "GOG's 404 reaches the game")
expect(lines.text.contains("/users/<user>/stats: HTTP 404") && lines.text.contains("/users/<user>/achievements: HTTP 404"),
       "a failing request's line names its path with the user masked")
/// GOG's servers, unreachable: every request fails as URLSession fails, with the URL in the error.
struct OfflineGOG: GalaxyHTTP {
    func send(_ method: String, _ url: URL, bearer: GalaxySecret?, json: [UInt8]?, language: String?) async throws -> (status: Int, body: [UInt8]) {
        throw URLError(.notConnectedToInternet, userInfo: [NSURLErrorFailingURLErrorKey: url, NSURLErrorFailingURLStringErrorKey: url.absoluteString])
    }
}
expect("\(URLError(.notConnectedToInternet, userInfo: [NSURLErrorFailingURLStringErrorKey: "https://gameplay.gog.com/users/\(user)"]))".contains(user),
       "a URLError's own text names its URL (what the log must not print)")
let offline = GalaxyService(client: client, tokens: tokens, http: OfflineGOG(), log: log)
let offlineAuth = await offline.handle(GalaxyFrame(sort: 1, type: 3, oseq: 1, payload: auth.bytes))
expect(offlineAuth.count == 2, "AUTH_INFO is answered with GOG's users service unreachable")
let offlineReply = await offline.handle(GalaxyFrame(sort: 1, type: GalaxyMessage.getUserAchievementsRequest.rawValue, oseq: 2, payload: []))
expect(offlineReply.last?.status == GalaxyFrame.Status.unavailable.rawValue, "an unreachable GOG is unavailable to the game")
expect(lines.text.contains("GET_USER_ACHIEVEMENTS: network error \(URLError.Code.notConnectedToInternet.rawValue)"),
       "a failed connection's line names the error's code")
expect(!lines.text.contains(user) && !lines.text.contains("gameplay.gog.com"), "no line holds the user ID or a request's URL")
expect(GalaxyService.logPath("/clients/5012/users/\(user)/stats/77") == "/clients/5012/users/<user>/stats/77"
       && GalaxyService.logPath("/users/\(user)") == "/users/<user>" && GalaxyService.logPath("/clients/5012/users") == "/clients/5012/users",
       "logPath masks only the user segment")

// GOG's dates, read and written from numbers (BCD's change to GalaxyService: no ICU date formatter anywhere in the app).
expect(GalaxyService.parseDate("2026-10-07T12:00:00+0000")?.timeIntervalSince1970 == 1791374400, "GOG's own shape, +0000")
expect(GalaxyService.parseDate("2026-10-07T12:00:00+00:00")?.timeIntervalSince1970 == 1791374400 && GalaxyService.parseDate("2026-10-07T12:00:00Z")?.timeIntervalSince1970 == 1791374400, "+00:00 and Z")
expect(GalaxyService.parseDate("2026-10-07T14:30:00+02:30")?.timeIntervalSince1970 == 1791374400 && GalaxyService.parseDate("2026-10-07T07:00:00-0500")?.timeIntervalSince1970 == 1791374400, "a zone that is not UTC")
expect(GalaxyService.parseDate("2026-10-07T12:00:00.250+00:00")?.timeIntervalSince1970 == 1791374400.25, "a fraction")
expect(GalaxyService.parseDate("1999-12-31T23:59:59Z")?.timeIntervalSince1970 == 946684799 && GalaxyService.parseDate("2024-02-29T00:00:00Z")?.timeIntervalSince1970 == 1709164800, "a year's end and a leap day")
for bad in ["", "2026-10-07", "2026-10-07 12:00:00+0000", "2026-13-07T12:00:00Z", "2026-10-07T24:00:00Z", "2026-10-07T12:00:00", "2026-10-07T12:00:00+00", "2026-10-07T12:00:00Zx",
            "2026-10-07T12:00:00.Z", "20a6-10-07T12:00:00Z", "2026-10-07T12:00:00+0000 "] {
    expect(GalaxyService.parseDate(bad) == nil, "not a date: '\(bad)'")
}
expect(GalaxyService.rfc3339(Date(timeIntervalSince1970: 1791374400)) == "2026-10-07T12:00:00+00:00" && GalaxyService.rfc3339(Date(timeIntervalSince1970: 946684799.9)) == "1999-12-31T23:59:59+00:00"
       && GalaxyService.rfc3339(Date(timeIntervalSince1970: 1709164800)) == "2024-02-29T00:00:00+00:00" && GalaxyService.rfc3339(Date(timeIntervalSince1970: -2)) == "1969-12-31T23:59:58+00:00",
       "written as GOG takes it, in UTC")
var day = -800_000
while day <= 800_000 {
    let c = GalaxyService.civilFromDays(day)
    if GalaxyService.daysFromCivil(c.year, c.month, c.day) != day || !(1...12).contains(c.month) || !(1...31).contains(c.day) { fail("day \(day) does not round-trip"); break }
    day += 37
}

// RUN
