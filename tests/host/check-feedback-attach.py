#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Settings > Feedback > "Attach my last game's log" (round 707, A2's F15: three bug reports of the round could not be matched to a session).

A bug report carries the newest game log unless the person turns that off; the choice is kept; an idea or another message carries a log only when
the person turns the switch on for it, as before. The log goes through the existing upload (LogUpload.send, scrubbed on the device) and its code goes
into the message (FeedbackUpload.send, the `log` field the intake already takes); nothing else changes.

  1. BCDFeedbackAttach.swift (Foundation only) compiled with tests/host/bcd_feedback_attach_checks.swift: the stored choice (absent means on), who carries
     the log, the words that say which log goes.
  2. Source: the draft's two choices, the row, the privacy line while a log goes, the live backend (the log first, then the message; a retry does not send
     the log twice; the stored choice is not undone when there is no log), the upload path and the intake unchanged, the preview needs nothing.

  check-feedback-attach.py [--require-swift]
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
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
core = (APP / "BCDFeedbackAttach.swift").read_text()
view = (APP / "BCDSettingsFeedback.swift").read_text()
live = (APP / "BCDSettingsLive.swift").read_text()
upload = (APP / "FeedbackUpload.swift").read_text()
upload_core = (APP / "FeedbackUploadCore.swift").read_text()
worker = (R / "bcd-logs/worker/worker.js").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
workflow = (R / ".github/workflows/build-ipa.yml").read_text()

check(core.startswith(HEADER) and (R / "tests/host/bcd_feedback_attach_checks.swift").read_text().startswith(HEADER), "licence headers")
check(re.findall(r"^import (\w+)", core, re.M) == ["Foundation"], "the rules import Foundation only")
draft = block(view, "final class BCDFeedbackDraft: ObservableObject")
check("@Published private var bugLog = BCDFeedbackAttach.bugChoice()" in draft and "@Published private var otherLog = false" in draft,
      "the draft: a bug report starts from the stored choice (on unless turned off), any other message from off, as it always was")
check("get { kind == .bug ? bugLog : otherLog }" in draft and "BCDFeedbackAttach.setBugChoice(newValue)" in draft
      and re.search(r"if kind == \.bug \{\s*bugLog = newValue\s*BCDFeedbackAttach\.setBugChoice\(newValue\)\s*\} else \{\s*otherLog = newValue\s*\}", draft),
      "the draft: turning the switch off for a bug report is kept; for another message it is for that message only")
row = block(view, "private var attachRow: some View")
check("title: BCDFeedbackAttach.title" in row and "BCDFeedbackAttach.note(isBug: draft.kind == .bug, on: on, log: shown)" in row
      and "BCDFeedbackAttach.Shown(name: $0.name, when: BCDFormat.dateTime($0.date), size: BCDFormat.bytes($0.bytes), hasGame: $0.hasGame)" in row
      and "isOn: on, enabled: log != nil && !sending) { draft.attachLog = $0 }" in row,
      "the row: its own title, and the words name the game, the day and the size of the log that would go")
check('BCDSettingsToggleRow(id: BCDSettingsID.control(.feedback, "log")' in row, "the row is a controller control")
send_block = block(view, "private var sendBlock: some View")
check("Text(BCDFeedbackRules.privacy)" in send_block and "if backend.newestLog != nil && draft.attachLog {" in send_block
      and "Text(BCDFeedbackAttach.privacyWithLog)" in send_block, "the privacy line, and a second line while a log goes along")
check('static let privacy = "BCD sends what you write, the BCD build, the iOS version and the device model. No name or account."' in view,
      "the privacy line itself is word for word what it was")
lf = block(live, "final class BCDFeedbackLive: BCDFeedbackBackend")
check("guard draft.attachLog, let url = newest else { return post(kind, text, log: nil) }" in lf
      and lf.index("LogUpload.send(url") < lf.index("post(kind, text, log: code)") < lf.index("FeedbackUpload.send(kind: kind, text: text, log: log)"),
      "live: the log goes first through LogUpload.send, its code goes into the message, a message with no log or the switch off goes alone")
check("done.path == file.path, done.date == file.date, done.bytes == file.bytes" in lf and "return post(kind, text, log: done.code)" in lf,
      "live: a retried message does not send the log twice")
check("draft.attachLog = false" not in lf and "draft.attachLog" not in lf.split("func send()")[0], "live: having no log does not undo the person's stored choice")
check("BCDFeedbackAttach.logFailed(error.localizedDescription)" in lf and "Your message is still here" in lf + (APP / "BCDFeedbackAttach.swift").read_text(),
      "live: a log that cannot go leaves the words and names the switch")
check("LogUpload.send(url, game: LogUpload.game(of: url)" in lf and "LogUpload.recentLogs()" in lf, "live: the existing log upload, among the newest logs only")
check("static func send(kind: String, text: String, log: String? = nil" in upload and 'if let log, isLogCode(log) { fields["log"] = log }' in upload_core,
      "FeedbackUpload and FeedbackUploadCore are as they were: one optional `log` field")
log_lines = [l for l in worker.splitlines() if 'Object.hasOwn(body, "log")' in l]
check(len(log_lines) == 2 and not any("kind" in l for l in log_lines) and '!/^[0-9A-HJKMNP-TV-Z]{6}$/.test(body.log)' in worker,
      "the intake takes a log code with any kind (its two checks of `log` name no kind), so a bug report needs no new field or route", str(log_lines))
preview = (R / "app/BCDPreview/BCDSettingsPreviewData.swift").read_text()
check("attachLog" in preview and "draft.attachLog = true" in preview, "the preview's sample still sets the draft through attachLog")
check(project.count("/* BCDFeedbackAttach.swift in Sources */") == 2 and project.count("path = BCDFeedbackAttach.swift;") == 1, "BCDFeedbackAttach.swift is in the Xcode project")
if "python3 tests/host/check-feedback-attach.py" not in workflow:
    print("NOTE: .github/workflows/build-ipa.yml does not run this check yet (the step is in the hand-back)")

swiftc = shutil.which("swiftc")
if not swiftc:
    check(not args.require_swift, "swiftc on the host (behaviour checks)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-fbattach-") as t:
        tmp = Path(t)
        shutil.copy(R / "tests/host/bcd_feedback_attach_checks.swift", tmp / "main.swift")
        exe = tmp / "run"
        b = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(exe), str(APP / "BCDFeedbackAttach.swift"), str(tmp / "main.swift")],
                           capture_output=True, text=True)
        check(b.returncode == 0, "the rules and their checks compile on the host", b.stderr[-2000:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
            bad = [l for l in r.stdout.splitlines() if l.startswith("FAIL") or l.startswith("     ")]
            summary = [l for l in r.stdout.splitlines() if l.startswith("feedback attach:")]
            check(r.returncode == 0 and summary, summary[0] if summary else "the attach rules", "\n      ".join(bad[:10]))

print("check-feedback-attach: " + ("PASS" if failures == 0 else f"{failures} FAILED"))
sys.exit(0 if failures == 0 else 1)
