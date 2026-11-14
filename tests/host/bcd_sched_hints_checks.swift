// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The checks of tests/host/check-sched-hints.py for app/Madeira/BCDSchedHints.swift (compiled with it; it names no other type).

import Foundation

var failures = 0
func check(_ what: String, _ ok: Bool) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
}

/// `seconds` of samples, four a second, with `cores` fast cores busy at `ghz`. The events in order.
func run(_ c: inout BCDCPUClamp, seconds: Double, cores: Double, ghz: Double, step: Double = 250) -> [BCDCPUClamp.Event] {
    var out: [BCDCPUClamp.Event] = []
    var t = 0.0
    while t < seconds * 1000 - 0.5 {
        if let e = c.step(dtMs: step, fastMs: cores * step, cycles: ghz * 1e6 * cores * step) { out.append(e) }
        t += step
    }
    return out
}

// the key
check("the key: no line, empty and anything unknown are off", BCDSchedHints.mode(nil) == .off && BCDSchedHints.mode("") == .off && BCDSchedHints.mode("0") == .off && BCDSchedHints.mode("fast") == .off)
check("the key: 1 gives the hints, watch only logs", BCDSchedHints.mode("1") == .hints && BCDSchedHints.mode(" on ") == .hints && BCDSchedHints.mode("watch") == .watch && BCDSchedHints.mode("LOG") == .watch)
check("off does nothing; watch logs without a hint; 1 does both", !BCDSchedHints.Mode.off.gives && !BCDSchedHints.Mode.off.watches
      && !BCDSchedHints.Mode.watch.gives && BCDSchedHints.Mode.watch.watches && BCDSchedHints.Mode.hints.gives && BCDSchedHints.Mode.hints.watches)
check("Settings offers the three, and the key's values are the ones the reader knows", BCDSchedHints.choices.map { $0.0 } == ["", "watch", "1"]
      && BCDSchedHints.choices.allSatisfy { BCDSchedHints.mode($0.0).rawValue == $0.0 })

// the start's line
check("the log says what the kernel answered and what the process has now",
      BCDSchedHints.startLine(.hints, latency: 0, throughput: 0, tiers: (0xff0001, 0xfe0001))
      == "[sched] hints given: activity (user-initiated, latency-critical), latency tier taken, throughput tier taken; the process now has 0xff0001 / 0xfe0001; the fast cores' clock is watched ([clamp] lines)")
check("a refusal is said with the kernel's number", BCDSchedHints.startLine(.hints, latency: 4, throughput: 0, tiers: nil).contains("latency tier refused (4), throughput tier taken;"))
check("watch says that no hint was given", BCDSchedHints.startLine(.watch, latency: 0, throughput: 0, tiers: nil).contains("no hint given"))

// the rule
var c = BCDCPUClamp()
check("a busy game at full clock: nothing, however long", run(&c, seconds: 600, cores: 2.5, ghz: 3.9).isEmpty && !c.held && c.count == 0)
check("an idle game at a low clock: nothing (idle cores clock down)", run(&c, seconds: 600, cores: 0.3, ghz: 0.9).isEmpty && !c.held)
var events = run(&c, seconds: 2.75, cores: 1.5, ghz: 1.09)
check("2.75 s under load at 1.09 GHz: not yet", events.isEmpty && !c.held)
events = run(&c, seconds: 0.25, cores: 1.5, ghz: 1.09)
check("3 s: held back, said once, with the clock", events == [.held(ghz: 1.09)] && c.held && c.count == 1 && abs(c.ghz - 1.09) < 1e-9)
check("it stays held through light moments and through clocks in between, and is not said again",
      run(&c, seconds: 30, cores: 0.2, ghz: 3.0).isEmpty && run(&c, seconds: 30, cores: 1.5, ghz: 1.5).isEmpty && run(&c, seconds: 30, cores: 1.5, ghz: 1.1).isEmpty && c.held)
events = run(&c, seconds: 1.75, cores: 1.5, ghz: 2.4)
check("1.75 s at 2.4 GHz: still held", events.isEmpty && c.held)
events = run(&c, seconds: 0.25, cores: 1.5, ghz: 2.4)
check("2 s: free again, with how long it was held (the 90 s of the three stretches and these 2 s)", events.count == 1 && !c.held && {
    if case .free(let ghz, let heldMs) = events[0] { return abs(ghz - 2.4) < 1e-9 && abs(heldMs - 92000) < 1 } else { return false } }())
check("the session's total and count", c.count == 1 && abs(c.totalMs - 92000) < 1 && c.endLine() == "[clamp] session end: the fast cores were held back 1 time, 92 s in all")
var d = BCDCPUClamp()
_ = run(&d, seconds: 2, cores: 1, ghz: 1.0); _ = run(&d, seconds: 0.25, cores: 1, ghz: 2.5)
check("a sample at a high clock in between starts the three seconds again", run(&d, seconds: 2.75, cores: 1, ghz: 1.0).isEmpty && run(&d, seconds: 0.25, cores: 1, ghz: 1.0) == [.held(ghz: 1.0)])
_ = run(&d, seconds: 1.5, cores: 1, ghz: 2.5); _ = run(&d, seconds: 0.25, cores: 1, ghz: 1.2)
check("a sample at a low clock in between starts the two seconds again", run(&d, seconds: 1.75, cores: 1, ghz: 2.5).isEmpty && d.held && run(&d, seconds: 0.25, cores: 1, ghz: 2.5).count == 1 && !d.held)
var e = BCDCPUClamp()
check("exactly the limits count: half a core, 1.3 GHz", run(&e, seconds: 3, cores: 0.5, ghz: 1.3) == [.held(ghz: 1.3)])
check("exactly 1.8 GHz frees", run(&e, seconds: 2, cores: 0.5, ghz: 1.8).count == 1 && !e.held)
var f = BCDCPUClamp()
check("one-second samples (the app's timer) work the same: the third says it", run(&f, seconds: 2, cores: 1, ghz: 1.0, step: 1000).isEmpty && run(&f, seconds: 1, cores: 1, ghz: 1.0, step: 1000) == [.held(ghz: 1.0)])
var g = BCDCPUClamp()
check("numbers that are no numbers, no time and counters that ran backwards do nothing",
      g.step(dtMs: 0, fastMs: 100, cycles: 1e8) == nil && g.step(dtMs: -5, fastMs: 100, cycles: 1e8) == nil && g.step(dtMs: .nan, fastMs: 100, cycles: 1e8) == nil
      && g.step(dtMs: 1000, fastMs: .infinity, cycles: 1e8) == nil && g.step(dtMs: 1000, fastMs: 800, cycles: -1) == nil && g.step(dtMs: 1000, fastMs: -800, cycles: 1e8) == nil && g == BCDCPUClamp())
check("a session that was never held back has no end line", BCDCPUClamp().endLine() == nil)
check("the lines", BCDCPUClamp.line(.held(ghz: 1.094), after: 131.4, thermal: "warm") == "[clamp] the fast cores run at 1.09 GHz under load, 131 s after the start (heat: warm): iOS is holding them back"
      && BCDCPUClamp.line(.free(ghz: 2.41, heldMs: 92000), after: 300, thermal: "cool") == "[clamp] the fast cores run free again at 2.41 GHz after 92 s held back (heat: cool)")

print(failures == 0 ? "ALL PASS" : "FAILED: \(failures)")
exit(failures == 0 ? 0 : 1)
