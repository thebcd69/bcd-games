#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The performance trace (round 604, D3): one line a second, size-limited, sent with the log when the player asked for it.

Behaviour (swiftc; --require-swift makes its absence a failure):
  - app/Madeira/BCDPerfTraceCore.swift compiled with tests/host/bcd_perftrace_checks.swift: the row and its reading back, the frames of an
    interval (rate, mean and worst gap, a stall, a program that never presented), the cut (first 120 + newest 3480 of at most 3600),
    the file names, the store (what Send Log appends, the note for the comment box, keeping the newest 20), the digest;
  - the block the app appends is read by tools/log-facts.py (its card says how the session went), field for field;
  - LogUploadCore.pack's trailer is tested in check-log-upload.py.

Source (every host):
  1. Off, nothing exists: the sampler makes its one timer only in began(), only when the player's switch or a guided debug run says so, and
     stops it with the session; no GPU meter is turned on, the frame ring is read through a reader of its own (the HUD's is not shared),
     battery monitoring is turned on only if it was off and turned off again.
  2. The file lives under Documents/logs/traces (the logs' own listing does not show it), is named after the session log's inode, is cut back
     only by a rewrite once in `slack` rows, and the newest 20 are kept.
  3. The hooks are the ones listed: the recorder (start, end), Send Log (trailer), the comment box (a note), Settings › Logs (the switch,
     off by default).
  4. CI runs this check.

  check-bcd-perftrace.py [--require-swift]
"""
import argparse
import importlib.util
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
core_text = (APP / "BCDPerfTraceCore.swift").read_text()
live_text = (APP / "BCDPerfTraceLive.swift").read_text()
core, live = code(core_text), code(live_text)
recorder = code((APP / "BCDSessionRecorderLive.swift").read_text())
upload = code((APP / "LogUpload.swift").read_text())
upload_core = (APP / "LogUploadCore.swift").read_text()
comp = (APP / "BCDComponents.swift").read_text()
sections = (APP / "BCDSettingsSections.swift").read_text()
settings_live = (APP / "BCDSettingsLive.swift").read_text()
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
preview = (R / "tools/ui-preview.py").read_text()

# ---- 1. licence and layering ---------------------------------------------------------------------------------
check(core_text.startswith(HEADER) and live_text.startswith(HEADER), "BCDPerfTraceCore.swift and BCDPerfTraceLive.swift: BCD's licence header")
check(re.findall(r"^import (\w+)", core, re.M) == ["Foundation"] and "UIKit" not in core and "SwiftUI" not in core,
      "the core knows Foundation only")
check(not re.search(r"\b(LibraryModel|LogStore|MadeiraConfig|madeira_|winios_|wine_|bcd_frame_|UserDefaults)\b", core), "the core names no engine type")
check(not re.search(r"(?i)auto-?tune", core + live), "the new files name no auto-tune")
for f in ("BCDPerfTraceCore.swift", "BCDPerfTraceLive.swift"):
    check(pbx.count(f"/* {f} in Sources */") == 2 and pbx.count(f"path = {f};") == 1, f"the Xcode project builds {f}")
check(re.search(r'ENGINE_BOUND \|= \{[^}]*"BCDPerfTraceLive.swift"', preview) is not None, "the preview app leaves the sampler out (the core builds there)")
check("python3 tests/host/check-bcd-perftrace.py" in workflow, "CI runs this check")

# ---- 2. the sampler: off, nothing exists ------------------------------------------------------------------------
cls = block(live, "@MainActor final class BCDPerfTraceLive {")
began = block(cls, "func began() {")
check(cls.count("Timer(") == 1 and "Timer(" in began and "RunLoop.main.add(t" in began and "DispatchSource" not in cls and "DispatchQueue" not in cls,
      "one timer, made in began() and nowhere else; no queue of its own")
# Porthole port, item 11: the [benchmark] line's key (off by default) may also start the sampler, without a file.
check(began.index("let records = BCDTraceRules.records(switchOn: Self.switchOn, guidedRun: Self.guidedRun)")
      < began.index("guard records || benchmark != nil, let store = Self.store else { return }") < began.index("Timer("),
      "... only after the switch or a guided debug run says so")
check("t.tolerance = 0.25" in began and "BCDTraceRules.intervalSeconds" in began, "a second, with a tolerance the system may fold into another wake-up")
stop = block(cls, "private func stop() {")
check("timer?.invalidate()" in stop and "timer = nil" in stop and "handle?.close()" in stop, "the session's end stops the timer and closes the file")
check("madeira_gpu_meter_enable" not in live, "the GPU meter is never turned on for the trace (it costs a completion handler per frame)")
check("bcd_frame_reader_start()" in began and "bcd_frame_ring_drain(&reader" in cls and "BCDFrameRing" not in live, "the frame ring is read through a reader of the trace's own")
check("if !UIDevice.current.isBatteryMonitoringEnabled {" in began and "batteryMonitored = true" in began
      and "if batteryMonitored { UIDevice.current.isBatteryMonitoringEnabled = false; batteryMonitored = false }" in stop,
      "battery monitoring is turned on only when it was off, and off again")
check("UserDefaults.standard.bool(forKey: switchKey)" in cls and 'static let switchKey = "madeira.bcd.trace"' in cls,
      "the switch is off unless set (a bool that was never written is false)")
check("static var guidedRun = false" in cls and "UserDefaults" not in cls.split("static var guidedRun")[1].split("static var folder")[0],
      "a guided run's request is never saved: an app closed meanwhile forgets it")
check('appendingPathComponent("logs").appendingPathComponent(BCDTraceRules.folder, isDirectory: true)' in cls,
      "the traces live in Documents/logs/traces: the log list reads only logs/*.txt, so it does not show them")
check("systemFileNumber" in began and "store.url(inode: inode)" in began, "the file is named after the session log's inode")
check("store.prune(keep: BCDTraceRules.keptFiles - 1)" in began, "the newest 20 are kept (19 old ones and this one)")
sample = block(cls, "private func sample() {")
check(sample.count("handle.write(contentsOf:") == 1 and "fsync" not in sample and "synchronize" not in sample,
      "one write a second, no sync")
check("if rows >= cutAt { cutBack() }" in sample and "cutAt = BCDTraceRules.maxLines + BCDTraceRules.slack" in began,
      "the file is cut back by a rewrite once in `slack` rows, not every second")
check("BCDHUDLive.memory()" in sample and "madeira_hud_jit_pool" in sample and "madeira_hud_swap_bytes()" in sample and "thermalState" in sample,
      "a sample reads memory (iOS's own number), the JIT pool, the swap and the heat")
check(sample.count("task_threads") == 0 and "cpu?.sample()" in sample, "the CPU comes from the HUD's own meter")
check("static let intervalSeconds = 1.0" in core and "static let maxLines = 3600" in core and "static let keptFiles = 20" in core,
      "the numbers: a second, an hour, 20 traces")

# ---- 3. the hooks ------------------------------------------------------------------------------------------------
rec_began = block(recorder, "func began(_ entry: LibraryEntry) {")
rec_ended = block(recorder, "func ended() {")
check(rec_began.count("BCDPerfTraceLive.shared.began()") == 1 and rec_began.index("writeMarker(for: entry)") < rec_began.index("BCDPerfTraceLive.shared.began()"),
      "the recorder starts the trace after the marker is written")
check(rec_ended.count("BCDPerfTraceLive.shared.ended()") == 1 and rec_ended.index("summary(partial: false).line()") < rec_ended.index("BCDPerfTraceLive.shared.ended()"),
      "... and ends it after the summary line")
check(recorder.count("BCDPerfTraceSend.install()") == 1, "the comment box learns about traces when the app starts")
check("trailer: [String] = []" in upload_core and "for line in trailer" in upload_core, "LogUploadCore.pack takes a trailer (default: none)")
check("let trace = BCDPerfTraceSend.lines(for: log)" in upload and "trailer: trace" in upload and upload.index("guard recentLogs().contains(") < upload.index("BCDPerfTraceSend.lines(for: log)"),
      "Send Log appends the trace after it has checked the log is one of the 20 newest")
check("static var traceNote: ((URL) -> String?)?" in comp and "if let log, let note = traceNote?(log) { message += " in comp,
      "the comment box says that a trace goes with the log")
check("var perfTrace: Bool { get }" in sections and "var perfTrace: Bool { false }" in sections and 'BCDSettingsID.control(.logs, "perftrace")' in sections
      and "BCDPerfTraceLive.switchOn" in settings_live, "Settings › Logs has the switch, off by default")
send = block(live, "enum BCDPerfTraceSend {")
check("BCDPerfTraceLive.store" in send and "inode(of: log)" in send, "what is sent is the trace of this log's inode and no other")

# ---- behaviour --------------------------------------------------------------------------------------------------
swiftc = shutil.which("swiftc")
if not swiftc:
    check(not args.require_swift, "swiftc on the host (behaviour checks)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-perftrace-") as t:
        t = Path(t)
        exe, sent = t / "run", t / "sent.txt"
        b = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(exe), str(APP / "BCDPerfTraceCore.swift"),
                            str(R / "tests/host/bcd_perftrace_checks.swift")], capture_output=True, text=True)
        check(b.returncode == 0, "the trace core and its checks compile on the host", b.stderr[-1500:])
        if b.returncode == 0:
            r = subprocess.run([str(exe), str(sent)], capture_output=True, text=True)
            oks = [l for l in r.stdout.splitlines() if l.startswith("ok")]
            bad = [l for l in r.stdout.splitlines() if l.startswith("FAIL") or l.startswith("     ")]
            check(r.returncode == 0 and len(oks) >= 30, f"the trace rules: {len(oks)} checks", "\n      ".join(bad[:12]))
            # tools/log-facts.py reads the block the app appends
            spec = importlib.util.spec_from_file_location("log_facts", R / "tools/log-facts.py")
            facts = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(facts)
            lines = ["[21:05:03.000] [INFO] [trace] recording one line a second to logs/traces/x.trace.csv", "last log line"]
            lines += sent.read_text().split("\n") if sent.exists() else []
            header, rows = facts.parse_trace(lines)
            check(header is not None and header.get("jit_total") == "896" and header.get("rows") == "200" and header.get("start") == "21:05:03.120"
                  and len(rows) == 200 and rows[0]["t"] == 0 and rows[-1]["t"] == 199 and rows[5]["mem"] == 1005 and rows[3]["fps"] == 58.1,
                  "log-facts.py reads the block the app appends, field for field (the app's own timestamped [trace] line is no part of it)", str(header))
            words = facts.trace_words(header, rows)
            check("3 min" in words and "fps avg 58" in words and "memory peak 1199 MB" in words and "JIT pool peak 40 of 896 MB" in words, "... and says it in words", words)
            check(facts.parse_trace(["[trace] 1,2,3", "x"])[1] == [] and facts.parse_trace([])[1] == [], "... nothing else is a trace")

print("PASS: performance trace" if failures == 0 else f"FAILED: performance trace ({failures})")
sys.exit(1 if failures else 0)
