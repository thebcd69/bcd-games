#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""GOG in BCD, milestone 1 (app/Madeira/BCDGOG/; docs/GOG.md).

Never contacts GOG and holds no account data: GOG's replies are synthetic, written here, and
GOG's hosts are stand-ins (a URLProtocol for auth.gog.com and users.gog.com, a local HTTP server
for the content).

Source checks (every host):
  - BCD's licence header, exactly, on every GOG file; each built by the Xcode project with an ID
    from the content range; all of them below app/Madeira's top level, where the preview build
    (tools/ui-preview.py, a top-level BCD*.swift glob) never compiles them, and no screen names them;
  - the tokens: one Keychain item ("bcd.gog.tokens", this device only, readable after the first
    unlock), never printed (the token type's descriptions say <redacted>), never in a log line,
    the environment, UserDefaults or a file; GOG's client secret only in BCDGOGAuth.swift;
  - the sign-in sheet: WebKit's own user agent, a data store that lives only as long as the sheet,
    provider windows, the Safari fallback;
  - the settings catalog lists env.BCD_GOG (on) and env.BCD_GOG_SFC (off);
  - Steam's files name nothing of GOG or the content engine.
Behaviour (swiftc on macOS; --require-swift makes its absence a failure):
  - sign-in: the login address, the redirect matcher against hostile addresses, the code
    exchange, the user name, refresh five minutes early and once for many callers, a refused
    refresh signing out, sign-out, an API call refused with 401 retried once after a refresh,
    and not one token, code, secret or account name in the log;
  - the library: Windows games only, IDs and picture hosts checked, gamesdb's covers, the art lists;
  - content: the newest public generation-2 build, older-only and missing builds, the build and
    depot manifests, the language choice, the depot choice (language, neutral, 64-bit, each once),
    support files and links left out, unknown file sources refused, the small-files container
    (sliced when on, or when a file has no chunks of its own), secure links (order, expiry,
    renewal) and chunk addresses;
  - install end to end: GOG's manifests (zlib) planned, the chunks downloaded from a local server
    through GOG's chunk addresses, committed under C:\\GOG Games (a clash gets " (GOG)"), the play
    task read from goggame-<id>.info and matched to the files' letter case, the library entry's
    program, arguments, working folder and start mode, the record kept and read back;
  - play tasks: primary, the first game task, URL tasks ignored, hostile paths refused.

  check-gog.py [--require-swift]
"""
import argparse
import hashlib
import http.server
import json
import os
import random
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import zlib
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
GOG = APP / "BCDGOG"
CONTENT = APP / "BCDContent"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
          "// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
FILES = ["BCDGOGAuth.swift", "BCDGOGCatalog.swift", "BCDGOGContent.swift", "BCDGOGInstall.swift", "BCDGOG.swift",
         "BCDLiveStoreGOG.swift", "BCDGOGRegistry.swift", "BCDGOGUpdate.swift"]
# Upstream Madeira's files, as they came (their own licence headers; tests/host/check-gog-script.py checks them): not BCD Material.
UPSTREAM_FILES = ["GOGInstallScript.swift", "GOGSetup.swift"]
HOST_FILES = ["BCDGOGAuth.swift", "BCDGOGCatalog.swift", "BCDGOGContent.swift", "BCDGOGInstall.swift", "GOGInstallScript.swift", "GOGSetup.swift", "BCDGOGUpdate.swift"]
CONTENT_HOST_FILES = ["BCDContentPlan.swift", "BCDContentCodec.swift", "BCDContentJournal.swift", "BCDContentFetch.swift",
                      "BCDContentEngine.swift", "BCDContentQueue.swift", "BCDContentSecrets.swift"]
SECRET = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"

# ---- source checks ------------------------------------------------------------------------
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check(sorted(p.name for p in GOG.glob("*.swift")) == sorted(FILES + UPSTREAM_FILES), "app/Madeira/BCDGOG holds exactly GOG's files",
      sorted(p.name for p in GOG.glob("*.swift")))
sources = {}
for name in FILES:
    text = (GOG / name).read_text()
    sources[name] = text
    check(text.startswith(HEADER), f"{name}: BCD's licence header, exactly")
    build = re.findall(r"(A1000C[0-3][0-9A-F]) /\* BCDGOG/" + re.escape(name) + r" in Sources \*/ = \{isa = PBXBuildFile; fileRef = (A2000C[0-3][0-9A-F])", pbx)
    ref = re.findall(r"(A2000C[0-3][0-9A-F]) /\* " + re.escape(name) + r' \*/ = \{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "BCDGOG/' + re.escape(name) + '"', pbx)
    ok = len(build) == 1 and len(ref) == 1 and build[0][1] == ref[0] \
        and pbx.count(f"{build[0][0]} /* BCDGOG/{name} in Sources */,") == 1 and pbx.count(f"{ref[0]} /* {name} */,") == 1
    check(ok, f"{name}: built by the Xcode project (content ID range, group and Sources phase)")
    for call in re.findall(r"BCDContentLog\.write\((.*)\)", code(text)):
        bad = [e for e in re.findall(r"\\\(([^)]*)\)", call)
               if re.search(r"(?i)url|path|token|code\b|secret|folder|host|title|name|link|account|user", e)]
        check(not bad, f"{name}: a log line carries no address, path, token or name", call)
    if name != "BCDGOGAuth.swift":
        check(SECRET not in text, f"{name}: GOG's client secret only in BCDGOGAuth.swift")
    check("UserDefaults" not in code(text) and "setenv" not in code(text), f"{name}: nothing GOG keeps goes to UserDefaults or the environment")

preview = (R / "tools/ui-preview.py").read_text()
# Recursive resource hashing does not change the compiler's source selection.
# Keep this engine-file exclusion check on the actual compilation function.
preview_build = preview.split("def _build(out):", 1)[-1].split("\ndef ", 1)[0]
check('APP.glob("BCD*.swift")' in preview_build and "rglob" not in preview_build,
      "the preview build compiles app/Madeira's top-level BCD*.swift only, so the engine-bound GOG files stay out")
screens = [p for p in APP.glob("BCD*.swift") if p.name not in ("BCDLiveStore.swift", "BCDLiveFrontEnd.swift", "BCDSetupView.swift")
           and not p.name.endswith("Live.swift")]
named = sorted(p.name for p in screens if re.search(r"\bBCDGOG\w*|\bBCDContent(Queue|Engine)\b", code(p.read_text())))
check(not named, "no screen names GOG's code or the content engine", named)
auth = sources["BCDGOGAuth.swift"]
check('static let itemName = "bcd.gog.tokens"' in auth and auth.count("JSONEncoder().encode(fresh)") == 1
      and sum(t.count("JSONEncoder().encode(") for n, t in sources.items() if "BCDGOGTokens" in t and n != "BCDGOGAuth.swift") == 0,
      "the tokens are written in one place, the store's secret item")
check('var description: String { "BCDGOGTokens(<redacted>)" }' in auth and "var debugDescription: String { description }" in auth,
      "printing the tokens shows nothing of them")
secrets = (CONTENT / "BCDContentSecrets.swift").read_text()
check("kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly" in secrets and "kSecAttrSynchronizable as String: false" in secrets,
      "the Keychain item is this device's only, never synced, readable after the first unlock")
sheet = code((CONTENT / "BCDWebSignIn.swift").read_text())
# 2026-10-22: the sheet is shared with Epic Games, whose page may name a user agent (Page.userAgent). GOG's page never does:
# its Cloudflare check fails a custom one.
check("configuration.websiteDataStore = .nonPersistent()" in sheet and sheet.count("customUserAgent") == 1
      and "if let agent = page.userAgent, !agent.isEmpty { web.customUserAgent = agent }" in sheet
      and "userAgent" not in code(sources["BCDGOG.swift"])
      and "createWebViewWith" in sheet and "UIApplication.shared.open(page.start)" in sheet,
      "the sign-in sheet: WebKit's own user agent unless the store's page names one (GOG's never does), a store that dies with it, provider windows, Safari and paste")
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
check('ConfigOption(key: "env.BCD_GOG", title: "", kind: .bool, defaultValue: "1"' in catalog
      and 'ConfigOption(key: "env.BCD_GOG_SFC", title: "", kind: .bool, defaultValue: "0"' in catalog,
      "Settings › All settings lists env.BCD_GOG (on) and env.BCD_GOG_SFC (off)")
facade = code(sources["BCDGOG.swift"])
check('MadeiraConfig.flag("BCD_GOG", fallback: true)' in facade and 'MadeiraConfig.flag("BCD_GOG_SFC", fallback: false)' in facade,
      "the switches read as the catalog lists them")
live = code(sources["BCDLiveStoreGOG.swift"])
check(all(f"func {h}" in live for h in ["gogMerge(_ games: inout [BCDGame])", "gogDownloads() -> [BCDDownload]", "gogAccount() -> BCDAccount",
                                         "gogPlayEntry(_ game: BCDGame) -> LibraryEntry?", "gog(_ action: BCDGOGAction, _ id: String) -> Bool",
                                         "gogAccountAction(signIn: Bool, _ front: BCDStoreFront) -> Bool", "gogRefresh()"])
      and all("BCDGOG.enabled" in live[live.index(f"func {h}"):live.index(f"func {h}") + 400]
              for h in ["gogMerge", "gogDownloads", "gogPlayEntry", "gog(_ action", "gogAccountAction", "gogRefresh"])
      and "BCDGOG.enabled ? BCDGOG.shared.changes : Empty" in live,
      "the live store's GOG hooks exist, each doing nothing with env.BCD_GOG = 0")
store = code((APP / "BCDLiveStore.swift").read_text())
wired = {
    "changes": "gogChanges," in store,
    "games": "gogMerge(&games)" in store,
    "downloads": "downloadList(names: names) + gogDownloads()" in store,
    "account": "gogAccount()," in store,
    "play": "if let entry = gogPlayEntry(game) { begin(entry, launch); return }" in store,   # begin: the graphics tip first (build 622)
    "actions": all(f"if gog(.{a}, {arg}) {{ return }}" in store
                   for a, arg in [("install", "game.id"), ("update", "game.id"), ("verify", "game.id"), ("uninstall", "game.id"),
                                  ("pause", "id"), ("resume", "id"), ("cancel", "id")]),
    "sign-in": "if gogAccountAction(signIn: true, front) { return }" in store
               and "if gogAccountAction(signIn: false, front) { return }" in store,
    "refresh": "gogRefresh()" in store,
}
check(all(wired.values()), "BCDLiveStore calls every GOG hook (a merge must not drop one)",
      [k for k, v in wired.items() if not v])
steam_files = [APP / "SteamOwnedLibrary.swift", APP / "SteamDownloadBackground.swift", APP / "SteamGames.swift",
               APP / "SteamInstall.swift"] + sorted((APP / "SwiftSteam").rglob("*.swift"))
touched = [p.name for p in steam_files if re.search(r"BCDGOG|BCDContent", p.read_text())]
check(not touched, "Steam's files name nothing of GOG or the content engine", touched)

# ---- the Swift harness ----------------------------------------------------------------------
HARNESS = r'''
import Foundation

var failures = 0
func check(_ ok: Bool, _ what: String) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
    fflush(stdout)
}
func result(_ pairs: [(String, Any)]) { print("RESULT " + pairs.map { "\($0.0)=\($0.1)" }.joined(separator: " ")); fflush(stdout) }

struct Fixture: Decodable {
    var work: String
    var base: String
    var product: String
    var info: String
    var launcherInfo: String
}

var logs: [String] = []
let logLock = NSLock()

final class Clock: @unchecked Sendable {
    private let lock = NSLock()
    private var value = Date(timeIntervalSince1970: 1_800_000_000)
    var now: Date { lock.withLock { value } }
    func advance(_ seconds: TimeInterval) { lock.withLock { value = value.addingTimeInterval(seconds) } }
}

/// GOG's sign-in and profile hosts, answered here.
final class StubGOG: URLProtocol {
    static let lock = NSLock()
    nonisolated(unsafe) static var refreshes = 0
    nonisolated(unsafe) static var apiCalls: [String] = []
    override class func canInit(with request: URLRequest) -> Bool {
        ["auth.gog.com", "users.gog.com", "embed.gog.com"].contains(request.url?.host ?? "")
    }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let url = request.url!
        let items = Dictionary((URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []).map { ($0.name, $0.value ?? "") },
                               uniquingKeysWith: { a, _ in a })
        var status = 200
        var body: Any = [:]
        switch (url.host!, url.path) {
        case ("auth.gog.com", "/token"):
            guard items["client_id"] == "46899977096215655", items["client_secret"]?.count == 64 else { status = 401; break }
            if items["grant_type"] == "authorization_code" {
                if items["code"] == "CODE-SECRET-GOOD" && items["redirect_uri"] == "https://embed.gog.com/on_login_success?origin=client" {
                    body = ["access_token": "ACCESS-1-SECRET", "refresh_token": "REFRESH-1-SECRET", "expires_in": 3600,
                            "token_type": "bearer", "user_id": "48628349957132247", "session_id": "SESSION-SECRET"]
                } else { status = 400; body = ["error": "invalid_grant"] }
            } else if items["grant_type"] == "refresh_token" {
                StubGOG.lock.withLock { StubGOG.refreshes += 1 }
                Thread.sleep(forTimeInterval: 0.2)
                if items["refresh_token"] == "REFRESH-1-SECRET" {
                    body = ["access_token": "ACCESS-2-SECRET", "expires_in": "3600"]
                } else { status = 400; body = ["error": "invalid_grant", "error_description": "REFRESH-REVOKED-SECRET is revoked"] }
            }
        case ("users.gog.com", "/users/48628349957132247"):
            body = ["id": "48628349957132247", "username": "TestUserName"]
        case ("embed.gog.com", "/account/getFilteredProducts"):
            let auth = request.value(forHTTPHeaderField: "Authorization") ?? ""
            StubGOG.lock.withLock { StubGOG.apiCalls.append(auth) }
            if auth == "Bearer ACCESS-2-SECRET" { body = ["products": [], "totalPages": 1] } else { status = 401 }
        default:
            status = 404
        }
        let data = try! JSONSerialization.data(withJSONObject: body)
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: ["Content-Length": "\(data.count)"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

func json(_ text: String) -> [String: Any] { try! JSONSerialization.jsonObject(with: Data(text.utf8)) as! [String: Any] }

// MARK: sign-in

func authPhase(_ fx: Fixture) async {
    let login = URLComponents(url: BCDGOGAuth.loginURL, resolvingAgainstBaseURL: false)!
    let q = Dictionary(login.queryItems!.map { ($0.name, $0.value!) }, uniquingKeysWith: { a, _ in a })
    check(login.host == "auth.gog.com" && login.path == "/auth" && q["client_id"] == "46899977096215655"
          && q["redirect_uri"] == "https://embed.gog.com/on_login_success?origin=client" && q["response_type"] == "code" && q["layout"] == "client2"
          && login.percentEncodedQuery!.contains("redirect_uri=https%3A%2F%2Fembed.gog.com%2Fon_login_success%3Forigin%3Dclient"),
          "the login page: Galaxy's client, the redirect fully encoded, a code, the client2 layout")
    let good = "https://embed.gog.com/on_login_success?origin=client&code=AbC-123_xyz.~0123456789"
    check(BCDGOGAuth.code(from: URL(string: good)!) == "AbC-123_xyz.~0123456789", "the finishing address gives its code")
    check(BCDGOGAuth.code(from: URL(string: "HTTPS://EMBED.GOG.COM/on_login_success?code=abcdefghij")!) == "abcdefghij", "scheme and host in any case")
    for bad in ["http://embed.gog.com/on_login_success?code=abcdefghij",
                "https://embed.gog.com.evil.example/on_login_success?code=abcdefghij",
                "https://evil.example/on_login_success?code=abcdefghij&x=embed.gog.com",
                "https://embed.gog.com@evil.example/on_login_success?code=abcdefghij",
                "https://user:pw@embed.gog.com/on_login_success?code=abcdefghij",
                "https://embed.gog.com:8443/on_login_success?code=abcdefghij",
                "https://embed.gog.com/on_login_success/x?code=abcdefghij",
                "https://embed.gog.com/on_login_successX?code=abcdefghij",
                "https://embed.gog.com/on_login_success?origin=client",
                "https://embed.gog.com/on_login_success?code=abcdefghij&code=klmnopqrst",
                "https://embed.gog.com/on_login_success?code=abc%22%3Cscript",
                "https://embed.gog.com/on_login_success?code=short",
                "https://login.gog.com/auth?code=abcdefghij"] {
        check(BCDGOGAuth.code(from: URL(string: bad)!) == nil, "no code from \(bad.prefix(64))")
    }
    let now = Date(timeIntervalSince1970: 1_000)
    let parsed = try? BCDGOGAuth.tokens(from: Data(#"{"access_token":"A","refresh_token":"R","expires_in":3600,"user_id":48628349957132247}"#.utf8), now: now)
    check(parsed?.userID == "48628349957132247" && parsed?.expires == now.addingTimeInterval(3600), "the token reply: numbers or strings, the lapse time")
    check((try? BCDGOGAuth.tokens(from: Data(#"{"access_token":"A","expires_in":3600}"#.utf8), now: now)) == nil, "a reply without a refresh token or user is refused")
    let kept = try? BCDGOGAuth.tokens(from: Data(#"{"access_token":"B","expires_in":60000000}"#.utf8), now: now, previous: parsed)
    check(kept?.refreshToken == "R" && kept?.userID == "48628349957132247" && kept!.expires.timeIntervalSince(now) == 86400,
          "a refresh keeps the refresh token and user it did not send; lifetimes are bounded")
    check("\(parsed!)" == "BCDGOGTokens(<redacted>)" && String(reflecting: parsed!) == "BCDGOGTokens(<redacted>)",
          "printing the tokens shows nothing of them")
    check(parsed!.needsRefresh(now: now.addingTimeInterval(3301)) && !parsed!.needsRefresh(now: now.addingTimeInterval(3299)),
          "a token is refreshed five minutes before it lapses")

    let secrets = BCDMemorySecrets()
    let clock = Clock()
    let http = BCDHTTPStream(protocols: [StubGOG.self])
    let session = BCDGOGSession(secrets: secrets, http: http, now: { clock.now })
    do {
        await expectError("a wrong code is refused") { _ = try await session.signIn(code: "CODE-SECRET-BAD") }
        check(secrets.read(BCDGOGSession.itemName) == nil, "nothing is kept after a refused sign-in")
        let tokens = try await session.signIn(code: "CODE-SECRET-GOOD")
        check(tokens.userName == "TestUserName" && secrets.read(BCDGOGSession.itemName) != nil, "signed in: the tokens kept, the public name read")
        let first = try await session.accessToken()
        check(first == "ACCESS-1-SECRET" && StubGOG.refreshes == 0, "a fresh token is used as it is")
        clock.advance(3400)
        let tokensNow = try await withThrowingTaskGroup(of: String.self) { group -> [String] in
            for _ in 0..<5 { group.addTask { try await session.accessToken() } }
            var all: [String] = []
            for try await t in group { all.append(t) }
            return all
        }
        check(tokensNow.allSatisfy { $0 == "ACCESS-2-SECRET" } && StubGOG.refreshes == 1, "five callers near the lapse share one refresh (\(StubGOG.refreshes))")
        let reread = BCDGOGSession(secrets: secrets, http: http, now: { clock.now })
        let stored = await reread.current()
        check(stored?.accessToken == "ACCESS-2-SECRET" && stored?.refreshToken == "REFRESH-1-SECRET", "the refreshed tokens are what the item holds")
        // An API call refused with 401 is retried once after a refresh.
        clock.advance(-3400)
        var t = stored!
        t.accessToken = "ACCESS-STALE-SECRET"
        t.expires = clock.now.addingTimeInterval(3600)
        try secrets.write(BCDGOGSession.itemName, JSONEncoder().encode(t))
        let apiSession = BCDGOGSession(secrets: secrets, http: http, now: { clock.now })
        let api = BCDGOGAPI(session: apiSession, http: http)
        let page = try await api.json(BCDGOGCatalog.pageURL(1), signedIn: true)
        check(page["totalPages"] as? Int == 1 && StubGOG.apiCalls == ["Bearer ACCESS-STALE-SECRET", "Bearer ACCESS-2-SECRET"],
              "an API call refused with 401 is tried again once with a refreshed token")
        // A refused refresh signs out.
        t.refreshToken = "REFRESH-REVOKED-SECRET"
        t.expires = clock.now
        try secrets.write(BCDGOGSession.itemName, JSONEncoder().encode(t))
        let revoked = BCDGOGSession(secrets: secrets, http: http, now: { clock.now })
        await expectError("a refused refresh fails") { _ = try await revoked.accessToken() }
        let after = await revoked.current()
        check(after == nil && secrets.read(BCDGOGSession.itemName) == nil, "...and signs out: the item is gone")
        try secrets.write(BCDGOGSession.itemName, JSONEncoder().encode(stored!))
        let leaving = BCDGOGSession(secrets: secrets, http: http, now: { clock.now })
        await leaving.signOut()
        check(secrets.read(BCDGOGSession.itemName) == nil, "sign-out deletes the item")
    } catch { check(false, "session: \(error)") }
    let joined = logLock.withLock { logs.joined(separator: "\n") }
    check(!joined.isEmpty && !joined.contains("SECRET") && !joined.contains("TestUserName") && !joined.contains("9d85c43b")
          && !joined.contains("48628349957132247"), "the log has no token, code, secret, account name or user ID")
}

func expectError(_ what: String, _ body: () async throws -> Void) async {
    do { try await body(); check(false, what + " (no error)") } catch { check(true, what) }
}

// MARK: the library

func catalogPhase(_ fx: Fixture) async {
    let page = json(#"""
    {"page":1,"totalPages":3,"products":[
      {"id":1207658930,"title":"  The Witcher 2  ","image":"//images-1.gog-statics.com/abc123def","worksOn":{"Windows":true,"Mac":true}},
      {"id":1207658931,"title":"Mac Only","image":"//images-1.gog-statics.com/x","worksOn":{"Windows":false,"Mac":true}},
      {"id":"12a","title":"Bad ID","worksOn":{"Windows":true}},
      {"id":1207658932,"title":"","worksOn":{"Windows":true}},
      {"id":1207658933,"title":"Odd Picture","image":"//evil.example/abc","worksOn":{"Windows":true}},
      {"id":"1207658934","title":"No Picture","worksOn":{"Windows":true}}]}
    """#)
    do {
        let reply = try BCDGOGCatalog.parsePage(page)
        check(reply.products.map(\.id) == ["1207658930", "1207658933", "1207658934"] && reply.pages == 3,
              "the owned list keeps Windows games with an ID and a title")
        check(reply.products[0].title == "The Witcher 2" && reply.products[0].image == "//images-1.gog-statics.com/abc123def"
              && reply.products[1].image == nil, "titles trimmed; a picture only from GOG's image hosts")
        var product = reply.products[0]
        let art = BCDGOGCatalog.art(product)
        check(art.cover.map(\.absoluteString) == ["https://images-1.gog-statics.com/abc123def_800.jpg"]
              && art.header == art.cover && art.hero.map(\.absoluteString) == ["https://images-1.gog-statics.com/abc123def_bg_crop_1920x655.jpg"],
              "without gamesdb: the list's picture as tile, cover fallback and backdrop")
        let db = BCDGOGCatalog.parseGamesDB(json(#"""
        {"game":{"vertical_cover":{"url_format":"https://images.gog.com/37fa{formatter}.{ext}?namespace=gamesdb"},
                 "background":{"url_format":"https://images.gog.com/a75b{formatter}.{ext}?namespace=gamesdb"},
                 "logo":{"url_format":"http://images.gog.com/a3b9{formatter}.{ext}"}}}
        """#))
        check(db.cover == "https://images.gog.com/37fa.jpg?namespace=gamesdb" && db.background == "https://images.gog.com/a75b.jpg?namespace=gamesdb",
              "gamesdb: the tall cover and backdrop, original size")
        check(BCDGOGCatalog.parseGamesDB(json(#"{"game":{"vertical_cover":{"url_format":"https://evil.example/{formatter}.{ext}"}}}"#)).cover == nil,
              "gamesdb pictures only from GOG's hosts")
        product.cover = db.cover
        product.background = db.background
        let full = BCDGOGCatalog.art(product)
        check(full.cover.first?.absoluteString == db.cover && full.cover.count == 2 && full.hero.first?.absoluteString == db.background,
              "with gamesdb: the tall cover first, the tile after it")
        let key = BCDGOGCatalog.accountKey("48628349957132247")
        check(key.count == 64 && !key.contains("48628349957132247"), "the cached list names its account by a hash")
        check(BCDGOGCatalog.validID("1207658930") && !BCDGOGCatalog.validID("12a") && !BCDGOGCatalog.validID("") && !BCDGOGCatalog.validID("../1"),
              "product IDs are decimal")
    } catch { check(false, "catalog: \(error)") }
}

// MARK: content

func contentPhase(_ fx: Fixture) async {
    let builds = json(#"""
    {"items":[
      {"build_id":"1","generation":2,"branch":"beta","version_name":"beta","link":"https://gog-cdn-fastly.gog.com/content-system/v2/meta/aa/bb/aabb0000000000000000000000000000"},
      {"build_id":"56452082907692588","generation":2,"branch":null,"version_name":"3.5.0.26g","link":"https://gog-cdn-fastly.gog.com/content-system/v2/meta/e5/18/e518c17d90805e8e3998a35fac8b8505"},
      {"build_id":"3161","generation":1,"branch":null,"link":"https://gog-cdn-fastly.gog.com/content-system/v1/manifests/1/windows/2/repository.json"}]}
    """#)
    check((try? BCDGOGContent.newestBuild(builds))?.id == "56452082907692588", "the newest public generation-2 build (a branch skipped)")
    check((try? BCDGOGContent.newestBuild(json(#"{"items":[{"build_id":"3161","generation":1,"branch":null,"link":"https://cdn.gog.com/x"}]}"#))) == nil,
          "only older builds: refused")
    do { _ = try BCDGOGContent.newestBuild(json(#"{"items":[{"build_id":"3161","generation":1,"branch":null}]}"#)) }
    catch { check(error as? BCDGOGError == .olderBuildsOnly, "...with the older-builds reason") }
    do { _ = try BCDGOGContent.newestBuild(json(#"{"items":[]}"#)) } catch { check(error as? BCDGOGError == .noWindowsBuild, "no build: no Windows download") }
    check((try? BCDGOGContent.newestBuild(json(#"{"items":[{"build_id":"5","generation":2,"branch":null,"link":"https://evil.example/m"}]}"#))) == nil,
          "a build link off GOG's hosts is refused")
    // GOG's list names the CDN it picks for the player; in some regions that is gog-cdn.gcdn.co (2026-10-24: BioShock 2,
    // Cult of the Lamb, RollerCoaster Tycoon Deluxe). A manifest link of the exact content-addressed form moves to
    // cdn.gog.com with the same path; a gog.com link stays as it was; anything else is still refused.
    let gcdn = "https://gog-cdn.gcdn.co/content-system/v2/meta/f7/0d/f70d7d066327a1f183582c5535803eeb"
    check((try? BCDGOGContent.newestBuild(json(#"{"items":[{"build_id":"59645104523292869","generation":2,"branch":null,"link":"\#(gcdn)"}]}"#)))?.link.absoluteString
          == "https://cdn.gog.com/content-system/v2/meta/f7/0d/f70d7d066327a1f183582c5535803eeb",
          "a manifest link on another CDN (gog-cdn.gcdn.co) is read from cdn.gog.com, same path")
    check((try? BCDGOGContent.newestBuild(builds))?.link.absoluteString
          == "https://gog-cdn-fastly.gog.com/content-system/v2/meta/e5/18/e518c17d90805e8e3998a35fac8b8505",
          "a link on a gog.com host is used unchanged")
    for bad in ["http://gog-cdn.gcdn.co/content-system/v2/meta/f7/0d/f70d7d066327a1f183582c5535803eeb",
                "https://gog-cdn.gcdn.co/content-system/v2/meta/aa/0d/f70d7d066327a1f183582c5535803eeb",
                "https://gog-cdn.gcdn.co/content-system/v2/meta/f7/0d/F70D7D066327A1F183582C5535803EEB",
                "https://gog-cdn.gcdn.co/content-system/v2/meta/f7/0d/f70d7d066327a1f183582c5535803eeb?x=1",
                "https://gog-cdn.gcdn.co/x/content-system/v2/meta/f7/0d/f70d7d066327a1f183582c5535803eeb",
                "https://gog-cdn.gcdn.co/content-system/v1/manifests/1207658945/windows/48071975/repository.json",
                "https://u@gog-cdn.gcdn.co/content-system/v2/meta/f7/0d/f70d7d066327a1f183582c5535803eeb"] {
        check((try? BCDGOGContent.newestBuild(json(#"{"items":[{"build_id":"5","generation":2,"branch":null,"link":"\#(bad)"}]}"#))) == nil,
              "refused off GOG's hosts: \(bad)")
    }
    // The queue's "[content] failed ... reason=" used to print the error's type name for every GOG failure ("BCDGOGError",
    // 2026-10-20, two testers' installs); now the step: which reply GOG sent that BCD could not read.
    let reasonA = await BCDContentQueue.reason(BCDGOGError.badReply("builds"))
    let reasonB = await BCDContentQueue.reason(BCDGOGError.noWindowsBuild)
    let reasonC = await BCDContentQueue.reason(BCDContentError.http(403))
    check(reasonA == "reply-builds" && reasonB == "no-windows-build" && reasonC == "http-403",
          "a failed GOG job logs its step (reply-builds, ...), not the error's type name")
    check(BCDGOGAPI.shape(json(#"{"items":[{"build_id":5,"generation":2,"branch":null,"link":"x"}],"total_count":1}"#), lists: ["items"])
          == "top[items=a,total_count=n] items=1 first[branch=N,build_id=n,generation=n,link=s]",
          "a reply's shape for the log: keys and kinds, never a value")

    func L(_ offered: [String], _ preferred: [String]) -> String { BCDGOGContent.language(offered: offered, preferred: preferred) }
    let offered = ["de-DE", "en-US", "fr-FR", "zh-Hans", "pt-BR"]
    check(L(offered, ["tr-TR", "fr-CA"]) == "fr-FR", "language: the first preferred language GOG offers, by language")
    check(L(offered, ["zh-Hans-CN"]) == "zh-Hans", "language: by language and script")
    check(L(offered, ["pt-BR", "en-US"]) == "pt-BR" && L(offered, ["tr-TR"]) == "en-US", "language: exact first; else US English")
    check(L(["en", "de"], ["en-GB"]) == "en" && L(["pl-PL", "ru-RU"], []) == "pl-PL" && L(["*"], ["en-US"]) == "*",
          "language: region-less codes match; else the first offered; neutral-only builds")

    let manifest = try! BCDGOGContent.parseBuildManifest(json(#"""
    {"version":2,"baseProductId":"1234567890","buildId":"77","installDirectory":"Test Game","clientId":"47874882724506271","clientSecret":"s",
     "dependencies":["MSVC2017","DirectX"],
     "depots":[
      {"manifest":"00000000000000000000000000000001","productId":"1234567890","languages":["*"],"size":1},
      {"manifest":"00000000000000000000000000000002","productId":"1234567890","languages":["en-US","de-DE"],"size":1},
      {"manifest":"00000000000000000000000000000003","productId":"1234567890","languages":["de-DE"],"size":1},
      {"manifest":"00000000000000000000000000000004","productId":"1234567890","languages":["en-US"],"isGogDepot":true,"size":1},
      {"manifest":"00000000000000000000000000000004","productId":"1234567890","languages":["zh-Hans"],"isGogDepot":true,"size":1},
      {"manifest":"00000000000000000000000000000005","productId":"1234567890","languages":["*"],"osBitness":["32"],"size":1},
      {"manifest":"00000000000000000000000000000006","productId":"1234567890","languages":["*"],"osBitness":["64"],"size":1},
      {"manifest":"00000000000000000000000000000007","productId":"1999999999","languages":["*"],"size":1}]}
    """#))
    let chosen = BCDGOGContent.depots(manifest, products: ["1234567890"], language: "en-US").map { $0.manifest.suffix(1) }
    check(chosen == ["1", "2", "4", "6"], "depots: neutral and the language's, 64-bit over 32-bit, GOG's own, each once, no DLC (\(chosen))")
    check(manifest.dependencies == ["MSVC2017", "DirectX"] && manifest.clientID == "47874882724506271" && manifest.installDirectory == "Test Game",
          "the build manifest's folder, redistributables and the game's client")
    check((try? BCDGOGContent.parseBuildManifest(json(#"{"version":2,"baseProductId":"1","depots":[{"manifest":"../x","productId":"1"}]}"#))) == nil,
          "a depot manifest name that isn't a hash is refused")

    // Depot manifests into a plan.
    func chunk(_ n: Int, size: Int64 = 100) -> [String: Any] {
        let hex = String(format: "%032x", n), packed = String(format: "%032x", n + 1000)
        return ["md5": hex, "size": size, "compressedMd5": packed, "compressedSize": size / 2]
    }
    let depot: [String: Any] = ["depot": [
        "items": [
            ["type": "DepotDirectory", "path": "saves"],
            ["type": "DepotFile", "path": "Bin\\Game.exe", "chunks": [chunk(1), chunk(2, size: 50)], "flags": ["executable"]],
            ["type": "DepotFile", "path": "setup_helper.exe", "chunks": [chunk(3)], "flags": ["support", "executable"]],
            ["type": "DepotLink", "path": "link", "target": "x"],
            ["type": "DepotFile", "path": "Data\\a.txt", "chunks": [chunk(4, size: 30)], "sfcRef": ["offset": 0, "size": 30]],
            ["type": "DepotFile", "path": "data\\b.txt", "chunks": [chunk(5, size: 40)], "sfcRef": ["offset": 30, "size": 40], "md5": String(format: "%032x", 77)],
            ["type": "DepotFile", "path": "c.txt", "chunks": [], "sfcRef": ["offset": 50, "size": 20]],
            ["type": "DepotFile", "path": "empty.txt", "chunks": []],
        ],
        "smallFilesContainer": ["chunks": [chunk(10, size: 60), chunk(11, size: 30)]],
    ] as [String: Any], "version": 2]
    for containers in [false, true] {
        do {
            var builder = try BCDContentPlanBuilder(key: .init(store: "gog", id: "1234567890"), title: "T", version: "77", folder: "Test Game")
            let added = try BCDGOGContent.add(depot: depot, product: "1234567890", to: &builder, containers: containers)
            let plan = try builder.build()
            let paths = plan.files.map(\.path)
            check(paths == ["Bin/Game.exe", "Data/a.txt", "Data/b.txt", "c.txt", "empty.txt"] && added.support == 1 && added.links == 1
                  && plan.directories.contains("saves"), "containers=\(containers): files planned, support files and links left out, folders folded")
            let c = plan.files.firstIndex { $0.path == "c.txt" }!
            let cParts = plan.parts.filter { $0.file == c }
            check(cParts.map { plan.chunks[$0.chunk].locator } == [String(format: "%032x", 1010), String(format: "%032x", 1011)]
                  && cParts.map(\.chunkOffset) == [50, 0] && cParts.map(\.length) == [10, 10] && cParts.map(\.fileOffset) == [0, 10],
                  "containers=\(containers): a file with no chunks of its own is cut from the container across its chunks")
            let a = plan.files.firstIndex { $0.path == "Data/a.txt" }!
            let fromContainer = plan.parts.contains { $0.file == a && plan.chunks[$0.chunk].locator == String(format: "%032x", 1010) }
            check(fromContainer == containers && added.containerFiles == (containers ? 3 : 1),
                  "containers=\(containers): small files with chunks of their own use the container only when it is on")
            check(plan.files.first { $0.path == "Data/b.txt" }?.digest?.hex == String(format: "%032x", 77)
                  && plan.chunks.allSatisfy { $0.codec == .zlib && $0.group == "1234567890" && $0.fetchDigest?.hex == $0.locator },
                  "chunks: zlib, served by the product, checked by MD5 on the wire and unpacked")
        } catch { check(false, "depot containers=\(containers): \(error)") }
    }
    for (what, item) in [("an unknown item type", ["type": "DepotSymlinkX", "path": "x"] as [String: Any]),
                         ("a file whose bytes come from elsewhere", ["type": "DepotFile", "path": "x", "chunks": [], "newSource": ["a": 1]]),
                         ("a container-only file without a usable container", ["type": "DepotFile", "path": "x", "chunks": [], "sfcRef": ["offset": 0, "size": 5]])] {
        var builder = try! BCDContentPlanBuilder(key: .init(store: "gog", id: "1"), title: "T", version: "1", folder: "G")
        do { _ = try BCDGOGContent.add(depot: ["depot": ["items": [item]]], product: "1", to: &builder, containers: false); check(false, what + " is refused") }
        catch { check(error as? BCDGOGError == .unknownFileSource, what + " is refused") }
    }
    var hostile = try! BCDContentPlanBuilder(key: .init(store: "gog", id: "1"), title: "T", version: "1", folder: "G")
    check((try? BCDGOGContent.add(depot: ["depot": ["items": [["type": "DepotFile", "path": "..\\..\\evil.dll", "chunks": [chunk(1)]]]]],
                                  product: "1", to: &hostile, containers: false)) == nil, "a path out of the game's folder is refused")

    // Secure links.
    let now = Date(timeIntervalSince1970: 2_000_000_000)
    let link = json(#"""
    {"urls":[
      {"url_format":"https://fallback.example/{path}?t={token}","parameters":{"path":"/store/1","token":"T3","expires_at":2000003600},"priority":9,"fallback_only":true},
      {"url_format":"https://low.example{path}","parameters":{"path":"/store/1","expires_at":2000001000},"priority":3,"fallback_only":false},
      {"url_format":"https://high.example{path}?token={token}","parameters":{"path":"/content/1","token":"T1"},"priority":5,"fallback_only":false},
      {"url_format":"https://old.example{path}","parameters":{"path":"/x"},"priority":10,"supports_generation":[1]}]}
    """#)
    do {
        let parsed = try BCDGOGContent.parseSecureLink(link, now: now)
        check(parsed.endpoints.map(\.priority) == [5, 3, 9] && parsed.endpoints.map(\.fallbackOnly) == [false, false, true],
              "secure link: higher priority first, fallback-only last, generation-1-only endpoints dropped")
        check(parsed.endpoints.first?.priority == 5 && parsed.endpoints.last?.fallbackOnly == true && parsed.expires == Date(timeIntervalSince1970: 2_000_001_000),
              "...and the link lapses at its earliest expiry")
        let loc = "abcdef0123456789abcdef0123456789"
        check(BCDGOGContent.chunkURL(parsed.endpoints[0], locator: loc)?.absoluteString == "https://high.example/content/1/ab/cd/\(loc)?token=T1",
              "a chunk's address: the path gains /ab/cd/<compressed MD5>, the parameters are filled")
        check(BCDGOGContent.chunkURL(.init(format: "https://x.example{path}?{missing}", parameters: ["path": "/a"], priority: 0, fallbackOnly: false), locator: loc) == nil
              && BCDGOGContent.chunkURL(.init(format: "http://x.example{path}", parameters: ["path": "/a"], priority: 0, fallbackOnly: false), locator: loc) == nil
              && BCDGOGContent.chunkURL(parsed.endpoints[0], locator: "../x") == nil,
              "no address with an unfilled parameter, without https, or for a bad locator")
    } catch { check(false, "secure link: \(error)") }
    let clock = Clock()
    final class Count: @unchecked Sendable { var n = 0; let lock = NSLock() }
    let fetches = Count()
    let source = BCDGOGChunkSource(fetch: { product in
        fetches.lock.withLock { fetches.n += 1 }
        return ["urls": [["url_format": "https://cdn.example{path}", "parameters": ["path": "/\(product)", "expires_at": clock.now.timeIntervalSince1970 + 600],
                          "priority": 1]]]
    }, now: { clock.now })
    let chunkA = BCDContentChunk(locator: "abcdef0123456789abcdef0123456789", group: "1234567890", fetchSize: 1, size: 1, fetchDigest: nil, digest: nil, codec: .zlib)
    do {
        let first = try await source.urls(for: chunkA)
        _ = try await source.urls(for: chunkA)
        check(first.map(\.absoluteString) == ["https://cdn.example/1234567890/ab/cd/abcdef0123456789abcdef0123456789"] && fetches.n == 1,
              "one secure link per product, kept while it is valid")
        clock.advance(500)
        _ = try await source.urls(for: chunkA)
        check(fetches.n == 2, "renewed when under two minutes are left")
        try await source.refresh(group: "1234567890")
        check(fetches.n == 3, "renewed when a host refuses it")
    } catch { check(false, "chunk source: \(error)") }
}

// MARK: play tasks

func playPhase(_ fx: Fixture) async {
    func task(_ text: String) -> BCDGOGPlayTask? { try? BCDGOGInstall.playTask(fromInfo: Data(text.utf8)) }
    let primary = task(#"{"playTasks":[{"type":"URLTask","isPrimary":true,"link":"https://x"},{"type":"FileTask","category":"tool","path":"tool.exe"},{"type":"FileTask","isPrimary":true,"category":"game","path":"bin\\game.exe","arguments":"-skipintro \"a b\"","workingDir":"bin"}]}"#)
    check(primary == BCDGOGPlayTask(path: "bin/game.exe", arguments: "-skipintro \"a b\"", workingDirectory: "bin", category: "game"),
          "the primary file task: program, arguments verbatim, working folder (a URL task ignored)")
    check(task(#"{"playTasks":[{"type":"FileTask","category":"tool","path":"tool.exe"},{"type":"FileTask","category":"game","path":".\\Game.exe"}]}"#)?.path == "Game.exe",
          "no primary: the first game task; ./ dropped")
    check(task(#"{"playTasks":[{"type":"FileTask","isPrimary":true,"category":"launcher","path":"Launcher.exe","workingDir":""}]}"#)?.category == "launcher",
          "a launcher task is recognised")
    for bad in [#"{"playTasks":[{"type":"FileTask","isPrimary":true,"path":"..\\..\\evil.exe"}]}"#, #"{"playTasks":[{"type":"FileTask","isPrimary":true,"path":"C:\\evil.exe"}]}"#,
                #"{"playTasks":[{"type":"URLTask","isPrimary":true,"link":"https://x"}]}"#, #"{"playTasks":[]}"#, "not json"] {
        check(task(bad) == nil, "no play task from \(bad.prefix(48))")
    }
    let root = BCDInstallRoot(drive: "C", folder: "GOG Games")
    let record = BCDGOGInstallRecord(productID: "1", title: "T", buildID: "1", versionName: "", language: "en-US", root: root, folder: "Test Game",
                                     bytes: 1, depots: [], dependencies: [], supportFiles: 0, play: nil, entryID: nil, installed: Date())
    let launch = BCDGOGInstall.launch(record, play: primary!, program: "Bin/Game.exe", working: "Bin")
    check(launch.relativePath == "GOG Games/Test Game/Bin/Game.exe" && launch.workingDirectory == "C:\\GOG Games\\Test Game\\Bin"
          && launch.arguments == "-skipintro \"a b\"" && launch.launchMode == nil, "the library entry: program under drive_c, working folder as a C:\\ path, a direct start")
    var launcher = primary!
    launcher.category = "launcher"
    let viaDesktop = BCDGOGInstall.launch(record, play: launcher, program: "Launcher.exe", working: "")
    check(viaDesktop.workingDirectory == "C:\\GOG Games\\Test Game" && viaDesktop.launchMode == "desktop",
          "a launcher starts in the Wine desktop; no working folder means the game's folder")
}

// MARK: install, end to end

final class LocalSource: BCDChunkSource, @unchecked Sendable {
    let inner: BCDGOGChunkSource
    let base: String
    init(_ inner: BCDGOGChunkSource, base: String) { self.inner = inner; self.base = base }
    func urls(for chunk: BCDContentChunk) async throws -> [URL] {
        // GOG's chunk address, pointed at the local server (same path).
        try await inner.urls(for: chunk).map { URL(string: base + $0.path)! }
    }
    func refresh(group: String) async throws { try await inner.refresh(group: group) }
}

func installPhase(_ fx: Fixture, containers: Bool, name: String) async {
    let work = URL(fileURLWithPath: fx.work)
    let drive = work.appendingPathComponent("drive_c-" + name, isDirectory: true)
    BCDInstallRoot.hostDrive = { $0 == "C" ? drive : nil }
    let root = BCDInstallRoot(drive: "C", folder: "GOG Games")
    // A folder of the game's name that BCD did not make.
    try? FileManager.default.createDirectory(at: drive.appendingPathComponent("GOG Games/Test Game"), withIntermediateDirectories: true)
    let fetch: BCDGOGContent.Fetch = { url, signedIn in
        let file: URL
        if url.host == "content-system.gog.com" && url.path.hasSuffix("/builds") {
            file = work.appendingPathComponent("builds.json")
        } else if url.host == "gog-cdn-fastly.gog.com" && url.path.hasPrefix("/content-system/v2/meta/") {
            file = work.appendingPathComponent("meta/" + url.lastPathComponent)
        } else {
            throw BCDGOGError.badReply("unexpected address")
        }
        let data = try BCDZlib.jsonData(Data(contentsOf: file), limit: 64 << 20)
        return try JSONSerialization.jsonObject(with: data) as! [String: Any]
    }
    do {
        let key = BCDContentKey(store: "gog", id: fx.product)
        let build = try await BCDGOGContent.newest(fx.product, fetch: fetch)
        let made = try await BCDGOGContent.plan(key, title: "Test Game", build: build, preferred: ["de-AT", "en-GB"], containers: containers, fetch: fetch)
        check(made.info.language == "de-DE" && made.info.dependencies == ["MSVC2017"] && made.info.supportFiles == 1 && made.info.containers == containers,
              "\(name): planned in the device's language, redistributables noted, the support file left out")
        let inner = BCDGOGChunkSource(fetch: { product in
            ["urls": [["url_format": "https://gog-cdn.example{path}", "parameters": ["path": "/content/" + product], "priority": 1]]]
        })
        var options = BCDContentOptions()
        options.backoff = 0
        let stage = root.stagingURL!.appendingPathComponent(key.folderName)
        try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: work.appendingPathComponent("job-" + name), source: LocalSource(inner, base: fx.base),
                                            options: options) { _ in }
        let record = try BCDGOGInstall.finish(made.plan, info: made.info, stage: stage, root: root)
        check(record.folder == "Test Game (GOG)" && record.windowsFolder == "C:\\GOG Games\\Test Game (GOG)",
              "\(name): a folder of the same name already there: the game goes to \"Test Game (GOG)\"")
        check(record.play == BCDGOGPlayTask(path: "Bin/Game.exe", arguments: "-windowed -lang=de", workingDirectory: "Bin", category: "game"),
              "\(name): the play task, matched to the files' letter case (\(String(describing: record.play)))")
        let base = work.appendingPathComponent("records-" + name)
        try BCDGOGInstall.save(record, base: base)
        var broken = record
        broken.productID = "1"
        broken.folder = "../x"
        try JSONEncoder().encode(broken).write(to: BCDGOGInstall.recordsFolder(base).appendingPathComponent("1.json"))
        let loaded = BCDGOGInstall.loadRecords(base)
        check(loaded == [record.productID: record], "\(name): the record reads back; one with an unsafe folder is ignored")
        result([("folder", record.folder)])
        // Letter case and the clash rule.
        check(BCDGOGInstall.onDisk("bin/GAME.EXE", in: drive.appendingPathComponent("GOG Games/Test Game (GOG)"), directory: false) == "Bin/Game.exe"
              && BCDGOGInstall.onDisk("bin", in: drive.appendingPathComponent("GOG Games/Test Game (GOG)"), directory: false) == nil
              && BCDGOGInstall.onDisk("missing.exe", in: drive.appendingPathComponent("GOG Games/Test Game (GOG)"), directory: false) == nil,
              "\(name): paths are matched without case, files apart from folders")
        check(BCDGOGInstall.folderName(preferred: "Test Game", in: drive.appendingPathComponent("GOG Games")) == "Test Game (GOG 2)",
              "\(name): a second clash numbers the folder")
    } catch { check(false, "\(name): \(error)") }
}

@main struct Harness {
    @MainActor static func main() async {
        let arguments = CommandLine.arguments
        let fx = try! JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: arguments[2])))
        BCDContentLog.sink = { line in logLock.withLock { logs.append(line) }; print("LOG " + line); fflush(stdout) }
        switch arguments[1] {
        case "auth": await authPhase(fx)
        case "catalog": await catalogPhase(fx)
        case "content": await contentPhase(fx)
        case "play": await playPhase(fx)
        case "install": await installPhase(fx, containers: false, name: "own-chunks")
        case "install-containers": await installPhase(fx, containers: true, name: "containers")
        default: check(false, "unknown phase")
        }
        exit(failures == 0 ? 0 : 1)
    }
}
'''

SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not SWIFTC or sys.platform != "darwin":
    if args.require_swift:
        check(False, "swiftc on macOS is required (--require-swift)")
    else:
        print("skip: the behaviour checks need swiftc on macOS")
    print(f"check-gog: {failures} FAILED" if failures else "check-gog: all passed")
    sys.exit(1 if failures else 0)


# ---- a synthetic GOG product ----------------------------------------------------------------
rng = random.Random(1018)
PRODUCT = "1234567890"


def blob(n):
    words = [bytes(rng.choice(b"klmnopqrst") for _ in range(rng.randint(2, 8))) for _ in range(50)]
    out = bytearray()
    while len(out) < n:
        out += rng.choice(words) + b"\n"
    return bytes(out[:n])


class Chunks:
    def __init__(self):
        self.wire = {}

    def make(self, raw):
        wire = zlib.compress(raw, 6)
        packed = hashlib.md5(wire).hexdigest()
        self.wire[packed] = wire
        return dict(md5=hashlib.md5(raw).hexdigest(), size=len(raw), compressedMd5=packed, compressedSize=len(wire))


def build_product(work):
    chunks = Chunks()
    expect = {}
    meta = work / "meta"
    meta.mkdir()

    def write_meta(obj):
        data = zlib.compress(json.dumps(obj).encode(), 6)
        name = hashlib.md5(data).hexdigest()
        (meta / name).write_bytes(data)
        return name

    def file_item(path, raw, final, pieces=1, **extra):
        size = len(raw)
        cuts = [size * i // pieces for i in range(pieces + 1)]
        expect[final] = raw
        return dict(type="DepotFile", path=path, chunks=[chunks.make(raw[a:b]) for a, b in zip(cuts, cuts[1:])], **extra)

    game = blob(700000)
    small = [blob(3000), blob(5000), blob(1200)]
    container = b"".join(small)
    main_items = [
        dict(type="DepotDirectory", path="saves"),
        file_item("Bin\\Game.exe", game, "Bin/Game.exe", pieces=3, flags=["executable"]),
        file_item("bin\\data.pak", blob(250000), "Bin/data.pak", pieces=2),
        file_item("setup_test_game.exe", blob(4000), None, flags=["support", "executable"]),
    ]
    expect.pop(None)
    at = 0
    for i, raw in enumerate(small):
        main_items.append(file_item(f"Data\\small{i}.txt", raw, f"Data/small{i}.txt", sfcRef=dict(offset=at, size=len(raw))))
        at += len(raw)
    main_depot = dict(depot=dict(items=main_items, smallFilesContainer=dict(chunks=[chunks.make(container[:6000]), chunks.make(container[6000:])])), version=2)
    lang = dict(depot=dict(items=[file_item("Bin\\German.txt", blob(2000), "Bin/German.txt")]), version=2)
    english = dict(depot=dict(items=[file_item("Bin\\English.txt", blob(2000), "Bin/English.txt")]), version=2)
    info = dict(gameId=PRODUCT, rootGameId=PRODUCT, buildId="77", name="Test Game", playTasks=[
        dict(type="FileTask", category="tool", path="bin\\tool.exe"),
        dict(type="FileTask", isPrimary=True, category="game", path="bin\\GAME.exe", arguments="-windowed -lang=de", workingDir="BIN"),
    ])
    info_raw = json.dumps(info).encode()
    gog_depot = dict(depot=dict(items=[file_item(f"goggame-{PRODUCT}.info", info_raw, f"goggame-{PRODUCT}.info")]), version=2)
    depots = [
        dict(manifest=write_meta(main_depot), productId=PRODUCT, languages=["*"], size=1, compressedSize=1),
        dict(manifest=write_meta(lang), productId=PRODUCT, languages=["de-DE"], size=1, compressedSize=1),
        dict(manifest=write_meta(english), productId=PRODUCT, languages=["en-US"], size=1, compressedSize=1),
        dict(manifest=write_meta(gog_depot), productId=PRODUCT, languages=["de-DE"], isGogDepot=True, size=1, compressedSize=1),
    ]
    english_path = "Bin/English.txt"
    expect.pop(english_path)
    build = dict(version=2, baseProductId=PRODUCT, buildId="77", installDirectory="Test Game", clientId="47874882724506271",
                 clientSecret="0" * 64, dependencies=["MSVC2017"], depots=depots, platform="windows", products=[dict(productId=PRODUCT, name="Test Game")])
    link_name = write_meta(build)
    (work / "builds.json").write_text(json.dumps(dict(items=[dict(
        build_id="77", generation=2, branch=None, version_name="1.0",
        link=f"https://gog-cdn-fastly.gog.com/content-system/v2/meta/{link_name[:2]}/{link_name[2:4]}/{link_name}")])))
    return chunks, expect, small


class State:
    lock = threading.Lock()
    wire = {}
    log = []


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        m = re.fullmatch(r"/content/(\d+)/([0-9a-f]{2})/([0-9a-f]{2})/([0-9a-f]{32})", self.path)
        body = State.wire.get(m.group(4)) if m and m.group(4)[:2] == m.group(2) and m.group(4)[2:4] == m.group(3) else None
        with State.lock:
            State.log.append(m.group(4) if m else self.path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


work = Path(tempfile.mkdtemp(prefix="bcd-gog-"))
server = None
try:
    harness = work / "main.swift"
    harness.write_text(HARNESS)
    exe = work / "harness"
    build = subprocess.run([SWIFTC, "-parse-as-library", "-swift-version", "5", "-Onone", "-o", str(exe), str(harness)]
                           + [str(CONTENT / f) for f in CONTENT_HOST_FILES] + [str(GOG / f) for f in HOST_FILES],
                           capture_output=True, text=True)
    check(build.returncode == 0, "the production GOG and engine files compile on the host", build.stderr[-4000:])
    if build.returncode:
        print(f"check-gog: {failures} FAILED")
        sys.exit(1)
    data = work / "data"
    data.mkdir()
    chunks, expect, small = build_product(data)
    State.wire = chunks.wire
    server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fixture = dict(work=str(data), base=f"http://127.0.0.1:{server.server_address[1]}", product=PRODUCT, info="", launcherInfo="")
    fixture_path = work / "fixture.json"
    fixture_path.write_text(json.dumps(fixture))
    env = dict(os.environ, SWIFT_BACKTRACE="enable=no")

    def run(phase):
        r = subprocess.run([str(exe), phase, str(fixture_path)], capture_output=True, text=True, timeout=300, env=env)
        lines = [l for l in r.stdout.splitlines() if not l.startswith("LOG ")]
        sys.stdout.write("".join("  " + l + "\n" for l in lines if l.startswith(("PASS", "FAIL"))))
        check(r.returncode == 0, f'Swift phase "{phase}" passed', ((r.stderr or "")[-2000:] + "\n".join(lines[-8:])).strip())
        return r.stdout

    outputs = [run(p) for p in ("auth", "catalog", "content", "play")]
    for name, phase in (("own-chunks", "install"), ("containers", "install-containers")):
        with State.lock:
            State.log.clear()
        outputs.append(run(phase))
        folder = data / f"drive_c-{name}" / "GOG Games" / "Test Game (GOG)"
        got = {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()} if folder.exists() else {}
        check(got == expect, f"{name}: every installed file is what GOG's manifests describe (German, not English; no support file)",
              f"missing={sorted(set(expect) - set(got))} extra={sorted(set(got) - set(expect))}")
        with State.lock:
            requested = list(State.log)
        small_own = {hashlib.md5(zlib.compress(raw, 6)).hexdigest() for raw in small}
        if name == "containers":
            check(not (small_own & set(requested)) and len(requested) == len(set(requested)),
                  "with the container: the small files' own chunks are not fetched, each chunk once")
        else:
            check(small_own <= set(requested) and len(requested) == len(set(requested)),
                  "without it: each small file's own chunk, each chunk once")
    logged = "\n".join(l for o in outputs for l in o.splitlines() if l.startswith("LOG "))
    check("[gog-content] plan product=1234567890" in logged and "[gog-install] installed product=1234567890" in logged
          and "http" not in logged and str(work) not in logged and "Test Game" not in logged,
          "GOG's log lines: product IDs and counts, no address, path or title")
finally:
    if server:
        server.shutdown()
    shutil.rmtree(work, ignore_errors=True)

print(f"check-gog: {failures} FAILED" if failures else "check-gog: all passed")
sys.exit(1 if failures else 0)
