// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// The compiled part of tests/host/check-gog-gen1.py: GOG's generation 1 in BCD (app/Madeira/BCDGOG/BCDGOGContentV1.swift) and the
// content engine's ranged chunks (BCDContentChunk.rangeStart, BCDHTTPStream.fetch(slice:)), on the production files. Phases (argv[1]),
// each given the driver's fixture file (argv[2]):
//   units    builds list, repository, language, depots, chunk slicing, secure links, refusals (GOG's public replies + constructed shapes)
//   engine   a generation-2 plan keeps its identity and saved form; the slice requests (Range forms, resume, refusals) on a stand-in host
//   rtcw     Return to Castle Wolfenstein's real repository and depot manifests planned (no download): the driver compares the summary
//   install  a constructed generation-1 game (and DLC) downloaded from the driver's local server, with faults, and installed
//   repair   the installed game with one file changed and one removed: only those two planned and put back
//   dlc      the owned DLC found by the update check and installed by an update
//   resume1  a download stopped once some slices are journaled; resume2 finishes it

import Foundation

var failures = 0
func check(_ ok: Bool, _ what: String) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
    fflush(stdout)
}
func emit(_ tag: String, _ text: String) { print(tag + " " + text); fflush(stdout) }

struct Fixture: Decodable {
    var work: String
    var base: String
    var fixtures: String
    var product: String
    var dlc: String
    var timestamp: String
}

var logs: [String] = []
let logLock = NSLock()
func logged() -> String { logLock.withLock { logs.joined(separator: "\n") } }

func json(_ text: String) -> [String: Any] { try! JSONSerialization.jsonObject(with: Data(text.utf8)) as! [String: Any] }
func jsonFile(_ url: URL) throws -> [String: Any] {
    let data = try BCDZlib.jsonData(Data(contentsOf: url), limit: 64 << 20)
    return try JSONSerialization.jsonObject(with: data) as! [String: Any]
}

func expectError(_ what: String, _ wanted: ((Error) -> Bool)? = nil, _ body: () async throws -> Void) async {
    do { try await body(); check(false, what + " (no error)") } catch { check(wanted?(error) ?? true, what + " (\(error))") }
}
func isGOG(_ e: BCDGOGError) -> (Error) -> Bool { { ($0 as? BCDGOGError) == e } }
func isContent(_ e: BCDContentError) -> (Error) -> Bool { { ($0 as? BCDContentError) == e } }

final class Box<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: T
    init(_ value: T) { self.value = value }
    var get: T { lock.withLock { value } }
    func set(_ new: T) { lock.withLock { value = new } }
    func update(_ change: (inout T) -> Void) { lock.withLock { change(&value) } }
}

// MARK: units

func unitsPhase(_ fx: Fixture) async {
    let dir = URL(fileURLWithPath: fx.fixtures)
    // The real builds list of Return to Castle Wolfenstein (public, 2026-11-15): one public generation-1 build, its link on gog-cdn.gcdn.co.
    let builds = try! jsonFile(dir.appendingPathComponent("rtcw/builds.json"))
    do { _ = try BCDGOGContent.newestBuild(builds); check(false, "generation 2's own reading still refuses the list") }
    catch { check(error as? BCDGOGError == .olderBuildsOnly, "generation 2's own reading of the list is unchanged: older builds only") }
    let fetched = Box<[String]>([])
    let rtcwFetch: BCDGOGContent.Fetch = { url, signedIn in
        fetched.update { $0.append((signedIn ? "+" : "-") + url.absoluteString) }
        if url.absoluteString == "https://content-system.gog.com/products/1441704976/os/windows/builds?generation=2" { return builds }
        throw BCDGOGError.badReply("unexpected address")
    }
    do {
        let build = try await BCDGOGContent.newest("1441704976", fetch: rtcwFetch)
        check(build == BCDGOGBuild(id: "48442691146817137", version: "1.41",
                                   link: URL(string: "https://cdn.gog.com/content-system/v1/manifests/1441704976/windows/50078739/repository.json")!, generation: 1),
              "newest(): the generation-1 build, its repository read from cdn.gog.com (the list named gog-cdn.gcdn.co)")
        check(fetched.get == ["+https://content-system.gog.com/products/1441704976/os/windows/builds?generation=2"],
              "newest(): one signed-in read of the same builds list as generation 2")
    } catch { check(false, "newest() on the real list: \(error)") }
    let log = logged()
    check(log.contains("[gog-content] no public generation-2 build: items=1 gen1=1 gen2=0 branches=0")
          && log.contains("[gog-content] generation-1 repository read from cdn.gog.com")
          && log.contains("[gog-content] generation-1 build: no public generation-2 build"),
          "the log says why (counts only) and which content system is read")

    // Lists that generation 2 reads are read as before.
    let mixed = json(#"""
    {"items":[
      {"build_id":"9","generation":1,"branch":null,"link":"https://cdn.gog.com/content-system/v1/manifests/1/windows/2/repository.json"},
      {"build_id":"56452082907692588","generation":2,"branch":null,"version_name":"3.5","link":"https://gog-cdn-fastly.gog.com/content-system/v2/meta/e5/18/e518c17d90805e8e3998a35fac8b8505"}]}
    """#)
    let mixedBuild = try? await BCDGOGContent.newest("1", fetch: { _, _ in mixed })
    check(mixedBuild?.generation == 2 && mixedBuild?.id == "56452082907692588", "a list with a public generation-2 build: that build, generation 2 (as before)")
    await expectError("a generation-1 build only in a branch: no Windows download (as before)", isGOG(.noWindowsBuild)) {
        _ = try await BCDGOGContent.newest("1", fetch: { _, _ in json(#"{"items":[{"build_id":"9","generation":1,"branch":"beta","link":"https://cdn.gog.com/x"}]}"#) })
    }
    let branchFirst = json(#"""
    {"items":[
      {"build_id":"8","generation":1,"branch":"beta","link":"https://cdn.gog.com/content-system/v1/manifests/1/windows/3/repository.json"},
      {"build_id":"7","generation":1,"branch":null,"version_name":" 1.0 ","link":"https://gog-cdn-fastly.gog.com/content-system/v1/manifests/1/windows/2/repository.json"}]}
    """#)
    let picked = try? await BCDGOGContent.newest("1", fetch: { _, _ in branchFirst })
    check(picked?.id == "7" && picked?.version == "1.0" && picked?.link.host == "gog-cdn-fastly.gog.com",
          "generation 1: the first public build (a branch skipped); a gog.com link is used as it is")
    for bad in ["http://gog-cdn.gcdn.co/content-system/v1/manifests/1/windows/2/repository.json",
                "https://gog-cdn.gcdn.co/content-system/v1/manifests/1/windows/2/repository.json?x=1",
                "https://gog-cdn.gcdn.co/content-system/v1/manifests/1/windows/2/other.json",
                "https://gog-cdn.gcdn.co/content-system/v1/manifests/1/osx/2/repository.json",
                "https://gog-cdn.gcdn.co/content-system/v1/manifests/1a/windows/2/repository.json",
                "https://gog-cdn.gcdn.co/content-system/v1/manifests/1/windows/../repository.json",
                "https://gog-cdn.gcdn.co/x/content-system/v1/manifests/1/windows/2/repository.json",
                "https://u@gog-cdn.gcdn.co/content-system/v1/manifests/1/windows/2/repository.json",
                "https://gog-cdn.gcdn.co/content-system/v2/meta/f7/0d/f70d7d066327a1f183582c5535803eeb"] {
        await expectError("a generation-1 link off GOG's hosts that is not a repository link is refused: \(bad)", isGOG(.badReply("build"))) {
            _ = try await BCDGOGContent.newest("1", fetch: { _, _ in json(#"{"items":[{"build_id":"7","generation":1,"branch":null,"link":"\#(bad)"}]}"#) })
        }
    }
    await expectError("a generation-1 build with a bad id is refused", isGOG(.badReply("build"))) {
        _ = try await BCDGOGContent.newest("1", fetch: { _, _ in json(#"{"items":[{"build_id":"7x","generation":1,"branch":null,"link":"https://cdn.gog.com/a"}]}"#) })
    }

    // The repository (real: Return to Castle Wolfenstein, Quake: The Offering).
    do {
        let repo = try BCDGOGContent.parseRepositoryV1(try jsonFile(dir.appendingPathComponent("rtcw/repository.json")))
        check(repo.rootProductID == "1441704976" && repo.timestamp == "50078739" && repo.installDirectory == "Return to Castle Wolfenstein"
              && repo.products == ["1441704976"] && repo.dependencies == ["DirectX"],
              "RTCW's repository: product, folder, install directory, the DirectX redistributable")
        check(repo.depots.map(\.manifest) == ["6553fa0b-3e8e-4209-a888-d116f450423f.json", "70877085-58b3-48b7-90bc-4191efe5bf9e.json",
                                              "a968af32-f5ae-4735-9c5f-de1511d931ab.json", "34e78725-aed4-40f9-9c78-5ffa67a5bb97.json"]
              && repo.depots.map(\.size) == [1274800, 805384744, 953758861, 0] && repo.depots[3].languages == ["English", "German"],
              "RTCW's four depots: manifests, sizes written as strings, languages")
        let quake = try BCDGOGContent.parseRepositoryV1(try jsonFile(dir.appendingPathComponent("quake-offering/repository.json")))
        check(quake.dependencies == ["nGlide", "DirectX"] && quake.depots.count == 2, "Quake: The Offering: nGlide (no installer) and DirectX")
        func L(_ preferred: [String]) -> String {
            BCDGOGContent.language(offered: Array(Set(repo.depots.flatMap { $0.languages.map(BCDGOGContent.v1LanguageCode) })).sorted(), preferred: preferred)
        }
        check(L(["en-US"]) == "en-US" && L(["de-AT", "en-GB"]) == "de-DE" && L(["tr-TR"]) == "en-US" && L(["fr-FR", "de-CH"]) == "de-DE",
              "RTCW's language: the device's (by language), else US English")
        func chosen(_ language: String) -> [String] { BCDGOGContent.depotsV1(repo, products: ["1441704976"], language: language).map { String($0.manifest.prefix(4)) } }
        check(chosen("en-US") == ["6553", "7087", "34e7"] && chosen("de-DE") == ["6553", "a968", "34e7"],
              "RTCW's depots: neutral, the language's, the shared folder depot, each once")
    } catch { check(false, "repository: \(error)") }
    check(BCDGOGContent.v1LanguageCode("English") == "en-US" && BCDGOGContent.v1LanguageCode(" german ") == "de-DE"
          && BCDGOGContent.v1LanguageCode("Neutral") == "*" && BCDGOGContent.v1LanguageCode("cz") == "cs-CZ"
          && BCDGOGContent.v1LanguageCode("Chinese (Simplified)") == "zh-Hans" && BCDGOGContent.v1LanguageCode("pl-PL") == "pl-PL"
          && BCDGOGContent.v1LanguageCode("Portuguese (Brazilian)") == "pt-BR" && BCDGOGContent.v1LanguageCode("Klingon") == "Klingon"
          && BCDGOGContent.v1Languages.count == 84,
          "languages: GOG's names, codes and older codes as codes; Neutral is every language; an unknown one kept as written")
    // Constructed shapes (not GOG's): DLC, another system, refusals.
    let shaped = json(#"""
    {"version":1,"product":{"rootGameID":"100","timestamp":7,"installDirectory":"/Game/","gameIDs":[{"gameID":"100"},{"gameID":"200"}],
     "depots":[
      {"languages":["Neutral"],"manifest":"aaaa-1.json","gameIDs":["100"],"size":"10","systems":["Windows"]},
      {"languages":["Neutral"],"manifest":"bbbb-2.json","gameIDs":["200"],"size":5},
      {"languages":["Neutral"],"manifest":"cccc-3.json","gameIDs":["100"],"systems":["Mac"]},
      {"languages":["French"],"manifest":"dddd-4.json","gameIDs":["100"]},
      {"languages":["Neutral"],"manifest":"aaaa-1.json","gameIDs":["100"]},
      {"redist":"MSVC2005"},{"redist":""},{"redist":"PhysX","targetDir":"/"}]}}
    """#)
    do {
        let repo = try BCDGOGContent.parseRepositoryV1(shaped)
        check(repo.timestamp == "7" && repo.installDirectory == "Game" && repo.products == ["100", "200"] && repo.dependencies == ["MSVC2005", "PhysX"]
              && !repo.depots.contains { $0.manifest == "cccc-3.json" }, "a depot for another system is left out; a redistributable without a name too")
        let mine = BCDGOGContent.depotsV1(repo, products: ["100"], language: "en-US").map(\.manifest)
        let withDLC = BCDGOGContent.depotsV1(repo, products: ["100", "200"], language: "fr-FR").map(\.manifest)
        check(mine == ["aaaa-1.json"] && withDLC == ["aaaa-1.json", "bbbb-2.json", "dddd-4.json"],
              "depots: the DLC's only when it is owned; the language's; a manifest named twice once")
    } catch { check(false, "constructed repository: \(error)") }
    for (what, text) in [("another version", #"{"version":2,"product":{"rootGameID":"1","timestamp":1,"depots":[]}}"#),
                         ("no product", #"{"version":1}"#),
                         ("a folder that is not a number", #"{"version":1,"product":{"rootGameID":"1","timestamp":"../1","depots":[]}}"#),
                         ("a bad product", #"{"version":1,"product":{"rootGameID":"x1","timestamp":1,"depots":[]}}"#)] {
        do { _ = try BCDGOGContent.parseRepositoryV1(json(text)); check(false, "a repository with \(what) is refused") }
        catch { check(error as? BCDGOGError == .badReply("v1-repository"), "a repository with \(what) is refused") }
    }
    for (what, depot) in [("a manifest name out of the folder", #"{"languages":["Neutral"],"manifest":"../../x.json","gameIDs":["1"]}"#),
                          ("a manifest name with a space", #"{"languages":["Neutral"],"manifest":"a b.json","gameIDs":["1"]}"#),
                          ("a manifest without .json", #"{"languages":["Neutral"],"manifest":"abcd","gameIDs":["1"]}"#),
                          ("a product that is not a number", #"{"languages":["Neutral"],"manifest":"a.json","gameIDs":["1x"]}"#),
                          ("no products", #"{"languages":["Neutral"],"manifest":"a.json","gameIDs":[]}"#),
                          ("no languages", #"{"languages":[],"manifest":"a.json","gameIDs":["1"]}"#)] {
        do { _ = try BCDGOGContent.parseRepositoryV1(json(#"{"version":1,"product":{"rootGameID":"1","timestamp":1,"depots":[\#(depot)]}}"#)); check(false, "a depot with \(what) is refused") }
        catch { check(error as? BCDGOGError == .badReply("v1-depot"), "a depot with \(what) is refused") }
    }
    check(BCDGOGContent.gogHostLinkV1(URL(string: "https://gog-cdn.gcdn.co/content-system/v1/manifests/1441704976/windows/50078739/repository.json")!)?.absoluteString
          == "https://cdn.gog.com/content-system/v1/manifests/1441704976/windows/50078739/repository.json", "a repository link moves to cdn.gog.com, same path")
    let depot = BCDGOGRepositoryV1.Depot(manifest: "70877085-58b3-48b7-90bc-4191efe5bf9e.json", products: ["1441704976"], languages: ["English"], size: 1)
    check(BCDGOGContent.depotURLV1(depot, timestamp: "50078739", near: URL(string: "https://cdn.gog.com/x")!)?.absoluteString
          == "https://cdn.gog.com/content-system/v1/manifests/1441704976/windows/50078739/70877085-58b3-48b7-90bc-4191efe5bf9e.json"
          && BCDGOGContent.depotURLV1(depot, timestamp: "5/1", near: URL(string: "https://cdn.gog.com/x")!) == nil,
          "a depot manifest's address: the build's folder under the depot's first product")

    // Slices of main.bin.
    let md5 = BCDDigest(.md5, String(repeating: "a", count: 32))!
    let piece = BCDGOGContent.v1Piece
    let one = BCDGOGContent.v1Chunks(product: "1", timestamp: "2", offset: 1000, size: piece, digest: md5)
    let two = BCDGOGContent.v1Chunks(product: "1", timestamp: "2", offset: 1000, size: piece + 1, digest: md5)
    check(piece == 16 << 20 && BCDGOGContent.v1Chunks(product: "1", timestamp: "2", offset: 5, size: 0, digest: md5).isEmpty
          && one.count == 1 && one[0].digest == md5 && one[0].rangeStart == 1000 && one[0].fetchSize == piece && one[0].codec == .stored
          && one[0].group == "v1:1:2" && one[0].locator == "1000+\(piece):" + md5.hex,
          "a file that fits one slice: one stored chunk at its offset, checked by the file's MD5")
    check(two.count == 2 && two.map(\.rangeStart) == [1000, 1000 + piece] && two.map(\.size) == [piece, 1] && two.allSatisfy { $0.digest == nil }
          && two.map(\.locator) == ["1000+\(piece)", "\(1000 + piece)+1"], "a bigger file: slices of 16 MiB without a check of their own")
    let group = BCDGOGContent.v1GroupParts("v1:1441704976:50078739")
    check(group?.product == "1441704976" && group?.timestamp == "50078739" && BCDGOGContent.v1GroupParts("1441704976") == nil
          && BCDGOGContent.v1GroupParts("v1:1:x") == nil && BCDGOGContent.v1GroupParts("redist") == nil, "a slice's group names its product and folder")

    // A depot manifest into a plan: constructed shapes.
    func plan(_ files: String, products: [String] = ["100"]) throws -> (BCDContentPlan, BCDGOGContent.DepotResult) {
        var builder = try BCDContentPlanBuilder(key: .init(store: "gog", id: "100"), title: "T", version: "1", folder: "G")
        let d = BCDGOGRepositoryV1.Depot(manifest: "a.json", products: products, languages: ["Neutral"], size: 0)
        let added = try BCDGOGContent.addV1(depot: json(#"{"version":1,"depot":{"name":"x","files":[\#(files)]}}"#), depot: d, timestamp: "7", to: &builder)
        let kept = BCDGOGContent.addSupportFiles(added.kept, to: &builder)
        var result = added
        result.support += kept.skipped
        return (try builder.build(), result)
    }
    let h1 = String(format: "%032x", 1), h2 = String(format: "%032x", 2)
    do {
        let (p, r) = try plan(#"""
          {"directory":true,"path":"/saves/"},
          {"path":"/Bin/Game.exe","offset":10,"size":20,"hash":"\#(h1)","url":"100/main.bin","executable":true},
          {"path":"/Data/a.cfg","offset":40,"size":5,"hash":"\#(h2)","url":"100/main.bin"},
          {"path":"/Data/b.cfg","offset":40,"size":5,"hash":"\#(h2)","url":"100/main.bin"},
          {"path":"/empty.txt","offset":0,"size":0,"hash":"","url":"100/main.bin"},
          {"path":"100/app/dosbox.conf","offset":60,"size":7,"hash":"\#(h1)","url":"100/main.bin","support":true},
          {"path":"100/app/bin/game.exe","offset":70,"size":3,"hash":"\#(h1)","url":"100/main.bin","support":true},
          {"path":"100/galaxy_helper.exe","offset":80,"size":9,"hash":"\#(h1)","url":"100/main.bin","support":true}
        """#)
        check(p.files.map(\.path) == ["Bin/Game.exe", "Data/a.cfg", "Data/b.cfg", "empty.txt", "dosbox.conf"] && p.directories.contains("saves")
              && r.support == 2 && r.kept.count == 2,
              "files and folders planned; the support folder's app/ file kept below app/ but never over a game file; GOG's helper left out")
        let a = p.files.firstIndex { $0.path == "Data/a.cfg" }!, b = p.files.firstIndex { $0.path == "Data/b.cfg" }!
        check(Set(p.parts.filter { $0.file == a || $0.file == b }.map(\.chunk)).count == 1 && p.chunks.count == 3,
              "two files at the same bytes share one chunk (GOG keeps identical files once)")
        check(p.files.first { $0.path == "empty.txt" }?.digest?.hex == BCDGOGContent.v1EmptyMD5 && !p.parts.contains { p.files[$0.file].path == "empty.txt" },
              "an empty file: no slice, the MD5 of no bytes whatever GOG lists")
        check(p.chunks.allSatisfy { $0.codec == .stored && $0.rangeStart != nil && $0.group == "v1:100:7" && $0.fetchDigest == nil }
              && p.chunks.first { $0.rangeStart == 10 }?.digest?.hex == h1, "every chunk a stored slice of the product's main.bin, checked by the file's MD5")
    } catch { check(false, "constructed depot: \(error)") }
    do {
        let (p, _) = try plan(#"{"path":"/x.bin","offset":3,"size":5,"hash":"\#(h1)","url":"200/main.bin"}"#, products: ["100", "200"])
        check(p.chunks.first?.group == "v1:200:7", "a file of a depot shared by two products comes from the main.bin its url names")
        let (q, _) = try plan(#"{"path":"/x.bin","offset":3,"size":5,"hash":"\#(h1)"}"#, products: ["200", "100"])
        check(q.chunks.first?.group == "v1:200:7", "no url: the depot's first product (gogdl's rule)")
    } catch { check(false, "url products: \(error)") }
    for (what, item, wanted) in [
        ("a field BCD does not know", #"{"path":"/x","offset":0,"size":1,"hash":"\#(h1)","compressed":true}"#, BCDGOGError.unknownFileSource),
        ("a url that is not a main.bin", #"{"path":"/x","offset":0,"size":1,"hash":"\#(h1)","url":"100/other.bin"}"#, .unknownFileSource),
        ("a url of a product the depot is not for", #"{"path":"/x","offset":0,"size":1,"hash":"\#(h1)","url":"300/main.bin"}"#, .unknownFileSource),
        ("no MD5", #"{"path":"/x","offset":0,"size":1}"#, .badReply("v1-file")),
        ("an MD5 that is not one", #"{"path":"/x","offset":0,"size":1,"hash":"xyz"}"#, .badReply("v1-file")),
        ("a negative size", #"{"path":"/x","offset":0,"size":-1,"hash":"\#(h1)"}"#, .badReply("v1-file")),
        ("a size that is true", #"{"path":"/x","offset":0,"size":true,"hash":"\#(h1)"}"#, .badReply("v1-file")),
        ("a fractional offset", #"{"path":"/x","offset":1.5,"size":1,"hash":"\#(h1)"}"#, .badReply("v1-file")),
        ("an offset that is text", #"{"path":"/x","offset":"abc","size":1,"hash":"\#(h1)"}"#, .badReply("v1-file")),
        ("no path", #"{"offset":0,"size":1,"hash":"\#(h1)"}"#, .badReply("v1-item")),
    ] {
        do { _ = try plan(item); check(false, "a file with \(what) is refused") }
        catch { check(error as? BCDGOGError == wanted, "a file with \(what) is refused (\(error))") }
    }
    for (what, item) in [("a path out of the game's folder", #"{"path":"/../evil.dll","offset":0,"size":1,"hash":"\#(h1)"}"#),
                         ("a drive letter", #"{"path":"C:/evil.dll","offset":0,"size":1,"hash":"\#(h1)"}"#),
                         ("an empty path", #"{"path":"/","offset":0,"size":1,"hash":"\#(h1)"}"#)] {
        do { _ = try plan(item); check(false, "a file with \(what) is refused") }
        catch { check(error as? BCDContentError == .unsafePath, "a file with \(what) is refused (\(error))") }
    }
    do {
        var builder = try BCDContentPlanBuilder(key: .init(store: "gog", id: "100"), title: "T", version: "1", folder: "G")
        let d = BCDGOGRepositoryV1.Depot(manifest: "a.json", products: ["100"], languages: ["Neutral"], size: 0)
        _ = try BCDGOGContent.addV1(depot: json(#"{"version":2,"depot":{"files":[]}}"#), depot: d, timestamp: "7", to: &builder)
        check(false, "a depot manifest of another version is refused")
    } catch { check(error as? BCDGOGError == .badReply("v1-depot-manifest"), "a depot manifest of another version is refused") }

    // Secure links: generation 2's reply, generation-1 endpoints, main.bin.
    let now = Date(timeIntervalSince1970: 2_000_000_000)
    let link = json(#"""
    {"urls":[
      {"url_format":"https://two.example{path}","parameters":{"path":"/x"},"priority":20,"supports_generation":[2]},
      {"url_format":"https://fallback.example{path}?t={token}","parameters":{"path":"/content-system/v1/depots/100/windows/7","token":"T"},"priority":9,"fallback_only":true,"supports_generation":[1,2]},
      {"url_format":"https://main.example{path}","parameters":{"path":"/c/100/windows/7","expires_at":2000001000},"priority":5,"supports_generation":[1]},
      {"url_format":"https://plain.example{path}","parameters":{"path":"/p"},"priority":3}]}
    """#)
    do {
        let parsed = try BCDGOGContent.parseSecureLinkV1(link, now: now)
        check(parsed.endpoints.map(\.priority) == [5, 3, 9] && parsed.expires == Date(timeIntervalSince1970: 2_000_001_000),
              "a generation-1 secure link: generation-2-only endpoints dropped, then generation 2's order and expiry")
        check(BCDGOGContent.mainBinURL(parsed.endpoints[0])?.absoluteString == "https://main.example/c/100/windows/7/main.bin"
              && BCDGOGContent.mainBinURL(parsed.endpoints[2])?.absoluteString == "https://fallback.example/content-system/v1/depots/100/windows/7/main.bin?t=T",
              "main.bin's address: the path gains /main.bin (gogdl's form), the parameters filled")
        check(BCDGOGContent.mainBinURL(.init(format: "http://x.example{path}", parameters: ["path": "/a"], priority: 0, fallbackOnly: false)) == nil
              && BCDGOGContent.mainBinURL(.init(format: "https://x.example{path}?{missing}", parameters: ["path": "/a"], priority: 0, fallbackOnly: false)) == nil,
              "no address without https or with an unfilled parameter")
    } catch { check(false, "secure link v1: \(error)") }
    await expectError("a secure link with only generation-2 endpoints is refused", isGOG(.badReply("secure-link"))) {
        _ = try BCDGOGContent.parseSecureLinkV1(json(#"{"urls":[{"url_format":"https://a{path}","parameters":{"path":"/x"},"supports_generation":[2]}]}"#), now: now)
    }
    check(BCDGOGContent.secureLinkURLV1("1441704976", timestamp: "50078739")?.absoluteString
          == "https://content-system.gog.com/products/1441704976/secure_link?_version=2&type=depot&path=/windows/50078739/"
          && BCDGOGContent.secureLinkURLV1("1x", timestamp: "5") == nil && BCDGOGContent.secureLinkURLV1("1", timestamp: "5/") == nil,
          "the generation-1 secure link: type=depot for the build's folder (gogdl's form)")

    // The chunk source: one link per product and folder, renewed; any other chunk to the inner source.
    final class Inner: BCDChunkSource, @unchecked Sendable {
        var asked: [String] = []
        func urls(for chunk: BCDContentChunk) async throws -> [URL] { asked.append(chunk.group); return [URL(string: "https://inner.example/x")!] }
        func refresh(group: String) async throws { asked.append("refresh " + group) }
    }
    let inner = Inner()
    let clock = Box(Date(timeIntervalSince1970: 1_900_000_000))
    let calls = Box<[String]>([])
    let source = BCDGOGV1ChunkSource(fetch: { product, timestamp in
        calls.update { $0.append(product + "@" + timestamp) }
        return ["urls": [["url_format": "https://cdn.example{path}", "parameters": ["path": "/v1/\(product)", "expires_at": clock.get.timeIntervalSince1970 + 600],
                          "priority": 1, "supports_generation": [1]]]]
    }, inner: inner, now: { clock.get })
    let slice = BCDContentChunk(locator: "5+5", group: "v1:100:7", fetchSize: 5, size: 5, fetchDigest: nil, digest: nil, codec: .stored, rangeStart: 5)
    let redist = BCDContentChunk(locator: String(repeating: "b", count: 32), group: "redist", fetchSize: 5, size: 9, fetchDigest: nil, digest: nil, codec: .zlib)
    do {
        let first = try await source.urls(for: slice)
        _ = try await source.urls(for: slice)
        check(first.map(\.absoluteString) == ["https://cdn.example/v1/100/main.bin"] && calls.get == ["100@7"], "one generation-1 link per product and folder, kept while valid")
        clock.update { $0 = $0.addingTimeInterval(500) }
        _ = try await source.urls(for: slice)
        try await source.refresh(group: "v1:100:7")
        check(calls.get == ["100@7", "100@7", "100@7"], "renewed with under two minutes left, and when a host refuses it")
        let other = try await source.urls(for: redist)
        try await source.refresh(group: "redist")
        check(other.map(\.absoluteString) == ["https://inner.example/x"] && inner.asked == ["redist", "refresh redist"] && calls.get.count == 3,
              "the job's other chunks (redistributables) go to generation 2's source")
    } catch { check(false, "chunk source: \(error)") }
}

// MARK: engine (generation 2 untouched; slices)

func enginePhase(_ fx: Fixture) async {
    // The plan of tools/../baseline harness: built by the code before build 663 and by this one, the same identity and saved form.
    func d(_ n: Int) -> BCDDigest { BCDDigest(.md5, String(format: "%032x", n))! }
    do {
        var b = try BCDContentPlanBuilder(key: .init(store: "gog", id: "1234567890"), title: "Test Game", version: "77", folder: "Test Game")
        try b.addDirectory("saves")
        let f1 = try b.addFile("Bin\\Game.exe", size: 150, digest: d(77))
        let c1 = try b.addChunk(BCDContentChunk(locator: String(format: "%032x", 1001), group: "1234567890", fetchSize: 60, size: 100, fetchDigest: d(1001), digest: d(1), codec: .zlib))
        let c2 = try b.addChunk(BCDContentChunk(locator: String(format: "%032x", 1002), group: "1234567890", fetchSize: 30, size: 50, fetchDigest: d(1002), digest: d(2), codec: .zlib))
        try b.addPart(chunk: c1, chunkOffset: 0, length: 100, file: f1, fileOffset: 0)
        try b.addPart(chunk: c2, chunkOffset: 0, length: 50, file: f1, fileOffset: 100)
        let f2 = try b.addFile("Data/a.txt", size: 30, digest: nil)
        let f3 = try b.addFile("data/b.txt", size: 40, digest: d(78))
        let c3 = try b.addChunk(BCDContentChunk(locator: String(format: "%032x", 1010), group: "1234567890", fetchSize: 40, size: 70, fetchDigest: d(1010), digest: d(10), codec: .zlib))
        try b.addPart(chunk: c3, chunkOffset: 0, length: 30, file: f2, fileOffset: 0)
        try b.addPart(chunk: c3, chunkOffset: 30, length: 40, file: f3, fileOffset: 0)
        let f4 = try b.addFile("redist/x.dll", size: 20, digest: nil)
        let c4 = try b.addChunk(BCDContentChunk(locator: "ChunksV4/12/0000000000000001_00000000000000000000000000000001.chunk", group: "redist", fetchSize: 20, size: 20, fetchDigest: nil, digest: nil, codec: .stored))
        try b.addPart(chunk: c4, chunkOffset: 0, length: 20, file: f4, fileOffset: 0)
        _ = try b.addFile("empty.txt", size: 0, digest: nil)
        let plan = try b.build()
        check(plan.identity == "fb33ce4c9673bbb6a1ab57c9a8e8750679a656542722772a7eb66675ee93bfeb",
              "a plan without ranged chunks has the identity the code before build 663 gave it (a paused download resumes after the update)")
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let saved = try Data(contentsOf: URL(fileURLWithPath: fx.fixtures).appendingPathComponent("gen2-plan-base-sorted.json"))
        check(try encoder.encode(plan) == saved && !String(decoding: saved, as: UTF8.self).contains("rangeStart"),
              "...and its saved form, byte for byte (the file the code before build 663 wrote, sorted keys)")
        let back = try JSONDecoder().decode(BCDContentPlan.self, from: saved)
        try back.validate()
        check(back.chunks.allSatisfy { $0.rangeStart == nil } && back.identity == plan.identity, "a plan saved before reads back, validates, has no ranged chunk")
        var ranged = plan
        ranged.chunks[3].rangeStart = 0
        ranged.identity = ranged.computeIdentity()
        let rangedText = String(decoding: try encoder.encode(ranged), as: UTF8.self)
        check(ranged.identity != plan.identity && (try? ranged.validate()) != nil && rangedText.contains("\"rangeStart\":0"),
              "a ranged chunk changes the identity and is saved with its start")
        var zlibRanged = plan
        zlibRanged.chunks[0].rangeStart = 0
        zlibRanged.identity = zlibRanged.computeIdentity()
        var negative = ranged
        negative.chunks[3].rangeStart = -1
        negative.identity = negative.computeIdentity()
        check((try? zlibRanged.validate()) == nil && (try? negative.validate()) == nil, "a ranged chunk must be stored and start at a byte of the file")
    } catch { check(false, "plan identity: \(error)") }

    // Slices against the driver's local server (/stream/...): it answers as the comments say and logs every Range it was asked for
    // (the driver checks them). The body is 100,000 bytes, byte i = i % 251 (more than the 512 bytes URLSession may hold back to sniff a type).
    let stream = BCDHTTPStream()
    defer { stream.close() }
    let body = Data((0..<100_000).map { UInt8($0 % 251) })
    func url(_ name: String) -> URL { URL(string: fx.base + "/stream/" + name)! }
    do {
        let whole = BCDHTTPCollector(limit: 1 << 20)
        try await stream.fetch(URLRequest(url: url("whole")), into: whole)          // dropped at 40,000 once
        let slice = BCDHTTPCollector(limit: 1 << 20)
        try await stream.fetch(URLRequest(url: url("slice")), into: slice, slice: 10_000...59_999)   // dropped after 20,000 once
        let zero = BCDHTTPCollector(limit: 1 << 20)
        try await stream.fetch(URLRequest(url: url("slice-zero-whole")), into: zero, slice: 0...99_999)   // answered with 200
        check(whole.data == body, "no slice: the whole file, a dropped connection continued (the driver checks the Range forms)")
        check(slice.data == body.subdata(in: 10_000..<60_000), "a slice: its bytes only, a dropped connection continued to the slice's last byte")
        check(zero.data == body, "a slice from the first byte answered with the whole file of that size: taken")
    } catch { check(false, "slices: \(error)") }
    await expectError("a slice answered from another byte than asked is refused", isContent(.http(206))) {
        try await stream.fetch(URLRequest(url: url("slice-wrong-start")), into: BCDHTTPCollector(limit: 1 << 20), slice: 10_000...59_999)
    }
    await expectError("a slice further in answered with the whole file (200) is refused", isContent(.http(200))) {
        try await stream.fetch(URLRequest(url: url("slice-whole")), into: BCDHTTPCollector(limit: 1 << 20), slice: 10_000...59_999)
    }
}

// MARK: Return to Castle Wolfenstein, planned from GOG's real replies

func rtcwPhase(_ fx: Fixture) async {
    let dir = URL(fileURLWithPath: fx.fixtures).appendingPathComponent("rtcw")
    let fetched = Box<[String]>([])
    let fetch: BCDGOGContent.Fetch = { url, signedIn in
        fetched.update { $0.append(url.absoluteString) }
        let prefix = "https://cdn.gog.com/content-system/v1/manifests/1441704976/windows/50078739/"
        if url.absoluteString == "https://content-system.gog.com/products/1441704976/os/windows/builds?generation=2" { return try jsonFile(dir.appendingPathComponent("builds.json")) }
        guard !signedIn, url.absoluteString.hasPrefix(prefix) else { throw BCDGOGError.badReply("unexpected address") }
        return try jsonFile(dir.appendingPathComponent(String(url.absoluteString.dropFirst(prefix.count))))
    }
    for (name, preferred) in [("en", ["en-GB"]), ("de", ["de-DE"])] {
        do {
            let key = BCDContentKey(store: "gog", id: "1441704976")
            let build = try await BCDGOGContent.newest("1441704976", fetch: fetch)
            let made = try await BCDGOGContent.plan(key, title: "Return to Castle Wolfenstein", build: build, preferred: preferred, containers: false, fetch: fetch)
            let p = made.plan
            var summary: [String: Any] = [:]
            summary["language"] = made.info.language
            summary["folder"] = p.folder
            summary["depots"] = made.info.depots
            summary["dependencies"] = made.info.dependencies
            summary["support"] = made.info.supportFiles
            summary["directories"] = p.directories
            summary["files"] = p.files.map { [$0.path, $0.size, $0.digest?.hex ?? ""] as [Any] }
            summary["chunks"] = p.chunks.map { [$0.group, $0.rangeStart ?? -1, $0.fetchSize, $0.digest?.hex ?? ""] as [Any] }
            summary["parts"] = p.parts.map { [$0.chunk, $0.chunkOffset, $0.length, $0.file, $0.fileOffset] }
            summary["download"] = p.downloadBytes
            summary["total"] = p.totalBytes
            let data = try JSONSerialization.data(withJSONObject: summary, options: [.sortedKeys])
            emit("SUMMARY-" + name, data.base64EncodedString())
            check(build.generation == 1 && p.chunks.allSatisfy { $0.rangeStart != nil && $0.codec == .stored }, "RTCW (\(name)): a generation-1 plan of stored slices")
        } catch { check(false, "RTCW plan (\(name)): \(error)") }
    }
    check(!fetched.get.contains { $0.contains("a968af32") && $0.contains("70877085") }, "RTCW: one language's depot per plan")
    let quake = URL(fileURLWithPath: fx.fixtures).appendingPathComponent("quake-offering")
    do {
        var builder = try BCDContentPlanBuilder(key: .init(store: "gog", id: "1435828198"), title: "Q", version: "1", folder: "Quake")
        let repo = try BCDGOGContent.parseRepositoryV1(try jsonFile(quake.appendingPathComponent("repository.json")))
        let depot = repo.depots.first { $0.manifest.hasPrefix("999de75e") }!
        let added = try BCDGOGContent.addV1(depot: try jsonFile(quake.appendingPathComponent(depot.manifest)), depot: depot, timestamp: repo.timestamp, to: &builder)
        let kept = BCDGOGContent.addSupportFiles(added.kept, to: &builder)
        let p = try builder.build()
        let summary: [String: Any] = ["files": p.files.map { [$0.path, $0.size] as [Any] }, "support": added.support + kept.skipped, "kept": kept.app]
        emit("SUMMARY-quake", try JSONSerialization.data(withJSONObject: summary, options: [.sortedKeys]).base64EncodedString())
    } catch { check(false, "Quake: The Offering: \(error)") }
}

// MARK: a constructed generation-1 game, downloaded and installed

final class LocalSource: BCDChunkSource, @unchecked Sendable {
    let inner: BCDChunkSource
    let base: String
    init(_ inner: BCDChunkSource, base: String) { self.inner = inner; self.base = base }
    func urls(for chunk: BCDContentChunk) async throws -> [URL] {
        // GOG's main.bin address, pointed at the local server (same path).
        try await inner.urls(for: chunk).map { URL(string: base + $0.path)! }
    }
    func refresh(group: String) async throws { try await inner.refresh(group: group) }
}

struct Game {
    let fx: Fixture
    let work: URL
    let fetched = Box<[String]>([])
    let links = Box<[String]>([])
    let owned = Box<[String]>([])

    init(_ fx: Fixture) { self.fx = fx; work = URL(fileURLWithPath: fx.work) }

    var fetch: BCDGOGContent.Fetch {
        let fx = fx, work = work, fetched = fetched, owned = owned
        return { url, signedIn in
            fetched.update { $0.append((signedIn ? "+" : "-") + url.absoluteString) }
            let text = url.absoluteString
            if text == "https://content-system.gog.com/products/\(fx.product)/os/windows/builds?generation=2", signedIn { return try jsonFile(work.appendingPathComponent("builds.json")) }
            if text == "https://embed.gog.com/user/data/games", signedIn { return ["owned": owned.get] }
            for product in [fx.product, fx.dlc] {
                let prefix = "https://cdn.gog.com/content-system/v1/manifests/\(product)/windows/\(fx.timestamp)/"
                if text.hasPrefix(prefix), !signedIn { return try jsonFile(work.appendingPathComponent("v1/\(product)/" + text.dropFirst(prefix.count))) }
            }
            throw BCDGOGError.badReply("unexpected address")
        }
    }

    func source() -> BCDChunkSource {
        let links = links, timestamp = fx.timestamp
        let v1 = BCDGOGV1ChunkSource(fetch: { product, folder in
            links.update { $0.append(product + "@" + folder) }
            guard folder == timestamp else { throw BCDGOGError.badReply("secure-link") }
            return ["urls": [["url_format": "https://gog-cdn.example{path}", "parameters": ["path": "/v1/\(product)", "expires_at": Date().timeIntervalSince1970 + 3600],
                              "priority": 1, "supports_generation": [1]],
                             ["url_format": "https://gen2-only.example{path}", "parameters": ["path": "/x"], "priority": 9, "supports_generation": [2]]]]
        }, inner: BCDGOGChunkSource(fetch: { _ in throw BCDGOGError.badReply("no generation-2 chunk in this job") }))
        return LocalSource(v1, base: fx.base)
    }

    /// BCDGOG.options(for:)'s rule (BCDGOG.swift is not compiled here; the driver pins its line), with the host checks' timing.
    func options(_ plan: BCDContentPlan, streams: Int = 3) -> BCDContentOptions {
        var options = BCDContentOptions()
        if plan.chunks.contains(where: { $0.rangeStart != nil && $0.digest == nil }) { options.verifyFiles = true }
        options.backoff = 0
        options.streams = streams
        options.durableSeconds = 0
        return options
    }
}

func installPhase(_ fx: Fixture) async {
    let game = Game(fx)
    let drive = game.work.appendingPathComponent("drive_c", isDirectory: true)
    BCDInstallRoot.hostDrive = { $0 == "C" ? drive : nil }
    let root = BCDInstallRoot(drive: "C", folder: "GOG Games")
    do {
        let key = BCDContentKey(store: "gog", id: fx.product)
        let build = try await BCDGOGContent.newest(fx.product, fetch: game.fetch)
        check(build.generation == 1 && build.id == "50001" && build.link.host == "cdn.gog.com", "the newest public generation-1 build (a branch skipped), its link moved to cdn.gog.com")
        let made = try await BCDGOGContent.plan(key, title: "Test Game One", build: build, preferred: ["de-AT", "en-GB"], containers: false, fetch: game.fetch)
        check(made.info.language == "de-DE" && made.info.depots == ["n-1.json", "de-2.json"] && made.info.dependencies == ["DirectX"]
              && made.info.supportFiles == 2 && made.info.dlc == [] && made.info.kind == .install && made.plan.folder == "Test Game One",
              "planned in the device's language; the Mac depot, the English one and the DLC not owned left out; the helper and the clashing app/ file left out")
        let job = game.work.appendingPathComponent("job-install")
        let stage = root.stagingURL!.appendingPathComponent(key.folderName)
        let options = game.options(made.plan)
        check(options.verifyFiles, "a file cut into slices: the whole-file check is on for this job")
        try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: job, source: game.source(), options: options) { _ in }
        let record = try BCDGOGInstall.finish(made.plan, info: made.info, stage: stage, root: root)
        try BCDGOGInstall.save(record, base: game.work.appendingPathComponent("records"))
        check(record.folder == "Test Game One" && record.play == BCDGOGPlayTask(path: "Bin/Game.exe", arguments: "-v1", workingDirectory: "Bin", category: "game")
              && record.buildID == "50001" && record.language == "de-DE" && record.depots == ["n-1.json", "de-2.json"],
              "installed under C:\\GOG Games\\Test Game One; the play task from goggame-<id>.info matched to the files' letter case")
        check(Set(game.links.get) == ["\(fx.product)@\(fx.timestamp)"], "the generation-1 secure link asked for is the game's, for the build's folder")
        let fetched = game.fetched.get.joined(separator: "\n")
        check(!fetched.contains("mac-3.json") && !fetched.contains("en-4.json") && !fetched.contains("dlc-5.json"), "the depots left out are not read")
        emit("FOLDER", drive.appendingPathComponent("GOG Games/Test Game One").path)
    } catch { check(false, "install: \(error)") }
    let log = logged()
    check(log.contains("[gog-content] plan product=\(fx.product) generation=1 kind=install depots=2 dlc=0 files=8 current=0 support-skipped=2 support-app-files=1")
          && log.contains("split-files=1") && log.contains("[gog-content] link generation=1 product=\(fx.product) endpoints=1")
          && log.contains("[gog-install] installed product=\(fx.product)"),
          "the log: product IDs and counts (generation 1, slices, split files)")
}

func repairPhase(_ fx: Fixture) async {
    let game = Game(fx)
    let drive = game.work.appendingPathComponent("drive_c", isDirectory: true)
    BCDInstallRoot.hostDrive = { $0 == "C" ? drive : nil }
    do {
        let records = BCDGOGInstall.loadRecords(game.work.appendingPathComponent("records"))
        guard let record = records[fx.product], let folder = record.hostFolder else { check(false, "repair: no record"); return }
        let key = BCDContentKey(store: "gog", id: fx.product)
        let build = try await BCDGOGContent.newest(fx.product, fetch: game.fetch)
        let read = Box<Int64>(0)
        let current = BCDGOGContent.Current(kind: .repair, folder: folder, oldDepots: record.depots, language: record.language,
                                            report: { done, _ in read.set(done) })
        let made = try await BCDGOGContent.plan(key, title: record.title, build: build, preferred: ["en-US"], containers: false, current: current, fetch: game.fetch)
        check(Set(made.plan.files.map(\.path)) == ["Bin/Game.exe", "Data/copy-b.cfg"] && made.info.language == "de-DE" && made.info.kind == .repair && read.get > 0,
              "a repair reads every file and plans only the changed and the missing one, in the language the game has")
        let stage = game.work.appendingPathComponent("stage-repair")
        try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: game.work.appendingPathComponent("job-repair"), source: game.source(),
                                            options: game.options(made.plan)) { _ in }
        let done = try BCDGOGInstall.finishUpdate(made.plan, info: made.info, stage: stage, record: record)
        check(done.merged.replaced == 1 && done.merged.added == 1 && done.merged.removed == 0, "the two files put back (one replaced, one added)")
    } catch { check(false, "repair: \(error)") }
}

func dlcPhase(_ fx: Fixture) async {
    let game = Game(fx)
    let drive = game.work.appendingPathComponent("drive_c", isDirectory: true)
    BCDInstallRoot.hostDrive = { $0 == "C" ? drive : nil }
    game.owned.set([fx.product, fx.dlc])
    do {
        let base = game.work.appendingPathComponent("records")
        guard var record = BCDGOGInstall.loadRecords(base)[fx.product], let folder = record.hostFolder else { check(false, "dlc: no record"); return }
        let found = try await BCDGOGContent.available(product: fx.product, buildID: record.buildID, depots: record.depots, language: record.language, dlc: true, fetch: game.fetch)
        check(found == BCDGOGContent.Available(buildID: "50001", version: "1.2", dlc: [fx.dlc], newerBuild: false), "the update check finds the owned DLC the game lacks")
        let none = try await BCDGOGContent.available(product: fx.product, buildID: record.buildID, depots: record.depots, language: record.language, dlc: false, fetch: game.fetch)
        check(none == nil, "with DLC off: nothing")
        let key = BCDContentKey(store: "gog", id: fx.product)
        let build = try await BCDGOGContent.newest(fx.product, fetch: game.fetch)
        let current = BCDGOGContent.Current(kind: .update, folder: folder, oldDepots: record.depots, language: record.language)
        let owned = try await BCDGOGContent.owned(fetch: game.fetch, required: true)
        let made = try await BCDGOGContent.plan(key, title: record.title, build: build, preferred: ["de-DE"], containers: false, ownedDLC: owned, current: current, fetch: game.fetch)
        check(made.plan.files.map(\.path) == ["DLC/extra.pak"] && made.info.dlc == [fx.dlc] && made.info.depots == ["n-1.json", "de-2.json", "dlc-5.json"]
              && made.plan.chunks.allSatisfy { $0.group == "v1:\(fx.dlc):\(fx.timestamp)" },
              "an update with the DLC owned: only the DLC's file, from the DLC's main.bin")
        let stage = game.work.appendingPathComponent("stage-dlc")
        try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: game.work.appendingPathComponent("job-dlc"), source: game.source(),
                                            options: game.options(made.plan)) { _ in }
        let done = try BCDGOGInstall.finishUpdate(made.plan, info: made.info, stage: stage, record: record)
        record = done.record
        check(done.merged.added == 1 && record.dlc == [fx.dlc] && Set(game.links.get) == ["\(fx.dlc)@\(fx.timestamp)"],
              "the DLC's file added, the record holds the DLC, the DLC's own secure link was asked for")
        let after = try await BCDGOGContent.available(product: fx.product, buildID: record.buildID, depots: record.depots, language: record.language, dlc: true, fetch: game.fetch)
        check(after == nil, "then the update check finds nothing")
    } catch { check(false, "dlc: \(error)") }
}

func resumePhase(_ fx: Fixture, second: Bool) async {
    let game = Game(fx)
    let drive = game.work.appendingPathComponent("drive_c-resume", isDirectory: true)
    BCDInstallRoot.hostDrive = { $0 == "C" ? drive : nil }
    let root = BCDInstallRoot(drive: "C", folder: "GOG Games")
    do {
        let key = BCDContentKey(store: "gog", id: fx.product)
        let build = try await BCDGOGContent.newest(fx.product, fetch: game.fetch)
        let made = try await BCDGOGContent.plan(key, title: "Test Game One", build: build, preferred: ["de-DE"], containers: false, fetch: game.fetch)
        let job = game.work.appendingPathComponent("job-resume")
        let stage = root.stagingURL!.appendingPathComponent(key.folderName)
        let options = game.options(made.plan, streams: 2)
        if !second {
            let task = Task { try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: job, source: game.source(), options: options) { _ in } }
            var journaled = Set<Int>()
            for _ in 0..<600 where journaled.count < 3 {
                try await Task.sleep(nanoseconds: 50_000_000)
                journaled = BCDContentJournal.load(job.appendingPathComponent("journal"), identity: made.plan.identity, chunkCount: made.plan.chunks.count)
            }
            task.cancel()
            let outcome = await task.result
            if case .failure(let error) = outcome { check(error is CancellationError, "the stopped download ends as a pause (\(error))") } else { check(false, "the download was stopped") }
            journaled = BCDContentJournal.load(job.appendingPathComponent("journal"), identity: made.plan.identity, chunkCount: made.plan.chunks.count)
            check(journaled.count >= 3, "some slices are journaled before the stop (\(journaled.count))")
            for c in journaled.sorted() { emit("JOURNALED", "bytes=\(made.plan.chunks[c].rangeStart!)-\(made.plan.chunks[c].rangeStart! + made.plan.chunks[c].fetchSize - 1)") }
        } else {
            try await BCDContentEngine.download(made.plan, stage: stage, jobFolder: job, source: game.source(), options: options) { _ in }
            let record = try BCDGOGInstall.finish(made.plan, info: made.info, stage: stage, root: root)
            check(record.folder == "Test Game One", "the resumed download is installed")
            emit("FOLDER", drive.appendingPathComponent("GOG Games/Test Game One").path)
        }
    } catch { check(false, "resume (second=\(second)): \(error)") }
}

@main struct Harness {
    static func main() async {
        let arguments = CommandLine.arguments
        let fx = try! JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: arguments[2])))
        BCDContentLog.sink = { line in logLock.withLock { logs.append(line) }; print("LOG " + line); fflush(stdout) }
        switch arguments[1] {
        case "units": await unitsPhase(fx)
        case "engine": await enginePhase(fx)
        case "rtcw": await rtcwPhase(fx)
        case "install": await installPhase(fx)
        case "repair": await repairPhase(fx)
        case "dlc": await dlcPhase(fx)
        case "resume1": await resumePhase(fx, second: false)
        case "resume2": await resumePhase(fx, second: true)
        default: check(false, "unknown phase")
        }
        exit(failures == 0 ? 0 : 1)
    }
}
