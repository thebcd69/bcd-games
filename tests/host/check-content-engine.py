#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's store-neutral content engine (app/Madeira/BCDContent/; docs/GOG.md).

Never contacts a store: the content is synthetic, served by a local HTTP server built here.

Source checks (every host):
  - BCD's licence header, exactly, on every engine file; each is built by the Xcode project
    with an ID from the content range (A1000C00-A1000C3F / A2000C00-A2000C3F);
  - the engine files are Foundation-only, except the background task and the sign-in sheet;
  - background continuation: its own "<bundle>.download.stores" task under Info.plist's
    ".download.*" entry, Steam's ".download.queue" left as it is, Steam's two switches read
    through SteamDownloadBackground (no new read site), one download at a time with Steam;
  - log lines carry no address, path, token, code or secret.
Behaviour (swiftc on the host; --require-swift makes its absence a failure): the production
engine files compiled with a harness and run against a local server with a dead host, a host
that corrupts each chunk's first answer, one that drops a connection halfway (Range), a slow
one and one that refuses addresses until they are renewed:
  - units: path rules (hostile names), folder folding, file/folder clash, a later file
    replacing an earlier one, plan coverage and identity, the journal (torn and foreign
    records), streaming zlib in any piece size, truncated and trailing data, redaction;
  - install: every file byte for byte through dead and corrupting hosts, each chunk taken once
    from the healthy host, shared and sliced chunks, empty files, folded folders;
  - a download killed (SIGKILL) and resumed fetches none of the journaled chunks again;
  - pause (cancel) keeps the journal; resume completes without refetching it;
  - not enough space is refused before anything is fetched;
  - an external drive (log FCS8QA): its figure is read through the drive_c/External link as Steam reads it; files that
    already hold their room (exFAT) are not asked for it again; the device's rule is as before; a refusal logs its figures;
  - a dropped connection continues with a Range request; refused addresses are renewed once;
  - whole-file checks: a file that keeps failing its check fails the job after one refetch;
  - the queue: a job to the end, a game session pausing and resuming it, waiting for another
    downloader, cancel removing staged files and journal, pause kept across a restart;
  - memory: a 32 MiB download in 8 MiB chunks raises the process's peak memory by far less
    than one chunk per stream.

  check-content-engine.py [--require-swift]
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import random
import re
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
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
    """The text without whole-line comments."""
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
          "// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
FILES = ["BCDContentPlan.swift", "BCDContentCodec.swift", "BCDContentJournal.swift", "BCDContentFetch.swift",
         "BCDContentEngine.swift", "BCDContentQueue.swift", "BCDContentSecrets.swift", "BCDContentBackground.swift",
         "BCDWebSignIn.swift"]
HOST_FILES = ["BCDContentPlan.swift", "BCDContentCodec.swift", "BCDContentJournal.swift", "BCDContentFetch.swift",
              "BCDContentEngine.swift", "BCDContentQueue.swift", "BCDContentSecrets.swift"]

# ---- source checks ------------------------------------------------------------------------
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
present = sorted(p.name for p in CONTENT.glob("*.swift"))
check(present == sorted(FILES), "app/Madeira/BCDContent holds exactly the engine's files", str(present))
for name in FILES:
    text = (CONTENT / name).read_text()
    check(text.startswith(HEADER), f"{name}: BCD's licence header, exactly")
    build = re.findall(r"(A1000C[0-3][0-9A-F]) /\* BCDContent/" + re.escape(name) + r" in Sources \*/ = \{isa = PBXBuildFile; fileRef = (A2000C[0-3][0-9A-F])", pbx)
    ref = re.findall(r"(A2000C[0-3][0-9A-F]) /\* " + re.escape(name) + r' \*/ = \{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "BCDContent/' + re.escape(name) + '"', pbx)
    ok = len(build) == 1 and len(ref) == 1 and build[0][1] == ref[0] \
        and pbx.count(f"{build[0][0]} /* BCDContent/{name} in Sources */,") == 1 and pbx.count(f"{ref[0]} /* {name} */,") == 1
    check(ok, f"{name}: built by the Xcode project (content ID range, group and Sources phase)")
    imports = set(re.findall(r"^import (\w+)", text, re.M))
    if name in HOST_FILES:
        check(imports <= {"Foundation", "CryptoKit", "zlib", "Darwin", "Glibc", "Combine", "Security", "FoundationNetworking"},
              f"{name}: Foundation-level imports only (the host checks compile it)", str(sorted(imports)))
    body = code(text)
    for call in re.findall(r"BCDContentLog\.write\((.*)\)", body):
        bad = [e for e in re.findall(r"\\\(([^)]*)\)", call)
               if re.search(r"(?i)url|path|token|code|secret|stage|folder|host|title|name", e)]
        check(not bad, f"{name}: a log line carries no address, path, token or name", call)

background = (CONTENT / "BCDContentBackground.swift").read_text()
steam_background = (APP / "SteamDownloadBackground.swift").read_text()
info = (APP / "Info.plist").read_text()
check("<string>$(PRODUCT_BUNDLE_IDENTIFIER).download.*</string>" in info, "Info.plist permits <bundle>.download.* tasks")
check('String($0.dropLast()) + "stores"' in background and 'String(wildcard.dropLast()) + "queue"' in steam_background,
      "the stores' continued-processing task is <bundle>.download.stores, Steam keeps <bundle>.download.queue")
check("SteamDownloadBackground.continuedEnabled" in background and "SteamDownloadBackground.notificationsEnabled" in background
      and not re.search(r'flag\("MADEIRA_', code(background)),
      "the background reads Steam's two switches through SteamDownloadBackground (no new read site)")
check("request.strategy = .fail" in background and "BGContinuedProcessingTaskRequest(identifier: identifier, title: title, subtitle: subtitle)" in background,
      "continued processing as Steam's: submitted at a job's start, with its title and subtitle")
queue_src = code((CONTENT / "BCDContentQueue.swift").read_text())
check("guard active == nil, !inSession, !otherDownloadActive(), let key = order.first" in queue_src,
      "the queue starts nothing during a session or while another downloader works")

# Log FCS8QA (build 686): an external drive's figure is read as Steam's URL.freeSpace reads it (Steam installs on these
# drives and was not refused): the same steps in the same order. The drive's "is internal" answer decides nothing.
engine_src = code((CONTENT / "BCDContentEngine.swift").read_text())
steam_src = code((APP / "SwiftSteam/Content/DepotDownloader.swift").read_text())


def body_after(src, head):
    start = src.find(head)
    if start < 0:
        return ""
    end = src.find("\n    }\n", start)
    return src[start:end if end > 0 else len(src)]


def space_steps(body):
    return re.findall(r"resolvingSymlinksInPath|volumeAvailableCapacityForImportantUsage\b|volumeAvailableCapacity\b"
                      r"|statfs\(|return nil", body)


drive_steps = space_steps(body_after(engine_src, "static func driveFreeSpace(_ url: URL) -> Int64? {"))
check(len(drive_steps) >= 6 and drive_steps == space_steps(body_after(steam_src, "public var freeSpace: Int64? {")),
      "an external drive's free space takes the steps of Steam's URL.freeSpace", drive_steps)
check("volumeIsInternal" not in engine_src, "no volume's \"is internal\" answer decides the room")
device_branch = body_after(engine_src, "static func freeSpace(_ url: URL) -> Int64? {")
check(re.search(r"if onDevice\(probe\) \{\s*return \(try\? probe\.resourceValues\(forKeys: "
                r"\[\.volumeAvailableCapacityForImportantUsageKey\]\)\)\?\s*\.volumeAvailableCapacityForImportantUsage\s*\}",
                device_branch) is not None,
      "on the device the figure is the important-usage one alone, as before")
check(engine_src.count("options.onDevice(stage)") == 2 and engine_src.count("device ? remaining : min(remaining") == 2,
      "both room checks keep the device's rule and count a drive's files' held room")

# ---- fixtures -----------------------------------------------------------------------------
rng = random.Random(20261018)


def blob(n, compressible=True):
    if compressible:
        words = [bytes(rng.choice(b"abcdefghij") for _ in range(rng.randint(3, 9))) for _ in range(64)]
        out = bytearray()
        while len(out) < n:
            out += rng.choice(words) + b" "
        return bytes(out[:n])
    return rng.randbytes(n)


class PlanFixture:
    def __init__(self):
        self.files, self.chunks, self.parts, self.dirs = [], [], [], []
        self.wire = {}               # locator -> bytes on the wire
        self.by_locator = {}

    def chunk(self, raw, codec="zlib"):
        wire = zlib.compress(raw, 6) if codec == "zlib" else raw
        loc = hashlib.md5(wire).hexdigest()
        if loc in self.by_locator:
            return self.by_locator[loc]
        self.chunks.append(dict(locator=loc, fetchSize=len(wire), size=len(raw), fetchMd5=loc,
                                md5=hashlib.md5(raw).hexdigest(), codec=codec))
        self.wire[loc] = wire
        self.by_locator[loc] = len(self.chunks) - 1
        return len(self.chunks) - 1

    def file(self, path, pieces):
        """pieces: [(chunk index, offset in chunk, length)] in file order."""
        index = len(self.files)
        at = 0
        for c, off, length in pieces:
            self.parts.append(dict(chunk=c, chunkOffset=off, length=length, file=index, fileOffset=at))
            at += length
        self.files.append(dict(path=path, size=at, md5=None))
        return index

    def json(self):
        return dict(files=self.files, chunks=self.chunks, parts=self.parts, dirs=self.dirs)


def main_plan():
    p = PlanFixture()
    p.dirs = ["saves", "Data\\Sub"]
    exe = [blob(300000), blob(300000), blob(300000), blob(123457)]
    exe_chunks = [p.chunk(b) for b in exe]
    small = blob(10000)
    c_small = p.chunk(small)
    box = blob(50000, compressible=False)
    c_box = p.chunk(box, codec="stored")
    readme_old, readme_new = blob(5000), blob(6000)
    c_old, c_new = p.chunk(readme_old), p.chunk(readme_new)
    expect = {}
    p.file("bin\\game.exe", [(c, 0, len(b)) for c, b in zip(exe_chunks, exe)])
    expect["bin/game.exe"] = b"".join(exe)
    p.files[-1]["md5"] = hashlib.md5(expect["bin/game.exe"]).hexdigest()
    p.file("Data\\Sub\\x.bin", [(c_small, 0, len(small))]); expect["Data/Sub/x.bin"] = small
    p.file("data\\copy.bin", [(c_small, 0, len(small))]); expect["Data/copy.bin"] = small
    p.file("Data\\small1.txt", [(c_box, 0, 20000)]); expect["Data/small1.txt"] = box[:20000]
    p.file("data\\small2.txt", [(c_box, 20000, 15000)]); expect["Data/small2.txt"] = box[20000:35000]
    p.files[-1]["md5"] = hashlib.md5(box[20000:35000]).hexdigest()
    p.file("small3.txt", [(c_box, 35000, 15000)]); expect["small3.txt"] = box[35000:]
    p.file("DATA\\small1-copy.txt", [(c_box, 0, 20000)]); expect["Data/small1-copy.txt"] = box[:20000]
    p.file("Readme.txt", [(c_old, 0, len(readme_old))])
    p.file("README.TXT", [(c_new, 0, len(readme_new))]); expect["README.TXT"] = readme_new
    p.file("logs\\empty.log", []); expect["logs/empty.log"] = b""
    return p, expect


def single_plan(name, size, compressible=True, bad_digest=False):
    p = PlanFixture()
    data = blob(size, compressible)
    c = p.chunk(data)
    p.file(name, [(c, 0, size)])
    p.files[-1]["md5"] = ("0" * 32) if bad_digest else hashlib.md5(data).hexdigest()
    return p, {name.replace("\\", "/"): data}


def big_plan():
    p = PlanFixture()
    expect = {}
    for i in range(4):
        data = blob(8 << 20, compressible=False)
        c = p.chunk(data)
        p.file(f"pak{i}.bin", [(c, 0, len(data))])
        expect[f"pak{i}.bin"] = data
    return p, expect


# ---- the local content server ---------------------------------------------------------------
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.chunks = {}
        self.log = []          # (label, locator, range)
        self.seen = set()


S = State()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, status, body=b"", headers=()):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        parts = self.path.split("/")
        if len(parts) != 5 or parts[3] != "chunk":
            return self.reply(404)
        label, token, loc = parts[1], parts[2], parts[4]
        rng_header = self.headers.get("Range")
        with S.lock:
            S.log.append((label, loc, rng_header))
            first = (label, loc) not in S.seen
            S.seen.add((label, loc))
        body = S.chunks.get(loc)
        mode = label.rstrip("0123456789")
        if body is None:
            return self.reply(404)
        if mode == "auth" and token != "t2":
            return self.reply(403)
        if mode == "slow":
            time.sleep(0.3)
        if mode == "corrupt" and first:
            body = body[:len(body) // 2] + bytes([body[len(body) // 2] ^ 0xFF]) + body[len(body) // 2 + 1:]
        if mode == "drop" and first and not rng_header:
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(body[:len(body) // 2])
            self.wfile.flush()
            time.sleep(0.5)          # the client takes what came, then the connection drops
            self.close_connection = True
            return
        if rng_header:
            m = re.fullmatch(r"bytes=(\d+)-", rng_header)
            start = int(m.group(1)) if m else 0
            return self.reply(206, body[start:], [("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}"),
                                                  ("Accept-Ranges", "bytes")])
        if mode == "pace":
            # A fast but real network: about 40 MB/s per connection.
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for at in range(0, len(body), 256 << 10):
                self.wfile.write(body[at:at + (256 << 10)])
                time.sleep(0.006)
            return
        self.reply(200, body, [("Accept-Ranges", "bytes")])


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass   # a client that hung up (a cancelled or paused download) is expected here


# ---- the Swift harness ----------------------------------------------------------------------
HARNESS = r'''
import Foundation
#if canImport(Darwin)
import Darwin
#endif

var failures = 0
func check(_ ok: Bool, _ what: String) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
    fflush(stdout)
}
func result(_ pairs: [(String, Any)]) {
    print("RESULT " + pairs.map { "\($0.0)=\($0.1)" }.joined(separator: " "))
    fflush(stdout)
}

struct PlanFixture: Decodable {
    struct File: Decodable { var path: String; var size: Int64; var md5: String? }
    struct Chunk: Decodable { var locator: String; var fetchSize: Int64; var size: Int64; var fetchMd5: String?; var md5: String?; var codec: String }
    struct Part: Decodable { var chunk: Int; var chunkOffset: Int64; var length: Int64; var file: Int; var fileOffset: Int64 }
    var files: [File]; var chunks: [Chunk]; var parts: [Part]; var dirs: [String]
}
struct Fixture: Decodable {
    var plans: [String: PlanFixture]
    var base: String
    /// A second server: another host for the host checks' rotation.
    var base2: String
    var work: String
    var zlibVector: String
    var zlibPlainMd5: String
    var jsonZlib: String
}

func makePlan(_ f: PlanFixture, id: String, folder: String = "Game") throws -> BCDContentPlan {
    var b = try BCDContentPlanBuilder(key: BCDContentKey(store: "test", id: id), title: "Test", version: "1", folder: folder)
    for d in f.dirs { try b.addDirectory(d) }
    var map: [Int: Int] = [:]
    for (i, c) in f.chunks.enumerated() {
        map[i] = try b.addChunk(BCDContentChunk(locator: c.locator, group: "g", fetchSize: c.fetchSize, size: c.size,
                                                fetchDigest: c.fetchMd5.flatMap { BCDDigest(.md5, $0) },
                                                digest: c.md5.flatMap { BCDDigest(.md5, $0) }, codec: c.codec == "zlib" ? .zlib : .stored))
    }
    for (i, file) in f.files.enumerated() {
        let index = try b.addFile(file.path, size: file.size, digest: file.md5.flatMap { BCDDigest(.md5, $0) })
        for p in f.parts where p.file == i {
            try b.addPart(chunk: map[p.chunk]!, chunkOffset: p.chunkOffset, length: p.length, file: index, fileOffset: p.fileOffset)
        }
    }
    return try b.build()
}

final class TestSource: BCDChunkSource, @unchecked Sendable {
    private let lock = NSLock()
    private let hosts: [String]
    private var token = "t1"
    private(set) var refreshes = 0
    init(_ hosts: [String]) { self.hosts = hosts }
    func urls(for chunk: BCDContentChunk) async throws -> [URL] {
        let token = lock.withLock { self.token }
        return hosts.map { URL(string: "\($0)/\(token)/chunk/\(chunk.locator)")! }
    }
    func refresh(group: String) async throws {
        lock.withLock { refreshes += 1; token = "t2" }
    }
}

func options(streams: Int = 4, durableSeconds: Double = 3) -> BCDContentOptions {
    var o = BCDContentOptions()
    o.backoff = 0
    o.streams = streams
    o.durableSeconds = durableSeconds
    return o
}

func folder(_ fx: Fixture, _ name: String) -> URL { URL(fileURLWithPath: fx.work).appendingPathComponent(name, isDirectory: true) }

func journalCount(_ url: URL) -> Int {
    let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
    return max(0, (size - 72) / 8)
}

func expectError(_ what: String, _ body: () async throws -> Void, _ match: (Error) -> Bool) async {
    do { try await body(); check(false, what + " (no error)") } catch { check(match(error), what + " (\(error))") }
}

// MARK: units

func units(_ fx: Fixture) async {
    for bad in ["", "/abs/x", "..\\x", "a/../b", "a/./b", "C:\\x.exe", "a//b", "CON", "con.txt", "Lpt1.dat", "a\u{1}b",
                "trail.", "trail ", "a/b?", "x|y", "q\"q", "<x>", String(repeating: "a", count: 300)] {
        check((try? BCDContentPaths.components(bad)) == nil, "the path \(bad.debugDescription.prefix(24)) is refused")
    }
    check((try? BCDContentPaths.components("Data\\Sub\\x.bin")) == ["Data", "Sub", "x.bin"], "Windows separators become folders")
    check(BCDContentPaths.folderName("The Witcher 2") == "The Witcher 2" && BCDContentPaths.folderName("a:b?.") == "a_b_"
          && BCDContentPaths.folderName("CON") == nil && BCDContentPaths.folderName("../x") == ".._x",
          "install folder names are made safe")
    do {
        var b = try BCDContentPlanBuilder(key: .init(store: "t", id: "1"), title: "T", version: "1", folder: "G")
        try b.addDirectory("Data")
        let f1 = try b.addFile("data\\y.bin", size: 0, digest: nil)
        let f2 = try b.addFile("DATA/Z.bin", size: 0, digest: nil)
        let plan = try b.build()
        check(plan.files[f1].path == "Data/y.bin" && plan.files[f2].path == "Data/Z.bin", "a folder spelled two ways is one folder, first spelling")
    } catch { check(false, "folding: \(error)") }
    do {
        var b = try BCDContentPlanBuilder(key: .init(store: "t", id: "1"), title: "T", version: "1", folder: "G")
        try b.addFile("a/b", size: 0, digest: nil)
        check((try? b.addFile("A/B/c", size: 0, digest: nil)) == nil, "a file and a folder of the same name are refused")
        var c = try BCDContentPlanBuilder(key: .init(store: "t", id: "1"), title: "T", version: "1", folder: "G")
        try c.addFile("a/b/c", size: 0, digest: nil)
        check((try? c.addFile("A/B", size: 0, digest: nil)) == nil, "...in either order")
    } catch { check(false, "clash: \(error)") }
    check((try? BCDContentPlanBuilder(key: .init(store: "t", id: "1"), title: "T", version: "1", folder: "a/b")) == nil,
          "an install folder must be one safe name")
    // Coverage and identity.
    let main = fx.plans["main"]!
    do {
        let plan = try makePlan(main, id: "u")
        let again = try makePlan(main, id: "u")
        check(plan.identity == again.identity && plan.identity.count == 64, "a plan's identity is stable")
        check(!plan.chunks.contains { c in main.chunks.firstIndex { $0.locator == c.locator } == 6 },
              "a replaced file's chunk leaves the plan")
        check(plan.files.contains { $0.path == "README.TXT" } && !plan.files.contains { $0.path == "Readme.txt" },
              "a later file at the same path (letter case aside) replaces the earlier one")
        var tampered = plan
        tampered.parts[0].length -= 1
        check((try? tampered.validate()) == nil, "a changed part fails the plan's identity")
        tampered.identity = tampered.computeIdentity()
        check((try? tampered.validate()) == nil, "a part that leaves a gap in its file is refused")
        let data = try JSONEncoder().encode(plan)
        let back = try JSONDecoder().decode(BCDContentPlan.self, from: data)
        check((try? back.validate()) != nil && back.identity == plan.identity, "a plan survives its file")
        check(BCDContentEngine.remainingBytes(plan, done: []) == plan.totalBytes, "nothing done: every byte remains")
    } catch { check(false, "plan: \(error)") }
    // The journal.
    let dir = folder(fx, "units")
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let journalURL = dir.appendingPathComponent("journal")
    let identity = String(repeating: "ab", count: 32)
    do {
        let journal = try BCDContentJournal(url: journalURL, identity: identity, keep: [])
        try journal.append([3, 5, 9])
        let handle = try FileHandle(forWritingTo: journalURL)
        try handle.seekToEnd()
        try handle.write(contentsOf: Data([7, 0, 0, 0, 0, 0, 0, 0]))     // 7 with a wrong complement
        try handle.write(contentsOf: Data([1, 0, 0]))                    // torn
        try handle.close()
        check(BCDContentJournal.load(journalURL, identity: identity, chunkCount: 100) == [3, 5, 9],
              "the journal keeps whole, checked records; a torn or wrong one is ignored")
        check(BCDContentJournal.load(journalURL, identity: String(repeating: "cd", count: 32), chunkCount: 100).isEmpty,
              "another plan's journal counts for nothing")
        check(BCDContentJournal.load(journalURL, identity: identity, chunkCount: 6) == [3, 5], "records past the plan are ignored")
        let reopened = try BCDContentJournal(url: journalURL, identity: identity, keep: [3, 9])
        try reopened.append([11])
        check(BCDContentJournal.load(journalURL, identity: identity, chunkCount: 100) == [3, 9, 11], "a journal is rewritten with what is kept")
    } catch { check(false, "journal: \(error)") }
    // Streaming zlib.
    let packed = Data(base64Encoded: fx.zlibVector)!
    for piece in [1, 7, 4096, packed.count] {
        do {
            let inflater = try BCDInflater(outputSize: 1000)
            var hasher = BCDHasher(.md5)
            var at = 0
            while at < packed.count {
                let slice = packed[at..<min(packed.count, at + piece)]
                try slice.withUnsafeBytes { try inflater.feed($0) { hasher.update($0) } }
                at += piece
            }
            try inflater.finish()
            check(hasher.finalize() == fx.zlibPlainMd5, "zlib unpacks the same in pieces of \(piece) bytes")
        } catch { check(false, "zlib pieces \(piece): \(error)") }
    }
    do {
        let inflater = try BCDInflater()
        try packed.dropLast(6).withUnsafeBytes { try inflater.feed($0) { _ in } }
        check((try? inflater.finish()) == nil, "a cut zlib stream is refused")
    } catch { check(true, "a cut zlib stream is refused") }
    check((try? BCDZlib.inflate(packed + Data([1, 2, 3]), limit: 1 << 24)) == nil, "bytes after a zlib stream are refused")
    check((try? BCDZlib.inflate(packed, limit: 100)) == nil, "an unpacked size over the limit is refused")
    check((try? BCDZlib.jsonData(Data(" {\"a\":1}".utf8), limit: 100)) == Data(" {\"a\":1}".utf8)
          && (try? BCDZlib.jsonData(Data(base64Encoded: fx.jsonZlib)!, limit: 100)) == Data("{\"a\":1}".utf8),
          "a reply may be plain JSON or zlib")
    // Digests and redaction.
    check(BCDDigest(.md5, "XYZ") == nil && BCDDigest(.md5, String(repeating: "A", count: 32))?.hex == String(repeating: "a", count: 32),
          "digests are checked hex, kept lowercase")
    let leaked = BCDContentLog.redact("a?access_token=SEKRIT1&code=SEKRIT2 Bearer SEKRIT3 {\"refresh_token\": \"SEKRIT4\", \"client_secret\":\"SEKRIT5\"} password=SEKRIT6")
    check(!leaked.contains("SEKRIT"), "redaction masks tokens, codes, secrets and passwords: \(leaked)")
}

// MARK: downloads

func download(_ fx: Fixture, plan: BCDContentPlan, name: String, hosts: [String], opts: BCDContentOptions,
              source: TestSource? = nil) async throws {
    let base = folder(fx, name)
    let source = source ?? TestSource(hosts)
    try await BCDContentEngine.download(plan, stage: base.appendingPathComponent("stage"), jobFolder: base.appendingPathComponent("job"),
                                        source: source, options: opts) { _ in }
}

func commit(_ fx: Fixture, plan: BCDContentPlan, name: String) throws {
    let base = folder(fx, name)
    try BCDContentEngine.commit(stage: base.appendingPathComponent("stage"), to: base.appendingPathComponent("root/" + plan.folder))
}

func install(_ fx: Fixture) async {
    do {
        let plan = try makePlan(fx.plans["main"]!, id: "install")
        try await download(fx, plan: plan, name: "install", hosts: ["http://127.0.0.1:1/dead", fx.base + "/corrupt", fx.base2 + "/good"], opts: options())
        try commit(fx, plan: plan, name: "install")
        check(true, "the install finished through a dead and a corrupting host")
        check((try? BCDContentEngine.commit(stage: folder(fx, "install").appendingPathComponent("stage"),
                                            to: folder(fx, "install").appendingPathComponent("root/" + plan.folder))) == nil,
              "a commit never replaces an existing folder")
        var values = try folder(fx, "install").appendingPathComponent("root/Game").resourceValues(forKeys: [.isExcludedFromBackupKey])
        check(values.isExcludedFromBackup == true, "the installed folder is kept out of the device's backup")
        values = URLResourceValues()
    } catch { check(false, "install: \(error)") }
}

func slow(_ fx: Fixture) async {
    do {
        let plan = try makePlan(fx.plans["main"]!, id: "kill")
        result([("order", plan.chunks.map(\.locator).joined(separator: ","))])
        try await download(fx, plan: plan, name: "kill", hosts: [fx.base + "/slow"], opts: options(streams: 1, durableSeconds: 0))
        result([("completed", 1)])
    } catch { check(false, "slow: \(error)") }
}

func resume(_ fx: Fixture) async {
    do {
        let plan = try makePlan(fx.plans["main"]!, id: "kill")
        try await download(fx, plan: plan, name: "kill", hosts: [fx.base + "/good2"], opts: options())
        try commit(fx, plan: plan, name: "kill")
        check(true, "a killed download resumed to the end")
    } catch { check(false, "resume: \(error)") }
}

func pause(_ fx: Fixture) async {
    do {
        let plan = try makePlan(fx.plans["main"]!, id: "pause")
        let journal = folder(fx, "pause").appendingPathComponent("job/journal")
        let task = Task { try await download(fx, plan: plan, name: "pause", hosts: [fx.base + "/slow2"], opts: options(streams: 1, durableSeconds: 0)) }
        let deadline = Date().addingTimeInterval(20)
        while journalCount(journal) < 2 && Date() < deadline { try await Task.sleep(nanoseconds: 20_000_000) }
        task.cancel()
        var cancelled = false
        do { try await task.value } catch is CancellationError { cancelled = true } catch { check(false, "pause threw \(error)") }
        check(cancelled, "a paused download ends with a cancellation")
        let kept = BCDContentJournal.load(journal, identity: plan.identity, chunkCount: plan.chunks.count)
        check(kept.count >= 2 && kept.count < plan.chunks.count, "the journal keeps what finished before the pause (\(kept.count))")
        result([("paused", kept.map { plan.chunks[$0].locator }.sorted().joined(separator: ","))])
        try await download(fx, plan: plan, name: "pause", hosts: [fx.base + "/good3"], opts: options())
        try commit(fx, plan: plan, name: "pause")
        check(true, "a paused download resumes to the end")
    } catch { check(false, "pause: \(error)") }
}

func space(_ fx: Fixture) async {
    do {
        let plan = try makePlan(fx.plans["main"]!, id: "space")
        var opts = options()
        opts.freeSpace = { _ in 1000 }
        await expectError("not enough space is refused", {
            try await download(fx, plan: plan, name: "space", hosts: [fx.base + "/good4"], opts: opts)
        }) { error in
            if case BCDContentError.notEnoughSpace(let needed, let available) = error { return needed > plan.totalBytes && available == 1000 }
            return false
        }
    } catch { check(false, "space: \(error)") }
}

/// Log FCS8QA (2026-11-20, build 686): every Epic install on a tester's external drive was refused for space at once. The
/// drive's figure is now read as Steam reads it, through the link; on a drive without sparse files (exFAT) the room the files
/// already hold is not asked for again. The device's rule is as before.
func spaceDrive(_ fx: Fixture) async {
    do {
        let fm = FileManager.default
        let base = folder(fx, "space-volume")
        try fm.createDirectory(at: base, withIntermediateDirectories: true)
        check(BCDContentEngine.onDevice(base.appendingPathComponent("not/made/yet")), "a folder on the app's own volume is on the device")
        let important = (try? base.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?.volumeAvailableCapacityForImportantUsage
        let deviceFree = BCDContentEngine.freeSpace(base.appendingPathComponent("not/made/yet"))
        check((important == nil) == (deviceFree == nil) && abs((important ?? 0) - (deviceFree ?? 0)) < 1 << 30,
              "on the device the figure is the important-usage one, as before")
        // Another volume of the host stands in for the drive: drive_c/External/<name> is a link to it.
        let other = ["/System/Volumes/VM", "/System/Volumes/Preboot"].first { path in
            var a = stat(), b = stat()
            return stat(path, &a) == 0 && stat(base.path, &b) == 0 && a.st_dev != b.st_dev
        }
        if let other {
            let link = base.appendingPathComponent("drive")
            try? fm.removeItem(at: link)
            try fm.createSymbolicLink(atPath: link.path, withDestinationPath: other)
            let stage = link.appendingPathComponent("Epic Games/.bcd-stage/test.space")
            check(!BCDContentEngine.onDevice(stage), "a folder through a link to another volume is on a drive")
            var info = statfs()
            let real: Int64 = statfs(other, &info) == 0 ? Int64(info.f_bavail) * Int64(info.f_bsize) : -1
            let free = BCDContentEngine.freeSpace(stage) ?? 0
            check(free > 0 && abs(free - real) < 1 << 30, "a drive's figure is read through the link (\(free), statfs \(real))")
        } else {
            print("skip: no second volume on this host")
        }

        let plan = try makePlan(fx.plans["main"]!, id: "space-drive")
        func room(device: Bool) -> BCDContentOptions {
            var o = options()
            o.onDevice = { _ in device }
            o.spaceMargin = 100
            o.freeSpace = { _ in 1000 }
            return o
        }
        // Files that hold their whole room already, as an exFAT drive gives them when they are created at their final size.
        func held(_ name: String) throws {
            let stage = folder(fx, name).appendingPathComponent("stage")
            for d in plan.directories { try fm.createDirectory(at: stage.appendingPathComponent(d), withIntermediateDirectories: true) }
            for f in plan.files {
                let url = stage.appendingPathComponent(f.path)
                try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
                try Data(count: Int(f.size)).write(to: url)
            }
            check(BCDContentEngine.growthBytes(plan, stage: stage) == 0, "files written at their final size hold all their room")
        }
        check(BCDContentEngine.growthBytes(plan, stage: folder(fx, "space-none")) == plan.totalBytes,
              "files not made yet still need all their room")

        await expectError("a drive whose files hold no room yet still needs all of it", {
            try await download(fx, plan: plan, name: "space-sparse", hosts: [fx.base + "/good5"], opts: room(device: false))
        }) { error in
            if case BCDContentError.notEnoughSpace(let needed, let available) = error { return needed == plan.totalBytes + 100 && available == 1000 }
            return false
        }
        try held("space-full")
        try await download(fx, plan: plan, name: "space-full", hosts: [fx.base + "/good6"], opts: room(device: false))
        try commit(fx, plan: plan, name: "space-full")
        check(true, "on a drive whose files hold their room the download is not refused, and finishes")
        try held("space-device")
        await expectError("on the device the rule is as before: the bytes still to be written", {
            try await download(fx, plan: plan, name: "space-device", hosts: [fx.base + "/good7"], opts: room(device: true))
        }) { error in
            if case BCDContentError.notEnoughSpace(let needed, let available) = error { return needed == plan.totalBytes + 100 && available == 1000 }
            return false
        }
    } catch { check(false, "space-drive: \(error)") }
}

func range(_ fx: Fixture) async {
    do {
        let plan = try makePlan(fx.plans["range"]!, id: "range")
        try await download(fx, plan: plan, name: "range", hosts: [fx.base + "/drop"], opts: options())
        try commit(fx, plan: plan, name: "range")
        check(true, "a dropped connection continued to the end")
    } catch { check(false, "range: \(error)") }
}

func auth(_ fx: Fixture) async {
    do {
        let plan = try makePlan(fx.plans["main"]!, id: "auth")
        let source = TestSource([fx.base + "/auth"])
        try await download(fx, plan: plan, name: "auth", hosts: [], opts: options(streams: 1), source: source)
        try commit(fx, plan: plan, name: "auth")
        check(source.refreshes == 1, "refused addresses are renewed once (\(source.refreshes))")
    } catch { check(false, "auth: \(error)") }
}

func verify(_ fx: Fixture) async {
    do {
        let plan = try makePlan(fx.plans["bad"]!, id: "bad")
        var opts = options()
        opts.verifyFiles = true
        await expectError("a file that keeps failing its whole-file check fails the job", {
            try await download(fx, plan: plan, name: "bad", hosts: [fx.base + "/good5"], opts: opts)
        }) { ($0 as? BCDContentError) == .checkFailed }
        let good = try makePlan(fx.plans["main"]!, id: "checked")
        try await download(fx, plan: good, name: "checked", hosts: [fx.base + "/good6"], opts: opts)
        check(try BCDContentEngine.verify(good, folder: folder(fx, "checked").appendingPathComponent("stage")).isEmpty,
              "whole-file checks pass on a good install")
    } catch { check(false, "verify: \(error)") }
}

func memory(_ fx: Fixture) async {
    func peak() -> Int64 {
        var usage = rusage()
        getrusage(RUSAGE_SELF, &usage)
        return Int64(usage.ru_maxrss)   // bytes on Darwin
    }
    do {
        // The engine's own path: 32 MiB through the chunk writer in 64 KiB pieces, as the network
        // delivers them, read from disk so nothing but the writer can hold them.
        let plan = try makePlan(fx.plans["big"]!, id: "big")
        let stage = folder(fx, "big-writer")
        try FileManager.default.createDirectory(at: stage, withIntermediateDirectories: true)
        for file in plan.files {
            FileManager.default.createFile(atPath: stage.appendingPathComponent(file.path).path, contents: nil)
        }
        let writer = BCDChunkWriter(plan: plan, index: BCDContentPlan.Index(plan), stage: stage, meter: BCDContentMeter(done: 0, total: 0))
        let before = peak()
        for (c, chunk) in plan.chunks.enumerated() {
            let handle = try FileHandle(forReadingFrom: folder(fx, "wire").appendingPathComponent(chunk.locator))
            writer.start(c)
            try writer.begin(at: 0)
            // Each piece in its own pool: FileHandle's Data is autoreleased (the network's arrive in
            // URLSession's operations, which drain their own).
            while try autoreleasepool(invoking: { () throws -> Bool in
                guard let piece = try handle.read(upToCount: 64 << 10), !piece.isEmpty else { return false }
                try writer.receive(piece)
                return true
            }) {}
            try writer.finish()
            try handle.close()
        }
        result([("writer_before", before), ("writer_after", peak())])
        // The whole download, at a fast but real network's pace (4 streams of 40 MB/s): the network
        // stack's buffers come on top, so this bound is looser.
        try await download(fx, plan: makePlan(fx.plans["range"]!, id: "warm"), name: "warm", hosts: [fx.base + "/good7"], opts: options())
        let start = peak()
        try await download(fx, plan: plan, name: "big", hosts: [fx.base + "/pace"], opts: options())
        result([("rss_before", start), ("rss_after", peak())])
    } catch { check(false, "memory: \(error)") }
}

// MARK: the queue

@MainActor final class FakeDriver: BCDContentDriver {
    let store = "test"
    let storeTitle = "Test"
    var plans: [String: BCDContentPlan] = [:]
    var sources: [String: TestSource] = [:]
    let root: BCDInstallRoot
    var finished: [String] = []
    var cancelledKeys: [String] = []
    init(root: BCDInstallRoot) { self.root = root }
    func blocker(_ key: BCDContentKey) -> String? { nil }
    func plan(for key: BCDContentKey, stored: BCDContentPlan?) async throws -> BCDContentPlan {
        if let stored, stored.identity == plans[key.id]?.identity { return stored }
        return plans[key.id]!
    }
    func source(for plan: BCDContentPlan) async throws -> BCDChunkSource { sources[plan.key.id]! }
    func root(for key: BCDContentKey) -> BCDInstallRoot { root }
    func options(for plan: BCDContentPlan) -> BCDContentOptions {
        var o = BCDContentOptions(); o.backoff = 0; o.streams = 1; o.durableSeconds = 0; return o
    }
    func finish(_ plan: BCDContentPlan, stage: URL) async throws {
        try BCDContentEngine.commit(stage: stage, to: root.url!.appendingPathComponent(plan.folder))
        finished.append(plan.key.id)
    }
    func cancelled(_ key: BCDContentKey) { cancelledKeys.append(key.id) }
}

final class Flag: @unchecked Sendable { var on = false }

@MainActor func waitUntil(_ seconds: Double, _ cond: () -> Bool) async -> Bool {
    let deadline = Date().addingTimeInterval(seconds)
    while !cond() {
        if Date() > deadline { return false }
        try? await Task.sleep(nanoseconds: 20_000_000)
    }
    return true
}

@MainActor func queuePhase(_ fx: Fixture) async {
    let drive = folder(fx, "queue/drive_c")
    BCDInstallRoot.hostDrive = { $0 == "C" ? drive : nil }
    let state = folder(fx, "queue/state")
    let root = BCDInstallRoot(drive: "C", folder: "Test Games")
    let queue = BCDContentQueue(folder: state)
    let driver = FakeDriver(root: root)
    queue.register(driver)
    let other = Flag()
    queue.otherDownloadActive = { other.on }
    do {
        for (id, host) in [("one", "good8"), ("two", "slow3"), ("three", "good9"), ("four", "slow4"), ("five", "slow5")] {
            driver.plans[id] = try makePlan(fx.plans["main"]!, id: id, folder: id.capitalized)
            driver.sources[id] = TestSource([fx.base + "/" + host])
        }
    } catch { check(false, "queue plans: \(error)"); return }
    func key(_ id: String) -> BCDContentKey { BCDContentKey(store: "test", id: id) }
    func progressed(_ id: String) -> Bool {
        queue.job(key(id))?.state == .active && journalCount(queue.jobFolder(key(id)).appendingPathComponent("journal")) >= 1
    }

    queue.enqueue(key("one"), title: "One")
    check(await waitUntil(30) { driver.finished.contains("one") && queue.job(key("one")) == nil },
          "a queued job runs to the end and leaves the list")
    check(FileManager.default.fileExists(atPath: drive.appendingPathComponent("Test Games/One/bin/game.exe").path),
          "the driver's finish put the game in place")
    check(!FileManager.default.fileExists(atPath: queue.jobFolder(key("one")).path), "a finished job's journal and plan are gone")

    queue.enqueue(key("two"), title: "Two")
    check(await waitUntil(20) { progressed("two") }, "a second job starts")
    queue.sessionChanged(active: true)
    check(await waitUntil(10) { queue.job(key("two"))?.state == .paused }, "a game session pauses the running job")
    queue.enqueue(key("three"), title: "Three")
    check(queue.job(key("three"))?.state == .paused, "a job asked for during a session waits, paused")
    queue.sessionChanged(active: false)
    check(await waitUntil(30) { driver.finished.contains("two") && driver.finished.contains("three") },
          "after the session the paused jobs continue to the end")

    other.on = true
    driver.finished.removeAll()
    driver.plans["three"] = try? makePlan(fx.plans["main"]!, id: "three", folder: "Three again")
    queue.enqueue(key("three"), title: "Three")
    try? await Task.sleep(nanoseconds: 500_000_000)
    check(queue.job(key("three"))?.state == .queued && driver.finished.isEmpty, "while another downloader works, a job waits")
    other.on = false
    queue.otherDownloadChanged()
    check(await waitUntil(30) { driver.finished.contains("three") }, "...and runs once it is done")

    queue.enqueue(key("four"), title: "Four")
    check(await waitUntil(20) { progressed("four") }, "a job to cancel starts")
    let stageFour = queue.stage(key("four"), root: root)!
    check(FileManager.default.fileExists(atPath: stageFour.path), "it has staged files")
    queue.cancel(key("four"))
    check(await waitUntil(20) { driver.cancelledKeys.contains("four") }, "cancel tells the driver")
    check(queue.job(key("four")) == nil && !FileManager.default.fileExists(atPath: stageFour.path)
          && !FileManager.default.fileExists(atPath: queue.jobFolder(key("four")).path),
          "cancel removes the job, its staged files and its journal")

    queue.enqueue(key("five"), title: "Five")
    check(await waitUntil(20) { progressed("five") }, "a job to pause starts")
    queue.pause(key("five"))
    check(await waitUntil(10) { queue.job(key("five"))?.state == .paused }, "pause stops it")
    check(queue.hasPartial(key("five")), "a paused job keeps its plan and journal")
    let restored = BCDContentQueue(folder: state)
    check(restored.job(key("five"))?.state == .paused && restored.job(key("five"))?.title == "Five",
          "after a restart the paused job is listed as paused")
}

@main struct Harness {
    @MainActor static func main() async {
        let arguments = CommandLine.arguments
        let fx = try! JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: arguments[2])))
        BCDContentLog.sink = { print("LOG " + $0); fflush(stdout) }
        switch arguments[1] {
        case "units": await units(fx)
        case "install": await install(fx)
        case "slow": await slow(fx)
        case "resume": await resume(fx)
        case "pause": await pause(fx)
        case "space": await space(fx)
        case "space-drive": await spaceDrive(fx)
        case "range": await range(fx)
        case "auth": await auth(fx)
        case "verify": await verify(fx)
        case "memory": await memory(fx)
        case "queue": await queuePhase(fx)
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
    print(f"check-content-engine: {failures} FAILED" if failures else "check-content-engine: all passed")
    sys.exit(1 if failures else 0)

work = Path(tempfile.mkdtemp(prefix="bcd-content-engine-"))
server = None
try:
    harness = work / "main.swift"
    harness.write_text(HARNESS)
    exe = work / "harness"
    # Optimised, as the app is built: the memory phase measures what the decoder keeps up with.
    build = subprocess.run([SWIFTC, "-parse-as-library", "-swift-version", "5", "-O", "-o", str(exe), str(harness)]
                           + [str(CONTENT / f) for f in HOST_FILES], capture_output=True, text=True)
    check(build.returncode == 0, "the production engine files compile on the host", build.stderr[-4000:])
    if build.returncode:
        print(f"check-content-engine: {failures} FAILED")
        sys.exit(1)

    main, main_expect = main_plan()
    rng_plan, rng_expect = single_plan("big.bin", 2 << 20, compressible=False)
    bad, _ = single_plan("bad.bin", 30000, bad_digest=True)
    big, big_expect = big_plan()
    for plan in (main, rng_plan, bad, big):
        S.chunks.update(plan.wire)
    vector = blob(200000)

    server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server2 = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server2.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    base2 = f"http://127.0.0.1:{server2.server_address[1]}"
    data = work / "data"
    data.mkdir()
    fixture = dict(plans=dict(main=main.json(), range=rng_plan.json(), bad=bad.json(), big=big.json()),
                   base=base, base2=base2, work=str(data), zlibVector=base64.b64encode(zlib.compress(vector, 9)).decode(),
                   zlibPlainMd5=hashlib.md5(vector).hexdigest(), jsonZlib=base64.b64encode(zlib.compress(b'{"a":1}')).decode())
    fixture_path = work / "fixture.json"
    fixture_path.write_text(json.dumps(fixture))
    env = dict(os.environ, SWIFT_BACKTRACE="enable=no")

    def parse(stdout):
        lines = [l for l in stdout.splitlines() if not l.startswith("LOG ")]
        results = {}
        for line in lines:
            if line.startswith("RESULT "):
                for kv in line[7:].split():
                    k, _, v = kv.partition("=")
                    results[k] = v
        return lines, results

    def run(phase, timeout=240):
        r = subprocess.run([str(exe), phase, str(fixture_path)], capture_output=True, text=True, timeout=timeout, env=env)
        lines, results = parse(r.stdout)
        sys.stdout.write("".join("  " + l + "\n" for l in lines if l.startswith(("PASS", "FAIL"))))
        check(r.returncode == 0, f'Swift phase "{phase}" passed', ((r.stderr or "")[-2000:] + "\n".join(lines[-8:])).strip())
        return results, r.stdout

    def tree(folder):
        return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}

    def requests(label):
        with S.lock:
            return [entry for entry in S.log if entry[0] == label]

    all_logs = []

    # units
    _, out = run("units"); all_logs.append(out)

    # install through a dead and a corrupting host
    _, out = run("install"); all_logs.append(out)
    got = tree(data / "install/root/Game")
    check(got == main_expect, "every installed file is byte for byte what the plan describes",
          f"missing={sorted(set(main_expect) - set(got))} extra={sorted(set(got) - set(main_expect))} "
          f"different={sorted(k for k in got if k in main_expect and got[k] != main_expect[k])}")
    names = sorted(os.listdir(data / "install/root/Game"))
    check(names == ["Data", "README.TXT", "bin", "logs", "saves", "small3.txt"] and os.listdir(data / "install/root/Game/Data/Sub") == ["x.bin"],
          "listed folders exist, folded to their first spelling", names)
    good = requests("good")
    main_locs = set(main.wire)
    used = {c["locator"] for c in main.chunks} - {main.chunks[6]["locator"]}
    check(sorted(l for _, l, _ in good) == sorted(used), "each chunk was taken once from the healthy host (shared and sliced ones too)",
          f"{len(good)} requests for {len(used)} chunks")
    check(len(requests("corrupt")) >= 1, "the corrupting host was tried and its bad bytes refused")
    check(not (data / "install/stage").exists(), "the staging folder became the game's folder (one rename)")

    # a download killed with SIGKILL, then resumed
    proc = subprocess.Popen([str(exe), "slow", str(fixture_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    journal = data / "kill/job/journal"
    deadline = time.time() + 60
    while time.time() < deadline and proc.poll() is None:
        if journal.exists() and journal.stat().st_size >= 72 + 8 * 2:
            break
        time.sleep(0.02)
    killed = proc.poll() is None
    if killed:
        proc.send_signal(signal.SIGKILL)
    stdout, _ = proc.communicate(timeout=30)
    check(killed, "the slow download was killed halfway")
    _, results = parse(stdout)
    order = results.get("order", "").split(",")
    raw = journal.read_bytes() if journal.exists() else b""
    done = set()
    for i in range(72, len(raw) - 7, 8):
        index = int.from_bytes(raw[i:i + 4], "little")
        if int.from_bytes(raw[i + 4:i + 8], "little") == (~index & 0xFFFFFFFF) and index < len(order):
            done.add(order[index])
    check(len(done) >= 2, f"the journal survived the kill with {len(done)} chunks")
    _, out = run("resume"); all_logs.append(out)
    again = {l for _, l, _ in requests("good2")}
    check(not (again & done), "the resumed download fetched none of the journaled chunks again", f"{len(again & done)} again")
    check(tree(data / "kill/root/Game") == main_expect, "the resumed install is complete and correct")

    # pause and resume
    results, out = run("pause"); all_logs.append(out)
    paused = set(filter(None, results.get("paused", "").split(",")))
    check(paused and not (paused & {l for _, l, _ in requests("good3")}), "a resumed download does not fetch what finished before the pause")
    check(tree(data / "pause/root/Game") == main_expect, "the paused and resumed install is complete and correct")

    # not enough space
    _, out = run("space"); all_logs.append(out)
    check(not requests("good4"), "nothing was fetched when the space was missing")

    # an external drive's room (log FCS8QA)
    _, out = run("space-drive"); all_logs.append(out)
    check(not requests("good5") and not requests("good7"), "nothing was fetched when a drive or the device lacked the room")
    check(requests("good6") and tree(data / "space-full/root/Game") == main_expect,
          "the install on a drive whose files hold their room is complete and correct")
    check(re.search(r"\[content\] space needed-mb=\d+ free-mb=\d+ volume=drive", out)
          and re.search(r"\[content\] space needed-mb=\d+ free-mb=\d+ volume=device", out),
          "a refusal for space logs the figures and the volume", out[-1500:])

    # a dropped connection, continued with Range
    _, out = run("range"); all_logs.append(out)
    drops = requests("drop")
    half = len(next(iter(rng_plan.wire.values()))) // 2
    resumed_at = int(re.fullmatch(r"bytes=(\d+)-", drops[1][2] or "bytes=0-").group(1)) if len(drops) == 2 else 0
    check(len(drops) == 2 and drops[0][2] is None and 0 < resumed_at <= half,
          "the connection dropped halfway continued with a Range request from where it stopped", drops)
    check(tree(data / "range/root/Game") == rng_expect, "the continued file is correct")

    # refused addresses
    _, out = run("auth"); all_logs.append(out)
    check(tree(data / "auth/root/Game") == main_expect, "after renewing its addresses the install is correct")

    # whole-file checks
    _, out = run("verify"); all_logs.append(out)
    check(len(requests("good5")) == 2, "a file failing its whole-file check is fetched again once, then the job fails",
          len(requests("good5")))

    # the queue
    _, out = run("queue"); all_logs.append(out)

    # memory
    wire = data / "wire"
    wire.mkdir()
    for loc, body in big.wire.items():
        (wire / loc).write_bytes(body)
    results, out = run("memory"); all_logs.append(out)
    check(tree(data / "big-writer") == big_expect and tree(data / "big/stage") == big_expect, "the 32 MiB downloads are correct")
    writer = (int(results.get("writer_after", 0)) - int(results.get("writer_before", 0))) / (1 << 20)
    check("writer_after" in results and writer < 4,
          f"streaming: 32 MiB through the chunk writer raised peak memory by {writer:.1f} MiB (one 8 MiB chunk held would be 8+)")
    grown = (int(results.get("rss_after", 0)) - int(results.get("rss_before", 0))) / (1 << 20)
    check("rss_after" in results and grown < 40,
          f"the whole download raised peak memory by {grown:.1f} MiB (the network stack buffers at most about a chunk per stream "
          "when writing stalls; the chunks plus their unpacked copies would be 64+)")

    # logs
    joined = "\n".join(all_logs)
    log_lines = [l for l in joined.splitlines() if l.startswith("LOG ")]
    check(log_lines and not any(base in l or "http" in l or str(work) in l for l in log_lines),
          "log lines carry no address or path", [l for l in log_lines if "http" in l or str(work) in l][:3])
finally:
    if server:
        server.shutdown()
        server2.shutdown()
    shutil.rmtree(work, ignore_errors=True)

print(f"check-content-engine: {failures} FAILED" if failures else "check-content-engine: all passed")
sys.exit(1 if failures else 0)
