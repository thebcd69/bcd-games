// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// The Swift half of tests/host/check-epic.py for milestone 1 (install): Epic's chunk files, binary
// manifests, the asset service's pointer, the plan, the rules (what BCD refuses and why), the launch
// arguments, and the whole install over a local CDN (the engine, the records, the folders). The builds,
// manifests and chunk files are synthetic, written by tests/host/epic_fixtures.py from the layout the
// design records; the CDN is a local server; Epic is never contacted.

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

var failures = 0
func check(_ ok: Bool, _ what: String) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
    fflush(stdout)
}

let logLock = NSLock()
nonisolated(unsafe) var logs: [String] = []
func installSink() {
    BCDContentLog.sink = { line in
        logLock.lock(); logs.append(line); logLock.unlock()
        print("LOG " + line)
    }
}

struct BuildInfo: Decodable {
    var prefix: String
    var manifest: String
    var sha1: String
    var level: Int
    var locators: [String]
    var wireSizes: [Int64]
    var windows: [Int64]
    var expected: String
    var paths: [String]
}

struct ChunkFileInfo: Decodable {
    var wire: String
    var plain: String
    var hv: Int
    var storedAs: Int
}

struct Fixture: Decodable {
    /// Three listeners of one CDN (three hosts: the engine blames a host that fails), and a port nothing listens on.
    var cdn: String
    var cdn2: String
    var cdn3: String
    var dead: String
    var work: String
    var builds: [String: BuildInfo]
    var variants: [String: String]
    var chunkFiles: [ChunkFileInfo]
    var hostile: [String]
    var fuzz: [String]
}

let fixture: Fixture = {
    let path = ProcessInfo.processInfo.environment["EPIC_FIXTURE"] ?? ""
    guard let data = FileManager.default.contents(atPath: path), let value = try? JSONDecoder().decode(Fixture.self, from: data) else {
        print("FAIL: the fixture could not be read"); exit(2)
    }
    return value
}()

func file(_ relative: String) -> Data { FileManager.default.contents(atPath: fixture.work + "/" + relative) ?? Data() }

func hex(_ bytes: [UInt8]) -> String { bytes.map { String(format: "%02x", $0) }.joined() }

func raw(_ data: Data, _ body: (UnsafeRawBufferPointer) throws -> Void) rethrows { try data.withUnsafeBytes { try body($0) } }

/// Feeds `wire` to a fresh decoder in pieces of `step` bytes (or at the given cuts); returns what came out, or the error.
func decode(_ wire: Data, step: Int = Int.max, cuts: [Int]? = nil) -> (data: Data?, error: Error?, decoder: BCDChunkFileDecoder) {
    let decoder = BCDChunkFileDecoder()
    var out = Data()
    do {
        var at = 0
        var pieces = cuts ?? []
        while at < wire.count {
            let size = cuts != nil ? (pieces.isEmpty ? wire.count - at : min(pieces.removeFirst(), wire.count - at)) : min(step, wire.count - at)
            try wire.subdata(in: at..<at + max(size, 1)).withUnsafeBytes { input in
                try decoder.feed(input) { out.append(contentsOf: $0) }
            }
            at += max(size, 1)
        }
        try decoder.finish()
        return (out, nil, decoder)
    } catch {
        return (nil, error, decoder)
    }
}

func isError(_ error: Error?, _ expected: BCDContentError) -> Bool { (error as? BCDContentError) == expected }

// MARK: Phase 1: chunk files

func chunkPhase() {
    for (i, info) in fixture.chunkFiles.enumerated() {
        let wire = file(info.wire), plain = file(info.plain)
        let whole = decode(wire)
        check(whole.data == plain && whole.decoder.header?.version == info.hv && whole.decoder.header?.storedAs == UInt8(info.storedAs),
              "chunk \(i) (header v\(info.hv), stored-as \(info.storedAs)): decoded whole")
        let size = whole.decoder.header?.headerSize ?? 0
        check(size == [1: 41, 2: 62, 3: 66, 4: 98][info.hv] && whole.decoder.received == Int64(wire.count)
              && whole.decoder.header?.bodySize == Int64(wire.count - size), "chunk \(i): the header's size and the body's length")
        if info.hv >= 3 { check(whole.decoder.header?.decodedSize == Int64(plain.count), "chunk \(i): the header's decoded size is the data's") }
        if info.hv >= 2 { check(hex(whole.decoder.header?.sha1 ?? []) == BCDHasher.hex(.sha1, of: plain), "chunk \(i): the header's SHA-1 is the data's") }
        check(decode(wire, step: 1).data == plain, "chunk \(i): decoded a byte at a time")
        var seed: UInt64 = UInt64(i + 1) &* 0x9E3779B97F4A7C15
        func next() -> Int { seed = seed &* 6364136223846793005 &+ 1442695040888963407; return Int(seed >> 33) }
        let cuts = (0..<12).map { _ in 1 + next() % max(1, wire.count / 4) }
        check(decode(wire, cuts: cuts).data == plain, "chunk \(i): decoded in odd pieces")
        check(decode(wire, step: size).data == plain && decode(wire, step: size - 1).data == plain && decode(wire, step: size + 1).data == plain,
              "chunk \(i): decoded when a piece ends at, before and after the header's end")
    }

    let wire = file(fixture.chunkFiles[7].wire)     // the small one (header v3, zlib)
    // Every prefix of a chunk file is incomplete, whatever the cut.
    var prefixesFail = true
    for n in stride(from: 0, to: wire.count, by: 7) {
        let result = decode(wire.prefix(n))
        if result.error == nil { prefixesFail = false }
    }
    check(prefixesFail, "no prefix of a chunk file is accepted as a whole one")
    check(isError(decode(wire + Data([0])).error, .tooLarge), "a byte past the end of the body is refused")
    check(isError(decode(wire + wire).error, .tooLarge), "a second chunk file after the first is refused")
    func patched(_ at: Int, _ value: [UInt8]) -> Data { var d = wire; d.replaceSubrange(at..<at + value.count, with: value); return d }
    check(isError(decode(patched(0, [0, 0, 0, 0])).error, .decode), "a wrong magic is refused")
    check(isError(decode(patched(4, [9, 0, 0, 0])).error, .decode) && isError(decode(patched(4, [0, 0, 0, 0])).error, .decode), "an unknown header version is refused")
    check(isError(decode(patched(8, [10, 0, 0, 0])).error, .decode) && isError(decode(patched(8, [0, 8, 0, 0])).error, .decode)
          && isError(decode(patched(8, [65, 0, 0, 0])).error, .decode), "a header size below the version's or above the limit is refused")
    check(isError(decode(patched(12, [0, 0, 0, 0])).error, .decode), "a body of no bytes is refused")
    check(isError(decode(patched(12, [0xFF, 0xFF, 0xFF, 0x7F])).error, .decode), "a body past the limit is refused")
    check(isError(decode(patched(12, [0xF0, 0xFF, 0, 0])).error, .decode), "a body longer than the file is incomplete")
    check({ if case .refused? = decode(patched(40, [3])).error as? BCDContentError { return true }; return false }(), "an encrypted chunk is refused, not retried")
    var garbled = wire
    for k in (66 + 8)..<(66 + 40) { garbled[k] ^= 0x5A }
    check(decode(garbled).error != nil, "a damaged zlib body is refused")
    check(decode(Data()).error != nil, "an empty file is refused")
    // Stored data: the body is the data itself, and the header may not claim otherwise.
    let stored = file(fixture.chunkFiles[3].wire)
    check(decode(stored).data == file(fixture.chunkFiles[3].plain), "stored data comes through as it is")
    var truncatedStored = stored
    truncatedStored.removeLast(5)
    check(decode(truncatedStored).error != nil, "stored data cut short is incomplete")
}

// MARK: Phase 2: manifests

func manifestPhase() async {
    let main = BCDEpicManifest.parse
    do {
        let m = try main(file(fixture.variants["main"]!))
        check(m.featureLevel == 17 && m.appName == "Fixture" && m.buildVersion == "1.2.3-fixture" && m.launchExe == "Game\\Binaries\\Win64\\Game.exe"
              && m.launchCommand == "-nosplash" && m.buildID == "build-id-fixture" && m.prereqIDs == ["prereq-id"], "the main manifest: level, names, launch program and command")
        check(m.chunks.count == 8 && m.files.count == 11, "...8 chunks and 11 files")
        let build = fixture.builds["main"]!
        check(m.chunks.map(\.fileSize) == build.wireSizes && m.chunks.map(\.window) == build.windows, "...each chunk's file size and window")
        check(m.chunks.map { BCDEpicContent.chunkPath(featureLevel: m.featureLevel, $0) } == build.locators, "...and the chunk paths the fixtures name (directory by level, group, hash, GUID)")
        check(m.files[0].name == "Game/Binaries/Win64/Game.exe" && m.files[0].size == 923_457 && m.files[0].parts.count == 3 && m.files[0].flags == 4
              && m.files[0].sha1.count == 20, "...a file: name, size from its parts, flags, SHA-1")
        check(m.files[8].parts.isEmpty && m.files[8].size == 0 && m.files[9].symlinkTarget == "Game/Content", "...an empty file and a symlink")
        check(m.totalBytes == build.expectedTotal && m.downloadBytes == build.wireSizes.reduce(0, +), "...the install and download sizes")
        check(m.chunks[0].guid.hex.count == 32 && m.chunks[0].guid.hex == m.chunks[0].guid.hex.uppercased(), "...a GUID is 32 upper-case digits")
    } catch { check(false, "the main manifest: \(error)") }

    // Optional columns and sections come and go with the data versions.
    for name in ["meta0", "fml0", "fml1", "utf16"] {
        do {
            let m = try main(file(fixture.variants[name]!))
            check(m.files.count == 11 && m.chunks.count == 8 && (name == "meta0" ? m.buildID == nil : m.buildID == "build-id-fixture")
                  && (name != "utf16" || m.files.contains { $0.name == "Spiel/Größe.bin" }), "manifest variant \(name): read")
        } catch { check(false, "manifest variant \(name): \(error)") }
    }
    func fails(_ name: String, _ expected: BCDEpicManifestError) {
        do { _ = try main(file(fixture.variants[name]!)); check(false, "manifest \(name) was accepted") } catch {
            check((error as? BCDEpicManifestError) == expected, "manifest \(name) is refused (\(error))")
        }
    }
    fails("newer", .newer(22))
    fails("encrypted", .encrypted)
    fails("badhash", .badHash)
    fails("magic", .notManifest)
    check(BCDEpicManifestError.newer(22).errorDescription?.contains("newer format") == true && BCDEpicManifestError.encrypted.errorDescription?.contains("encrypted") == true,
          "...with words for the player")
    do { _ = try main(Data()) } catch { check((error as? BCDEpicManifestError) == .notManifest, "an empty file is not a manifest") }

    // Cut anywhere, a manifest is refused (never read past its end).
    let whole = file(fixture.variants["main"]!)
    var accepted = 0
    for n in stride(from: 0, to: whole.count - 1, by: 3) { if (try? main(whole.prefix(n))) != nil { accepted += 1 } }
    check(accepted == 0, "no cut-short manifest is accepted")
    // Hostile bodies with valid hashes: refused or read safely.
    var refused = 0, read = 0
    for path in fixture.hostile {
        if let m = try? main(file(path)) {
            read += 1
            // What is read must hold together: every part inside its chunk's window.
            let sound = m.files.allSatisfy { $0.parts.allSatisfy { $0.chunk >= 0 && $0.chunk < m.chunks.count && $0.offset + $0.size <= m.chunks[$0.chunk].window } }
            check(sound, "hostile manifest \(path) was read, and holds together")
        } else {
            refused += 1
        }
    }
    check(refused >= 14, "hostile manifests (lying counts, sizes, GUIDs, strings) are refused: \(refused) of \(fixture.hostile.count) (\(read) read safely)")
    for name in ["cdl-huge", "cdl-over", "cdl-more", "fml-huge", "fml-over", "fml-more", "meta-short", "meta-long", "part-unknown-guid", "part-offset", "part-size", "string-huge", "string-huge-wide"] {
        check((try? main(file("hostile/" + name + ".bin"))) == nil, "hostile manifest \(name) is refused")
    }
    // A large game's manifest: 60,000 chunks and 20,000 files, read and planned in seconds.
    do {
        let started = Date()
        let big = try main(file(fixture.variants["scale"]!))
        let parsed = Date().timeIntervalSince(started)
        let planned = try await BCDEpicContent.plan(BCDContentKey(store: "epic", id: "Scale"), title: "Scale", folder: "Scale",
                                                    loaded: BCDEpicContent.Loaded(build: BCDEpicBuild(buildVersion: "9.9", manifestHash: nil, manifestURLs: [], bases: [], deploymentID: nil, revision: nil),
                                                                                  bases: [], file: Data(), hash: "", manifest: big), item: item())
        let both = Date().timeIntervalSince(started)
        check(big.chunks.count == 60_000 && big.files.count == 20_000 && planned.plan.files.count == 20_000 && planned.plan.chunks.count == 60_000,
              "a manifest of 60,000 chunks and 20,000 files is read and planned (\(String(format: "%.1f", parsed)) s to read, \(String(format: "%.1f", both)) s with the plan)")
        check(both < 120, "...in a bounded time even unoptimised and under the address sanitizer")
    } catch { check(false, "a large manifest: \(error)") }
    var worst = 0
    for path in fixture.fuzz { if (try? main(file(path))) != nil { worst += 1 } }
    check(true, "\(fixture.fuzz.count) damaged manifests did not crash the reader (\(worst) still read)")
}

extension BuildInfo {
    var expectedTotal: Int64 {
        paths.reduce(0) { $0 + Int64(FileManager.default.contents(atPath: fixture.work + "/" + expected + "/" + $1)?.count ?? 0) }
    }
}

// MARK: Phase 3: the asset service's pointer

func assetPhase() {
    func element(_ extra: [String: Any] = [:], manifests: [[String: Any]]? = nil, remove: [String] = []) -> [String: Any] {
        var e: [String: Any] = ["appName": "Fixture", "labelName": "Live-Windows", "buildVersion": "1.2.3",
                                "hash": "ABCDEF0123456789ABCDEF0123456789ABCDEF01",
                                "manifests": manifests ?? [["uri": "http://download.example/Builds/Org/o-x/ITEM/default/AAAA.manifest",
                                                            "queryParams": [["name": "f_token", "value": "a+b/c=="], ["name": "x", "value": "1 2"]]],
                                                           ["uri": "https://cdn2.example:443/Builds/Org/o-x/ITEM/default/AAAA.manifest", "queryParams": []]],
                                "sidecar": ["config": "{\"deploymentId\":\"0123456789ABCDEF0123456789abcdef\",\"x\":1}", "rvn": 4]]
        for (k, v) in extra { e[k] = v }
        for k in remove { e[k] = nil }
        return ["elements": [e]]
    }
    do {
        let b = try BCDEpicContent.parseAsset(element())
        check(b.buildVersion == "1.2.3" && b.manifestHash == "abcdef0123456789abcdef0123456789abcdef01" && b.revision == 4
              && b.deploymentID == "0123456789abcdef0123456789abcdef", "the pointer: version, manifest hash (lower case), revision, deployment ID")
        check(b.manifestURLs.count == 2 && b.manifestURLs[0].absoluteString == "https://download.example/Builds/Org/o-x/ITEM/default/AAAA.manifest?f_token=a%2Bb%2Fc%3D%3D&x=1%202"
              && b.manifestURLs[1].absoluteString == "https://cdn2.example/Builds/Org/o-x/ITEM/default/AAAA.manifest", "...http becomes https, the CDN's token is percent-encoded")
        check(b.bases.map(\.absoluteString) == ["https://download.example/Builds/Org/o-x/ITEM/default", "https://cdn2.example/Builds/Org/o-x/ITEM/default"],
              "...the chunks' folders have no query and no file name")
    } catch { check(false, "the pointer: \(error)") }
    func asset(_ e: [String: Any]) -> BCDEpicContentError? { do { _ = try BCDEpicContent.parseAsset(e); return nil } catch { return error as? BCDEpicContentError } }
    check(asset(["elements": []]) == .noBuild && asset([:]) == .noBuild, "no element: Epic offers no Windows download")
    check(asset(element(remove: ["buildVersion"])) == .badAsset("version"), "no build version is refused")
    check(asset(element(manifests: [])) == .badAsset("manifests") && asset(element(remove: ["manifests"])) == .badAsset("manifests"), "no manifests is refused")
    check(asset(element(manifests: [["uri": "ftp://x/y/z.manifest"], ["uri": "https://u:p@x/y/z.manifest"], ["uri": ""], ["nouri": 1]])) == .badAsset("uri"),
          "only ftp, credentials, empty and missing addresses: refused")
    let mixed = try? BCDEpicContent.parseAsset(element(manifests: [["uri": "ftp://x/a"], ["uri": "https://good.example/p/q.manifest"]]))
    check(mixed?.manifestURLs.count == 1 && mixed?.bases.first?.host == "good.example", "a bad address among good ones is skipped")
    let weird = try? BCDEpicContent.parseAsset(element(["hash": "zz", "sidecar": ["config": "{bad", "rvn": "x"]]))
    check(weird?.manifestHash == nil && weird?.deploymentID == nil && weird?.revision == nil, "a malformed hash and sidecar are ignored, not fatal")
    let shortID = try? BCDEpicContent.parseAsset(element(["sidecar": ["config": "{\"deploymentId\":\"abc\"}"]]))
    check(shortID?.deploymentID == nil, "a deployment ID that is not 32 hexadecimal digits is dropped")
    let many = (0..<30).map { ["uri": "https://c\($0).example/p/q.manifest"] as [String: Any] }
    check((try? BCDEpicContent.parseAsset(element(manifests: many)))?.manifestURLs.count == 16, "at most sixteen manifests are read")

    check(BCDEpicContent.folder(of: URL(string: "https://a.example/b/c/d.manifest?x=1")!)?.absoluteString == "https://a.example/b/c"
          && BCDEpicContent.folder(of: URL(string: "https://a.example")!) == nil, "the folder of an address")
    let a = URL(string: "https://one.example/x")!, bb = URL(string: "https://two.cdn.example/x")!, c = URL(string: "https://three.example/x")!
    check(BCDEpicContent.order([a, bb, c], preferring: "two") == [bb, a, c] && BCDEpicContent.order([a, bb, c], preferring: "TWO.CDN") == [bb, a, c]
          && BCDEpicContent.order([a, bb, c], preferring: nil) == [a, bb, c] && BCDEpicContent.order([a, bb, c], preferring: "nothing") == [a, bb, c]
          && BCDEpicContent.order([a, bb, c], preferring: "") == [a, bb, c], "env.BCD_EPIC_CDN puts the CDN whose host matches first")
    check(BCDEpicContent.assetURL(namespace: "ns1", catalogItem: "cat2", app: "App.3").absoluteString
          == "https://launcher-public-service-prod06.ol.epicgames.com/launcher/api/public/assets/v2/platform/Windows/namespace/ns1/catalogItem/cat2/app/App.3/label/Live",
          "the asset pointer's address (v2, Windows, Live)")
    var bytes = BCDEpicManifest.Chunk(guid: BCDEpicGUID(a: 0x01020304, b: 0xAABBCCDD, c: 0, d: 0xFFFFFFFF), rollingHash: 0xDEADBEEF, sha1: [], group: 7, window: 1, fileSize: 1)
    check(BCDEpicContent.chunkPath(featureLevel: 17, bytes) == "ChunksV4/07/00000000DEADBEEF_01020304AABBCCDD00000000FFFFFFFF.chunk"
          && BCDEpicContent.chunkPath(featureLevel: 15, bytes).hasPrefix("ChunksV4/") && BCDEpicContent.chunkPath(featureLevel: 14, bytes).hasPrefix("ChunksV3/")
          && BCDEpicContent.chunkPath(featureLevel: 6, bytes).hasPrefix("ChunksV3/") && BCDEpicContent.chunkPath(featureLevel: 5, bytes).hasPrefix("ChunksV2/")
          && BCDEpicContent.chunkPath(featureLevel: 3, bytes).hasPrefix("ChunksV2/"), "a chunk's path: directory by level, group, 16-digit hash, 32-digit GUID")
    bytes.group = 255
    check(BCDEpicContent.chunkPath(featureLevel: 17, bytes).hasPrefix("ChunksV4/255/") && !BCDEpicContent.validLocator(BCDEpicContent.chunkPath(featureLevel: 17, bytes)) == false,
          "...a group of three digits is kept")
    let good = "ChunksV4/05/DEADBEEF12345678_0123456789ABCDEF0123456789ABCDEF.chunk"
    check(BCDEpicContent.validLocator(good), "a chunk locator of the made shape is valid")
    for bad in ["", "../ChunksV4/05/DEADBEEF12345678_0123456789ABCDEF0123456789ABCDEF.chunk", "ChunksV4/05/DEADBEEF12345678_0123456789ABCDEF0123456789ABCDEF.chunk/../x",
                "ChunksV9/05/DEADBEEF12345678_0123456789ABCDEF0123456789ABCDEF.chunk", "ChunksV4/5/DEADBEEF12345678_0123456789ABCDEF0123456789ABCDEF.chunk",
                "ChunksV4/05/deadbeef12345678_0123456789abcdef0123456789abcdef.chunk", "ChunksV4/05/DEADBEEF12345678_0123456789ABCDEF.chunk",
                "ChunksV4/05/DEADBEEF12345678_0123456789ABCDEF0123456789ABCDEF.chunk?x=1", "https://evil.example/x.chunk", "ChunksV4/aa/DEADBEEF12345678_0123456789ABCDEF0123456789ABCDEF.chunk",
                "ChunksV4/05/DEADBEEF12345678_0123456789ABCDEF0123456789ABCDEF.chunk\n"] {
        check(!BCDEpicContent.validLocator(bad), "not a chunk locator: \(bad.prefix(50).replacingOccurrences(of: "\n", with: "\\n"))")
    }
}

// MARK: Phase 4: the rules and the launch arguments

func item(_ edit: (inout BCDEpicItem) -> Void = { _ in }) -> BCDEpicItem {
    var i = BCDEpicItem(id: "Fixture", namespace: "nsfix", catalogItemId: "0123456789abcdef0123456789abcdef")
    i.kind = .game
    i.title = "Fixture Game"
    i.folderName = "FixtureGame"
    i.canRunOffline = true
    edit(&i)
    return i
}

func rulesPhase() {
    check(BCDEpicRules.catalogRefusal(item()) == nil && BCDEpicRules.catalogWarning(item()) == nil, "a plain game: nothing to refuse or warn about")
    check(BCDEpicRules.catalogRefusal(item { $0.ownershipToken = true }) == nil
          && BCDEpicRules.catalogRefusal(item { $0.ownershipToken = true }, signedStart: false)?.code == "ownership-token"
          && BCDEpicRules.catalogRefusal(item { $0.ownershipToken = true }, signedStart: false)?.message.contains("switched off") == true,
          "an ownership-token game is installable now that BCD starts games signed in; with the signed-in start off it is refused, saying why (milestone 2)")
    check(BCDEpicRules.catalogRefusal(item { $0.accessControl = true })?.code == "access-control", "a game with Epic's access control is refused")
    let ubi = BCDEpicRules.catalogRefusal(item { $0.thirdParty = "Ubisoft" })
    check(ubi?.code == "other-launcher" && ubi?.message.contains("(Ubisoft)") == true, "another company's launcher is refused, by name")
    check(BCDEpicRules.catalogRefusal(item { $0.thirdParty = "yes" })?.message.contains("another company's launcher, which") == true, "...and without a name when Epic gives none")
    check(BCDEpicRules.catalogRefusal(item { $0.thirdParty = "<b>Evil</b>\n" })?.message.contains("<") == false, "...a provider's name is cleaned before it is shown")
    check(BCDEpicRules.catalogRefusal(item { $0.thirdParty = "Ubisoft"; $0.ownershipToken = true })?.code == "other-launcher", "the launcher reason comes first")
    check(BCDEpicRules.catalogWarning(item { $0.canRunOffline = false }) == BCDEpicRules.onlineNote && BCDEpicRules.catalogWarning(item { $0.canRunOffline = false }, signedStart: false) == BCDEpicRules.offlineNote
          && BCDEpicRules.catalogRefusal(item { $0.canRunOffline = false }) == nil
          && BCDEpicRules.catalogWarning(item { $0.canRunOffline = nil }) == nil, "a game that must be online is warned about, not refused")
    for text in [BCDEpicRules.offlineNote, BCDEpicRules.onlineNote, BCDEpicRules.tokenNote, BCDEpicRules.eosNote(signedStart: true), BCDEpicRules.eosNote(signedStart: false), BCDEpicRules.gtaReason, ubi?.message ?? "",
                 BCDEpicRules.catalogRefusal(item { $0.ownershipToken = true }, signedStart: false)?.message ?? ""] {
        check(!text.contains("Madeira") && text.hasSuffix(".") && text.count < 260, "a message is plain, short and never says Madeira: \(text.prefix(40))")
    }

    // The manifest's files.
    check(BCDEpicRules.antiCheat(in: ["Game/Binaries/Win64/Game.exe", "Game/EasyAntiCheat/EasyAntiCheat_Setup.exe"]) == "Easy Anti-Cheat"
          && BCDEpicRules.antiCheat(in: ["Game\\EasyAntiCheat_EOS\\Launcher.exe"]) == "Easy Anti-Cheat"
          && BCDEpicRules.antiCheat(in: ["Game/BattlEye/BEClient_x64.dll"]) == "BattlEye" && BCDEpicRules.antiCheat(in: ["BEService_x64.exe"]) == "BattlEye"
          && BCDEpicRules.antiCheat(in: ["Game/EQU8/equ8.dll"]) == "EQU8" && BCDEpicRules.antiCheat(in: ["Game/equ8_helper.exe"]) == "EQU8",
          "anti-cheat is found by its files (Easy Anti-Cheat, BattlEye, EQU8)")
    check(BCDEpicRules.antiCheat(in: ["Game/Binaries/Win64/Game.exe", "Game/Content/anticheating-tips.txt", "Game/Content/belt.dll", "Game/Content/beauty/BEAUTY.dll"]) == nil
          && BCDEpicRules.antiCheat(in: []) == nil, "...and an ordinary game is not mistaken for one")
    // round 624 (agent R S24; 7Z8VKR, TV6H06): Epic's GTA V starts Rockstar's launcher, which needs Windows' service manager
    check(BCDEpicRules.startsRockstarLauncher("gta5-enhanced") && BCDEpicRules.startsRockstarLauncher("gta5-legacy")
          && !BCDEpicRules.startsRockstarLauncher(nil) && !BCDEpicRules.startsRockstarLauncher("other")
          && BCDEpicRules.antiCheatExceptions.allSatisfy { BCDEpicRules.startsRockstarLauncher($0.id) },
          "the GTA V records (both editions, every anti-cheat exception today) start Windows services; a record without an exception does not")
    check(BCDEpicRules.manifestRefusal(files: ["Game/Bin/Win64/Game.exe"], launchExe: "game\\BIN\\win64\\GAME.EXE") == nil
          && BCDEpicRules.manifestRefusal(files: ["Game/Bin/Win64/Game.exe"], launchExe: "./Game/Bin/Win64/Game.exe") == nil
          && BCDEpicRules.manifestRefusal(files: ["Game/Bin/Win64/Game.exe"], launchExe: "Game\\Other.exe")?.message == "The program Epic names for this game isn't among its files.",
          "the program Epic names must be among the files, whatever its letter case or slashes")
    check(BCDEpicRules.manifestRefusal(files: ["a.exe"], launchExe: "a.exe") == nil && BCDEpicRules.manifestRefusal(files: ["a.exe"], launchExe: "")?.code == "no-program"
          && BCDEpicRules.manifestRefusal(files: ["a.exe"], launchExe: "start.bat")?.code == "no-program"
          && BCDEpicRules.manifestRefusal(files: ["a.exe", "EasyAntiCheat/x.dll"], launchExe: "a.exe")?.code == "anti-cheat", "a build with no Windows program, or with anti-cheat, is refused")
    check(BCDEpicRules.manifestRefusal(files: ["Game/Game.exe", "Game/EasyAntiCheat/x.dll"], launchExe: "Game/Game.exe")?.message == "This game comes with anti-cheat software (Easy Anti-Cheat), which can't run in BCD."
          && BCDEpicRules.manifestRefusal(files: ["Game/Game.exe", "BattlEye/BEClient.dll"], launchExe: "Game/Game.exe")?.message == "This game comes with anti-cheat software (BattlEye), which can't run in BCD.",
          "the anti-cheat refusal names the software once")
    check(BCDEpicRules.usesEOS(["Game/Binaries/Win64/EOSSDK-Win64-Shipping.dll"]) && BCDEpicRules.usesEOS(["x\\eossdk-win32-shipping.dll"]) && !BCDEpicRules.usesEOS(["Game/EOSSDK-readme.txt", "Game/Game.exe"]),
          "Epic Online Services' SDK is found by its DLL")
    check(BCDEpicRules.prerequisiteNote(name: "", path: "") == nil && BCDEpicRules.prerequisiteNote(name: "UE Prerequisites (x64)", path: "Engine/Extras/Redist/en-us/UEPrereqSetup_x64.exe") == nil
          && BCDEpicRules.prerequisiteNote(name: "", path: "Engine/Extras/Redist/UE4PrereqSetup_x64.exe") == nil
          && BCDEpicRules.prerequisiteNote(name: "Game Runtime Setup", path: "redist/setup.exe") == "Epic lists a setup step for this game (Game Runtime Setup). BCD doesn't run it yet.",
          "a setup step is mentioned unless it is Unreal Engine's own")

    // What the page says before Install: the order of the reasons, and that a warning never sits beside a refusal.
    let eosFacts = BCDEpicFacts(buildVersion: "1", installBytes: 10, downloadBytes: 5, eos: true, refusal: nil)
    let acFacts = BCDEpicFacts(buildVersion: "1", installBytes: 10, downloadBytes: 5, eos: true, refusal: .init(code: "anti-cheat", message: "AC"))
    let remembered = BCDEpicRules.Refusal(code: "anti-cheat", message: "REMEMBERED")
    func said(_ a: (blocker: String?, warning: String?), _ blocker: String?, _ warning: String?) -> Bool { a.blocker == blocker && a.warning == warning }
    check(said(BCDEpicRules.advice(item: item(), remembered: nil, facts: nil), nil, nil), "advice: a plain game with no look yet: nothing to say")
    check(said(BCDEpicRules.advice(item: item(), remembered: nil, facts: eosFacts), nil, BCDEpicRules.eosNote(signedStart: true)), "advice: a look that found Epic Online Services: its note, no refusal")
    check(said(BCDEpicRules.advice(item: item { $0.canRunOffline = false }, remembered: nil, facts: eosFacts), nil, BCDEpicRules.onlineNote), "advice: the catalog's word comes before the look's")
    check(said(BCDEpicRules.advice(item: item(), remembered: nil, facts: acFacts), "AC", nil), "advice: a look that found anti-cheat refuses, with no warning beside it")
    check(BCDEpicRules.advice(item: item(), remembered: remembered, facts: acFacts).blocker == "REMEMBERED", "advice: a refusal remembered from a try comes before the look's")
    check(BCDEpicRules.advice(item: item { $0.ownershipToken = true }, remembered: remembered, facts: acFacts, signedStart: false).blocker == BCDEpicRules.catalogRefusal(item { $0.ownershipToken = true }, signedStart: false)?.message
          && BCDEpicRules.advice(item: item { $0.ownershipToken = true; $0.canRunOffline = false }, remembered: nil, facts: eosFacts, signedStart: false).warning == nil
          && BCDEpicRules.advice(item: item { $0.ownershipToken = true; $0.canRunOffline = false }, remembered: nil, facts: eosFacts).warning == BCDEpicRules.tokenNote,
          "advice: with the signed-in start off the catalog's refusal comes first and a refused game gets no warning; with it on the token game gets its note")

    // Ids.
    check(BCDEpicRules.gameID("Fixture") == "epic.Fixture" && BCDEpicRules.claimedApp("epic.Fixture", enabled: true) == "Fixture", "an Epic game's id, and the app it names")
    for id in ["epic.Fixture", "epic.", "epic.a b", "epic..", "steam.620", "gog.123", "local.0F2A5B47-0000-4000-8000-000000000000", "", "Epic.Fixture", "epic", "xepic.Fixture"] {
        check(BCDEpicRules.claimedApp(id, enabled: false) == nil, "with Epic off no id is Epic's: \(id)")
    }
    for id in ["steam.620", "gog.123", "local.0F2A5B47-0000-4000-8000-000000000000", "", "Epic.Fixture", "epic", "epic.a b", "epic.a/b", "epic.\(String(repeating: "a", count: 200))"] {
        check(BCDEpicRules.claimedApp(id, enabled: true) == nil, "with Epic on, another store's id is still not Epic's: \(id.prefix(30))")
    }

    // The language and the arguments.
    check(BCDEpicLaunch.locale(preferred: ["en-US"]) == "en" && BCDEpicLaunch.locale(preferred: ["tr_TR"]) == "tr" && BCDEpicLaunch.locale(preferred: ["zh-Hans-CN"]) == "zh"
          && BCDEpicLaunch.locale(preferred: []) == "en" && BCDEpicLaunch.locale(preferred: ["x"]) == "en" && BCDEpicLaunch.locale(preferred: ["12"]) == "en"
          && BCDEpicLaunch.locale(preferred: ["fil-PH"]) == "fil", "the language Epic's launcher passes")
    let deployment = "0123456789abcdef0123456789abcdef"
    let full = BCDEpicLaunch.arguments(app: "Fixture", manifestCommand: "-nosplash", extraCommand: "-extra=1", deploymentID: deployment, preferredLanguages: ["tr-TR"])
    check(full.text == "-nosplash -extra=1 -epicapp=Fixture -epicenv=Prod -EpicPortal -epiclocale=tr -epicdeploymentid=\(deployment)" && full.dropped == 0,
          "the arguments: the game's own, then Epic's public ones (app, environment, portal, language, deployment)")
    let lean = BCDEpicLaunch.arguments(app: "Fixture", manifestCommand: "", extraCommand: "", deploymentID: nil, preferredLanguages: [])
    check(lean.text == "-epicapp=Fixture -epicenv=Prod -EpicPortal -epiclocale=en", "...with no game arguments and no deployment ID: Epic's four")
    check(!full.text.lowercased().contains("auth") && !full.text.contains("-epicuserid") && !full.text.contains("-epicusername") && !full.text.contains("-epicovt")
          && !full.text.contains("exchange"), "no sign-in, account or token argument is ever passed in this version")
    check(BCDEpicLaunch.arguments(app: "Fixture", manifestCommand: "", extraCommand: "", deploymentID: "not-an-id", preferredLanguages: ["en"]).text == lean.text, "a deployment ID that is not 32 hexadecimal digits is not passed")
    let unsafe = BCDEpicLaunch.arguments(app: "Fixture", manifestCommand: "-a \"unclosed", extraCommand: "-b\n-c", deploymentID: nil, preferredLanguages: ["en"])
    check(unsafe.text == lean.text && unsafe.dropped == 2, "game arguments with an open quote or a control character are dropped, and counted")
    let quoted = BCDEpicLaunch.arguments(app: "Fixture", manifestCommand: "-name=\"Two Words\"", extraCommand: "", deploymentID: nil, preferredLanguages: ["en"])
    check(quoted.text.hasPrefix("-name=\"Two Words\" -epicapp=Fixture") && quoted.dropped == 0, "...closed quotes are kept")
    let many = BCDEpicLaunch.arguments(app: "Fixture", manifestCommand: (0..<40).map { "-a\($0)" }.joined(separator: " "), extraCommand: (0..<30).map { "-b\($0)" }.joined(separator: " "),
                                       deploymentID: deployment, preferredLanguages: ["en"])
    check(many.text == "-epicapp=Fixture -epicenv=Prod -EpicPortal -epiclocale=en -epicdeploymentid=\(deployment)" && many.dropped == 2,
          "game arguments that are too many (the library takes 64) give way to Epic's, and are counted")
    let huge = BCDEpicLaunch.arguments(app: "Fixture", manifestCommand: String(repeating: "x", count: 1400), extraCommand: String(repeating: "y", count: 1400) + " " + String(repeating: "z", count: 100),
                                       deploymentID: deployment, preferredLanguages: ["en"])
    check(huge.text.utf8.count < 3500 && huge.text.hasSuffix("-epicdeploymentid=\(deployment)") && huge.text.contains("-epicapp=Fixture"), "...and so are game arguments that are too long")
    check(BCDEpicLaunch.clean("  -a -b  ") == "-a -b" && BCDEpicLaunch.clean("   ") == "" && BCDEpicLaunch.clean("a\0b") == nil && BCDEpicLaunch.clean(String(repeating: "a", count: 1501)) == nil,
          "an argument string is trimmed, empty ones vanish, odd ones are dropped")
    check(BCDEpicLaunch.validDeploymentID(deployment) && BCDEpicLaunch.validDeploymentID(deployment.uppercased()) && !BCDEpicLaunch.validDeploymentID(String(deployment.dropLast()))
          && !BCDEpicLaunch.validDeploymentID(deployment.replacingOccurrences(of: "a", with: "g")), "a deployment ID is 32 hexadecimal digits")
    check(BCDEpicRules.sanitized("Ubi<soft>\n & Co") == "Ubisoft & Co" && BCDEpicRules.sanitized(String(repeating: "x", count: 100)).count == 40, "text from a server is cleaned for a message")
}

// MARK: Phase 5: the refusals' memory

func refusalsPhase() throws {
    var store = BCDEpicRefusals()
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let refusal = BCDEpicRules.Refusal(code: "anti-cheat", message: "This game comes with BattlEye anti-cheat, which can't run in BCD.")
    store.record(refusal, app: "Fixture", now: now, build: "583")
    check(store.refusal("Fixture", now: now.addingTimeInterval(3600), build: "583") == refusal && store.refusal("Other", now: now, build: "583") == nil, "a refused game is remembered, and only that game")
    check(store.refusal("Fixture", now: now.addingTimeInterval(3 * 86_400 + 1), build: "583") == nil && store.refusal("Fixture", now: now.addingTimeInterval(3 * 86_400 - 5), build: "583") == refusal,
          "...for three days: Epic may have changed the build")
    check(store.refusal("Fixture", now: now, build: "584") == nil && store.refusal("Fixture", now: now, build: "") == nil, "...and only by the build of BCD that noted it: a new version tries again")
    let url = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("epic-refusals-\(getpid())/refusals.json")
    store.save(url)
    check(BCDEpicRefusals.load(url).refusal("Fixture", now: now, build: "583") == refusal, "...across a restart")
    store.forget("Fixture")
    check(store.refusal("Fixture", now: now, build: "583") == nil, "...and forgotten when the game installs")
    try? "not json".write(to: url, atomically: true, encoding: .utf8)
    check(BCDEpicRefusals.load(url).entries.isEmpty && BCDEpicRefusals.load(url.appendingPathExtension("none")).entries.isEmpty, "a damaged or missing file is an empty memory")
    try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
}

// MARK: Phase 6: the plan

func planPhase() async {
    BCDEpicContent.secureOnly = false
    let key = BCDContentKey(store: "epic", id: "Fixture")
    func loaded(_ variant: String, build: String = "main", edit: (inout BCDEpicBuild) -> Void = { _ in }) throws -> BCDEpicContent.Loaded {
        let data = file(fixture.variants[variant]!)
        var b = BCDEpicBuild(buildVersion: "1.2.3-fixture", manifestHash: nil, manifestURLs: [], bases: [URL(string: "https://one.example/p")!], deploymentID: "0123456789abcdef0123456789abcdef", revision: 4)
        edit(&b)
        return BCDEpicContent.Loaded(build: b, bases: b.bases, file: data, hash: BCDHasher.hex(.sha1, of: data), manifest: try BCDEpicManifest.parse(data))
    }
    do {
        let l = try loaded("main")
        let made = try await BCDEpicContent.plan(key, title: "Fixture Game", folder: "FixtureGame", loaded: l, item: item { $0.extraArguments = "-extra=1" })
        let plan = made.plan, info = made.info
        let build = fixture.builds["main"]!
        check(plan.files.count == 10 && plan.chunks.count == 8 && plan.key == key && plan.folder == "FixtureGame" && plan.version == "1.2.3-fixture",
              "the plan: 10 files (the symlink left out), 8 chunks, the folder and the build")
        check(plan.chunks.allSatisfy { $0.codec == .epic && $0.group == "epic" && $0.fetchDigest == nil && $0.digest?.kind == .sha1 } && plan.chunks.map(\.locator).sorted() == build.locators.sorted(),
              "...every chunk: the Epic codec, its SHA-1, the path the CDN serves it at")
        check(plan.files.allSatisfy { $0.digest?.kind == .sha1 || $0.size == 0 } && plan.files.contains { $0.path == "Game/Content/Extra/b.bin" } && !plan.files.contains { $0.path.hasPrefix("game/") },
              "...every file with its SHA-1, folders spelled once (Game/Content, not game/content)")
        check(plan.downloadBytes == build.wireSizes.reduce(0, +) && plan.totalBytes == build.expectedTotal, "...download and install sizes")
        check(plan.files.first(where: { $0.path == "Game/Content/zero-part.bin" })?.size == 10 && plan.files.first(where: { $0.path == "Game/Saved/empty.txt" })?.size == 0, "...a part of no bytes is left out; an empty file stays")
        check((try? plan.validate()) != nil, "...and passes the engine's own validation")
        check(info.launchPath == "Game/Binaries/Win64/Game.exe" && info.launchCommand == "-nosplash" && info.extraCommand == "-extra=1" && info.eos && info.deploymentID == "0123456789abcdef0123456789abcdef"
              && info.manifestHash == l.hash && info.featureLevel == 17 && info.symlinksSkipped == 1 && info.canRunOffline == true && info.prerequisiteNote == nil && info.revision == 4,
              "the facts kept for the record: program (spelled as the plan spells it), commands, EOS, deployment, hash, level")
        // A new build is a new plan; the same build the same one.
        let again = try await BCDEpicContent.plan(key, title: "Fixture Game", folder: "FixtureGame", loaded: l, item: item())
        check(again.plan.identity == plan.identity, "the same build gives the same plan (a resume continues it)")
        var newer = l
        newer.build.buildVersion = "1.2.4"
        check(try await BCDEpicContent.plan(key, title: "Fixture Game", folder: "FixtureGame", loaded: newer, item: item()).plan.identity != plan.identity, "...a new build version gives another (nothing staged is kept)")
        // The program need not be spelled as the manifest says it.
        var lower = l
        lower.manifest.launchExe = "game\\BINARIES\\win64\\GAME.EXE"
        check(try await BCDEpicContent.plan(key, title: "T", folder: "F", loaded: lower, item: item()).info.launchPath == "Game/Binaries/Win64/Game.exe", "the launch program is found whatever its letter case")
        var other = l
        other.manifest.prereqName = "Game Runtime Setup"
        other.manifest.prereqPath = "redist/setup.exe"
        check(try await BCDEpicContent.plan(key, title: "T", folder: "F", loaded: other, item: item()).info.prerequisiteNote?.contains("Game Runtime Setup") == true, "a setup step the manifest lists is kept as a note")
    } catch { check(false, "the plan: \(error)") }

    // What the game's page learns from a look at the manifest, before any chunk is fetched.
    do {
        let facts = BCDEpicContent.facts(try loaded("main"))
        check(facts.buildVersion == "1.2.3-fixture" && facts.installBytes == fixture.builds["main"]!.expectedTotal && facts.downloadBytes == fixture.builds["main"]!.wireSizes.reduce(0, +)
              && facts.eos && facts.refusal == nil, "the look at a manifest: the install and download sizes, Epic Online Services, no refusal")
        let acFacts = BCDEpicContent.facts(try loaded("ac")), noExeFacts = BCDEpicContent.facts(try loaded("noexe"))
        check(acFacts.refusal?.code == "anti-cheat" && noExeFacts.refusal?.code == "no-program",
              "...a build with anti-cheat, or with no program, is refused before it is downloaded")
        let newerFacts = BCDEpicContent.facts(from: BCDEpicManifestError.newer(22)), encryptedFacts = BCDEpicContent.facts(from: BCDEpicManifestError.encrypted)
        check(newerFacts?.refusal?.code == "manifest-level-22" && encryptedFacts?.refusal?.code == "manifest-encrypted" && newerFacts?.installBytes == 0
              && BCDEpicContent.facts(from: BCDEpicManifestError.badHash) == nil && BCDEpicContent.facts(from: URLError(.timedOut)) == nil,
              "a look at a manifest BCD will not read (newer, encrypted) refuses before the download; any other failure says nothing")
        check(BCDEpicPreflight(facts: facts, at: Date(timeIntervalSince1970: 0)) == BCDEpicPreflight(facts: facts, at: Date(timeIntervalSince1970: 0)), "(a look is a plain value)")
    } catch { check(false, "the look at a manifest: \(error)") }

    func refused(_ variant: String, edit: (inout BCDEpicContent.Loaded) -> Void = { _ in }) async -> String? {
        do {
            var l = try loaded(variant)
            edit(&l)
            _ = try await BCDEpicContent.plan(key, title: "T", folder: "F", loaded: l, item: item())
            return nil
        } catch let r as BCDEpicRefused { return r.refusal.code } catch { return "error \(error)" }
    }
    check(await refused("ac") == "anti-cheat", "a build with Easy Anti-Cheat in it is refused")
    check(await refused("noexe") == "no-program", "a build that names no program is refused")
    check(await refused("main", edit: { $0.manifest.launchExe = "Game\\Missing.exe" }) == "no-program", "...and one whose program is not among its files")
    check(BCDEpicRefused(.init(code: "anti-cheat", message: "m")).logReason == "refused-anti-cheat" && BCDEpicRefused(.init(code: "x", message: "m")).errorDescription == "m", "the refusal carries its message and a log reason")
    // Hostile names: a path out of the folder stops the plan.
    do {
        var l = try loaded("main")
        l.manifest.files[1].name = "../escape.dll"
        _ = try await BCDEpicContent.plan(key, title: "T", folder: "F", loaded: l, item: item())
        check(false, "a file outside the folder was planned")
    } catch { check((error as? BCDContentError) == .unsafePath, "a file named outside the game's folder stops the plan (\(error))") }
}

// MARK: Phase 7: the install over a local CDN

actor Collector {
    var values: [BCDContentProgress] = []
    var count = 0
    func add(_ p: BCDContentProgress) { values.append(p) }
    func tick() { count += 1 }
}

/// A chunk source that serves only the first `limit` distinct chunks, then has no address (a connection that
/// stays down): the engine stops, with what finished journaled.
actor LimitedSource: BCDChunkSource {
    let inner: BCDChunkSource
    let limit: Int
    private var served = Set<String>()
    init(_ inner: BCDChunkSource, limit: Int) { self.inner = inner; self.limit = limit }
    func urls(for chunk: BCDContentChunk) async throws -> [URL] {
        let allowed = served.contains(chunk.locator) || served.count < limit
        if allowed { served.insert(chunk.locator) }
        return allowed ? try await inner.urls(for: chunk) : []
    }
    func refresh(group: String) async throws { try await inner.refresh(group: group) }
}

func cdnLog() async -> [String] {
    let stream = BCDHTTPStream(timeout: 10)
    defer { stream.close() }
    guard let data = try? await stream.data(URLRequest(url: URL(string: fixture.cdn + "/_log")!), limit: 4 << 20) else { return [] }
    return String(decoding: data, as: UTF8.self).split(separator: "\n").map(String.init)
}

func cdnReset() async {
    let stream = BCDHTTPStream(timeout: 10)
    defer { stream.close() }
    _ = try? await stream.data(URLRequest(url: URL(string: fixture.cdn + "/_reset")!), limit: 1024)
}

func chunkRequests(_ log: [String], label: String? = nil) -> [String] {
    log.filter { $0.contains(".chunk") && (label == nil || $0.hasPrefix(label! + " ")) }
}

func tree(_ root: URL) -> [String: Data] {
    var out: [String: Data] = [:]
    let base = root.resolvingSymlinksInPath()
    guard let walker = FileManager.default.enumerator(at: base, includingPropertiesForKeys: [.isRegularFileKey]) else { return out }
    for case let url as URL in walker {
        guard (try? url.resourceValues(forKeys: [.isRegularFileKey]))?.isRegularFile == true else { continue }
        out[String(url.resolvingSymlinksInPath().path.dropFirst(base.path.count + 1))] = FileManager.default.contents(atPath: url.path)
    }
    return out
}

func difference(_ got: [String: Data], _ want: [String: Data]) -> String {
    let missing = want.keys.filter { got[$0] == nil }.sorted(), extra = got.keys.filter { want[$0] == nil }.sorted()
    let wrong = want.keys.filter { got[$0] != nil && got[$0] != want[$0] }.sorted()
    return "missing \(missing.prefix(3)) extra \(extra.prefix(3)) wrong \(wrong.prefix(3))"
}

func expectedTree(_ name: String) -> [String: Data] {
    let info = fixture.builds[name]!
    return Dictionary(uniqueKeysWithValues: info.paths.map { ($0, file(info.expected + "/" + $0)) })
}

func installPhase() async {
    BCDEpicContent.secureOnly = false
    let fm = FileManager.default
    let work = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("epic-install-\(getpid())", isDirectory: true)
    try? fm.removeItem(at: work)
    try? fm.createDirectory(at: work, withIntermediateDirectories: true)
    defer { try? fm.removeItem(at: work) }
    let drive = work.appendingPathComponent("drive_c", isDirectory: true)
    try? fm.createDirectory(at: drive, withIntermediateDirectories: true)
    BCDInstallRoot.hostDrive = { $0 == "C" ? drive : nil }
    let root = BCDEpicInstall.root
    check(root.drive == "C" && root.folder == "Program Files/Epic Games" && root.windowsPath("Limbo", "Limbo.exe") == "C:\\Program Files\\Epic Games\\Limbo\\Limbo.exe"
          && root.url == drive.appendingPathComponent("Program Files/Epic Games", isDirectory: true), "the install root: Epic's own default, C:\\Program Files\\Epic Games, kept in one place")
    let token = "TOKEN-SECRET-123"
    let key = BCDContentKey(store: "epic", id: "Fixture")
    let catalogID = "0123456789abcdef0123456789abcdef"

    func asset(_ buildName: String, labels: [String], hash: Bool = true) -> [String: Any] {
        let b = fixture.builds[buildName]!
        let manifests = labels.map { label -> [String: Any] in
            // A label picks the behaviour, and its own host: dead (nothing listens), corrupt* and good2* on the others, the rest on the first.
            let host = label.hasPrefix("dead") ? fixture.dead : label.hasPrefix("corrupt") ? fixture.cdn2 : label.hasPrefix("good2") ? fixture.cdn3 : fixture.cdn
            return ["uri": "\(host)/\(label)\(b.prefix)/\(b.manifest)", "queryParams": [["name": "f_token", "value": token]]]
        }
        var e: [String: Any] = ["appName": "Fixture", "buildVersion": "1.2.3-fixture", "manifests": manifests,
                                "sidecar": ["config": "{\"deploymentId\":\"\(catalogID)\"}", "rvn": 4]]
        if hash { e["hash"] = b.sha1 }
        return ["elements": [e]]
    }
    func load(_ buildName: String, labels: [String], hash: Bool = true, preferring: String? = nil) async throws -> BCDEpicContent.Loaded {
        let object = asset(buildName, labels: labels, hash: hash)
        return try await BCDEpicContent.load(app: "Fixture", namespace: "nsfix", catalogItem: catalogID, preferring: preferring,
                                             fetchAsset: { _ in object },
                                             fetchFile: { url in
                                                 let stream = BCDHTTPStream(timeout: 10)
                                                 defer { stream.close() }
                                                 return try await stream.data(URLRequest(url: url), limit: 64 << 20)
                                             })
    }
    func options(streams: Int = 4) -> BCDContentOptions {
        var o = BCDContentOptions()
        o.streams = streams
        o.backoff = 0
        o.verifyFiles = true
        return o
    }
    func source(_ l: BCDEpicContent.Loaded) -> BCDEpicChunkSource { BCDEpicChunkSource(bases: l.bases, renew: { l.bases }) }

    // 1. A dead CDN, a corrupting one and a good one: the manifest comes from the first that answers, the chunks from the good one.
    do {
        await cdnReset()
        let l = try await load("main", labels: ["dead", "corrupt", "good"])
        check(l.bases.count == 3 && l.bases[0].port == URL(string: fixture.cdn2)?.port && l.bases[0].path.hasPrefix("/corrupt") && l.bases[1].port == 1,
              "the manifest came from the first CDN that answered; its folder is first, the others keep their order")
        check(l.build.deploymentID == catalogID && l.hash == fixture.builds["main"]!.sha1 && l.manifest.files.count == 11, "...checked against the pointer's SHA-1")
        let made = try await BCDEpicContent.plan(key, title: "Fixture Game", folder: "FixtureGame", loaded: l, item: item())
        let stage = root.stagingURL!.appendingPathComponent(key.folderName, isDirectory: true)
        let job = work.appendingPathComponent("job-main", isDirectory: true)
        let progress = Collector()
        try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: job, source: source(l), options: options()) { p in Task { await progress.add(p) } }
        try? await Task.sleep(nanoseconds: 300_000_000)
        let got = tree(stage)
        let want = expectedTree("main")
        check(got == want, "the install: every file is exactly what the build holds (\(got.count) files)" + (got == want ? "" : " " + difference(got, want)))
        let log = await cdnLog()
        let corrupt = chunkRequests(log, label: "corrupt"), good = chunkRequests(log, label: "good")
        check(!corrupt.isEmpty && !good.isEmpty, "...a chunk the corrupting CDN spoiled was fetched again from another (corrupt asked \(corrupt.count) times, good \(good.count))")
        let values = await progress.values
        check(values.contains { $0.phase == .downloading } && values.contains { $0.phase == .verifying } && values.allSatisfy { $0.doneBytes <= $0.totalBytes && $0.totalBytes == made.plan.downloadBytes }
              && (values.filter { $0.phase == .downloading }.map(\.doneBytes).last ?? 0) == made.plan.downloadBytes, "...progress runs to the download size and never past it")

        // 2. Nothing is fetched again when everything is journaled and the files are right.
        await cdnReset()
        try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: job, source: source(l), options: options()) { _ in }
        check(chunkRequests(await cdnLog()).isEmpty && tree(stage) == want, "a run over a finished job fetches nothing")

        // 3. A file damaged on the disk is found by the check and fetched again (the repair round), nothing else.
        let victim = stage.appendingPathComponent("Game/Content/Paks/data.pak")
        var data = fm.contents(atPath: victim.path)!
        data[1234] ^= 0xFF
        fm.createFile(atPath: victim.path, contents: data)
        check(try BCDContentEngine.verify(made.plan, folder: stage).count == 1, "the check finds a file with one byte wrong")
        await cdnReset()
        try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: job, source: source(l), options: options()) { _ in }
        let fetched = Set(chunkRequests(await cdnLog()).map { String($0.split(separator: " ")[1]) })
        check(tree(stage) == want && fetched.count == 3, "...a run repairs it from the chunks of that file only (\(fetched.count) chunks)")
        check(try BCDContentEngine.verify(made.plan, folder: stage).isEmpty, "...and the files check clean")

        // 4. The install: one rename into the root, the record, the kept manifest, the program.
        let base = work.appendingPathComponent("EpicState", isDirectory: true)
        let pendingInfo = BCDEpicInstall.pendingInfoFile("Fixture", base: base)
        try? fm.createDirectory(at: pendingInfo.deletingLastPathComponent(), withIntermediateDirectories: true)
        try l.file.write(to: BCDEpicInstall.pendingManifestFile("Fixture", base: base))
        let record = try BCDEpicInstall.finish(made.plan, info: made.info, pendingManifest: BCDEpicInstall.pendingManifestFile("Fixture", base: base), stage: stage, root: root, base: base, now: Date(timeIntervalSince1970: 1_800_000_000))
        let installed = root.url!.appendingPathComponent("FixtureGame")
        check(record.folder == "FixtureGame" && record.windowsFolder == "C:\\Program Files\\Epic Games\\FixtureGame" && tree(installed) == want && !fm.fileExists(atPath: stage.path),
              "the install is moved into place: C:\\Program Files\\Epic Games\\FixtureGame")
        check(fm.contents(atPath: BCDEpicInstall.manifestFile("Fixture", base: base).path) == l.file && !fm.fileExists(atPath: BCDEpicInstall.pendingManifestFile("Fixture", base: base).path),
              "the manifest is kept beside the record (for the checks and updates of later versions)")
        check(record.launchPath == "Game/Binaries/Win64/Game.exe" && record.bytes == made.plan.totalBytes && record.eos && record.buildVersion == "1.2.3-fixture" && record.manifestHash == l.hash,
              "the record: program, size, EOS, build, manifest hash")
        let launch = BCDEpicInstall.launch(record, preferredLanguages: ["en-GB"])
        check(launch.relativePath == "Program Files/Epic Games/FixtureGame/Game/Binaries/Win64/Game.exe"
              && launch.arguments == "-nosplash -epicapp=Fixture -epicenv=Prod -EpicPortal -epiclocale=en -epicdeploymentid=\(catalogID)" && record.arguments == BCDEpicInstall.launch(record).arguments,
              "the library entry's program (relative to drive_c) and its public arguments")
        check(record.note() == BCDEpicRules.eosNote(signedStart: true), "the page's note for a game with Epic Online Services")
        // Records on disk.
        try BCDEpicInstall.save(record, base: base)
        check(BCDEpicInstall.loadRecords(base) == ["Fixture": record], "the record is read back as it was")
        try? "{}".write(to: BCDEpicInstall.recordsFolder(base).appendingPathComponent("Other.json"), atomically: true, encoding: .utf8)
        try? "x".write(to: BCDEpicInstall.recordsFolder(base).appendingPathComponent("Fixture2.json"), atomically: true, encoding: .utf8)
        check(BCDEpicInstall.loadRecords(base).keys.sorted() == ["Fixture"], "damaged records, and records under another name, are left out")
        // A second copy of the same game gets another folder; an existing folder is never replaced.
        check(BCDEpicInstall.folderName(preferred: "FixtureGame", in: root.url!) == "FixtureGame (Epic)", "a folder already there: the game gets \"(Epic)\" after its name")
        try? fm.createDirectory(at: root.url!.appendingPathComponent("FixtureGame (Epic)"), withIntermediateDirectories: true)
        check(BCDEpicInstall.folderName(preferred: "FixtureGame", in: root.url!) == "FixtureGame (Epic 2)" && BCDEpicInstall.folderName(preferred: "a/b:c", in: root.url!) == "a_b_c"
              && BCDEpicInstall.folderName(preferred: "..", in: root.url!) == "Epic game", "...and a hostile or empty name is made safe")
        // Uninstall: the folder goes out of sight at once; the record and the kept manifest go.
        let trash = root.stagingURL!.appendingPathComponent("trash", isDirectory: true)
        await BCDContentEngine.remove(installed, trash: trash)
        BCDEpicInstall.delete("Fixture", base: base)
        check(!fm.fileExists(atPath: installed.path) && BCDEpicInstall.loadRecords(base).isEmpty && !fm.fileExists(atPath: BCDEpicInstall.manifestFile("Fixture", base: base).path)
              && (try? fm.contentsOfDirectory(atPath: trash.path))?.isEmpty != false, "uninstall: the files, the record and the kept manifest are gone")
    } catch { check(false, "the install: \(error)") }

    // 5. Resume: the first run stops after three chunks; the second fetches only the rest.
    do {
        await cdnReset()
        let l = try await load("main", labels: ["good"])
        let made = try await BCDEpicContent.plan(key, title: "T", folder: "Resume", loaded: l, item: item())
        let stage = root.stagingURL!.appendingPathComponent("epic-resume", isDirectory: true)
        let job = work.appendingPathComponent("job-resume", isDirectory: true)
        do {
            try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: job, source: LimitedSource(source(l), limit: 3), options: options(streams: 1)) { _ in }
            check(false, "the stopped run finished")
        } catch { check((error as? BCDContentError) == .chunkFailed("no-address"), "a run whose connection stays down stops (\(error))") }
        let journal = BCDContentJournal.load(job.appendingPathComponent("journal"), identity: made.plan.identity, chunkCount: made.plan.chunks.count)
        check(journal.count == 3, "...with the three finished chunks journaled")
        await cdnReset()
        try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: job, source: source(l), options: options(streams: 1)) { _ in }
        let log = chunkRequests(await cdnLog())
        let asked = Set(log.map { String($0.split(separator: " ")[1]) })
        check(tree(stage) == expectedTree("main") && asked.count == made.plan.chunks.count - 3 && log.count == asked.count,
              "the resume fetches only the \(made.plan.chunks.count - 3) chunks that were missing, each once")
        // A plan that changed (a new build) starts clean: the old journal does not belong to it.
        var newer = l
        newer.build.buildVersion = "1.2.4"
        let other = try await BCDEpicContent.plan(key, title: "T", folder: "Resume", loaded: newer, item: item())
        check(BCDContentJournal.load(job.appendingPathComponent("journal"), identity: other.plan.identity, chunkCount: other.plan.chunks.count).isEmpty, "a journal belongs to one plan: a new build's plan finds none")
    } catch { check(false, "resume: \(error)") }

    // 6. A chunk file counted differently in the list (without its header, or a hundred bytes too many) still installs, and the meter ends at the total.
    for name in ["body", "plus"] {
        do {
            let b = fixture.builds[name]!
            await cdnReset()
            let l = try await load(name, labels: ["good"])
            let made = try await BCDEpicContent.plan(key, title: "T", folder: "Size-" + name, loaded: l, item: item())
            let stage = root.stagingURL!.appendingPathComponent("epic-" + name, isDirectory: true)
            let progress = Collector()
            try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: work.appendingPathComponent("job-" + name), source: source(l), options: options()) { p in Task { await progress.add(p) } }
            try? await Task.sleep(nanoseconds: 300_000_000)
            let last = await progress.values.filter { $0.phase == .downloading }.last
            check(tree(stage) == expectedTree(name) && made.plan.downloadBytes == b.wireSizes.reduce(0, +), "a list that counts the chunk files \(name == "body" ? "without their header" : "100 bytes long"): the install is right")
            check(last?.doneBytes == made.plan.downloadBytes, "...and the progress ends at the download size (\(last?.doneBytes ?? -1) of \(made.plan.downloadBytes))")
        } catch { check(false, "list sizes \(name): \(error)") }
    }

    // 7. A file whose list checksum is wrong cannot be repaired by fetching again: the install fails, not silently.
    do {
        let l = try await load("badfile", labels: ["good"])
        let made = try await BCDEpicContent.plan(key, title: "T", folder: "Bad", loaded: l, item: item())
        let stage = root.stagingURL!.appendingPathComponent("epic-badfile", isDirectory: true)
        do {
            try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: work.appendingPathComponent("job-badfile"), source: source(l), options: options()) { _ in }
            check(false, "a file with a wrong checksum was accepted")
        } catch { check((error as? BCDContentError) == .checkFailed, "a file that does not match its checksum after a repair round fails the install (\(error))") }
    } catch { check(false, "bad file: \(error)") }

    // 8. An encrypted chunk is refused at once, not retried against every CDN.
    do {
        await cdnReset()
        let l = try await load("encrypted", labels: ["good", "good2"])
        let made = try await BCDEpicContent.plan(key, title: "T", folder: "Enc", loaded: l, item: item())
        let stage = root.stagingURL!.appendingPathComponent("epic-enc", isDirectory: true)
        do {
            try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: work.appendingPathComponent("job-enc"), source: source(l), options: options(streams: 1)) { _ in }
            check(false, "an encrypted chunk was accepted")
        } catch {
            var refused = false
            if case .refused? = error as? BCDContentError { refused = true }
            let asks = chunkRequests(await cdnLog())
            check(refused && Set(asks.map { $0.split(separator: " ")[1] }).count <= made.plan.chunks.count && asks.count <= made.plan.chunks.count, "an encrypted chunk stops the install with a reason, without retries (\(asks.count) requests)")
        }
    } catch { check(false, "encrypted chunk: \(error)") }

    // 9. Free space: checked before anything is fetched.
    do {
        await cdnReset()
        let l = try await load("main", labels: ["good"])
        let made = try await BCDEpicContent.plan(key, title: "T", folder: "Space", loaded: l, item: item())
        var o = options()
        o.freeSpace = { _ in 1000 }
        let stage = root.stagingURL!.appendingPathComponent("epic-space", isDirectory: true)
        do {
            try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: work.appendingPathComponent("job-space"), source: source(l), options: o) { _ in }
            check(false, "a download started without room")
        } catch {
            var short = false
            if case .notEnoughSpace? = error as? BCDContentError { short = true }
            let asked = chunkRequests(await cdnLog())
            check(short && asked.isEmpty, "not enough room: stopped before any chunk is fetched")
        }
    } catch { check(false, "space: \(error)") }

    // 10. Manifest sources: hash mismatch, nothing answering, a CDN that serves another file.
    do {
        _ = try await load("main", labels: ["missing"])
        check(false, "a manifest that is missing everywhere loaded")
    } catch { check((error as? BCDEpicContentError) == .noManifest, "no CDN has the manifest: the player is told to check the connection (\(error))") }
    do {
        let object = asset("main", labels: ["good"], hash: true)
        var e = (object["elements"] as! [[String: Any]])[0]
        e["hash"] = String(repeating: "0", count: 40)
        _ = try await BCDEpicContent.load(app: "Fixture", namespace: "n", catalogItem: catalogID, preferring: nil, fetchAsset: { _ in ["elements": [e]] },
                                          fetchFile: { url in let s = BCDHTTPStream(timeout: 10); defer { s.close() }; return try await s.data(URLRequest(url: url), limit: 1 << 24) })
        check(false, "a manifest that does not match the pointer's SHA-1 loaded")
    } catch { check((error as? BCDEpicContentError) == .manifestHash, "a manifest that does not match the pointer's SHA-1 is not used (\(error))") }
    do {
        let l = try await load("main", labels: ["good"], hash: false)
        check(l.manifest.files.count == 11, "a pointer without a hash still loads (the manifest's own checks apply)")
    } catch { check(false, "no hash: \(error)") }
    do {
        let l = try await load("main", labels: ["good", "good2"], preferring: "127.0.0.1")
        check(l.bases.count == 2, "the preferred CDN names a host part")
    } catch { check(false, "preferring: \(error)") }
    do {
        _ = try await load("newer", labels: ["good"])
        check(false, "a manifest of a newer format loaded")
    } catch { check((error as? BCDEpicManifestError) == .newer(22), "a newer format is refused with its own reason (\(error))") }

    // 11. The chunk source renews its folders when a host refuses.
    do {
        let a = URL(string: "https://one.example/p")!, b = URL(string: "https://two.example/q")!
        let renewed = Collector()
        let source = BCDEpicChunkSource(bases: [a], renew: { await renewed.tick(); return [b, a] })
        let chunk = BCDContentChunk(locator: "ChunksV4/05/DEADBEEF12345678_0123456789ABCDEF0123456789ABCDEF.chunk", group: "epic", fetchSize: 10, size: 10, fetchDigest: nil, digest: nil, codec: .epic)
        let before = try await source.urls(for: chunk).map(\.absoluteString)
        try await source.refresh(group: "epic")
        let after = try await source.urls(for: chunk).map(\.absoluteString)
        let renewals = await renewed.count
        check(before == ["https://one.example/p/" + chunk.locator] && after == ["https://two.example/q/" + chunk.locator, "https://one.example/p/" + chunk.locator]
              && renewals == 1, "chunk addresses: the folders in order, renewed when a host refuses")
        var odd = chunk
        odd.locator = "../../etc/passwd"
        check(try await source.urls(for: odd).isEmpty, "a locator that is not a chunk path gets no address")
    } catch { check(false, "source: \(error)") }
}

// MARK: Phase 8: logs

func logPhase() {
    logLock.lock(); let all = logs; logLock.unlock()
    let joined = all.joined(separator: "\n")
    check(!joined.contains("TOKEN-SECRET-123") && !joined.contains("127.0.0.1") && !joined.contains("http://") && !joined.contains("https://") && !joined.contains(NSTemporaryDirectory()),
          "no CDN token, address or path in any log line of the whole run (\(all.count) lines)")
    check(all.contains { $0.hasPrefix("[epic-cdn] manifest from cdn ") && $0.contains(" level=17 files=11 chunks=8 ") && $0.contains("checked=1") }
          && all.contains { $0.hasPrefix("[epic-install] plan app=Fixture level=17 files=10 chunks=8 download=") && $0.contains("eos=1") }
          && all.contains { $0.hasPrefix("[epic-install] installed app=Fixture files=10 bytes=") }, "the install's lines: [epic-cdn] the manifest, [epic-install] the plan and the install, with counts only")
    check(all.allSatisfy { $0.hasPrefix("[epic-") || $0.hasPrefix("[content]") }, "every line the run logged is an [epic-*] or [content] line")
    check(BCDEpicLog.app("Fixture") == "Fixture" && BCDEpicLog.app("0123456789abcdef0123456789abcdef") == "01..ef" && BCDEpicLog.app("a b") == ".." && BCDEpicLog.app("") == "..",
          "a game's app name is logged as a word, and masked when it is a 32-digit identifier")
}

@main struct Harness {
    static func main() async {
        installSink()
        chunkPhase()
        await manifestPhase()
        assetPhase()
        rulesPhase()
        try? refusalsPhase()
        await planPhase()
        await installPhase()
        logPhase()
        print(failures == 0 ? "EPIC-INSTALL-HARNESS: all passed" : "EPIC-INSTALL-HARNESS: \(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
