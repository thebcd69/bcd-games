#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""In-app "What does this mean?" (Porthole port, item 15; app/Madeira/BCDExplain.swift). No iPhone.

1. Compiled and run here: each entry answers the message BCD really writes (the texts are read from the app's own sources, so a message
   that changes wording is caught here), the first match wins, a message without an entry gets no button.
2. The words: plain English for players, "license" spelling, no address, no website, nothing about upstream.
3. The library's alert: the button only for a message with an entry and never for a JIT connection problem (that alert has its own
   actions); the explanation in an alert of its own; nothing opened or sent.
"""
from pathlib import Path
import json
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


src = (APP / "BCDExplain.swift").read_text()
sources = "\n".join(p.read_text() for p in APP.rglob("*.swift") if p.name != "BCDExplain.swift")

# The messages BCD writes that the entries answer, as they stand in the sources (a changed message fails here).
messages = {
    "jit-needed": "JIT has to be enabled before a game can start.",
    "jit-then-play": "JIT is now enabled, but the game didn't start. Tap Play once more.",
    "one-session": "Another session is still running.",
    "desktop-running": "Windows is still running. On Home, choose Show Windows to return to it, or Stop before starting a game.",
    "memory-limit": "This install lacks the increased memory limit",
    "drive-missing": "The external drive is not connected. Plug it in, then try again.",
    "steam-signin": "Steam no longer accepts your sign-in. Sign out of Steam in Settings, then sign in again.",
    "free-space": "Not enough free space: ",
    "command-line": "The program path or its launch arguments are longer than BCD allows.",
}
for id, text in messages.items():
    check(f"{id}: the message is still BCD's own words", text in sources, text)

swiftc = shutil.which("swiftc")
if not swiftc:
    check("swiftc for the behaviour checks", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory(prefix="bcd-explain-") as tmp:
        main = Path(tmp) / "main.swift"
        cases = "\n".join(f'expect(BCDExplain.explanation(for: {json.dumps(t)})?.id == "{i}", "{i}")' for i, t in messages.items())
        main.write_text("import Foundation\nvar fails = 0\nfunc expect(_ ok: Bool, _ what: String) { print((ok ? \"ok   \" : \"FAIL \") + what); if !ok { fails += 1 } }\n"
                        + cases + "\n"
                        + 'expect(BCDExplain.explanation(for: "Saving the library failed: disk") == nil, "a message without an entry gets nothing")\n'
                        + 'expect(BCDExplain.explanation(for: nil) == nil && BCDExplain.explanation(for: "") == nil, "no message, nothing")\n'
                        + 'expect(Set(BCDExplain.entries.map { $0.explanation.id }).count == BCDExplain.entries.count, "ids are unique")\n'
                        + 'expect(BCDExplain.entries.allSatisfy { $0.match == $0.match.lowercased() && !$0.explanation.text.isEmpty }, "pieces are lower case, texts not empty")\n'
                        + "exit(fails == 0 ? 0 : 1)\n")
        exe = Path(tmp) / "run"
        r = subprocess.run([swiftc, "-o", str(exe), str(APP / "BCDExplain.swift"), str(main)], capture_output=True, text=True)
        if check("BCDExplain.swift compiles alone on the host (Foundation only)", r.returncode == 0, (r.stderr or r.stdout)[-2000:]):
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            print(r.stdout.rstrip())
            check("each message finds its explanation, and only those do", r.returncode == 0)

texts = re.findall(r'text: "([^"]*)"', src) + re.findall(r'title: "([^"]*)"', src)
check("the words name no address or website, and spell license with an s",
      not any(re.search(r"https?://|www\.|website|licence|Madeira|Porthole|upstream", t, re.I) for t in texts))
check("Foundation only, no network, nothing opened", "import Foundation" in src and not re.search(r"UIApplication|openURL|URLSession|import SwiftUI", src))
front = (APP / "BCDLiveFrontEnd.swift").read_text()
check("the library's alert offers the button only for a message with an entry, never for a JIT connection problem",
      "if jitProblem == nil, let why = BCDExplain.explanation(for: library.error) {" in front
      and "Button(BCDExplain.buttonTitle) { explaining = why; library.error = nil }" in front)
check("the explanation is an alert of its own, with OK alone", ".modifier(BCDExplainAlert(explaining: $explaining))" in front
      and front[front.index("struct BCDExplainAlert"):].count("Button(") == 1)
check("in the project", "BCDExplain.swift in Sources" in (R / "app/Madeira.xcodeproj/project.pbxproj").read_text())
check("the workflow runs this check", "run: python3 tests/host/check-bcd-explain.py" in (R / ".github/workflows/build-ipa.yml").read_text())

print("FAILED: in-app explanations" if failures else "PASS: in-app explanations")
sys.exit(1 if failures else 0)
