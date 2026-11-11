#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Steam game language and the download and install sizes: nothing changes unless a language is chosen.

Steam downloads always took the common and English depots and wrote `english` into the install record. A game can now
have its own language (Settings › Accounts › Steam, or the game's page). BCD keeps English as the default, so an
installed game, an update and a download made without choosing are exactly as before; "device" is a choice of its own.

Part A reads the sources: the default is English (not the device's language), the DLC wrapper and the downloader follow the
language, the record writer takes it, BCD's pages offer it only for Steam games.
Part B compiles the production SteamAppInfo.swift, SteamKeyValues.swift, SteamInstall.swift, AppManifestWriter.swift,
BCDSteamDLC.swift and the VDF parser with stand-ins for the log and the config, and runs tests/host/steam_language_checks.swift
(the default, each choice, the DLC depots, the install record both ways, the device mapping, the sizes).
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
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if detail and not cond else ""))
    if not cond:
        ok = False


def read(p):
    return Path(p).read_text()


info = read(APP / "SwiftSteam/Library/SteamAppInfo.swift")
dd = read(APP / "SwiftSteam/Content/DepotDownloader.swift")
dlc = read(APP / "BCDSteamDLC/BCDSteamDLC.swift")
fetcher = read(APP / "SwiftSteam/Library/SteamLibraryFetcher.swift")
writer = read(APP / "SwiftSteam/Install/AppManifestWriter.swift")

check("the language setting unset means English, not the device's language",
      'guard let value = chosen else { return "english" }' in info and "chosen ?? device" not in info)
check("  'device' is a choice of its own", 'static let deviceChoice = "device"' in info and "value == deviceChoice ? device : value" in info)
check("the depot selection takes the game language, English when the game has no depots for it",
      "func installDepots(os: String = \"windows\", arch: String = \"64\",\n                       language: String? = nil) -> [DepotInfo] {\n        let language = language ?? depotLanguage" in info
      and 'return depots.contains { $0.language.caseInsensitiveCompare(wanted) == .orderedSame } ? wanted : "english"' in info)
check("the downloader logs the language and gives the record the language it installed",
      "language=\\(app.depotLanguage)/\\(app.gameLanguage)" in dd and dd.count("language: app.gameLanguage") == 2)
check("the record writer takes a language, English by default", writer.count('language: String = "english"') == 2
      and writer.count('\\"language\\"\\t\\t\\"\\(language)\\"') == 2)
check("BCD's DLC depot rules follow the language too (they used to name English)",
      "language: String = \"english\"" not in dlc and dlc.count("language ?? depotLanguage") == 2 and "language ?? app.depotLanguage" in dlc)
check("shared-depot resolution accepts English and the game language",
      '["english", SteamLanguage.download(for: appID)].contains(d.language.lowercased())' in fetcher)

# The settings catalog's choice row for the setting lists exactly SteamLanguage.all, after English (default) and Device language.
block = info[info.index("static let all: [(code: String, name: String)] = ["):info.index("static func name(_ code: String)")]
codes = re.findall(r'\("(\w+)", "([^"]+)"\)', block)
cat = read(APP / "ConfigCatalog.generated.swift")
line = next((l for l in cat.split("\n") if 'key: "env.MADEIRA_STEAM_LANGUAGE"' in l), "")
choices = re.search(r"choices: \[(.*)\], sources", line)
listed = re.findall(r'\("([^"]*)", "((?:[^"\\]|\\.)*)"\)', choices.group(1)) if choices else []
check("the settings catalog has the Steam game language as a choice row",
      bool(line) and "kind: .choice" in line and 'category: "Steam & Dock"' in line, line[:200])
check("  listing English (default), Device language and every language of SteamLanguage.all in order, with the same names",
      listed[:2] == [("", "English (default)"), ("device", "Device language")] and listed[2:] == [c for c in codes if c[0] != "english"],
      (len(listed), len(codes)))
page = read(APP / "BCDGamePageLive.swift")
check("the game page offers the language as a Steam option, before and after the install",
      page.count("languageSetting(appID") == 2 and 'case "language":' in page and 'case "langdownload":' in page
      and "SteamLanguage.chooseForGame(UInt32(id)" in page and "d.offersDownloadOptions = true" in page)

if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift part", not args.require_swift)
else:
    fetcher_text = fetcher
    vdf_parser = "import Foundation\n\n" + fetcher_text[fetcher_text.index("// MARK: - Simple VDF Binary Parser"):]
    with tempfile.TemporaryDirectory(prefix="steam-lang-") as tmp:
        t = Path(tmp)
        (t / "VDF.swift").write_text(vdf_parser)
        shutil.copy(R / "tests/host/steam_language_checks.swift", t / "main.swift")
        sources = [t / "VDF.swift", APP / "SwiftSteam/Library/SteamAppInfo.swift", APP / "SteamKeyValues.swift", APP / "SteamInstall.swift",
                   APP / "ExternalStorage.swift", APP / "SwiftSteam/Install/AppManifestWriter.swift", APP / "BCDSteamDLC/BCDSteamDLC.swift"]
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "lang"), str(t / "main.swift")] + [str(s) for s in sources],
                           capture_output=True, text=True)
        check("the language rules compile on their own with the production app info, record writer and DLC rules", r.returncode == 0,
              (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            r = subprocess.run([str(t / "lang")], capture_output=True, text=True, timeout=120)
            print("\n".join("     " + line for line in r.stdout.strip().split("\n")))
            check("the language rules hold (default English, each choice, the DLC depots, the record, the device mapping, the sizes)",
                  r.returncode == 0, (r.stdout + r.stderr)[-2500:])

print("PASS: Steam language" if ok else "FAIL: Steam language")
sys.exit(0 if ok else 1)
