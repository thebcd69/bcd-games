#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Launch arguments keep their dashes and quotes (round 632; the idea is upstream Madeira's #365).

iOS Smart Punctuation turns "--" into an em dash and straight double quotes into curly ones while a launch argument is typed, so "--windowed"
reaches the game as an em dash followed by "windowed" and a quoted path is not quoted. No BCD log shows a damaged argument yet (the Discord ideas
"Steam Launch Options" and "Let choose exe file when using steam" ask for arguments), so this only closes a door.

Checks:
  - LaunchArgumentText.plain, taken from Library.swift and built on the host: an em dash or en dash where an argument starts becomes "--", a minus
    sign "-", a curly double quote a straight one; a dash inside a value, a curly single quote and every other character stay; every plain ASCII line is
    returned unchanged (random lines, quotes, backslashes and tabs included: the identity on everything that works today); the function is idempotent;
  - the three places: the text field of BCD's setting editor turns smart dashes and quotes off (a UIKit probe behind it), the save of the
    "arguments" setting and the export of MADEIRA_ARGS (the direct start) call plain; the fail-closed parser of upstream (refusing odd quotes,
    more than 64 tokens) is not taken, because it refuses arguments that run today;
  - CI runs this check.
"""
from pathlib import Path
import os, random, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


library = (root / "app/Madeira/Library.swift").read_text()
live = (root / "app/Madeira/BCDGamePageLive.swift").read_text()
components = (root / "app/Madeira/BCDComponents.swift").read_text()
page = (root / "app/Madeira/BCDGamePage.swift").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()

start = library.index("enum LaunchArgumentText {")
depth, i = 0, library.index("{", start)
j = i
while True:
    depth += (library[j] == "{") - (library[j] == "}")
    j += 1
    if depth == 0:
        break
enum_text = library[start:j]

check("the save of the arguments setting and the export of MADEIRA_ARGS go through plain",
      "case \"arguments\": change(game) { $0.arguments = LaunchArgumentText.plain(value) }" in live
      and 'setenv("MADEIRA_ARGS", LaunchArgumentText.plain(command.args), 1)' in library)
check("every other MADEIRA_ARGS export is left as it was (the Dock host's and the held values of a restart)",
      library.count('setenv("MADEIRA_ARGS"') == 2 and 'setenv("MADEIRA_ARGS", hold.args, 1)' in library)
check("the setting editor's text field turns smart dashes and quotes off, through a probe in BCDComponents.swift",
      ".bcdSmartPunctuationOff()" in page[page.index("TextField(prompt, text: $draft, axis: .vertical)"):][:600]
      and "field.smartDashesType = .no; field.smartQuotesType = .no" in components and "text.smartDashesType = .no; text.smartQuotesType = .no" in components
      and "isUserInteractionEnabled = false" in components[components.index("struct BCDSmartPunctuationOff"):][:1800])
check("upstream's fail-closed parser is not here (it refuses arguments that run today)", "static func valid" not in enum_text and "LaunchArgumentText.valid" not in library)
check("CI runs this check", "python3 tests/host/check-launch-argument-text.py" in wf)

swiftc = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not swiftc:
    print("skip: no swiftc")
else:
    cases = [
        ("\u2014windowed", "--windowed"), ("\u2013fullscreen", "--fullscreen"), ("\u2212w", "-w"),
        ("-nosplash \u2014windowed", "-nosplash --windowed"), ("-a\t\u2014b", "-a\t--b"),
        ("\u201CC:\\My Game\\g.exe\u201D", "\"C:\\My Game\\g.exe\""), ("-title \u201EHi\u201C", "-title \"Hi\""),
        ("--name=Foo\u2014Bar", "--name=Foo\u2014Bar"), ("a\u2014b", "a\u2014b"), ("-path Marvel\u2019s", "-path Marvel\u2019s"),
        ("\u2018single\u2019", "\u2018single\u2019"), ("caf\u00e9 \u65e5\u672c", "caf\u00e9 \u65e5\u672c"), ("", ""),
        ("\u2014", "--"), ("a \u2014", "a --"),
    ]
    rng = random.Random(632)
    alphabet = "abcXYZ 019-_=.\"'\\/:\t,;"
    ascii_lines = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60))) for _ in range(400)]
    unicode_pool = "\u2014\u2013\u2212\u201c\u201d\u201e\u2018\u2019 -\"a\tb"
    mixed = ["".join(rng.choice(unicode_pool) for _ in range(rng.randint(0, 30))) for _ in range(400)]

    def literal(text):
        return '"' + "".join("\\u{%x}" % ord(c) if (ord(c) > 126 or c in '"\\') or ord(c) < 32 else c for c in text) + '"'

    code = "import Foundation\n" + enum_text + "\nnonisolated(unsafe) var failures = 0\n" + r'''
func check(_ condition: Bool, _ label: String) { if !condition { print("FAIL " + label); failures += 1 } }
let cases: [(String, String)] = [''' + ",\n".join("(%s, %s)" % (literal(a), literal(b)) for a, b in cases) + r'''
]
for (input, want) in cases { check(LaunchArgumentText.plain(input) == want, "plain(\(input.unicodeScalars.map { String($0.value, radix: 16) })) = \(LaunchArgumentText.plain(input).unicodeScalars.map { String($0.value, radix: 16) })") }
let asciiLines: [String] = [''' + ",\n".join(literal(x) for x in ascii_lines) + r'''
]
for line in asciiLines { check(LaunchArgumentText.plain(line) == line, "ASCII changed: \(line)") }
let mixedLines: [String] = [''' + ",\n".join(literal(x) for x in mixed) + r'''
]
for line in mixedLines {
    let once = LaunchArgumentText.plain(line)
    check(LaunchArgumentText.plain(once) == once, "not idempotent: \(line)")
    check(!once.contains("\u{201C}") && !once.contains("\u{201D}") && !once.contains("\u{201E}"), "a curly double quote is left: \(line)")
}
print(failures == 0 ? "ok   plain: \(cases.count) cases, \(asciiLines.count) ASCII lines unchanged, \(mixedLines.count) mixed lines idempotent" : "FAILURES: \(failures)")
exit(failures == 0 ? 0 : 1)
'''
    with tempfile.TemporaryDirectory(prefix="madeira-launch-args-") as td:
        t = Path(td)
        (t / "main.swift").write_text(code)
        b = subprocess.run([swiftc, "-swift-version", "5", "-warnings-as-errors", str(t / "main.swift"), "-o", str(t / "main")], capture_output=True, text=True)
        check("the production LaunchArgumentText builds on the host (warnings are errors)", b.returncode == 0, b.stderr[-2500:])
        if b.returncode == 0:
            r = subprocess.run([str(t / "main")], capture_output=True, text=True, timeout=60)
            sys.stdout.write(r.stdout)
            check("the normaliser's cases pass", r.returncode == 0 and "ok   plain:" in r.stdout, r.stdout[-1500:] + r.stderr[-1000:])

print("PASS: launch arguments keep their dashes and quotes" if ok else "FAILED: launch arguments keep their dashes and quotes")
sys.exit(0 if ok else 1)
