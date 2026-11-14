#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Send Log's "Make big logs smaller" and the [benchmark] line (Porthole port, item 11). No iPhone.

1. The thinner (app/Madeira/BCDLogThin.swift) and Send Log's packing (LogUploadCore.swift), compiled and run here on a made log of
   2 MB: off (no plan) the gzip is what it always was; on, the note comes first, the first 300 and last 400 lines, BCD's own
   timestamped lines and its measurement tags stay whole, a storm of one kind keeps its first 150 and one in 40, and every kept line
   is scrubbed as before. A log under 1.5 MB is sent whole.
2. The [benchmark] line (BCDPerfTraceCore.swift, BCDBenchmark), compiled and run: nil without a frame; the averages, the slowest 1 %
   and 5 % of seconds, the lowest second, the memory, swap, JIT and heat peaks, the switches in force and the game's name last.
3. Off path, on the source: the thinning switch is a UserDefaults bool (absent: off) and pack gets no plan without it; the line's key
   is read once at a session's start and the sampler runs without a trace file only for it; the trace as before otherwise.
4. Settings, catalog, credit, workflow.
"""
from pathlib import Path
import gzip
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
failures = []


def check(what, cond, detail=""):
    print(("PASS: " if cond else "FAIL: ") + what + (f"\n      {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(what)
    return cond


def block(text, start):
    i = text.index(start)
    depth = 0
    for j in range(text.index("{", i), len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    raise ValueError(start)


thin_src = (APP / "BCDLogThin.swift").read_text()
upload = (APP / "LogUpload.swift").read_text()
upload_core = (APP / "LogUploadCore.swift").read_text()
trace_core = (APP / "BCDPerfTraceCore.swift").read_text()
trace_live = (APP / "BCDPerfTraceLive.swift").read_text()

swiftc = shutil.which("swiftc")
if not swiftc:
    check("swiftc for the behaviour checks", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory(prefix="bcd-log-thin-") as tmp:
        t = Path(tmp)
        # ---- 1. thinning through Send Log's packing
        main = t / "main.swift"
        main.write_text(r'''
import Foundation
let a = CommandLine.arguments
let src = URL(fileURLWithPath: a[1]), dst = URL(fileURLWithPath: a[2])
var plan = a[3] == "thin" ? BCDLogThinner.plan(src) : nil
let r = try! LogUploadCore.pack(src, to: dst, comment: "it stutters", trailer: ["[trace] end"], head: plan.map { [$0.note] } ?? [],
                                keep: plan == nil ? nil : { plan!.thinner.keep($0) })
print("\(plan == nil ? "whole" : "thinned") \(r.raw) \(r.packed)")
''')
        exe = t / "pack"
        b = subprocess.run([swiftc, "-O", "-o", str(exe), str(APP / "LogUploadCore.swift"), str(APP / "BCDLogThin.swift"), str(main)],
                           capture_output=True, text=True)
        if check("the thinner builds with Send Log's packing on the host", b.returncode == 0, b.stderr[-2500:]):
            lines = [f"head line {i} from user@example.com" for i in range(300)]
            for i in range(30000):
                lines.append(f"[thread-stacks]   bt[{i % 7}] Madeira`server_select+0x{i:x}")
                if i % 100 == 0:
                    lines.append(f"[12:00:{i % 60:02d}.{i % 1000:03d}] [INFO] [autotune] Game: line {i}")
                if i % 500 == 0:
                    lines.append(f"[footprint] rev=ml1060 fast phys={i} MB")
                if i % 1000 == 0:
                    lines.append(f"0150:err:module:load_dll failed to load L\"x{i}.dll\"")
            lines += [f"tail line {i} 10.0.0.{i % 200}" for i in range(400)]
            src = t / "big.txt"
            src.write_text("\n".join(lines) + "\n")
            def run(mode, path):
                out = t / f"{mode}.gz"
                r = subprocess.run([str(exe), str(path), str(out), mode], capture_output=True, text=True)
                return r.stdout.strip(), gzip.decompress(out.read_bytes()).decode() if r.returncode == 0 else ""
            said, whole = run("whole", src)
            check("off: every line is sent (scrubbed), the comment first and the trailer last, as always",
                  said.startswith("whole") and whole.count("\n") == len(lines) + 2 and whole.startswith("[user-comment] it stutters\n")
                  and whole.rstrip("\n").endswith("[trace] end") and "user@example.com" not in whole, said)
            said, small = run("thin", src)
            got = small.split("\n")
            check("on: the comment, then the note, then the log", got[0] == "[user-comment] it stutters" and got[1].startswith("[log-thin] ")
                  and " of %d lines" % len(lines) in got[1], got[:2])
            body = got[2:-2]
            check("the first 300 and the last 400 lines stay, scrubbed", body[:300] == [f"head line {i} from <email>" for i in range(300)]
                  and body[-400:] == [f"tail line {i} <ip>" for i in range(400)])
            check("BCD's own timestamped lines and its measurement tags stay whole",
                  sum(1 for l in body if "[INFO] [autotune]" in l) == 300 and sum(1 for l in body if l.startswith("[footprint]")) == 60)
            stacks = [l for l in body if l.startswith("[thread-stacks]")]
            check("a storm of one kind keeps its first 150 and one in 40 after that", len(stacks) == 150 + (30000 // 40 - 150 // 40), len(stacks))
            check("the copy is several times smaller and the trailer still ends it", len(small) * 4 < len(whole) and got[-2] == "[trace] end")
            tiny = t / "tiny.txt"
            tiny.write_text("\n".join(lines[:5000]) + "\n")
            said, _ = run("thin", tiny)
            check("a log under 1.5 MB is sent whole even with the switch on", said.startswith("whole"), said)

        # ---- 2. the [benchmark] line
        (t / "bench").mkdir()
        bmain = t / "bench" / "main.swift"   # top-level code is allowed in main.swift only
        bmain.write_text(r'''
import Foundation
var fails = 0
func expect(_ ok: Bool, _ what: String) { print((ok ? "ok   " : "FAIL ") + what); if !ok { fails += 1 } }
expect(BCDBenchmark.line(game: "G", build: "658", samples: [], switches: []) == nil, "no sample: no line")
var none = BCDTraceSample(); none.t = 1; none.fps = 0
expect(BCDBenchmark.line(game: "G", build: "658", samples: [none], switches: []) == nil, "no frame: no line")
var samples: [BCDTraceSample] = []
for i in 0..<100 {
    var s = BCDTraceSample(); s.t = i + 1; s.fps = i == 0 ? 0 : Double(i % 10 == 0 ? 20 : 60); s.worstMs = i == 50 ? 120 : 17
    s.memoryMB = 1000 + i; s.freeMB = 3000 - i * 10; s.swapMB = i; s.jitMB = 200 + i; s.thermal = i > 90 ? 2 : 0
    samples.append(s)
}
let line = BCDBenchmark.line(game: "Some Game", build: "658", samples: samples, switches: ["MADEIRA_PRESENT_FX=1"])!
print(line)
expect(line.hasPrefix("[benchmark] build=658 seconds=100 "), "the build and the length first")
expect(line.contains(" fps-avg=56.4 ") && line.contains(" fps-1pct=20.0 ") && line.contains(" fps-5pct=20.0 ") && line.contains(" fps-min=20.0 "),
       "the average and the slowest seconds, over the seconds with frames")
expect(line.contains(" worst-ms=120 ") && line.contains(" peak-mb=1099 ") && line.contains(" least-free-mb=2010 ") && line.contains(" swap-peak-mb=99 ")
       && line.contains(" jit-peak-mb=299 ") && line.contains(" heat=serious "), "the peaks")
expect(line.contains(" switches=MADEIRA_PRESENT_FX=1 ") && line.hasSuffix(" game=Some Game"), "the switches, and the game's name last")
expect(BCDBenchmark.line(game: "G", build: "1", samples: samples, switches: [])!.contains(" switches=none "), "no switch: none")
expect(BCDBenchmark.on("1") && !BCDBenchmark.on(nil) && !BCDBenchmark.on("0") && !BCDBenchmark.on(""), "only 1 is on")
exit(fails == 0 ? 0 : 1)
''')
        bexe = t / "bench-run"
        b = subprocess.run([swiftc, "-O", "-o", str(bexe), str(APP / "BCDPerfTraceCore.swift"), str(bmain)], capture_output=True, text=True)
        if check("BCDPerfTraceCore.swift with the line builds on the host", b.returncode == 0, b.stderr[-2500:]):
            r = subprocess.run([str(bexe)], capture_output=True, text=True)
            print(r.stdout.rstrip())
            check("the [benchmark] line", r.returncode == 0)

# ---- 3. off path
check("the thinning switch is a UserDefaults bool (absent: off) and Send Log plans only with it",
      'static let switchKey = "bcd.logs.thin"' in thin_src
      and "var thin = UserDefaults.standard.bool(forKey: BCDLogThinner.switchKey) ? BCDLogThinner.plan(log) : nil" in upload)
check("pack's new parts default to nothing, and a line is dropped only when the plan says so",
      'head: [String] = [], keep: ((UnsafeRawBufferPointer) -> Bool)? = nil' in upload_core
      and "if let keep, !keep(line) { return }" in upload_core and "for line in head {" in upload_core)
check("the line's key is read once at a session's start (bcd.cfg or the game's line, a named constant: no catalog churn)",
      "benchmark = BCDBenchmark.on(MadeiraConfig.gameValue(BCDBenchmark.key) ?? MadeiraConfig.get(BCDBenchmark.key)) ? [] : nil" in trace_live)
began = block(trace_live, "func began() {")
check("without the trace's switch and without the key, began() returns before anything is made, as before",
      "guard records || benchmark != nil, let store = Self.store else { return }" in began
      and began.index("guard records || benchmark != nil") < began.index("Timer("))
check("the trace file is made only when the trace records", "if records {" in began and began.index("if records {") < began.index("store.url(inode: inode)"))
sample = block(trace_live, "private func sample() {")
check("a second is written to the file only when there is one; the line keeps its samples only when asked",
      "guard let handle else { return }" in sample and "benchmark?.append(s)" in sample
      and sample.index("benchmark?.append(s)") < sample.index("guard let handle else { return }"))
ended = block(trace_live, "func ended() {")
check("the session's end says the trace's lines only for a trace, and writes the [benchmark] line only when asked",
      "if recording { LogStore.shared.log(\"[trace] \\(lines) lines kept\") }" in ended and "BCDBenchmark.line(" in ended)

# ---- 4. the rest
sections = (APP / "BCDSettingsSections.swift").read_text()
check("Settings › Logs has the thinning switch, off by default", 'title: "Make big logs smaller before sending"' in sections
      and "var thinsLogs: Bool { false }" in sections)
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
check("the catalog has the line's key, off by default", re.search(r'key: "benchmark-line", title: "[^"]*", kind: \.bool, defaultValue: "0"', catalog) is not None)
check("BCDLogThin.swift keeps Porthole's licence and copyright", thin_src.startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright (C) 2026 The Porthole authors.")
      and "app/Sources/Core/LogThinner.swift" in thin_src[:600] and "BCD Material" not in thin_src)
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
check("its row in the notices", "| `app/Madeira/BCDLogThin.swift` |" in notices)
check("the new file is in the project", "BCDLogThin.swift in Sources" in (R / "app/Madeira.xcodeproj/project.pbxproj").read_text())
check("the workflow runs this check", "run: python3 tests/host/check-log-thin.py" in (R / ".github/workflows/build-ipa.yml").read_text())

print("FAILED: log thinning and the benchmark line" if failures else "PASS: log thinning and the benchmark line")
sys.exit(1 if failures else 0)
