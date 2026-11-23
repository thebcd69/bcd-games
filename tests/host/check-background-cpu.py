#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Settings > Memory & performance > In the background > "CPU limit in the background" (round 707, A2's F11: a player's downloads were
slow in the background under BCD's 50 % cap).

  1. BCDBackgroundCPU.swift (Foundation only) compiled with tests/host/bcd_background_cpu_checks.swift: the row's choices, what each writes,
     how every kind of bcd.cfg value reads back, and that the standard writes no line, which is exactly today's behaviour.
  2. Source: BackgroundRun.cpuPercent is still the rule BCDBackgroundCPU.effective copies (word for word), the default stays 50, the settings
     catalog already has the key (no new key), the row is in the In the background group between the downloads row and the sound row, the live
     backend writes through MadeiraConfig with the key as a value, the preview's backend needs nothing new, the files are in the project.

  check-background-cpu.py [--require-swift]
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
core = (APP / "BCDBackgroundCPU.swift").read_text()
run = (APP / "BackgroundRun.swift").read_text()
sections = (APP / "BCDSettingsSections.swift").read_text()
live = (APP / "BCDSettingsLive.swift").read_text()
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
preview = (R / "app/BCDPreview/BCDSettingsPreviewData.swift").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
workflow = (R / ".github/workflows/build-ipa.yml").read_text()

check(core.startswith(HEADER) and (R / "tests/host/bcd_background_cpu_checks.swift").read_text().startswith(HEADER), "licence headers")
check(re.findall(r"^import (\w+)", core, re.M) == ["Foundation"], "the rules import Foundation only")
check('guard let raw = MadeiraConfig.gameValue("background-cpu") ?? MadeiraConfig.get("background-cpu"), let v = Int32(raw) else { return 50 }\n        return max(0, v)' in run
      and "guard let raw, let value = Int32(raw) else { return standardPercent }\n        return Int(max(0, value))" in core
      and "static let standardPercent = 50" in core,
      "BackgroundRun.cpuPercent is the rule the row copies: a whole number, at least 0, anything else 50")
check("let cap = Self.cpuPercent" in run and "if cap > 0 { madeira_bg_cpu_cap(cap); cpuCapOn = true }" in run,
      "the engine reads the key each time BCD is left, and 0 is no cap")
check('ConfigOption(key: "background-cpu", title: "CPU cap in the background (% of one core)", kind: .int, defaultValue: "50"' in catalog,
      "the settings catalog already has background-cpu with its default of 50: the row adds no key")
check("var backgroundCPU: String { get }" in sections and "func setBackgroundCPU(_ percent: String)" in sections
      and 'var backgroundCPU: String { "" }' in sections and "func setBackgroundCPU(_ percent: String) {}" in sections,
      "the memory backend carries the value, with a default so the preview's backend needs nothing")
row = 'BCDSettingsValueRow(id: BCDSettingsID.control(.memory, "background-cpu"), title: BCDBackgroundCPU.title, note: BCDBackgroundCPU.note,'
group = sections.split('BCDSettingsGroup(title: "In the background"')[1].split("if backend.extensionTestAvailable")[0]
check(row in group and group.index('"background-download"') < group.index('"background-cpu"') < group.index('"background-sound"'),
      "the row is in the In the background group, after Keep downloads running and before Hear the game meanwhile")
check("choices: BCDBackgroundCPU.choices(current: backend.backgroundCPU)" in group and "value: backend.backgroundCPU) { backend.setBackgroundCPU($0) }" in group,
      "the row offers BCDBackgroundCPU's choices and writes through the backend")
check("a game or a download goes slower than in front" in group and "loads slower than in front" not in group,
      "the group's footer says a download goes slower too")
check('static let backgroundCPU = BCDBackgroundCPU.key' in live
      and "backgroundCPU = BCDBackgroundCPU.rowValue(MadeiraConfig.get(BCDLiveKey.backgroundCPU))" in live
      and "MadeiraConfig.set(BCDLiveKey.backgroundCPU, stored)" in live and "let stored = BCDBackgroundCPU.stored(percent)" in live,
      "live: reads the key as the engine does and writes the standard as no line (the key is passed as a value, so the catalog scanner counts no new reader)")
check('MadeiraConfig.get("background-cpu")' not in live and 'MadeiraConfig.set("background-cpu"' not in live, "live: no literal read or write of the key")
check("backgroundCPU" not in preview, "the preview's own backend is untouched")
check(project.count("/* BCDBackgroundCPU.swift in Sources */") == 2 and project.count("path = BCDBackgroundCPU.swift;") == 1, "BCDBackgroundCPU.swift is in the Xcode project")
if "python3 tests/host/check-background-cpu.py" not in workflow:
    print("NOTE: .github/workflows/build-ipa.yml does not run this check yet (the step is in the hand-back)")

swiftc = shutil.which("swiftc")
if not swiftc:
    check(not args.require_swift, "swiftc on the host (behaviour checks)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-bgcpu-") as t:
        tmp = Path(t)
        shutil.copy(R / "tests/host/bcd_background_cpu_checks.swift", tmp / "main.swift")
        exe = tmp / "run"
        b = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(exe), str(APP / "BCDBackgroundCPU.swift"), str(tmp / "main.swift")],
                           capture_output=True, text=True)
        check(b.returncode == 0, "the rules and their checks compile on the host", b.stderr[-2000:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
            bad = [l for l in r.stdout.splitlines() if l.startswith("FAIL") or l.startswith("     ")]
            summary = [l for l in r.stdout.splitlines() if l.startswith("background cpu:")]
            check(r.returncode == 0 and summary, summary[0] if summary else "the CPU limit's rules", "\n      ".join(bad[:10]))

print("check-background-cpu: " + ("PASS" if failures == 0 else f"{failures} FAILED"))
sys.exit(0 if failures == 0 else 1)
