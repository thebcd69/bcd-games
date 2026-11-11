#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The JIT memory request that the external debugger never answers (night round 2026-11-11, M7: A2 S1, A4 S3, G10 S10, A5 S8).

The pool request is a BRK #0xf00d that StikDebug (BCD's script, madeira-jit.js) answers; when it does not, the whole process stays stopped inside
the request, so nothing can be drawn or logged from inside it (25 of 769 archive starts: 40GTCK, K1S139, MWSY45, XBQFH4, 1Z6JQQ, YPH36Q, QA2DT6,
WAEQRS, 9B60CH = 5VGNK5, E07N6M = JVFX4W, W7Z1Y7, RHF21M ...). The cause is StikDebug's; what BCD can do is on either side of the request:

1. Before it, on the risky path only (an external debugger whose attach is 25 s old or older, or a phone in a serious heat state: StikDebug 5.8 %
   against 1.3 % for the built-in helper, an attach older than 30 s 15 % against 2 %, a serious heat state 22.7 % with StikDebug), the starting
   screen says what BCD waits for (JITRequestRules.waitWords, LibraryModel.poolWait, BCDSessionLive's "pool-wait" note).
2. Around it, always: "[jit-pool] request A: ..." before and "answered after N ms" after (the file has the first line even when the process
   never comes back), "[jit-age] ... method= thermal=", the trail record's context (method, attach age, source, LiveContainer, iOS, footprint), and a
   main-queue watch that says "[jit-brk-wait]" and puts JITRequestRules.stalledWords on the starting screen when 30 s passed (only if the main
   thread still runs: G10 saw a UI line 20 s into a stalled request, A2/A4/A5 say nothing runs; the line tells which).
3. The player's words (MemoryRights.poolStopWords) name the two risk factors the data shows.

1. Swift, JITRequestRules compiled and driven. 2. The trail with a context compiled and driven. 3. Source pins and the preview's copies.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
failures = 0


def require(condition, label, detail=''):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label + (('\n      ' + str(detail)[:1500]) if (detail and not condition) else ''))
    if not condition:
        failures += 1


def block(text, header_text):
    p = text.index(header_text)
    a = text.index('{', p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == '{') - (text[b] == '}')
        b += 1
    return text[p:b]


helper = (app / 'StikJITHelper.swift').read_text(encoding='utf-8')
rec = (app / 'BCDSessionRecorderLive.swift').read_text(encoding='utf-8')
library = (app / 'Library.swift').read_text(encoding='utf-8')
live = (app / 'BCDSessionLive.swift').read_text(encoding='utf-8')
content = (app / 'ContentView.swift').read_text(encoding='utf-8')
rights = (app / 'MemoryRights.swift').read_text(encoding='utf-8')
preview = (root / 'app/BCDPreview/BCDPreviewSession.swift').read_text(encoding='utf-8')
workflow = (root / '.github/workflows/build-ipa.yml').read_text(encoding='utf-8')
rules = block(helper, 'enum JITRequestRules {')
trail = rec[rec.index('enum BCDBrkTrail {'):]
trail = trail[:trail.index('\n}\n') + 3]

# ------------------------------------------------------------------ 1. Swift: the rules
swift = r'''
import Foundation
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
typealias J = JITRequestRules

@main struct Checks {
    static func main() {
        // ---- the risky path
        require(J.risk(external: false, attachAgeSeconds: 70, thermalSerious: true) == nil, "the built-in helper is never on the risky path (0 of 28 hot starts failed)")
        require(J.risk(external: true, attachAgeSeconds: 5, thermalSerious: false) == nil && J.risk(external: true, attachAgeSeconds: 24, thermalSerious: false) == nil,
                "StikDebug, a fresh attach, a cool phone: nothing (93 % of the starts)")
        require(J.risk(external: true, attachAgeSeconds: 25, thermalSerious: false) == J.Risk(ageSeconds: 25, hot: false)
                && J.risk(external: true, attachAgeSeconds: 70, thermalSerious: false) == J.Risk(ageSeconds: 70, hot: false), "1Z6JQQ: a 70 s old attach is risky")
        require(J.risk(external: true, attachAgeSeconds: 3, thermalSerious: true) == J.Risk(ageSeconds: 3, hot: true), "a fresh attach on a hot phone is risky")
        require(J.risk(external: true, attachAgeSeconds: nil, thermalSerious: false) == nil && J.risk(external: true, attachAgeSeconds: nil, thermalSerious: true) != nil,
                "an unknown age counts as not old")
        require(J.riskAgeSeconds == 25 && J.stallSeconds == 30, "25 s, 30 s")
        // ---- the words and the lines
        require(J.waitWords == "Waiting for StikDebug to hand over JIT memory. The screen can freeze for up to 30 seconds. Don't close BCD.", "the line before the request")
        require(J.stalledWords.hasPrefix("BCD has waited 30 seconds for JIT memory and StikDebug has not answered. Close BCD") && J.stalledWords.hasSuffix("press Play again straight away."),
                "the line after 30 s")
        require(J.ageLineTail(method: "stikdebug", thermal: 2) == " method=stikdebug thermal=2", "[jit-age] gets the method and the heat state")
        require(J.requestLine(region: "A", sizeMB: 592, attempt: 0, ageSeconds: 55, source: "at-start", method: "stikdebug")
                == "[jit-pool] request A: size=592MB attempt=0 attach-age=55s source=at-start method=stikdebug", "the line before a request (40GTCK's numbers)")
        require(J.requestLine(region: "C", sizeMB: 64, attempt: nil, ageSeconds: nil, source: nil, method: "?")
                == "[jit-pool] request C: size=64MB attempt=- attach-age=?s source=? method=?", "unknowns are question marks")
        require(J.trailContext(method: "stikdebug", ageSeconds: 13, source: "enable-jit", liveContainer: true, os: "27.2.0", footprintMB: 310)
                == "method=stikdebug attach-age=13s source=enable-jit lc=1 ios=27.2.0 foot=310MB", "the trail record's context")
        exit(failures == 0 ? 0 : 1)
    }
}
'''

# ------------------------------------------------------------------ 2. the trail with a context
trail_driver = r'''
import Foundation
func os_proc_available_memory() -> Int { 3 << 30 }
''' + trail + r'''
let dir = CommandLine.arguments[1]
BCDBrkTrail.url = URL(fileURLWithPath: dir + "/jit-brk-last.txt")
BCDBrkTrail.begin(site: 864, size: 592 << 20, attempt: 0, region: "A")
print("plain " + (BCDBrkTrail.takeUnfinished() ?? "nil"))
BCDBrkTrail.context = { "method=stikdebug attach-age=55s source=at-start lc=1 ios=27.2.0 foot=310MB" }
BCDBrkTrail.begin(site: 864, size: 592 << 20, attempt: 0, region: "A")
print("context " + (BCDBrkTrail.takeUnfinished() ?? "nil"))
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'rules.swift').write_text('import Foundation\n' + rules + '\n')
    (tmp / 'main.swift').write_text(swift)
    r = subprocess.run([SWIFTC, '-parse-as-library', '-o', str(tmp / 'rules'), str(tmp / 'rules.swift'), str(tmp / 'main.swift')],
                       capture_output=True, text=True)
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the Swift rules compile')
    else:
        r = subprocess.run([str(tmp / 'rules')], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-2000:])
            failures += 1
    (tmp / 'trail').mkdir()
    (tmp / 'trail' / 'main.swift').write_text(trail_driver)
    r = subprocess.run([SWIFTC, '-o', str(tmp / 'trailbin'), str(tmp / 'trail' / 'main.swift')], capture_output=True, text=True)
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the trail compiles on the host')
    else:
        out = subprocess.run([str(tmp / 'trailbin'), str(tmp)], capture_output=True, text=True).stdout.splitlines()
        got = dict((l.split(' ', 1) + [''])[:2] for l in out)
        plain = got.get('plain', '')
        ctx = got.get('context', '')
        require(plain.startswith('begin site=L864 region=A size=592MB attempt=0 uptime=') and plain.endswith(' avail=3072MB'),
                'without a context the record is as before (the older readers and check-jit-brk-trail.py)', out)
        require(ctx.startswith('begin site=L864 region=A size=592MB attempt=0 uptime=') and ' avail=3072MB method=stikdebug attach-age=55s source=at-start lc=1 ios=27.2.0 foot=310MB' in ctx
                and ctx.endswith('foot=310MB') and ctx.count('\n') == 0, 'with a context its fields come last, after avail=', out)

# ------------------------------------------------------------------ 3. source
req = block(helper, 'private static func requestRegion(_ size: Int, attempt: Int? = nil, region: String, site: Int = #line) -> UnsafeMutableRawPointer? {')
dbg = req[req.index('guard noDebugger else {'):]
require(dbg.index('let watch = StikJITHelper.requestWatchBegin(region: region, size: size, attempt: attempt)') < dbg.index('BCDBrkTrail.begin(site: site, size: size, attempt: attempt, region: region)')
        < dbg.index('return jit26_prepare_region(nil, size)')
        and 'defer { StikJITHelper.requestWatchEnd(watch) }' in dbg and 'defer { BCDBrkTrail.end() }' in dbg,
        'the line and the watch come before the trail record and the BRK, and end after them (defers run in reverse)')
require('LogStore.shared.log(JITRequestRules.requestLine(region: region' in block(helper, 'static func requestWatchBegin(region: String, size: Int, attempt: Int?) -> RequestWatch {'),
        'the line is written in the file before the request goes out')
watch = block(helper, 'static func requestWatchBegin(region: String, size: Int, attempt: Int?) -> RequestWatch {')
require('DispatchQueue.main.asyncAfter(deadline: .now() + JITRequestRules.stallSeconds, execute: item)' in watch and '[jit-brk-wait]' in watch
        and 'LibraryModel.shared.poolWait = JITRequestRules.stalledWords' in watch and 'watch.fired = true' in watch,
        'the main-queue watch: 30 s, the log line, the starting screen, and a flag that it fired')
end = block(helper, 'static func requestWatchEnd(_ watch: RequestWatch) {')
require('watch.item?.cancel()' in end and 'answered after' in end and 'poolWait = captionUp ? JITRequestRules.waitWords : nil' in end,
        'an answered request cancels the watch, says so, and takes the stalled note off (the caption of the risky path stays)')
ann = block(helper, 'static func announceRiskyRequest() -> Bool {')
require('guard isDebuggerTraced() else { return false }' in ann and 'JITRequestRules.risk(external: requestMethod != "builtin" || age?.source == "at-start"' in ann
        and ann.index('LibraryModel.shared.poolWait = JITRequestRules.waitWords') < ann.index('Thread.sleep(forTimeInterval: 0.4)') and '[jit-risk]' in ann,
        'the caption goes up first, then SwiftUI gets 0.4 s, only for an attached external debugger on the risky path; the line [jit-risk] says why')
require('[jit-age] attach age=\\(age.seconds)s source=\\(age.source)" + JITRequestRules.ageLineTail(method: requestMethod, thermal: ProcessInfo.processInfo.thermalState.rawValue)' in helper,
        '[jit-age] keeps its first words (the archive scripts and the rules read them) and gets method= thermal=')
require('static func brkTrailContext() -> String {' in helper and 'BCDBrkTrail.context = { StikJITHelper.brkTrailContext() }' in rec
        and rec.index('BCDBrkTrail.context = {') < rec.index('if let last = BCDBrkTrail.takeUnfinished()'), 'the trail gets its context at app start, before the next start reads the old record')
pool = content[content.index('winiosPhase("pool-alloc-begin")'):]
pool = pool[:pool.index('let elapsed = CFAbsoluteTimeGetCurrent() - t0')]
require('let requestCaption = StikJITHelper.announceRiskyRequest()' in pool and 'if requestCaption { StikJITHelper.clearRequestCaption() }' in pool
        and pool.index('announceRiskyRequest()') < pool.index('let t0 = CFAbsoluteTimeGetCurrent()') < pool.index('StikJITHelper.allocatePool(') < pool.index('clearRequestCaption()'),
        'ContentView: the caption before the request (not counted in "BRK suspension lasted"), cleared when it returns')
require('StikJITHelper.noteRequestMethod(jitCoordinator.resolvedMethod)' in content
        and content.index('StikJITHelper.noteRequestMethod(') < content.index('logStore.uiPaused = true'), 'the method is read on the main thread, before the sequence goes to a background queue')
require('@Published var poolWait: String?' in library and 'poolWait = nil   // night round 2026-11-11 (M7)' in library and 'desktopNotices = []; poolWait = nil' in library,
        'LibraryModel keeps the line, a start begins and ends without one')
require('model.$poolWait.map { _ in () }.eraseToAnyPublisher()' in live and 'BCDStartNote(id: "pool-wait", text: words, tone: .warm)' in live,
        'the starting screen shows it as a warm note and refreshes when it changes')


def swift_words(src, marker):
    rest = src[src.index(marker) + len(marker):]
    out, first = [], True
    for line in rest.splitlines():
        t = line.strip()
        if not first and not t.startswith('+'):
            break
        out += re.findall(r'"((?:[^"\\]|\\.)*)"', t)
        first = False
    return ''.join(out)


require(swift_words(rules, 'static let waitWords = ') == swift_words(preview, 'static let poolWaitWords = ')
        and swift_words(rules, 'static let stalledWords = ') == swift_words(preview, 'static let poolStalledWords = ')
        and 'case "starting-poolwait", "starting-poolstalled":' in preview,
        "the preview app's two lines (starting-poolwait, starting-poolstalled) say exactly what JITRequestRules says")

# ------------------------------------------------------------------ the player's words
words = swift_words(rights, 'static let poolStopWords = ')
require(words.startswith('Last time BCD stopped while JIT was being set up for the game: its JIT memory request did not come back. ')
        and 'StikDebug stops answering far more often when the phone is hot' in words and 'The built-in way answers these requests faster than StikDebug.' in words
        and 'press Play soon after JIT turns on' in words,
        'MemoryRights.poolStopWords keeps its first sentence and its advice and names the heat state')
require('python3 tests/host/check-jit-pool-wait.py' in workflow, 'CI runs this check')

print(f'\n{"PASS" if failures == 0 else "FAIL"}: jit pool wait ({failures} failure(s))')
sys.exit(1 if failures else 0)
