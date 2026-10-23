#!/usr/bin/env python3
"""Battle.net account and native download tests. Synthetic data only; never contacts Blizzard."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app/Madeira"
parser = argparse.ArgumentParser()
parser.add_argument("--require-swift", action="store_true")
parser.add_argument("--live-metadata", action="store_true", help="also validate public Classic Era bootstrap metadata; no game payloads")
args = parser.parse_args()

# Build membership and preview isolation are checked even without Apple's toolchain.
pbx = (ROOT / "app/Madeira.xcodeproj/project.pbxproj").read_text()
for file in sorted((APP / "BCDBattleNet").glob("*.swift")):
    assert f'path = "BCDBattleNet/{file.name}"' in pbx, f"not in Xcode project: {file}"
    assert f"{file.name} in Sources" in pbx, f"not built: {file}"
assert "case steam, epic, gog, battlenet, local" in (APP / "BCDStore.swift").read_text()
print("PASS: Battle.net files belong to the app target and the service is registered")

swift = shutil.which("swiftc")
if not swift:
    if args.require_swift:
        raise SystemExit("FAIL: Swift is required for behavioural checks")
    print("SKIP: Swift is unavailable; behavioural checks have NOT run")
    raise SystemExit(0)

HARNESS = r'''
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif


func require(_ value: @autoclosure () throws -> Bool, _ label: String) rethrows {
    guard try value() else { fatalError("FAIL: " + label) }
    print("PASS: " + label)
}
func refuses(_ label: String, _ body: () throws -> Void) {
    do { try body() } catch { print("PASS: " + label); return }
    fatalError("FAIL: " + label)
}
@main struct Checks {
    static func main() async throws {
        let origin = URL(string: "https://account.blizzard.com")!
        let now = Date(timeIntervalSince1970: 1000)
        for address in ["http://account.blizzard.com/games", "https://account.blizzard.com.evil.test/games",
                        "https://evil.test/account.blizzard.com", "https://user@account.blizzard.com/games",
                        "https://account.blizzard.com:8443/games", "https://evil.account.blizzard.com/games"] {
            require(!BCDBattleNetAccount.validOrigin(URL(string: address)!), "refuse hostile origin " + address)
        }
        require(BCDBattleNetAccount.validOrigin(URL(string: "https://eu.account.blizzard.com/games")!), "regional account origin")
        require(!BCDBattleNetAccount.applies("evilblizzard.com", to: "account.blizzard.com"), "domain boundary")
        func cookie(domain: String = ".blizzard.com", secure: Bool = true, expiry: Date? = nil) -> HTTPCookie {
            var properties: [HTTPCookiePropertyKey: Any] = [.name: "session", .value: "SECRET", .domain: domain, .path: "/"]
            if secure { properties[.secure] = "TRUE" }
            if let expiry { properties[.expires] = expiry }
            return HTTPCookie(properties: properties)!
        }
        require(BCDBattleNetCredential.Cookie(cookie(secure: false), origin: origin, now: now) == nil, "reject insecure cookie")
        require(BCDBattleNetCredential.Cookie(cookie(domain: "evil.test"), origin: origin, now: now) == nil, "reject unrelated cookie")
        require(BCDBattleNetCredential.Cookie(cookie(expiry: now), origin: origin, now: now) == nil, "reject expired cookie")
        let held = BCDBattleNetCredential.Cookie(cookie(expiry: now.addingTimeInterval(300)), origin: origin, now: now)!
        let credential = BCDBattleNetCredential(origin: origin, cookies: [held])
        let request = try credential.request("/api/games-and-subs", now: now)
        require(request.url?.host == "account.blizzard.com", "credential pinned to account host")
        require(request.value(forHTTPHeaderField: "Cookie") == "session=SECRET", "cookie applied to account API")
        let stream = BCDHTTPStream(redirectAllowed: { _ in false })
        let session = URLSession(configuration: .ephemeral)
        let task = session.dataTask(with: request)
        let redirect = HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: nil, headerFields: nil)!
        var redirected: URLRequest? = request
        stream.urlSession(session, task: task, willPerformHTTPRedirection: redirect,
            newRequest: URLRequest(url: URL(string: "https://evil.test")!)) { redirected = $0 }
        require(redirected == nil, "redirect cannot forward account cookies")
        task.cancel(); session.invalidateAndCancel(); stream.close()
        require(!String(describing: credential).contains("SECRET") && !String(reflecting: credential).contains("SECRET"), "redacted credential")
        refuses("no arbitrary API paths") { _ = try credential.request("/payments", now: now) }
        refuses("no credential after expiry") { _ = try credential.request("/api/games-and-subs", now: now.addingTimeInterval(301)) }
        let decoded = try JSONDecoder().decode(BCDBattleNetCredential.self, from: JSONEncoder().encode(credential))
        try require(try decoded.request("/api/details", now: now).value(forHTTPHeaderField: "Cookie") == "session=SECRET", "Keychain round trip")
        require(decoded.cachedProducts == nil, "credentials saved before library caching remain readable")
        var cached = credential
        cached.cachedName = "FixtureAccount"
        cached.cachedProducts = [.init(id: "wow", title: "World of Warcraft")]
        cached.updated = now
        let restored = try JSONDecoder().decode(BCDBattleNetCredential.self, from: JSONEncoder().encode(cached))
        require(restored.cachedName == "FixtureAccount" && restored.cachedProducts?.first?.id == "wow" && restored.updated == now,
                "account library snapshot survives a Keychain round trip")
        for json in ["{}", "[]", "{\"gameAccounts\":null}", "not JSON"] {
            refuses("malformed reply refused") { _ = try BCDBattleNetAccount.products(Data(json.utf8)) }
        }
        try require(try BCDBattleNetAccount.products(Data(#"{"gameAccounts":[]}"#.utf8)).isEmpty, "empty account is not WoW ownership")
        try require(try BCDBattleNetAccount.products(Data(#"{"gameAccounts":[{"titleId":17459}]}"#.utf8)).isEmpty, "other games do not grant WoW")
        let products = try BCDBattleNetAccount.products(Data(#"{"gameAccounts":[{"titleId":5730135},{"titleId":"5730135"}]}"#.utf8))
        require(products.map(\.id) == ["wow", "wow_classic", "wow_classic_era"], "duplicate subscriptions yield three client choices")
        require(BCDBattleNetAccount.installedProduct(#"Games\WoW\_retail_\WoW.exe"#) == "wow", "Retail import")
        require(BCDBattleNetAccount.installedProduct("WoW/_classic_/WowClassic.exe") == "wow_classic", "Classic import")
        require(BCDBattleNetAccount.installedProduct("WoW/_classic_era_/WowClassic.exe") == "wow_classic_era", "Classic Era import")
        require(BCDBattleNetAccount.installedProduct("Games/Wow.exe") == nil, "unrelated executable not classified")
        try await runDownloadChecks()
        if CommandLine.arguments.contains("--live-metadata") { try await runLiveMetadataChecks() }
    }
}
'''
with tempfile.TemporaryDirectory(prefix="bcd-battlenet-") as directory:
    directory = Path(directory)
    harness = directory / "checks.swift"
    harness.write_text(HARNESS)
    executable = directory / "checks"
    subprocess.run([swift, "-swift-version", "5", "-parse-as-library", str(APP / "BCDBattleNet/BCDBattleNetAccount.swift"),
                    *[str(APP / "BCDBattleNet" / file) for file in ["BCDBattleNetNGDP.swift", "BCDBattleNetBLTE.swift", "BCDBattleNetCASC.swift", "BCDBattleNetDownload.swift"]],
                    *[str(APP / "BCDContent" / ("BCDContent" + file + ".swift")) for file in ["Plan", "Codec", "Journal", "Fetch", "Engine"]],
                    str(ROOT / "tests/host/BCDBattleNetDownloadTests.swift"),
                    str(harness), "-o", str(executable)], check=True)
    subprocess.run([str(executable)] + (["--live-metadata"] if args.live_metadata else []), check=True)
