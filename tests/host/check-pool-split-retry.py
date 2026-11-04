#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 le0-VV (the placement fixture: thebcd69/bcd-games#16, submitted with a Signed-off-by under CONTRIBUTING.md)
# Copyright 2026 thebcd69 (BCD) (the changes below)
# Modified by BCD (thebcd69), 2026-10-24: ported into the tree behind the public repository (host check, build step, opt-in switch).
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""pool-split-retry = 1: a second JIT region that lands outside the safe range is requested again (up to 3 attempts); no device.

Based on thebcd69/bcd-games#16 by le0-VV (Minecraft Dungeons II). takeSecondRegion (app/Madeira/StikJITHelper.swift) plugs every
free run below the run it wants, then asks the debugger for the region, which allocates first-fit. When the free runs change in
between (another thread frees a block below), the region lands elsewhere and the pool stays one region (560-630 MB instead of
about 880 MB). With `pool-split-retry = 1` in the game's file the landing is released and the request repeated, the lower runs
plugged afresh, up to three attempts in all; a missing region, a failed release or a third miss end as before. Without the line
there is one attempt: the same code, the same log lines. It is read only by this function, so only a game with pool-split on
(and the line) can reach it.

This does NOT touch the first region's placement loop (the BAD POOL re-rolls: Slay the Spire 2, where the free hole starts inside the
unheld 0x140000000 window); that is a different path.

The production poolRunSize and takeSecondRegion (up to the pool-mid block) are cut out of StikJITHelper.swift and compiled with
swiftc against fake address-space calls that simulate the races. Needs xcrun swiftc (macOS); other hosts check the source only.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'app/Madeira/StikJITHelper.swift').read_text()
ci = (root / '.github/workflows/build-ipa.yml').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(signature):
    a = src.index(signature)
    return src[a:src.index('\n    }\n', a) + 7]


helper = function('private static func poolRunSize(')
take = function('private static func takeSecondRegion(')
take = take[:take.index('        // madeira-bcd pool-mid:')]
take += '        return (base: b, size: size)\n    }\n'

# ---------------------------------------------------------------- source shape
check('the retry is read from the game\'s file or bcd.cfg, once per call, and one attempt is the default',
      take.count('MadeiraConfig.gameValue("pool-split-retry") ?? MadeiraConfig.get("pool-split-retry")') == 1
      and 'contains(retryText) ? 3 : 1' in take)
check('the last miss and a failed release keep the old message and end with nil (the pool stays one region)',
      '"released (kr=%d); the pool stays one region", Int(b), Int(best.base), dkr), level: .error)\n                return nil' in take
      and 'if dkr == KERN_SUCCESS && attempt + 1 < attempts {' in take)
check('the old landing test is unchanged',
      'b < aEnd || b + size > ceiling || (aEnd < exeWindow.base + exeWindow.size && b > exeWindow.base)' in take)
check('the plugs are rebuilt and released inside every attempt', take.index('for attempt in 0..<attempts {') < take.index('var plugs:')
      < take.index('requestRegion(Int(size), region: "B")') < take.index('for (a, sz) in plugs { vm_deallocate(mach_task_self_, a, sz) }'))
check('the first region\'s placement loop (BAD POOL re-rolls) was not touched', src.count('for attempt in 0..<3 {') == 1
      and 'BAD POOL placement 0x%lx (%@) — re-rolling (attempt %d)' in src)
check('build-ipa.yml runs this check', 'python3 tests/host/check-pool-split-retry.py' in ci)
check('the key is in the settings catalog and the generator',
      'ConfigOption(key: "pool-split-retry"' in catalog and '"pool-split-retry"' in (root / 'build/tools/gen-config-catalog.py').read_text())

# ---------------------------------------------------------------- compiled run
prefix = r'''
import Foundation
typealias vm_address_t = UInt
typealias vm_size_t = UInt
let mach_task_self_: Int32 = 0
let KERN_SUCCESS: Int32 = 0
let VM_PROT_NONE: Int32 = 0
func vm_protect(_ task: Int32, _ address: UInt, _ size: UInt, _ setMax: Int32, _ prot: Int32) -> Int32 { 0 }
final class Fixture {
    static var mode = ""
    static var retry: String? = nil
    static var requests = 0
    static var livePlugs = 0
    static var releases = 0
    static var failedRelease = false
    static var notes: [String] = []
    static func reset(_ s: String, retry r: String? = nil) {
        mode = s; retry = r; requests = 0; livePlugs = 0; releases = 0; failedRelease = false; notes = []
    }
}
enum MadeiraConfig {
    static func gameValue(_ key: String) -> String? { key == "pool-split-retry" ? Fixture.retry : nil }
    static func get(_ key: String) -> String? { nil }
}
struct LogStore {
    enum Level { case error, success }
    static let shared = LogStore()
    func log(_ message: String, level: Level = .error) { Fixture.notes.append(message) }
}
func vm_allocate(_ task: Int32, _ address: UnsafeMutablePointer<UInt>, _ size: UInt, _ flags: Int32) -> Int32 {
    if Fixture.mode == "plugRefused" { return 1 }
    Fixture.livePlugs += 1
    return 0
}
@discardableResult func vm_deallocate(_ task: Int32, _ address: UInt, _ size: UInt) -> Int32 {
    if size == 256 << 20 && (address == 0x11d7a4000 || address == 0x181000000) {
        Fixture.releases += 1
        if Fixture.mode == "releaseFailure" { Fixture.failedRelease = true; return 1 }
    } else {
        Fixture.livePlugs -= 1
        precondition(Fixture.livePlugs >= 0)
    }
    return 0
}
struct StikJITHelper {
    static func freeRuns(_ lo: UInt, _ hi: UInt, minSize: UInt) -> [(base: UInt, size: UInt)] {
        if lo != 0x100000000 { return [(0x16fab8000, 261 << 20)] }       // the census above the first region
        return [(0x11d7a4000, 540 << 20)]                                 // the lower run that first-fit would take
    }
    static func requestRegion(_ size: Int, region: String = "?") -> UnsafeMutableRawPointer? {
        Fixture.requests += 1
        if Fixture.mode == "nil" { return nil }
        if Fixture.mode == "alwaysWrong" || Fixture.mode == "releaseFailure" || (Fixture.mode == "race" && Fixture.requests == 1)
            || (Fixture.mode == "raceTwice" && Fixture.requests <= 2) {
            return UnsafeMutableRawPointer(bitPattern: 0x11d7a4000)
        }
        if Fixture.mode == "aboveCeiling" { return UnsafeMutableRawPointer(bitPattern: 0x181000000) }
        return UnsafeMutableRawPointer(bitPattern: 0x16fab8000)
    }
    static var poolMid: (base: vm_address_t, size: vm_address_t)? = nil
'''
suffix = r'''
    static func run() -> (base: UInt, size: UInt)? {
        takeSecondRegion(above: 0x16f000000, want: 272 << 20, pageFit: false, exeWindow: (0x140000000, 128 << 20))
    }
    static func blockedWindow() -> (base: UInt, size: UInt)? {
        takeSecondRegion(above: 0x130000000, want: 272 << 20, pageFit: false, exeWindow: (0x140000000, 128 << 20))
    }
}
func expect(_ cond: Bool, _ what: String) {
    print((cond ? "ok   " : "FAIL ") + what)
    if !cond { exit(1) }
}
let good: (base: UInt, size: UInt) = (0x16fab8000, 256 << 20)
func isGood(_ r: (base: UInt, size: UInt)?) -> Bool { r?.base == good.base && r?.size == good.size }

// a valid placement: one attempt, with or without the line
for retry in [nil, "1", "on", "true", "yes", " 1 ", "0", "off", ""] as [String?] {
    Fixture.reset("success", retry: retry)
    expect(isGood(StikJITHelper.run()) && Fixture.requests == 1 && Fixture.livePlugs == 0 && Fixture.releases == 0,
           "a valid placement takes one attempt and holds no plug (retry = \(retry ?? "unset"))")
}
// the race: the first request lands in the lower run
for retry in ["1", "on", "true", "yes", " 1 "] {
    Fixture.reset("race", retry: retry)
    expect(isGood(StikJITHelper.run()) && Fixture.requests == 2 && Fixture.releases == 1 && Fixture.livePlugs == 0
           && Fixture.notes.contains { $0.contains("attempt 1") },
           "pool-split-retry = \(retry): the misplaced region is released and the second request is placed, no plug is left")
}
for retry in [nil, "0", "off", "", "no", "2"] as [String?] {
    Fixture.reset("race", retry: retry)
    expect(StikJITHelper.run() == nil && Fixture.requests == 1 && Fixture.releases == 1 && Fixture.livePlugs == 0
           && !Fixture.notes.contains { $0.contains("attempt") }
           && Fixture.notes.contains { $0.contains("the second region landed at 0x11d7a4000, not in the run at 0x16fab8000 — released (kr=0); the pool stays one region") },
           "retry \(retry ?? "unset"): one attempt, the old message, the pool stays one region")
}
Fixture.reset("raceTwice", retry: "1")
expect(isGood(StikJITHelper.run()) && Fixture.requests == 3 && Fixture.releases == 2 && Fixture.livePlugs == 0, "two misses and a third request that lands")
Fixture.reset("alwaysWrong", retry: "1")
expect(StikJITHelper.run() == nil && Fixture.requests == 3 && Fixture.releases == 3 && Fixture.livePlugs == 0
       && Fixture.notes.last?.contains("released (kr=0); the pool stays one region") == true, "three misses end as before, every region released")
Fixture.reset("alwaysWrong")
expect(StikJITHelper.run() == nil && Fixture.requests == 1 && Fixture.releases == 1 && Fixture.livePlugs == 0, "without the line: one request")
Fixture.reset("nil", retry: "1")
expect(StikJITHelper.run() == nil && Fixture.requests == 1 && Fixture.livePlugs == 0, "no region from the debugger: no retry, plugs released")
Fixture.reset("releaseFailure", retry: "1")
expect(StikJITHelper.run() == nil && Fixture.requests == 1 && Fixture.failedRelease && Fixture.livePlugs == 0,
       "a release that fails ends the function (no second request on top of a region that is still mapped)")
Fixture.reset("aboveCeiling", retry: "1")
expect(StikJITHelper.run() == nil && Fixture.requests == 3 && Fixture.releases == 3 && Fixture.livePlugs == 0, "a landing above the shared-region ceiling is a miss too")
Fixture.reset("aboveCeiling")
expect(StikJITHelper.run() == nil && Fixture.requests == 1 && Fixture.releases == 1, "... and one attempt without the line")
Fixture.reset("plugRefused", retry: "1")
expect(isGood(StikJITHelper.run()) && Fixture.requests == 1 && Fixture.livePlugs == 0, "plugs the address space refuses are not counted or released")
Fixture.reset("success", retry: "1")
expect(StikJITHelper.blockedWindow() == nil && Fixture.requests == 0, "a gap that would hold the executable window is refused before any request")
print("PASS")
'''

swiftc = shutil.which('xcrun')
if swiftc is None:
    print('skip the compiled run: no xcrun swiftc on this host')
else:
    with tempfile.TemporaryDirectory(prefix='bcd-pool-split-retry-') as td:
        td = Path(td)
        (td / 't.swift').write_text(prefix + helper + take + suffix)
        build = subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(td / 'modules'), '-warnings-as-errors', str(td / 't.swift'),
                                '-o', str(td / 't')], capture_output=True, text=True)
        check('the production poolRunSize and takeSecondRegion compile against the fake address space', build.returncode == 0,
              (build.stdout + build.stderr)[-3000:])
        if build.returncode == 0:
            run = subprocess.run([str(td / 't')], capture_output=True, text=True)
            for line in run.stdout.splitlines():
                if line.startswith('ok   ') or line.startswith('FAIL'):
                    check(line[5:], line.startswith('ok   '))
            check('every placement scenario passes', run.returncode == 0 and 'PASS' in run.stdout, (run.stdout + run.stderr)[-1500:])

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
