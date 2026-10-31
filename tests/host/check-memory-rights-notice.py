#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""An install without the increased memory limit is told, never blocked (app/Madeira/MemoryRights.swift).

Owner, 2026-10-21: no hard block (simple games run without the limit: Doom 3, GTA V Legacy, Moonlighter and
Tomb Raider: Underworld got in game in the 89 logs of such installs); a small box with OK once per build over the
library, and the same advice where a log is sent (Settings › Logs and a game's page). Source invariants: the
condition, once per build, OK only, never during a game, the advice at both send points, nothing refused, and the
layering (the engine-side file is not a BCD*.swift screen file; the screens get the text through their models).
"""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
app = root / "app/Madeira"
failures = 0


def code(text):
    """The text without // comments (a comment may name a file; only code counts)."""
    return "\n".join(l.split("//", 1)[0] for l in text.splitlines())


def check(ok, label):
    global failures
    print(("PASS: " if ok else "FAIL: ") + label)
    failures += 0 if ok else 1


rights = (app / "MemoryRights.swift").read_text()
front = (app / "BCDLiveFrontEnd.swift").read_text()
page = (app / "BCDGamePage.swift").read_text()
page_live = (app / "BCDGamePageLive.swift").read_text()
sections = (app / "BCDSettingsSections.swift").read_text()
settings_live = (app / "BCDSettingsLive.swift").read_text()
pbx = (root / "app/Madeira.xcodeproj/project.pbxproj").read_text()

check("static let lacking: Bool = !EntitlementStatus.check().increasedMemory" in rights,
      "the condition is the increased-memory entitlement (it matches the profile line on all 427 logged installs)")
check("lacking && build != nil && shownBuild != build" in rights and "UserDefaults.standard.set(build, forKey: MemoryRights.shownBuildKey)" in rights,
      "the box is due once per build, only when the limit is lacking")
check("[memory-rights]" in rights, "the log says it was told")
box = front[front.index('.alert(memory.title, isPresented:'):front.index('.alert("Update available"')]
check("library.current == nil" in box and "!onboarding.presented" in box and box.count("Button(") == 1
      and 'Button("OK", role: .cancel)' in box, "the box waits for the library and has OK alone")
check("memory.start()" in front[front.index(".onAppear {"):front.index(".onChange(of: scenePhase)")], "it starts when the library appears")
check("details.sendLogAdvice" in page and "var sendLogAdvice: String? = nil" in page
      and "d.sendLogAdvice = MemoryRights.lacking ? MemoryRights.sendLogAdvice : nil" in page_live,
      "a game's page says it before a log is sent")
check("backend.sendLogAdvice" in sections and "var sendLogAdvice: String? { nil }" in sections
      and "var sendLogAdvice: String? { MemoryRights.lacking ? MemoryRights.sendLogAdvice : nil }" in settings_live,
      "Settings › Logs says it before a log is sent")
dock_screen = (app / "DockStartScreen.swift").read_text()
autotune_ui = (app / "AutoTune.swift").read_text()
_sc = autotune_ui.index('        case "stuck", "gave-up":')
stuck_case = autotune_ui[_sc:autotune_ui.index('        case "known":', _sc)]
check('static let failureNote = "This install lacks the increased memory limit, the usual reason a game stops this early. "' in rights
      and '+ "Add it with GetMoreRam (with SideStore or LiveContainer), or sign BCD with a paid developer account."' in rights
      and 'failure = current.failure == nil && MemoryRights.lacking ? words + "\\n\\n" + MemoryRights.failureNote : words' in dock_screen
      and 'if MemoryRights.lacking { text += MemoryRights.failureNote + "\\n\\n" }' in stuck_case
      and stuck_case.index("MemoryRights.failureNote") < stuck_case.index('"Export the auto-tune report'),
      "round 624 (N S1): where a game has just stopped on such an install, the Dock's generic stopped words and auto-tune's stuck alert name it "
      "(a sign-in or licence text keeps its own words)")
notice = rights[rights.index("@MainActor final class MemoryRightsNotice"):]
start = notice[notice.index("    func start() {"):]
start = start[:start.index("\n    }\n") + 7]
stopped = notice[notice.index("    func lastStartStoppedInPoolRequest() {"):]
stopped = stopped[:stopped.index("\n    }\n") + 7]
recover = (app / "BCDSessionRecorderLive.swift").read_text()
recover = recover[recover.index("    func recoverUnfinished() {"):]
check('static let poolStopTitle = "The last start stopped early"' in notice
      and 'static let poolStopWords = "Last time BCD stopped while JIT was being set up for the game: its JIT memory request did not come back. "' in notice
      and "if let words = poolStop {" in start and start.index("if let words = poolStop {") < start.index("MemoryRights.due(")
      and "title = Self.poolStopTitle" in start and start.index('title = "Memory limit"') < start.index("MemoryRights.due(")
      and "if message == nil { title = Self.poolStopTitle; message = Self.poolStopWords }" in stopped,
      "round 624 (N S16): a run that never came back from the JIT memory request is told once, in this box with its own title, before "
      "and never together with the memory-limit words; told at once if the library is already up and the box is free")
check('if let phase, phase.name == "pool-alloc-begin", Date().timeIntervalSince1970 - phase.at < 86400 {' in recover
      and recover.index('phase.name == "pool-alloc-begin"') < recover.index("guard let url = Self.markerURL")
      and "MemoryRightsNotice.shared.lastStartStoppedInPoolRequest()" in recover and "[jit-pool] the last run ended inside the JIT memory request" in recover,
      "... the next start finds it by the phase file alone (pool-alloc-begin, under a day old), with or without a session marker, and logs it")
check(not any(w in code(rights) for w in ("refuse", "throw", "return false")) and "MemoryRights" not in
      code((app / "ContentView.swift").read_text()), "nothing is refused: no start path reads it")
check(not any("MemoryRights" in code(f.read_text()) for f in app.glob("BCD*.swift")
              if f.name not in ("BCDLiveFrontEnd.swift", "BCDGamePageLive.swift", "BCDSettingsLive.swift", "BCDSessionRecorderLive.swift")),
      "only the engine-bound live files name it (the screens get text through their models)")
check(pbx.count("/* MemoryRights.swift in Sources */") == 2 and pbx.count("path = MemoryRights.swift;") == 1,
      "the Xcode project builds MemoryRights.swift")

# Round 625 (owner's item 20, N-S15; V71SS6, 9Z31S2): no Send Log button on auto-tune's alert for an install-limit verdict; the ids
# are strings, so a renamed rule would silently bring the button back: each must exist in the rule table.
_views = (app / "AutoTuneViews.swift").read_text()
_rules = (root / "build/tools/autotune_rules.py").read_text()
check('static let installLimits: Set<String> = ["wow-window-no-address-space", "reserve-no-address-space-without-rights"]' in _views
      and '!(offer.state.status == "known" && Self.installLimits.contains(offer.state.known ?? ""))' in _views
      and 'if ["stuck", "gave-up", "known"].contains(offer.state.status),' in _views
      and '"id": "wow-window-no-address-space"' in _rules and '"id": "reserve-no-address-space-without-rights"' in _rules,
      "auto-tune's alert offers no Send Log for an install-limit verdict (the two rules exist); every other verdict keeps it")

if failures:
    print(f"check-memory-rights-notice: {failures} FAILED")
    sys.exit(1)
print("check-memory-rights-notice: all passed")
