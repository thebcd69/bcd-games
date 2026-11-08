// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Host test for GOG's DLC, update and repair in BCD (tests/host/check-gog-update.py). Synthetic data only; GOG is never contacted.
import CryptoKit
import Foundation

var failures = 0
func check(_ ok: Bool, _ label: String) { print((ok ? "PASS: " : "FAIL: ") + label); if !ok { failures += 1 } }

func md5(_ data: Data) -> String { Insecure.MD5.hash(data: data).map { String(format: "%02x", $0) }.joined() }
func hash(_ c: Character) -> String { String(repeating: c, count: 32) }

/// A chunk of a manifest for the bytes `data`: its MD5 as GOG states it, and a made-up compressed MD5 (the plan never reads the bytes).
func chunk(_ data: Data, packed n: Int) -> [String: Any] {
    ["md5": md5(data), "size": data.count, "compressedMd5": String(format: "%032x", n), "compressedSize": max(1, data.count / 2)]
}
func file(_ path: String, _ pieces: [Data], packed: Int, extra: [String: Any] = [:]) -> [String: Any] {
    var item: [String: Any] = ["type": "DepotFile", "path": path, "chunks": pieces.enumerated().map { chunk($1, packed: packed + $0) }]
    for (k, v) in extra { item[k] = v }
    return item
}
func depot(_ items: [[String: Any]]) -> [String: Any] { ["depot": ["items": items], "version": 2] }
func bytes(_ text: String) -> Data { Data(text.utf8) }

// ---- identity and the files' reading ----------------------------------------------------------
let a1 = bytes("hello "), a2 = bytes("world")
check(BCDGOGUpdate.identity(ofItem: file("a", [a1, a2], packed: 100)) == "chunks:\(md5(a1)),\(md5(a2))", "identity: the MD5s of the chunks in order")
check(BCDGOGUpdate.identity(ofItem: file("a", [a1], packed: 100, extra: ["md5": "ABCDEF"])) == "md5:abcdef", "identity: the whole-file digest first")
check(BCDGOGUpdate.identity(ofItem: ["type": "DepotFile", "path": "a", "chunks": [], "sfcRef": ["offset": 0, "size": 3]]) == nil, "identity: nothing for a file only in the container")
check(BCDGOGUpdate.identity(ofItem: ["type": "DepotFile", "path": "a", "chunks": [["size": 1]]]) == nil, "identity: nothing for a chunk with no MD5")
let ids = BCDGOGUpdate.identities(ofDepot: depot([file("Bin\\Game.exe", [a1], packed: 1), file("setup.exe", [a1], packed: 2, extra: ["flags": ["support"]]),
                                                    ["type": "DepotDirectory", "path": "x"]]))
check(ids.count == 1 && ids["bin/game.exe"] == .some("chunks:\(md5(a1))"), "identities: folded paths, support files and folders left out")

let fm = FileManager.default
let work = fm.temporaryDirectory.appendingPathComponent("gog-update-bcd-\(getpid())")
try? fm.createDirectory(at: work, withIntermediateDirectories: true)
defer { try? fm.removeItem(at: work) }
let folder = work.appendingPathComponent("Game")
try? fm.createDirectory(at: folder.appendingPathComponent("Data"), withIntermediateDirectories: true)
try! (a1 + a2).write(to: folder.appendingPathComponent("Data/Pak.bin"))
func facts(_ path: String, _ pieces: [Data], digest: BCDDigest? = nil, chunked: Bool = true) -> BCDGOGFileFacts {
    BCDGOGFileFacts(path: path, size: Int64(pieces.reduce(0) { $0 + $1.count }), digest: digest,
                    chunks: chunked ? pieces.map { BCDContentChunk(locator: md5($0), group: "1", fetchSize: 1, size: Int64($0.count), fetchDigest: nil, digest: BCDDigest(.md5, md5($0)), codec: .zlib) } : [],
                    identity: nil)
}
let real = folder.appendingPathComponent("Data/Pak.bin")
var readBytes: Int64 = 0
check(BCDGOGUpdate.matches(real, facts("Data/Pak.bin", [a1, a2])) { readBytes += $0 } == true && readBytes == 11, "matches: every chunk's MD5 agrees (the file is read once: \(readBytes) bytes)")
check(!BCDGOGUpdate.matches(real, facts("Data/Pak.bin", [a1, bytes("worlD")])), "matches: a changed byte is found")
check(!BCDGOGUpdate.matches(real, facts("Data/Pak.bin", [a1])), "matches: another size")
check(!BCDGOGUpdate.matches(folder.appendingPathComponent("Data/none.bin"), facts("Data/none.bin", [a1])), "matches: a missing file")
check(BCDGOGUpdate.matches(real, facts("Data/Pak.bin", [a1, a2], digest: BCDDigest(.md5, md5(a1 + a2)), chunked: false)), "matches: the whole-file digest when there are no chunk MD5s")
check(!BCDGOGUpdate.matches(real, facts("Data/Pak.bin", [a1, a2], digest: BCDDigest(.md5, hash("0")), chunked: false)), "matches: a wrong whole-file digest")
check(!BCDGOGUpdate.matches(real, facts("Data/Pak.bin", [a1, a2], chunked: false)), "matches: nothing to compare with: the file is downloaded again")
try? fm.createSymbolicLink(at: folder.appendingPathComponent("link.bin"), withDestinationURL: real)
check(!BCDGOGUpdate.matches(folder.appendingPathComponent("link.bin"), facts("link.bin", [a1, a2])) && BCDGOGUpdate.existing("link.bin", in: folder) == nil, "a symbolic link is not a file of the game")
check(BCDGOGUpdate.existing("data/PAK.BIN", in: folder)?.lastPathComponent == "Pak.bin" && BCDGOGUpdate.existing("Data", in: folder) == nil
      && BCDGOGUpdate.existing("Data/Missing.bin", in: folder) == nil, "existing: letter case is free; a folder or a missing file is nil")

// ---- merging ---------------------------------------------------------------------------------------
do {
    var builder = try BCDContentPlanBuilder(key: .init(store: "gog", id: "1"), title: "T", version: "2", folder: "Game")
    for (path, data) in [("data/pak.bin", bytes("NEWPAK")), ("New/Deep/x.dat", bytes("xyz")), ("Root.txt", bytes("r"))] {
        let i = try builder.addFile(path, size: Int64(data.count), digest: nil)
        let c = try builder.addChunk(BCDContentChunk(locator: md5(data), group: "1", fetchSize: 1, size: Int64(data.count), fetchDigest: nil, digest: nil, codec: .zlib))
        try builder.addPart(chunk: c, chunkOffset: 0, length: Int64(data.count), file: i, fileOffset: 0)
    }
    let plan = try builder.build()
    let stage = work.appendingPathComponent("stage")
    for file in plan.files {
        let url = stage.appendingPathComponent(file.path)
        try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(repeating: 0x41, count: Int(file.size)).write(to: url)
    }
    try bytes("old").write(to: folder.appendingPathComponent("Gone.txt"))
    try fm.createDirectory(at: folder.appendingPathComponent("OLD"), withIntermediateDirectories: true)
    try bytes("old").write(to: folder.appendingPathComponent("OLD/Dead.dll"))
    let merged = try BCDGOGUpdate.merge(plan, stage: stage, into: folder, remove: ["gone.txt", "old/dead.dll", "not/there.txt"])
    check(merged == BCDGOGUpdate.MergeResult(replaced: 1, added: 2, removed: 2), "merge: one file replaced, two added, two removed (a file that is not there is no error): \(merged)")
    check((try? fm.contentsOfDirectory(atPath: folder.path))?.contains("Data") == true && (try? fm.contentsOfDirectory(atPath: folder.appendingPathComponent("Data").path)) == ["Pak.bin"]
          && (try? Data(contentsOf: folder.appendingPathComponent("Data/Pak.bin")))?.count == 6, "merge: the device's spelling of a folder and file that exist is kept (a listing shows it: this Mac's disk ignores letter case), the new bytes are in it")
    check(fm.fileExists(atPath: folder.appendingPathComponent("New/Deep/x.dat").path) && fm.fileExists(atPath: folder.appendingPathComponent("Root.txt").path)
          && !fm.fileExists(atPath: folder.appendingPathComponent("Gone.txt").path) && !fm.fileExists(atPath: folder.appendingPathComponent("OLD/Dead.dll").path),
          "merge: new folders made, removed files gone")
    check(!fm.fileExists(atPath: stage.appendingPathComponent("Root.txt").path), "merge: the staged files moved (a rename each)")
    // a file where a folder is
    try bytes("f").write(to: folder.appendingPathComponent("Blocker"))
    var clash = try BCDContentPlanBuilder(key: .init(store: "gog", id: "1"), title: "T", version: "3", folder: "Game")
    let i = try clash.addFile("Blocker/inside.txt", size: 1, digest: nil)
    let c = try clash.addChunk(BCDContentChunk(locator: md5(bytes("z")), group: "1", fetchSize: 1, size: 1, fetchDigest: nil, digest: nil, codec: .zlib))
    try clash.addPart(chunk: c, chunkOffset: 0, length: 1, file: i, fileOffset: 0)
    let clashPlan = try clash.build()
    let stage2 = work.appendingPathComponent("stage2")
    try fm.createDirectory(at: stage2.appendingPathComponent("Blocker"), withIntermediateDirectories: true)
    try bytes("z").write(to: stage2.appendingPathComponent("Blocker/inside.txt"))
    do { _ = try BCDGOGUpdate.merge(clashPlan, stage: stage2, into: folder, remove: []); check(false, "merge: a file where a folder is needed is refused") }
    catch { check(error as? BCDContentError == .pathClash && (try? Data(contentsOf: folder.appendingPathComponent("Blocker"))) == bytes("f"), "merge: a file where a folder is needed is refused and left alone") }
    // a missing staged file
    let stage3 = work.appendingPathComponent("stage3")
    try fm.createDirectory(at: stage3, withIntermediateDirectories: true)
    do { _ = try BCDGOGUpdate.merge(plan, stage: stage3, into: folder, remove: []); check(false, "merge: a staged file that is not there is an error") }
    catch { check(true, "merge: a staged file that is not there is an error, nothing is made up") }
} catch {
    check(false, "merge: \(error)")
}

// ---- planning an update, a repair and DLC ---------------------------------------------------------
let X1 = bytes("game-exe-bytes"), P1 = bytes("pak-part-one"), P2 = bytes("pak-part-two"), P3 = bytes("pak-part-three-NEW")
let N1 = bytes("added-in-78"), R1 = bytes("readme"), E1 = bytes("dlc-extra-pak"), G1 = bytes("gone-dll")
let oldDepot = depot([file("Bin\\Game.exe", [X1], packed: 100), file("Data\\Pak.bin", [P1, P2], packed: 200), file("Docs\\readme.txt", [R1], packed: 300),
                      file("Old\\Gone.dll", [G1], packed: 400)])
let newDepot = depot([file("Bin\\Game.exe", [X1], packed: 100), file("Data\\Pak.bin", [P1, P3], packed: 210), file("New\\Added.dat", [N1], packed: 500),
                      file("Docs\\readme.txt", [R1], packed: 300)])
let dlcDepot = depot([file("DLC\\Extra.pak", [E1], packed: 600)])
let linkName = "https://cdn.gog.com/content-system/v2/meta/aa/bb/build78"
let buildJSON: [String: Any] = [
    "version": 2, "baseProductId": "1234567890", "buildId": "78", "installDirectory": "Game",
    "depots": [["manifest": hash("b"), "productId": "1234567890", "languages": ["*"], "size": 100],
               ["manifest": hash("c"), "productId": "555", "languages": ["*"], "size": 20],
               ["manifest": hash("d"), "productId": "777", "languages": ["fr-FR"], "size": 20]],
]
final class Net: @unchecked Sendable {
    let lock = NSLock()
    var asked: [String] = []
    var failing: Set<String> = []
    var owned: [String: Any] = ["owned": [1234567890, 555, "777"]]
    var builds: [String: Any] = ["items": [["build_id": "78", "generation": 2, "branch": NSNull(), "version_name": "1.1", "link": linkName]]]
    func answer(_ url: URL, _ signedIn: Bool) async throws -> [String: Any] {
        let key = url.absoluteString
        lock.withLock { asked.append(key) }
        if failing.contains(where: { key.hasSuffix($0) || key.contains($0) }) { throw URLError(.timedOut) }
        if key == linkName { return buildJSON }
        if key.hasSuffix(hash("a")) { return oldDepot }
        if key.hasSuffix(hash("b")) { return newDepot }
        if key.hasSuffix(hash("c")) { return dlcDepot }
        if key.hasSuffix(hash("d")) { return depot([]) }
        if key == BCDGOGContent.ownedURL.absoluteString { return owned }
        if key.contains("/builds") { return builds }
        throw BCDGOGError.badReply("unexpected " + key)
    }
}
func planKey(_ id: String = "1234567890") -> BCDContentKey { .init(store: "gog", id: id) }
let build78 = BCDGOGBuild(id: "78", version: "1.1", link: URL(string: linkName)!)

// The game as installed from build 77: Game.exe's bytes are NOT the manifest's (same size), the pak is the old one, Gone.dll only the old build had.
let installed = work.appendingPathComponent("installed")
func lay(_ files: [(String, Data)]) {
    try? fm.removeItem(at: installed)
    for (path, data) in files {
        let url = installed.appendingPathComponent(path)
        try? fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try! data.write(to: url)
    }
}
let tampered = Data(repeating: 0x5A, count: X1.count)
lay([("bin/game.exe", tampered), ("Data/Pak.bin", P1 + P2), ("Docs/readme.txt", R1), ("Old/Gone.dll", G1)])

func run(_ kind: BCDGOGJobKind, owned: Set<String>, net: Net, old: [String] = [hash("a")], language: String = "en-US", preferred: [String] = ["en-US"],
         report: @escaping @Sendable (Int64, Int64) -> Void = { _, _ in }) async throws -> (plan: BCDContentPlan, info: BCDGOGPlanInfo) {
    try await BCDGOGContent.plan(planKey(), title: "Game", build: build78, preferred: preferred, containers: false, ownedDLC: owned,
                                 current: .init(kind: kind, folder: installed, oldDepots: old, language: language, report: report), fetch: { try await net.answer($0, $1) })
}
do {
    // An update compares the two builds' file lists and does not read the disk: Game.exe is the same in both, so it stays out although its bytes on the disk are wrong.
    let net = Net()
    let up = try await run(.update, owned: ["555"], net: net)
    check(up.plan.files.map(\.path).sorted() == ["DLC/Extra.pak", "Data/Pak.bin", "New/Added.dat"], "update: the changed pak, the added file and the DLC's file: \(up.plan.files.map(\.path))")
    check(up.info.kind == .update && up.info.dlc == ["555"] && up.info.removed == ["old/gone.dll"], "update: the info says the DLC chosen and the one file the new build no longer has: \(up.info.removed ?? [])")
    let allBytes = [X1, P1 + P3, N1, R1, E1].reduce(0) { $0 + $1.count }
    check(up.info.installBytes == Int64(allBytes), "update: the size the game takes afterwards counts the files left out of the plan: \(up.info.installBytes ?? -1) of \(allBytes)")
    check(net.asked.contains { $0.hasSuffix(hash("a")) }, "update: the installed build's file list was read")

    // A repair reads the disk: the tampered Game.exe and the old pak come back, the missing files too, the right readme does not.
    var seenReports: [Int64] = []
    let reportLock = NSLock()
    let rep = try await run(.repair, owned: ["555"], net: Net(), report: { done, _ in reportLock.withLock { seenReports.append(done) } })
    check(rep.plan.files.map(\.path).sorted() == ["Bin/Game.exe", "DLC/Extra.pak", "Data/Pak.bin", "New/Added.dat"] && rep.info.removed == nil,
          "repair: every file that is missing or wrong, none that is right (the readme stays out), nothing removed: \(rep.plan.files.map(\.path))")
    check(!seenReports.isEmpty && seenReports == seenReports.sorted(), "repair: progress is reported while the files are read (\(seenReports.last ?? 0) bytes)")

    // An update whose old file lists cannot be read checks every file as a repair does.
    let blind = Net()
    blind.failing = [hash("a")]
    let fallback = try await run(.update, owned: ["555"], net: blind)
    check(fallback.plan.files.map(\.path).sorted() == ["Bin/Game.exe", "DLC/Extra.pak", "Data/Pak.bin", "New/Added.dat"] && fallback.info.removed == nil,
          "update without the old file lists: every file is checked against the disk, nothing is removed: \(fallback.plan.files.map(\.path))")

    // The installed language is kept: a French DLC depot is not taken, and an installed de-DE stays de-DE although the device prefers en-US.
    var langDepots = buildJSON
    langDepots["depots"] = [["manifest": hash("b"), "productId": "1234567890", "languages": ["*"]],
                            ["manifest": hash("d"), "productId": "1234567890", "languages": ["de-DE"]],
                            ["manifest": hash("c"), "productId": "1234567890", "languages": ["en-US"]]]
    let german = Net()
    let kept = try await BCDGOGContent.plan(planKey(), title: "Game", build: build78, preferred: ["en-US"], containers: false, ownedDLC: [],
                                            current: .init(kind: .repair, folder: installed, oldDepots: [], language: "de-DE"),
                                            fetch: { url, signedIn in url.absoluteString == linkName ? langDepots : try await german.answer(url, signedIn) })
    check(kept.info.language == "de-DE" && !kept.info.depots.contains(hash("c")), "an update or a repair keeps the installed language: \(kept.info.language), \(kept.info.depots.count) depot(s)")

    // A fresh install with DLC: the owned DLC's depot is in the plan, a DLC with another language list is not, and its chunks are its own group.
    let fresh = try await BCDGOGContent.plan(planKey(), title: "Game", build: build78, preferred: ["en-US"], containers: false, ownedDLC: ["555", "777", "999"], fetch: { try await Net().answer($0, $1) })
    check(fresh.info.kind == .install && fresh.info.dlc == ["555"] && fresh.plan.files.map(\.path).contains("DLC/Extra.pak")
          && fresh.plan.chunks.filter { $0.group == "555" }.count == 1, "install: the owned DLC with a depot for this language comes with the game, its chunks are served by its own product: dlc=\(fresh.info.dlc ?? [])")
    let alone = try await BCDGOGContent.plan(planKey(), title: "Game", build: build78, preferred: ["en-US"], containers: false, fetch: { try await Net().answer($0, $1) })
    check(alone.info.dlc == [] && !alone.plan.files.map(\.path).contains("DLC/Extra.pak"), "install without the owned list: the game alone, as before")
    check(fresh.info.removed == nil && fresh.info.installBytes == fresh.plan.totalBytes, "install: nothing removed, the size is the plan's")

    // The owned list.
    check(BCDGOGContent.parseOwned(["owned": [1234567890, "555", "x1", 0.5, "123456789012345678901"]]) == ["1234567890", "555"], "the owned list: numbers or strings, only product ids")
    let bad = Net()
    bad.failing = ["user/data/games"]
    let ownedFetch: BCDGOGContent.Fetch = { try await bad.answer($0, $1) }
    check((try await BCDGOGContent.owned(fetch: ownedFetch, required: false)).isEmpty, "install: an owned list that cannot be read is the game alone")
    var refused = false
    do { _ = try await BCDGOGContent.owned(fetch: ownedFetch, required: true) } catch { refused = true }
    check(refused, "update and repair: an owned list that cannot be read stops them (a DLC's files must not be taken for removed ones)")

    // Is there an update?
    let avail = Net()
    let newer = try await BCDGOGContent.available(product: "1234567890", buildID: "77", depots: [hash("a")], language: "en-US", dlc: true, fetch: { try await avail.answer($0, $1) })
    check(newer == BCDGOGContent.Available(buildID: "78", version: "1.1", dlc: [], newerBuild: true), "a newer build is an update (no further call)")
    let dlcOnly = try await BCDGOGContent.available(product: "1234567890", buildID: "78", depots: [hash("b")], language: "en-US", dlc: true, fetch: { try await avail.answer($0, $1) })
    check(dlcOnly == BCDGOGContent.Available(buildID: "78", version: "1.1", dlc: ["555"], newerBuild: false), "the same build with an owned DLC the game lacks is an update: \(String(describing: dlcOnly))")
    let have = try await BCDGOGContent.available(product: "1234567890", buildID: "78", depots: [hash("b"), hash("c")], language: "en-US", dlc: true, fetch: { try await avail.answer($0, $1) })
    check(have == nil, "…and not once the DLC is installed")
    let off = try await BCDGOGContent.available(product: "1234567890", buildID: "78", depots: [hash("b")], language: "en-US", dlc: false, fetch: { try await avail.answer($0, $1) })
    check(off == nil, "…nor with DLC off")
    avail.owned = ["owned": [1234567890]]
    let none = try await BCDGOGContent.available(product: "1234567890", buildID: "78", depots: [hash("b")], language: "en-US", dlc: true, fetch: { try await avail.answer($0, $1) })
    check(none == nil, "the same build, nothing owned beyond the game: no update")

    // finishUpdate: the record takes the new build.
    let root = work.appendingPathComponent("root")
    BCDInstallRoot.hostDrive = { _ in root }
    let gameFolder = root.appendingPathComponent("GOG Games/Game")
    try fm.createDirectory(at: gameFolder.appendingPathComponent("Data"), withIntermediateDirectories: true)
    try (P1 + P2).write(to: gameFolder.appendingPathComponent("Data/Pak.bin"))
    try G1.write(to: gameFolder.appendingPathComponent("Gone.dll"))
    let stage = work.appendingPathComponent("stage-up")
    for f in up.plan.files {
        let url = stage.appendingPathComponent(f.path)
        try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(repeating: 0x42, count: Int(f.size)).write(to: url)
    }
    var info = up.info
    info.removed = ["gone.dll"]
    let record = BCDGOGInstallRecord(productID: "1234567890", title: "Game", buildID: "77", versionName: "1.0", language: "en-US",
                                     root: BCDInstallRoot(drive: "C", folder: "GOG Games"), folder: "Game", bytes: 1, depots: [hash("a")], dependencies: [], supportFiles: 0,
                                     play: BCDGOGPlayTask(path: "Bin/Game.exe", arguments: "", workingDirectory: "", category: "game"), entryID: nil,
                                     installed: Date(timeIntervalSince1970: 0))
    let finished = try BCDGOGInstall.finishUpdate(up.plan, info: info, stage: stage, record: record)
    check(finished.record.buildID == "78" && finished.record.versionName == "1.1" && finished.record.depots == info.depots && finished.record.dlc == ["555"]
          && finished.record.scripts == true && finished.record.bytes == Int64(allBytes) && finished.record.play?.path == "Bin/Game.exe",
          "finishUpdate: the record takes the new build, its depots, DLC, size and the script mark; the play task stays when the build names none")
    check(finished.merged == BCDGOGUpdate.MergeResult(replaced: 1, added: 2, removed: 1) && !fm.fileExists(atPath: stage.path)
          && !fm.fileExists(atPath: gameFolder.appendingPathComponent("Gone.dll").path) && fm.fileExists(atPath: gameFolder.appendingPathComponent("DLC/Extra.pak").path),
          "finishUpdate: the files are in place, the removed one is gone, the staging folder is gone: \(finished.merged)")
} catch {
    check(false, "planning: \(error)")
}

print(failures == 0 ? "ALL OK" : "\(failures) FAILED")
exit(failures == 0 ? 0 : 1)
