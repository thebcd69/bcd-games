#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The JIT pool's retry after a placement in the guest window asks for less (night round 2026-11-11, G9 S2a).

Slay the Spire 2 on an iPad16,3 (78BPN7, build 634): the executable window was held, the census found one hole, 0x148000000+560 MB (the early
placeholder BCD had just released, no slack), the pool was sized to it, and a stray mapping of a few pages that appeared between the census and
the debugger's request, 0.4 s later, left the hole a little short of 560 MB. The debugger's first-fit then put the region in the guest window
([0x7000000000, 0x8000000000), refused), and the two retries asked for the SAME size and fell to the guest window again: "BAD POOL: no valid
placement after retries", the game does not start. The archive has 21 logs with an in-run BAD POOL and no start that a same-size retry
recovered (the three logs that look recovered carry the previous run's tail), so a retry may ask for less: after a guest-window placement the next
try takes 16 MB (the pool's granule) off the size requested at first, only where the pool was sized to the census's hole (within 32 MB of it),
never below the census's own minimum of 256 MB, never for a pool pair or a reservation, only with the window held.

1. Swift, StikJITHelper.swift PoolRetryRules, compiled and driven: the numbers of 78BPN7, a pool with slack, the floor, the attempt.
2. The loop modelled with a first-fit that cannot take the exact size: the old retries never place, the new ones place at the second try.
3. Source: the loop's size per try, the placed size becomes the pool's size, the conditions, the census's largest hole is the one kept.
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
rules = block(helper, 'enum PoolRetryRules {')

swift = r'''
import Foundation
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
typealias R = PoolRetryRules
let MB = 1 << 20

/// The debugger's first-fit as 78BPN7 met it: one hole of `holeMB` (less `strayKB` a stray mapping took), then the guest window.
func firstFit(size: Int, holeMB: Int, strayKB: Int) -> Int {
    let free = holeMB * MB - strayKB * 1024
    return size <= free ? 0x148000000 : 0x7008400000
}
/// allocatePool's loop (StikJITHelper.swift), the part that picks the size: nil when no try is placed.
func loop(poolSize: Int, largest: Int, holeMB: Int, strayKB: Int, stepDown: Bool) -> (placed: Int?, tries: [Int]) {
    var attemptSize = poolSize
    var tries: [Int] = []
    for attempt in 0..<3 {
        tries.append(attemptSize)
        let a = firstFit(size: attemptSize, holeMB: holeMB, strayKB: strayKB)
        let inGuestWindow = a + attemptSize > 0x7000000000 && a < 0x8000000000
        if !inGuestWindow { return (attemptSize, tries) }
        if stepDown, attempt < 2, let next = R.stepDown(requested: poolSize, tried: attemptSize, largestHole: largest, attempt: attempt) { attemptSize = next }
    }
    return (nil, tries)
}

@main struct Checks {
    static func main() {
        // ---- the numbers of 78BPN7: a 560 MB pool in a 560 MB hole
        require(R.stepDown(requested: 560 * MB, tried: 560 * MB, largestHole: 560 * MB, attempt: 0) == 544 * MB, "78BPN7: the second try asks for 544 MB")
        require(R.stepDown(requested: 560 * MB, tried: 544 * MB, largestHole: 560 * MB, attempt: 1) == 528 * MB, "... the third for 528 MB (32 MB off the first request)")
        require(R.step == 16 << 20 && R.exactFitSlack == 32 << 20 && R.minimum == 256 << 20, "16 MB step, 32 MB slack, 256 MB floor")
        // ---- where it does nothing (the old behaviour stays)
        require(R.stepDown(requested: 560 * MB, tried: 560 * MB, largestHole: 700 * MB, attempt: 0) == nil,
                "a pool with room to spare (the hole is 140 MB larger): a stray mapping is not the reason, the same size is asked again")
        require(R.stepDown(requested: 560 * MB, tried: 560 * MB, largestHole: 591 * MB, attempt: 0) == 544 * MB
                && R.stepDown(requested: 560 * MB, tried: 560 * MB, largestHole: 592 * MB, attempt: 0) == nil, "the slack is under 32 MB")
        require(R.stepDown(requested: 264 * MB, tried: 264 * MB, largestHole: 264 * MB, attempt: 0) == nil
                && R.stepDown(requested: 272 * MB, tried: 272 * MB, largestHole: 272 * MB, attempt: 0) == 256 * MB
                && R.stepDown(requested: 272 * MB, tried: 256 * MB, largestHole: 272 * MB, attempt: 1) == nil, "never under 256 MB")
        require(R.stepDown(requested: 560 * MB, tried: 560 * MB, largestHole: 128 * MB, attempt: 0) == nil, "a census whose largest hole is under 256 MB: nothing to step into")
        require(R.stepDown(requested: 560 * MB, tried: 528 * MB, largestHole: 560 * MB, attempt: 0) == nil, "never a larger size than the one just tried")

        // ---- the loop: the old retries never place, the new ones place at the second try
        let old = loop(poolSize: 560 * MB, largest: 560 * MB, holeMB: 560, strayKB: 16, stepDown: false)
        require(old.placed == nil && old.tries == [560 * MB, 560 * MB, 560 * MB], "78BPN7 as it was: three tries of 560 MB, none placed")
        let new = loop(poolSize: 560 * MB, largest: 560 * MB, holeMB: 560, strayKB: 16, stepDown: true)
        require(new.placed == 544 * MB && new.tries == [560 * MB, 544 * MB], "with the step-down the second try (544 MB) is placed in the hole")
        let big = loop(poolSize: 560 * MB, largest: 560 * MB, holeMB: 560, strayKB: 20 * 1024, stepDown: true)
        require(big.placed == 528 * MB && big.tries == [560 * MB, 544 * MB, 528 * MB], "a stray mapping of 20 MB: the third try (528 MB) is placed")
        let healthy = loop(poolSize: 560 * MB, largest: 560 * MB, holeMB: 560, strayKB: 0, stepDown: true)
        require(healthy.placed == 560 * MB && healthy.tries == [560 * MB], "a start that works today asks once, for the same size: nothing changes")
        exit(failures == 0 ? 0 : 1)
    }
}
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

# ------------------------------------------------------------------ source: the loop in allocatePool
alloc = block(helper, 'static func allocatePool(poolSize requestedPoolSize: Int = 128 * 1024 * 1024)')
loop = alloc[alloc.index('        var attemptSize = poolSize\n'):alloc.index('        // ml1040: the plugs existed only to steer first-fit')]
require('guard let p = requestRegion(attemptSize, attempt: attempt, region: "A")' in loop and 'requestRegion(poolSize' not in loop,
        'every try asks for attemptSize (the first one for the size the census chose)')
require('let inGuestWindow = a + attemptSize > guestLo && a < guestHi' in loop
        and 'overlapsExeWindow(vm_address_t(a), vm_address_t(attemptSize))' in loop
        and 'plugs.append((vm_address_t(a), vm_size_t(attemptSize)))' in loop, 'the checks and the pin use the size that was tried')
require('rxPtrOpt = p\n                poolSize = attemptSize' in loop, 'the size that was placed becomes the pool\'s size (the RW alias, the log and the result use it)')
require('if inGuestWindow, windowHeld, pairA == nil, reservePlan == nil, attempt < 2,' in loop
        and 'PoolRetryRules.stepDown(requested: poolSize, tried: attemptSize, largestHole: Int(censusLargest), attempt: attempt)' in loop,
        'only after a guest-window placement, with the window held, for a single-region pool outside a reservation')
require('censusLargest = largest' in alloc and alloc.count('var censusLargest: vm_address_t = 0') == 1
        and alloc.index('var censusLargest') < alloc.index('censusLargest = largest') < alloc.index('PoolRetryRules.stepDown('),
        "the census's largest hole is kept for the loop")
require('[pool-retry] ml1036:' in loop, 'a step-down is logged')
workflow = (root / '.github/workflows/build-ipa.yml').read_text(encoding='utf-8')
require('python3 tests/host/check-pool-retry.py' in workflow, 'CI runs this check')

print(f'\n{"PASS" if failures == 0 else "FAIL"}: pool retry ({failures} failure(s))')
sys.exit(1 if failures else 0)
