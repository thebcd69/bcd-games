#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The log scanner's pair filter (build 657; app/Madeira/AutoTuneCore.swift, AutoTuneLogScanner).

Auto-tune reads the last session's log when the next one starts: every line against every signal of every rule (about 1,600
signals, each a memmem). The Last of Us Part I's logs of build 656 were 17 to 51 MB (up to 357,000 lines), and the scan ran for
about two minutes at 70 to 100 % of an efficiency core while the game was starting and loading ("[xp-names] ... uses 100% of a
core; its stack ... memmem <- AutoTuneLogScanner.find <- hasAll <- line <- feed <- scan <- AutoTune.analyse", five of six logs).
A needle can only be in a line when every pair of neighbouring bytes of the needle is in the line; the scanner now notes each
line's pairs once (4096 hashed bits) and searches a signal only when the pairs of all its needles are there.
Checks:
  - the source: the filter only ever skips a search (it sits in front of hasAll, which is unchanged), the pairs come from the
    needles themselves, at most eight a needle, and the line's bits are rebuilt for every line from the bytes the search reads;
  - on the host (needs swiftc): every fixture log scanned with the filter and without it, whole and in 4096-byte and 7-byte
    pieces: counts, first lines, evidence and line count equal; the same for made-up lines built from every signal's needles
    (at the start, in the middle, at the end, cut by one byte, backwards, across the 16384-byte line limit);
  - CI runs this check.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:2000])


core = (R / "app/Madeira/AutoTuneCore.swift").read_text()
sc = core[core.index("final class AutoTuneLogScanner {"):core.index("    private func markers(_ l: UnsafeRawBufferPointer) {")]

check("the filter sits in front of the unchanged search: a signal is counted only when hasAll finds every needle",
      "for i in needles.indices where (!filtered || pairsPresent(i)) && Self.hasAll(l, needles[i]) {" in sc
      and "for n in list where find(l, n) == nil { return false }" in sc and "return !list.isEmpty" in sc)
check("...the line's pairs are noted for the same bytes the search reads (after the 16384-byte cut), for every line",
      sc.index("let l = whole.count > Self.maxLine ? UnsafeRawBufferPointer(rebasing: whole[0..<Self.maxLine]) : whole")
      < sc.index("if filtered { notePairs(l) }") < sc.index("for i in needles.indices where (!filtered || pairsPresent(i))")
      and "for k in 0..<64 { w[k] = 0 }" in sc)
check("...a signal's pairs come from its own needles: neighbouring bytes n[at], n[at + 1], at most maxPairs (8) a needle, spread over it",
      "bits.append(Self.pairHash(n[at], n[at + 1]))" in sc and "static let maxPairs = 8" in sc
      and "let pairs = n.count - 1, take = min(pairs, Self.maxPairs)" in sc
      and "let at = take == pairs ? k : k * (pairs - 1) / (take - 1)" in sc and "for n in list where n.count >= 2 {" in sc)
check("...one hash for the needle's pairs and the line's (12 bits), and a missing bit is the only way to skip",
      sc.count("Self.pairHash(") == 2 and "UInt16(truncatingIfNeeded: (UInt32(a) &* 31 &+ UInt32(b)) & 4095)" in sc
      and "if linePairs[h >> 6] & (1 << UInt64(h & 63)) == 0 { return false }" in sc)
check("...the app always filters: only init's pairFilter (default true) turns it off, and scan() does not pass it",
      "init(signals: [AutoTuneSignal], pairFilter: Bool = true) {" in sc and "usePairs = pairFilter" in sc
      and "let scanner = AutoTuneLogScanner(signals: signals)" in sc and "pairFilter: false" not in core)
check("the pair positions cover the needle's first and last pair when it is longer than eight pairs",
      all((0 * (p - 1) // 7 == 0) and (7 * (p - 1) // 7 == p - 1) for p in range(9, 200)))

swiftc = shutil.which("swiftc")
logs = sorted((R / "tests/fixtures/autotune").glob("*.log"))
if swiftc:
    with tempfile.TemporaryDirectory(prefix="scanfilter-") as tmp:
        exe = str(pathlib.Path(tmp) / "scanfilter")
        b = subprocess.run([swiftc, "-O", "-parse-as-library", "-swift-version", "5", "-o", exe,
                            str(R / "app/Madeira/AutoTuneCore.swift"), str(R / "app/Madeira/AutoTuneRules.generated.swift"),
                            str(R / "tests/host/autotune_scan_filter.swift")], capture_output=True, text=True)
        check("the scanner and the comparison build on the host", b.returncode == 0, b.stderr[-3000:])
        if b.returncode == 0:
            r = subprocess.run([exe] + [str(p) for p in logs], capture_output=True, text=True)
            print(r.stdout.strip()[-1500:])
            check("%d fixture logs and the made-up lines: the filter changes no count, first line, evidence or line count" % len(logs),
                  r.returncode == 0 and "all equal" in r.stdout and len(logs) > 500, r.stdout[-2000:] + r.stderr[-1000:])
else:
    print("note: no swiftc on this host; the comparison was not run")

ci = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-autotune-scan-filter.py" in ci)

print("PASS: autotune-scan-filter" if ok else "FAILED: autotune-scan-filter")
sys.exit(0 if ok else 1)
