#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A Done button on the keyboard where config lines and long text are typed (BCDKeyboardDone in BCDComponents.swift).

The keyboard of a multi-line editor has a return key that adds a line, so it could not be closed from the keyboard. Each
place the BCD interface edits config text or long text now has a Done button on the keyboard's bar that closes the keyboard
and keeps the text (a field that saves when it loses focus saves); tapping outside works as before. Checks:
  1. BCDKeyboardDone: a toolbar item group in the keyboard placement, a Spacer and a Done button, which ends the screen's
     own focus (when it gives one) and makes the first responder let go; the modifier is plain SwiftUI and UIKit.
  2. Every editor: each Swift file under app/Madeira that has a TextEditor or a TextField with axis: .vertical, and the files
     whose fields hold config lines (All settings: the lines of bcd.cfg and your own), applies .bcdKeyboardDone, once for
     the screen (the keyboard's bar collects the buttons of every view in the screen, so one for each field would show
     several). The editors this finds are listed, so a new one fails here until it has the button.
  3. Nothing else changes: no other place gets one (the single-line fields have a return key), no text is changed by it,
     and the one existing Done beside the feedback counter is still there.
"""
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-2000:]) if detail and not cond else ""))
    ok = ok and bool(cond)


comp = (APP / "BCDComponents.swift").read_text()
mod = comp[comp.index("struct BCDKeyboardDone: ViewModifier"):]
check("BCDKeyboardDone is a toolbar item group in the keyboard's placement", "ToolbarItemGroup(placement: .keyboard)" in mod)
check("... with a Spacer then a Done button", re.search(r"Spacer\(\)\s*Button\(\"Done\"\)", mod) is not None)
check("... which ends the screen's own focus, then makes the first responder let go (the text stays)",
      "closing?()" in mod and "#selector(UIResponder.resignFirstResponder)" in mod and mod.index("closing?()") < mod.index("resignFirstResponder"))
check("... and does not touch the text", "text" not in re.sub(r"//[^\n]*", "", mod[:mod.index("enum BCDKeyboardDoneBar")]).replace("context", ""))
# 2026-10-24 (the owner, iOS 27: the Game config sheet's keyboard had no Done): SwiftUI's keyboard bar does not show outside a
# navigation stack, and the game page (a full-screen cover) and its sheets are not in one. A multi-line editor that starts editing
# without a bar gets the same Done from UIKit.
bar = comp[comp.index("enum BCDKeyboardDoneBar"):comp.index("extension View {", comp.index("enum BCDKeyboardDoneBar"))]
check("the modifier installs the UIKit fallback when its screen appears", ".onAppear { BCDKeyboardDoneBar.install() }" in mod)
check("the fallback watches multi-line editors only (UITextView), once", "UITextView.textDidBeginEditingNotification" in bar
      and "UITextField" not in re.sub(r"//[^\n]*", "", bar) and "guard observer == nil else { return }" in bar)
check("... adds its bar only to an editable editor that has the keyboard and no bar of its own (SwiftUI's, where it shows)",
      "guard editor.isFirstResponder, editor.isEditable, editor.inputAccessoryView == nil else { return }" in bar
      and "editor.reloadInputViews()" in bar)
check("... whose Done makes the first responder let go and does not touch the text",
      'UIBarButtonItem(title: "Done"' in bar and "#selector(UIResponder.resignFirstResponder)" in bar
      and not re.search(r"\.text\b", re.sub(r"//[^\n]*", "", bar)))
check("the view modifier is bcdKeyboardDone(_ closing:)", "func bcdKeyboardDone(_ closing: (() -> Void)? = nil) -> some View" in comp)

# every editor of long text
found = {}
for f in sorted(APP.glob("*.swift")):
    text = re.sub(r"//[^\n]*", "", f.read_text())
    n = len(re.findall(r"\bTextEditor\(", text)) + len(re.findall(r"axis: \.vertical", text))
    if n:
        found[f.name] = n
expected = {"BCDGamePage.swift": 2, "BCDSettingsFeedback.swift": 1, "GameProfiles.swift": 1}
check("the multi-line editors are the game page's two (a setting typed as text, the game's config), the feedback message and the "
      "raw game config editor", found == expected, found)
screens = {
    "BCDGamePage.swift": 2,        # BCDGameTextPage, BCDGameConfigPage
    "BCDSettingsFeedback.swift": 1,
    "GameProfiles.swift": 1,
    "BCDSettingsAll.swift": 1,     # the lines of bcd.cfg: each option's value and "Add a line"
    "BCDAccounts.swift": 1,        # round 632: Steam's "PC address" field (downloads from a PC on this network); a decimal pad has no Return key
}
for name, count in screens.items():
    text = (APP / name).read_text()
    got = len(re.findall(r"\.bcdKeyboardDone\s*\{", text))
    check(f"{name} has the Done button {count} time(s)", got == count, got)
page = (APP / "BCDGamePage.swift").read_text()
text_page = page[page.index("private struct BCDGameTextPage"):page.index("private struct BCDGameConfigPage")]
config_page = page[page.index("private struct BCDGameConfigPage"):page.index("private struct BCDGameStoreSearchPage")]
check("a setting typed as text: Done ends its focus", ".bcdKeyboardDone { typing = false }" in text_page and ".focused($typing)" in text_page)
check("the game's config: its TextEditor has a focus state and Done ends it",
      "@FocusState private var typing: Bool" in config_page and ".focused($typing)" in config_page and ".bcdKeyboardDone { typing = false }" in config_page)
check("the game's config keeps Save and Cancel as they were", 'Button("Save", action: save)' in config_page and 'Button("Cancel", action: cancel)' in config_page)
allset = (APP / "BCDSettingsAll.swift").read_text()
check("All settings: one Done for the screen, ending its typing focus (the rows' own focus ends when the keyboard lets go)",
      ".bcdKeyboardDone { typing = nil }" in allset and "TextField(entry.defaultValue" in allset)
fb = (APP / "BCDSettingsFeedback.swift").read_text()
check("the feedback message keeps the Done beside its counter and has the keyboard's", 'Text("Done")' in fb and ".bcdKeyboardDone { typing = false }" in fb)
gp = (APP / "GameProfiles.swift").read_text()
check("the raw game config editor has a focus state and the Done", "@FocusState private var typing: Bool" in gp and ".bcdKeyboardDone { typing = false }" in gp)
acc = (APP / "BCDAccounts.swift").read_text()
check("Steam's PC address field (a decimal pad, no Return key): Done ends its focus",
      ".bcdKeyboardDone { lanFieldFocused = false }" in acc and ".focused($lanFieldFocused)" in acc)
others = [f.name for f in APP.glob("*.swift") if ".bcdKeyboardDone" in f.read_text() and f.name not in set(screens) | {"BCDComponents.swift"}]
check("no other file has one", not others, others)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs this check", "tests/host/check-bcd-keyboard-done.py" in wf)
sys.exit(0 if ok else 1)
