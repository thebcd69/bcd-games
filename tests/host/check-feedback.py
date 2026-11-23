#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Settings › Feedback (madeira-bcd FORK-ONLY: never in a pull request to upstream).

A message written in BCD reaches the owner through the log intake's Cloudflare Worker, the one
Send Log uses.

Static contracts:
  - the Worker: POST /v1/feedback behind the same X-BCD-Intake gate and the same RL rate limit as
    logs, checked before the body is read; the key feedback/<day>/<code>.json; admin routes under
    /v1/admin/feedback/ behind the pull key; no new binding in wrangler.toml;
  - the app: FeedbackUpload.swift sends to the same host and header as LogUpload, path
    /v1/feedback; the body's fields are exactly the Worker's (kind, text, build, ios, model, log)
    and no source of a name, an account, a device name or an address is read; the model is the
    identifier from uname, not UIDevice; the limits agree in the Worker, the client and the view
    (4000 characters, 20 KB); the last 20 sent are kept as code, kind, date and first line;
  - Settings: the Feedback section sits right after Logs, with a symbol; its page has the three
    kind chips, a message field with a counter, "Attach my last game's log", Send, the sent list and
    the privacy line; every control is reachable by a controller and A on the message opens the
    keyboard; the live backend sends the log first and puts its code in the message; the view and
    the preview's sample name no engine type; no "Madeira" in what a person reads;
  - the Xcode project builds the new files; CI runs this check.
Behaviour:
  - the Worker's tests pass under node (bcd-logs/worker/worker.test.mjs), when node is found;
  - FeedbackUploadCore.swift, compiled alone on the host: the person's words keep every character
    but control characters (an e-mail, an address, a path, accented letters, emoji and a line break
    stay), the body has no other field than the six, a device name never travels as the model, the
    limits hold; and every body it builds is accepted by the real Worker (run under node) and
    stored with the same text;
  - tools/pull-logs.py --feedback, run against a local server: it downloads and lists new
    feedback (date, code, kind, the log's code, first line cleaned of control characters), and
    never prints the key, even when the server echoes it back in an error.
The Swift part is skipped when swiftc is missing (--require-swift makes that a failure); the node
parts when node is missing (--require-node makes that a failure).
"""
import argparse
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote

R = Path(__file__).resolve().parents[2]
APP = R / "app" / "Madeira"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
ap.add_argument("--require-node", action="store_true")
args = ap.parse_args()
ok = True


def check(a, b, detail=""):
    """check(what, cond) or check(cond, what): whichever is the sentence."""
    global ok
    what, cond = (a, b) if isinstance(a, str) else (b, a)
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


def code(text):
    """The text without // comments."""
    return "\n".join(l if l.find("//") < 0 else l[:l.find("//")] for l in text.split("\n"))


def bare(text):
    """Code without comments or string contents: what names a type."""
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', code(text))


def block(text, header):
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


worker = (R / "bcd-logs/worker/worker.js").read_text()
toml = (R / "bcd-logs/worker/wrangler.toml").read_text()
upload = (APP / "FeedbackUpload.swift").read_text()
core = (APP / "FeedbackUploadCore.swift").read_text()
logup = (APP / "LogUpload.swift").read_text()
view = (APP / "BCDSettingsFeedback.swift").read_text()
settings_core = (APP / "BCDSettingsCore.swift").read_text()
settings = (APP / "BCDSettings.swift").read_text()
live = (APP / "BCDSettingsLive.swift").read_text()
sample = (R / "app/BCDPreview/BCDSettingsPreviewData.swift").read_text()
pull = (R / "tools/pull-logs.py").read_text()
preview_tool = (R / "tools/ui-preview.py").read_text()
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")

# ---- headers --------------------------------------------------------------------------------
for name in ("FeedbackUpload.swift", "FeedbackUploadCore.swift", "BCDSettingsFeedback.swift"):
    check(f"{name}: BCD's licence header", (APP / name).read_text().startswith(HEADER))
for f in ("app/Madeira/FeedbackUpload.swift", "app/Madeira/FeedbackUploadCore.swift", "tests/host/check-feedback.py"):
    check(f"{f} is marked FORK-ONLY or carries BCD's header", "FORK-ONLY" in (R / f).read_text()[:800] or f.endswith(".py"))

# ---- the Worker -----------------------------------------------------------------------------
fb = block(worker, "async function feedback(request, env, now)")
lg = block(worker, "async function intake(request, env, now)")
check('request.method === "POST" && url.pathname === "/v1/feedback"' in worker and "return feedback(request, env, now)" in worker,
      "Worker: POST /v1/feedback")
gate = 'if (request.headers.get("x-bcd-intake") !== INTAKE) return json({ error: "not the app" }, 403);'
limit = "const { success } = await env.RL.limit({ key: ip });"
check(gate in fb and gate in lg and limit in fb and limit in lg,
      "Worker: feedback has the same X-BCD-Intake gate (403) and the same RL rate limit as logs")
check(fb.index("x-bcd-intake") < fb.index("env.RL.limit") < fb.index("readLimited(request"),
      "Worker: the gate and the rate limit come before the body is read")
check("const key = `feedback/${day(now)}/${c}.json`;" in fb and "received: new Date(now).toISOString()" in fb
      and "return json({ code: c });" in fb and "await env.LOGS.head(key)" in fb,
      "Worker: stored at feedback/<day>/<code>.json with the received time, the code returned, an existing key skipped")
admin = block(worker, "async function adminFeedback(request, env, url)")
check("if (url.pathname.startsWith(\"/v1/admin/feedback/\")) return adminFeedback(request, env, url);" in worker
      and worker.index("if (!authorized(request, env))") < worker.index("return adminFeedback(request, env, url)")
      and all(p in admin for p in ('"/v1/admin/feedback/list"', '"/v1/admin/feedback/get"', '"/v1/admin/feedback/delete"'))
      and 'prefix = "logs/"' in worker and '"feedback/")' in admin,
      "Worker: feedback's admin routes (list, get, delete) behind the pull key, apart from the logs' routes")
check(toml.count("[[r2_buckets]]") == 1 and toml.count("[[ratelimits]]") == 1 and 'binding = "LOGS"' in toml and 'name = "RL"' in toml
      and "secret" not in code(toml.replace("#", "//")).lower(),
      "Worker: no new binding or secret (the LOGS bucket, the RL limit, the PULL_KEY it has)")
w_fields = re.search(r"const FEEDBACK_FIELDS = \[(.*?)\];", worker).group(1)
w_fields = re.findall(r'"(\w+)"', w_fields)
w_text = int(re.search(r"export const FEEDBACK_TEXT_MAX = (\d+);", worker).group(1))
w_body = eval(re.search(r"export const MAX_FEEDBACK_BYTES = ([0-9 *]+);", worker).group(1))
w_kinds = re.findall(r'"(\w+)"', re.search(r"export const FEEDBACK_KINDS = \[(.*?)\];", worker).group(1))

# ---- the app: the client ---------------------------------------------------------------------
host = lambda text: re.search(r'URL\(string: "https://([^/"]+)(/[^"]*)"\)!', text).groups()
check(host(upload)[0] == host(logup)[0] and host(upload)[1] == "/v1/feedback" and host(logup)[1] == "/v1/logs"
      and host(upload)[0] == "bcd.bcd-logs.workers.dev",
      "FeedbackUpload: the same host as Send Log, path /v1/feedback")
check('static let intake = "madeira-bcd/2"' in upload and 'static let intake = "madeira-bcd/2"' in logup
      and 'export const INTAKE = "madeira-bcd/2";' in worker
      and 'req.setValue(intake, forHTTPHeaderField: "X-BCD-Intake")' in upload,
      "FeedbackUpload: the same X-BCD-Intake header as Send Log")
src = code(upload) + code(core)
forbidden = ["UIDevice", "identifierForVendor", "NSUserName", "NSFullUserName", "hostName", "gethostname", "getifaddrs",
             "ASIdentifierManager", "advertisingIdentifier", "Keychain", "SecItem", "NSHomeDirectory", "UIPasteboard",
             "Locale.", "TimeZone", "CNContact", "SteamAccount", "SteamID", "Authorization"]
check(not [t for t in forbidden if t in src], "FeedbackUpload: reads no name, account, device name, address or key",
      str([t for t in forbidden if t in src]))
check("uname(&u)" in upload and "u.machine" in upload and "model: deviceModel" in upload
      and "static func model(_ machine: String)" in core,
      "FeedbackUpload: the model is the identifier from uname, never the device's name")
c_fields = set(re.findall(r'"(\w+)": ', re.search(r"var fields = \[(.*?)\]\n", core).group(1))) | set(re.findall(r'fields\["(\w+)"\] =', core))
check(c_fields == set(w_fields) == {"kind", "text", "build", "ios", "model", "log"},
      "the body's fields are exactly the Worker's: kind, text, build, ios, model, log", f"app {c_fields} worker {w_fields}")
c_text = int(re.search(r"static let maxCharacters = (\d+)", core).group(1))
c_body = eval(re.search(r"static let maxBody = ([0-9 *]+)", core).group(1))
v_text = int(re.search(r"static let maxCharacters = (\d+)", view).group(1))
check(c_text == w_text == v_text == 4000, "the 4000-character limit is the same in the Worker, the client and the view",
      f"{w_text} {c_text} {v_text}")
check(c_body == w_body == 20480, "the 20 KB body limit is the same in the Worker and the client", f"{w_body} {c_body}")
c_kinds = re.search(r"static let kinds = \[(.*?)\]", core).group(1)
v_kinds = re.search(r"enum BCDFeedbackKind: String, CaseIterable, Identifiable \{\s*case (.*?)\n", view).group(1)
check(re.findall(r'"(\w+)"', c_kinds) == w_kinds == [k.strip() for k in v_kinds.split(",")] == ["bug", "idea", "other"],
      "the kinds are bug, idea, other in the Worker, the client and the view")
check("private static let kept = 20" in upload and "prefix(kept)" in upload
      and re.search(r"struct Sent: Codable, Identifiable, Equatable \{\s*var code: String\s*var kind: String\s*var date: Date\s*var line: String", upload),
      "the last 20 sent are kept: code, kind, date, first line")
check(not [l for l in code(upload).split("\n") if "LogStore.shared.log(" in l and re.search(r"\\\((text|line|body|words)", l)],
      "the app's log never holds the message")
check("done: @escaping (Result<String, Error>) -> Void" in upload
      and "DispatchQueue.main.async { done(r) }" in upload,
      "FeedbackUpload: the code or the error comes back on the main queue")
person = [s for s in re.findall(r'"((?:[^"\\]|\\.)*)"', code(upload) + code(view) + code(core)) if not s.startswith("https://")]
check(not [s for s in person if "madeira" in s.lower()], "what a person reads never says the old name",
      str([s for s in person if "madeira" in s.lower()]))

# ---- Settings -------------------------------------------------------------------------------
cases = re.search(r"enum BCDSettingsSection: String, CaseIterable, Identifiable \{\s*case (.*?)\n", settings_core).group(1).split(", ")
check(cases.index("feedback") == cases.index("logs") + 1, "Settings: the Feedback section is right after Logs", str(cases))
check('case .feedback: return "Feedback"' in settings_core and 'case .feedback: return "text.bubble.fill"' in settings_core
      and 'case .feedback: return "' in settings_core.split("var summary")[1].split("var symbol")[0],
      "Settings: Feedback has a title, a summary and a symbol")
check("var feedback: any BCDFeedbackBackend" in settings and "case .feedback: Self.feedback(model.backends.feedback)" in settings
      and "feedback: BCDFeedbackLive()" in live and "feedback: BCDPreviewFeedback()" in sample,
      "Settings: the backend protocol, the live backend and the preview's sample are wired in")
check("let privacy = \"BCD sends what you write, the BCD build, the iOS version and the device model. No name or account.\"" in view
      and "Text(BCDFeedbackRules.privacy)" in view,
      "Feedback: the privacy line, word for word")
check(all(w in view for w in ('title: BCDFeedbackAttach.title', 'Label(sending ? "Sending…" : "Send"', "TextEditor(text: $draft.text)",
                              'case .bug: return "Bug"', 'case .idea: return "Idea"', 'case .other: return "Other"',
                              "\\(used) / \\(BCDFeedbackRules.maxCharacters)", "Sent. Your code: ", 'title: "Sent"'))
      and "ForEach(BCDFeedbackKind.allCases)" in view and "backend.sent.prefix(5)" in view,
      "Feedback: three kind chips, the message with a counter, Attach my last game's log (BCDFeedbackAttach.title), Send and its status, the sent list")
check('static let title = "Attach my last game\'s log"' in (APP / "BCDFeedbackAttach.swift").read_text(), "Feedback: the attach row's title (tests/host/check-feedback-attach.py holds the rest)")
check("if BCDFeedbackRules.count(new) > BCDFeedbackRules.maxCharacters { draft.text = BCDFeedbackRules.limited(new) }" in view,
      "Feedback: the field stops at 4000 characters")
ids = re.findall(r'BCDSettingsID\.control\(\.feedback, "([^"]+)"\)', view)
check({"kind", "message", "log", "send", "done"} <= set(ids) and any(i.startswith("sent.") for i in ids),
      "Feedback: every control has a focus id (kind, message, log, send, done, the sent codes)", str(ids))
kind_row = block(view, "private var kindRow: some View")
message_row = block(view, "private var messageRow: some View")
check(".bcdSettingsControl(BCDSettingsID.control(.feedback, \"kind\")" in kind_row and "step: { stepKind($0) }" in kind_row
      and "stepKind(1, wrap: true)" in kind_row,
      "controller: left and right change the kind, A moves to the next")
check(".bcdSettingsControl(BCDSettingsID.control(.feedback, \"message\"), cornerRadius: 12) { typing = true }" in message_row
      and ".focused($typing)" in message_row,
      "controller: A on the message field opens the keyboard")
check("BCDSettingsToggleRow(id: BCDSettingsID.control(.feedback, \"log\")" in view
      and ".bcdSettingsControl(BCDSettingsID.control(.feedback, \"send\")" in view,
      "controller: the log switch and Send are focusable")
lf = block(live, "final class BCDFeedbackLive: BCDFeedbackBackend")
check(lf.index("LogUpload.send(url") < lf.index("post(kind, text, log: code)") < lf.index("FeedbackUpload.send(kind: kind, text: text, log: log)")
      and "guard draft.attachLog, let url = newest else { return post(kind, text, log: nil) }" in lf
      and "LogUpload.recentLogs()" in lf,
      "live: with Attach my last game's log on, the newest log goes first through LogUpload.send and its code goes into the message")
check("draft.text = \"\"" in lf and "Your message is still here" in lf,
      "live: a sent message clears the field; a failure keeps the words")
engine = ["LibraryModel", "LogUpload", "LogStore", "MadeiraConfig", "FeedbackUpload", "FeedbackCore", "UIDevice"]
mine = bare(view) + bare(block(sample, "final class BCDPreviewFeedback"))
check(not [t for t in engine if re.search(r"\b" + t + r"\b", mine)], "the view and the preview's sample name no engine type")
bound = set(re.findall(r'"([^"]+)"', re.search(r"ENGINE_BOUND = \{([^}]*)\}", preview_tool).group(1)))
check("BCDSettingsLive.swift" in bound and "BCDSettingsFeedback.swift" not in bound,
      "ui-preview.py: the live backend stays out of the preview build, the view is in it")
for name, a, b in (("FeedbackUpload.swift", "A1000FE0", "A2000FE0"), ("FeedbackUploadCore.swift", "A1000FE1", "A2000FE1"),
                   ("BCDSettingsFeedback.swift", "A1000B87", "A2000B87")):
    check(f"the Xcode project builds {name}",
          pbx.count(f"{a} /* {name} in Sources */") == 2 and pbx.count(f"{b} /* {name} */") == 3 and pbx.count(f"path = {name};") == 1)
CI = R / ".github/workflows/build-ipa.yml"
if CI.exists():
    check("CI runs this check", "python3 tests/host/check-feedback.py --require-swift" in CI.read_text())

# ---- the Worker under node -----------------------------------------------------------------
node = shutil.which("node") or str(Path.home() / "madeira-local/tools/node/bin/node")
have_node = Path(node).exists()
if have_node:
    r = subprocess.run([node, "--test", str(R / "bcd-logs/worker/worker.test.mjs")], capture_output=True, text=True)
    check("the Worker's tests pass under node", r.returncode == 0, (r.stdout + r.stderr)[-2000:])
else:
    check("node found for the Worker's tests (--require-node)", not args.require_node)
    print("SKIP the Worker's node tests: no node")

# ---- FeedbackUploadCore, compiled alone ---------------------------------------------------
DRIVER = r'''
import Foundation
var failed = false
func check(_ cond: Bool, _ what: String) {
    print((cond ? "ok   " : "FAIL ") + what)
    if !cond { failed = true }
}
let out = URL(fileURLWithPath: CommandLine.arguments[1])
typealias F = FeedbackCore
func fields(_ d: Data) -> [String: String] { (try! JSONSerialization.jsonObject(with: d)) as! [String: String] }
func fails(_ p: F.Problem, _ make: () throws -> Data) -> Bool {
    do { _ = try make(); return false } catch let e as F.Problem { return e == p } catch { return false }
}

// The person's own words: only control characters go.
check(F.clean("  hello\r\nworld\rtab\there\u{7}\u{1b}[0m\u{7f}\u{85}end \n") == "hello\nworld\ntab here[0mend",
      "clean: CR LF and CR are one line break, a tab a space, other control characters go, the ends are trimmed")
let own = ["Ali's iPhone crashes: write to ali@example.com, from 192.168.1.5, in /Users/ali/Desktop/BCD",
           "Très bien: äöüßéèç ÄÖÜÉÈÇ, ça gèle au démarrage", "emoji \u{1F600} and a family \u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467} and \u{FB01}",
           "two  spaces\tinside", "line one\n\nline three", "C:\\Users\\steamuser\\x and 76561198012345678", "東京 and العربية and ‮bidi‬"]
for text in own {
    check(F.clean(text) == text.replacingOccurrences(of: "\t", with: " "), "clean: keeps every character of \"\(text.prefix(24))…\" (an e-mail, an address, a path, a name stay)")
}
check(F.clean("\u{7}\u{1b}  \n\t") == "", "clean: control characters alone are nothing")
check(F.count("a\u{1F600}b") == 3 && F.count(String(repeating: "\u{1F600}", count: 4000)) == F.maxCharacters, "count: characters are Unicode scalars, as the intake counts them")
check(F.count(F.limited(String(repeating: "x", count: 5000))) == F.maxCharacters && F.limited("short") == "short", "limited: cut at 4000, short text untouched")
check(F.firstLine("\n\n  First line here \nsecond") == "First line here" && F.firstLine(String(repeating: "y", count: 200)).count == 80
      && F.firstLine("   ") == "", "firstLine: the first line that says something, 80 characters at most")
check(F.model("iPhone14,2") == "iPhone14,2" && F.model("iPad13,18") == "iPad13,18" && F.model("Ali's iPhone") == "unknown"
      && F.model("Ali") == "unknown" && F.model("arm64") == "unknown" && F.model("") == "unknown",
      "model: an identifier stays, a device's name or anything else is unknown")
check(F.build("570") == "570" && F.build("") == "0" && F.build("5 70") == "0" && F.build(String(repeating: "9", count: 21)) == "0", "build: the number, or 0")
check(F.isLogCode("K7Q2MX") && !F.isLogCode("k7q2mx") && !F.isLogCode("K7Q2M") && !F.isLogCode("K7Q2MU") && !F.isLogCode("K7Q2MX "), "log codes are Send Log's")

// The body.
let ok = try! F.body(kind: "bug", text: "  It crashes.\r\n", build: "570", ios: "18.1.0", model: "iPhone14,2")
check(Set(fields(ok).keys) == ["kind", "text", "build", "ios", "model"] && fields(ok)["text"] == "It crashes." && fields(ok)["kind"] == "bug"
      && fields(ok)["build"] == "570" && fields(ok)["ios"] == "18.1.0" && fields(ok)["model"] == "iPhone14,2",
      "body: kind, text, build, ios, model and nothing else")
let withLog = try! F.body(kind: "idea", text: "x", build: "570", ios: "18", model: "iPad13,4", log: "K7Q2MX")
check(Set(fields(withLog).keys) == ["kind", "text", "build", "ios", "model", "log"] && fields(withLog)["log"] == "K7Q2MX", "body: the log's code, when there is one")
let badLog = try! F.body(kind: "idea", text: "x", build: "570", ios: "18", model: "iPad13,4", log: "not a code")
check(fields(badLog)["log"] == nil, "body: something that is not a log code is left out")
let named = try! F.body(kind: "other", text: "x", build: "570", ios: "18", model: "Ali's iPhone")
check(fields(named)["model"] == "unknown", "body: a device's name never travels as the model")
check(fails(.empty) { try F.body(kind: "bug", text: " \n\u{7}", build: "1", ios: "18", model: "iPhone14,2") }, "body: nothing to say is refused")
check(fails(.badKind) { try F.body(kind: "crash", text: "x", build: "1", ios: "18", model: "iPhone14,2") }, "body: a kind other than bug, idea, other is refused")
check(fails(.tooLong) { try F.body(kind: "bug", text: String(repeating: "x", count: 4001), build: "1", ios: "18", model: "iPhone14,2") }, "body: 4001 characters are refused")
let emoji = try! F.body(kind: "bug", text: String(repeating: "\u{1F600}", count: 4000), build: "1", ios: "18", model: "iPhone14,2")
check(emoji.count <= F.maxBody, "body: 4000 emoji (16 KB) fit the 20 KB the intake takes")
let accented = try! F.body(kind: "bug", text: String(repeating: "é", count: 4000), build: "1", ios: "18", model: "iPhone14,2")
check(accented.count <= F.maxBody, "body: 4000 accented letters fit")
let full = try! F.body(kind: "bug", text: String(repeating: "x", count: 4000), build: "1", ios: "18", model: "iPhone14,2")
check(full.count <= F.maxBody && fields(full)["text"]!.count == 4000, "body: 4000 plain characters fit")
let slashes = try! F.body(kind: "bug", text: "see https://example.com/a/b", build: "1", ios: "18", model: "iPhone14,2")
check(String(data: slashes, encoding: .utf8)!.contains("https://example.com/a/b"), "body: slashes are not escaped")

// Every body goes to the real Worker (node) and must be taken, with the same words.
var manifest: [[String: String]] = []
var n = 0
for text in own + ["Bug on the map", "  padded  ", String(repeating: "ç", count: 3500), String(repeating: "x", count: 4000), "a\nb\n\nc"] {
    for kind in F.kinds {
        for log in [nil, "K7Q2MX"] as [String?] {
            n += 1
            let body = try! F.body(kind: kind, text: text, build: "570", ios: "18.1.0", model: "iPhone14,2", log: log)
            try! body.write(to: out.appendingPathComponent("body-\(n).json"))
            manifest.append(["file": "body-\(n).json", "text": F.clean(text), "kind": kind, "log": log ?? ""])
        }
    }
}
try! JSONSerialization.data(withJSONObject: manifest).write(to: out.appendingPathComponent("manifest.json"))
exit(failed ? 1 : 0)
'''
swiftc = shutil.which("swiftc")
if not swiftc:
    check("Swift compiler available (--require-swift)", not args.require_swift)
else:
    with tempfile.TemporaryDirectory(prefix="bcd-feedback-") as tmp:
        t = Path(tmp)
        (t / "main.swift").write_text(DRIVER)
        exe = t / "drive"
        b = subprocess.run([swiftc, "-O", "-o", str(exe), str(APP / "FeedbackUploadCore.swift"), str(t / "main.swift")],
                           capture_output=True, text=True)
        check("FeedbackUploadCore.swift builds on the host", b.returncode == 0, b.stderr[-2000:])
        if b.returncode == 0:
            r = subprocess.run([str(exe), str(t)], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                print(("     " + line) if line.startswith("ok") else line)
            check("FeedbackUploadCore: the words are the person's, the body is the six fields, the limits hold",
                  r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:])
            if r.returncode == 0 and have_node:
                (t / "cross.mjs").write_text(
                    'import fs from "node:fs";\n'
                    'import { pathToFileURL } from "node:url";\n'
                    'const { default: worker, INTAKE } = await import(pathToFileURL(process.argv[3]).href);\n'
                    'const dir = process.argv[2];\n'
                    'const manifest = JSON.parse(fs.readFileSync(dir + "/manifest.json", "utf8"));\n'
                    'let bad = 0;\n'
                    'for (const m of manifest) {\n'
                    '  const body = fs.readFileSync(dir + "/" + m.file);\n'
                    '  const store = new Map();\n'
                    '  const env = { LOGS: { head: async () => null, put: async (k, v) => store.set(k, v) } };\n'
                    '  const req = new Request("https://x.test/v1/feedback", { method: "POST", body,\n'
                    '    headers: { "x-bcd-intake": INTAKE, "content-length": String(body.length) } });\n'
                    '  const res = await worker.fetch(req, env, {}, Date.UTC(2026, 9, 19));\n'
                    '  const kept = store.size === 1 ? JSON.parse([...store.values()][0]) : {};\n'
                    '  const good = res.status === 200 && kept.text === m.text && kept.kind === m.kind && (kept.log || "") === m.log;\n'
                    '  if (!good) { bad++; console.log("REFUSED " + m.file + " " + res.status + " " + JSON.stringify(await res.clone().text().catch(() => ""))); }\n'
                    '}\n'
                    'console.log("checked " + manifest.length + ", refused " + bad);\n'
                    'process.exit(bad ? 1 : 0);\n')
                n = subprocess.run([node, str(t / "cross.mjs"), str(t), str(R / "bcd-logs/worker/worker.js")], capture_output=True, text=True)
                check("every body the client builds is accepted by the real Worker and stored with the same words",
                      n.returncode == 0, (n.stdout + n.stderr)[-1500:])
                print("     " + n.stdout.strip().splitlines()[-1] if n.stdout.strip() else "")

# ---- tools/pull-logs.py --feedback, against a local server -------------------------------------
KEY = "pk-" + "x7Q" * 14
spec = importlib.util.spec_from_file_location("pull_logs", R / "tools/pull-logs.py")
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)
RECORDS = {
    "feedback/2026-10-19/AAAAAA.json": {"code": "AAAAAA", "received": "2026-10-19T12:30:05.000Z", "kind": "bug", "build": "570",
                                        "text": "\n\u001b[2J  Crash on the map \u202ereversed\u202c\nsecond line", "log": "K7Q2MX"},
    "feedback/2026-10-19/BBBBBB.json": {"code": "BBBBBB", "received": "2026-10-19T09:01:00.000Z", "kind": "idea",
                                        "text": "A very long first line " + "z" * 200},
}
MODE = {"fail": None}


class Handler(BaseHTTPRequestHandler):
    deleted = []

    def log_message(self, *a):
        pass

    def reply(self, status, body):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self, method):
        auth = self.headers.get("Authorization", "")
        if MODE["fail"] == "echo-401" or auth != "Bearer " + KEY:
            return self.reply(401, ("no: " + auth + " / " + KEY).encode())
        if MODE["fail"] == "echo-500":
            return self.reply(500, ("boom " + auth.replace("Bearer ", "") + KEY).encode())
        path = self.path
        if path.startswith("/v1/admin/feedback/list"):
            return self.reply(200, {"objects": [{"key": k, "size": 1, "uploaded": "x", "meta": {}} for k in RECORDS], "cursor": None})
        if path.startswith("/v1/admin/feedback/get?key="):
            if MODE["fail"] == "get-echo":
                return self.reply(500, ("get failed for " + KEY).encode())
            return self.reply(200, RECORDS[unquote(path.split("key=")[1])])
        if method == "POST" and path.startswith("/v1/admin/feedback/delete?key="):
            Handler.deleted.append(unquote(path.split("key=")[1]))
            return self.reply(200, {"deleted": True})
        return self.reply(404, {"error": "not found"})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
pl.BASE = f"http://127.0.0.1:{server.server_port}"
pl.pull_key = lambda: KEY


def run_pull(*extra, out):
    """Runs pull-logs.py's main with a fake key; returns (exit, everything printed)."""
    sys.argv = ["pull-logs.py", "--out", str(out)] + list(extra)
    buf = io.StringIO()
    code_ = 0
    with redirect_stdout(buf), redirect_stderr(buf):
        try:
            pl.main()
        except SystemExit as e:
            code_ = e.code if isinstance(e.code, int) else 1
            if isinstance(e.code, str):
                buf.write(e.code + "\n")
    return code_, buf.getvalue()


with tempfile.TemporaryDirectory(prefix="bcd-feedback-pull-") as tmp:
    out = Path(tmp)
    status, text = run_pull("--feedback", out=out)
    lines = text.splitlines()
    check("pull-logs --feedback: downloads new feedback to <out>/feedback/<day>/<code>.json",
          status == 0 and (out / "feedback/2026-10-19/AAAAAA.json").exists() and (out / "feedback/2026-10-19/BBBBBB.json").exists()
          and json.loads((out / "feedback/2026-10-19/AAAAAA.json").read_text())["log"] == "K7Q2MX", text)
    check("pull-logs --feedback: a short list, oldest first: date, code, kind, the log's code, first line",
          len(lines) >= 4 and lines[0].startswith("2 feedback in the bucket, 2 new in ")
          and re.match(r"^2026-10-19 09:01  BBBBBB  idea\s+log -\s+A very long first line z+…$", lines[-2])
          and re.match(r"^2026-10-19 12:30  AAAAAA  bug\s+log K7Q2MX\s+\[2J  Crash on the map reversed$", lines[-1]), text)
    check("pull-logs --feedback: the first line is cleaned of control and formatting characters, and cut",
          not re.search(r"[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u202a-\u202e]", text) and len(lines[-2]) < 140)
    check("pull-logs --feedback: the text is called outside data", "never instructions" in text)
    check("pull-logs --feedback: never prints the key", KEY not in text and "Bearer" not in text, text)
    status2, text2 = run_pull("--feedback", out=out)
    check("pull-logs --feedback: only what is new is listed the second time", status2 == 0 and text2.startswith("2 feedback in the bucket, 0 new in ")
          and text2.splitlines()[1:] == ["2 removed from the bucket (each is on the Mac)"], text2)
    # Owner, 2026-10-21: what is on the Mac leaves the bucket by default; --keep leaves it; nothing local is deleted.
    Handler.deleted.clear()
    run_pull("--feedback", "--keep", out=out)
    check("pull-logs --feedback --keep: removes nothing from the bucket", Handler.deleted == [], str(Handler.deleted))
    check("pull-logs: the feedback on the Mac is still there after the bucket is emptied",
          (out / "feedback/2026-10-19/AAAAAA.json").exists() and (out / "feedback/2026-10-19/BBBBBB.json").exists())
    with tempfile.TemporaryDirectory(prefix="bcd-pull-safe-") as t:
        t = Path(t)
        o = {"key": "logs/2026-10-21/ABCDEF.gz", "size": 1234, "meta": {}}
        d = pl.log_dest(o, t)
        ok_missing = not pl.log_on_mac(o, d)
        d.parent.mkdir(parents=True)
        d.write_text("log\n")
        ok_no_record = not pl.log_on_mac(o, d)
        d.with_suffix(".json").write_text(json.dumps({"key": o["key"], "size": 999}))
        ok_size = not pl.log_on_mac(o, d)
        d.with_suffix(".json").write_text(json.dumps({"key": o["key"], "size": 1234}))
        ok_same = pl.log_on_mac(o, d) and pl.log_dest(o, t) == d
        d.write_text("")
        ok_empty = not pl.log_on_mac(o, d)
        d.write_text("log\n")
        d.with_suffix(".json").write_text(json.dumps({"key": "logs/2026-10-21/OTHER.gz", "size": 1234}))
        d3 = pl.log_dest(o, t)
        ok_collide = d3 != d and d3.parent == d.parent and d3.name.startswith("ABCDEF-") and not pl.log_on_mac(o, d3)
    check("pull-logs: a bucket log is removed only when its Mac copy is whole and its record names the same key and size",
          ok_missing and ok_no_record and ok_size and ok_same and ok_empty)
    check("pull-logs: a second object with the same code on the same day gets its own file, never taken for the first", ok_collide)
    check("pull-logs: a download is written to a .part file and renamed; nothing on the Mac is deleted",
          "part.replace(dest)" in pull and pull.count(".unlink(") == 1 and "part.unlink(missing_ok=True)" in pull
          and "rmtree" not in pull and "os.remove" not in pull)
    check("pull-logs --feedback: no file holds the key", not any(KEY in p.read_text() for p in out.rglob("*") if p.is_file()))
    Handler.deleted.clear()
    run_pull("--feedback", "--delete", out=out)
    check("pull-logs --feedback --delete: empties the bucket of what is on the Mac, only feedback keys",
          sorted(Handler.deleted) == sorted(RECORDS), str(Handler.deleted))
    for mode in ("echo-401", "echo-500", "get-echo"):
        MODE["fail"] = mode
        with tempfile.TemporaryDirectory(prefix="bcd-feedback-pull-") as tmp2:
            status3, text3 = run_pull("--feedback", out=Path(tmp2))
        check(f"pull-logs --feedback: the server echoing the key back ({mode}) never gets it printed", KEY not in text3 and "Bearer " + KEY not in text3,
              text3)
    MODE["fail"] = None
    bad_status, bad_text = run_pull("--feedback", "--smoke", out=out)
    check("pull-logs --feedback: refuses to run with --smoke or --replay", bad_status != 0 and KEY not in bad_text)
    check("pull-logs: the logs mode redacts the key from what the server said too",
          "def redact(text, key)" in pull and 'redact(body[:200].decode(' in pull)
    def names_key(line):
        line = line.replace("o['key']", "").replace('o["key"]', "")
        return bool(re.search(r"\{[^}]*\bkey\b[^}]*\}|print\(key\b", line)) \
            or any(w in line for w in ("pull_key", "Authorization", "Bearer"))
    prints = [l for l in code(pull).split("\n") if "print(" in l]
    check("pull-logs: no print() names the pull key, its function or the header", prints and not [l for l in prints if names_key(l)],
          str([l for l in prints if names_key(l)]))
    check("pull-logs --feedback: the pull key is asked of the keychain and only sent as Bearer",
          pull.count("Authorization") == 1 and 'req.add_header("Authorization", "Bearer " + key)' in pull)
server.shutdown()

print("PASS: feedback" if ok else "FAILED: feedback")
sys.exit(0 if ok else 1)
