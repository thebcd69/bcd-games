// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Host test for Epic's cloud saves in BCD (tests/host/check-epic-cloud.py): the save service over BCD's session, the save rule from the
// catalog, what is remembered, what a page shows, and a whole sync against an in-memory service. Synthetic data only; Epic is never contacted.
import CryptoKit
import Foundation

var failures = 0
func check(_ ok: Bool, _ label: String) { print((ok ? "PASS: " : "FAIL: ") + label); if !ok { failures += 1 } }

let account = "0123456789abcdef0123456789abcdef"

// ---- the service's address ------------------------------------------------------------------------
func addressChecks() {
    check(BCDEpicCloudHTTP.savesyncURL(account: account, app: "TestApp")?.absoluteString
          == "https://datastorage-public-service-liveegs.live.use1a.on.epicgames.com/api/v1/access/egstore/savesync/\(account)/TestApp/", "the save service's folder for a game of the account")
    check(BCDEpicCloudHTTP.savesyncURL(account: account, app: "../x") == nil && BCDEpicCloudHTTP.savesyncURL(account: "a/b", app: "TestApp") == nil
          && BCDEpicCloudHTTP.savesyncURL(account: account, app: "a b") == nil, "an app name or an account that is not one makes no address")
    check(BCDEpicAPI.trusted(BCDEpicCloudHTTP.savesyncURL(account: account, app: "TestApp")!), "the service's host is one of Epic's (the token may go there)")
}

// ---- the transport over BCD's session ----------------------------------------------------------------
final class Recorder: BCDEpicTransport, @unchecked Sendable {
    struct Call { var method: String; var url: String; var auth: String?; var agent: String?; var body: Data? }
    let lock = NSLock()
    var calls: [Call] = []
    var answers: [(Int, Data)] = []
    func send(_ request: URLRequest) async throws -> (status: Int, body: Data) {
        let answer: (Int, Data) = lock.withLock {
            calls.append(Call(method: request.httpMethod ?? "GET", url: request.url!.absoluteString, auth: request.value(forHTTPHeaderField: "Authorization"),
                              agent: request.value(forHTTPHeaderField: "User-Agent"), body: request.httpBody))
            return answers.isEmpty ? (200, Data()) : answers.removeFirst()
        }
        return (answer.0, answer.1)
    }
}
func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }

let secrets = BCDMemorySecrets()
let tokens = BCDEpicTokens(accessToken: "ACCESS-TOKEN-SECRET", refreshToken: "REFRESH", accessExpires: Date().addingTimeInterval(3600), refreshExpires: nil,
                           accountID: account, displayName: nil)
let recorder = Recorder()
let session = BCDEpicSession(secrets: secrets, transport: recorder)
let api = BCDEpicAPI(session: session, transport: recorder)
let http = BCDEpicCloudHTTP(api: api, accountID: account)

@main struct Harness {
    static func main() async {
        try! secrets.write(BCDEpicSession.itemName, try! JSONEncoder().encode(tokens))
        addressChecks()
        // list: a GET to the service with the token and the launcher's long user agent
        recorder.answers = [(200, json(["files": ["\(account)/savesync/TestApp/manifests/2026.10.15-12.30.45.manifest": ["readLink": "https://signed.example/r1"],
                                                    "\(account)/savesync/TestApp/ChunksV4/00/c.chunk": ["readLink": "https://signed.example/r2"]]]))]
        do {
            let listed = try await http.list(app: "TestApp")
            let call = recorder.calls[0]
            check(listed.count == 2 && listed.contains { $0.path.hasSuffix("manifests/2026.10.15-12.30.45.manifest") && $0.readLink == "https://signed.example/r1" }, "list: the files of the game's folder with their links")
            check(call.method == "GET" && call.auth == "bearer ACCESS-TOKEN-SECRET" && call.agent == BCDEpicCloudHTTP.userAgent && call.url.contains("/savesync/\(account)/TestApp/"),
                  "list: a GET with the token and the launcher's long user agent (the transport keeps a user agent the request names)")
        } catch { check(false, "list: \(error)") }

        // an unreadable answer
        recorder.answers = [(200, Data("[]".utf8))]
        do { _ = try await http.list(app: "TestApp"); check(false, "list: an answer that is not the service's is refused") }
        catch { check((error as? EpicContentError) != nil, "list: an answer that is not the service's is refused") }

        // write targets: a POST with the paths
        recorder.calls = []
        recorder.answers = [(200, json(["files": ["a": ["writeLink": "https://signed.example/w1"]]]))]
        do {
            let targets = try await http.writeTargets(app: "TestApp", paths: ["a", "b"])
            let call = recorder.calls[0]
            let body = (try? JSONSerialization.jsonObject(with: call.body ?? Data()) as? [String: Any])?["files"] as? [String]
            check(targets.first?.writeLink == "https://signed.example/w1" && call.method == "POST" && body == ["a", "b"] && call.auth != nil, "writeTargets: a POST with the paths, the token on it")
        } catch { check(false, "writeTargets: \(error)") }

        // the pre-signed links: no token, https only, no user name, bounded
        recorder.calls = []
        recorder.answers = [(200, Data("chunk-bytes".utf8)), (200, Data()), (403, Data())]
        do {
            let got = try await http.read(EpicCloudObject(path: "p", readLink: "https://signed.example/r", writeLink: nil))
            try await http.write(EpicCloudObject(path: "p", readLink: nil, writeLink: "https://signed.example/w"), data: Data("up".utf8))
            check(got == Data("chunk-bytes".utf8) && recorder.calls.count == 2 && recorder.calls.allSatisfy { $0.auth == nil && $0.agent == BCDEpicCloudHTTP.userAgent },
                  "a pre-signed link is fetched and written without the token")
            check(recorder.calls[1].method == "PUT" && recorder.calls[1].body == Data("up".utf8), "an upload is a PUT of the bytes")
            do { try await http.write(EpicCloudObject(path: "p", readLink: nil, writeLink: "https://signed.example/w"), data: Data()); check(false, "a refused upload is an error") }
            catch { check((error as? EpicContentError).map { "\($0)".contains("403") || $0.errorDescription?.contains("403") == true } ?? false, "a refused upload is an error carrying the status") }
        } catch { check(false, "pre-signed links: \(error)") }
        for bad in ["http://signed.example/r", "https://user:pw@signed.example/r", "ftp://x/y", "https:///nohost", "not a url"] {
            do { _ = try await http.read(EpicCloudObject(path: "p", readLink: bad, writeLink: nil)); check(false, "a link '\(bad.prefix(24))' is refused") }
            catch { check(true, "a link '\(bad.prefix(24))' is refused") }
        }
        check(recorder.calls.count == 3, "…and none of them was fetched")
        do { _ = try await http.read(EpicCloudObject(path: "p", readLink: nil, writeLink: nil)); check(false, "no link: an error") } catch { check(true, "no link: an error") }

        // the save rule from the catalog
        let attrs: [String: Any] = ["CloudSaveFolder": ["type": "STRING", "value": "{AppData}/Game/Saved"], "CloudIncludeList": ["value": "*.sav, Profiles/"],
                                    "CloudExcludeList": ["value": "*.tmp"], "Other": ["value": "x"]]
        let byID: [String: Any] = ["cat1": ["id": "cat1", "title": "T", "customAttributes": attrs]]
        let rule = BCDEpicCloudRules.saveConfig(fromBulk: byID, catalogItemId: "cat1")
        check(rule == EpicSaveConfig(folder: "{AppData}/Game/Saved", include: ["*.sav", "Profiles/"], exclude: ["*.tmp"]), "the save rule: folder, include and exclude lists from the entry's attributes")
        check(BCDEpicCloudRules.saveConfig(fromBulk: ["items": [["id": "cat1", "customAttributes": attrs]]], catalogItemId: "cat1") == rule, "…from a list of entries too")
        check(BCDEpicCloudRules.saveConfig(fromBulk: ["cat1": ["id": "cat1", "customAttributes": ["FolderName": ["value": "x"]]]], catalogItemId: "cat1") == nil
              && BCDEpicCloudRules.saveConfig(fromBulk: byID, catalogItemId: "other") == nil && BCDEpicCloudRules.saveConfig(fromBulk: [:], catalogItemId: "cat1") == nil, "a game without a save folder, or an entry that is not there: no cloud saves")

        // the backup folder
        let root = URL(fileURLWithPath: "/tmp/x")
        let backup = BCDEpicCloudRules.backupFolder(title: "A/B: C?", app: "App1", root: root, now: Date(timeIntervalSince1970: 0))
        check(backup.path == "/tmp/x/A-B- C- (App1)/1970.01.01-00.00.00", "the backup folder: a safe title, the app, the time: \(backup.path)")
        check(BCDEpicCloudRules.backupFolder(title: "...", app: "App1", root: root).deletingLastPathComponent().lastPathComponent == "App1", "a title of nothing usable: the app name")

        // what is remembered
        let work = FileManager.default.temporaryDirectory.appendingPathComponent("epic-cloud-bcd-\(getpid())")
        try? FileManager.default.removeItem(at: work)   // start empty: exit() skips defer, so a folder with a reused process id may be left
        try? FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: work) }
        let store = work.appendingPathComponent("cloud-sync.json")
        var records: [String: BCDEpicCloudRecord] = ["TestApp": BCDEpicCloudRecord(account: BCDEpicCatalog.accountKey(account), config: rule, noCloud: false,
                                                                                   baseline: EpicSaveBaseline(device: ["a.sav": "aa"], cloud: ["a.sav": "aa"], snapshot: "2026.10.15-12.30.45")),
                                                     "../evil": BCDEpicCloudRecord()]
        BCDEpicCloudStore.save(records, to: store)
        let text = (try? String(contentsOf: store, encoding: .utf8)) ?? ""
        check(!text.contains(account) && !text.contains("ACCESS") && text.contains(BCDEpicCatalog.accountKey(account)), "the file holds the account's hash, never the ID or a token")
        records["../evil"] = nil
        check(BCDEpicCloudStore.load(store) == ["TestApp": records["TestApp"]!], "what is remembered comes back, and a name that is not an app's is dropped")
        try? Data("not json".utf8).write(to: store)
        check(BCDEpicCloudStore.load(store).isEmpty, "a damaged file is an empty list")

        // the build version's date, from numbers
        for (seconds, text) in [(0.0, "1970.01.01-00.00.00"), (1_800_000_000.0, "2027.01.15-08.00.00"), (951_782_400.0 + 86_399, "2000.02.29-23.59.59"), (-1.0, "1969.12.31-23.59.59"),
                                (4_102_444_800.0, "2100.01.01-00.00.00"), (1_760_531_445.0, "2025.10.15-12.30.45")] {
            check(EpicBuildTime.format(Date(timeIntervalSince1970: seconds)) == text && EpicBuildTime.parse(text)?.timeIntervalSince1970 == seconds,
                  "the build version \(text) is \(Int(seconds)) s, both ways")
        }
        for bad in ["2026.02.30-10.00.00", "2026.13.01-00.00.00", "2026.10.15 12.30.45", "2026.10.15-24.00.00", "26.10.15-12.30.45", "2026.10.15-12.30", "2026.1.5-12.30.45", "x", "", "2026.10.15-12.30.45-1"] {
            check(EpicBuildTime.parse(bad) == nil, "not a build version: '\(bad)'")
        }
        check(EpicCloudSaves.date(ofBuildVersion: "2025.10.15-12.30.45") == Date(timeIntervalSince1970: 1_760_531_445) && EpicSaveWriter.buildVersion(Date(timeIntervalSince1970: 1_760_531_445)) == "2025.10.15-12.30.45",
              "the snapshot's date goes through the same two functions")

        // what a page shows: the saves that differ
        let device = [EpicDeviceSave(path: "same.sav", size: 4, sha: "11", modified: Date(timeIntervalSince1970: 100), url: root),
                      EpicDeviceSave(path: "changed.sav", size: 5, sha: "22", modified: Date(timeIntervalSince1970: 200), url: root),
                      EpicDeviceSave(path: "device-only.sav", size: 6, sha: "33", modified: Date(timeIntervalSince1970: 300), url: root)]
        let snapshot = EpicCloudSnapshot(buildVersion: "2026.10.15-12.30.45", manifest: EpicManifestStub.make(), prefix: "p",
                                         files: ["same.sav": "11", "changed.sav": "99", "cloud-only.sav": "44"], sizes: ["same.sav": 4, "changed.sav": 7, "cloud-only.sav": 8],
                                         date: Date(timeIntervalSince1970: 150))
        let differences = BCDEpicCloudState.differences(device: device, cloud: snapshot)
        check(differences.map(\.name) == ["changed.sav", "cloud-only.sav", "device-only.sav"], "the saves that differ: changed, only in the cloud, only here (the same one is not listed): \(differences.map(\.name))")
        check(differences[0].cloudBytes == 7 && differences[0].deviceBytes == 5 && differences[1].deviceBytes == nil && differences[2].cloudBytes == 0, "…each with what the two sides hold")
        check(BCDEpicCloudState.differences(device: [], cloud: nil).isEmpty, "nothing on either side: nothing differs")

        // a whole sync against an in-memory service: upload, then download on another device
        final class Cloud: EpicCloudTransport, @unchecked Sendable {
            var files: [String: Data] = [:]
            func list(app: String) async throws -> [EpicCloudObject] { files.keys.sorted().map { EpicCloudObject(path: "acct/savesync/\(app)/" + $0, readLink: "r:" + $0, writeLink: nil) } }
            func read(_ object: EpicCloudObject) async throws -> Data { files[String(object.readLink!.dropFirst(2))]! }
            func writeTargets(app: String, paths: [String]) async throws -> [EpicCloudObject] { paths.map { EpicCloudObject(path: $0, readLink: nil, writeLink: "w:" + $0) } }
            func write(_ object: EpicCloudObject, data: Data) async throws { files[String(object.writeLink!.dropFirst(2))] = data }
        }
        let cloud = Cloud()
        let deviceA = work.appendingPathComponent("A"), deviceB = work.appendingPathComponent("B")
        try? FileManager.default.createDirectory(at: deviceA.appendingPathComponent("Profiles"), withIntermediateDirectories: true)
        try? Data("slot one".utf8).write(to: deviceA.appendingPathComponent("slot1.sav"))
        try? Data(repeating: 7, count: 1_500_000).write(to: deviceA.appendingPathComponent("Profiles/big.sav"))
        try? Data("skip".utf8).write(to: deviceA.appendingPathComponent("junk.tmp"))
        do {
            let ruleAll = EpicSaveConfig(folder: "{AppData}/G", include: [], exclude: ["*.tmp"])
            let scanned = try EpicSaveScan.scan(deviceA, config: ruleAll)
            check(scanned.map(\.path) == ["Profiles/big.sav", "slot1.sav"], "the scan follows the exclude list: \(scanned.map(\.path))")
            let sent = try await EpicCloudSaves.upload(scanned, app: "TestApp", accountID: account, config: ruleAll, transport: cloud, date: Date(timeIntervalSince1970: 1_800_000_000))
            check(sent.files == 2 && cloud.files.keys.contains { $0.hasPrefix("manifests/") } && cloud.files.keys.contains { $0.hasPrefix("ChunksV4/") }, "upload: a snapshot, its manifest and its chunks")
            let latest = try await EpicCloudSaves.latest(app: "TestApp", transport: cloud)
            check(latest?.snapshot.files.count == 2 && latest?.snapshot.buildVersion == sent.version, "the newest snapshot is the one just sent")
            let got = try await EpicCloudSaves.download(latest!.snapshot, all: latest!.all, into: deviceB, backup: work.appendingPathComponent("backup"), transport: cloud)
            let same = (try? Data(contentsOf: deviceB.appendingPathComponent("Profiles/big.sav"))) == Data(repeating: 7, count: 1_500_000)
                && (try? String(contentsOf: deviceB.appendingPathComponent("slot1.sav"), encoding: .utf8)) == "slot one"
            check(got.files == 2 && same && !FileManager.default.fileExists(atPath: deviceB.appendingPathComponent("junk.tmp").path), "download on another device: both files, byte for byte, none that was excluded")
            let decide = EpicSavePlanner.decide(device: Dictionary(uniqueKeysWithValues: scanned.map { ($0.path, $0.sha) }), cloud: latest?.snapshot.files, baseline: nil)
            check(decide == .inSync, "both sides now agree")
        } catch { check(false, "a whole sync: \(error)") }

        print(failures == 0 ? "ALL OK" : "\(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}

/// A manifest with nothing in it, for a snapshot value (the page's difference list reads only the snapshot's file maps).
enum EpicManifestStub {
    static func make() -> EpicManifest {
        let package = try! EpicSaveWriter.package(files: [], appName: "A", accountID: "B", saveFolder: "{AppData}/x", date: Date(timeIntervalSince1970: 0))
        let manifest = package.uploads.first { $0.path.hasPrefix("manifests/") }!.data
        return try! EpicManifest.parse(manifest)
    }
}
