#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The display truth in every log (round 626, agent H S1; Hollow Knight: Silksong DF0537 = GXX35R, build 625, iPad17,1).

A player saw 120 FPS on the HUD and a game that looked like 60. The logs could not say what iOS granted: the two "[promote]" lines
went to stderr before stderr reaches the log, and has30Cap's first read published intent 0 over the intent apply(mode:) had set
(37 archive logs print display_intent=0Hz in MAX, RAW or the 40 cap). Log text only; nothing here changes pacing:
  * ProMotionIntent.setActive logs "[promote] display link armed ..." / "released" through LogStore (no stderr), apply(mode:) logs
    one "[promote] apply ..." line, and tick(_:) feeds DisplayRateMeter, which logs what the link was granted ("granted N Hz",
    "-- BELOW the request") and what the main run loop received ("arrived", "-- callbacks arrived late"), 12 lines a session at most;
  * LibraryModel.begin logs "[display-rate] session start: ..." after applying the pacing;
  * has30Cap's initialiser publishes the last intent instead of 0;
  * win32u's "[vmode] synthesized CURRENT = WxH @NHz" says the rate Windows programs are told.

Checks: the strings and call sites above; the meter (cut from ProMotionIntent.swift between "display-rate-meter:begin" and
"display-rate-meter:end", Foundation only) compiled with a harness: 120 Hz gives one line after 2 s and stays quiet, 60 Hz granted for
120 asked is flagged BELOW, a move 60 -> 120 is logged once more, a late main thread is told apart, a long pause is not read as a low
rate, never more than 12 lines, the first callbacks log nothing; the names other checks grep stay (holdMaximum, has30Cap,
ThermalPacing); CI runs this check.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
pm = (root / "app/Madeira/ProMotionIntent.swift").read_text()
lib = (root / "app/Madeira/Library.swift").read_text()
sp = (root / "build/win32u-unix/sysparams_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


check("no [promote] line goes to stderr any more (a session's first stderr is not in the log yet)", 'fputs("[promote]' not in pm)
check("setActive logs the link armed and released through LogStore",
      'LogStore.shared.log("[promote] display link armed preferred=' in pm and 'LogStore.shared.log("[promote] display link released")' in pm)
check("apply(mode:) records the intent and logs it once per call", "lastIntent = hz" in pm
      and 'LogStore.shared.log("[promote] apply mode=\\(mode) intent=\\(hz)Hz panel_max=\\(panelMaxFPS)Hz hold-in-60-cap=\\(holdMaximum ? 1 : 0)")' in pm)
init = pm[pm.index("static var has30Cap: Bool = {"):]
init = init[:init.index("}()")]
check("has30Cap's first read publishes the last intent, not 0 (DXMT's [frame] line said intent 0 while the link ran)",
      "madeira_set_display_max_fps(Int32(panelMaxFPS), Int32(lastIntent))" in init and "madeira_set_display_max_fps(Int32(panelMaxFPS), 0)" not in init)
tick = pm[pm.index("@objc private func tick(_ sender: CADisplayLink)"):]
tick = tick[:tick.index("\n    }\n") + 6]
check("tick(_:) feeds the meter with the link's own timestamps and logs what it returns",
      "meter.note(timestamp: sender.timestamp, targetTimestamp: sender.targetTimestamp" in tick and "LogStore.shared.log(line)" in tick)
check("a new link starts a new measurement", "meter.reset()" in pm[pm.index("func setActive("):pm.index("static func maxHz(for mode: Int32)")])
check("the names other checks read are kept", "static var holdMaximum: Bool" in pm and "static var has30Cap: Bool" in pm
      and "final class ThermalPacing {" in pm and 'MadeiraConfig.flag("MADEIRA_PROMOTE", fallback: false)' in pm)
beg = lib[lib.index("ProMotionIntent.apply(mode: entry.effectiveFPSMode)"):]
beg = beg[:600]
check("LibraryModel.begin logs the session's display rate after applying the pacing",
      'LogStore.shared.log("[display-rate] session start: limit=' in beg and "reported-to-Windows=\\(ProMotionIntent.reportedHz(for: entry.effectiveFPSMode)) Hz" in beg)
check('win32u says the rate Windows programs are told ("[vmode] synthesized CURRENT = WxH @NHz")',
      'dprintf(2, "[vmode] synthesized %s = %dx%d @%uHz\\n",' in sp and "(unsigned int)devmode->dmDisplayFrequency" in sp)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-display-rate.py" in wf)

b = pm.index("// display-rate-meter:begin")
e = pm.index("// display-rate-meter:end")
meter = pm[b:e]
check("the meter is pure (Foundation only)", "UIScreen" not in meter and "CADisplayLink" not in meter and "LogStore" not in meter)
HARNESS = r'''
import Foundation
var failed = 0
func check(_ ok: Bool, _ what: String) { print((ok ? "ok   " : "FAIL ") + what); if !ok { failed += 1 } }
func run(hz: Double, seconds: Double, start: Double = 100, dropEvery: Int = 0, meter: inout DisplayRateMeter) -> [String] {
    var out: [String] = []; var t = start; var n = 0
    while t < start + seconds {
        n += 1
        if dropEvery == 0 || n % dropEvery != 0 {
            if let l = meter.note(timestamp: t, targetTimestamp: t + 1 / hz, preferred: 120, panelMax: 120) { out.append(l) }
        }
        t += 1 / hz
    }
    return out
}
var m = DisplayRateMeter()
var l = run(hz: 120, seconds: 5, meter: &m)
check(l.count == 1 && l[0].contains("granted 120.0 Hz") && !l[0].contains("--"), "120 Hz: one line after 2 s, no warning: \(l.first ?? "-")")
l = run(hz: 120, seconds: 60, start: 200, meter: &m)
check(l.count <= 3, "steady 120 Hz for 60 s: quiet (at most one repeat per 20 s): \(l.count) lines")
m.reset(); l = run(hz: 60, seconds: 4, meter: &m)
check(l.count == 1 && l[0].contains("granted 60.0 Hz") && l[0].contains("BELOW the request"), "60 Hz granted for a 120 Hz request is flagged: \(l.first ?? "-")")
l += run(hz: 120, seconds: 6, start: 120, meter: &m)
check(l.count == 2 && l[1].contains("granted 120.0 Hz"), "a move from 60 to 120 Hz is logged once more")
m.reset(); l = run(hz: 120, seconds: 4, dropEvery: 3, meter: &m)
check(l.count == 1 && l[0].contains("granted 120.0 Hz") && l[0].contains("callbacks arrived late"), "a late main thread is told apart from a slow panel: \(l.first ?? "-")")
m.reset(); _ = run(hz: 120, seconds: 3, meter: &m)
let before = m.lines
l = run(hz: 120, seconds: 3, start: 300, meter: &m)
check(before == 1 && l.count <= 1 && !(l.first ?? "").contains("BELOW"), "a long pause does not read as a low rate")
m.reset(); var many = 0
for k in 0..<40 { let hz = k % 2 == 0 ? 120.0 : 60.0; many += run(hz: hz, seconds: 3, start: Double(1000 + k * 10), meter: &m).count }
check(many == DisplayRateMeter.maxLines, "never more than \(DisplayRateMeter.maxLines) lines a session: \(many)")
m.reset()
check(m.note(timestamp: 1, targetTimestamp: 1.008, preferred: 120, panelMax: 120) == nil && m.note(timestamp: 1.008, targetTimestamp: 1.016, preferred: 120, panelMax: 120) == nil, "the first callbacks log nothing")
exit(failed == 0 ? 0 : 1)
'''
swiftc = shutil.which("swiftc") or (shutil.which("xcrun") and "xcrun")
if swiftc:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "meter.swift").write_text("import Foundation\n" + meter)
        (t / "main.swift").write_text(HARNESS)
        cmd = ([swiftc] if swiftc != "xcrun" else ["xcrun", "swiftc"]) + ["-O", str(t / "meter.swift"), str(t / "main.swift"), "-o", str(t / "meter")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        check("the meter compiles on the host", r.returncode == 0, r.stderr[-2000:])
        if r.returncode == 0:
            run = subprocess.run([str(t / "meter")], capture_output=True, text=True)
            print("     " + "\n     ".join(run.stdout.strip().splitlines()))
            check("the meter's cases pass", run.returncode == 0, run.stdout[-1500:])
else:
    print("note: no swiftc; the meter was not run")
print("PASS: display rate in the log" if ok else "FAILED: display rate in the log")
sys.exit(0 if ok else 1)
