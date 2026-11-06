#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A Steam game's version, its beta branches (app/Madeira/BCDGameVersion.swift, BCDSteamBranches/; docs/STEAM_LIBRARY.md, "Version").

Never contacts Steam. Part A reads the sources: the feature is keyed and does nothing for a game whose player picked no beta, the
public path is the one it was, the screens name no engine type, the beta code is never logged or shown, the files are registered and
carry the right notices. Part B compiles the section's rules (BCDGameVersion.swift) with the page's rules and runs them on synthetic
states. (The engine half is run by tests/host/check-steam-library.py: the branch list, the private section, a whole beta install
and the switch back through a local content server.)
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
LIVE = APP / "BCDSteamBranches"
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


# The install record before version selection existed (written by the writer of that day, from the same arguments), and the same with a beta.
RECORD_PUBLIC = (
    '"AppState"\n'
    '{\n'
    '\t"appid"\t\t"9000"\n'
    '\t"universe"\t\t"1"\n'
    '\t"name"\t\t"Fixture Game"\n'
    '\t"StateFlags"\t\t"4"\n'
    '\t"installdir"\t\t"Fixture Game"\n'
    '\t"LastUpdated"\n'
    '\t"LastPlayed"\t\t"0"\n'
    '\t"SizeOnDisk"\t\t"1234"\n'
    '\t"StagingSize"\t\t"0"\n'
    '\t"buildid"\t\t"1000"\n'
    '\t"LastOwner"\t\t"76561190000000000"\n'
    '\t"DownloadType"\t\t"1"\n'
    '\t"UpdateResult"\t\t"0"\n'
    '\t"BytesToDownload"\t\t"0"\n'
    '\t"BytesDownloaded"\t\t"0"\n'
    '\t"BytesToStage"\t\t"0"\n'
    '\t"BytesStaged"\t\t"0"\n'
    '\t"TargetBuildID"\t\t"1000"\n'
    '\t"AutoUpdateBehavior"\t\t"0"\n'
    '\t"AllowOtherDownloadsWhileRunning"\t\t"0"\n'
    '\t"ScheduledAutoUpdate"\t\t"0"\n'
    '\t"InstalledDepots"\n'
    '\t{\n'
    '\t\t"9001"\n'
    '\t\t{\n'
    '\t\t\t"manifest"\t\t"111"\n'
    '\t\t\t"size"\t\t"1000"\n'
    '\t\t}\n'
    '\t\t"9002"\n'
    '\t\t{\n'
    '\t\t\t"manifest"\t\t"112"\n'
    '\t\t\t"size"\t\t"234"\n'
    '\t\t}\n'
    '\t}\n'
    '\t"SharedDepots"\n'
    '\t{\n'
    '\t\t"9003"\t\t"9100"\n'
    '\t}\n'
    '\t"CheckGuid"\n'
    '\t{\n'
    '\t\t"0"\t\t"tool\\\\custom.bin"\n'
    '\t}\n'
    '\t"UserConfig"\n'
    '\t{\n'
    '\t\t"language"\t\t"german"\n'
    '\t}\n'
    '\t"MountedConfig"\n'
    '\t{\n'
    '\t\t"language"\t\t"german"\n'
    '\t}\n'
    '}'
)
RECORD_BETA = (
    '"AppState"\n'
    '{\n'
    '\t"appid"\t\t"9000"\n'
    '\t"universe"\t\t"1"\n'
    '\t"name"\t\t"Fixture Game"\n'
    '\t"StateFlags"\t\t"4"\n'
    '\t"installdir"\t\t"Fixture Game"\n'
    '\t"LastUpdated"\n'
    '\t"LastPlayed"\t\t"0"\n'
    '\t"SizeOnDisk"\t\t"1234"\n'
    '\t"StagingSize"\t\t"0"\n'
    '\t"buildid"\t\t"1000"\n'
    '\t"LastOwner"\t\t"76561190000000000"\n'
    '\t"DownloadType"\t\t"1"\n'
    '\t"UpdateResult"\t\t"0"\n'
    '\t"BytesToDownload"\t\t"0"\n'
    '\t"BytesDownloaded"\t\t"0"\n'
    '\t"BytesToStage"\t\t"0"\n'
    '\t"BytesStaged"\t\t"0"\n'
    '\t"TargetBuildID"\t\t"1000"\n'
    '\t"AutoUpdateBehavior"\t\t"0"\n'
    '\t"AllowOtherDownloadsWhileRunning"\t\t"0"\n'
    '\t"ScheduledAutoUpdate"\t\t"0"\n'
    '\t"InstalledDepots"\n'
    '\t{\n'
    '\t\t"9001"\n'
    '\t\t{\n'
    '\t\t\t"manifest"\t\t"111"\n'
    '\t\t\t"size"\t\t"1000"\n'
    '\t\t}\n'
    '\t\t"9002"\n'
    '\t\t{\n'
    '\t\t\t"manifest"\t\t"112"\n'
    '\t\t\t"size"\t\t"234"\n'
    '\t\t}\n'
    '\t}\n'
    '\t"SharedDepots"\n'
    '\t{\n'
    '\t\t"9003"\t\t"9100"\n'
    '\t}\n'
    '\t"CheckGuid"\n'
    '\t{\n'
    '\t\t"0"\t\t"tool\\\\custom.bin"\n'
    '\t}\n'
    '\t"UserConfig"\n'
    '\t{\n'
    '\t\t"language"\t\t"german"\n'
    '\t\t"BetaKey"\t\t"preview"\n'
    '\t}\n'
    '\t"MountedConfig"\n'
    '\t{\n'
    '\t\t"language"\t\t"german"\n'
    '\t\t"BetaKey"\t\t"preview"\n'
    '\t}\n'
    '}'
)


# ------------------------------------------------------------------------------ Part A: the sources
page_rules, live = read(APP / "BCDGameVersion.swift"), read(LIVE / "BCDGamePageLiveVersion.swift")
page, page_live = read(APP / "BCDGamePage.swift"), read(APP / "BCDGamePageLive.swift")
info, fetcher = read(APP / "SwiftSteam/Library/SteamAppInfo.swift"), read(APP / "SwiftSteam/Library/SteamLibraryFetcher.swift")
dd, owned = read(APP / "SwiftSteam/Content/DepotDownloader.swift"), read(APP / "SteamOwnedLibrary.swift")
writer, project = read(APP / "SwiftSteam/Install/AppManifestWriter.swift"), read(R / "app/Madeira.xcodeproj/project.pbxproj")

for f in [APP / "BCDGameVersion.swift", LIVE / "BCDGamePageLiveVersion.swift", R / "tests/host/steam_branches_checks.swift",
          R / "tests/host/check-steam-branches.py"]:
    lines = read(f).split("\n")
    check(f"{f.name}: BCD's licence header, exactly", (lines[:3] if f.suffix == ".swift" else [l.replace("# ", "// ", 1) for l in lines[1:4]]) == HEADER)
for f in [APP / "BCDGameVersion.swift", LIVE / "BCDGamePageLiveVersion.swift"]:
    rel = f.relative_to(APP).as_posix()
    check(f"{f.name}: built by the Xcode project (build file, reference and group entry)",
          project.count(f"/* {rel} in Sources */") == 2 and project.count(f"/* {f.name} */") >= 3, rel)
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', read(f))
    check(f"{f.name}: no \"Madeira\" in a string it shows or logs (the env key MADEIRA_STEAM_BRANCHES is the one name that stays)",
          not [x for x in literals if "madeira" in x.lower() and x != "MADEIRA_STEAM_BRANCHES"])
ids = sorted(set(re.findall(r"\b(A[12]000C7[0-9A-F])\b", project)))
check("project IDs A1000C70/C71 and A2000C70/C71 are this feature's, each used for exactly its file", ids == ["A1000C70", "A1000C71", "A2000C70", "A2000C71"], ids)

# Files of others' that BCD changed carry the dated notice and keep their licence lines.
for f in ["SwiftSteam/Library/SteamAppInfo.swift", "SwiftSteam/Library/SteamLibraryFetcher.swift", "SwiftSteam/Content/DepotDownloader.swift",
          "SteamOwnedLibrary.swift", "SwiftSteam/Core/SteamError.swift", "SwiftSteam/Core/SteamProtocol.swift",
          "SwiftSteam/Install/AppManifestWriter.swift", "SteamInstall.swift", "SteamGames.swift"]:
    head = read(APP / f).split("import ", 1)[0]
    check(f"{f}: marked as modified by BCD, with the date (GPL-3.0 section 5)", "Modified by BCD (thebcd69), 2026-11-05" in head)
    lines = head.split("\n")
    check(f"{f}: the original licence lines are untouched",
          lines[0] == "// SPDX-License-Identifier: GPL-3.0-or-later" and lines[1].startswith("// Copyright 2026 ")
          and lines[2] == "// Madeira Converter Exception: see LICENSE-EXCEPTION.md")

# Keyed, and a game whose player picked no beta downloads as before.
check("env.MADEIRA_STEAM_BRANCHES = 0 turns it off (on by default; the section appears only for a game Steam lists a beta for)",
      'static var branchesEnabled: Bool { enabled && SteamSignIn.flag("MADEIRA_STEAM_BRANCHES", default: true) }' in owned)
check("a download takes the public build unless the player chose a beta (and always with the feature off)",
      "Self.branchesEnabled ? SteamBranch.chosen(appID) : SteamAppInfo.publicBranch" in owned
      and "try await fetcher.applyBranch(&info, branch: branchToDownload(appID), code: SteamBranch.code(appID))" in owned)
check("for the public branch applyBranch asks Steam nothing: it returns before any request",
      re.search(r"func applyBranch\(.*?\{\n\s+guard branch != SteamAppInfo.publicBranch, let entry = app.branches.first\(where: \{ \$0.name == branch \}\) else \{\n"
                r".*?app.branch = SteamAppInfo.publicBranch\n\s+return\n\s+\}", fetcher, re.S) is not None)
check("only a protected beta is checked with Steam at download time", "if entry.passwordRequired {" in fetcher and fetcher.count("checkBetaCode(") >= 2)
check("a protected beta without a code Steam accepts stops the install (it never quietly takes the public build)",
      "throw SteamError.betaCodeNeeded(branch)" in fetcher and fetcher.count("throw SteamError.betaCodeNeeded(branch)") == 2)
check("the depot rule for the public branch is the one it was (manifestID(\"public\") is publicManifestID)",
      "d.manifestID(branch) != nil && !d.lowViolence &&" in info and "func manifestID(_ branch: String) -> UInt64? {\n            manifests[branch] ?? publicManifestID" in info)
check("the manifest request names a branch only for a beta's own manifest",
      "if let branch, branch != SteamAppInfo.publicBranch { encoder.writeString(fieldNumber: 4, value: branch) }" in dd
      and "branch: depot.hasOwnManifest(app.branch) ? app.branch : nil" in dd and "branchLog = app.branch == SteamAppInfo.publicBranch ? \"\"" in dd)
check("the install record names a branch only for a beta (UserConfig and MountedConfig), the public record is the one it was",
      'let beta = betaKey == "public" || betaKey.isEmpty ? nil : escapeVDFString(betaKey)' in writer and 'if let beta { lines.append(' in writer
      and "customExecutables: custom, language: app.gameLanguage, betaKey: app.branch)" in dd)
check("a DLC taken away keeps the game on its version: the record is rewritten with its BetaKey",
      "betaKey: state.betaKey)" in read(APP / "BCDSteamDLC/BCDSteamDLC.swift") and "SteamInstallFiles.betaKey(in: record)" in read(APP / "BCDSteamDLC/BCDSteamDLC.swift"))
check("the update check compares with the build of the branch the record names; a public install is told by a newer number only",
      "latest > 0 && latest != installedBuild && (latest > installedBuild || installedBranch != nil)" in owned
      and "installedBranch: SteamGamesModel.shared.branches[item.id])" in read(APP / "BCDLiveStore.swift"))

# The beta code: kept on this device, sent to Steam only, never logged or shown.
logged = [l for l in (info + fetcher + owned + dd + live).split("\n") if "[steam-branch]" in l]
check("no [steam-branch] line carries the beta code", logged and not [l for l in logged if re.search(r"\\\(\s*(code|typed|value)\s*\)", l)], logged)
check("no [steam-branch] line carries account data", not [l for l in logged if re.search(r"accountName|steamID|SteamID|email|token|password", l.replace("passwordRequired", ""))])
check("the code is sent to Steam only (the check request) and kept in the app's defaults",
      fetcher.count("request.writeString(fieldNumber: 2, value: code)") == 1 and "UserDefaults.standard" in info and "URLSession" not in code(live + page_rules))
check("the page never shows the code: the row's value stays empty and the row says Type it in",
      'kind: .text(prompt: "Beta code"), value: ""' in page_rules and 'blank: "Type it in"' in page_rules)
check("the code is checked over BCD's own connection, never during a game session",
      '"Close the game first' in owned and "guard Self.branchesEnabled, signedIn, !inSession" in owned)

# The page: BCD's own section, no engine type in the screens, the live side in a folder the preview build does not read.
check("the screens name no engine type (BCDGameVersion.swift, BCDGamePage.swift)",
      not re.findall(r"\b(SteamOwnedLibrary|SteamBranch|SteamLibraryFetcher|DepotDownloader|SteamGamesModel|SteamSignIn|LibraryModel)\b", code(page_rules + page)))
check("BCDGameVersion.swift is a top-level BCD screen file (the preview build compiles it); the engine side is in BCDSteamBranches/",
      (APP / "BCDGameVersion.swift").is_file() and (LIVE / "BCDGamePageLiveVersion.swift").is_file()
      and "BCDGameVersion.swift" not in read(R / "tools/ui-preview.py") and "BCDGamePageLiveVersion" not in read(R / "tools/ui-preview.py"))
check("the page's live backend calls the version hooks (section before and after install, set, opened, changes)",
      all(h in page_live for h in ["if let version = versionGroup(game) { d.groups.append(version) }",
                                   "if versionSet(setting, to: value, for: game) { return }", "versionOpened(game)", "versionChanges,"]))
check("only a game BCD downloaded itself gets the section (BCD changes only its own library folder)",
      "SteamInstallPaths.isManaged(library: installed.library)" in live and "guard game.state.isInstalled else { return true }" in live)
check("a choice is taken only from the versions the section lists (the public build, an open beta, a protected one whose code was entered)",
      "$0.name == value && (!$0.passwordRequired || unlocked.contains(value))" in live and "guard allowed, steam.downloads[id] == nil else { return true }" in live)
check("the section's Download row is the library's own install (an update's path): nothing new downloads a game",
      "steam.install(id)" in live and "guard steam.signedIn, steam.downloads[id] == nil else { return true }" in live)
check("the branch lookup is once a run, signed in, outside a game session, and only for a managed game",
      "branchesRead.insert(appID).inserted" in owned and "guard SteamOwnedLibrary.branchesEnabled, let id = versionAppID(game), versionManaged(game, id) else { return }" in live)
check("no purchase anywhere in the section's words", not [x for x in re.findall(r'"((?:[^"\\]|\\.)*)"', page_rules) if re.search(r"\b(buy|purchase|price|cart)\b", x, re.I)])
check("a text setting shows its own words while empty only when asked (the default stays None)",
      'case .text: return value.isEmpty ? (blank ?? "None") : value' in page and "var blank: String? = nil" in page)

# CI and the other checks that compile the page rules.
ci = read(R / ".github/workflows/build-ipa.yml")
check("CI runs this check", "run: python3 tests/host/check-steam-branches.py --require-swift" in ci)

# ------------------------------------------------------------------------------ Part B: the section, compiled
if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift parts", not args.require_swift)
else:
    start = page.index("// MARK: - The page's rules")
    end = page.index("// MARK: - Opening the page")
    with tempfile.TemporaryDirectory(prefix="steam-branches-") as tmp:
        t = Path(tmp)
        (t / "BCDStore.swift").write_text(read(APP / "BCDStore.swift"))
        (t / "BCDGameDLC.swift").write_text(read(APP / "BCDGameDLC.swift"))   # the page's rules name the DLC question
        (t / "BCDGameVersion.swift").write_text(page_rules)
        (t / "Rules.swift").write_text("import Combine\nimport Foundation\nimport SwiftUI\n\n" + page[start:end])
        shutil.copy(R / "tests/host/steam_branches_checks.swift", t / "Main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(t / "rules"),
                            str(t / "BCDStore.swift"), str(t / "BCDGameDLC.swift"), str(t / "BCDGameVersion.swift"), str(t / "Rules.swift"), str(t / "Main.swift")],
                           capture_output=True, text=True)
        check("the section's rules compile on their own with the page's rules and BCDStore.swift", r.returncode == 0, (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            r = subprocess.run([str(t / "rules"), "-AppleLocale", "en_US", "-AppleLanguages", "(en)"], capture_output=True, text=True, timeout=120)
            print("\n".join("     " + line for line in r.stdout.strip().split("\n")))
            check("the section's rules hold (the choice, the download row, the beta code, the words)", r.returncode == 0, (r.stdout + r.stderr)[-2500:])

    # The install record: a public install is the record of before, byte for byte; a beta names its branch in both sections.
    with tempfile.TemporaryDirectory(prefix="steam-branches-record-") as tmp:
        t = Path(tmp)
        (t / "stubs.swift").write_text("import Foundation\n")
        shutil.copy(R / "tests/host/steam_branches_record.swift", t / "main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(t / "writer"), str(t / "main.swift"),
                            str(APP / "SwiftSteam/Install/AppManifestWriter.swift"), str(APP / "SteamKeyValues.swift")], capture_output=True, text=True)
        check("the install record writer compiles on its own with the key-value reader", r.returncode == 0, (r.stderr or r.stdout)[-2000:])
        if r.returncode == 0:
            (t / "public").mkdir()
            (t / "beta").mkdir()
            public = subprocess.run([str(t / "writer"), str(t / "public")], capture_output=True, text=True, timeout=60).stdout
            beta = subprocess.run([str(t / "writer"), str(t / "beta"), "preview"], capture_output=True, text=True, timeout=60).stdout
            check("a public install's record is the one written before version selection, byte for byte", public == RECORD_PUBLIC, public)
            check("a beta install's record names the beta in UserConfig and MountedConfig, and is otherwise that record", beta == RECORD_BETA, beta)

print("PASS: Steam versions" if ok else "FAIL: Steam versions")
sys.exit(0 if ok else 1)
