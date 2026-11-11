#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Save backups per game (round 603): app/Madeira/BCDSaveBackups/, BCDGameSaves.swift.

Never starts a game and never touches the real prefix. Part A reads the sources and pins the promises: the hooks are
one line each, the switch is off unless the player turns it on, nothing is deleted except the automatic rotation, no
file is written outside drive_c, no date is made with an ICU formatter, no word of the old name is shown.
Part B compiles the rules, the file side and the job on this Mac (with the production zip writer and reader cut out of
SavesAndShortcuts.swift) and runs them on a prefix built in a scratch folder: where the saves are found from a session's
changes, the backup zip, the restore (other files stay, hostile entry names refused), the names, the rotation, the
size limits, the automatic job (new saves, unchanged saves, nothing known). Part C compiles the section's words and
rows (BCDGameSaves.swift) with the page's rules and runs them.
Run with python3; --require-swift fails when xcrun swiftc is missing (the macOS build runner).
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
SAVES = APP / "BCDSaveBackups"
HEADER = ["// SPDX-License-Identifier: GPL-3.0-only",
          "// Copyright 2026 thebcd69 (BCD)",
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md."]
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)))
    ok &= bool(cond)


def read(path):
    return Path(path).read_text(encoding="utf-8")


def code(text):
    """The source without comments and string contents, for 'names no X' checks."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r'"(?:[^"\\]|\\.)*"', '""', line.split("//")[0]) for line in text.split("\n"))


# ------------------------------------------------------------------------------ Part A: the sources
files = [SAVES / "GameSaveRules.swift", SAVES / "GameSaveBackups.swift", SAVES / "GameSaveWork.swift", SAVES / "GameSaveAuto.swift",
         SAVES / "BCDGamePageLiveSaves.swift", APP / "BCDGameSaves.swift"]
project = read(R / "app/Madeira.xcodeproj/project.pbxproj")
for f in files:
    check(f"{f.name}: BCD's licence header, exactly", read(f).split("\n")[:3] == HEADER)
    rel = f.relative_to(APP).as_posix()
    check(f"{f.name}: built by the Xcode project (build file, reference and group entry)",
          project.count(f"/* {rel} in Sources */") == 2 and project.count(f"/* {f.name} */") >= 3, rel)
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', read(f))
    check(f"{f.name}: no \"Madeira\" in a string it shows or logs (a folder name the rules skip, \"madeira\", is data)",
          not [x for x in literals if "madeira" in x.lower() and x != "madeira"])
    check(f"{f.name}: never names the external drive", "ssd" not in code(read(f)).lower() and "external" not in code(read(f)).lower())

rules = read(SAVES / "GameSaveRules.swift")
backups = read(SAVES / "GameSaveBackups.swift")
work = read(SAVES / "GameSaveWork.swift")
auto = read(SAVES / "GameSaveAuto.swift")
live = read(SAVES / "BCDGamePageLiveSaves.swift")
ui = read(APP / "BCDGameSaves.swift")
for name, text in (("GameSaveRules", rules), ("GameSaveBackups", backups), ("GameSaveWork", work), ("BCDGameSaves", ui)):
    imports = set(re.findall(r"^import (\w+)", text, re.M))
    check(f"{name}.swift: Foundation only (the host check compiles it on this Mac)", imports == {"Foundation"}, imports)
check("the engine side lives in the folder the preview build does not read", live and (SAVES / "BCDGamePageLiveSaves.swift").exists()
      and not list(APP.glob("*Saves*Live*.swift")))

# The hooks into older files: one line each.
page_live = read(APP / "BCDGamePageLive.swift")
check("BCDGamePageLive: the group is appended once, only for a game with its own program, only while Options is open",
      page_live.count("saveBackupsGroup(game, entry)") == 1
      and "if !desktop { d.groups.append(saveBackupsGroup(game, entry)) }" in page_live
      and page_live.index("dlcGroup(game)") < page_live.index("saveBackupsGroup(game, entry)") < page_live.index("libraryGroup(entry, gog:"))
check("BCDGamePageLive: set() hands the section's rows over once", page_live.count("saveBackupsSet(setting, to: value, for: game)") == 1
      and page_live.index("saveBackupsSet(setting") < page_live.index("let on = value == \"1\""))
library = read(APP / "Library.swift")
check("LibraryModel.finish() calls the hook once", library.count("GameSaveAuto.sessionEnded(activeEntry, started: launchStarted)") == 1)
finish = library[library.index("    private func finish() {"):library.index("    /// madeira-bcd: what auto-tune needs from a session that ended")]
check("and it runs before the session is torn down (the entry and the start time are still there)",
      finish.index("GameSaveAuto.sessionEnded") < finish.index("current = nil; activeEntry = nil"))
check("only the hook and the section appear in the older files (no other file names GameSave*)",
      all("GameSave" not in read(f) for f in APP.glob("*.swift")
          if f.name not in ("BCDGameSaves.swift",) and f.name not in ("Library.swift", "BCDGamePageLive.swift", "BCDGamePageLiveSaves.swift",
                                                                 # BCDGameRekey (build 635): a game that moves keeps its record
                                                                 "LibraryBCD.swift")))

# The promises.
check("the automatic backup is off unless the player turns it on", "var automatic = false" in work and 'value: s.automatic ? "1" : "0", defaultValue: "0"' in ui)
check("turning it on asks first", re.search(r'BCDGameChoice\(value: "1", label: "On: keep the newest \\\(s\.keep\)", question: autoQuestion', ui) is not None)
check("a restore asks first (the row carries the question) and keeps the way back (a Before restore backup, made first)",
      "question: restoreQuestion(item)" in ui and live.index("kind: .beforeRestore") < live.index("GameSaveBackups.restore(backup.url"))
check("a restore stops when the copy of what is there cannot be made (the copy is made with try)", "before = try GameSaveBackups.backUp(" in live)
removals = [m.start() for m in re.finditer(r"removeItem", code(backups))]
check("files are removed in two places only: the half-written zip, and the automatic rotation",
      len(removals) == 2 and "func rotate" in backups[backups.rindex("func ", 0, removals[-1] + 40) - 10:removals[-1] + 40] or
      (len(removals) == 2 and backups.index("defer { try? fm.removeItem(at: partial) }") > 0 and "entry.kind == .automatic" in backups))
check("rotation only removes automatic backups (twice checked: the rule and the loop)", "entry.kind == .automatic" in backups and "$0.kind == .automatic" in rules)
check("a restore writes only below drive_c by safe paths, and skips what is not", "GameSaveRules.safeParts(entry.name)" in backups and "refused += 1" in backups)
check("a backup is written to a hidden partial file and moved: a name is never written over", ".partial-" in backups and "moveItem(at: partial, to: final)" in backups)
check("the session hook records times for every game and scans only when the game's switch is on",
      auto.index("GameSaveStore.rememberSession") < auto.index("guard GameSaveStore.record(key).automatic else { return }") < auto.index("startAutomatic"))
check("a session that ended in a Windows error is not backed up", "wine_crash_exit_status" in auto and "guard !crashed else" in auto)
check("nothing runs on the main thread but the lookups: the scan and the zip run detached",
      "Task.detached(priority: .utility)" in auto and live.count("Task.detached(priority: .userInitiated)") >= 3)
check("saves from the Steam Cloud list come only from the comparison BCD already made (no network call here)",
      "SteamOwnedLibrary.shared.cloud[appID]" in auto and "fetchAppInfo" not in auto and "URLSession" not in auto + backups + work + live)
check("the log carries counts and the title, not the Windows user (paths go through logPath or are not logged)",
      "logPath" in rules and "[saves]" in backups and "[saves]" in work and "root.path" not in "".join(re.findall(r'log\([^\n]*', backups + work + auto)))
check("no date is made with an ICU formatter in the new files", not re.search(r"DateFormatter|ISO8601|\.formatted\(", code(rules + backups + work + auto + live + ui)))

workflow = read(R / ".github/workflows/build-ipa.yml")
check("CI runs this check", "python3 tests/host/check-game-saves.py" in workflow)
ui_preview = read(R / "tools/ui-preview.py")
check("the preview build still reads BCDGameSaves.swift (it names no engine type) and not the engine folder",
      "BCDGameSaves.swift" not in ui_preview and "APP.glob(\"BCD*.swift\")" in ui_preview
      and not re.search(r"\b(LibraryEntry|LibraryModel|SteamOwnedLibrary|LogStore|GameSave)\w*", code(ui)))

# ------------------------------------------------------------------------------ Part B and C: compiled and run
if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift parts", not args.require_swift)
else:
    zip_src = read(APP / "SavesAndShortcuts.swift")
    writer = zip_src[zip_src.index("final class SaveZipWriter {"):zip_src.index("// MARK: - Zip reading")]
    reader = zip_src[zip_src.index("struct SaveZipReader {"):zip_src.index("// MARK: - Save backups")]
    support = ("import Foundation\nimport Compression\nimport zlib\n\nenum LibraryError: LocalizedError {\n    case message(String)\n"
               "    var errorDescription: String? { if case let .message(text) = self { return text }; return nil }\n}\n\n" + writer + "\n" + reader)
    with tempfile.TemporaryDirectory(prefix="game-saves-") as tmp:
        t = Path(tmp)
        (t / "ZipSupport.swift").write_text(support)
        shutil.copy(R / "tests/host/game_saves_host.swift", t / "main.swift")
        sources = [t / "main.swift", t / "ZipSupport.swift", SAVES / "GameSaveRules.swift", SAVES / "GameSaveBackups.swift", SAVES / "GameSaveWork.swift"]
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "saves")] + [str(s) for s in sources], capture_output=True, text=True)
        check("the rules, the file side and the job compile on their own with the production zip writer and reader", r.returncode == 0, (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            r = subprocess.run([str(t / "saves")], capture_output=True, text=True, timeout=300)
            print("\n".join("     " + line for line in r.stdout.strip().split("\n") if line.startswith("FAIL") or line.startswith("     ") or "checks," in line))
            check("save backups hold (roots, backups, restore, rotation, limits, the job)", r.returncode == 0, (r.stdout + r.stderr)[-2500:])

        # the section's words and rows, with the page's rules
        page = read(APP / "BCDGamePage.swift")
        start = page.index("// MARK: - The page's rules")
        end = page.index("// MARK: - Opening the page")
        (t / "Rules.swift").write_text("import Combine\nimport Foundation\nimport SwiftUI\n\n" + page[start:end])
        shutil.copy(R / "tests/host/game_saves_ui_host.swift", t / "main.swift")
        for n in ("BCDStore.swift", "BCDGameDLC.swift", "BCDGameSaves.swift"):
            shutil.copy(APP / n, t / n)
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "ui")]
                           + [str(t / n) for n in ("BCDStore.swift", "BCDGameDLC.swift", "BCDGameSaves.swift", "Rules.swift", "main.swift")],
                           capture_output=True, text=True)
        check("the section's rows compile with the page's rules", r.returncode == 0, (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            r = subprocess.run([str(t / "ui"), "-AppleLocale", "en_US", "-AppleLanguages", "(en)"], capture_output=True, text=True, timeout=120)
            print("\n".join("     " + line for line in r.stdout.strip().split("\n") if line.startswith("FAIL") or line.startswith("     ") or "checks," in line))
            check("the section's rows hold (rows, questions, notes, limits)", r.returncode == 0, (r.stdout + r.stderr)[-2500:])

print("PASS: game saves" if ok else "FAIL: game saves")
sys.exit(0 if ok else 1)
