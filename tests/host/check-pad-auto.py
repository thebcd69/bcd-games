#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A game that never asks for a controller (a game's own line `pad-auto = 1`; app/Madeira/BCDPadAuto.swift, BCDPadAutoLive.swift,
Winios/WiniosGamepad.c). No iPhone, no controller.

1. The two rules and what is kept between runs (tests/host/bcd_pad_auto_checks.swift).
2. The engine's count, compiled and run here: every XInput read counts, connected or not, and nothing else does.
3. Off path, on the source: without the game's line the session's start returns at its first test (no timer, nothing read); the
   rule never writes the game's Controller mode; a chosen Controller mode or another Controller API than XInput stands it down.
4. The game page's switch, the catalog, the project, the credit, the workflow.
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


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)
    return cond


plain = (APP / "BCDPadAuto.swift").read_text()
live = (APP / "BCDPadAutoLive.swift").read_text()
pad_c = (APP / "Winios/WiniosGamepad.c").read_text()
pad_h = (APP / "Winios/WiniosGamepad.h").read_text()

# ---- 1. the rules
if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift part", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        shutil.copy(R / "tests/host/bcd_pad_auto_checks.swift", t / "main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "run"), str(t / "main.swift"), str(APP / "BCDPadAuto.swift")], capture_output=True, text=True)
        if check("BCDPadAuto.swift and its harness compile on the host", r.returncode == 0):
            r = subprocess.run([str(t / "run")], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print(line)
            check(f"the Swift harness: {sum(1 for l in r.stdout.splitlines() if l.startswith('PASS'))} checks, none failing", r.returncode == 0 and "ALL PASS" in r.stdout)
        else:
            print((r.stdout + r.stderr)[-2500:])

# ---- 2. the engine's count
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP: no C compiler for the counter")
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "main.c").write_text(r'''
#include "WiniosGamepad.h"
#include <stdio.h>
#include <string.h>
static int fails;
static void expect(int c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
int main(void) {
  struct winios_gamepad pad, out;
  struct winios_hidpad hid;
  int i;
  expect(winios_gamepad_read_count() == 0, "nothing read yet: 0");
  memset(&pad, 0, sizeof pad); pad.connected = 1; pad.buttons = 0x1000;
  winios_gamepad_set_state(0, &pad);
  winios_hidpad_get_state(&hid);
  winios_gamepad_set_vibration(0, 1, 2);
  expect(winios_gamepad_read_count() == 0, "publishing a pad, the HID pad and vibration are not reads");
  expect(winios_gamepad_get_state(0, &out) == 1 && out.buttons == 0x1000 && winios_gamepad_read_count() == 1, "one read of a connected slot: 1, and the answer is what it was");
  for (i = 1; i < 4; i++) winios_gamepad_get_state(i, &out);
  expect(winios_gamepad_read_count() == 4, "reads of the three empty slots count too (a game asks even with no controller)");
  winios_gamepad_get_state(-1, &out); winios_gamepad_get_state(4, &out);
  expect(winios_gamepad_read_count() == 4, "a slot that does not exist is not counted (and not read)");
  for (i = 0; i < 1000; i++) winios_gamepad_get_state(i & 3, NULL);
  expect(winios_gamepad_read_count() == 1004, "a thousand more: 1004");
  return fails ? 1 : 0;
}
''')
        b = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-Wextra", f"-I{APP / 'Winios'}", "-o", str(t / "h"), str(t / "main.c"), str(APP / "Winios/WiniosGamepad.c"), "-lpthread"],
                           capture_output=True, text=True)
        if check("WiniosGamepad.c compiles on the host without a warning", b.returncode == 0 and "warning" not in b.stderr):
            r = subprocess.run([str(t / "h")], capture_output=True, text=True, timeout=60)
            print(r.stdout.rstrip())
            check("the count of XInput reads", r.returncode == 0 and "ok  " in r.stdout)
        else:
            print(b.stderr[-2000:])
get = pad_c[pad_c.index("int winios_gamepad_get_state(int index, struct winios_gamepad *out)"):]
get = get[:get.index("\n}\n")]
check("the read itself is what it was, with one relaxed atomic add in front of it",
      "__atomic_fetch_add(&pad_reads[index], 1, __ATOMIC_RELAXED);" in get and pad_c.count("pad_reads[") == 3
      and "uint64_t winios_gamepad_read_count(void);" in pad_h)

# ---- 3. off path
begin = live[live.index("    static func begin() {"):live.index("    private static func useKeys(say: Bool) {")]
check("a session's start returns at its first test without the game's own line (bcd.cfg does not count), before a timer is made or the count is read",
      begin.splitlines()[1].strip() == "guard timer == nil, BCDPadAuto.on(MadeiraConfig.gameValue(BCDPadAuto.key)), GamepadInput.keyboardMouseAvailable else { return }"
      and "MadeiraConfig.get(" not in live and begin.index("else { return }") < begin.index("winios_gamepad_read_count()") < begin.index("Timer("))
check("the player's choice comes first: a Controller mode for the game, or another Controller API than XInput, stands the rule down",
      'padRule = entry.controllerMode == nil && GamepadInput.configuredPadMode().source == "default"' in begin
      and "if padRule, model.controllerMode != nil {" in live)
check("the rule never writes the game's Controller mode (the library would keep it as the player's choice): it switches the sampler only",
      re.search(r"\.controllerMode\s*=[^=]", live) is None and "GamepadInput.shared.setKeyboardMouse(PadBindings.build(" in live
      and "model.applyControllerMode()" in live and "if ours { GamepadInput.shared.setKeyboardMouse(nil) }" in live)
check("the end returns at once when nothing was started", live[live.index("    static func end() {"):].splitlines()[1].strip() == "guard timer != nil else { return }")
check("the plain file reads nothing of the app or the engine", re.search(r"winios_|GCController|LibraryModel|MadeiraConfig|UserDefaults", plain) is None)
others = [p.name for p in APP.rglob("*.swift") if p.name != "BCDPadAutoLive.swift" and "winios_gamepad_read_count" in p.read_text()]
check("only BCDPadAutoLive.swift reads the count", not others)
content = (APP / "ContentView.swift").read_text()
menu = (APP / "BCDMenuMore.swift").read_text()
check("ContentView starts it once per session, and the session's end stops it",
      content.count("BCDPadAutoLive.launch()") == 1 and menu.count("BCDPadAutoLive.end()") == 1 and menu.index("static func sessionEnded() {") < menu.index("BCDPadAutoLive.end()"))

# ---- 4. the app around it
page = (APP / "BCDGamePageLive.swift").read_text()
check("the game's page has the switch beside Controller mode, and writes the game's own line",
      'settings.append(Self.toggle("padauto", BCDPadAuto.title, BCDPadAuto.on(profile.get(BCDPadAuto.key)), note: BCDPadAuto.note))' in page
      and 'case "padauto":\n            setLine(game, BCDPadAuto.key, on ? "1" : "")' in page)
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
check("the catalog has the key, off by default", re.search(r'key: "pad-auto", title: "[^"]*", kind: \.bool, defaultValue: "0"', catalog) is not None)
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("both files are in the project; the preview build leaves the engine-bound one out",
      project.count("BCDPadAuto.swift in Sources") >= 1 and project.count("BCDPadAutoLive.swift in Sources") >= 1 and '"BCDPadAutoLive.swift"' in (R / "tools/ui-preview.py").read_text())
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
check("the rules and the counter are credited: Porthole's files in the headers, their rows in the notices",
      "app/Sources/Core/PadAuto.swift and LayoutSuggestion.swift" in plain.split("import Foundation")[0] and "BCD Material" not in plain
      and "patches/0019-pad-read-counters.patch" in pad_c[:600] and "| `app/Madeira/BCDPadAuto.swift` |" in notices and "| `app/Madeira/Winios/WiniosGamepad.c` |" in notices)
check("the workflow runs this check", "run: python3 tests/host/check-pad-auto.py" in (R / ".github/workflows/build-ipa.yml").read_text())

print("FAILED: pad auto" if failures else "PASS: pad auto")
sys.exit(1 if failures else 0)
