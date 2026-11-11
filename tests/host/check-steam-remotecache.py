#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Valve's client's remotecache.vdf, rewritten from the device (SteamCloud.swift, SteamRemoteCache); no Steam runs.

Valve's client in a BCD Dock session answers ISteamRemoteStorage only from the files its remotecache.vdf lists; for a game
BCD starts itself that client runs just its "init" sync and an init with no list deletes the file, so the saves BCD
downloaded were invisible to the game (Slay the Spire 2 started a new profile over them). SteamRemoteCache rewrites the
list after each cloud comparison. It is OFF in BCD unless env.MADEIRA_STEAM_CLOUD_REMOTECACHE is set (upstream turns it
on): it changes a file the client reads for every game with Steam Cloud API saves.

  1. the production SteamRemoteCache is compiled on the host with the comparison types and run over temporary prefixes:
     parse and format round-trip a client-written file (quoting, tabs, nested file blocks), a damaged one is refused;
     write() lists a file identical to the cloud's as synced (syncstate 1) at the cloud's change number, keeps the client's
     entry for a file that differs or that only the device has (or gives a new one syncstate 3), leaves a cloud-only file
     out and removes its old entry, keeps entries under another root (AutoCloud), writes nothing when nothing changes, and
     does nothing for an app with no remote files and no list;
  2. the source: the switch is read with default false (any value but 0 turns it on), the one call is gated by it and by
     "not in a session", the rewrite touches only remotecache.vdf next to the remote folder.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc") or str(Path.home() / ".local/share/swiftly/bin/swiftc")
failures = 0


def require(cond, label):
    global failures
    print(("PASS: " if cond else "FAIL: ") + label)
    if not cond:
        failures += 1


cloud = (APP / "SteamCloud.swift").read_text()
owned = (APP / "SteamOwnedLibrary.swift").read_text()
compare = cloud[cloud.index("// MARK: - Where a cloud path lives in the Wine prefix"):cloud.index("// MARK: - Download")]
remote = cloud[cloud.index("// MARK: - Valve's client's file list"):cloud.index("// MARK: - What to do with a comparison")]
plan = cloud[cloud.index("// MARK: - What to do with a comparison"):cloud.index("// MARK: - Upload")]

# ------------------------------------------------------------------ source
require('static var cloudRemoteCache: Bool { SteamSignIn.flag("MADEIRA_STEAM_CLOUD_REMOTECACHE", default: false) }' in owned,
        "the rewrite is off unless env.MADEIRA_STEAM_CLOUD_REMOTECACHE is set")
require(owned.count("SteamRemoteCache.write(") == 1
        and "if Self.cloudRemoteCache, !inSession, let written = SteamRemoteCache.write(appID: appID, audit: audit, paths: paths)" in owned,
        "the one call is gated by the switch and by 'no session running'")
require('appendingPathComponent("remotecache.vdf")' in remote and remote.count("write(to:") == 1 and remote.count(".removeItem") == 0,
        "it writes one file, remotecache.vdf beside the remote folder, and deletes nothing")
require('"syncstate", "1"' in remote.replace('("syncstate", "1")', '"syncstate", "1"') and '("syncstate", "3")' in remote,
        "synced files are state 1, files only the device has state 3")

# ------------------------------------------------------------------ Swift
main = r'''
import Foundation
enum SteamAppInfo {
    struct RootOverride: Equatable { var root: String; var os: String; var useInstead: String; var addPath: String }
    struct SaveFile: Equatable { var root: String; var path: String; var pattern: String; var recursive: Bool; var platforms: [String] }
}
struct SteamCloudFile { var prefix: String; var name: String; var sha: Data; var timestamp: UInt64; var size: UInt64; var persistState: UInt32
    var path: String { prefix + name } }
struct SteamCloudListing { var changeNumber: UInt64 = 0; var files: [SteamCloudFile] = [] }
var failed = 0
func check(_ ok: Bool, _ label: String) { print((ok ? "PASS: " : "FAIL: ") + label); if !ok { failed += 1 } }

let clientFile = """
"2868840"
{
\t"ChangeNumber"\t\t"77"
\t"ostype"\t\t"-184"
\t"progress.save"
\t{
\t\t"root"\t\t"0"
\t\t"size"\t\t"100"
\t\t"localtime"\t\t"1700000000"
\t\t"time"\t\t"1700000001"
\t\t"remotetime"\t\t"1700000001"
\t\t"sha"\t\t"aaaa"
\t\t"syncstate"\t\t"3"
\t\t"persiststate"\t\t"0"
\t\t"platformstosync2"\t\t"-1"
\t}
\t"autocloud.dat"
\t{
\t\t"root"\t\t"2"
\t\t"size"\t\t"5"
\t\t"sha"\t\t"bbbb"
\t\t"syncstate"\t\t"1"
\t}
}

"""
let parsed = SteamRemoteCache.parse(clientFile)
check(parsed != nil && parsed?.appID == "2868840" && parsed?.header.count == 2 && parsed?.files.count == 2, "parse: app id, two header fields, two files")
check(parsed?.files.first?.name == "progress.save" && parsed?.files.first?.fields.count == 9, "  a file block keeps its nine fields in order")
check(parsed.map { SteamRemoteCache.parse(SteamRemoteCache.format($0)) == $0 } ?? false, "format then parse gives the same list")
check(SteamRemoteCache.parse("\"1\"\n{\n\t\"ChangeNumber\"\t\t\"1\"\n") == nil, "a list with no closing brace is refused")
check(SteamRemoteCache.parse("garbage") == nil && SteamRemoteCache.parse("") == nil, "garbage and an empty file are refused")
let quoted = SteamRemoteCache.List(appID: "9", header: [("k", "a\"b\\c")], files: [("we\"ird", [("x", "y")])])
check(SteamRemoteCache.parse(SteamRemoteCache.format(quoted)) == quoted, "quotes and backslashes survive the round trip")

func sha(_ b: UInt8) -> Data { Data(repeating: b, count: 20) }
func hex(_ b: UInt8) -> String { SteamCloudPlan.hex(sha(b)) }
func entry(_ path: String, _ kind: SteamCloudEntry.Kind, size: UInt64 = 10, ltime: UInt64 = 111, ctime: UInt64 = 222, local: UInt8 = 1, cloud: UInt8 = 1) -> SteamCloudEntry {
    var e = SteamCloudEntry(path: path, kind: kind)
    e.localSize = size; e.localTime = ltime; e.cloudTime = ctime; e.localSHA = sha(local); e.cloudSHA = sha(cloud)
    return e
}
func lines(_ list: SteamRemoteCache.List?) -> [String: [String: String]] {
    var out: [String: [String: String]] = [:]
    for f in list?.files ?? [] { out[f.name] = Dictionary(f.fields.map { ($0.key, $0.value) }, uniquingKeysWith: { $1 }) }
    return out
}

let tmp = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("remotecache-\(getpid())", isDirectory: true)
func prefix(_ name: String) -> (SteamCloudPaths, URL) {
    let app = tmp.appendingPathComponent(name, isDirectory: true)
    let remote = app.appendingPathComponent("remote", isDirectory: true)
    try! FileManager.default.createDirectory(at: remote, withIntermediateDirectories: true)
    let drive = tmp.appendingPathComponent("drive_c", isDirectory: true)
    let paths = SteamCloudPaths(drive: drive, userFolder: drive, installFolder: drive, remoteFolder: remote, steamID: 1, overrides: [])
    return (paths, app.appendingPathComponent("remotecache.vdf"))
}

// 1. no file, files only the cloud has: nothing to write
do {
    let (paths, vdf) = prefix("empty")
    check(SteamRemoteCache.write(appID: 5, audit: SteamCloudAudit(), paths: paths) == nil && !FileManager.default.fileExists(atPath: vdf.path),
          "no remote files and no list: nothing is written")
}

// 2. a fresh list from the device
do {
    let (paths, vdf) = prefix("fresh")
    var audit = SteamCloudAudit(changeNumber: 42)
    audit.entries = [entry("a.save", .same, size: 7, ltime: 5, ctime: 6, local: 3, cloud: 3),
                     entry("b.save", .differ, local: 4, cloud: 5),
                     entry("c.save", .localOnly, size: 9, ltime: 8, local: 6, cloud: 0),
                     entry("d.save", .cloudOnly, cloud: 7)]
    let w = SteamRemoteCache.write(appID: 5, audit: audit, paths: paths)
    check(w?.synced == 1 && w?.local == 2 && w?.dropped == 0, "counts: one synced, two device-side, none dropped (\(String(describing: w)))")
    let list = (try? String(contentsOf: vdf, encoding: .utf8)).flatMap(SteamRemoteCache.parse)
    let f = lines(list)
    check(list?.appID == "5" && list?.header.first(where: { $0.key == "ChangeNumber" })?.value == "42", "the app id and the cloud's change number")
    check(f["a.save"]?["syncstate"] == "1" && f["a.save"]?["sha"] == hex(3) && f["a.save"]?["remotetime"] == "6" && f["a.save"]?["size"] == "7",
          "a file identical to the cloud's is listed as synced at the cloud's time")
    check(f["b.save"]?["syncstate"] == "3" && f["b.save"]?["remotetime"] == "0" && f["b.save"]?["sha"] == hex(4), "a file that differs gets a device-side entry (state 3)")
    check(f["c.save"]?["syncstate"] == "3" && f["c.save"]?["size"] == "9", "a file only the device has gets one too")
    check(f["d.save"] == nil, "a file only the cloud has is not listed")
    check(SteamRemoteCache.write(appID: 5, audit: audit, paths: paths) == nil, "the same comparison again: nothing is rewritten")
}

// 3. the client's own list: its entries for differing files are kept, a cloud-only file's old entry goes, AutoCloud roots stay
do {
    let (paths, vdf) = prefix("client")
    try! Data(clientFile.utf8).write(to: vdf)
    var audit = SteamCloudAudit(changeNumber: 80)
    audit.entries = [entry("progress.save", .differ, local: 9, cloud: 8),
                     entry("gone.save", .cloudOnly, cloud: 2),
                     entry("autocloud.dat", .same, local: 1, cloud: 1)]
    let w = SteamRemoteCache.write(appID: 2868840, audit: audit, paths: paths)
    let list = (try? String(contentsOf: vdf, encoding: .utf8)).flatMap(SteamRemoteCache.parse)
    let f = lines(list)
    check(f["progress.save"]?["sha"] == "aaaa" && f["progress.save"]?["syncstate"] == "3", "a differing file keeps the client's own entry")
    check(f["autocloud.dat"]?["root"] == "2" && f["autocloud.dat"]?["sha"] == "bbbb", "an entry under another root is kept as it is")
    check(list?.header.first(where: { $0.key == "ChangeNumber" })?.value == "80" && list?.header.first(where: { $0.key == "ostype" })?.value == "-184",
          "the change number is updated and the client's other header fields stay")
    check(w != nil, "the changed change number is a change: written")
    var dropAudit = audit
    dropAudit.entries = [entry("progress.save", .differ, local: 9, cloud: 8), entry("old.save", .cloudOnly, cloud: 4)]
    try! Data(SteamRemoteCache.format(SteamRemoteCache.List(appID: "2868840", header: [("ChangeNumber", "80"), ("ostype", "-184")],
        files: [("old.save", [("root", "0"), ("syncstate", "1")]), ("progress.save", [("root", "0"), ("sha", "aaaa"), ("syncstate", "3")])])).utf8).write(to: vdf)
    let w2 = SteamRemoteCache.write(appID: 2868840, audit: dropAudit, paths: paths)
    let f2 = lines((try? String(contentsOf: vdf, encoding: .utf8)).flatMap(SteamRemoteCache.parse))
    check(w2?.dropped == 1 && f2["old.save"] == nil && f2["progress.save"] != nil, "a file only the cloud has loses its old entry")
}

// 4. a damaged list is replaced, not trusted
do {
    let (paths, vdf) = prefix("damaged")
    try! Data("not a vdf".utf8).write(to: vdf)
    var audit = SteamCloudAudit(changeNumber: 3)
    audit.entries = [entry("x.save", .same, local: 2, cloud: 2)]
    let w = SteamRemoteCache.write(appID: 6, audit: audit, paths: paths)
    let f = lines((try? String(contentsOf: vdf, encoding: .utf8)).flatMap(SteamRemoteCache.parse))
    check(w?.synced == 1 && f["x.save"]?["syncstate"] == "1", "a damaged list is rebuilt from the device")
}
try? FileManager.default.removeItem(at: tmp)
exit(failed == 0 ? 0 : 1)
'''

with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / "main.swift"
    src.write_text("import Foundation\nimport CryptoKit\n" + compare + "\n" + remote + "\n" + plan + "\n" + main.replace("import Foundation\n", "", 1))
    out = Path(tmp) / "remotecache"
    # SteamCloud.swift's paths check an external drive's folder (ExternalStorage.swift, which reads VDF with SteamKeyValues.swift).
    build = subprocess.run([SWIFTC, "-O", str(src), str(APP / "ExternalStorage.swift"), str(APP / "SteamKeyValues.swift"), "-o", str(out)],
                           capture_output=True, text=True)
    if build.returncode != 0:
        print(build.stderr[-3500:])
        require(False, "the production SteamRemoteCache and the comparison types compile on the host")
    else:
        run = subprocess.run([str(out)], capture_output=True, text=True)
        print(run.stdout, end="")
        if run.returncode != 0:
            failures += 1
            print(run.stderr[-1500:])

print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILURE(S)" % failures))
sys.exit(0 if failures == 0 else 1)
