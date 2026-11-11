// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Host test for GOG's redistributables in BCD (tests/host/check-gog-setup.py): GOGSetup.swift, the plan's redistributables,
// the open link's chunk addresses, the install record and the page's line. Synthetic data only; GOG is never contacted.
import Foundation

var failures = 0
func check(_ ok: Bool, _ label: String) { print((ok ? "PASS: " : "FAIL: ") + label); if !ok { failures += 1 } }

func hash(_ c: Character) -> String { String(repeating: c, count: 32) }
func chunk(_ n: Int, size: Int64 = 100) -> [String: Any] {
    ["md5": String(format: "%032x", n), "size": size, "compressedMd5": String(format: "%032x", n + 1000), "compressedSize": size / 2]
}

// ---- the repository -----------------------------------------------------------------------------
let repositoryJSON: [String: Any] = ["depots": [
    ["dependencyId": "openAL", "readableName": "Open AL (2.0.7.0)", "executable": ["path": "__redist/openAL/oalinst.exe", "arguments": "/s"], "manifest": hash("a"), "size": 100],
    ["dependencyId": "DirectX", "readableName": "DirectX", "executable": ["path": "__redist/DirectX/DXSETUP.exe", "arguments": "/silent"], "manifest": hash("b")],
    ["dependencyId": "MSVC2017_x64", "readableName": "VC 2017", "executable": ["path": "__redist\\MSVC2017_x64\\VC_redist.x64.exe", "arguments": "/install /quiet /norestart"], "manifest": hash("c")],
    ["dependencyId": "PhysX", "readableName": "PhysX", "executable": ["path": "__redist/PhysX/PhysX.msi", "arguments": "/qn"], "manifest": hash("d")],
    ["dependencyId": "DOSBOX", "readableName": "DOSBox", "manifest": hash("e")],
    ["dependencyId": "evil", "executable": ["path": "../../x.exe"], "manifest": hash("f")],
    ["dependencyId": "bad", "manifest": "not-a-hash"],
    ["dependencyId": "MSVC2010_x86", "readableName": "Visual C++ 2010 (x86)", "executable": ["path": "__redist/MSVC2010_x86/vcredist_x86.exe", "arguments": "/q /norestart"], "manifest": hash("1")],
    ["dependencyId": "shell", "readableName": "Shell", "executable": ["path": "__redist/shell/s.exe", "arguments": "/s & calc"], "manifest": hash("2")],
    ["dependencyId": "dotNet4", "readableName": ".NET 4", "executable": ["path": "__redist/dotNet4/dn.exe", "arguments": "/q"], "manifest": hash("3")],
]]
func syncChecks() {
    let repo = GOGDependency.parse(repositoryJSON)
    check(repo.count == 9 && repo.first { $0.id == "evil" }!.executable == "" && !repo.contains { $0.id == "bad" }, "the repository: a manifest name that is not a hash is dropped, an installer path out of the folder is emptied")
    check(repo.first { $0.id == "MSVC2017_x64" }!.executable == "__redist/MSVC2017_x64/VC_redist.x64.exe", "an installer path is read with / between folders")
    let plan = GOGSetup.plan(["openAL", "DirectX", "MSVC2017_x64", "PhysX", "DOSBOX", "dotNet4", "Missing", "openal", "shell", "MSVC2010_x86"], repository: repo, hasMsiexec: false)
    check(plan.run.map(\.id) == ["openAL", "MSVC2010_x86"], "the installers to run: Open AL and Visual C++ 2010 (DirectX, Visual C++ 2015-2022, .NET, an .msi without msiexec and an unknown id are not): \(plan.run.map(\.id))")
    check(plan.download.map(\.id) == ["openAL", "DOSBOX", "shell", "MSVC2010_x86"], "downloaded: the runnable ones, DOSBox's files (nothing to run) and the one left out by its command line: \(plan.download.map(\.id))")
    check(plan.notes.map(\.id) == ["DirectX", "MSVC2017_x64", "PhysX", "dotNet4", "Missing", "shell"], "left out, in the build's order: \(plan.notes.map(\.id))")
    check(plan.notes.first { $0.id == "shell" }!.note.hasPrefix("Skipped: its command line has a character"), "an installer whose arguments hold & | < > ^ is left out (BCD's addition)")
    check(GOGSetup.plan(["PhysX"], repository: repo, hasMsiexec: true).run.map(\.id) == ["PhysX"], "an .msi runs when the build has msiexec")
    check(plan.notes.allSatisfy { !$0.note.contains("Madeira") }, "the notes say BCD")

    let drive = FileManager.default.temporaryDirectory.appendingPathComponent("gog-setup-bcd-\(getpid())/drive_c")
    try? FileManager.default.removeItem(at: drive.deletingLastPathComponent())   // start empty: exit() skips defer, so a folder with a reused process id may be left
    defer { try? FileManager.default.removeItem(at: drive.deletingLastPathComponent()) }
    check(GOGSetup.resultFolder == "C:\\bcd-games\\gog-setup" && GOGSetup.resultPath("MSVC2017_x64") == "C:\\bcd-games\\gog-setup\\msvc2017_x64.result", "the results go in C:\\bcd-games\\gog-setup")
    check(GOGSetup.safeID("a b/c..d") == "a_b_c..d", "a result file name is made of safe characters")
    let batch = GOGSetup.batchLines(plan.run, folder: "C:\\GOG Games\\100% Game", drive: drive)
    check(batch.first == "if not exist \"C:\\bcd-games\\gog-setup\" mkdir \"C:\\bcd-games\\gog-setup\"" && batch.filter { $0.hasPrefix("echo [gog-setup]") }.count == 2, "the batch makes the result folder and runs two installers")
    check(batch.contains("pushd \"C:\\GOG Games\\100%% Game\\__redist\\openAL\"") && batch.contains("\"C:\\GOG Games\\100%% Game\\__redist\\openAL\\oalinst.exe\" /s"),
          "each installer runs from its own folder, % doubled for the batch file")
    check(batch.contains(">\"C:\\bcd-games\\gog-setup\\openal.result\" echo %ERRORLEVEL%"), "its exit code is written to its result file (the redirect first)")
    check(GOGSetup.state("openAL", drive: drive) == .pending, "no result yet: pending")
    let resultFile = GOGSetup.hostURL(GOGSetup.resultPath("openAL"), drive: drive)!
    try? FileManager.default.createDirectory(at: resultFile.deletingLastPathComponent(), withIntermediateDirectories: true)
    try! "3010 \r\n".write(to: resultFile, atomically: true, encoding: .utf8)
    check(GOGSetup.state("openAL", drive: drive) == .done, "3010 (a restart wanted) counts as done")
    check(GOGSetup.batchLines(plan.run, folder: "C:\\G", drive: drive).filter { $0.hasPrefix("echo [gog-setup]") }.count == 1, "a finished installer is not run again, by any game")
    try! "1603\r\n".write(to: resultFile, atomically: true, encoding: .utf8)
    check(GOGSetup.state("openAL", drive: drive) == .failed(1603) && GOGSetup.batchLines(plan.run, folder: "C:\\G", drive: drive).filter { $0.hasPrefix("echo [gog-setup]") }.count == 1,
          "a failed installer is not retried at every start")
    GOGSetup.forget(["openAL"], drive: drive)
    check(GOGSetup.state("openAL", drive: drive) == .pending, "Repair forgets the result: the installer runs at the next start")
    check(GOGSetup.batchLines([], folder: "C:\\G", drive: drive).isEmpty, "no installers: no lines")
    check(GOGSetup.folderOf("C:\\a\\b\\x.exe") == "C:\\a\\b" && GOGSetup.folderOf("C:\\x.exe") == "C:\\" && GOGSetup.folderOf("x.exe") == "C:\\", "folderOf")

    // ---- the page's line ----------------------------------------------------------------------------
    let note = BCDGOGInstall.setupNote(setup: plan.run.map { GOGSetupProgram(id: $0.id, name: $0.name, executable: $0.executable, arguments: $0.arguments) }, notes: plan.notes)
    check(note?.hasPrefix("Redistributables BCD installs once, at the game's first start: Open AL (2.0.7.0), Visual C++ 2010 (x86).") == true
          && note?.contains("Left out: DirectX (not needed: BCD has its own DirectX); VC 2017 (not needed: BCD includes the Visual C++ 2015-2022 runtime)") == true
          && (note?.count ?? 0) <= 600, "the line on the game's page: what runs, what is left out and why: \(note ?? "nil")")
    check(BCDGOGInstall.setupNote(setup: [], notes: []) == nil, "nothing to say: no line")
}

// ---- the plan -----------------------------------------------------------------------------------
final class Net: @unchecked Sendable {
    let lock = NSLock()
    var asked: [String] = []
    var depots: [String: [String: Any]] = [:]
    var failing: Set<String> = []
    var repositoryFails = false
    func answer(_ url: URL, _ signedIn: Bool) async throws -> [String: Any] {
        let key = url.absoluteString
        lock.withLock { asked.append(key + (signedIn ? " (signed in)" : "")) }
        if key == BCDGOGContent.repositoryURL.absoluteString {
            if repositoryFails { throw BCDGOGError.badReply("x") }
            return ["repository_manifest": "https://cdn.gog.com/content-system/v2/dependencies/repository/manifest"]
        }
        if key == "https://cdn.gog.com/content-system/v2/dependencies/repository/manifest" { return repositoryJSON }
        if let h = depots.keys.first(where: { key.hasSuffix($0) }) {
            if failing.contains(h) { throw URLError(.timedOut) }
            return depots[h]!
        }
        if key.contains("/builds") { return ["items": []] }
        if key == "https://cdn.gog.com/content-system/v2/meta/aa/bb/build" { return buildManifest }
        let game = hash("9")
        if key.hasSuffix(game) { return gameDepot }
        throw BCDGOGError.badReply("unexpected " + key)
    }
    let buildManifest: [String: Any] = [
        "version": 2, "baseProductId": "1234567890", "buildId": "77", "installDirectory": "Test Game",
        "dependencies": ["openAL", "DirectX", "MSVC2010_x86", "DOSBOX", "Missing"],
        "depots": [["manifest": hash("9"), "productId": "1234567890", "languages": ["*"]]],
    ]
    let gameDepot: [String: Any] = ["depot": ["items": [["type": "DepotFile", "path": "Bin\\Game.exe", "chunks": [chunk(1)]]]], "version": 2]
}
func redistDepot(_ path: String, _ n: Int) -> [String: Any] {
    ["depot": ["items": [["type": "DepotFile", "path": path, "chunks": [chunk(n)]]]], "version": 2]
}
let build = BCDGOGBuild(id: "77", version: "1.0", link: URL(string: "https://cdn.gog.com/content-system/v2/meta/aa/bb/build")!)
let key = BCDContentKey(store: "gog", id: "1234567890")

func runPlan(_ net: Net, redist: Bool, msiexec: Bool = false) async throws -> (plan: BCDContentPlan, info: BCDGOGPlanInfo) {
    try await BCDGOGContent.plan(key, title: "Test Game", build: build, preferred: ["en-US"], containers: false,
                                 redistributables: redist, hasMsiexec: msiexec, fetch: { try await net.answer($0, $1) })
}

@main struct Harness {
    static func main() async {
        syncChecks()
        let net = Net()
        net.depots = [hash("a"): redistDepot("__redist\\openAL\\oalinst.exe", 11), hash("1"): redistDepot("__redist\\MSVC2010_x86\\vcredist_x86.exe", 12),
                      hash("e"): redistDepot("__redist\\DOSBOX\\dosbox.exe", 13)]
        // 1. off by default: no repository is asked, the plan is what it was
        do {
            let off = try await runPlan(net, redist: false)
            check(off.plan.files.map(\.path) == ["Bin/Game.exe"] && off.info.setup == nil && off.info.setupNotes == nil
                  && !net.asked.contains { $0.contains("dependencies") }, "redistributables not asked for: the plan is the game's, the repository is not contacted, the info has no list")
        } catch { check(false, "plan without redistributables: \(error)") }

        // 2. on
        net.lock.withLock { net.asked = [] }
        do {
            let on = try await runPlan(net, redist: true)
            let paths = on.plan.files.map(\.path)
            check(paths == ["Bin/Game.exe", "__redist/openAL/oalinst.exe", "__redist/MSVC2010_x86/vcredist_x86.exe", "__redist/DOSBOX/dosbox.exe"],
                  "the plan: the game, then each redistributable's files under __redist: \(paths)")
            check(on.info.setup?.map(\.id) == ["openAL", "MSVC2010_x86"] && on.info.setupNotes?.map(\.id) == ["DirectX", "Missing"], "the info: the installers to run and what was left out")
            let redistChunks = on.plan.chunks.filter { $0.group == BCDGOGContent.redistGroup }
            check(redistChunks.count == 3 && on.plan.chunks.filter { $0.group == "1234567890" }.count == 1, "the redistributables' chunks are their own group (the open link serves them)")
            check(!net.asked.contains { $0.hasSuffix("(signed in)") && $0.contains("dependenc") }, "the repository and the redistributables' lists are read without a sign-in")
            check(on.plan.directories.contains("__redist/openAL") || on.plan.directories.contains { $0.hasPrefix("__redist") }, "the folders are in the plan")
        } catch { check(false, "plan with redistributables: \(error)") }

        // 3. the repository cannot be read: the plan is the game's, with a note
        net.repositoryFails = true
        do {
            let down = try await runPlan(net, redist: true)
            check(down.plan.files.map(\.path) == ["Bin/Game.exe"] && down.info.setup == [] && down.info.setupNotes?.count == 1
                  && down.info.setupNotes?.first?.note == "Could not be read from GOG this time.", "no repository: the game installs, with a note and no installer")
        } catch { check(false, "plan without a repository: \(error)") }
        net.repositoryFails = false

        // 4. one redistributable's list cannot be read: the others stay
        net.failing = [hash("a")]
        do {
            let some = try await runPlan(net, redist: true)
            check(some.info.setup?.map(\.id) == ["MSVC2010_x86"] && some.plan.files.map(\.path).contains("__redist/MSVC2010_x86/vcredist_x86.exe")
                  && !some.plan.files.map(\.path).contains("__redist/openAL/oalinst.exe") && some.info.setupNotes?.contains { $0.id == "openAL" } == true,
                  "one list unreadable: that redistributable is left out with a note, the others are planned")
        } catch { check(false, "plan with one failing redistributable: \(error)") }
        net.failing = []

        // 5. a redistributable whose installer is not among its files
        net.depots[hash("a")] = redistDepot("__redist\\openAL\\other.exe", 14)
        do {
            let odd = try await runPlan(net, redist: true)
            check(odd.info.setup?.map(\.id) == ["MSVC2010_x86"] && odd.info.setupNotes?.contains { $0.id == "openAL" } == true && !odd.plan.files.map(\.path).contains("__redist/openAL/other.exe"),
                  "an installer that its list does not hold: left out, its files not planned")
        } catch { check(false, "plan with a missing installer: \(error)") }
        net.depots[hash("a")] = redistDepot("__redist\\openAL\\oalinst.exe", 11)

        // 6. a cancel is thrown
        let task = Task { () -> Bool in
            do { _ = try await BCDGOGContent.plan(key, title: "T", build: build, preferred: ["en-US"], containers: false, redistributables: true,
                                                  fetch: { _, _ in try await Task.sleep(nanoseconds: 5_000_000_000); return [:] }); return false }
            catch { return error is CancellationError }
        }
        task.cancel()
        check(await task.value, "a cancel during the plan is a cancel")

        // 7. the open link's chunk addresses
        let open: [String: Any] = ["urls": [["url_format": "https://gog-cdn.example/store", "priority": 1]]]
        do {
            let parsed = try BCDGOGContent.parseSecureLink(open, now: Date(timeIntervalSince1970: 0), open: true)
            let url = BCDGOGContent.chunkURL(parsed.endpoints[0], locator: "ab" + "cd" + String(repeating: "0", count: 28))
            check(parsed.endpoints.count == 1 && url?.absoluteString == "https://gog-cdn.example/store/ab/cd/abcd" + String(repeating: "0", count: 28), "an open link without parameters or {path}: the chunk's path follows the store's address")
        } catch { check(false, "open link: \(error)") }
        check((try? BCDGOGContent.parseSecureLink(open, now: Date(timeIntervalSince1970: 0))) == nil, "a secure link entry without parameters is still skipped, as before")
        let secure: [String: Any] = ["urls": [["url_format": "https://high.example{path}?token={token}", "parameters": ["path": "/content/1", "token": "T1"], "priority": 5]]]
        if let parsed = try? BCDGOGContent.parseSecureLink(secure, now: Date(timeIntervalSince1970: 0)) {
            check(BCDGOGContent.chunkURL(parsed.endpoints[0], locator: String(format: "%032x", 0x1234))?.absoluteString
                  == "https://high.example/content/1/00/00/" + String(format: "%032x", 0x1234) + "?token=T1", "a secure link's chunk address is what it was")
        } else { check(false, "secure link parses") }

        // 8. the install record
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("gog-setup-bcd-install-\(getpid())")
        try? FileManager.default.removeItem(at: root)   // start empty: exit() skips defer, so a folder with a reused process id may be left
        defer { try? FileManager.default.removeItem(at: root) }
        BCDInstallRoot.hostDrive = { _ in root }
        do {
            var builder = try BCDContentPlanBuilder(key: key, title: "T", version: "77", folder: "Test Game")
            let index = try builder.addFile("Bin/Game.exe", size: 3, digest: nil)
            _ = try builder.addChunk(BCDContentChunk(locator: String(format: "%032x", 1), group: "1", fetchSize: 3, size: 3, fetchDigest: nil, digest: nil, codec: .zlib))
            try builder.addPart(chunk: 0, chunkOffset: 0, length: 3, file: index, fileOffset: 0)
            let made = try builder.build()
            let stage = root.appendingPathComponent("stage")
            try FileManager.default.createDirectory(at: stage.appendingPathComponent("Bin"), withIntermediateDirectories: true)
            try Data("abc".utf8).write(to: stage.appendingPathComponent("Bin/Game.exe"))
            let info = BCDGOGPlanInfo(productID: "1234567890", title: "T", buildID: "77", version: "1.0", language: "en-US", depots: [], dependencies: ["openAL"],
                                      supportFiles: 0, links: 0, containers: false,
                                      setup: [GOGSetupProgram(id: "openAL", name: "Open AL", executable: "__redist/openAL/oalinst.exe", arguments: "/s")],
                                      setupNotes: [GOGSetupNote(id: "DirectX", name: "DirectX", note: "Not needed: BCD has its own DirectX")])
            let record = try BCDGOGInstall.finish(made, info: info, stage: stage, root: BCDInstallRoot(drive: "C", folder: "GOG Games"))
            check(record.setup?.map(\.id) == ["openAL"] && record.setupNotes?.count == 1 && record.scripts == true, "the install record keeps the installers, the notes and the script mark")
            try BCDGOGInstall.save(record, base: root.appendingPathComponent("base"))
            check(BCDGOGInstall.loadRecords(root.appendingPathComponent("base"))["1234567890"]?.setup?.first?.executable == "__redist/openAL/oalinst.exe", "…and they survive the file")
            // a record file from before has none of them
            var object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(record)) as! [String: Any]
            object.removeValue(forKey: "setup"); object.removeValue(forKey: "setupNotes")
            let old = try JSONDecoder().decode(BCDGOGInstallRecord.self, from: JSONSerialization.data(withJSONObject: object))
            check(old.setup == nil && old.setupNotes == nil, "a record file from before decodes with no installers: such a game runs none")
        } catch { check(false, "install record: \(error)") }

        print(failures == 0 ? "ALL OK" : "\(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
