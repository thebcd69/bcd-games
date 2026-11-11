#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright 2026 Paulo Eduardo Correa (pauloec): the native iPhone Duo interface (Liquid Glass, system tabs, controller navigation, Windows desktop on Home), from thebcd69/bcd-games pull request #43; GPL-3.0-only, modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""BCD's menus: a game's menu on its card (app/Madeira/BCDMenu.swift; docs/BCD-UI-ARCHITECTURE.md)
and the Library's sort menu.

Checks:
  - BCD's licence header, no engine type, the Xcode project builds BCDMenu.swift;
  - a game's menu opens on a long press of a Library card, of a recent game on Home and of Home's
    Continue playing banner, and on X while a controller's ring is on one of them; X is free there
    and its hint ("Menu") shows only on a card;
  - the menu is Play (or Install, Pause, Resume), Update when there is one, Game page, and
    Uninstall or Remove from library when the game has it, from the game page's own details and plan,
    through the same store actions; Uninstall and Remove ask first, in the game page's words;
  - it holds the controller as a layer (A, B, the D-pad reach only its rows) and gives the shell's
    buttons and ring back; the shell's buttons are not reinstalled over a layer or Settings; a
    control's registration belongs to the latest view of its id (a turn of the device replaces views);
  - a card holds one Bool for this and nothing is built until a menu opens (memory first);
  - no animation that runs by itself, none longer than 0.25 s;
  - the menu's rules, compiled on this Mac and run;
  - the preview build compiles (tools/ui-preview.py --build-only; --no-preview leaves it out);
  - CI runs this check.
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
MENU = APP / "BCDMenu.swift"
HEADER = ["// SPDX-License-Identifier: GPL-3.0-only",
          "// Copyright 2026 thebcd69 (BCD)",
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md."]
ENGINE = ["LibraryModel", "LibraryEntry", "LibraryDetail", "LibraryController", "SteamOwnedLibrary", "SteamGamesModel",
          "SteamSignInModel", "MadeiraDock", "MadeiraDockModel", "MadeiraConfig", "GameProfile", "GameRecommendations",
          "LogStore", "ContentView", "BCDLiveStore", "BCDLiveID", "BCDGamePageLive", "SteamCatalog"]

ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
ap.add_argument("--no-preview", action="store_true")
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


def code(text):
    return "\n".join(re.split(r"(?<!:)//", line, maxsplit=1)[0] if not line.strip().startswith("///") else ""
                     for line in text.split("\n"))


def bare(text):
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', code(text))


menu = MENU.read_text()
shell = (APP / "BCDFrontEnd.swift").read_text()
cards = (APP / "BCDComponents.swift").read_text()
home = (APP / "BCDHomeView.swift").read_text()
library = (APP / "BCDLibraryView.swift").read_text()
page = (APP / "BCDGamePage.swift").read_text()
focus = (APP / "BCDFocus.swift").read_text()

# ---- the file ------------------------------------------------------------------------------------
check("BCDMenu.swift starts with BCD's licence header", menu.split("\n")[:3] == HEADER)
named = sorted({t for t in ENGINE if re.search(r"\b" + t + r"\b", bare(menu))})
check("BCDMenu.swift names no engine type", not named, named)
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the Xcode project builds BCDMenu.swift (build file, file reference, group, Sources)",
      pbx.count("/* BCDMenu.swift in Sources */") == 2 and pbx.count("path = BCDMenu.swift;") == 1
      and pbx.count("/* BCDMenu.swift */") == 3)

# ---- touch: a long press ----------------------------------------------------------------------------
check("a long press of a card opens the game's menu (and the tap that ends it opens nothing)",
      "LongPressGesture(minimumDuration: 0.45)" in menu and "menu.showCard(gameID)" in menu
      and "if held { held = false } else { tap() }" in menu and "if !down { DispatchQueue.main.async(execute: released) }" in menu)
check("a card has a VoiceOver action for the menu", '.accessibilityAction(named: "Game menu")' in menu)
check("Library cards and Home's recent games are BCDGameButtons (BCDCoverCard)",
      "BCDGameButton(gameID: game.id, tap: open) {" in cards)
check("Home's Continue playing banner is a BCDGameButton",
      "BCDGameButton(gameID: game.id, tap: { store.actions?.play(game) }) {" in home)
check("Home and the Library let the menu open a game's page over themselves",
      ".onAppear { menu?.openPage = { opened = $0 }; installRecentNavigation() }" in home and ".onAppear { menu?.openPage = { opened = $0 } }" in library)

# ---- controller: X ----------------------------------------------------------------------------------
check("X opens the menu of the game under the ring, on Home and the Library, with the hint \"Menu\"",
      'if tab != .downloads, let game = BCDMenuModel.gameID(focused: focus.focused, store: store) {' in shell
      and 'BCDHint(button: .x, title: "Menu"), { cardMenu.showCard(game) }' in shell)
check("a card's focus ids name its game: library.game.<id>, home.recent.<id>, home.continue",
      'static let cardPrefixes = ["library.game.", "home.recent.", "home.continue."]' in menu and 'static let continueID = "home.continue"' in menu
      and 'focusID: "library.game.\\(game.id)"' in library and 'focusID: "home.recent.\\(game.id)"' in home
      and 'desktopAsUtility ? "home.continue.\\(game.id)" : "home.continue"' in home)
check("the hint follows the ring", ".onChange(of: focus.focused) { _, _ in installButtons() }" in shell)
check("the shell's buttons are not set over a page, a menu or Settings",
      "guard !focus.layerOpen, !showSettings else { return }" in shell and "var layerOpen: Bool { !layers.isEmpty }" in focus)
check("the shell's buttons come back when Settings closes (it clears them as it goes)",
      "onDismiss: { installButtons(); BCDPresentation.didClose() }" in shell)
check("a control's registration belongs to the latest view of its id: a turn of the device builds the new view before the old one goes, and the old one's going leaves the new one's action, frame and ring alone",
      "if let owner, let latest = owners[id], latest != owner { return }" in focus
      and "func setAction(_ id: String, _ action: @escaping () -> Void, owner: UUID? = nil)" in focus
      and "focus.setAction(id, action, owner: owner)" in focus and "focus.unregister(id, owner: owner)" in focus
      and "@State private var owner = UUID()" in focus)
check("the menu is a layer: the D-pad and A reach only its rows; B closes; the shell's buttons and ring come back",
      'static let layer = "menu."' in menu and "focus.openLayer(prefix)" in menu and "focus.closeLayer(prefix)" in menu
      and 'BCDHint(button: .b, title: "Close"), { model.dismiss() }' in menu
      and 'BCDHint(button: .b, title: "Cancel"), { model.answer(false) }' in menu
      and '.bcdFocusable(prefix + "item." + item.id' in menu)
check("what a choice does runs after the controller is back under the menu",
      "model.closed()?()" in menu and menu.index("focus.closeLayer(prefix)") < menu.index("model.closed()?()"))
check("a question that deletes starts on Cancel", '(asked.question.destructive || asked.question.defaultCancel) ? prefix + "ask.no" : prefix + "ask.yes"' in menu)
check("a menu that is on screen when the shell goes lets the controller go (a game starts)",
      ".onDisappear {" in menu and "model.dismiss()" in menu)
check("the shell's hint bar gives way to the menu's own", ".opacity(focus.layerOpen ? 0 : 1)" in shell and "ForEach(focus.hints) { BCDHintView(hint: $0) }" in menu)
check("the menu sits inside the environment it reads (focus, layout)",
      shell.index(".modifier(BCDMenuHost(model: cardMenu))") < shell.index(".environmentObject(focus)")
      and shell.index(".modifier(BCDMenuHost(model: cardMenu))") < shell.index(".environment(\\.bcdLayout, layout)"))

# ---- what it offers ---------------------------------------------------------------------------------
check("the items are the game page's: its plan's main action and Update, Game page, and its removal",
      "BCDGameDetailSource.backend?.details(for: game, settings: false)" in menu and "BCDGamePagePlan(game: game," in menu
      and "if let main = plan.main { add(main.action, main.title, main.symbol) }" in menu
      and "plan.others.first(where: { $0.action == .update })" in menu
      and 'id: "page", title: "Game page"' in menu
      and 'add(.uninstall, "Uninstall", "trash"' in menu and 'add(.removeFromLibrary, "Remove from library", "minus.circle"' in menu)
check("Uninstall and Remove ask first, with the page's own question", "question: action.question(for: game, details: details)" in menu)
check("the question is the page's view, under its own focus ids",
      "BCDGameQuestionView(question: asked.question, prefix: prefix" in menu and 'var prefix = "game.ask."' in page)
perform = menu[menu.index("private static func perform"):]
for call in ("actions?.play(game)", "actions?.install(game, place: place)", "actions?.update(game)", "actions?.pauseDownload(game.id)",
             "actions?.resumeDownload(game.id)", "actions?.verify(game)", "actions?.uninstall(game)"):
    check(f"a choice calls the store's {call.split('?.')[1].split('(')[0]}", call in perform, call)
plain = code(menu)
check("every button of the menu can be reached with a controller",
      len(re.findall(r"\bButton\b[ ({]", plain)) <= len(re.findall(r"\.bcdFocusable\(", plain)) + 1,
      "Button %d, .bcdFocusable( %d" % (len(re.findall(r"\bButton\b[ ({]", plain)), len(re.findall(r"\.bcdFocusable\(", plain))))

# ---- memory and cool running ----------------------------------------------------------------------------
check("a card holds one Bool for the menu; the model is a reference nothing observes but the overlay",
      "@State private var held = false" in menu and "@State private var cardMenu = BCDMenuModel()" in shell
      and "@Environment(\\.bcdMenu) private var menu" in menu)
for bad in ("repeatForever", "ProgressView(", "TimelineView", "Timer.", "AsyncImage", ".blur(", "Material", "CADisplayLink", "contextMenu"):
    check(f"BCDMenu.swift: no {bad}", bad not in plain)
long = [d for d in re.findall(r"duration: ([0-9.]+)", plain) if float(d) > 0.25]
check("BCDMenu.swift: animations of 0.25 s or less", not long, long)
literals = re.findall(r'"((?:[^"\\]|\\.)*)"', code(menu))
check("BCDMenu.swift: no \"Madeira\" in its text", not [s for s in literals if "madeira" in s.lower()])

# ---- the menu's rules, compiled and run ---------------------------------------------------------------------
swiftc = shutil.which("xcrun")
if not swiftc:
    check("xcrun swiftc is here for the Swift parts", not args.require_swift)
else:
    rules_start = page.index("// MARK: - The page's rules")
    rules_end = page.index("// MARK: - Opening the page")
    model_end = menu.index("// MARK: - A game's button")
    with tempfile.TemporaryDirectory(prefix="bcd-menu-") as tmp:
        t = Path(tmp)
        (t / "BCDStore.swift").write_text((APP / "BCDStore.swift").read_text())
        (t / "BCDGameDLC.swift").write_text((APP / "BCDGameDLC.swift").read_text())   # the page's model asks the DLC question
        (t / "Rules.swift").write_text("import Combine\nimport Foundation\nimport SwiftUI\n\n" + page[rules_start:rules_end])
        (t / "Menu.swift").write_text("import SwiftUI\n\n" + menu[menu.index("// MARK: - What a menu holds"):model_end])
        (t / "Main.swift").write_text((Path(__file__).resolve().parent / "bcd_menu_checks.swift").read_text())
        # The question's view is the page's (BCDGamePage.swift, outside the rules): the model needs only its type.
        r = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(t / "rules")]
                           + [str(t / n) for n in ("BCDStore.swift", "BCDGameDLC.swift", "Rules.swift", "Menu.swift", "Main.swift")],
                           capture_output=True, text=True)
        check("the menu's rules compile on their own with BCDStore.swift and the page's rules", r.returncode == 0, (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            r = subprocess.run([str(t / "rules"), "-AppleLocale", "en_US", "-AppleLanguages", "(en)"],
                               capture_output=True, text=True, timeout=120)
            print("\n".join("     " + line for line in r.stdout.strip().split("\n")))
            check("the menu's rules hold (items, questions, actions, X)", r.returncode == 0, r.stderr[-2000:])
    if args.no_preview:
        print("SKIP the preview build (--no-preview)")
    else:
        with tempfile.TemporaryDirectory(prefix="bcd-menu-preview-") as tmp:
            r = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp],
                               capture_output=True, text=True, timeout=1800)
            check("the preview build compiles (tools/ui-preview.py --build-only)", r.returncode == 0, (r.stdout + r.stderr)[-3000:])

CI = R / ".github/workflows/build-ipa.yml"
if CI.exists():
    check("CI runs this check", "run: python3 tests/host/check-bcd-menu.py --require-swift" in CI.read_text())

print("PASS: BCD menus" if ok else "FAIL: BCD menus")
sys.exit(0 if ok else 1)
