#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""GOG's generation 1 in BCD (build 663; app/Madeira/BCDGOG/BCDGOGContentV1.swift) and the content engine's ranged chunks.

A game whose builds list has no public generation-2 build (Return to Castle Wolfenstein, feedback 9XAP8E) is read from GOG's
older content system: the build's repository, its depot manifests, each file a slice of one main.bin per product fetched with
an HTTP Range. Generation 2 is not changed: this check pins the junctions and proves a generation-2 plan keeps its identity and
saved form. Never contacts GOG and holds no account data.

Fixtures (tests/host/gog-gen1/):
  - rtcw/builds.json, rtcw/repository.json and rtcw/<uuid>.json (4): GOG's public replies for Return to Castle Wolfenstein
    (product 1441704976), read without a sign-in on 2026-11-15 from
    https://content-system.gog.com/products/1441704976/os/windows/builds?generation=2 and
    https://cdn.gog.com/content-system/v1/manifests/1441704976/windows/50078739/<name> (gog-cdn.gcdn.co and
    gog-cdn-fastly.gog.com served the same bytes);
  - quake-offering/repository.json and quake-offering/999de75e-....json: Quake: The Offering (1435828198), the same way, from
    https://cdn.gog.com/content-system/v1/manifests/1435828198/windows/46992478/<name> (support app/ files: DOSBox and game configs);
  - gen2-plan-base-sorted.json: a generation-2-shaped plan saved (sorted keys) by the engine code of 95e0fc7e, before build 663;
  - the constructed game of the install phases is built here (its shapes follow the real files above; nothing in it is GOG's).

Source checks (every host): the new file's licence and credit header, the Xcode project, the fixtures' SHA-256, the junctions in
the generation-2 files exactly as written (newest, plan, available, BCDGOG.source and options, the engine's slice), no new key,
log lines without an address, path, token or name, the notices row, CI.
Behaviour (swiftc on macOS; --require-swift makes its absence a failure): tests/host/gog-gen1/main.swift on the production files:
  - units: the builds list, the cdn.gog.com move, the repository, languages, depots, slices, secure links, refusals;
  - engine: a generation-2 plan's identity and saved form as before; Range requests of a slice (resume, wrong start, 200);
  - rtcw: RTCW's real manifests planned in English and German, compared with this file's own reading of them; Quake's app/ files;
  - install: a constructed game (and DLC) from a local server with a corrupted slice, a corrupted piece of a split file, a dropped
    connection and a server that ignores the Range: every file byte for byte, each slice asked for as expected;
  - repair, dlc, resume: one changed and one missing file put back; the owned DLC found and installed from its own main.bin; a
    stopped download resumed without asking again for a journaled slice.

  check-gog-gen1.py [--require-swift]
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
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
GOG = APP / "BCDGOG"
CONTENT = APP / "BCDContent"
FIX = R / "tests/host/gog-gen1"
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
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


# ---- source checks ------------------------------------------------------------------------
v1 = (GOG / "BCDGOGContentV1.swift").read_text()
HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
          "// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n"
          "// Portions copyright 2025-2026 Paweł Lidwin (imLinguin): GOG's language names and codes (`v1Languages`), from heroic-gogdl's "
          "gogdl/languages.py (https://github.com/Heroic-Games-Launcher/heroic-gogdl, commit 6c6ef53f); GPL-3.0, modified for BCD in "
          "November 2026. See THIRD-PARTY-NOTICES.md.\n")
check(v1.startswith(HEADER), "BCDGOGContentV1.swift: BCD's header and the credit for gogdl's language table")
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("A1000C24 /* BCDGOG/BCDGOGContentV1.swift in Sources */ = {isa = PBXBuildFile; fileRef = A2000C24 /* BCDGOGContentV1.swift */; };" in pbx
      and 'A2000C24 /* BCDGOGContentV1.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "BCDGOG/BCDGOGContentV1.swift"; sourceTree = "<group>"; };' in pbx
      and pbx.count("A2000C24 /* BCDGOGContentV1.swift */,") == 1 and pbx.count("A1000C24 /* BCDGOG/BCDGOGContentV1.swift in Sources */,") == 1
      and pbx.count("A1000C24") == 2 and pbx.count("A2000C24") == 3,
      "the Xcode project builds it (IDs A1000C24/A2000C24, one group entry, one Sources entry)")
SUMS = {
    "rtcw/builds.json": "3b6b5b58ee0dca6dfe584aad2e3c0066eab7116710bfc6b7676191c11f20e519",
    "rtcw/repository.json": "46e5cb1d8d635ceee8ff5dc4d27a416e0623bfec10c57fc6d0ca787b8f68074d",
    "rtcw/6553fa0b-3e8e-4209-a888-d116f450423f.json": "7adc17beed2272e1babca047c057d22b7109bf50b3534f8bd270f1dd8024c85c",
    "rtcw/70877085-58b3-48b7-90bc-4191efe5bf9e.json": "127e5473431f9348c5cdfc01a9473cd9748e936996ebbb2f912c306f2846b988",
    "rtcw/a968af32-f5ae-4735-9c5f-de1511d931ab.json": "6b188f00373bf7711ee16a3ebc91f914c44331c7871669e0be1b5ece488d20a9",
    "rtcw/34e78725-aed4-40f9-9c78-5ffa67a5bb97.json": "452cffc8e652442115ed277cd5702df6c75bac5458ed8d4bf8ec1a28ff13dfef",
    "quake-offering/repository.json": "b6fde7ae0c6a062f28fb2200d0f0c71e7d6668dbfc491359b693f95a6b45fc61",
    "quake-offering/999de75e-fa2d-4e6c-93ad-bb547288ce46.json": "ed0bec8e4c8bf82f54ec208299810b5c5a61ce3a02673ff9cc1f1f42ef7b602e",
    "gen2-plan-base-sorted.json": "903c73364c3fe0af58dbced07b609a24f77df2db5cf90aabd3b3b9b8440193ec",
}
got = {str(p.relative_to(FIX)): hashlib.sha256(p.read_bytes()).hexdigest() for p in FIX.rglob("*.json")}
check(got == SUMS, "the fixtures are GOG's replies as read on 2026-11-15 and the plan the code before build 663 saved (SHA-256)",
      sorted(set(got.items()) ^ set(SUMS.items())))

content = (GOG / "BCDGOGContent.swift").read_text()
check("    var generation = 2\n}" in content
      and "        let object = try await fetch(buildsURL(id), true)\n        do {\n            return try newestBuild(object)\n"
          "        } catch BCDGOGError.olderBuildsOnly {\n" in content
      and "            return try newestBuildV1(object)\n" in content
      and content.count("if build.generation == 1 {") == 2
      and "            return try await availableDLCV1(product: product, build: build, depots: installed, language: language, owned: owned, fetch: fetch)\n" in content
      and "            return try await planV1(key, title: title, build: build, preferred: preferred, redistributables: redistributables,\n" in content
      and "newestBuildV1" not in content.split("static func newest(")[0] and "throw older ? BCDGOGError.olderBuildsOnly : BCDGOGError.noWindowsBuild" in content,
      "BCDGOGContent.swift hands a generation-1 build over in three places only (newest, available, plan); newestBuild itself is as it was")
plan_fn = content[content.index("    static func plan(_ key: BCDContentKey"):]
check(plan_fn.index("if build.generation == 1 {") < plan_fn.index("let manifest = try parseBuildManifest(await fetch(build.link, false))"),
      "plan(): the generation-1 hand-over comes before anything of generation 2")
facade = code((GOG / "BCDGOG.swift").read_text())
check("if plan.chunks.contains(where: { $0.rangeStart != nil }) { return BCDGOGContent.v1Source(api: api) }" in facade
      and "if plan.chunks.contains(where: { $0.rangeStart != nil && $0.digest == nil }) { options.verifyFiles = true }" in facade
      and "? try await api.json(BCDGOGContent.openLinkURL, signedIn: false, limit: 1 << 20)" in facade
      and ": try await api.json(BCDGOGContent.secureLinkURL(product), signedIn: true, limit: 1 << 20)" in facade,
      "BCDGOG: a plan with slices gets the generation-1 source and the whole-file check; any other plan the source and options it had")
fetch = (CONTENT / "BCDContentFetch.swift").read_text()
engine = (CONTENT / "BCDContentEngine.swift").read_text()
plan_src = (CONTENT / "BCDContentPlan.swift").read_text()
check('            } else if offset > 0 { attempt.setValue("bytes=\\(offset)-", forHTTPHeaderField: "Range") }' in fetch
      and 'attempt.setValue("bytes=\\(slice.lowerBound + offset)-\\(slice.upperBound)", forHTTPHeaderField: "Range")' in fetch
      and "guard request.base == 0 else { throw BCDContentError.http(200) }" in fetch
      and "guard let start, start == request.base + request.offset else { throw BCDContentError.http(206) }" in fetch
      and "try await one(attempt, offset: offset, base: slice?.lowerBound ?? 0, consumer: consumer)" in fetch
      and "func data(_ request: URLRequest, limit: Int) async throws -> Data {\n        let collector = BCDHTTPCollector(limit: limit)\n"
          "        try await fetch(request, into: collector, resumes: 1)" in fetch,
      "the HTTP stream: a slice asks for its own bytes; without one (every request before) the Range and checks are the old ones")
check("try await stream.fetch(request, into: writer, slice: chunk.rangeStart.map { $0...($0 + chunk.fetchSize - 1) })" in engine
      and "    var rangeStart: Int64? = nil\n}" in plan_src and 'let range = chunk.rangeStart.map { "\\tR\\($0)" } ?? ""' in plan_src
      and 'guard chunk.codec == .stored, start >= 0, start <= 1 << 50 else { throw BCDContentError.badPlan("chunk range") }' in plan_src,
      "the engine: a ranged chunk is fetched as its slice; the plan's identity line changes only for one")
check(not re.search(r"MadeiraConfig|getenv|setenv|UserDefaults", code(v1)), "generation 1 adds no key and no setting: it is the path that failed before")
for call in re.findall(r"BCDContentLog\.write\((.*)\)", code(v1)):
    bad = [e for e in re.findall(r"\\\(([^)]*)\)", call)
           if re.search(r"(?i)url|path|token|code\b|secret|folder|host|title|name|link|account|user", e)]
    check(not bad, "BCDGOGContentV1.swift: a log line carries no address, path, token or name", call)
check(re.search(r"\b(Shared|Darwin|UIKit|SwiftUI)\b", "\n".join(re.findall(r"^import .*", v1, re.M))) is None and "import Foundation" in v1,
      "Foundation only (the host checks compile it)")
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
check("heroic-gogdl" in notices and "app/Madeira/BCDGOG/BCDGOGContentV1.swift" in notices and "Paweł Lidwin" in notices,
      "THIRD-PARTY-NOTICES.md credits gogdl's language table in BCDGOGContentV1.swift")
ci = (R / ".github/workflows/build-ipa.yml").read_text()
check("python3 tests/host/check-gog-gen1.py --require-swift" in ci, "build-ipa.yml runs this check")

SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not SWIFTC or sys.platform != "darwin":
    if args.require_swift:
        check(False, "swiftc on macOS is required (--require-swift)")
    else:
        print("skip: the behaviour checks need swiftc on macOS")
    print(f"check-gog-gen1: {failures} FAILED" if failures else "check-gog-gen1: all passed")
    sys.exit(1 if failures else 0)

# ---- RTCW, read here on its own: what the plan must be ---------------------------------------
PIECE = 16 << 20
NAMES = {"English": "en-US", "German": "de-DE", "Neutral": "*"}


def expected_rtcw(language):
    repo = json.loads((FIX / "rtcw/repository.json").read_text())["product"]
    ts = str(repo["timestamp"])
    depots = [d for d in repo["depots"] if "manifest" in d and any(NAMES.get(l, l) in ("*", language) for l in d["languages"])]
    files, dirs, support = {}, set(), 0
    for d in depots:
        for rec in json.loads((FIX / "rtcw" / d["manifest"]).read_text())["depot"]["files"]:
            path = rec["path"].replace("\\", "/").lstrip("/")
            if "directory" in rec:
                parts = path.rstrip("/").lower().split("/")
                dirs.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
                continue
            if rec.get("support"):
                support += 1
                continue
            files[path.lower()] = (path, rec["size"], rec["hash"] if rec["size"] else "d41d8cd98f00b204e9800998ecf8427e", rec["offset"], rec["url"].split("/")[0])
    for path, *_ in files.values():
        parts = path.lower().split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    chunks = {}
    for path, size, md5, offset, product in files.values():
        whole = size <= PIECE
        at = 0
        while at < size:
            length = min(PIECE, size - at)
            chunks[(product, offset + at, length, md5 if whole else "")] = 1
            at += length
    return dict(language=language, depots=[d["manifest"] for d in depots], files=files, dirs=dirs, support=support, chunks=set(chunks),
                download=sum(k[2] for k in chunks), total=sum(f[1] for f in files.values()), timestamp=ts)


def compare_rtcw(name, summary, want):
    check(summary["language"] == want["language"] and summary["depots"] == want["depots"] and summary["support"] == want["support"]
          and summary["folder"] == "Return to Castle Wolfenstein" and summary["dependencies"] == ["DirectX"],
          f"RTCW ({name}): language, depots, the support helper left out, folder, DirectX", {k: summary[k] for k in ("language", "depots", "support")})
    got_files = {p.lower(): (p, s, h) for p, s, h in summary["files"]}
    check({k: (v[0].lower(), v[1], v[2]) for k, v in got_files.items()} == {k: (v[0].lower(), v[1], v[2]) for k, v in want["files"].items()},
          f"RTCW ({name}): every file of the chosen depots, with its size and MD5 ({len(want['files'])} files)")
    check({d.lower() for d in summary["directories"]} == want["dirs"], f"RTCW ({name}): every folder (the empty Docs/Help/_private included)")
    chunks = summary["chunks"]
    got_chunks = {(g.split(":")[1], start, size, md5) for g, start, size, md5 in chunks}
    check(got_chunks == want["chunks"] and len(chunks) == len(want["chunks"]) and all(g.split(":")[2] == want["timestamp"] for g, *_ in chunks),
          f"RTCW ({name}): one slice per file (16 MiB pieces for the big ones), identical files sharing theirs ({len(chunks)} slices)")
    check(summary["download"] == want["download"] and summary["total"] == want["total"],
          f"RTCW ({name}): {want['download']} bytes to download for {want['total']} on the disk (identical files once)")
    # Every byte of every file comes from its own place in main.bin.
    files = summary["files"]
    ok = True
    for c, coff, length, f, foff in summary["parts"]:
        start = chunks[c][1]
        path = files[f][0]
        if start + coff != want["files"][path.lower()][3] + foff:
            ok = False
    check(ok, f"RTCW ({name}): each part maps main.bin's bytes to the file's own offset")


# ---- the constructed game ----------------------------------------------------------------
rng = random.Random(663)
P, D, TS = "1441799991", "1441799992", "51234567"


def blob(n):
    return bytes(rng.getrandbits(8) for _ in range(n)) if n < 1 << 16 else rng.randbytes(n)


class Bin:
    def __init__(self):
        self.data = bytearray()

    def put(self, raw, gap=None):
        self.data += blob(rng.randint(1, 300) if gap is None else gap)   # never at byte 0: the empty file's offset
        at = len(self.data)
        self.data += raw
        return at


def build_game(work):
    mains = {P: Bin(), D: Bin()}
    expect = {}
    repo_depots = []
    info = dict(gameId=P, rootGameId=P, name="Test Game One", playTasks=[
        dict(type="FileTask", category="tool", path="bin\\tool.exe"),
        dict(type="FileTask", isPrimary=True, category="game", path="bin\\GAME.exe", arguments="-v1", workingDir="BIN")])
    slices = {}

    def rec(product, path, raw, final, support=False, at=None, hash_=None):
        offset = mains[product].put(raw) if at is None else at
        if final:
            expect[final] = raw
        r = dict(offset=offset, path=path, size=len(raw), hash=hash_ if hash_ is not None else hashlib.md5(raw).hexdigest(), url=f"{product}/main.bin")
        if support:
            r["support"] = True
        slices[final or path] = (product, offset, len(raw))
        return r

    game = blob(300_000)
    big = blob(34 * (1 << 20) + 123)
    copy = blob(4000)
    neutral = [
        rec(P, f"/goggame-{P}.info", json.dumps(info).encode(), f"goggame-{P}.info"),
        rec(P, "/Bin/Game.exe", game, "Bin/Game.exe"),
        rec(P, "/Data/big.pak", big, "Data/big.pak"),
    ]
    a = rec(P, "/Data/copy-a.cfg", copy, "Data/copy-a.cfg")
    neutral.append(a)
    neutral.append(rec(P, "/Data/copy-b.cfg", copy, "Data/copy-b.cfg", at=a["offset"]))
    neutral.append(rec(P, "/Data/empty.txt", b"", "Data/empty.txt", at=0, hash_="0" * 32))
    neutral.append(rec(P, f"{P}/app/dosbox_game.conf", blob(900), "dosbox_game.conf", support=True))
    neutral.append(rec(P, f"{P}/app/bin/game.exe", blob(50), None, support=True))
    neutral.append(rec(P, f"{P}/galaxy_helper.exe", blob(5000), None, support=True))
    neutral.append(dict(directory=True, path="/saves"))
    german = [rec(P, "/Bin/German.txt", blob(2000), "Bin/German.txt")]
    english = [rec(P, "/Bin/English.txt", blob(2000), None)]
    mac = [rec(P, "/Mac/Game.app", blob(3000), None)]
    dlc_raw = blob(70_000)
    dlc = [rec(D, "/DLC/extra.pak", dlc_raw, None)]

    v1 = work / "v1"
    for product in (P, D):
        (v1 / product).mkdir(parents=True)

    def depot(name, products, languages, files, systems=("Windows",)):
        (v1 / products[0] / name).write_text(json.dumps(dict(version=1, depot=dict(name="Test Game One", files=files))))
        repo_depots.append(dict(languages=languages, manifest=name, gameIDs=products, size=str(sum(f.get("size", 0) for f in files)), systems=list(systems)))

    depot("n-1.json", [P], ["Neutral"], neutral)
    depot("de-2.json", [P], ["German"], german)
    depot("mac-3.json", [P], ["Neutral"], mac, systems=("Mac",))
    depot("en-4.json", [P], ["English"], english)
    depot("dlc-5.json", [D], ["Neutral"], dlc)
    repo_depots.append(dict(redist="DirectX", executable="__redist/DirectX/DXSETUP.exe", argument="/silent"))
    repo = dict(version=1, product=dict(rootGameID=P, timestamp=int(TS), installDirectory="Test Game One", projectName="Test Game One",
                                        gameIDs=[dict(gameID=P, name=dict(en="Test Game One"), standalone=True, dependencies=[]),
                                                 dict(gameID=D, name=dict(en="Test DLC"), standalone=False, dependencies=[])],
                                        support_commands=[], depots=repo_depots))
    (v1 / P / "repository.json").write_text(json.dumps(repo))
    (work / "builds.json").write_text(json.dumps(dict(total_count=2, count=2, items=[
        dict(build_id="50002", product_id=P, os="windows", branch="beta", version_name="1.3b", generation=1,
             link=f"https://gog-cdn.gcdn.co/content-system/v1/manifests/{P}/windows/{TS}/repository.json"),
        dict(build_id="50001", product_id=P, os="windows", branch=None, version_name="1.2", generation=1,
             link=f"https://gog-cdn.gcdn.co/content-system/v1/manifests/{P}/windows/{TS}/repository.json", legacy_build_id=int(TS))])))
    return {k: bytes(v.data) for k, v in mains.items()}, expect, slices, dlc_raw


class S:
    lock = threading.Lock()
    mains = {}
    log = []          # (phase, product, range header, status)
    phase = ""
    faults = {}       # (product, start) -> kind, applied to the first request of that slice
    seen = set()
    stall_after = None
    served = 0


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, status, body, headers=()):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def stream(self, name, rng_header):
        body = bytes(i % 251 for i in range(100_000))
        with S.lock:
            first = name not in S.seen
            S.seen.add(name)
            S.log.append((S.phase, name, rng_header, 0))
        r = re.fullmatch(r"bytes=(\d+)-(\d*)", rng_header)
        a = int(r.group(1)) if r else 0
        b = int(r.group(2)) if r and r.group(2) else len(body) - 1

        def cut(status, part, headers, keep):
            self.send_response(status)
            self.send_header("Content-Length", str(len(part)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(part[:keep])
            self.wfile.flush()
            time.sleep(0.5)          # the client takes what came, then the connection drops
            self.close_connection = True
        if name == "whole":
            if first:
                return cut(200, body, [], 40_000)
            return self.reply(206, body[a:], [("Content-Range", f"bytes {a}-{len(body) - 1}/{len(body)}")])
        if name == "slice":
            headers = [("Content-Range", f"bytes {a}-{b}/{len(body)}")]
            return cut(206, body[a:b + 1], headers, 20_000) if first else self.reply(206, body[a:b + 1], headers)
        if name == "slice-wrong-start":
            return self.reply(206, body[0:50_000], [("Content-Range", f"bytes 0-49999/{len(body)}")])
        if name in ("slice-whole", "slice-zero-whole"):
            return self.reply(200, body)
        return self.reply(404, b"")

    def do_GET(self):
        if self.path.startswith("/stream/"):
            return self.stream(self.path[len("/stream/"):], self.headers.get("Range") or "-")
        m = re.fullmatch(r"/v1/(\d+)/main\.bin", self.path)
        rng_header = self.headers.get("Range") or ""
        r = re.fullmatch(r"bytes=(\d+)-(\d+)", rng_header)
        main = S.mains.get(m.group(1)) if m else None
        if main is None or r is None:
            with S.lock:
                S.log.append((S.phase, m.group(1) if m else self.path, rng_header, 404))
            return self.reply(404, b"")
        a, b = int(r.group(1)), int(r.group(2))
        product = m.group(1)
        with S.lock:
            key = (product, a)
            kind = S.faults.get(key) if key not in S.seen else None
            S.seen.add(key)
            S.served += 1
            stall = S.stall_after is not None and S.served > S.stall_after
            S.log.append((S.phase, product, rng_header, 200 if kind == "whole" else 206))
        if stall:
            time.sleep(15)
        body = main[a:b + 1]
        if kind == "whole":
            return self.reply(200, main)
        if kind == "corrupt":
            body = body[:len(body) // 2] + bytes([body[len(body) // 2] ^ 0xFF]) + body[len(body) // 2 + 1:]
        headers = [("Content-Range", f"bytes {a}-{b}/{len(main)}"), ("Accept-Ranges", "bytes")]
        if kind == "drop":
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body[:len(body) // 2])
            self.wfile.flush()
            time.sleep(0.5)
            self.close_connection = True
            return
        self.reply(206, body, headers)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass   # a client that hung up (a stopped download) is expected here


def requests(phase):
    with S.lock:
        return [(p, h, s) for ph, p, h, s in S.log if ph == phase]


def folder_files(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()} if folder.exists() else {}


work = Path(tempfile.mkdtemp(prefix="bcd-gog-gen1-"))
server = None
try:
    exe = work / "harness"
    sources = [str(FIX / "main.swift")] + [str(CONTENT / f) for f in ["BCDContentPlan.swift", "BCDContentCodec.swift", "BCDContentJournal.swift",
                                                                       "BCDContentFetch.swift", "BCDContentEngine.swift", "BCDContentQueue.swift",
                                                                       "BCDContentSecrets.swift"]] \
        + [str(GOG / f) for f in ["BCDGOGAuth.swift", "BCDGOGCatalog.swift", "BCDGOGContent.swift", "BCDGOGContentV1.swift", "BCDGOGInstall.swift",
                                  "GOGInstallScript.swift", "GOGSetup.swift", "BCDGOGUpdate.swift"]]
    build = subprocess.run([SWIFTC, "-parse-as-library", "-swift-version", "5", "-Onone", "-o", str(exe)] + sources, capture_output=True, text=True)
    check(build.returncode == 0, "the production GOG and engine files compile with the harness on the host", build.stderr[-4000:])
    if build.returncode:
        print(f"check-gog-gen1: {failures} FAILED")
        sys.exit(1)
    data = work / "data"
    data.mkdir()
    mains, expect, slices, dlc_raw = build_game(data)
    S.mains = mains
    server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fixture = dict(work=str(data), base=f"http://127.0.0.1:{server.server_address[1]}", fixtures=str(FIX), product=P, dlc=D, timestamp=TS)
    fixture_path = work / "fixture.json"
    fixture_path.write_text(json.dumps(fixture))
    env = dict(os.environ, SWIFT_BACKTRACE="enable=no")

    def run(phase):
        S.phase = phase
        r = subprocess.run([str(exe), phase, str(fixture_path)], capture_output=True, text=True, timeout=600, env=env)
        lines = [l for l in r.stdout.splitlines() if not l.startswith("LOG ")]
        sys.stdout.write("".join("  " + l + "\n" for l in lines if l.startswith(("PASS", "FAIL"))))
        check(r.returncode == 0, f'Swift phase "{phase}" passed', ((r.stderr or "")[-2000:] + "\n".join(lines[-8:])).strip())
        return r.stdout

    def tagged(out, tag):
        return [l.split(" ", 1)[1] for l in out.splitlines() if l.startswith(tag + " ")]

    outputs = [run("units"), run("engine")]
    seen = requests("engine")
    check([h for p, h, _ in seen if p == "whole"] == ["-", "bytes=40000-"],
          "no slice: no Range at first, a dropped connection continued with bytes=<received>- (the requests before build 663)", seen)
    check([h for p, h, _ in seen if p == "slice"] == ["bytes=10000-59999", "bytes=30000-59999"],
          "a slice: bytes=<first>-<last>, a dropped connection continued from what arrived to the slice's last byte", seen)
    check([h for p, h, _ in seen if p == "slice-zero-whole"] == ["bytes=0-99999"], "a slice from the first byte asks for it as a Range too", seen)
    rtcw = run("rtcw")
    outputs.append(rtcw)
    for name, language in (("en", "en-US"), ("de", "de-DE")):
        found = tagged(rtcw, "SUMMARY-" + name)
        check(len(found) == 1, f"RTCW ({name}): the harness printed its plan")
        if found:
            compare_rtcw(name, json.loads(base64.b64decode(found[0])), expected_rtcw(language))
    quake = tagged(rtcw, "SUMMARY-quake")
    if quake:
        q = json.loads(base64.b64decode(quake[0]))
        manifest = json.loads((FIX / "quake-offering/999de75e-fa2d-4e6c-93ad-bb547288ce46.json").read_text())["depot"]["files"]
        kept = {r["path"].split("/", 2)[2].lower() for r in manifest if r.get("support")}
        game_files = {r["path"].lstrip("/").lower() for r in manifest if "directory" not in r and not r.get("support")}
        check(kept <= {p.lower() for p, _ in q["files"]} and q["kept"] == 6 and q["support"] == 0 and len(q["files"]) == len(game_files) + 6,
              "Quake: The Offering: its six support app/ files (DOSBox and game configs) go into the game's folder below app/")
    else:
        check(False, "Quake: the harness printed its plan")

    # Install, with faults on the first answer of four slices.
    big_start = slices["Data/big.pak"][1]
    S.faults = {(P, slices["Bin/Game.exe"][1]): "corrupt", (P, big_start + PIECE): "corrupt", (P, big_start): "drop",
                (P, slices["Bin/German.txt"][1]): "whole"}
    out = run("install")
    outputs.append(out)
    folder = Path((tagged(out, "FOLDER") or [""])[0])
    check(folder_files(folder) == expect, "install: every installed file is what the manifests describe (German, no helper, the app/ file, the empty file)",
          f"missing={sorted(set(expect) - set(folder_files(folder)))} extra={sorted(set(folder_files(folder)) - set(expect))}")
    got = requests("install")

    def bounds(h):
        m = re.fullmatch(r"bytes=(\d+)-(\d+)", h)
        return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)

    def count(name, offset_in=0):
        product, start, size = slices[name]
        return [h for p, h, _ in got if p == product and bounds(h)[0] == start + offset_in]
    ends = {}
    for name, (product, start, size) in slices.items():
        at = 0
        while at < size:
            length = min(PIECE, size - at) if size > PIECE else size
            ends[(product, start + at + length - 1)] = (start + at, name)
            at += length
    shapes = all(p == P and (p, bounds(h)[1]) in ends and ends[(p, bounds(h)[1])][0] <= bounds(h)[0] for p, h, _ in got)
    check(shapes and got, "install: every request is a Range of one planned slice of the game's main.bin (or the rest of one)", got[:6])
    check(len(count("Bin/Game.exe")) == 2 and len(count("Bin/German.txt")) == 2 and len(count("Data/copy-a.cfg")) == 1
          and all(len(count(n)) == 1 for n in (f"goggame-{P}.info", "dosbox_game.conf")),
          "install: a corrupted slice and a whole-file answer asked for again once; files at the same bytes asked for once")
    drops = [h for p, h, _ in got if p == P and big_start < bounds(h)[0] < big_start + PIECE]
    check(len(drops) == 1 and drops[0] == f"bytes={big_start + PIECE // 2}-{big_start + PIECE - 1}",
          "install: the dropped connection went on with a Range from the byte it stopped at", drops)
    check(all(len(count("Data/big.pak", k * PIECE)) >= 2 for k in range(3)),
          "install: the split file's corrupted piece failed the whole-file check, and its slices were fetched again")
    left_out = [n for n in ("Data/empty.txt", "/Bin/English.txt", "/Mac/Game.app", f"{P}/galaxy_helper.exe", f"{P}/app/bin/game.exe") if count(n)]
    check(not left_out and not any(p == D for p, _, _ in got), "install: nothing asked for of the empty file, the left-out depots, the helper or the DLC", left_out)

    # Repair: one file changed, one removed.
    exe_path = folder / "Bin/Game.exe"
    raw = bytearray(exe_path.read_bytes())
    raw[1234] ^= 0x55
    exe_path.write_bytes(bytes(raw))
    (folder / "Data/copy-b.cfg").unlink()
    S.faults = {}
    outputs.append(run("repair"))
    check(folder_files(folder) == expect, "repair: the folder is the build again")
    asked = {h for _, h, _ in requests("repair")}
    product, start, size = slices["Bin/Game.exe"]
    _, cstart, csize = slices["Data/copy-b.cfg"]
    check(asked == {f"bytes={start}-{start + size - 1}", f"bytes={cstart}-{cstart + csize - 1}"}, "repair: only the two files' slices asked for", asked)

    # DLC.
    outputs.append(run("dlc"))
    expect_dlc = dict(expect, **{"DLC/extra.pak": dlc_raw})
    check(folder_files(folder) == expect_dlc, "dlc: the DLC's file added to the game's folder")
    check({p for p, _, _ in requests("dlc")} == {D}, "dlc: asked for from the DLC's own main.bin only")

    # Resume: stopped after four slices, then finished.
    S.seen = set()
    S.served = 0
    S.stall_after = 4
    out1 = run("resume1")
    journaled = set(tagged(out1, "JOURNALED"))
    S.stall_after = None
    out2 = run("resume2")
    outputs += [out1, out2]
    again = {h for _, h, _ in requests("resume2")} & journaled
    check(len(journaled) >= 3 and not again, "resume: no journaled slice is asked for again", sorted(again))
    folder2 = Path((tagged(out2, "FOLDER") or [""])[0])
    check(folder_files(folder2) == expect, "resume: every file of the resumed download is right")

    logged = "\n".join(l for o in outputs for l in o.splitlines() if l.startswith("LOG "))
    check("[gog-content] plan product=" in logged and "http" not in logged and str(work) not in logged and "Test Game" not in logged
          and "Wolfenstein" not in logged, "the log: product IDs and counts; no address, path or title")
finally:
    if server:
        server.shutdown()
    shutil.rmtree(work, ignore_errors=True)

print(f"check-gog-gen1: {failures} FAILED" if failures else "check-gog-gen1: all passed")
sys.exit(1 if failures else 0)
