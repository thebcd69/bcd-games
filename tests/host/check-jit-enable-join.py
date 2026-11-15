#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Built-in JIT: an Enable while another Enable of this app runs joins it; no second helper starts (app/Madeira/JITBuiltInHost.swift).

Log R4Z1MY (build 630, iPad13,8, 2026-11-06): two Enable JIT requests (10:01:14 and 10:01:24) each started a helper at 10:01:32; both
prepared the Developer Disk Image at once, one failed "cryptex already exists (com.apple.security.cryptex: 17)" and the other attached.
Two helpers debugging BCD's own pid are never right (U1 counter-review F1, upstream merge of 2026-11-14).

Compiles the production MadeiraBuiltInJIT and HelperRequest (JITBuiltInHost.swift) and JITBuiltInMessages.swift with a stub log and a
fake NSExtension (the @objc selectors the helper host calls), and checks:
  - a second Enable while one runs starts no second helper; it gets the running request's start (at once when that one has started,
    or with it) and the same result; a third joins too;
  - after the Enable ended, a new Enable starts a new helper; Prepare and Reset are never joined;
  - a failed start (no request identifier) reaches every joined caller as the same failure, and none is told "started";
  - the rule `joins`: an Enable joins a running Enable younger than 90 s or one with a debugger attached; an older one with no
    debugger attached is left alone (a new helper starts, as before); nothing else joins;
  - send() ends in start(), the only place a HelperRequest is made; CI runs this check.
Needs swiftc (macOS) and python3.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
host = (app / 'JITBuiltInHost.swift').read_text()
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc')
failures = 0


def require(cond, what):
    global failures
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        failures += 1


def block(source, header):
    start = source.index(header)
    opening = source.index('{\n', start)   # the body's brace (a default argument like `= {}` comes before it)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


send = block(host, '    static func send(_ request: MadeiraJITRequest,')
require(send.rstrip().endswith('start(request.operation, data: data, extension: found, identifier: identifier, started: started, completion: completion)\n    }')
        and 'HelperRequest(' not in send, 'send() ends in start() and makes no HelperRequest itself')
require(host.count('HelperRequest(extension:') == 1 and 'static func start(' in host, 'start() is the only place a HelperRequest is made')

stubs = '''
import Foundation
final class LogStore {
    static let shared = LogStore()
    enum Level { case info, success, error, debug }
    var lines: [String] = []
    func log(_ message: String, level: Level = .info) { lines.append(message) }
}
var fakeTraced = false
func isDebuggerTraced() -> Bool { fakeTraced }
''' + block((app / 'BCDJIT.swift').read_text(), 'enum BCDLCHelper: String, Hashable {') + '''
enum BCDJITRules { static func lcHelperText(_ s: BCDLCHelper) -> String { "" } }

/// The NSExtension calls JITBuiltInHost.swift makes (NSExtensionShim), recorded.
final class FakeExtension: NSObject {
    static var begins = 0
    var startFails = false
    var done: ((NSUUID?, [Any]?) -> Void)?
    @objc(beginExtensionRequestWithInputItems:completion:)
    func beginRequest(_ items: [Any], completion: @escaping (NSUUID?) -> Void) {
        FakeExtension.begins += 1
        let fails = startFails
        DispatchQueue.main.async { completion(fails ? nil : NSUUID()) }
    }
    @objc(pidForRequestIdentifier:) func pid(_ identifier: NSUUID) -> Int32 { 4242 }
    @objc(setRequestCompletionBlock:) func setDone(_ block: @escaping (NSUUID?, [Any]?) -> Void) { done = block }
    @objc(setRequestCancellationBlock:) func setCancel(_ block: @escaping (NSUUID?, NSError?) -> Void) {}
    @objc(setRequestInterruptionBlock:) func setInterrupt(_ block: @escaping (NSUUID?) -> Void) {}
    func finish(_ success: Bool, _ message: String) {
        let item = NSExtensionItem()
        let response = MadeiraJITRequest.Response(success: success, message: message, txmPresent: nil)
        item.userInfo = [MadeiraJITRequest.Response.itemKey: try! JSONEncoder().encode(response)]
        done?(nil, [item])
    }
}
'''

main = r'''
import Foundation

@main struct Check {
    @MainActor static func main() {
        var failed = 0
        func expect(_ ok: Bool, _ label: String) { print((ok ? "PASS: " : "FAIL: ") + label); if !ok { failed += 1 } }
        func pump() { for _ in 0..<5 { RunLoop.main.run(until: Date().addingTimeInterval(0.02)) } }
        typealias R = Result<MadeiraJITRequest.Response, Error>
        let data = Data("{}".utf8)
        func message(_ r: R?) -> String? { if case .success(let v)? = r { return v.message }; return nil }

        // 1. Two Enables, the second before the first has started; then a third after it started.
        var starts = [0, 0, 0], results: [R?] = [nil, nil, nil]
        let a = FakeExtension(), b = FakeExtension(), c = FakeExtension()
        MadeiraBuiltInJIT.start(.enable, data: data, extension: a, identifier: "h", started: { starts[0] += 1 }, completion: { results[0] = $0 })
        MadeiraBuiltInJIT.start(.enable, data: data, extension: b, identifier: "h", started: { starts[1] += 1 }, completion: { results[1] = $0 })
        expect(FakeExtension.begins == 1, "a second Enable while one runs starts no second helper")
        pump()
        expect(starts == [1, 1, 0], "the joined Enable is told 'started' with the running one, once")
        MadeiraBuiltInJIT.start(.enable, data: data, extension: c, identifier: "h", started: { starts[2] += 1 }, completion: { results[2] = $0 })
        expect(FakeExtension.begins == 1 && starts[2] == 1, "a third Enable after the start joins too and is told 'started' at once")
        expect(LogStore.shared.lines.filter { $0.contains("this request joins it (one helper, one debugger)") }.count == 2, "each join is logged")
        a.finish(true, "attached and detached")
        pump()
        expect(results.map(message) == ["attached and detached", "attached and detached", "attached and detached"],
               "every joined Enable gets the running request's result")

        // 2. After it ended, a new Enable starts a new helper; Prepare and Reset are never joined.
        var r2: R?, r3: R?, r4: R?
        let d = FakeExtension(), e = FakeExtension(), f = FakeExtension()
        MadeiraBuiltInJIT.start(.enable, data: data, extension: d, identifier: "h", started: {}, completion: { r2 = $0 })
        expect(FakeExtension.begins == 2, "after the Enable ended, a new Enable starts a new helper")
        MadeiraBuiltInJIT.start(.prepare, data: data, extension: e, identifier: "h", started: {}, completion: { r3 = $0 })
        MadeiraBuiltInJIT.start(.resetDDI, data: data, extension: f, identifier: "h", started: {}, completion: { r4 = $0 })
        expect(FakeExtension.begins == 4, "Prepare and Reset while an Enable runs start their own helpers")
        pump()
        e.finish(true, "prepared"); f.finish(true, "reset"); d.finish(false, "enable failed")
        pump()
        expect(message(r3) == "prepared" && message(r4) == "reset" && message(r2) == "enable failed", "each of them gets its own result")

        // 3. A start that fails reaches every joined caller as the same failure; nobody is told "started".
        var failStarts = 0, failures: [R?] = [nil, nil]
        let g = FakeExtension(); g.startFails = true
        MadeiraBuiltInJIT.start(.enable, data: data, extension: g, identifier: "h", started: { failStarts += 1 }, completion: { failures[0] = $0 })
        MadeiraBuiltInJIT.start(.enable, data: data, extension: FakeExtension(), identifier: "h", started: { failStarts += 1 }, completion: { failures[1] = $0 })
        expect(FakeExtension.begins == 5, "the second Enable joined the one that is starting")
        pump()
        let texts = failures.map { r -> String? in if case .failure(let error)? = r { return error.localizedDescription }; return nil }
        expect(failStarts == 0 && texts[0] != nil && texts[0] == texts[1], "a failed start reaches both callers as the same failure")

        // 4. The rule.
        typealias J = MadeiraBuiltInJIT
        expect(J.joins(.enable, runningAge: 10, traced: false) && J.joins(.enable, runningAge: 89, traced: false),
               "an Enable joins a running Enable younger than 90 s")
        expect(!J.joins(.enable, runningAge: 120, traced: false) && J.joins(.enable, runningAge: 120, traced: true),
               "an older one joins only while a debugger is attached; otherwise a new helper starts, as before")
        expect(!J.joins(.enable, runningAge: nil, traced: true) && !J.joins(.prepare, runningAge: 1, traced: true)
               && !J.joins(.resetDDI, runningAge: 1, traced: true), "nothing else joins")
        exit(failed == 0 ? 0 : 1)
    }
}
'''

if not SWIFTC:
    print('SKIP: no swiftc')
    sys.exit(0)
with tempfile.TemporaryDirectory(prefix='bcd-jit-enable-join-') as tmp:
    tmp = Path(tmp)
    (tmp / 'stubs.swift').write_text(stubs)
    (tmp / 'main.swift').write_text(main)
    exe = tmp / 'check'
    build = subprocess.run([SWIFTC, '-parse-as-library', '-swift-version', '5', '-o', str(exe), str(app / 'JITBuiltInHost.swift'),
                            str(app / 'JITBuiltInMessages.swift'), str(tmp / 'stubs.swift'), str(tmp / 'main.swift')],
                           capture_output=True, text=True)
    require(build.returncode == 0, 'the production JIT helper host compiles with the stubs' + ('' if build.returncode == 0 else '\n' + build.stderr[-3000:]))
    if build.returncode == 0:
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
        print(run.stdout, end='')
        require(run.returncode == 0, 'every runtime check passed' + ('' if run.returncode == 0 else '\n' + run.stderr[-2000:]))

wf = (root / '.github/workflows/build-ipa.yml').read_text()
require('python3 tests/host/check-jit-enable-join.py' in wf, 'CI runs this check')
print('PASS' if not failures else f'FAILED ({failures})')
sys.exit(1 if failures else 0)
