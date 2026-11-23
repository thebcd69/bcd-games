// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// Steam for Windows (branch steam-client, milestone M1): the host harness of tests/host/check-steam-desktop.py. It is compiled with
// app/Madeira/SteamDesktop.swift and SteamRuntime.swift and runs the production code on fixtures the Python side makes: a manifest in
// Valve's format, a bootstrapper archive with a stand-in steam.exe, packages of random bytes. No Valve file is here or is fetched.

import Foundation

let fm = FileManager.default
let arguments = CommandLine.arguments
let mode = arguments.count > 1 ? arguments[1] : ""
var failures = 0

func check(_ name: String, _ ok: Bool, _ detail: @autoclosure () -> String = "") {
    print((ok ? "ok   " : "FAIL ") + name + (ok ? "" : "  [" + detail() + "]"))
    if !ok { failures += 1 }
}

func bytes(_ url: URL) -> Data { (try? Data(contentsOf: url)) ?? Data() }
func size(_ url: URL) -> Int64 { ((try? fm.attributesOfItem(atPath: url.path))?[.size] as? NSNumber)?.int64Value ?? -1 }

/// Every file under `folder`, by its path inside it.
func tree(_ folder: URL) -> [String: Data] {
    var out: [String: Data] = [:]
    guard let files = fm.enumerator(at: folder, includingPropertiesForKeys: [.isRegularFileKey]) else { return out }
    let base = folder.standardizedFileURL.path + "/"
    for case let url as URL in files where (try? url.resourceValues(forKeys: [.isRegularFileKey]))?.isRegularFile == true {
        out[String(url.standardizedFileURL.path.dropFirst(base.count))] = bytes(url)
    }
    return out
}

// MARK: - manifest

if mode == "manifest" {
    // manifest <file>: what the reader makes of it, one line per package, for the Python side to compare.
    let data = bytes(URL(fileURLWithPath: arguments[2]))
    do {
        let manifest = try SteamDesktopManifest.parse(data)
        print("version \(manifest.version)")
        for package in manifest.packages {
            print("package \(package.name) \(package.zip.name) \(package.zip.bytes) \(package.zip.sha256) "
                  + (package.compressed.map { "\($0.name) \($0.bytes) \($0.sha256)" } ?? "- - -") + " \(package.bootstrapper ? 1 : 0)")
        }
        print("bootstrapper \(manifest.bootstrapper.name) \(manifest.bootstrapper.zip.name)")
        print("download \(manifest.downloadBytes)")
    } catch SteamDesktopManifest.Failure.malformed(let why) {
        print("refused \(why)")
    }
    exit(0)
}

if mode == "manifest-refuse" {
    // manifest-refuse <folder>: every file in it is a manifest the reader must refuse.
    let folder = URL(fileURLWithPath: arguments[2])
    let names = ((try? fm.contentsOfDirectory(atPath: folder.path)) ?? []).sorted()
    check("there are manifests to refuse", names.count >= 12, "\(names.count)")
    for name in names {
        var refused = false
        do { _ = try SteamDesktopManifest.parse(bytes(folder.appendingPathComponent(name))) }
        catch SteamDesktopManifest.Failure.malformed { refused = true }
        catch {}
        check("refused: \(name)", refused)
    }
    exit(failures == 0 ? 0 : 1)
}

// MARK: - names, profile, registry

if mode == "rules" {
    // The folder and the program.
    check("the client's folder is its own, beside the Dock's",
          SteamDesktop.windowsRoot == "C:\\Program Files (x86)\\Steam Desktop" && SteamDesktop.relativeRoot == "Program Files (x86)/Steam Desktop"
          && SteamRuntimeFiles.relativeRoot == "Program Files (x86)/Steam" && SteamDesktop.windowsRoot == SteamRuntimeFiles.desktopRoot)
    check("the Dock's folder is told apart from it",
          SteamDesktop.isDockPath(relative: "Program Files (x86)/Steam") && SteamDesktop.isDockPath(relative: "program files (x86)/steam/package/x")
          && !SteamDesktop.isDockPath(relative: SteamDesktop.relativeRoot) && !SteamDesktop.isDockPath(relative: SteamDesktop.relativeRoot + "/steam.exe"))
    check("steam.exe of that folder is the client, in any spelling; the Dock's and any other are not",
          SteamDesktop.isClient(windowsPath: "C:\\Program Files (x86)\\Steam Desktop\\steam.exe")
          && SteamDesktop.isClient(windowsPath: "c:/program files (x86)/steam desktop/STEAM.EXE")
          && !SteamDesktop.isClient(windowsPath: "C:\\Program Files (x86)\\Steam\\steam.exe")
          && !SteamDesktop.isClient(windowsPath: "C:\\Program Files (x86)\\Steam Desktop\\steamwebhelper.exe")
          && !SteamDesktop.isClient(windowsPath: "D:\\Program Files (x86)\\Steam Desktop\\steam.exe"))
    // The command line.
    let flags = "-nofriendsui -noverifyfiles -cef-disable-gpu -cef-disable-gpu-compositing -cef-disable-breakpad -cef-disable-hang-timeouts -noshaders -vrdisable"
    check("the default arguments are the plan's eight", SteamDesktop.defaultArguments == flags)
    check("an entry without arguments takes them; the player's own text stays",
          SteamDesktop.arguments(entry: "") == flags && SteamDesktop.arguments(entry: "   ") == flags
          && SteamDesktop.arguments(entry: flags + " -no-dwrite") == flags + " -no-dwrite"
          && SteamDesktop.arguments(entry: "-silent") == "-silent")
    check("web views are software while -cef-disable-gpu is among the arguments",
          SteamDesktop.softwareWebViews(flags) && !SteamDesktop.softwareWebViews("-nofriendsui -cef-disable-gpu-compositing")
          && SteamDesktop.softwareWebViews("-CEF-DISABLE-GPU") && !SteamDesktop.softwareWebViews(""))
    check("the log names the arguments as default, or only the known options among the player's own, never what was typed",
          SteamDesktop.argumentsForLog(flags) == "default"
          && SteamDesktop.argumentsForLog(flags + " -no-dwrite") == "own(" + flags + " -no-dwrite; 0 other)"
          && SteamDesktop.argumentsForLog("-silent -login somebody secret") == "own(-silent; 3 other)"
          && SteamDesktop.argumentsForLog("word") == "own(-; 1 other)" && !SteamDesktop.argumentsForLog("-login somebody secret").contains("somebody"))
    check("the switch is off unless set, and the packages choice on unless cleared",
          !UserDefaults.standard.bool(forKey: SteamDesktop.enabledKey) && SteamDesktop.fetchesPackages)
    check("the session variable", SteamDesktop.sessionVariable == "MADEIRA_STEAM_DESKTOP_SESSION")

    // The session's settings.
    let wanted: [(String, String)] = [
        ("pool", "1152"), ("pool-split", "1"), ("pool-low", "1"),
        ("swap-mb", "2048"), ("swap-mode", "2"), ("swap-images", "1"),
        ("avail-phys", "1"), ("avail-phys-cef-mb", "900"),
        ("env.MADEIRA_X64_IMAGE_NOCOPY", "1"), ("env.MADEIRA_SC_PA_POOLS", "2"),
        ("env.MADEIRA_CEF_POOL_IMAGES", "steamwebhelper.exe"),
        ("env.MADEIRA_BG_QOS_PROCS", "steam.exe;steamwebhelper.exe;steamservice.exe"),
        // Not in the plan's list: a session that starts 64-bit and then runs 32-bit programs needs the GDI table as a section.
        ("env.MADEIRA_GDI_SHARED_SECTION", "1"),
        // Build 690 (Breakneck, 32-bit, refused its window with 6797 MB free): the EA app's floor of 2 GB left over.
        ("env.MADEIRA_WOW_MIN_FREE_GB", "2"),
        // Build 692 (no aligned slot free three minutes in): one window held from the start for the client's games, and the
        // engine's cap without which the held slot broke the web helper in 691.
        ("env.MADEIRA_WOW_PLACEHOLDER_SLOTS", "1"),
        ("env.MADEIRA_WOW_HOLD_FOR", "C:\\Program Files (x86)\\Steam Desktop\\steamapps\\common\\"),
        ("env.MADEIRA_WOW_HIGH_SIDE_CAP", "1"),
    ]
    let pairs = SteamDesktopProfile.pairs(SteamDesktopProfile.lines.joined(separator: "\n"))
    check("the session's settings: the plan's twelve, the GDI table as a section, the 2 GB floor, and a held window with the cap",
          pairs.count == 17 && pairs.count == wanted.count && zip(pairs, wanted).allSatisfy { $0.0.key == $0.1.0 && $0.0.value == $0.1.1 }, "\(pairs)")
    // Build 691 (22:32): the held window without the cap broke the web helper's pools, and Steam never opened.
    let heldKeys = SteamDesktopProfile.keys.filter { $0.hasPrefix("env.MADEIRA_WOW_PLACEHOLDER") || $0.hasPrefix("env.MADEIRA_WOW_HOLD_FOR") }
    check("a held 32-bit window comes only with the engine's cap (691 without it: the web helper could not reserve its pools)",
          heldKeys.isEmpty || pairs.contains { $0.key == "env.MADEIRA_WOW_HIGH_SIDE_CAP" && $0.value == "1" }, "\(SteamDesktopProfile.keys)")
    check("the held window's folder is the client's own steamapps\\common, as the engine reads it (backslashes, the trailing one)",
          pairs.first { $0.key == "env.MADEIRA_WOW_HOLD_FOR" }?.value == SteamDesktop.windowsRoot + "\\steamapps\\common\\"
          && "C:\\Program Files (x86)\\Steam Desktop\\steamapps\\common\\Breakneck\\Breakneck.exe".lowercased()
            .hasPrefix((SteamDesktop.windowsRoot + "\\steamapps\\common\\").lowercased()))
    check("pool-above-window is not among them (the session's pool line counts by itself)",
          !SteamDesktopProfile.keys.contains("pool-above-window") && !SteamDesktopProfile.keys.contains("env.MADEIRA_STEAM_IDENTITY"))
    let plain = SteamDesktopProfile.sessionFile(own: nil)
    check("without lines of the entry's own the session file is BCD's block",
          plain == SteamDesktopProfile.opening + "\n" + SteamDesktopProfile.lines.joined(separator: "\n") + "\n"
          && SteamDesktopProfile.sessionFile(own: "  \n") == plain)
    let own = "# mine\navail-phys-cef-mb = 300\nenv.MADEIRA_JITLESS = 0\npool = 896"
    let merged = SteamDesktopProfile.sessionFile(own: own)
    check("the entry's own lines come after BCD's, whole and unchanged",
          merged.hasPrefix(plain) && merged.hasSuffix(SteamDesktopProfile.ownOpening + "\n" + own + "\n"))
    // The engine's rule (build/madeira_cfg.h madeira_cfg__scan) and the app's (MadeiraConfig.parse): the last line of a key wins.
    var last: [String: String] = [:]
    for (key, value) in SteamDesktopProfile.pairs(merged) { last[key] = value }
    check("so a key the entry sets wins, and every other key keeps BCD's value",
          last["avail-phys-cef-mb"] == "300" && last["pool"] == "896" && last["env.MADEIRA_JITLESS"] == "0"
          && last["pool-split"] == "1" && last["swap-mb"] == "2048" && last["env.MADEIRA_SC_PA_POOLS"] == "2")
    let effective = SteamDesktopProfile.effective(sessionFile: merged)
    check("the log line names what is in effect and which keys the entry changed",
          effective.changed == ["pool", "avail-phys-cef-mb"]
          && SteamDesktopProfile.effectiveLine(sessionFile: merged).contains("pool=896 pool-split=1")
          && SteamDesktopProfile.effectiveLine(sessionFile: merged).hasSuffix("(the entry's own lines set: pool, avail-phys-cef-mb)")
          && SteamDesktopProfile.effectiveLine(sessionFile: plain).hasSuffix("(all BCD's)")
          && SteamDesktopProfile.effectiveLine(sessionFile: plain).hasPrefix("[steam-desktop] settings in effect: pool=1152 "))
    exit(failures == 0 ? 0 : 1)
}

// MARK: - BCD Dock's DLC choices in the client (2026-11-22)

if mode == "dlc" {
    func d(_ s: String) -> Data { Data(s.utf8) }
    func s(_ x: Data?) -> String { x.map { String(decoding: $0, as: UTF8.self) } ?? "<nil>" }
    func mounted(_ x: Data) -> String? {
        guard var parser = try? SteamKeyValues(x), let root = try? parser.read() else { return nil }
        return root["AppState"]?["MountedConfig"]?["DisabledDLC"]?.string
    }
    func parses(_ x: Data) -> Bool { (try? { var p = try SteamKeyValues(x); return try p.read() }()) != nil }
    // X-Plane 12 as BCD's AppManifestWriter writes it, the game alone (no DLC chosen), with one installed DLC depot for the test.
    let bcd = "\"AppState\"\n{\n\t\"appid\"\t\t\"2014780\"\n\t\"universe\"\t\t\"1\"\n\t\"name\"\t\t\"X-Plane 12\"\n\t\"StateFlags\"\t\t\"4\"\n"
        + "\t\"InstalledDepots\"\n\t{\n\t\t\"2014781\"\n\t\t{\n\t\t\t\"manifest\"\t\t\"123\"\n\t\t\t\"size\"\t\t\"10\"\n\t\t}\n"
        + "\t\t\"2126190\"\n\t\t{\n\t\t\t\"manifest\"\t\t\"456\"\n\t\t\t\"size\"\t\t\"20\"\n\t\t\t\"dlcappid\"\t\t\"2126105\"\n\t\t}\n\t}\n"
        + "\t\"UserConfig\"\n\t{\n\t\t\"language\"\t\t\"english\"\n\t}\n\t\"MountedConfig\"\n\t{\n\t\t\"language\"\t\t\"english\"\n\t}\n}"
    let owned: Set<UInt32> = [2126100, 2126101, 2126102, 2126103, 2126104, 2126105]
    let installed = SteamDesktopDLC.installed(d(bcd))
    check("the record's installed DLC are read from InstalledDepots' dlcappid", installed == [2126105], "\(installed)")
    let want = SteamDesktopDLC.disabled(owned: owned, installed: installed, current: SteamDesktopDLC.listed(d(bcd)), wanted: nil)
    check("the list: every owned DLC with files that BCD did not install", want == [2126100, 2126101, 2126102, 2126103, 2126104], "\(want)")
    check("one the player unticked in the client stays, an installed one never is listed",
          SteamDesktopDLC.disabled(owned: [1, 2], installed: [2, 9], current: [7, 9], wanted: nil) == [1, 7])
    check("a DLC chosen in BCD and not downloaded yet is left on, so the client may bring it down too (downloads are shared)",
          SteamDesktopDLC.disabled(owned: [1, 2, 3], installed: [], current: [3], wanted: [2, 3]) == [1]
          && SteamDesktopDLC.disabled(owned: [1, 2], installed: [], current: [], wanted: []) == [1, 2])
    // After the session (the other way): BCD's choice takes what the player did in the client.
    check("nothing done in the client: BCD's choice is not written",
          SteamDesktopDLC.adopted(wanted: nil, installedBefore: [5], written: [1, 2], installedAfter: [5], listedAfter: [1, 2]) == nil
          && SteamDesktopDLC.adopted(wanted: [5, 6], installedBefore: [5], written: [1], installedAfter: [5], listedAfter: [1]) == nil)
    check("a DLC ticked in the client (it left the list) joins BCD's choice, even while it still downloads",
          SteamDesktopDLC.adopted(wanted: nil, installedBefore: [5], written: [1, 2], installedAfter: [5], listedAfter: [2]) == [1, 5])
    check("one the client installed joins it, one the client took out leaves it",
          SteamDesktopDLC.adopted(wanted: [5], installedBefore: [5], written: [], installedAfter: [7], listedAfter: []) == [7])
    check("one unticked in the client (it joined the list) leaves BCD's choice, a pending BCD choice stays",
          SteamDesktopDLC.adopted(wanted: [5, 6, 8], installedBefore: [5, 6], written: [1], installedAfter: [5], listedAfter: [1, 6])
              == [5, 8])
    check("the two directions agree: what the client did is what the next session's list leaves on",
          SteamDesktopDLC.disabled(owned: [1, 2], installed: [5], current: [2],
                                   wanted: SteamDesktopDLC.adopted(wanted: nil, installedBefore: [5], written: [1, 2], installedAfter: [5],
                                                                   listedAfter: [2])) == [2])
    let edited = SteamDesktopDLC.record(d(bcd), disabled: want)
    let line = "\n\t\t\"DisabledDLC\"\t\t\"2126100,2126101,2126102,2126103,2126104\""
    check("written under UserConfig and MountedConfig, as Steam writes it when DLC is unticked",
          edited.map(SteamDesktopDLC.listed) == want && edited.flatMap(mounted) == "2126100,2126101,2126102,2126103,2126104", s(edited))
    check("every other byte stays: taking the two lines out gives the record back", s(edited).replacingOccurrences(of: line, with: "") == bcd
          && s(edited).components(separatedBy: line).count == 3, s(edited))
    check("a record that already says so is not written again", edited.flatMap { SteamDesktopDLC.record($0, disabled: want) } == nil)
    let changed = edited.flatMap { SteamDesktopDLC.record($0, disabled: [2126100]) }
    check("a different list changes only the value", s(changed) == bcd.replacingOccurrences(of: "\t\t\"language\"\t\t\"english\"\n\t}",
          with: "\t\t\"DisabledDLC\"\t\t\"2126100\"\n\t\t\"language\"\t\t\"english\"\n\t}").replacingOccurrences(of: "{\n\t\t\"DisabledDLC\"", with: "{\n\t\t\"DisabledDLC\""),
          s(changed))
    let cleared = edited.flatMap { SteamDesktopDLC.record($0, disabled: []) }
    check("an empty list takes the line out again, and the record is the one it was", s(cleared) == bcd, s(cleared))
    check("nothing to disable and no line: nothing is written", SteamDesktopDLC.record(d(bcd), disabled: []) == nil)
    // The same as Valve's client writes it: CRLF, lower-case keys, the list already there, a comment.
    let valve = "// written by Steam\r\n\"AppState\"\r\n{\r\n\t\"appid\"\t\t\"377160\"\r\n\t\"userconfig\"\r\n\t{\r\n\t\t\"language\"\t\t\"english\"\r\n"
        + "\t\t\"DisabledDLC\"\t\t\"480630,435881\"\r\n\t}\r\n\t\"mountedconfig\"\r\n\t{\r\n\t\t\"language\"\t\t\"english\"\r\n"
        + "\t\t\"DisabledDLC\"\t\t\"480630,435881\"\r\n\t}\r\n}\r\n"
    check("Valve's spelling: lower-case keys, CRLF, a comment; the player's own list is read",
          SteamDesktopDLC.listed(d(valve)) == [480630, 435881])
    let valveMore = SteamDesktopDLC.record(d(valve), disabled: [435881, 480630, 490650])
    check("and only its value changes there", s(valveMore) == valve.replacingOccurrences(of: "\"480630,435881\"", with: "\"435881,480630,490650\""),
          s(valveMore))
    let valveOff = SteamDesktopDLC.record(d(valve), disabled: [])
    check("an empty list there takes both lines out with their CRLF", s(valveOff) == valve.replacingOccurrences(
          of: "\r\n\t\t\"DisabledDLC\"\t\t\"480630,435881\"", with: "") && valveOff.map(parses) == true, s(valveOff))
    // A record without the two blocks (an older writer): both are added before AppState's closing brace.
    let bare = "\"AppState\"\n{\n\t\"appid\"\t\t\"620\"\n\t\"name\"\t\t\"Portal 2\"\n}\n"
    let added = SteamDesktopDLC.record(d(bare), disabled: [323180])
    check("missing UserConfig and MountedConfig are added, the record still reads, the rest unchanged",
          added.map(SteamDesktopDLC.listed) == [323180] && added.flatMap(mounted) == "323180" && added.map(parses) == true
          && s(added).hasPrefix("\"AppState\"\n{\n\t\"appid\"\t\t\"620\"\n\t\"name\"\t\t\"Portal 2\"\n\t\"") && s(added).hasSuffix("\n\t}\n}\n"), s(added))
    check("what is not an AppState record, or is cut short, is left alone",
          SteamDesktopDLC.record(d("\"libraryfolders\"\n{\n}\n"), disabled: [1]) == nil
          && SteamDesktopDLC.record(d("\"AppState\"\n{\n\t\"appid\"\t\t\"1"), disabled: [1]) == nil
          && SteamDesktopDLC.record(d(""), disabled: [1]) == nil)
    check("a quoted brace or an escaped quote inside a value does not confuse the block search",
          SteamDesktopDLC.record(d("\"AppState\"\n{\n\t\"name\"\t\t\"A {weird} \\\"name\\\"\"\n\t\"UserConfig\"\n\t{\n\t}\n}\n"), disabled: [5])
              .map(SteamDesktopDLC.listed) == [5])
    check("the lookups before a session take 30 s at most", SteamDesktopDLC.lookupSeconds == 30)
    exit(failures == 0 ? 0 : 1)
}

// MARK: - BCD Dock's games in the client's library (2026-11-22)

if mode == "libraries" {
    func paths(_ data: Data?) -> Set<String> { (try? SteamDesktopLibraries.listedPaths(data)) ?? [] }
    func parse(_ data: Data?) -> SteamValue? {
        guard let data, var parser = try? SteamKeyValues(data) else { return nil }
        return try? parser.read()
    }
    let own = "c:\\program files (x86)\\steam desktop", dock = "c:\\program files (x86)\\steam", drive = "c:\\external\\ssd\\steamlibrary"
    let drives = ["External/SSD/SteamLibrary"]

    // No list yet (before the client's first start): BCD makes it, the client's own folder first.
    let made = try? SteamDesktopLibraries.list(nil, share: true, drives: drives)
    check("with no list yet, BCD makes one: the client's folder, BCD Dock's library and the drive's",
          made.map { paths($0) } == [own, dock, drive], "\(String(decoding: made.flatMap { $0 } ?? Data(), as: UTF8.self))")
    let made0 = parse(made.flatMap { $0 })
    check("the client's own folder is entry 0", made0?["libraryfolders"]?["0"]?["path"]?.string?.lowercased() == own)

    // The list as Valve's client writes it: its own folder with its fields and apps.
    let valve = Data("""
    "libraryfolders"
    {
    \t"0"
    \t{
    \t\t"path"\t\t"C:\\\\Program Files (x86)\\\\Steam Desktop"
    \t\t"label"\t\t""
    \t\t"contentid"\t\t"7302518946283746"
    \t\t"totalsize"\t\t"0"
    \t\t"apps"
    \t\t{
    \t\t\t"2217630"\t\t"107845632"
    \t\t}
    \t}
    }
    """.utf8)
    let shared = try? SteamDesktopLibraries.list(valve, share: true, drives: [])
    let sharedValues = parse(shared.flatMap { $0 })
    check("Valve's list gets BCD Dock's library, and keeps its own entry's fields and apps",
          paths(shared.flatMap { $0 }) == [own, dock]
          && sharedValues?["libraryfolders"]?["0"]?["contentid"]?.string == "7302518946283746"
          && sharedValues?["libraryfolders"]?["0"]?["apps"]?["2217630"]?.string == "107845632")
    check("a list that already has every folder is left as Steam wrote it (not reformatted)",
          (try? SteamDesktopLibraries.list(shared.flatMap { $0 }, share: true, drives: [])) == .some(nil))
    let upper = Data(String(decoding: shared.flatMap { $0 } ?? Data(), as: UTF8.self)
        .replacingOccurrences(of: "C:\\\\Program Files (x86)\\\\Steam\"", with: "C:\\\\PROGRAM FILES (X86)\\\\STEAM\"").utf8)
    check("a folder Steam spelled in another case counts as listed",
          paths(upper).contains(dock) && (try? SteamDesktopLibraries.list(upper, share: true, drives: [])) == .some(nil))

    // The switch off: BCD Dock's folders come out again, the client's own stays.
    let withDrive = (try? SteamDesktopLibraries.list(valve, share: true, drives: drives)).flatMap { $0 }
    let unshared = (try? SteamDesktopLibraries.list(withDrive, share: false, drives: drives)).flatMap { $0 }
    let unsharedValues = parse(unshared)
    check("the switch off takes BCD Dock's library and the drive's out, and keeps the client's own entry whole",
          paths(unshared) == [own] && unsharedValues?["libraryfolders"]?["0"]?["contentid"]?.string == "7302518946283746")
    check("with the switch off and nothing of BCD Dock's listed, the list is not touched; nor without a list",
          (try? SteamDesktopLibraries.list(valve, share: false, drives: drives)) == .some(nil)
          && (try? SteamDesktopLibraries.list(nil, share: false, drives: drives)) == .some(nil))
    check("a list that is not Valve's format is refused, not overwritten",
          (try? SteamDesktopLibraries.list(Data("\"something\" { }".utf8), share: true, drives: [])) == nil)

    // The copy of BCD Dock's library files: the list, the marker and the appmanifests; never a game's own files.
    check("the copy keeps the list, the library marker and the appmanifests, and nothing else",
          ["libraryfolders.vdf", "libraryfolder.vdf", "appmanifest_2014780.acf", "APPMANIFEST_7.ACF"].allSatisfy(SteamDesktopLibraries.keepsInBackup)
          && !["common", "downloading", "appmanifest_1.acf.tmp", "workshop", "temp", "shadercache"].contains(where: SteamDesktopLibraries.keepsInBackup))
    check("the client's list and the copy live in the client's own folder, never in BCD Dock's",
          SteamDesktopLibraries.listRelative == "Program Files (x86)/Steam Desktop/steamapps/libraryfolders.vdf"
          && SteamDesktopLibraries.backupFolder.hasPrefix("Program Files (x86)/Steam Desktop/")
          && SteamDesktopLibraries.dockLibraries(drives: ["Program Files (x86)/STEAM", ""]) == ["Program Files (x86)/Steam"])
    // The other way (2026-11-22): BCD Dock's own list gets the client's library, so games installed in Steam for Windows show in
    // BCD's library and start through BCD Dock.
    func dockPaths(_ x: Data?) -> [String] {
        guard let x, var parser = try? SteamKeyValues(x), let root = try? parser.read() else { return [] }
        return (root["libraryfolders"]?.fields ?? [:]).sorted { $0.key < $1.key }.compactMap { $0.value["path"]?.string }
    }
    let dockFresh = try? SteamDesktopLibraries.dockList(nil, share: true)
    check("BCD Dock's list made from nothing: its own folder first, then the client's",
          dockPaths(dockFresh ?? nil) == ["C:\\Program Files (x86)\\Steam", "C:\\Program Files (x86)\\Steam Desktop"], "\(dockPaths(dockFresh ?? nil))")
    let dockWithDrive = (try? ExternalSteamLibraries.add(paths: ["Program Files (x86)/Steam", "External/USB/SteamLibrary"], to: nil)) ?? Data()
    let dockAdded = (try? SteamDesktopLibraries.dockList(dockWithDrive, share: true)) ?? nil
    check("a list with a drive keeps it and gains the client's library",
          Set(dockPaths(dockAdded)) == ["C:\\Program Files (x86)\\Steam", "C:\\External\\USB\\SteamLibrary", "C:\\Program Files (x86)\\Steam Desktop"],
          "\(dockPaths(dockAdded))")
    check("a list that already has it is not written again", dockAdded.flatMap { try? SteamDesktopLibraries.dockList($0, share: true) } == nil)
    let dockRemoved = dockAdded.flatMap { (try? SteamDesktopLibraries.dockList($0, share: false)) ?? nil }
    check("the switch off takes out only the client's library",
          Set(dockPaths(dockRemoved)) == ["C:\\Program Files (x86)\\Steam", "C:\\External\\USB\\SteamLibrary"], "\(dockPaths(dockRemoved))")
    check("off with nothing to take out, or without the file: nothing is written",
          ((try? SteamDesktopLibraries.dockList(dockWithDrive, share: false)) ?? nil) == nil
          && ((try? SteamDesktopLibraries.dockList(nil, share: false)) ?? nil) == nil)
    check("BCD Dock's list is its own steamapps\\libraryfolders.vdf, the file BCD already writes for the drives",
          SteamDesktopLibraries.dockListRelative == "Program Files (x86)/Steam/steamapps/libraryfolders.vdf")
    exit(failures == 0 ? 0 : 1)
}

if mode == "registry" {
    let root = "C:\\\\Program Files (x86)\\\\Steam Desktop"
    let dock = "C:\\\\Program Files (x86)\\\\Steam"
    // A prefix as the Dock's setup leaves it, with other values of Steam's and an unrelated key.
    let user = "WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000\n\n"
        + "[Software\\\\Keep] 1700000000\n#time=1dc000000000000\n\"Unrelated\"=\"preserved\"\n\n"
        + "[Software\\\\Valve\\\\Steam] 1700000000\n#time=1dc000000000000\n\"Language\"=\"english\"\n"
        + "\"SteamPath\"=\"\(dock)\"\n\"SteamExe\"=\"\(dock)\\\\steam.exe\"\n\"H264HWAccel\"=dword:00000001\n\n"
        + "[Software\\\\Valve\\\\Steam\\\\ActiveProcess] 1700000000\n#time=1dc000000000000\n\"pid\"=dword:00000000\n"
        + "\"SteamClientDll\"=\"\(dock)\\\\steamclient.dll\"\n\"SteamClientDll64\"=\"\(dock)\\\\steamclient64.dll\"\n\n"
        + "[Software\\\\Wine\\\\Tail] 1700000000\n\"Last\"=\"kept\"\n"
    let machine = "WINE REGISTRY Version 2\n;; All keys relative to \\\\Machine\n\n"
        + "[Software\\\\Valve\\\\Steam] 1700000000\n\"InstallPath\"=\"\(dock)\"\n\n"
        + "[Software\\\\Wow6432Node\\\\Valve\\\\Steam] 1700000000\n\"InstallPath\"=\"\(dock)\"\n"
    do {
        let seeded = try SteamDesktopRegistry.seeded(user, machine: false, softwareWebViews: true)
        let text = seeded.text
        check("the user hive: the four discovery values name the client's own folder",
              text.contains("\"SteamPath\"=\"\(root)\"\n") && text.contains("\"SteamExe\"=\"\(root)\\\\steam.exe\"\n")
              && text.contains("\"SteamClientDll\"=\"\(root)\\\\steamclient.dll\"\n")
              && text.contains("\"SteamClientDll64\"=\"\(root)\\\\steamclient64.dll\"\n") && !text.contains("\"\(dock)\"")
              && !text.contains("\(dock)\\\\steam"))
        check("the three web view values are 0 (one was 1, two were not there)",
              SteamDesktopRegistry.webViewValues == ["GPUAccelWebViewsV3", "H264HWAccel", "SmoothScrollWebViews"]
              && SteamDesktopRegistry.webViewValues.allSatisfy { text.contains("\"\($0)\"=dword:00000000\n") }
              && !text.contains("dword:00000001"))
        check("seven lines changed or added, counted", seeded.changed == 7, "\(seeded.changed)")
        check("everything else is as it was: other values, other keys, the lines' order",
              text.contains("\"Language\"=\"english\"\n") && text.contains("\"pid\"=dword:00000000\n")
              && text.contains("[Software\\\\Keep] 1700000000\n#time=1dc000000000000\n\"Unrelated\"=\"preserved\"\n")
              && text.hasSuffix("[Software\\\\Wine\\\\Tail] 1700000000\n\"Last\"=\"kept\"\n")
              && text.components(separatedBy: "\n").count == user.components(separatedBy: "\n").count + 2)
        // The two added values sit inside Steam's section, before the blank line that closes it.
        let lines = text.components(separatedBy: "\n")
        let start = lines.firstIndex { $0.hasPrefix("[Software\\\\Valve\\\\Steam]") }!
        let next = lines[(start + 1)...].firstIndex { $0.hasPrefix("[") }!
        let section = Array(lines[start..<next])
        check("added values are inside the section, above its closing blank line",
              section.contains("\"GPUAccelWebViewsV3\"=dword:00000000") && section.contains("\"SmoothScrollWebViews\"=dword:00000000")
              && section.last == "" && section.dropLast().allSatisfy { !$0.isEmpty })
        let again = try SteamDesktopRegistry.seeded(text, machine: false, softwareWebViews: true)
        check("a second run changes nothing", again.text == text && again.changed == 0)
        let left = try SteamDesktopRegistry.seeded(user, machine: false, softwareWebViews: false)
        check("without software web views the three values are left as they are",
              left.changed == 4 && left.text.contains("\"H264HWAccel\"=dword:00000001\n") && !left.text.contains("GPUAccelWebViewsV3"))
        let system = try SteamDesktopRegistry.seeded(machine, machine: true, softwareWebViews: true)
        check("the machine hive: InstallPath in both registry views, nothing else",
              system.changed == 2 && system.text.components(separatedBy: "\"InstallPath\"=\"\(root)\"\n").count == 3
              && !system.text.contains("GPUAccel") && !system.text.contains("SteamPath"))
        let empty = try SteamDesktopRegistry.seeded("WINE REGISTRY Version 2\n", machine: false, softwareWebViews: true)
        check("a hive without the sections gets them", empty.changed == 7 && empty.text.contains("[Software\\\\Valve\\\\Steam]\n\"SteamPath\"=\"\(root)\"\n")
              && empty.text.contains("[Software\\\\Valve\\\\Steam\\\\ActiveProcess]\n"))
        let lower = user.replacingOccurrences(of: "[Software\\\\Valve\\\\Steam]", with: "[software\\\\valve\\\\steam]")
            .replacingOccurrences(of: "\"SteamPath\"=\"\(dock)\"", with: "\"steampath\"=\"c:/program files (x86)/steam\"")
        let cased = try SteamDesktopRegistry.seeded(lower, machine: false, softwareWebViews: true)
        check("a section or value Steam wrote in lower case is the same section and value",
              cased.changed == 7 && !cased.text.contains("steampath") && cased.text.components(separatedBy: "Valve\\\\Steam]").count == 1
              && cased.text.components(separatedBy: "valve\\\\steam]").count == 2)
    } catch {
        check("the hives are seeded", false, "\(error)")
    }
    do { _ = try SteamDesktopRegistry.seeded("garbage", machine: false, softwareWebViews: true); check("a file that is no registry is refused", false) }
    catch SteamDesktopRegistry.Failure.notARegistryFile { check("a file that is no registry is refused", true) }
    catch { check("a file that is no registry is refused", false, "\(error)") }
    let twice = user + "\n[Software\\\\Valve\\\\Steam] 1700000001\n\"SteamPath\"=\"x\"\n"
    do { _ = try SteamDesktopRegistry.seeded(twice, machine: false, softwareWebViews: true); check("a hive with the section twice is left alone", false) }
    catch SteamDesktopRegistry.Failure.conflict { check("a hive with the section twice is left alone", true) }
    catch { check("a hive with the section twice is left alone", false, "\(error)") }

    // On files: the backup, the words, and BCD Dock's own check afterwards.
    let prefix = fm.temporaryDirectory.appendingPathComponent("steam-desktop-registry-" + UUID().uuidString)
    defer { try? fm.removeItem(at: prefix) }
    let userURL = prefix.appendingPathComponent("user.reg"), systemURL = prefix.appendingPathComponent("system.reg")
    let dockFolder = prefix.appendingPathComponent("drive_c/" + SteamRuntimeFiles.relativeRoot)
    try! fm.createDirectory(at: dockFolder, withIntermediateDirectories: true)
    try! Data("client".utf8).write(to: dockFolder.appendingPathComponent("steamclient64.dll"))
    try! Data(user.utf8).write(to: userURL)
    try! Data(machine.utf8).write(to: systemURL)
    let running = SteamDesktopRegistry.seed(prefix: prefix, softwareWebViews: true, sessionRunning: true)
    check("while a session runs nothing is written", running.state == ["skipped"] && bytes(userURL) == Data(user.utf8))
    let first = SteamDesktopRegistry.seed(prefix: prefix, softwareWebViews: true, sessionRunning: false)
    check("both hives written, with the count of values, and the log line says so",
          first.state == ["system.reg=written(values 2)", "user.reg=written(values 7)"]
          && first.line == "[steam-desktop] registry: system.reg=written(values 2) user.reg=written(values 7) web-views=software", first.line)
    check("each hive's copy from before is kept",
          bytes(userURL.appendingPathExtension("steam-desktop-bak")) == Data(user.utf8)
          && bytes(systemURL.appendingPathExtension("steam-desktop-bak")) == Data(machine.utf8))
    let second = SteamDesktopRegistry.seed(prefix: prefix, softwareWebViews: true, sessionRunning: false)
    check("a second run writes nothing", second.state == ["system.reg=ok", "user.reg=ok"])
    // Steam itself then writes its values in its own spelling; the backup stays the first one.
    let steamWrote = (try! String(contentsOf: userURL, encoding: .utf8))
        .replacingOccurrences(of: "\"SteamPath\"=\"\(root)\"", with: "\"SteamPath\"=\"c:/program files (x86)/steam desktop\"")
        .replacingOccurrences(of: "\"SteamExe\"=\"\(root)\\\\steam.exe\"", with: "\"SteamExe\"=\"c:/program files (x86)/steam desktop/steam.exe\"")
    try! Data(steamWrote.utf8).write(to: userURL)
    // A Dock start afterwards: BCD Dock's own check (SteamRuntimeFiles.repairRegistry, before every Dock start) puts every value back.
    var dockLines: [String] = []
    let repaired = SteamRuntimeFiles.repairRegistry(prefix: prefix, log: { dockLines.append($0) })
    let userNow = (try? String(contentsOf: userURL, encoding: .utf8)) ?? "", systemNow = (try? String(contentsOf: systemURL, encoding: .utf8)) ?? ""
    check("BCD Dock's check before its next start puts all six discovery values back to the Dock's folder",
          repaired == ["system.reg=put-back(sections 0, values 0, desktop-client 2)", "user.reg=put-back(sections 0, values 0, desktop-client 4)"]
          && !userNow.lowercased().contains("steam desktop") && !systemNow.lowercased().contains("steam desktop")
          && userNow.contains("\"SteamPath\"=\"\(dock)\"\n") && userNow.contains("\"SteamClientDll64\"=\"\(dock)\\\\steamclient64.dll\"\n")
          && systemNow.components(separatedBy: "\"InstallPath\"=\"\(dock)\"\n").count == 3, "\(repaired)")
    check("and leaves the web view values and everything else",
          userNow.contains("\"GPUAccelWebViewsV3\"=dword:00000000") && userNow.contains("\"Language\"=\"english\"")
          && userNow.contains("\"Unrelated\"=\"preserved\"") && SteamRuntimeFiles.repairRegistry(prefix: prefix, log: { _ in }) == ["system.reg=ok", "user.reg=ok"])
    // And a Steam for Windows start after that Dock start writes its own again.
    let third = SteamDesktopRegistry.seed(prefix: prefix, softwareWebViews: true, sessionRunning: false)
    check("the next Steam for Windows start points them at its folder again",
          third.state == ["system.reg=written(values 2)", "user.reg=written(values 4)"]
          && bytes(userURL.appendingPathExtension("steam-desktop-bak")) == Data(user.utf8), "\(third.state)")
    // BCD Dock's client download or switch after a Steam for Windows session (SteamRuntimeInstaller.prepare's rule for a value it may
    // replace): the values that name Steam for Windows' folder are put back, in Steam's own spelling too; nothing is a conflict.
    let afterSession = (try! String(contentsOf: userURL, encoding: .utf8))
        .replacingOccurrences(of: "\"SteamPath\"=\"\(root)\"", with: "\"SteamPath\"=\"c:/program files (x86)/steam desktop\"")
    do {
        let setUp = try SteamRuntimeFiles.registry(afterSession, machine: false, stale: { SteamRuntimeFiles.namesDesktopClient($0) })
        check("BCD Dock's client setup takes the hive a Steam for Windows session left: its values are put back, nothing is a conflict",
              !setUp.lowercased().contains("steam desktop") && setUp.contains("\"SteamPath\"=\"\(dock)\"\n")
              && setUp.contains("\"SteamClientDll64\"=\"\(dock)\\\\steamclient64.dll\"\n"))
    } catch {
        check("BCD Dock's client setup takes the hive a Steam for Windows session left: its values are put back, nothing is a conflict", false, "\(error)")
    }
    do {
        _ = try SteamRuntimeFiles.registry(afterSession, machine: false, stale: { _ in false })
        check("without that rule the same hive stops the setup (what the rule is for)", false)
    } catch SteamRuntimeFiles.Failure.conflict {
        check("without that rule the same hive stops the setup (what the rule is for)", true)
    } catch {
        check("without that rule the same hive stops the setup (what the rule is for)", false, "\(error)")
    }
    try! Data("garbage".utf8).write(to: systemURL)
    try? fm.removeItem(at: userURL)
    let odd = SteamDesktopRegistry.seed(prefix: prefix, softwareWebViews: false, sessionRunning: false)
    check("a file that is no registry and a missing hive are named and left",
          odd.state == ["system.reg=not-a-registry-file", "user.reg=unreadable"] && odd.line.hasSuffix("web-views=as-set-in-steam")
          && bytes(systemURL) == Data("garbage".utf8))
    exit(failures == 0 ? 0 : 1)
}

// MARK: - the session's log

if mode == "watch" {
    // watch <log with EXEC ALLOC FAILED> <log with the sign-in window>: two cuts of real Steam sessions (tests/fixtures/autotune).
    let memory = SteamDesktopWatch.Memory(footprintMB: 2310, leftMB: 4340)
    var watch = SteamDesktopWatch()
    let first = String(decoding: bytes(URL(fileURLWithPath: arguments[2])), as: UTF8.self)
    // Fed in pieces that end in the middle of lines, as the file is read while it grows.
    var lines: [String] = []
    var rest = Substring(first)
    var seconds = 3.0
    while !rest.isEmpty {
        let piece = rest.prefix(7001)
        rest = rest.dropFirst(piece.count)
        lines += watch.read(log: String(piece), at: seconds, memory: memory)
        seconds += 3
    }
    check("77VQJK: the updater's window, the refused code buffer and Steam's main window are milestones, once each, in the log's order",
          watch.milestones == ["updater-window", "exec-alloc-failed", "main-window"] && lines.count == 3, "\(watch.milestones) \(lines.count)")
    check("each milestone line carries the time, the memory and the counts",
          lines.first?.hasPrefix("[steam-desktop] milestone=updater-window t=+") == true
          && lines.allSatisfy { $0.contains(" footprint=2310MB left=4340MB helper-starts=0 helper-ends=0 libcef-loads=0 ") }
          && lines.last?.contains("exec-failed=1") == true, lines.joined(separator: " | "))
    check("the JIT pool's head and size are the log's last image line (36 of 592 MB here)",
          watch.poolSize == 0x25000000 && watch.poolHead == 0x2380000 && lines.last?.contains(" pool-head=") == true, "\(watch.poolHead) \(watch.poolSize)")
    check("one EXEC ALLOC FAILED: counted, and the session line's words are ready once",
          watch.execFailed == 1 && watch.notice == SteamDesktopWatch.noCodeMemoryWords)
    check("the previous run's tail, which a session copies into its log, is not read as this session",
          first.contains("[prev-tail]") && watch.helperStarts == 0)
    watch.notice = nil
    var second = SteamDesktopWatch()
    let signIn = second.read(log: String(decoding: bytes(URL(fileURLWithPath: arguments[3])), as: UTF8.self), at: 40, memory: memory)
    check("76WZC7: the sign-in window is a milestone",
          second.milestones.contains("sign-in-window") && signIn.contains { $0.hasPrefix("[steam-desktop] milestone=sign-in-window t=+40s footprint=2310MB") })

    // Build 689's first device run (2026-11-21 21:06, iPhone18,2): Steam took the system's language, Turkish. The engine's lines as
    // that log has them, in its order: the web helper's window is named by its page, then the main window, then a popup after it.
    func win(_ n: Int, _ hwnd: String, _ title: String) -> String { "[win-name] #\(n) hwnd=\(hwnd) class='SDL_app' text=\"\(title)\" rev=ml853" }
    let turkish = [
        "[win-name] #19 hwnd=0x10054 class='BootstrapUpdateUIClass' text=\"\" rev=ml853",
        "[win-name] #26 hwnd=0x10054 class='BootstrapUpdateUIClass' text=\"Steam\" rev=ml853",
        win(400, "0x1012e", "SDL_app"), win(401, "0x100ec", "SteamWebhelper"), win(402, "0x100ec", "Steam'e Giri? Yap"),
        win(403, "0x30148", "Menu"), win(404, "0x30134", "Steam"), win(405, "0x200f2", "?zel Teklifler"),
        win(406, "0x1019c", "notificationtoasts_199211735704_desktop"),
    ].joined(separator: "\n") + "\n"
    var localized = SteamDesktopWatch()
    let localizedLines = localized.read(log: turkish, at: 7, memory: memory)
    check("689, Turkish: the sign-in window is a milestone by its place, not by its English title; a popup after the main window is not",
          localized.milestones == ["updater-window", "sign-in-window", "main-window"] && localizedLines.count == 3,
          "\(localized.milestones)")
    var remembered = SteamDesktopWatch()
    _ = remembered.read(log: [win(1, "0x30134", "Steam"), win(2, "0x200f2", "?zel Teklifler"), win(3, "0x300ee", "Menu")]
                            .joined(separator: "\n") + "\n", at: 15, memory: memory)
    check("a remembered sign-in (the main window first): no sign-in milestone from a popup that comes after it",
          remembered.milestones == ["main-window"], "\(remembered.milestones)")
    check("the English title still counts, and the known other titles never do",
          SteamDesktopWatch.namesOwnWindow(Substring(win(1, "0x1", "Sign in to Steam")))
          && !["", "Steam", "SteamWebhelper", "SDL_app", "Menu", "Steam Big Picture Mode", "notificationtoasts_1_desktop"]
              .contains { SteamDesktopWatch.namesOwnWindow(Substring(win(1, "0x1", $0))) }
          && !SteamDesktopWatch.namesOwnWindow("[win-name] #1 hwnd=0x1 class='Chrome_WidgetWin_1' text=\"Steam'e Giri? Yap\" rev=ml853"))

    // The engine's own lines, as build/ntdll-unix writes them.
    var pool = SteamDesktopWatch()
    let engine = [
        "[jit-pool] image 0x7100000000+0xf000000 (libcef.dll) → pool 0x148004000 used=0x10000000/0x37000000 tramp+0x8000",
        "[x64-image] L\"\\\\??\\\\C:\\\\Program Files (x86)\\\\Steam Desktop\\\\bin\\\\cef\\\\cef.win64\\\\libcef.dll\" 0x7200000000+0xf000000 mapped without a pool copy (pure x64: the emulator runs it at the PE VA; MADEIRA_X64_IMAGE_NOCOPY)",
        "[jit-pool] tail EC_CODE rx=0x150000000 size=0x4000000 tail_resv=0x8000000 head_used=0x12000000/0x37000000",
        "[jit-pool] TAIL REFUSED (FEX EC_CODE): want=0x100000 tail_resv=0x9000000 head_used=0x13000000/0x37000000 — REFUSING honestly rev=ml421",
        "[jit-pool] EXHAUSTED (image 0x7300000000+0x100000): want=0x100000 bump=0x36f00000/0x37000000 tail_resv=0x9000000 freelist=0 — FAILING the load #1",
        "[proc-gate] REFUSING steamwebhelper --type= child (single-process mode; task #34)",
        "[steam-desktop] status t=+30s footprint=1MB EXEC ALLOC FAILED [jit-pool] EXHAUSTED",
    ].joined(separator: "\n") + "\n"
    let said = pool.read(log: engine, at: 100, memory: memory)
    check("libcef.dll loads are counted with and without a pool copy",
          pool.cefLoads == 2)
    check("head, size and tail follow the pool's lines; refusals and an exhausted pool are counted",
          pool.poolHead == 0x13000000 && pool.poolSize == 0x37000000 && pool.poolTail == 0x9000000 && pool.tailRefused == 1 && pool.poolExhausted == 1)
    check("a refused web helper child is counted, and the first exhausted pool is a milestone",
          pool.helperChildrenRefused == 1 && pool.milestones == ["pool-exhausted"] && said.count == 1, "\(pool.milestones)")
    check("BCD's own [steam-desktop] lines are never read back", pool.execFailed == 0 && pool.poolExhausted == 1 && pool.notice == nil)
    let failedNow = pool.read(log: "E 16C [code-buffer] rev=ml364 EXEC ALLOC FAILED even at 0x100000 — JIT pool exhausted; forcing honest fault at 0xdead\n"
                              + "E 170 [code-buffer] rev=ml364 EXEC ALLOC FAILED even at 0x100000 — JIT pool exhausted; forcing honest fault at 0xdead\n", at: 103, memory: memory)
    check("a refused last code buffer is a milestone once, counted each time, with the words for the session line",
          failedNow.count == 1 && failedNow[0].hasPrefix("[steam-desktop] milestone=exec-alloc-failed t=+103s") && pool.execFailed == 2
          && pool.notice == SteamDesktopWatch.noCodeMemoryWords && pool.notice?.contains("restart BCD") == true)

    // The process table (process_ios.c madeira_child_report and madeira_child_ends_since).
    var table = SteamDesktopWatch()
    let client = "C:\\Program Files (x86)\\Steam Desktop\\"
    func running(_ rows: [(String, Double)]) -> String { rows.map { "R\t\($0.0.contains("helper") ? 1 : 0)\t\(String(format: "%.1f", $0.1))\t\($0.0)" }.joined(separator: "\n") + "\n" }
    var out = table.read(processes: running([("C:\\windows\\system32\\explorer.exe", 9.0), (client + "steam.exe", 4.1)]), ends: "", at: 10, memory: memory)
    check("steam.exe starting is a milestone; explorer is not Steam's and is not named",
          out.count == 1 && out[0].hasPrefix("[steam-desktop] milestone=steam-start t=+10s"), "\(out)")
    out = table.read(processes: running([(client + "steam.exe", 7.1), (client + "bin\\cef\\cef.win64\\steamwebhelper.exe", 1.2)]), ends: "", at: 13, memory: memory)
    check("the web helper's first start is helper-start", out.count == 1 && out[0].contains("milestone=helper-start ") && table.helperStarts == 1, "\(out)")
    out = table.read(processes: running([(client + "steam.exe", 10.2), (client + "bin\\cef\\cef.win64\\steamwebhelper.exe", 4.1)]), ends: "", at: 16, memory: memory)
    check("the same processes at the next look are no new starts", out.isEmpty && table.helperStarts == 1, "\(out)")
    // The helper dies with an access violation and Steam starts it again.
    out = table.read(processes: running([(client + "steam.exe", 13.1), (client + "bin\\cef\\cef.win64\\steamwebhelper.exe", 0.9)]),
                     ends: "7\tc0000005\t6.2\t0\t1\t" + client + "bin\\cef\\cef.win64\\steamwebhelper.exe\n", at: 19, memory: memory)
    check("a helper that ended with a crash status and its restart: one line each, counted",
          out.count == 2 && out[0].contains("milestone=helper-restart-1 ") && out[1] == "[steam-desktop] process steamwebhelper.exe ended status=0xc0000005 lived=6s t=+19s footprint=2310MB"
          && table.helperStarts == 2 && table.helperEnds == 1 && table.helperCrashes == 1, "\(out)")
    out = table.read(processes: running([(client + "steam.exe", 16.0), (client + "bin\\cef\\cef.win64\\steamwebhelper.exe", 3.9),
                                         ("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Some Game\\game.exe", 0.4),
                                         (client + "bin\\steamservice.exe", 0.2)]), ends: "", at: 22, memory: memory)
    check("a game started from the client is a milestone with its file name only; another program of Steam's gets a line",
          out.count == 2 && out.contains { $0.contains("milestone=game-start ") && $0.hasSuffix(" exe=game.exe") }
          && out.contains("[steam-desktop] process steamservice.exe started t=+22s") && table.gameStarts == 1
          && !out.joined().contains("Some Game") && !out.joined().contains("Program Files"), "\(out)")
    check("the status and the last line carry every count",
          table.status(at: 30, memory: memory) == "[steam-desktop] status t=+30s footprint=2310MB left=4340MB helper-starts=2 helper-ends=1 libcef-loads=0 "
              + "pool-tail=0MB exec-failed=0 tail-refused=0 pool-exhausted=0 games-started=1"
          && table.summary(at: 31, memory: memory).hasPrefix("[steam-desktop] session end t=+31s ")
          && table.summary(at: 31, memory: memory).hasSuffix(" helper-crashes=1 helper-children-refused=0 games-started=1 "
              + "milestones=steam-start,helper-start,helper-restart-1,game-start"), table.summary(at: 31, memory: memory))
    exit(failures == 0 ? 0 : 1)
}

#if canImport(CryptoKit)
// MARK: - the install

/// Valve's host as a folder of files. `cut`: a file's next download stops after that many new bytes, as a connection that drops.
/// `cancelAt`: the task is cancelled when that file is asked for. Everything asked is noted.
final class FolderTransport: SteamDesktopTransport, @unchecked Sendable {
    let folder: URL
    var cut: [String: Int] = [:]
    var cancelAt: String?
    var asked: [String] = []
    var task: Task<SteamDesktopInstaller.Outcome, Error>?

    init(_ folder: URL) { self.folder = folder }

    func data(_ name: String, limit: Int) async throws -> Data {
        asked.append(name)
        guard let data = try? Data(contentsOf: folder.appendingPathComponent(name)), data.count <= limit else {
            throw SteamDesktopInstaller.Failure.download(name)
        }
        return data
    }

    func download(_ name: String, to file: URL, from offset: Int64, expected: Int64,
                  progress: @escaping @Sendable (Int64) -> Void) async throws {
        asked.append("\(name)@\(offset)")
        if cancelAt == name { task?.cancel(); throw CancellationError() }
        guard let source = try? Data(contentsOf: folder.appendingPathComponent(name)) else { throw SteamDesktopInstaller.Failure.download(name) }
        if !FileManager.default.fileExists(atPath: file.path) { FileManager.default.createFile(atPath: file.path, contents: nil) }
        let handle = try FileHandle(forWritingTo: file)
        defer { try? handle.close() }
        try handle.truncate(atOffset: UInt64(offset))
        try handle.seekToEnd()
        let rest = source.dropFirst(Int(offset))
        if let stop = cut.removeValue(forKey: name) {
            try handle.write(contentsOf: rest.prefix(stop))
            progress(offset + Int64(min(stop, rest.count)))
            throw SteamDesktopInstaller.Failure.download(name)
        }
        try handle.write(contentsOf: rest)
        progress(Int64(source.count))
    }
}

final class Lines: @unchecked Sendable {
    private let lock = NSLock()
    private var all: [String] = []
    func add(_ line: String) { lock.lock(); all.append(line); lock.unlock() }
    var text: [String] { lock.lock(); defer { lock.unlock() }; return all }
}

if mode == "install" {
    // install <host folder> <work folder>: the host folder holds the manifest and every file it names (and the variants below).
    let host = URL(fileURLWithPath: arguments[2]), work = URL(fileURLWithPath: arguments[3])
    let realHost = arguments.count > 4 ? URL(fileURLWithPath: arguments[4]) : nil
    let done = DispatchSemaphore(value: 0)
    Task.detached {
    let manifest = try! SteamDesktopManifest.parse(bytes(host.appendingPathComponent(SteamDesktopManifest.fileName)))
    let boot = manifest.bootstrapper.zip
    let files = manifest.packages.map(\.download)
    let program = bytes(host.appendingPathComponent("expected-steam.exe"))
    var number = 0

    /// A new drive with a Dock client in its own folder, which no install may touch.
    func drive() -> URL {
        number += 1
        let drive = work.appendingPathComponent("drive-\(number)/drive_c")
        let dock = drive.appendingPathComponent(SteamRuntimeFiles.relativeRoot)
        try! fm.createDirectory(at: dock.appendingPathComponent("package"), withIntermediateDirectories: true)
        try! fm.createDirectory(at: dock.appendingPathComponent("steamapps"), withIntermediateDirectories: true)
        try! Data("the Dock's pinned client".utf8).write(to: dock.appendingPathComponent("steamclient64.dll"))
        try! Data("the Dock's steam.exe".utf8).write(to: dock.appendingPathComponent("steam.exe"))
        try! Data("a game's record".utf8).write(to: dock.appendingPathComponent("steamapps/appmanifest_620.acf"))
        try! Data("the Dock's own package".utf8).write(to: dock.appendingPathComponent("package/" + files[0].name))
        return drive
    }
    func dockTree(_ drive: URL) -> [String: Data] { tree(drive.appendingPathComponent(SteamRuntimeFiles.relativeRoot)) }
    let untouched = dockTree(drive())
    func client(_ drive: URL) -> URL { drive.appendingPathComponent(SteamDesktop.relativeRoot) }
    func package(_ drive: URL, _ name: String) -> URL { client(drive).appendingPathComponent("package/" + name) }

    struct Run {
        var outcome: SteamDesktopInstaller.Outcome?
        var failure: SteamDesktopInstaller.Failure?
        var cancelled = false
        var other: String?
        var log: [String] = []
        var steps: [SteamDesktopInstaller.Progress] = []
    }
    func install(_ drive: URL, _ transport: FolderTransport, packages: Bool, running: Bool = false) async -> Run {
        var run = Run()
        let log = Lines(), steps = Lines()
        let task = Task {
            try await SteamDesktopInstaller.install(drive: drive, transport: transport, packages: packages,
                                                    sessionRunning: { running }, log: { log.add($0) },
                                                    progress: { steps.add("\($0.step.rawValue) \($0.received) \($0.total)") })
        }
        transport.task = task
        do { run.outcome = try await task.value }
        catch let failure as SteamDesktopInstaller.Failure { run.failure = failure }
        catch is CancellationError { run.cancelled = true }
        catch { run.other = "\(error)" }
        run.log = log.text
        run.steps = steps.text.map { line in
            let f = line.split(separator: " ")
            return SteamDesktopInstaller.Progress(step: SteamDesktopInstaller.Step(rawValue: String(f[0]))!, received: Int64(f[1])!, total: Int64(f[2])!)
        }
        return run
    }
    func complete(_ drive: URL) -> Bool {
        bytes(client(drive).appendingPathComponent("steam.exe")) == program && !program.isEmpty
            && files.allSatisfy { bytes(package(drive, $0.name)) == bytes(host.appendingPathComponent($0.name)) }
            && !((try? fm.contentsOfDirectory(atPath: client(drive).appendingPathComponent("package").path)) ?? []).contains { $0.hasSuffix(".bcd-part") }
    }

    do {
        // 1. steam.exe alone.
        var d = drive()
        var t = FolderTransport(host)
        var run = await install(d, t, packages: false)
        check("steam.exe alone: the manifest and the package with steam.exe are all that is asked for",
              t.asked == [SteamDesktopManifest.fileName, boot.name + "@0"] && run.outcome?.version == manifest.version
              && run.outcome?.packages == 0 && run.outcome?.programBytes == Int64(program.count), "\(t.asked) \(run)")
        check("steam.exe is in the client's own folder, byte for byte, and nothing else is",
              tree(client(d)) == ["steam.exe": program])
        check("the Dock's folder is as it was", dockTree(d) == untouched)
        check("the log has the manifest's version and the end, and no path",
              run.log.count == 2 && run.log[0].hasPrefix("[steam-desktop] install: manifest version=\(manifest.version) packages=\(manifest.packages.count) ")
              && run.log[1].hasPrefix("[steam-desktop] install: steam.exe in place bytes=\(program.count) packages=0 ")
              && !run.log.joined().contains("/"), run.log.joined(separator: " | "))
        check("the steps were reported in order", run.steps.first?.step == .manifest && run.steps.last?.step == .installing
              && run.steps.contains(SteamDesktopInstaller.Progress(step: .bootstrapper, received: boot.bytes, total: boot.bytes)))

        // 2. With Valve's packages.
        d = drive(); t = FolderTransport(host)
        run = await install(d, t, packages: true)
        check("with the packages: each is in the package folder under the name and in the form the manifest gives the updater",
              complete(d) && run.outcome?.packages == files.count && run.outcome?.kept == 0
              && run.outcome?.fetchedBytes == manifest.downloadBytes, "\(run)")
        check("the compressed form is taken where the manifest has one, the archive where it has none",
              t.asked == [SteamDesktopManifest.fileName, boot.name + "@0"] + files.map { $0.name + "@0" }
              && files.contains { $0.name.contains(".zip.vz.") } && files.contains { !$0.name.contains(".zip.vz.") })
        check("the client's folder holds steam.exe and the packages, nothing else",
              Set(tree(client(d)).keys) == Set(["steam.exe"] + files.map { "package/" + $0.name }))
        check("the Dock's folder is as it was", dockTree(d) == untouched)
        check("the packages' progress ends at the manifest's total",
              run.steps.contains(SteamDesktopInstaller.Progress(step: .packages, received: manifest.downloadBytes, total: manifest.downloadBytes)))
        let again = await install(d, FolderTransport(host), packages: true)
        check("an installed client is not installed again", again.failure == .alreadyInstalled)

        // 3. A connection that drops, then the next try.
        d = drive(); t = FolderTransport(host)
        let big = files.max { $0.bytes < $1.bytes }!
        let position = files.firstIndex(of: big)!
        t.cut[big.name] = 4321
        run = await install(d, t, packages: true)
        check("a download that stops early ends the install with the download's words, and steam.exe is not put in place",
              run.failure == .download(big.name) && !fm.fileExists(atPath: client(d).appendingPathComponent("steam.exe").path), "\(run)")
        check("what arrived stays, as BCD's own unfinished file",
              size(package(d, big.name + ".bcd-part")) == 4321 && !fm.fileExists(atPath: package(d, big.name).path))
        t = FolderTransport(host)
        run = await install(d, t, packages: true)
        check("the next try keeps the finished packages (checked, not fetched) and asks only for the rest of the unfinished one",
              complete(d) && run.outcome?.kept == position && run.outcome?.fetchedBytes == manifest.downloadBytes - files[..<position].reduce(0) { $0 + $1.bytes } - 4321
              && t.asked == [SteamDesktopManifest.fileName, boot.name + "@0", big.name + "@4321"] + files[(position + 1)...].map { $0.name + "@0" }, "\(t.asked) \(run)")
        check("and says where it went on", run.log.contains { $0.contains("package \(manifest.packages[position].name) fetched bytes=\(big.bytes) resumed-at=4321") })
        check("the Dock's folder is as it was", dockTree(d) == untouched)

        // 4. A package that is not the manifest's file: the same size, one byte different.
        d = drive()
        let bad = work.appendingPathComponent("host-bad-package")
        try? fm.removeItem(at: bad)
        try! fm.copyItem(at: host, to: bad)
        var wrong = bytes(host.appendingPathComponent(files[1].name)); wrong[wrong.count / 2] ^= 0x01
        try! wrong.write(to: bad.appendingPathComponent(files[1].name))
        run = await install(d, FolderTransport(bad), packages: true)
        check("a package whose SHA-256 is not the manifest's is not kept, in any form, and steam.exe is not put in place",
              run.failure == .verification(files[1].name) && !fm.fileExists(atPath: package(d, files[1].name).path)
              && !fm.fileExists(atPath: package(d, files[1].name + ".bcd-part").path)
              && !fm.fileExists(atPath: client(d).appendingPathComponent("steam.exe").path), "\(run)")
        run = await install(d, FolderTransport(host), packages: true)
        check("the next try with the right file completes", complete(d) && run.outcome?.kept == 1)

        // 5. What is already in the package folder.
        d = drive()
        try! fm.createDirectory(at: client(d).appendingPathComponent("package"), withIntermediateDirectories: true)
        try! Data("not this file".utf8).write(to: package(d, files[0].name))                      // the name, another content
        try! Data(count: Int(files[2].bytes) + 5).write(to: package(d, files[2].name + ".bcd-part"))   // an unfinished file that is too long
        try! Data("old".utf8).write(to: package(d, "old_all.zip.vz.0000000000000000000000000000000000000000_3.bcd-part"))   // of an older manifest
        try! Data("steam's own".utf8).write(to: package(d, "steam_client_win64.manifest"))        // not BCD's: stays
        try! Data("steam's old package".utf8).write(to: package(d, "old_all.zip.1111111111111111111111111111111111111111"))
        t = FolderTransport(host)
        run = await install(d, t, packages: true)
        check("a wrong file under a package's name is replaced, a too-long unfinished file starts again, an older unfinished file of BCD's goes",
              complete(d) && run.outcome?.kept == 0 && t.asked.contains(files[2].name + "@0")
              && !fm.fileExists(atPath: package(d, "old_all.zip.vz.0000000000000000000000000000000000000000_3.bcd-part").path), "\(t.asked)")
        check("files in the package folder that are not BCD's stay",
              bytes(package(d, "steam_client_win64.manifest")) == Data("steam's own".utf8)
              && bytes(package(d, "old_all.zip.1111111111111111111111111111111111111111")) == Data("steam's old package".utf8))

        // 6. Cancelled, then taken up again.
        d = drive(); t = FolderTransport(host)
        t.cancelAt = files[3].name
        run = await install(d, t, packages: true)
        check("a cancelled install ends as cancelled, keeps the packages it finished and leaves no steam.exe",
              run.cancelled && (0..<3).allSatisfy { bytes(package(d, files[$0].name)) == bytes(host.appendingPathComponent(files[$0].name)) }
              && !fm.fileExists(atPath: client(d).appendingPathComponent("steam.exe").path), "\(run)")
        run = await install(d, FolderTransport(host), packages: true)
        check("and the next Install finishes it", complete(d) && run.outcome?.kept == 3)

        // 7. Refusals.
        d = drive(); t = FolderTransport(host)
        run = await install(d, t, packages: true, running: true)
        check("while a session runs nothing is asked for and nothing is written", run.failure == .activeSession && t.asked.isEmpty && !fm.fileExists(atPath: client(d).path))
        let refusals: [(String, SteamDesktopInstaller.Failure, String)] = [
            ("host-not-a-program", .notSteam, "a steam.exe that is no 64-bit Windows program"),
            ("host-no-program", .notSteam, "a package without steam.exe"),
            ("host-bad-bootstrapper", .verification(boot.name), "a package with steam.exe whose SHA-256 is not the manifest's"),
            ("host-short-bootstrapper", .download(boot.name), "a package with steam.exe that is shorter than the manifest says"),
        ]
        for (variant, expected, what) in refusals {
            d = drive()
            run = await install(d, FolderTransport(work.appendingPathComponent(variant)), packages: false)
            check("refused: \(what)", run.failure == expected && !fm.fileExists(atPath: client(d).appendingPathComponent("steam.exe").path)
                  && dockTree(d) == untouched, "\(run)")
        }
        d = drive()
        run = await install(d, FolderTransport(work.appendingPathComponent("host-bad-manifest")), packages: true)
        if case .manifest? = run.failure { check("refused: a manifest BCD cannot read, before anything else is asked for", !fm.fileExists(atPath: client(d).path)) }
        else { check("refused: a manifest BCD cannot read, before anything else is asked for", false, "\(run)") }
        d = drive()
        run = await install(d, FolderTransport(work.appendingPathComponent("host-empty")), packages: true)
        check("refused: a host without the manifest", run.failure == .download(SteamDesktopManifest.fileName))
        // The client's folder is a link to the Dock's: nothing is written through it.
        d = drive()
        try! fm.createSymbolicLink(at: client(d), withDestinationURL: d.appendingPathComponent(SteamRuntimeFiles.relativeRoot))
        t = FolderTransport(host)
        run = await install(d, t, packages: true)
        check("refused: a client folder that is a link (here: to the Dock's folder), before anything is asked for",
              run.failure == .conflict("steam.exe") && t.asked.isEmpty && dockTree(d) == untouched, "\(run)")
        // A second spelling of the folder.
        d = drive()
        try! fm.createDirectory(at: d.appendingPathComponent("Program Files (x86)/steam desktop"), withIntermediateDirectories: true)
        run = await install(d, FolderTransport(host), packages: false)
        check("refused: a folder of the same name in another spelling", run.failure == .conflict("steam.exe"), "\(run)")

        // A drive that has no folder yet (not even "Program Files (x86)").
        d = work.appendingPathComponent("drive-new/drive_c")
        try! fm.createDirectory(at: d, withIntermediateDirectories: true)
        run = await install(d, FolderTransport(host), packages: true)
        check("a drive without any folder yet: the folders are made and the install completes", complete(d) && run.outcome?.packages == files.count, "\(run)")

        // A whole real package folder, when the caller gave one (never fetched here).
        if let real = realHost {
            d = drive(); t = FolderTransport(real)
            run = await install(d, t, packages: false)
            let exe = client(d).appendingPathComponent("steam.exe")
            check("the real bootstrapper package of the given folder installs with the production reader (steam.exe \(size(exe)) bytes, client \(run.outcome?.version ?? "?"))",
                  run.outcome != nil && SteamDesktop.isWindowsProgram64(bytes(exe)), "\(run)")
        }
    }
    done.signal()
    }
    done.wait()
    exit(failures == 0 ? 0 : 1)
}

// MARK: - the update host

if mode == "cdn" {
    // cdn <port> <served folder> <work folder>: SteamDesktopCDN against a server on this Mac (tests/host/check-steam-desktop.py).
    let port = Int(arguments[2])!, servedFolder = URL(fileURLWithPath: arguments[3]), work = URL(fileURLWithPath: arguments[4])
    let done = DispatchSemaphore(value: 0)
    Task.detached {
    let valve = SteamDesktopCDN.Policy.valve
    check("the app's policy is HTTPS on Valve's client update host, the Dock's host, and nothing else",
          valve.scheme == "https" && valve.host == "client-update.akamai.steamstatic.com" && valve.port == nil
          && valve.base == SteamRuntimeFiles.origin && valve.base == "https://client-update.akamai.steamstatic.com/")
    func allowed(_ text: String) -> Bool { valve.allows(URL(string: text)) }
    check("it takes that host over HTTPS only",
          allowed("https://client-update.akamai.steamstatic.com/steam_client_win64") && allowed("https://client-update.akamai.steamstatic.com:443/x")
          && !allowed("http://client-update.akamai.steamstatic.com/x") && !allowed("https://client-update.akamai.steamstatic.com:8443/x")
          && !allowed("https://client-update.akamai.steamstatic.com.example.org/x") && !allowed("https://example.org/client-update.akamai.steamstatic.com")
          && !allowed("https://user@client-update.akamai.steamstatic.com/x") && !allowed("https://cdn.steamstatic.com/x") && !valve.allows(nil))
    let local = SteamDesktopCDN.Policy(scheme: "http", host: "127.0.0.1", port: port, base: "http://127.0.0.1:\(port)/")
    let cdn = SteamDesktopCDN(policy: local)
    func served(_ name: String) -> Data { bytes(servedFolder.appendingPathComponent(name)) }
    func requests() -> [String] {
        String(decoding: bytes(servedFolder.appendingPathComponent("requests.log")), as: UTF8.self).split(separator: "\n").map(String.init)
    }
    final class Seen: @unchecked Sendable {
        private let lock = NSLock()
        private var values: [Int64] = []
        func add(_ value: Int64) { lock.lock(); values.append(value); lock.unlock() }
        var all: [Int64] { lock.lock(); defer { lock.unlock() }; return values }
    }
    enum Ending: Equatable { case ok, failed(String), cancelled, other(String) }
    func fetch(_ name: String, _ file: URL, from offset: Int64, seen: Seen = Seen()) async -> Ending {
        do {
            try await cdn.download(name, to: file, from: offset, expected: Int64(served(name.replacingOccurrences(of: "moved-", with: "")
                .replacingOccurrences(of: "away-", with: "").replacingOccurrences(of: "norange-", with: "").replacingOccurrences(of: "badrange-", with: "")
                .replacingOccurrences(of: "cut-", with: "").replacingOccurrences(of: "slow-", with: "").replacingOccurrences(of: "chunked-", with: "")).count)) { seen.add($0) }
            return .ok
        } catch let failure as SteamDesktopInstaller.Failure {
            if case .download(let what) = failure { return .failed(what) }
            return .other("\(failure)")
        } catch is CancellationError { return .cancelled }
        catch { return .other("\(error)") }
    }
    do {
        let whole = served("file.bin")
        // The manifest's way: a small file, whole.
        let text = try? await cdn.data("small.txt", limit: 4096)
        check("a small file comes whole", text == served("small.txt") && text?.isEmpty == false)
        var tooLong = false
        do { _ = try await cdn.data("file.bin", limit: 4096) } catch { tooLong = true }
        check("a file longer than the limit is refused", tooLong)
        // A package, whole.
        var file = work.appendingPathComponent("whole.part")
        let seen = Seen()
        var ending = await fetch("chunked-file.bin", file, from: 0, seen: seen)   // served in pieces: arrives in several reads
        check("a package is written as it arrives, the progress never goes back and ends at its size",
              ending == .ok && bytes(file) == whole && seen.all.last == Int64(whole.count) && seen.all == seen.all.sorted() && seen.all.count > 1, "\(ending) \(seen.all.count)")
        // The rest of a file.
        file = work.appendingPathComponent("rest.part")
        try! whole.prefix(70_000).write(to: file)
        ending = await fetch("file.bin", file, from: 70_000)
        check("a download that goes on asks for the rest only, and the file is whole",
              ending == .ok && bytes(file) == whole && requests().last == "GET /file.bin bytes=70000-", "\(ending) \(requests().last ?? "")")
        // The file holds less than the caller says: never a hole.
        file = work.appendingPathComponent("short.part")
        try! whole.prefix(1000).write(to: file)
        ending = await fetch("file.bin", file, from: 5000)
        check("a file that holds less than was said goes on from what it really holds",
              ending == .ok && bytes(file) == whole && requests().last == "GET /file.bin bytes=1000-", "\(ending)")
        // A connection that drops in the middle, then the rest.
        file = work.appendingPathComponent("cut.part")
        ending = await fetch("cut-file.bin", file, from: 0)
        let kept = size(file)
        check("a connection that drops leaves what arrived in the file and ends as a failed download",
              ending == .failed("cut-file.bin") && kept > 0 && kept < Int64(whole.count) && bytes(file) == whole.prefix(Int(kept)), "\(ending) \(kept)")
        ending = await fetch("file.bin", file, from: kept)
        check("and the rest completes it", ending == .ok && bytes(file) == whole)
        // A server that does not take the range.
        file = work.appendingPathComponent("norange.part")
        try! Data(repeating: 0x55, count: 30_000).write(to: file)
        ending = await fetch("norange-file.bin", file, from: 30_000)
        check("an answer with the whole file instead of the rest starts the file again, and it is whole",
              ending == .ok && bytes(file) == whole, "\(ending) \(size(file))")
        file = work.appendingPathComponent("badrange.part")
        try! whole.prefix(30_000).write(to: file)
        ending = await fetch("badrange-file.bin", file, from: 30_000)
        check("an answer that is another part of the file is refused and the file starts again from nothing",
              ending == .failed("badrange-file.bin") && size(file) == 0, "\(ending) \(size(file))")
        file = work.appendingPathComponent("past.part")
        try! (whole + Data(count: 10)).write(to: file)
        ending = await fetch("file.bin", file, from: Int64(whole.count) + 10)
        check("a range the server cannot give (416) fails and the file starts again from nothing", ending == .failed("file.bin") && size(file) == 0, "\(ending)")
        // Redirects.
        file = work.appendingPathComponent("moved.part")
        ending = await fetch("moved-file.bin", file, from: 0)
        check("a redirect on the same host is followed", ending == .ok && bytes(file) == whole, "\(ending)")
        file = work.appendingPathComponent("away.part")
        let before = requests().count
        ending = await fetch("away-file.bin", file, from: 0)
        check("a redirect to another host is not followed: nothing is fetched from there, nothing is kept",
              ending == .failed("away-file.bin") && size(file) == 0 && requests().count == before + 1, "\(ending) \(requests().suffix(2))")
        // What is not there, and names that are no file names.
        ending = await fetch("missing.bin", work.appendingPathComponent("missing.part"), from: 0)
        check("a file the host does not have fails", ending == .failed("missing.bin"))
        let asked = requests().count
        for name in ["", "../file.bin", "a/b.bin", ".hidden", "file.bin?x=1", "file bin", "f%2e.bin"] {
            var refused = false
            do { _ = try await cdn.data(name, limit: 10) } catch { refused = true }
            check("no request for the name \"\(name)\"", refused)
        }
        check("none of those reached the server", requests().count == asked)
        // More than the manifest's size.
        file = work.appendingPathComponent("longer.part")
        var longer = Ending.ok
        do { try await cdn.download("file.bin", to: file, from: 0, expected: Int64(whole.count) - 7) { _ in } }
        catch let failure as SteamDesktopInstaller.Failure { longer = .failed("\(failure)") } catch { longer = .other("\(error)") }
        check("a file longer than the manifest's size is refused", longer == .failed("download(\"file.bin\")") && size(file) < Int64(whole.count), "\(longer)")
        // Cancelled in the middle: what arrived stays, and the rest completes it.
        file = work.appendingPathComponent("slow.part")
        let slow = file
        let task = Task { await fetch("slow-file.bin", slow, from: 0) }
        try? await Task.sleep(nanoseconds: 600_000_000)
        task.cancel()
        ending = await task.value
        let part = size(file)
        check("a cancelled download ends as cancelled and keeps what arrived", ending == .cancelled && part > 0 && part < Int64(whole.count)
              && bytes(file) == whole.prefix(Int(part)), "\(ending) \(part)")
        ending = await fetch("file.bin", file, from: part)
        check("and Install later completes it", ending == .ok && bytes(file) == whole)
        cdn.close()
    }
    done.signal()
    }
    done.wait()
    exit(failures == 0 ? 0 : 1)
}
#else
if mode == "install" || mode == "cdn" {
    print("SKIP: no CryptoKit here; the installer and the update host are checked on macOS")
    exit(0)
}
#endif

print("unknown mode \(mode)")
exit(2)
