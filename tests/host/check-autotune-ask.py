#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Auto-tune's "Ask before trying a fix" (Porthole port, item 10; app/Madeira/AutoTuneAsk.swift). No iPhone.

1. The rules, compiled with AutoTuneCore.swift and run here (tests/host/bcd_autotune_ask_checks.swift): off changes nothing; on, a
   new fix waits for the player's yes, Not now is not asked again, Try it lets the next start have it, Undo forgets the answers.
2. Off path, on the source: every hook goes through AutoTuneAsk, which with the switch off returns auto-tune's own values; the switch is
   a UserDefaults bool (absent: off), shown in Settings only while auto-tune is on.
3. The alert, the game page and the Settings row; the workflow.
check-autotune.py registers every new line outside AutoTune*.swift that names auto-tune.
"""
from pathlib import Path
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


ask = (APP / "AutoTuneAsk.swift").read_text()
glue = (APP / "AutoTune.swift").read_text()
views = (APP / "AutoTuneViews.swift").read_text()
page = (APP / "BCDGamePage.swift").read_text()
page_live = (APP / "BCDGamePageLive.swift").read_text()
sections = (APP / "BCDSettingsSections.swift").read_text()
settings_live = (APP / "BCDSettingsLive.swift").read_text()

# ---- 1. behaviour
swiftc = shutil.which("swiftc")
if not swiftc:
    check("swiftc for the behaviour checks", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory(prefix="bcd-autotune-ask-") as tmp:
        exe = Path(tmp) / "bcd-autotune-ask-checks"
        r = subprocess.run([swiftc, "-parse-as-library", "-swift-version", "5", "-o", str(exe), str(APP / "AutoTuneCore.swift"),
                            str(APP / "AutoTuneAsk.swift"), str(R / "tests/host/bcd_autotune_ask_checks.swift")], capture_output=True, text=True)
        if check("AutoTuneAsk.swift compiles with auto-tune's core and its harness", r.returncode == 0, (r.stderr or r.stdout)[-3000:]):
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print("  " + line)
            n = sum(1 for l in r.stdout.splitlines() if l.startswith("PASS"))
            check(f"the Swift harness: {n} checks, none failing", r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:])

# ---- 2. off path
check("the switch is a UserDefaults bool, off while absent", 'static let key = "madeira.autotune.ask"' in ask
      and "static var on: Bool { UserDefaults.standard.bool(forKey: key) }" in ask)
check("nothing waits while it is off, and the layer is then auto-tune's own",
      "guard on, let next = state.candidate, !next.isEmpty else { return [] }" in ask
      and "waiting(state, windowsPath).isEmpty ? state.layerLines : AutoTuneState.merge(state.kept, [])" in ask)
check("the launch file gets AutoTuneAsk.layer (auto-tune's layer while off), and the start says when a fix waits",
      "let lines = AutoTuneAsk.layer(s, path).filter { !blocked.contains(AutoTuneRules.key($0)) }" in glue
      and "waits for the player's yes (Ask before trying a fix); this start runs without it" in glue)
check("the session's marker records what the start really got, only while the switch is on",
      "if AutoTuneAsk.on { m.layer = AutoTuneAsk.layer(state(entry.windowsPath), entry.windowsPath)" in glue)
post = glue[glue.index("            // madeira-bcd (Porthole port, item 10): the player put this very fix off"):]
check("a result about a fix the player put off is held back (logged), before the one place that posts a result",
      post.index("if AutoTuneAsk.quiet(offer) {") < post.index("AutoTuneOffers.shared.offer = offer") and "result not shown, the fix it would try waits" in post)
alert = views[views.index("struct AutoTuneAlert: ViewModifier {"):]
check("the alert's own branches stay: the What happened? question first, then the ask, then the result as always",
      alert.index("if offer.ask {") < alert.index("} else if AutoTuneAsk.asks(offer) {") < alert.index('Button("Close BCD")'))
check("the ask: Try it next start, Stop for this game, Not now (no restart from it)",
      'Button("Try it next start") { AutoTuneAsk.approve(offer.state, offer.windowsPath); offers.offer = nil }' in alert
      and 'Button("Not now", role: .cancel) { AutoTuneAsk.decline(offer.state, offer.windowsPath); offers.offer = nil }' in alert
      and alert.count("exit(0)") == 1)
check("Undo only when the switch is on and lines were kept; it is the page's Undo",
      "if AutoTuneAsk.offersUndo(offer) {" in alert and "AutoTune.reset(offer.windowsPath)" in alert
      and "on && !offer.ask && offer.state.status == \"found\" && !offer.state.kept.isEmpty" in ask)
check("the words: the ask's own while a fix waits, else the result's as always",
      "Text(AutoTuneAsk.asks(offer) ? AutoTuneAsk.message(offer) : offer.message)" in alert)

# ---- 3. page, Settings, CI
check("the game page: the waiting fix in the status line and a Try the suggested fix row (only while one waits)",
      "var waiting: [String] = []" in page and "if !tuning.waiting.isEmpty {" in page and "model.request(.approveTuning)" in page
      and "func approveTuning(_ game: BCDGame) {}" in page and "let waiting = tuned.on ? AutoTuneAsk.waiting(state, path) : []" in page_live)
check("Settings › Logs › After a crash has the switch, off by default, under Auto-tune and only while it is on",
      'title: "Ask before trying a fix"' in sections and sections.index('"autotune"), title: "Auto-tune"') < sections.index('"askfix")')
      and "if backend.crashTuning {" in sections and "var asksBeforeFix: Bool { false }" in sections
      and "UserDefaults.standard.set(on, forKey: AutoTuneAsk.key)" in settings_live)
check("the new file is BCD's own and in the project", ask.startswith("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)")
      and "AutoTuneAsk.swift in Sources" in (R / "app/Madeira.xcodeproj/project.pbxproj").read_text())
check("no timer, observer or environment read in it", not re.search(r"Timer|NotificationCenter|getenv\(|DispatchSource", ask))
check("the workflow runs this check", "run: python3 tests/host/check-autotune-ask.py" in (R / ".github/workflows/build-ipa.yml").read_text())

print("FAILED: ask before trying a fix" if failures else "PASS: ask before trying a fix")
sys.exit(1 if failures else 0)
