#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Screenshots from the in-game menu, and their way to Send Log (round 603).

Source (every host):
  1. The picture is the game's own surface (MetalHostView) drawn without waiting for the game's next frame, so none of BCD's own
     windows (menu, HUD, touch controls) is in it; only when the app has memory to spare; at most 2000 px on the long side, a JPEG,
     encoded and written off the main thread; kept in Documents/Screenshots (visible in Files).
  2. Photos is add-only: the permission asked is `.addOnly`, nothing reads the library, Info.plist has the add description and not the
     read one.
  3. A picture goes with a log only when the player picks "Send with the screenshot" in the comment dialog; the dialog says it is sent as
     it is; the choice is kept for that log only and is spent (or dropped when the log did not go); it is the picture taken during that
     log's session; LogUpload.attach has one caller.
  4. The upload: its own route, a JPEG of 160 KB at most (the intake's limit), after the log, under the log's code; whatever happens to it is
     a note and never undoes the log's success; the answer says how it went.
  4b. What is uploaded is a small copy made apart from the kept picture (BCDScreenshotShrink: 1280 px at quality 0.75 down to 640 px at 0.4,
     stopping at the first copy of 100 KB or less), logged with its size and dimensions; the kept picture (Files, Photos) keeps its size
     (4 MB cap, as before). The words under the choice and the Settings footer say it is a small copy.
  5. The intake (bcd-logs/worker): POST /v1/shots, its admin routes, a budget of its own; its node tests pass.
  6. tools/pull-logs.py saves each picture beside its log, only from a whole JPEG, with a name made of validated codes, removes it from the
     bucket only when it is on the Mac, and copes with an intake that has no such route yet (run against a fake bucket).
  7. CI runs this check.

  check-bcd-screenshot.py [--require-swift]
"""
import argparse
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from contextlib import redirect_stdout
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
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
    out = []
    for line in text.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r'\s//[^"]*$', "", line))
    return "\n".join(out)


def block(text, header):
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
shot_text = (APP / "BCDScreenshotLive.swift").read_text()
shot = code(shot_text)
upload = code((APP / "LogUpload.swift").read_text())
comp = code((APP / "BCDComponents.swift").read_text())
worker = (R / "bcd-logs/worker/worker.js").read_text()
sessionlive = code((APP / "BCDSessionLive.swift").read_text())
plist = (APP / "Info.plist").read_text()
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()

# ---- 1. the picture -----------------------------------------------------------------------------------------------
check(shot_text.startswith(HEADER), "BCDScreenshotLive.swift: BCD's licence header")
take = block(shot, "static func take() {")
check("let host = MetalHostView.shared" in take and "host.drawHierarchy(in: host.bounds, afterScreenUpdates: false)" in take,
      "the picture is the game's own surface, drawn without waiting for its next frame")
check(not [w for w in ("snapshotView", "UIScreen.main.snapshot", "keyWindow", ".windows", "renderInContext", ".render(in:", "drawHierarchy(in: UIScreen")
           if w in take], "no window of BCD's (menu, HUD, touch controls) is in it: nothing snapshots a window or the screen")
check("os_proc_available_memory()" in take and take.index("os_proc_available_memory()") < take.index("UIGraphicsImageRenderer("),
      "the memory is looked at before anything is drawn, and a game close to iOS's limit gets no picture")
check("BCDScreenshotRules.scale(for: host.bounds.size" in take and "format.preferredRange = .standard" in take and "format.opaque = true" in take,
      "it is drawn at most 2000 px on the long side, 8 bit, opaque")
check("DispatchQueue.global(qos: .userInitiated).async {" in take and "image.jpegData(compressionQuality: BCDScreenshotRules.jpegQuality)" in take
      and "first.count > BCDScreenshotRules.keptMaxBytes" in take, "encoding and writing are off the main thread; one over 4 MB is encoded again smaller")
check("BCDScreenshotShrink" not in shot and "uploadTargetBytes" not in take and "maxSendBytes" not in take,
      "the kept picture is not the small one: taking a screenshot never touches the upload copy's rules")
check('MadeiraConfig.documents?.appendingPathComponent(BCDScreenshotRules.folder' in shot and 'static let folder = "Screenshots"' in code((APP / "BCDSessionCore.swift").read_text()),
      "kept in Documents/Screenshots (Files › BCD › Screenshots)")
check("[screenshot] saved" in take and "black=" in take, "it is logged: name, size, bytes, whether Photos was asked, whether it came out black")
check("isBlack(image)" in take and "came out black" in take, "a black picture says so (the player may take a system screenshot instead)")
check('case "shot":' in sessionlive and "DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { BCDScreenshotLive.take() }" in sessionlive
      and 'BCDMenuRow(id: "shot", title: "Take a screenshot"' in sessionlive, "the in-game menu's Tools tab has it: the menu closes first, then the picture")
check('case "shot.photos": BCDScreenshotLive.toPhotos = on' in sessionlive, "... and the Photos switch")

# ---- 2. Photos ----------------------------------------------------------------------------------------------------
check("PHPhotoLibrary.requestAuthorization(for: .addOnly)" in shot and ".readWrite" not in shot and "PHAsset.fetch" not in shot and "PHFetch" not in shot,
      "Photos is add-only: nothing reads the library")
check("<key>NSPhotoLibraryAddUsageDescription</key>" in plist and "<key>NSPhotoLibraryUsageDescription</key>" not in plist,
      "Info.plist has the add description and not the read one")
check(subprocess.run(["plutil", "-lint", str(APP / "Info.plist")], capture_output=True).returncode == 0 or shutil.which("plutil") is None, "Info.plist is a valid property list")
check("static var toPhotos: Bool" in shot and "UserDefaults.standard.object(forKey: photosKey) == nil || UserDefaults.standard.bool(forKey: photosKey)" in shot,
      "adding to Photos is on unless turned off")
sections = (APP / "BCDSettingsSections.swift").read_text()
check("screenshotsToPhotos" in sections and 'BCDSettingsID.control(.logs, "shotphotos")' in sections, "Settings › Logs has the Photos switch")

# ---- 3. Send Log --------------------------------------------------------------------------------------------------
ask_entry = block(comp, "static func ask(for log: URL? = nil, _ send: @escaping (String) -> Void, cancelled: (() -> Void)? = nil) {")
# Round 660 (M123): the box is built by the private ask that looks again while another controller is still presenting or dismissing
# (six times, half a second apart) and then ends as a cancel, so whoever waits always gets an answer.
ask = block(comp, "private static func ask(for log: URL?, starting: String, _ send: @escaping (String) -> Void, cancelled: (() -> Void)?, attempt: Int) {")
check("ask(for: log, starting: starting, send, cancelled: cancelled, attempt: 0)" in ask_entry
      and "if top.presentedViewController != nil || top.isBeingDismissed || top.isBeingPresented {" in ask
      and "if attempt < 6 { return ask(for: log, starting: starting, send, cancelled: cancelled, attempt: attempt + 1) }" in ask
      and "if let cancelled { cancelled() } else { send(\"\") }" in ask,
      "a box that cannot be presented yet is tried again six times, then ends as a cancel (no leaked wait)")
check("let shot = log.flatMap { screenshotFor?($0) }" in ask and "if shot != nil { message += " in ask and "BCDScreenshotRules.sentAsItIs" in ask,
      "the comment dialog offers a picture only when one belongs to the log, and says it is sent as it is")
check('title: "Send with the screenshot"' in ask and ask.count("screenshotChosen?(log, shot)") == 1 and "alert.preferredAction = sendAction" in ask,
      "'Send with the screenshot' is its own button; the default button sends the log alone; the choice is made once, in that button")
check(upload.count("static func attach(") == 1 and (shot.count("LogUpload.attach(shot, to: log)") == 1)
      and sum(len(re.findall(r"LogUpload\.attach\(", code(p.read_text()))) for p in APP.glob("*.swift")) == 1,
      "LogUpload.attach has one caller: the dialog's choice (nothing attaches a picture by itself)")
check("static func candidate(for log: URL) -> URL?" in shot and "BCDScreenshotRules.belongs(shot: date, logStart: start, logEnd: end)" in shot,
      "the picture offered is the newest one taken during that log's session")
send = block(upload, "static func send(_ log: URL, game: String, comment: String? = nil,")
check("takeShot(for: log)" in send and send.count("takeShot(for: log)") == 2 and "case .failure(let e):" in send,
      "the chosen picture is taken for that log only, and dropped when the log did not go")
check(send.index("remember(Sent(") < send.index("sendShot(shot, logCode: code, game: game)"), "the picture follows the log, under the log's code")
check("done?(r)" in send and "lastShotNote = note" in send, "whatever happens to the picture is a note: the log's success stands")
shot_up = block(upload, "private static func sendShot(")
check("BCDScreenshotShrink.smallCopy(of: shot)" in shot_up and "copy.data.count <= BCDScreenshotRules.maxSendBytes" in shot_up
      and 'req.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")' in shot_up
      and '"Log": logCode' in shot_up and "shotEndpoint" in shot_up and "uploadTask(with: req, from: copy.data)" in shot_up and "fromFile" not in shot_up,
      "the upload: its own route, the small copy (not the kept file) of at most the intake's limit, the log's code in a header")
check("queue.async {" in shot_up and shot_up.index("queue.async {") < shot_up.index("BCDScreenshotShrink.smallCopy"),
      "the copy is made on the utility queue, not on the URLSession callback's thread nor the main thread")
check("[bcd-logs] screenshot copy for upload: " in shot_up and "copy.width" in shot_up and "copy.height" in shot_up and "copy.data.count" in shot_up
      and "over the target" in shot_up, "its final size and dimensions are logged (and a floor picture over the target says so)")
check(sum(len(re.findall(r"BCDScreenshotShrink\.", code(p.read_text()))) for p in APP.glob("*.swift") if p.name != "BCDScreenshotShrink.swift") == 1,
      "the shrink has one user: the upload (nothing shrinks the kept picture)")
check('shotEndpoint = URL(string: "https://bcd.bcd-logs.workers.dev/v1/shots")!' in upload and "intake = \"madeira-bcd/2\"" in upload,
      "the same intake host and gate as the logs")
check("the server does not take pictures yet" in shot_up, "an intake without the route says so, in words")
core_text = (APP / "BCDSessionCore.swift").read_text()
check("sentAsItIs" in code(core_text) and "cannot take anything out of it" in core_text and "A small copy of the picture (about 100 KB) is sent as it is" in core_text,
      "the words under the choice: a small copy, and the picture cannot be cleaned like the log")
check("a small copy of it (about 100 KB) goes as it is" in sections and "The picture kept in Files stays full size" in sections,
      "Settings › Logs says the same, and that the kept picture stays full size")

# ---- 3b. the small copy -------------------------------------------------------------------------------------------------
shrink_text = (APP / "BCDScreenshotShrink.swift").read_text()
shrink = code(shrink_text)
check(shrink_text.startswith(HEADER), "BCDScreenshotShrink.swift: BCD's licence header")
check(re.search(r"^import (?!Foundation$|ImageIO$|UniformTypeIdentifiers$)", shrink, re.M) is None and "UIKit" not in shrink and "SwiftUI" not in shrink,
      "it needs Foundation and ImageIO only (the preview build and this check compile it alone)")
check("kCGImageSourceThumbnailMaxPixelSize" in shrink and "CGImageSourceCreateThumbnailAtIndex" in shrink and "kCGImageDestinationLossyCompressionQuality" in shrink
      and "kCGImageSourceCreateThumbnailWithTransform: true" in shrink, "the copy is a scaled decode and a JPEG at a given quality, turned upright")
check("kCGImageDestinationMergeMetadata" not in shrink and "kCGImageDestinationMetadata" not in shrink and "CGImageDestinationAddImageFromSource" not in shrink
      and "CGImageDestinationAddImage(" in shrink, "no metadata of the original is carried into the copy")
rules = code(core_text)
check("static let uploadTargetBytes = 100 * 1024" in rules and "static let uploadStartEdge = 1280" in rules and "static let uploadFloorEdge = 640" in rules
      and "static let uploadFloorQuality = 0.4" in rules and "static let maxSendBytes = 160 * 1024" in rules and "static let keptMaxBytes = 4 << 20" in rules,
      "the rules: 100 KB wanted, 1280 px down to 640 px, quality down to 0.4, 160 KB at most for the intake, 4 MB for the kept picture")
check("[0.75, 0.6, 0.5, BCDScreenshotRules.uploadFloorQuality]" in shrink and "[BCDScreenshotRules.uploadStartEdge, 1120, 960, 800, 720, BCDScreenshotRules.uploadFloorEdge]" in shrink,
      "the steps: 0.75, 0.6, 0.5, 0.4 at 1280, 1120, 960, 800, 720 and 640 px")
check(project.count("/* BCDScreenshotShrink.swift in Sources */") == 2 and project.count("path = BCDScreenshotShrink.swift;") == 1,
      "BCDScreenshotShrink.swift is built by the Xcode project")

swiftc = shutil.which("swiftc")
if not swiftc:
    check(not args.require_swift, "swiftc on the host (the small copy's behaviour)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-shrink-") as t:
        exe = Path(t) / "run"
        b = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(exe), str(APP / "BCDSessionCore.swift"),
                            str(APP / "BCDScreenshotShrink.swift"), str(R / "tests/host/bcd_shot_shrink_checks.swift")], capture_output=True, text=True)
        check(b.returncode == 0, "the small copy's code and its checks compile on the host (BCDScreenshotShrink.swift alone with the rules)", b.stderr[-1500:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
            oks = [l for l in r.stdout.splitlines() if l.startswith("ok")]
            bad = [l for l in r.stdout.splitlines() if l.startswith("FAIL") or l.startswith("     ")]
            check(r.returncode == 0 and len(oks) >= 20, f"the small copy: {len(oks)} checks (steps, the search, real pictures: size, shape, no metadata, the floor)", "\n      ".join(bad[:12]))

# ---- 4/5. the intake ----------------------------------------------------------------------------------------------
check("POST /v1/shots" in worker and 'url.pathname === "/v1/shots"' in worker and "export const MAX_SHOT_BYTES = 160 * 1024;" in worker,
      "the intake takes POST /v1/shots, 160 KB at most")
check('SHOT_META = ["build", "game", "device", "os", "kind", "log"]' in worker and "/v1/admin/shots/" in worker and 'trim(env, SHOT_BUDGET_BYTES, SHOT_TRIM_TO_BYTES, "shots/")' in worker,
      "known metadata only, its own admin routes and its own budget in the hourly trim")
check("BCDScreenshotRules.maxSendBytes" in upload and "static let maxSendBytes = 160 * 1024" in code((APP / "BCDSessionCore.swift").read_text()),
      "the app's limit and the intake's are the same 160 KB")
node = shutil.which("node") or str(Path.home() / "madeira-local/tools/node/bin/node")
if Path(node).exists():
    r = subprocess.run([node, "--test", str(R / "bcd-logs/worker/worker.test.mjs")], capture_output=True, text=True)
    check(r.returncode == 0 and "a screenshot is stored under shots/" in (R / "bcd-logs/worker/worker.test.mjs").read_text(),
          "the intake's node tests pass, with the screenshot's", (r.stdout + r.stderr)[-1500:])
else:
    print("SKIP the Worker's node tests: no node")

# ---- 6. pull-logs.py, against a fake bucket --------------------------------------------------------------------------
spec = importlib.util.spec_from_file_location("pull_logs", R / "tools/pull-logs.py")
pull = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pull)
JPEG = b"\xff\xd8\xff" + b"\x11" * 400 + b"\xff\xd9"


class Bucket:
    def __init__(self, objects):
        self.objects = objects      # key -> (bytes, meta)
        self.deleted = []
        self.gets = []

    def call(self, path, key=None, method="GET", data=None, headers=None):
        route, _, query = path.partition("?")
        params = urllib.parse.parse_qs(query)
        if route == "/v1/admin/shots/list":
            return 200, json.dumps({"objects": [{"key": k, "size": len(v[0]), "uploaded": "2026-10-25T20:00:00Z", "meta": v[1]}
                                                  for k, v in sorted(self.objects.items())], "cursor": None}).encode()
        if route == "/v1/admin/shots/get":
            self.gets.append(params["key"][0])
            return 200, self.objects[params["key"][0]][0]
        if route == "/v1/admin/shots/delete":
            self.deleted.append(params["key"][0])
            return 200, b"{}"
        return 404, b"{}"


def run(bucket, out, delete=True):
    pull.call = bucket.call
    buf = io.StringIO()
    with redirect_stdout(buf):
        new = pull.pull_shots("k" * 40, out, delete)
    return new, buf.getvalue()


with tempfile.TemporaryDirectory(prefix="bcd-pull-shots-") as t:
    out = Path(t)
    (out / "2026-10-24").mkdir()
    (out / "2026-10-24" / "K7Q2MX.txt").write_text("a log")
    b = Bucket({"shots/2026-10-25/AAAAAA.jpg": (JPEG, {"log": "K7Q2MX", "build": "603", "game": "x.exe", "kind": "screenshot"}),
                "shots/2026-10-25/BBBBBB.jpg": (JPEG, {"log": "ZZZZZZ"}),
                "shots/2026-10-25/CCCCCC.jpg": (b"not a picture at all" * 20, {"log": "K7Q2MX"}),
                "shots/2026-10-25/DDDDDD.jpg": (JPEG, {"log": "../../etc/x"}),
                "shots/2026-10-25/EEEEEE.jpg": (JPEG, {})})
    new, text = run(b, out)
    beside = out / "2026-10-24" / "K7Q2MX.shot-AAAAAA.jpg"
    check(beside.is_file() and beside.read_bytes() == JPEG, "a picture is saved beside its log (the day the log is filed under)")
    check(beside.with_suffix(".json").is_file() and json.loads(beside.with_suffix(".json").read_text())["key"] == "shots/2026-10-25/AAAAAA.jpg",
          "... with a record naming its bucket key")
    check((out / "2026-10-25" / "ZZZZZZ.shot-BBBBBB.jpg").is_file(), "a picture whose log is not on the Mac goes under its own day, named for the log's code")
    check(not (out / "2026-10-25" / "CCCCCC.jpg").exists() and not list(out.rglob("*CCCCCC*")) and "shots/2026-10-25/CCCCCC.jpg" not in b.deleted,
          "something that is not a whole JPEG is not saved and stays in the bucket", text)
    check((out / "2026-10-25" / "shot-DDDDDD.jpg").is_file() and not list(out.rglob("etc")) and (out / "2026-10-25" / "shot-EEEEEE.jpg").is_file(),
          "a log code that is not six letters or digits never reaches a path: the picture is filed by its own code")
    check(sorted(b.deleted) == ["shots/2026-10-25/AAAAAA.jpg", "shots/2026-10-25/BBBBBB.jpg", "shots/2026-10-25/DDDDDD.jpg", "shots/2026-10-25/EEEEEE.jpg"],
          "each picture that is on the Mac is removed from the bucket, and only those", b.deleted)
    check(len(new) == 4, "four new pictures reported", text)
    again = Bucket(dict(b.objects))
    new2, _ = run(again, out)
    check(again.gets == ["shots/2026-10-25/CCCCCC.jpg"] and not new2 and sorted(again.deleted) == sorted(b.deleted),
          "a second run downloads nothing it already has (only the one that was not a JPEG is tried again) and removes what a failed delete left", (again.gets, again.deleted))
    kept = Bucket({"shots/2026-10-26/FFFFFF.jpg": (JPEG, {"log": "K7Q2MX"})})
    run(kept, out, delete=False)
    check(not kept.deleted and (out / "2026-10-24" / "K7Q2MX.shot-FFFFFF.jpg").is_file(), "--keep leaves the bucket as it is")
    old = Bucket({})
    old.call = lambda path, key=None, method="GET", data=None, headers=None: (404, b'{"error":"not found"}')
    new3, text3 = run(old, out)
    check(new3 == [] and "no screenshot route yet" in text3, "an intake without the route is no failure: logs were pulled already, the note says to deploy the Worker", text3)
    check(pull.shot_dest({"key": "logs/2026-10-25/AAAAAA.gz"}, out) is None, "a key that is not a screenshot's is not taken for one")
    check(re.fullmatch(r"[0-9A-Z]{6}", "K7Q2MX") is not None and pull.CODE.match("k7q2mx") is None, "codes are upper case letters and digits")

check("pull_shots(key, a.out, delete)" in (R / "tools/pull-logs.py").read_text() and "[screenshot]" in (R / "tools/pull-logs.py").read_text(),
      "the normal run of pull-logs.py pulls pictures too and marks a log that has one")
check("python3 tests/host/check-bcd-screenshot.py" in workflow, "CI runs this check")

print("check-bcd-screenshot: " + ("PASS" if failures == 0 else f"{failures} FAILED"))
sys.exit(0 if failures == 0 else 1)
