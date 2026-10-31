// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// The Swift half of tests/host/check-epic.py for milestone 2 (a signed-in start): the request BCD Host reads (its bytes, and sample
// files the C reader of bcd-host/ parses in check-epic.py), the report, the files of a start (written exclusively, 0600, never
// through a link, removed), Epic's launcher arguments, when a start is signed in, what the player is told, the calls for the exchange
// code and the ownership token against a stand-in for Epic written here (order, retries, refresh, failures, the deadline, no secret in
// any log line), the container search, and the rules milestone 2 changes (the ownership token, the anti-cheat exception for GTA V).
// Epic is never contacted; every code, token and name below is made up.

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
func snapshotLogs() -> [String] { logLock.lock(); defer { logLock.unlock() }; return logs }
func installSink() {
    BCDContentLog.sink = { line in
        logLock.lock(); logs.append(line); logLock.unlock()
        print("LOG " + line)
    }
}

func json(_ object: Any) -> Data { try! JSONSerialization.data(withJSONObject: object) }

// Made-up secrets: none may reach a log.
let CODE1 = "c0de1c0de1c0de1c0de1c0de1c0de1a"
let CODE2 = "c0de2c0de2c0de2c0de2c0de2c0de2b"
let ACCOUNT = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
let NAME = "Secret Display Name"
let ACCESS1 = "eg1~ACCESS1SECRET.PAYLOAD1.SIGNATURE1"
let ACCESS2 = "eg1~ACCESS2SECRET.PAYLOAD2.SIGNATURE2"
let REFRESH1 = "feedfacefeedfacefeedfacefeedface"
let REFRESH2 = "cafebabecafebabecafebabecafebabe"
let OVTTOKEN = "egoc1~OVTSECRETTOKEN.PAYLOADOVT.SIGNATUREOVT"
let OVTREPLY = Data(#"{"token":"\#(OVTTOKEN)","expiresInSeconds":299}"#.utf8)
let SECRETS = [CODE1, CODE2, ACCOUNT, NAME, "ACCESS1SECRET", "ACCESS2SECRET", REFRESH1, REFRESH2, "OVTSECRETTOKEN", "SIGNATUREOVT"]

func formFields(_ text: String) -> [String: String] {
    var out: [String: String] = [:]
    for pair in text.split(separator: "&") {
        let parts = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
        out[String(parts[0]).removingPercentEncoding ?? ""] = parts.count > 1 ? String(parts[1]).removingPercentEncoding ?? "" : ""
    }
    return out
}

func temp(_ name: String) -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("bcd-epic-launch-\(getpid())-\(name)", isDirectory: true)
    try? FileManager.default.removeItem(at: url)
    try! FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    return url
}

func mode(_ url: URL) -> Int { ((try? FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions]) as? NSNumber)?.intValue ?? -1 }

func creds(code: String = CODE1, ownership: Data? = nil, name: String? = NAME, made: Date = Date(), lifetime: TimeInterval = 299) -> BCDEpicCredentials {
    BCDEpicCredentials(accountID: ACCOUNT, displayName: name, exchangeCode: code, ownership: ownership, made: made, codeLifetime: lifetime)
}

// MARK: The request and the report

func requestPhase() {
    let sample = BCDEpicHostRequest(program: "C:\\Program Files\\Epic Games\\G\\a.exe", workingFolder: nil, arguments: "", ovt: nil, root: nil, flags: [])
    let bytes = try! sample.encoded()
    var want = Data("BCDHOST1".utf8)
    want.append(contentsOf: [1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0x23, 0, 0, 0])   // version 1, no flags, tag 1, 35 bytes
    want.append(Data("C:\\Program Files\\Epic Games\\G\\a.exe".utf8))
    check(bytes == want, "a request's bytes: the magic, the version, the flags, then the tagged fields (program only here)")

    var full = BCDEpicHostRequest(program: "C:\\a.exe", workingFolder: "C:\\", arguments: "-x", ovt: "C:\\ProgramData\\BCD\\Epic\\t.ovt", root: "C:\\", flags: [.tree, .services])
    var enc = try! full.encoded()
    check(Array(enc[8..<16]) == [1, 0, 0, 0, 3, 0, 0, 0], "the flags are the tree (1) and services (2) bits")
    check(enc.count == 16 + (6 + 8) + (6 + 3) + (6 + 2) + (6 + 29) + (6 + 3), "all five fields are written: \(enc.count) bytes")
    full.arguments = ""
    enc = try! full.encoded()
    check(enc.count == 16 + (6 + 8) + (6 + 3) + (6 + 29) + (6 + 3), "empty arguments are left out")

    func throwsProblem(_ r: BCDEpicHostRequest, _ p: BCDEpicHostRequest.Problem) -> Bool { (try? r.encoded()) == nil && { do { _ = try r.encoded(); return false } catch { return (error as? BCDEpicHostRequest.Problem) == p } }() }
    check(throwsProblem(BCDEpicHostRequest(program: "C:\\a.exe", workingFolder: nil, arguments: String(repeating: "x", count: 33_000), ovt: nil, root: nil), .tooBig),
          "a request over 32 KB is refused")
    check(throwsProblem(BCDEpicHostRequest(program: "C:\\a\n.exe", workingFolder: nil, arguments: "", ovt: nil, root: nil), .badValue("program")), "a control character in the program is refused")
    check(throwsProblem(BCDEpicHostRequest(program: "C:\\a.exe", workingFolder: nil, arguments: "-a\tb", ovt: nil, root: nil), .badValue("args")), "...and in the arguments")
    check(throwsProblem(BCDEpicHostRequest(program: "C:\\a.exe", workingFolder: nil, arguments: "-a\u{0}b", ovt: nil, root: nil), .badValue("args")), "...a NUL too")
    check((try? BCDEpicHostRequest(program: "C:\\a.exe", workingFolder: nil, arguments: "-name=日本 😀", ovt: nil, root: nil).encoded()) != nil, "non-ASCII text is written as UTF-8")

    // The report.
    let text = "v=1\nstage=10\nerror=0\nlast-error=0\nflags=3\nopen=1\nservices=1\njob=1\nwait=2\nchild-pid=4294967295\nchild-exit=3221225477\nsettle=1\novt=1\nseconds=12\nlived=200\nlate=1\nleft=2\nhelpers=5\nnames=1\n"
    let report = BCDEpicHostReport.parse(text)
    check(report?["stage"] == 10 && report?["child-pid"] == 4_294_967_295 && report?["child-exit"] == 3_221_225_477 && report?["wait"] == 2 && report?["seconds"] == 12, "a report's numbers are read")
    check(report?.logText == "stage=10 error=0 last-error=0 open=1 services=1 job=1 wait=2 settle=1 child-exit=3221225477 ovt=1 seconds=12 lived=200 late=1 left=2 helpers=5 names=1", "...and logged in a fixed order, numbers only")
    check(report?.failedBeforeStart == false, "a finished run did not fail before the start")
    let hostile = BCDEpicHostReport.parse("stage=3\nerror=12\nname=C:\\Secret\\path\nstage=99999999999\nchild-pid=-1\nopen=0x10\nwait= 2\n\u{1}garbage\nflags=1\n")
    check(hostile?["stage"] == 3 && hostile?["error"] == 12 && hostile?["flags"] == 1 && hostile?["name"] == nil && hostile?["child-pid"] == nil && hostile?["open"] == nil && hostile?["wait"] == nil,
          "a report is read by its listed keys and plain decimal numbers only: any other line is skipped")
    check(hostile?.failedBeforeStart == true && !(hostile?.logText.contains("Secret") ?? true), "an error before the start is seen, and no text reaches the log")
    check(BCDEpicHostReport.parse("") == nil && BCDEpicHostReport.parse("error=1\n") == nil && BCDEpicHostReport.parse(String(repeating: "stage=1\n", count: 400)) == nil, "an empty report, one with no stage and an oversized one are nothing")
    check(BCDEpicHostReport.parse("stage=5\nerror=24\nlast-error=5\n")?.failedBeforeStart == true && BCDEpicHostReport.parse("stage=7\nerror=0\n")?.failedBeforeStart == false
          && BCDEpicHostReport.parse("stage=6\nerror=0\n")?.failedBeforeStart == false, "failedBeforeStart: an error below stage 6 only")
    // round 624 (Discord forum item 7): the words of that case are about BCD Host, not about signing in to Epic
    check(BCDEpicHostFiles.notStartedWords.hasPrefix("BCD Host stopped before the game started; your Epic sign-in is fine.")
          && !BCDEpicHostFiles.notStartedWords.contains("signed in to Epic") && BCDEpicHostFiles.notStartedWords.contains("Settings › Logs"),
          "a host that stopped before the game: BCD Host's words, the sign-in is fine, send the log")
}

// MARK: Files

func filesPhase() throws {
    let base = temp("base"), drive = temp("drive")
    let fm = FileManager.default
    let file = try BCDEpicHostFiles.writeRequest(Data("request".utf8), base: base)
    check(file == BCDEpicHostFiles.requestFile(base) && file.lastPathComponent == "launch.req" && file.deletingLastPathComponent().lastPathComponent == "transfer", "the request lives in a folder of its own: transfer/launch.req")
    check(mode(file) == 0o600 && mode(file.deletingLastPathComponent()) == 0o700, "the request is readable by the app alone (0600 in a 0700 folder)")
    check((try? file.deletingLastPathComponent().resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup) == true, "...and its folder is kept out of backups")
    check((try? Data(contentsOf: file)) == Data("request".utf8), "...holding what was written")
    // An earlier request goes; a link in its place is not followed.
    let victim = base.appendingPathComponent("victim.txt")
    try Data("keep".utf8).write(to: victim)
    try fm.removeItem(at: file)
    try fm.createSymbolicLink(at: file, withDestinationURL: victim)
    let again = try BCDEpicHostFiles.writeRequest(Data("second".utf8), base: base)
    check((try? Data(contentsOf: victim)) == Data("keep".utf8) && (try? Data(contentsOf: again)) == Data("second".utf8)
          && (try? fm.destinationOfSymbolicLink(atPath: again.path)) == nil, "a link where the request goes is replaced, never followed: the file it pointed to is untouched")
    // A symlink as the folder is refused.
    let linked = temp("linked-base")
    try fm.createSymbolicLink(at: BCDEpicHostFiles.transferFolder(linked), withDestinationURL: base)
    var refused = false
    do { _ = try BCDEpicHostFiles.writeRequest(Data("x".utf8), base: linked) } catch { refused = true }
    check(refused && !fm.fileExists(atPath: base.appendingPathComponent("launch.req").path), "a link as the transfer folder is refused: nothing is written through it")

    check(BCDEpicHostFiles.guestPath(URL(fileURLWithPath: "/var/x/launch.auth")) == "\\\\?\\unix\\var\\x\\launch.auth", "the guest path goes through Wine's Unix namespace, as BCD Dock's transfer does")

    // The ownership token's file.
    check(BCDEpicHostFiles.ovtName(namespace: "ns0123456789abcdef0123456789abcd", catalogItemId: "cat0123456789abcdef0123456789abc") == "ns0123456789abcdef0123456789abcdcat0123456789abcdef0123456789abc.ovt"
          && BCDEpicHostFiles.ovtName(namespace: "a/b", catalogItemId: "c") == nil && BCDEpicHostFiles.ovtName(namespace: "", catalogItemId: "c") == nil
          && BCDEpicHostFiles.ovtName(namespace: "a", catalogItemId: "..") == nil && BCDEpicHostFiles.ovtName(namespace: String(repeating: "a", count: 81), catalogItemId: "c") == nil
          && BCDEpicHostFiles.ovtName(namespace: String(repeating: "a", count: 64), catalogItemId: String(repeating: "b", count: 64)) != nil,
          "the token file's name is the namespace and the catalog item, when both are plain")
    let written = try BCDEpicHostFiles.writeOwnership(OVTREPLY, name: "nsitem.ovt", drive: drive)
    check(written.windows == "C:\\ProgramData\\BCD\\Epic\\nsitem.ovt" && written.file.path.hasSuffix("/ProgramData/BCD/Epic/nsitem.ovt") && (try? Data(contentsOf: written.file)) == OVTREPLY,
          "the token file holds Epic's whole reply, under the prefix's ProgramData")
    check(mode(written.file) == 0o600, "...readable by the app alone")
    let long = try? BCDEpicHostFiles.writeOwnership(OVTREPLY, name: String(repeating: "a", count: 128) + ".ovt", drive: drive)
    check(long != nil, "a token file named by a 64-character namespace and item is written")
    if let long { try? fm.removeItem(at: long.file) }
    var badName = false
    for name in ["../x.ovt", "a/b.ovt", "x.txt", ".ovt", "a b.ovt", "x.ovt/"] {
        do { _ = try BCDEpicHostFiles.writeOwnership(OVTREPLY, name: name, drive: drive); badName = true } catch {}
    }
    check(!badName, "a token file's name with a path, a space or another extension is refused")

    // The report, then clean-up.
    let reportFile = BCDEpicHostFiles.reportFile(drive: drive)
    try Data("stage=10\nerror=0\n".utf8).write(to: reportFile)
    check(BCDEpicHostFiles.readReport(drive: drive)?["stage"] == 10, "the report is read from the prefix's ProgramData\\BCD\\host.txt")
    try Data(count: 5000).write(to: reportFile)
    check(BCDEpicHostFiles.readReport(drive: drive) == nil, "an oversized report is not read")
    try Data("stage=10\n".utf8).write(to: reportFile)
    try Data("other".utf8).write(to: BCDEpicHostFiles.ovtFolder(drive: drive).appendingPathComponent("keep.txt"))
    check(BCDEpicHostFiles.cleanup(base: base, drive: drive) == 2, "clean-up removes the request and the token file (and not the report unless asked)")
    check(!fm.fileExists(atPath: again.path) && !fm.fileExists(atPath: written.file.path) && fm.fileExists(atPath: reportFile.path)
          && fm.fileExists(atPath: BCDEpicHostFiles.ovtFolder(drive: drive).appendingPathComponent("keep.txt").path), "...another file in that folder stays")
    check(BCDEpicHostFiles.cleanup(base: base, drive: drive, report: true) == 1 && !fm.fileExists(atPath: reportFile.path), "...and the report goes when asked")
    check(BCDEpicHostFiles.cleanup(base: base, drive: drive, report: true) == 0 && BCDEpicHostFiles.cleanup(base: temp("nothing"), drive: nil) == 0, "clean-up with nothing there removes nothing and fails nowhere")
}

// MARK: Arguments and decisions

func argumentsPhase() {
    let c = creds(ownership: nil)
    let args = BCDEpicSignedLaunch.epicArguments(app: "Sugar", namespace: "nsfoo", deploymentID: "0123456789abcdef0123456789abcdef", credentials: c, ovtWindowsPath: nil, preferredLanguages: ["tr-TR"])
    check(args == "-AUTH_LOGIN=unused -AUTH_PASSWORD=\(CODE1) -AUTH_TYPE=exchangecode -epicapp=Sugar -epicenv=Prod -EpicPortal -epicusername=\"Secret Display Name\" "
          + "-epicuserid=\(ACCOUNT) -epiclocale=tr -epicsandboxid=nsfoo -epicdeploymentid=0123456789abcdef0123456789abcdef", "Epic's launcher arguments, in the design's order (§2.4)")
    let withToken = BCDEpicSignedLaunch.epicArguments(app: "Sugar", namespace: "nsfoo", deploymentID: nil, credentials: creds(name: "Plain"), ovtWindowsPath: "C:\\ProgramData\\BCD\\Epic\\x.ovt", preferredLanguages: [])
    check(withToken == "-AUTH_LOGIN=unused -AUTH_PASSWORD=\(CODE1) -AUTH_TYPE=exchangecode -epicapp=Sugar -epicenv=Prod -epicovt=C:\\ProgramData\\BCD\\Epic\\x.ovt -EpicPortal "
          + "-epicusername=Plain -epicuserid=\(ACCOUNT) -epiclocale=en -epicsandboxid=nsfoo", "with an ownership token: -epicovt after -epicenv; no deployment ID when there is none; no quotes for a name with no space")
    let noName = BCDEpicSignedLaunch.epicArguments(app: "Sugar", namespace: "nsfoo", deploymentID: nil, credentials: creds(name: nil), ovtWindowsPath: nil, preferredLanguages: ["en"])
    check(noName?.contains("-epicusername") == false && noName?.contains("-epicuserid=") == true, "no display name, no -epicusername")
    check(BCDEpicSignedLaunch.epicArguments(app: "Sugar", namespace: "nsfoo", deploymentID: "short", credentials: c, ovtWindowsPath: nil, preferredLanguages: [])?.contains("-epicdeploymentid") == false,
          "a deployment ID that is not 32 hexadecimal digits is left out")
    func bad(_ cred: BCDEpicCredentials, app: String = "Sugar", ns: String = "nsfoo", ovt: String? = nil) -> Bool {
        BCDEpicSignedLaunch.epicArguments(app: app, namespace: ns, deploymentID: nil, credentials: cred, ovtWindowsPath: ovt, preferredLanguages: []) == nil
    }
    var oddCode = c; oddCode.exchangeCode = "abc def-ghi-jkl-mno-pqr"
    var shortCode = c; shortCode.exchangeCode = "abc"
    var oddAccount = c; oddAccount.accountID = "x y"
    check(bad(oddCode) && bad(shortCode) && bad(oddAccount) && bad(c, app: "bad app") && bad(c, ns: "n s") && bad(c, ovt: "C:\\Other\\x.ovt") && bad(c, ovt: "C:\\ProgramData\\BCD\\Epic\\a b.ovt")
          && bad(c, ovt: "C:\\ProgramData\\BCD\\Epic\\a\".ovt"), "a code, ID, app, sandbox or token path of an odd shape gives no arguments at all")
    check(BCDEpicSignedLaunch.cleanName("A \"quoted\" Name\\") == "A quoted Name" && BCDEpicSignedLaunch.cleanName("  x\u{7}y \\\\ ") == "xy" && BCDEpicSignedLaunch.cleanName("\"\"") == nil
          && BCDEpicSignedLaunch.cleanName(String(repeating: "n", count: 90))?.count == 64 && BCDEpicSignedLaunch.cleanName(nil) == nil, "a display name loses quotes, control characters and trailing backslashes, and is cut at 64")
    let quotedTail = BCDEpicSignedLaunch.epicArguments(app: "Sugar", namespace: "ns", deploymentID: nil, credentials: creds(name: "Back\\"), ovtWindowsPath: nil, preferredLanguages: [])!
    check(quotedTail.contains("-epicusername=Back ") || quotedTail.contains("-epicusername=Back -"), "a name ending in a backslash cannot escape the closing quote")

    check(BCDEpicSignedLaunch.withoutEpicArguments("-windowed -epicapp=X -EPICENV=Dev -AUTH_PASSWORD=abc -fps 60 -epicusername=\"A B\" -epicovt=C:\\x.ovt -EpicPortal -epiclocale=de -epicuserid=1 -epicsandboxid=n -epicdeploymentid=d -AUTH_LOGIN=a -AUTH_TYPE=b -name=\"Some One\"")
          == "-windowed -fps 60 -name=\"Some One\"", "a player's own arguments keep their other words and lose Epic's (the launcher's set is the one that counts)")
    check(BCDEpicSignedLaunch.withoutEpicArguments("") == "" && BCDEpicSignedLaunch.withoutEpicArguments("  -epicapp=X  ") == "" && BCDEpicSignedLaunch.withoutEpicArguments("-epicapplication=1") == "-epicapplication=1",
          "...an empty text, only Epic's words, and a word that only starts like one")
    check(BCDEpicSignedLaunch.tail(gameArguments: "-a -b", epic: "-e") == "-a -b -e" && BCDEpicSignedLaunch.tail(gameArguments: "  ", epic: "-e") == "-e", "the tail: the game's own arguments, then Epic's")
    check(BCDEpicSignedLaunch.carriesSecret("x -AUTH_PASSWORD=abc") && BCDEpicSignedLaunch.carriesSecret("-epicuserid=ff")
          && !BCDEpicSignedLaunch.carriesSecret("-AUTH_PASSWORD=* -epicuserid=<id>") && !BCDEpicSignedLaunch.carriesSecret("-AUTH_PASSWORD= x"), "a line with a value in place of a mask is found; masks are not")
    check(BCDEpicLog.redact("x -AUTH_PASSWORD=\(CODE1) -epicuserid=\(ACCOUNT) -epicusername=\"Secret Display Name\" -epicapp=Sugar") == "x -AUTH_PASSWORD=<redacted> -epicuserid=<redacted> -epicusername=<redacted> -epicapp=Sugar"
          && BCDEpicLog.redact("-epicusername=Plain y") == "-epicusername=<redacted> y", "BCD's own log mask hides Epic's start arguments too")
    check("\(c)" == "BCDEpicCredentials(<redacted>)" && "\(String(reflecting: c))" == "BCDEpicCredentials(<redacted>)", "credentials print as <redacted>")

    // When a start is signed in.
    func decide(_ f: (inout BCDEpicSignedLaunch.Facts) -> Void) -> BCDEpicSignedLaunch.Kind {
        var facts = BCDEpicSignedLaunch.Facts(); f(&facts); return BCDEpicSignedLaunch.decide(facts)
    }
    check(decide { _ in } == .signed, "by default a start is signed in")
    check(decide { $0.signInSwitch = false } == .publicOnly("switch-off") && decide { $0.hostSwitch = false } == .publicOnly("host-off")
          && decide { $0.hostAvailable = false } == .publicOnly("no-host") && decide { $0.hostCannotStart = true } == .publicOnly("no-host-start")
          && decide { $0.offlineChosen = true } == .publicOnly("offline"), "each switch, a build without the host, an entry the host cannot start and a chosen offline start give public arguments only, with their reason")
    check(decide { $0.signedIn = false } == .publicOnly("signed-out") && decide { $0.signedIn = false; $0.needsOnline = true } == .needsSignIn, "signed out: a game that can run without Epic starts as before; one that needs it asks for the sign-in")
    check(decide { $0.signInSwitch = false; $0.signedIn = false; $0.needsOnline = true } == .publicOnly("switch-off")
          && decide { $0.offlineChosen = true; $0.signedIn = false; $0.needsOnline = true } == .publicOnly("offline"), "...the switches and an offline choice come first")
    check(BCDEpicSignedLaunch.offlineAllowed(canRunOffline: nil, ownershipToken: false) && BCDEpicSignedLaunch.offlineAllowed(canRunOffline: true, ownershipToken: false)
          && !BCDEpicSignedLaunch.offlineAllowed(canRunOffline: false, ownershipToken: false) && !BCDEpicSignedLaunch.offlineAllowed(canRunOffline: true, ownershipToken: true), "offline play only for a game Epic does not say must be online and that asks for no token")

    // Tickets.
    let now = Date()
    check(creds(made: now).valid(now: now.addingTimeInterval(10)) && creds(made: now).valid(now: now.addingTimeInterval(238))
          && !creds(made: now).valid(now: now.addingTimeInterval(240)) && !creds(made: now).valid(now: now.addingTimeInterval(-5))
          && creds(made: now, lifetime: 3000).valid(now: now.addingTimeInterval(239)) && !creds(made: now, lifetime: 3000).valid(now: now.addingTimeInterval(240)),
          "a ticket is good for Epic's lifetime less a minute (239 s for 299 s), and never for more than four minutes")
    check(!creds(made: now, lifetime: 90).valid(now: now.addingTimeInterval(31)) && creds(made: now, lifetime: 90).valid(now: now.addingTimeInterval(29)) && creds(made: now, lifetime: 10).valid(now: now.addingTimeInterval(29)),
          "...and for less when Epic's code lasts less (a minute before it lapses, thirty seconds at least)")

    // What the player is told.
    let offline = BCDEpicPrompt.make(for: .offline, offlineAllowed: true, game: "Sugar")
    check(offline.title == "Epic sign-in" && offline.offersRetry && offline.offersOffline && offline.message == "BCD couldn't reach Epic to sign Sugar in. Check the internet connection. You can also start the game without signing in to Epic.",
          "no connection: Try again and Play offline, in plain words")
    let token = BCDEpicPrompt.make(for: .notOwned, offlineAllowed: false, game: "Sugar")
    check(token.offersRetry && !token.offersOffline && !token.message.contains("without signing in") && token.message.hasPrefix("Epic didn't confirm that you own Sugar."), "a game that must be online has no Play offline")
    let signedOut = BCDEpicPrompt.make(for: .signedOut, offlineAllowed: true, game: "Sugar")
    check(!signedOut.offersRetry && signedOut.offersOffline && signedOut.message.contains("Settings › Accounts"), "a lapsed sign-in says where to sign in again, and offers no retry")
    check(!BCDEpicPrompt.make(for: .corrective, offlineAllowed: false, game: "S").offersRetry && BCDEpicPrompt.make(for: .server(503), offlineAllowed: false, game: "S").message.contains("(503)")
          && BCDEpicPrompt.make(for: .reply, offlineAllowed: false, game: "S").offersRetry, "a step on Epic's website cannot be retried; a server error says its status; an odd reply can be retried")
    check(BCDEpicPrompt.needsSignIn(game: "Sugar").message == "Sugar needs your Epic sign-in to start. Sign in to Epic Games in Settings › Accounts, then press Play." && !BCDEpicPrompt.needsSignIn(game: "S").offersRetry,
          "a game that needs the sign-in, started signed out")
    check(offline.answers == [.init(id: "retry", title: "Try again"), .init(id: "offline", title: "Play offline"), .init(id: "cancel", title: "Cancel", cancel: true)]
          && token.answers == [.init(id: "retry", title: "Try again"), .init(id: "cancel", title: "Cancel", cancel: true)]
          && signedOut.answers == [.init(id: "offline", title: "Play offline"), .init(id: "cancel", title: "Cancel", cancel: true)]
          && BCDEpicPrompt.needsSignIn(game: "S").answers == [.init(id: "cancel", title: "OK", cancel: true)], "the buttons: Try again and Play offline when offered, then Cancel (OK when it is the only one)")
    // The wording of the preview app's sample questions (app/BCDPreview/BCDPreviewEpicAsk.swift) is the app's: check-epic.py compares them.
    for (name, prompt) in [("offline", BCDEpicPrompt.make(for: .offline, offlineAllowed: true, game: "Hollow Lantern")),
                           ("owned", BCDEpicPrompt.make(for: .notOwned, offlineAllowed: false, game: "Citadel Keeper")),
                           ("signin", BCDEpicPrompt.needsSignIn(game: "Citadel Keeper"))] {
        print("SAMPLE \(name)|\(prompt.title)|\(prompt.message)|\(prompt.answers.map(\.title).joined(separator: ","))")
    }
    let words = [BCDEpicPlayFailure.offline, .signedOut, .corrective, .notOwned, .server(500), .reply].map(\.logReason)
    check(words == ["offline", "signed-out", "corrective", "not-owned", "http-500", "reply"], "each failure has a short log reason")
    let allMessages = ([BCDEpicPlayFailure.offline, .signedOut, .corrective, .notOwned, .server(500), .reply].map { BCDEpicPrompt.make(for: $0, offlineAllowed: true, game: "G").message } + [BCDEpicPrompt.needsSignIn(game: "G").message]).joined()
    check(!allMessages.contains("Madeira") && allMessages.contains("BCD") && !allMessages.contains("exchange") && !allMessages.contains("token"), "the words say BCD, never Madeira, and none is jargon")
}

// MARK: Epic's side, for the calls of a start

final class LaunchStub: BCDEpicTransport, @unchecked Sendable {
    struct Call { var method: String; var host: String; var path: String; var auth: String; var body: String }
    private let lock = NSLock()
    private var recorded: [Call] = []
    var validAccess: Set<String> = [ACCESS1]
    /// Scripts per endpoint: each call takes the next entry; the last repeats.
    var exchange: [Result<(Int, Data), URLError>] = []
    var ownership: [Result<(Int, Data), URLError>] = []
    var refresh: (Int, Data)?
    var delay: TimeInterval = 0
    private var exchangeIndex = 0, ownershipIndex = 0

    var calls: [Call] { lock.lock(); defer { lock.unlock() }; return recorded }
    func names() -> [String] { calls.map { $0.path.split(separator: "/").last.map(String.init) ?? "" } }

    private func record(_ call: Call) { lock.lock(); recorded.append(call); lock.unlock() }
    private func take(_ script: [Result<(Int, Data), URLError>], _ which: Int) throws -> (Int, Data) {
        lock.lock()
        let i = min(which == 0 ? exchangeIndex : ownershipIndex, script.count - 1)
        if which == 0 { exchangeIndex += 1 } else { ownershipIndex += 1 }
        lock.unlock()
        return try script[i].get()
    }

    func send(_ request: URLRequest) async throws -> (status: Int, body: Data) {
        let url = request.url!
        let auth = request.value(forHTTPHeaderField: "Authorization") ?? ""
        record(Call(method: request.httpMethod ?? "GET", host: url.host ?? "", path: url.path, auth: auth, body: request.httpBody.flatMap { String(data: $0, encoding: .utf8) } ?? ""))
        if delay > 0 { try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000)) }
        let bearer = auth.hasPrefix("bearer ") ? String(auth.dropFirst(7)) : ""
        let good = validAccess.contains(bearer)
        switch url.path {
        case "/account/api/oauth/token":
            if let refresh { return refresh }
            validAccess.insert(ACCESS2)
            return (200, json(["access_token": ACCESS2, "expires_in": 28800, "refresh_token": REFRESH2, "refresh_expires": 115200, "account_id": ACCOUNT, "displayName": NAME]))
        case "/account/api/oauth/exchange":
            guard good else { return (401, json(["errorCode": "errors.com.epicgames.common.authentication.token_verification_failed"])) }
            return try take(exchange, 0)
        default:
            guard url.path.hasSuffix("/ownershipToken") else { return (404, Data()) }
            guard good else { return (401, json(["errorCode": "errors.com.epicgames.common.authentication.token_verification_failed"])) }
            return try take(ownership, 1)
        }
    }
}

func exchangeOK(_ code: String = CODE1) -> Result<(Int, Data), URLError> { .success((200, json(["expiresInSeconds": 299, "code": code, "creatingClientId": BCDEpicAuth.clientID]))) }

func makeAPI(_ stub: LaunchStub, signedIn: Bool = true, refresh: String = REFRESH1, access: String = ACCESS1, expiresIn: TimeInterval = 3600) async -> BCDEpicAPI {
    let secrets = BCDMemorySecrets()
    if signedIn {
        let tokens = BCDEpicTokens(accessToken: access, refreshToken: refresh, accessExpires: Date().addingTimeInterval(expiresIn), refreshExpires: nil, accountID: ACCOUNT, displayName: NAME)
        try! secrets.write(BCDEpicSession.itemName, JSONEncoder().encode(tokens))
    }
    let session = BCDEpicSession(secrets: secrets, transport: stub)
    return BCDEpicAPI(session: session, transport: stub)
}

func failure(_ api: BCDEpicAPI, ownership: Bool = false, deadline: TimeInterval = 25) async -> BCDEpicPlayFailure? {
    do { _ = try await api.credentials(namespace: "nsfoo", catalogItemId: "catbar", needsOwnership: ownership, deadline: deadline); return nil }
    catch { return error as? BCDEpicPlayFailure }
}

func credentialsPhase() async {
    // The requests.
    let exchange = BCDEpicCredentialsAPI.exchangeRequest(accessToken: "T")
    check(exchange.url?.absoluteString == "https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/exchange" && (exchange.httpMethod ?? "GET") == "GET"
          && exchange.value(forHTTPHeaderField: "Authorization") == "bearer T" && exchange.timeoutInterval == 10, "the exchange request: a GET at the account service with the session's token, ten seconds")
    let owner = BCDEpicCredentialsAPI.ownershipRequest(accessToken: "T", accountID: ACCOUNT, namespace: "nsfoo", catalogItemId: "catbar")
    check(owner?.url?.absoluteString == "https://ecommerceintegration-public-service-ecomprod02.ol.epicgames.com/ecommerceintegration/api/public/platforms/EPIC/identities/\(ACCOUNT)/ownershipToken"
          && owner?.httpMethod == "POST" && owner?.value(forHTTPHeaderField: "Content-Type") == "application/x-www-form-urlencoded"
          && String(data: owner?.httpBody ?? Data(), encoding: .utf8) == "nsCatalogItemId=nsfoo%3Acatbar", "the ownership request: a POST with nsCatalogItemId=<namespace>:<item> as a form")
    check(BCDEpicCredentialsAPI.ownershipRequest(accessToken: "T", accountID: "a/b", namespace: "n", catalogItemId: "c") == nil
          && BCDEpicCredentialsAPI.ownershipRequest(accessToken: "T", accountID: ACCOUNT, namespace: "n s", catalogItemId: "c") == nil
          && BCDEpicCredentialsAPI.ownershipRequest(accessToken: "T", accountID: ACCOUNT, namespace: "n", catalogItemId: "../c") == nil, "an ID that is not plain makes no request")
    check(BCDEpicAPI.trusted(exchange.url!) && BCDEpicAPI.trusted(owner!.url!), "both addresses are on Epic's own hosts (the only ones the token goes to)")
    check(BCDEpicCredentialsAPI.exchange(from: json(["code": CODE1, "expiresInSeconds": 299]))?.code == CODE1 && BCDEpicCredentialsAPI.exchange(from: json(["code": CODE1, "expiresInSeconds": 299]))?.lifetime == 299
          && BCDEpicCredentialsAPI.exchange(from: json(["code": CODE1]))?.lifetime == 299 && BCDEpicCredentialsAPI.exchange(from: json(["code": CODE1, "expiresInSeconds": 5]))?.lifetime == 30
          && BCDEpicCredentialsAPI.exchange(from: json(["code": "a b"])) == nil && BCDEpicCredentialsAPI.exchange(from: json(["code": "short"])) == nil
          && BCDEpicCredentialsAPI.exchange(from: json(["nope": 1])) == nil && BCDEpicCredentialsAPI.exchange(from: Data("not json".utf8)) == nil, "the exchange reply: its code and its lifetime (299 s when it names none)")
    check(BCDEpicCredentialsAPI.ownershipReply(from: OVTREPLY) == OVTREPLY && BCDEpicCredentialsAPI.ownershipReply(from: json(["token": "plain"])) == nil
          && BCDEpicCredentialsAPI.ownershipReply(from: json(["nope": "egoc1~x"])) == nil && BCDEpicCredentialsAPI.ownershipReply(from: Data(repeating: 0x20, count: 20_000)) == nil,
          "the ownership reply is kept exactly as received, and only when it holds an egoc1~ token")

    // A plain start: only the code.
    var stub = LaunchStub(); stub.exchange = [exchangeOK()]
    var api = await makeAPI(stub)
    var got = try? await api.credentials(namespace: "nsfoo", catalogItemId: "catbar", needsOwnership: false)
    check(got?.exchangeCode == CODE1 && got?.accountID == ACCOUNT && got?.displayName == NAME && got?.ownership == nil && got?.codeLifetime == 299, "a start asks Epic for the code and has it, with the account")
    check(stub.names() == ["exchange"] && stub.calls[0].auth == "bearer " + ACCESS1 && stub.calls[0].host == BCDEpicAuth.accountHost, "...with one call, to the account service, with the session's token")

    // With an ownership token: the token first, the code last.
    stub = LaunchStub(); stub.exchange = [exchangeOK()]; stub.ownership = [.success((200, OVTREPLY))]
    api = await makeAPI(stub)
    got = try? await api.credentials(namespace: "nsfoo", catalogItemId: "catbar", needsOwnership: true)
    check(stub.names() == ["ownershipToken", "exchange"] && got?.ownership == OVTREPLY && got?.exchangeCode == CODE1, "a game that asks for an ownership token: the token is asked for first and the code last (it lasts only minutes)")
    check(stub.calls[0].body == "nsCatalogItemId=nsfoo%3Acatbar" && stub.calls[0].path.contains("/identities/\(ACCOUNT)/"), "...the ownership call names the account and the item")

    // A token that lapsed is refreshed first.
    stub = LaunchStub(); stub.exchange = [exchangeOK(CODE2)]
    api = await makeAPI(stub, access: ACCESS1, expiresIn: 100)
    got = try? await api.credentials(namespace: "nsfoo", catalogItemId: "catbar", needsOwnership: false)
    check(stub.names() == ["token", "exchange"] && stub.calls[1].auth == "bearer " + ACCESS2 && got?.exchangeCode == CODE2, "a session token about to lapse is refreshed before the call")

    // A 401 is refreshed once and tried again.
    stub = LaunchStub(); stub.validAccess = []; stub.exchange = [exchangeOK()]
    api = await makeAPI(stub)
    got = try? await api.credentials(namespace: "nsfoo", catalogItemId: "catbar", needsOwnership: false)
    check(stub.names() == ["exchange", "token", "exchange"] && got?.exchangeCode == CODE1, "a refused token is refreshed once and the call made again")
    stub = LaunchStub(); stub.validAccess = []; stub.refresh = (400, json(["errorCode": "errors.com.epicgames.account.oauth.invalid_refresh"]))
    api = await makeAPI(stub)
    var f = await failure(api)
    check(f == .signedOut, "a refresh Epic refuses ends the sign-in: the player is told to sign in again")

    // Retries.
    stub = LaunchStub(); stub.exchange = [.success((503, Data())), exchangeOK()]
    api = await makeAPI(stub)
    got = try? await api.credentials(namespace: "nsfoo", catalogItemId: "catbar", needsOwnership: false)
    check(stub.names() == ["exchange", "exchange"] && got?.exchangeCode == CODE1, "a server error is tried once more")
    stub = LaunchStub(); stub.exchange = [.success((500, Data()))]
    api = await makeAPI(stub); f = await failure(api)
    check(f == .server(500) && stub.names() == ["exchange", "exchange"], "...and then reported with its status")
    stub = LaunchStub(); stub.exchange = [.failure(URLError(.timedOut)), exchangeOK()]
    api = await makeAPI(stub); got = try? await api.credentials(namespace: "nsfoo", catalogItemId: "catbar", needsOwnership: false)
    check(got?.exchangeCode == CODE1 && stub.names() == ["exchange", "exchange"], "a time-out is tried once more")
    stub = LaunchStub(); stub.exchange = [.failure(URLError(.notConnectedToInternet))]
    api = await makeAPI(stub); f = await failure(api)
    check(f == .offline && stub.names() == ["exchange", "exchange"], "no connection (twice) is reported as offline")
    stub = LaunchStub(); stub.exchange = [.failure(URLError(.cancelled))]
    api = await makeAPI(stub); f = await failure(api)
    check(f == .offline && stub.names() == ["exchange"], "an error that is not the network's is not retried")

    // Failures.
    stub = LaunchStub(); stub.exchange = [exchangeOK()]; stub.ownership = [.success((403, json(["errorCode": "errors.com.epicgames.ecommerce.forbidden"])))]
    api = await makeAPI(stub); f = await failure(api, ownership: true)
    check(f == .notOwned && stub.names() == ["ownershipToken"], "Epic refusing the ownership token: not owned, and no code is asked for")
    stub = LaunchStub(); stub.exchange = [.success((400, json(["errorCode": "errors.com.epicgames.oauth.corrective_action_required", "correctiveAction": "PRIVACY_POLICY_ACCEPTANCE"])))]
    api = await makeAPI(stub); f = await failure(api)
    check(f == .corrective, "a step Epic wants finished first")
    stub = LaunchStub(); stub.exchange = [.success((200, json(["nope": 1])))]
    api = await makeAPI(stub); f = await failure(api)
    check(f == .reply, "a reply with no code")
    stub = LaunchStub(); stub.exchange = [exchangeOK()]; stub.ownership = [.success((200, json(["token": "plain"])))]
    api = await makeAPI(stub); f = await failure(api, ownership: true)
    check(f == .reply, "a token reply that is not a token")
    stub = LaunchStub(); stub.exchange = [exchangeOK()]
    api = await makeAPI(stub, signedIn: false); f = await failure(api)
    check(f == .signedOut && stub.calls.isEmpty, "signed out: no call is made")
    stub = LaunchStub(); stub.exchange = [exchangeOK()]; stub.delay = 5
    api = await makeAPI(stub)
    let started = Date()
    f = await failure(api, deadline: 0.4)
    check(f == .offline && Date().timeIntervalSince(started) < 2.5, "a start has a deadline: Epic not answering ends it as offline")

    check(BCDEpicAPI.failure(from: BCDEpicError.signInRefused) == .signedOut && BCDEpicAPI.failure(from: BCDEpicError.http(429)) == .server(429)
          && BCDEpicAPI.failure(from: BCDEpicError.rejected(status: 404, code: "x")) == .notOwned && BCDEpicAPI.failure(from: BCDEpicError.rejected(status: 400, code: "x")) == .server(400)
          && BCDEpicAPI.failure(from: BCDEpicError.badReply("x")) == .reply && BCDEpicAPI.failure(from: URLError(.badURL)) == .offline && BCDEpicAPI.failure(from: CancellationError()) == .offline
          && BCDEpicAPI.failure(from: NSError(domain: "x", code: 1)) == .reply, "every error of the calls becomes one of the failures a start reports")

    let all = snapshotLogs()
    let joined = all.joined(separator: "\n")
    check(SECRETS.filter { joined.contains($0) }.isEmpty && !joined.contains("eg1~") && !joined.contains("egoc1~"), "no code, token, account ID or name in any log line of these calls")
    check(all.contains { $0.hasPrefix("[epic-launch] code status=200 ms=") } && all.contains { $0.hasPrefix("[epic-launch] ownership status=200 ms=") }
          && all.contains { $0.hasPrefix("[epic-launch] code retry reason=net-") }, "the lines say the step, the status and the time: code, ownership, and a retry with its reason")
    check(all.filter { $0.hasPrefix("[epic-launch]") }.allSatisfy { $0.range(of: #"^\[epic-launch\] (code|ownership) (status=\d+ ms=\d+|retry reason=net-\-?\d+ ms=\d+)$"#, options: .regularExpression) != nil },
          "...and nothing else (no address, no value)")
}

// MARK: The container search

func auditPhase() throws {
    let root = temp("audit")
    let fm = FileManager.default
    func put(_ rel: String, _ data: Data) throws {
        let url = root.appendingPathComponent(rel)
        try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url)
    }
    let needles = BCDEpicAudit.needles(code: CODE1, accountID: ACCOUNT, token: OVTTOKEN)
    check(needles.filter { $0.label == "code" }.count == 2 && needles.filter { $0.label == "token" }.count == 2 && needles.contains { $0.label == "arg" && $0.wide },
          "the search looks for the exact code, ID and token (each as ASCII and as UTF-16) and for Epic's markers")
    check(BCDEpicAudit.needles(code: "short", accountID: nil, token: nil).filter { ["code", "id", "token"].contains($0.label) }.isEmpty, "a value too short to be one is not searched for")

    try put("clean/log.txt", Data("a normal log line\n[epic-launch] start app=Sugar signed-in=1\nNtCreateUserProcess: cmdline=\"a.exe -AUTH_PASSWORD=* -epicuserid=* -epicusername=* -epicapp=Sugar\"\negoc1~<redacted> eg1~<token>\n".utf8))
    try put("clean/masked-at-end.txt", Data("x -AUTH_PASSWORD=".utf8))
    try put("clean/quote.txt", Data("\"-AUTH_PASSWORD=\" and '-epicuserid=' and -epicusername= end".utf8))
    try put("hit/code.log", Data("noise \(CODE1) noise".utf8))
    try put("hit/args.log", Data("cmd -AUTH_PASSWORD=abcdef0123456789 -x".utf8))
    try put("hit/userid.log", Data("cmd -epicuserid=\(ACCOUNT)".utf8))
    try put("hit/token.log", Data("{\"token\":\"\(OVTTOKEN)\"}".utf8))
    try put("hit/family.log", Data("bearer eg1~realtoken.abc.def".utf8))
    try put("hit/wide.log", Data(("id=" + ACCOUNT).utf16.flatMap { [UInt8($0 & 0xFF), UInt8($0 >> 8)] }))
    try put("hit/wide-arg.log", Data("-epicusername=Bob ".utf16.flatMap { [UInt8($0 & 0xFF), UInt8($0 >> 8)] }))
    try put("clean/wide-masked.log", Data("-epicusername=* x".utf16.flatMap { [UInt8($0 & 0xFF), UInt8($0 >> 8)] }))
    try put("skip/Program Files/log.txt", Data(CODE1.utf8))
    try put("typed/game.log", Data(CODE1.utf8))
    try put("typed/game.bin", Data(CODE1.utf8))
    // A match that crosses the read boundary (a megabyte), in a large file.
    var big = Data(repeating: 0x61, count: (1 << 20) - 10)
    big.append(Data(CODE1.utf8)); big.append(Data(repeating: 0x62, count: 3 << 20))
    try put("hit/big.log", big)
    var bigClean = Data(repeating: 0x61, count: (1 << 20) - 6)
    bigClean.append(Data("-AUTH_PASSWORD=*".utf8)); bigClean.append(Data(repeating: 0x62, count: 2 << 20))
    try put("clean/big.log", bigClean)
    try fm.createSymbolicLink(at: root.appendingPathComponent("link.log"), withDestinationURL: root.appendingPathComponent("hit/code.log"))
    try fm.createSymbolicLink(at: root.appendingPathComponent("linkdir"), withDestinationURL: root.appendingPathComponent("hit"))

    let everything = BCDEpicAudit.run(roots: [.init(name: "t", url: root, skip: ["program files"], suffixes: [".log", ".txt"])], needles: needles, maxHits: 40)
    let hitFiles = Set(everything.hits.map(\.file))
    check(everything.counts["code"] == 3 && everything.counts["id"] == 2 && everything.counts["token"] == 1 && everything.counts["arg"] == 3 && everything.counts["family"] == 2 && everything.total == 11 && !everything.truncated,
          "the search finds each file that holds a code, ID, token or an unmasked Epic argument, once per kind: \(everything.summary)")
    check(everything.hits.count == 11 && hitFiles.contains("t:/hit/big.log") && hitFiles.contains("t:/hit/code.log") && hitFiles.contains("t:/hit/wide.log") && hitFiles.contains("t:/hit/wide-arg.log")
          && hitFiles.contains("t:/typed/game.log") && !hitFiles.contains { $0.contains("clean/") || $0.contains("Program Files") || $0.contains("game.bin") || $0.contains("link") },
          "...a match across the read boundary and one in UTF-16 are found; masked lines, skipped folders, other file kinds and links are not read")
    check(everything.files == 14 && everything.bytes > 7_000_000, "...the search counts the files and bytes it read (\(everything.files), \(everything.bytes))")
    check(everything.summary.hasPrefix("files=14 bytes=") && everything.summary.contains(" hits=11 code=3 id=2 token=1 arg=3 family=2 truncated=0 ms="), "the summary line is numbers only")
    check(!everything.hits.contains { $0.file.contains(CODE1) || $0.file.contains(ACCOUNT) }, "a hit names a file by its place, never by what was found")
    let limited = BCDEpicAudit.run(roots: [.init(name: "t", url: root, maxFiles: 3)], needles: needles)
    check(limited.truncated && limited.files <= 3, "a root's file limit ends the search for that root and says so")
    let none = BCDEpicAudit.run(roots: [.init(name: "t", url: root.appendingPathComponent("clean"))], needles: needles)
    check(none.total == 0 && none.files == 5, "a folder with nothing to find: no hits (\(none.summary))")
    check(BCDEpicAudit.run(roots: [.init(name: "t", url: root.appendingPathComponent("missing"))], needles: needles).files == 0, "a missing folder is no error")
    check(BCDEpicAudit.relativeForLog("/wine/drive_c/users/Some Person/AppData/x.log") == "/wine/drive_c/users/<user>/AppData/x.log" && BCDEpicAudit.relativeForLog("/a\u{1}b|c/é.log") == "/abc/.log"
          && BCDEpicAudit.relativeForLog("/" + String(repeating: "a", count: 200)).count == 90, "a file's place for the log: its user folder hidden, odd characters dropped, at most 90 characters")
    try? fm.removeItem(at: root)
}

// MARK: The rules milestone 2 changes

func rulesPhase() {
    // The anti-cheat exception.
    let legacy = ["GTA5.exe", "PlayGTAV.exe", "x64a.rpf", "BattlEye/BEClient_x64.dll", "BattlEye/BEService_x64.dll", "Launcher.exe"]
    let enhanced = ["GTA5_Enhanced.exe", "GTA5.exe", "PlayGTAV.exe", "BattlEye/BEClient_x64.dll", "BattlEye/BEService_x64.dll"]
    check(BCDEpicRules.antiCheats(in: legacy) == ["BattlEye"] && BCDEpicRules.antiCheats(in: ["EasyAntiCheat/x.dll", "BEClient.dll", "equ8/x"]) == ["Easy Anti-Cheat", "BattlEye", "EQU8"] && BCDEpicRules.antiCheats(in: ["a.exe"]).isEmpty,
          "every anti-cheat the files show, each once")
    check(BCDEpicRules.antiCheatException(files: legacy, launchExe: "PlayGTAV.exe")?.id == "gta5-legacy" && BCDEpicRules.antiCheatException(files: enhanced, launchExe: "playgtav.exe")?.id == "gta5-enhanced",
          "GTA V Legacy and GTA V Enhanced are told apart by the files beside PlayGTAV.exe (Enhanced has GTA5_Enhanced.exe)")
    check(BCDEpicRules.manifestRefusal(files: legacy, launchExe: "PlayGTAV.exe") == nil && BCDEpicRules.manifestRefusal(files: enhanced, launchExe: "PlayGTAV.exe") == nil, "...so neither is refused for its BattlEye files")
    check(BCDEpicRules.antiCheatException(files: legacy, launchExe: "GTA5.exe") == nil && BCDEpicRules.manifestRefusal(files: legacy, launchExe: "GTA5.exe")?.code == "anti-cheat",
          "the exception is for the program the game starts with (PlayGTAV.exe): GTA5.exe as the launch program is refused as before")
    check(BCDEpicRules.manifestRefusal(files: ["PlayGTAV.exe", "BattlEye/BEClient.dll"], launchExe: "PlayGTAV.exe")?.code == "anti-cheat", "PlayGTAV.exe with no GTA5.exe beside it is not GTA V: refused")
    check(BCDEpicRules.manifestRefusal(files: legacy + ["EasyAntiCheat/EasyAntiCheat.exe"], launchExe: "PlayGTAV.exe")?.message == "This game comes with anti-cheat software (BattlEye), which can't run in BCD.",
          "GTA V with another anti-cheat as well is refused (the first one found is named)")
    check(BCDEpicRules.manifestRefusal(files: ["Game.exe", "BattlEye/BEClient.dll"], launchExe: "Game.exe")?.code == "anti-cheat" && BCDEpicRules.manifestRefusal(files: ["Game.exe", "EasyAntiCheat/x.dll"], launchExe: "Game.exe")?.code == "anti-cheat",
          "any other game with BattlEye or Easy Anti-Cheat is refused, as in milestone 1")
    check(BCDEpicRules.manifestRefusal(files: ["PlayGTAV.exe", "GTA5.exe"], launchExe: "PlayGTAV.exe") == nil && BCDEpicRules.antiCheatException(files: ["PlayGTAV.exe", "GTA5.exe"], launchExe: "PlayGTAV.exe") == nil,
          "a game with no anti-cheat has no exception (nothing to except)")
    check(BCDEpicRules.antiCheatExceptions.count == 2 && BCDEpicRules.antiCheatExceptions.allSatisfy { $0.antiCheat == "BattlEye" && $0.argument == "-nobattleye" && $0.program == "playgtav.exe" && $0.reason.contains("-nobattleye") && $0.reason.contains("GTA Online") },
          "the list is short and explicit: two GTA V items, BattlEye only, -nobattleye, with the reason in words")
    check(BCDEpicRules.gtaReason == "This game's anti-cheat (BattlEye) guards only GTA Online. BCD starts the game with its own -nobattleye option, so Story Mode plays and GTA Online stays off.", "the reason shown")

    // The arguments of a game with an exception.
    let withArg = BCDEpicLaunch.arguments(app: "Sugar", manifestCommand: "-a", extraCommand: "", deploymentID: nil, preferredLanguages: ["en"], exceptionArgument: "-nobattleye")
    check(withArg.text == "-a -nobattleye -epicapp=Sugar -epicenv=Prod -EpicPortal -epiclocale=en" && withArg.dropped == 0, "the exception's option goes with the game's own arguments, before Epic's public ones")
    check(BCDEpicLaunch.arguments(app: "Sugar", manifestCommand: "-nobattleye", extraCommand: "", deploymentID: nil, preferredLanguages: ["en"], exceptionArgument: "-nobattleye").text
          == "-nobattleye -epicapp=Sugar -epicenv=Prod -EpicPortal -epiclocale=en", "...and not twice when the game already has it")
    check(BCDEpicLaunch.arguments(app: "Sugar", manifestCommand: "-a", extraCommand: "", deploymentID: nil, preferredLanguages: ["en"]).text == "-a -epicapp=Sugar -epicenv=Prod -EpicPortal -epiclocale=en",
          "a game without an exception has the arguments it had")
    check(BCDEpicLaunch.gameParts(manifestCommand: "-a", extraCommand: "-b", exceptionArgument: "-nobattleye").parts == ["-a", "-b", "-nobattleye"] && BCDEpicLaunch.gameParts(manifestCommand: "\"x", extraCommand: "", exceptionArgument: nil).dropped == 1,
          "the game's own parts: the manifest's, the catalog's, the option; an unsafe string is dropped and counted")

    // The ownership token and the notes.
    var item = BCDEpicItem(id: "Sugar", namespace: "ns", catalogItemId: "c", kind: .game, title: "Sugar")
    item.ownershipToken = true
    check(BCDEpicRules.catalogRefusal(item) == nil && BCDEpicRules.catalogRefusal(item, signedStart: true) == nil, "an ownership-token game is no longer refused: BCD starts it signed in")
    check(BCDEpicRules.catalogRefusal(item, signedStart: false)?.code == "ownership-token" && BCDEpicRules.catalogRefusal(item, signedStart: false)?.message.contains("switched off") == true,
          "...unless the signed-in start is switched off (or has no host): then the refusal of milestone 1 stands, saying why")
    check(BCDEpicRules.catalogWarning(item) == BCDEpicRules.tokenNote && BCDEpicRules.catalogWarning(item, signedStart: false) == nil, "...and the page warns that it needs a connection and may carry copy protection of its own")
    check(BCDEpicRules.tokenNote.contains("needs an internet connection") && BCDEpicRules.tokenNote.contains("copy protection"), "the token note's words")
    item.ownershipToken = false; item.canRunOffline = false
    check(BCDEpicRules.catalogWarning(item) == BCDEpicRules.onlineNote && BCDEpicRules.catalogWarning(item, signedStart: false) == BCDEpicRules.offlineNote
          && BCDEpicRules.onlineNote.contains("signed in to your Epic account"), "a game that must be online: BCD starts it signed in; with the sign-in off the old warning")
    item.thirdParty = "Ubisoft"; item.ownershipToken = true
    check(BCDEpicRules.catalogRefusal(item)?.code == "other-launcher", "another company's launcher is still refused first")
    item.thirdParty = nil; item.accessControl = true
    check(BCDEpicRules.catalogRefusal(item)?.code == "access-control", "...and Epic's access control")
    item.accessControl = nil; item.ownershipToken = nil; item.canRunOffline = nil
    check(BCDEpicRules.catalogRefusal(item) == nil && BCDEpicRules.catalogWarning(item) == nil, "a plain game has neither")

    let facts = BCDEpicFacts(buildVersion: "1", installBytes: 10, downloadBytes: 5, eos: true, refusal: nil, exception: BCDEpicRules.antiCheatExceptions[0])
    check(BCDEpicRules.advice(item: item, remembered: nil, facts: facts).warning == BCDEpicRules.gtaReason && BCDEpicRules.advice(item: item, remembered: nil, facts: facts).blocker == nil,
          "the page says the exception's reason beside Install")
    let eosFacts = BCDEpicFacts(buildVersion: "1", installBytes: 10, downloadBytes: 5, eos: true, refusal: nil)
    check(BCDEpicRules.advice(item: item, remembered: nil, facts: eosFacts).warning == BCDEpicRules.eosNote(signedStart: true) && BCDEpicRules.advice(item: item, remembered: nil, facts: eosFacts, signedStart: false).warning == BCDEpicRules.eosNote(signedStart: false),
          "the Epic Online Services note follows the switch")
    check(BCDEpicRules.eosNote(signedStart: true).contains("starts it signed in") && !BCDEpicRules.eosNote(signedStart: true).contains("can't pass your Epic sign-in") && BCDEpicRules.eosNote(signedStart: false).contains("switched off"),
          "...in words")
    var record = BCDEpicInstallRecord(app: "Sugar", namespace: "ns", catalogItemId: "c", title: "Sugar", buildVersion: "1", manifestHash: "h", featureLevel: 21, root: BCDEpicInstall.root, folder: "Sugar",
                                      bytes: 1, launchPath: "PlayGTAV.exe", launchCommand: "", extraCommand: "", deploymentID: nil, canRunOffline: nil, eos: true, prerequisiteNote: nil,
                                      ownershipToken: nil, exceptionID: "gta5-legacy", arguments: "", entryID: nil, installed: Date())
    check(record.exception?.id == "gta5-legacy" && record.note() == BCDEpicRules.gtaReason, "an installed game under an exception says why on its page")
    record.exceptionID = nil
    check(record.note() == BCDEpicRules.eosNote(signedStart: true) && record.note(signedStart: false) == BCDEpicRules.eosNote(signedStart: false), "an installed Epic Online Services game's note follows the switch")
    record.ownershipToken = true
    check(record.note() == BCDEpicRules.tokenNote, "...a token game's note first")
    check(BCDEpicInstall.launch(BCDEpicInstallRecord(app: "Sugar", namespace: "ns", catalogItemId: "c", title: "Sugar", buildVersion: "1", manifestHash: "h", featureLevel: 21, root: BCDEpicInstall.root, folder: "Grand Theft Auto V",
                                                     bytes: 1, launchPath: "PlayGTAV.exe", launchCommand: "", extraCommand: "", deploymentID: nil, canRunOffline: nil, eos: false, prerequisiteNote: nil,
                                                     ownershipToken: nil, exceptionID: "gta5-enhanced", arguments: "", entryID: nil, installed: Date()), preferredLanguages: ["en"]).arguments
          == "-nobattleye -epicapp=Sugar -epicenv=Prod -EpicPortal -epiclocale=en", "the library entry of a game under an exception starts with its option")
    // Records of milestone 1 still read.
    let old = Data(#"{"version":1,"app":"A","namespace":"n","catalogItemId":"c","title":"T","buildVersion":"1","manifestHash":"h","featureLevel":21,"root":{"drive":"C","folder":"Program Files/Epic Games"},"folder":"T","bytes":1,"launchPath":"a.exe","launchCommand":"","extraCommand":"","eos":false,"arguments":"","installed":1800000000}"#.utf8)
    let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .secondsSince1970
    let decoded = try? decoder.decode(BCDEpicInstallRecord.self, from: old)
    check(decoded?.ownershipToken == nil && decoded?.exceptionID == nil && decoded?.exception == nil, "a record written by milestone 1 (no token flag, no exception) still reads")
    check(BCDEpicRules.claimedApp("epic.Sugar", enabled: false) == nil && BCDEpicRules.claimedApp("epic.Sugar", enabled: true) == "Sugar" && BCDEpicRules.claimedApp("steam.1", enabled: true) == nil,
          "with Epic off no id is Epic's; with it on, only an \"epic.\" id")
}

func printPreviewWords() {
    print("WORDS token|\(BCDEpicRules.tokenNote)")
    print("WORDS online|\(BCDEpicRules.onlineNote)")
    print("WORDS eos|\(BCDEpicRules.eosNote(signedStart: true))")
    print("WORDS gta|\(BCDEpicRules.gtaReason)")
}

// MARK: Samples for the C reader (tests/host/check-epic.py runs bcd-host's parser on them)

func writeSamples(_ directory: URL) throws {
    var samples: [[String: Any]] = []
    func add(_ name: String, _ request: BCDEpicHostRequest, parses: Bool = true) throws {
        let data = try request.encoded()
        try data.write(to: directory.appendingPathComponent(name + ".req"))
        samples.append(["name": name, "flags": Int(request.flags.rawValue), "program": request.program, "cwd": request.workingFolder ?? "", "arguments": request.arguments, "ovt": request.ovt ?? "",
                        "root": request.root ?? "", "parses": parses])
    }
    let c = creds(ownership: OVTREPLY)
    let epic = BCDEpicSignedLaunch.epicArguments(app: "Sugar", namespace: "nsfoo", deploymentID: "0123456789abcdef0123456789abcdef", credentials: c,
                                                 ovtWindowsPath: "C:\\ProgramData\\BCD\\Epic\\nsfoocatbar.ovt", preferredLanguages: ["tr"])!
    try add("signed", BCDEpicHostRequest(program: "C:\\Program Files\\Epic Games\\Sugar Game\\Sugar.exe", workingFolder: "C:\\Program Files\\Epic Games\\Sugar Game",
                                         arguments: BCDEpicSignedLaunch.tail(gameArguments: "-nobattleye -windowed", epic: epic), ovt: "C:\\ProgramData\\BCD\\Epic\\nsfoocatbar.ovt",
                                         root: "C:\\Program Files\\Epic Games", flags: [.tree]))
    try add("services", BCDEpicHostRequest(program: "C:\\Program Files\\Epic Games\\Üñí 日本\\go.exe", workingFolder: nil, arguments: "-name=\"😀 x\"", ovt: nil, root: "C:\\Program Files\\Epic Games", flags: [.tree, .services]))
    try add("plain", BCDEpicHostRequest(program: "C:\\a.exe", workingFolder: nil, arguments: "", ovt: nil, root: nil, flags: []))
    try add("outside", BCDEpicHostRequest(program: "C:\\Other\\a.exe", workingFolder: nil, arguments: "", ovt: nil, root: "C:\\Program Files\\Epic Games", flags: []), parses: false)
    try add("badovt", BCDEpicHostRequest(program: "C:\\a.exe", workingFolder: nil, arguments: "", ovt: "C:\\Windows\\x.ovt", root: nil, flags: []), parses: false)
    try add("notexe", BCDEpicHostRequest(program: "C:\\a.bat", workingFolder: nil, arguments: "", ovt: nil, root: nil, flags: []), parses: false)
    try JSONSerialization.data(withJSONObject: samples).write(to: directory.appendingPathComponent("samples.json"))
}

@main struct Harness {
    static func main() async {
        installSink()
        requestPhase()
        do { try filesPhase() } catch { check(false, "files: \(error)") }
        argumentsPhase()
        await credentialsPhase()
        do { try auditPhase() } catch { check(false, "audit: \(error)") }
        rulesPhase()
        printPreviewWords()
        if let out = ProcessInfo.processInfo.environment["EPIC_LAUNCH_OUT"] {
            do { try writeSamples(URL(fileURLWithPath: out)); check(true, "sample requests written for the C reader") } catch { check(false, "samples: \(error)") }
        }
        print(failures == 0 ? "EPIC-LAUNCH-HARNESS: all passed" : "EPIC-LAUNCH-HARNESS: \(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
