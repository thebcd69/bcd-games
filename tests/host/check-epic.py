#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Epic Games in BCD (app/Madeira/BCDEpic/): milestone 0, sign-in and the owned games; milestone 1, install and play.

Never contacts Epic and holds no account data: Epic's replies are synthetic, written in
tests/host/epic_checks.swift (milestone 0) and tests/host/epic_fixtures.py with
tests/host/epic_install_checks.swift (milestone 1: builds, manifests, chunk files and a local CDN written by
the test), and Epic's hosts are stand-ins (a stub transport, a URLProtocol for the real URLSession transport,
a local server for the CDN). Every name, code and token in the fixtures is made up.

Source checks (every host):
  - BCD's licence header, exactly, on every Epic file; each built by the Xcode project with an ID from the
    Epic range (A1000D50-D5F, A2000D50-D5F); all of them below app/Madeira's top level, where the preview
    build (tools/ui-preview.py, a top-level BCD*.swift glob) never compiles them, and no screen names them;
  - tokens: one Keychain item ("bcd.epic.tokens", through BCDKeychain: this device only, never synced),
    written in one place, never printed (the token type's descriptions say <redacted>), never in a log line,
    UserDefaults, the environment or a file; Epic's client secret only in BCDEpicAuth.swift; the cached list
    holds an account hash and no token or name;
  - logs: every [epic-*] line goes through BCDEpicLog, and every value it interpolates is on a list of
    counts, statuses, timings and sanitized words (a new one must be added here on purpose);
  - the sign-in sheet reads page text only for the one address the store names, with a script that
    returns text; its data store still dies with it; GOG's page names no user agent;
  - BCDLiveStore calls every Epic hook, each doing nothing with env.BCD_EPIC = 0; the catalog lists
    env.BCD_EPIC (on) and epic-user-agent; Steam's and GOG's files name nothing of Epic;
  - Send Log's scrub knows Epic's token shapes (tests/host/check-log-upload.py holds the details);
  - CI runs this check.
Milestone 1, source checks: the new files (header, Xcode IDs E50-E54, Foundation-only where the host compiles
them); the install root named in one place and no external drive; no sign-in argument, account ID, name or
token anywhere in what Play passes; every shared-file hook pinned (BCDLiveStore, the game page's backend, the
engine's codec switch) so a merge cannot drop or add one; with env.BCD_EPIC = 0 every hook does nothing and
env.BCD_EPIC_INSTALL = 0 wires no queue; the engine's stored and zlib paths are the lines they were.
Milestone 1, behaviour (epic_install_checks.swift, with a local CDN): Epic's chunk files (every header version,
stored and zlib, any split of the bytes, truncated, extended, damaged, encrypted); the binary manifest (every
data version, UTF-16 names, hostile and damaged bodies); the asset pointer; the plan; the rules (what is
refused, with which words); the launch arguments; an install over a dead, a corrupting and a good CDN, its
resume, its file check and repair, a wrong list checksum, an encrypted chunk, no room; the record, the folder,
the kept manifest and the uninstall; no CDN token, address or path in any log line.
Behaviour (swiftc on macOS; --require-swift makes its absence a failure): see epic_checks.swift: the
login and redirect pages, the code from the redirect page's text, a local redirect and a pasted text; the
token requests and replies; refresh ten minutes early and once for many callers; a refused refresh signing
out and a failed one not; verify; sign-out; an API call refused with 401 retried once; the token sent to
Epic's hosts only; the library pages and their filters; catalog entries, kinds and pictures; the refresh
with four catalog calls at a time, reuse, stale entries and failures; the cache; the masks on log lines and
no secret in any line of the whole run.

Milestone 2, source checks (a signed-in start): the five new files and BCDAsk.swift (header, Xcode IDs F50-F55); the words of a
secret (the exchange code, the account's ID and name, the ownership token, Epic's start arguments) only in the files that
fetch, write or look for them; the environment touched only for the host's request path and its program, the request written
exclusively, never through a link, 0600 and removed; every hook a start has in the shared files pinned (ContentView's two
lines, the front end's one); Epic off or a non-Epic entry: nothing changes (the order of the guards).
Milestone 2, behaviour (epic_launch_checks.swift): the request's bytes and report, the files of a start, Epic's launcher
arguments, when a start is signed in, the calls for the code and the token against a stand-in for Epic (order, retries,
refresh, failure, deadline, no secret in any log), the container search, the rules (the ownership token, GTA V's anti-cheat
exception); then bcd-host's own reader (bcd-host/tests/parse_file.c) parses the request files the Swift writer made.

  check-epic.py [--require-swift] [--require-preview]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
EPIC = APP / "BCDEpic"
CONTENT = APP / "BCDContent"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
ap.add_argument("--require-preview", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    """The source with // comments dropped (strings stay)."""
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
          "// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
FILES = ["BCDEpic.swift", "BCDEpicAudit.swift", "BCDEpicAuth.swift", "BCDEpicCatalog.swift", "BCDEpicContent.swift", "BCDEpicCredentials.swift", "BCDEpicDriver.swift",
         "BCDEpicHostFiles.swift", "BCDEpicInstall.swift", "BCDEpicLog.swift", "BCDEpicManifest.swift", "BCDEpicRules.swift", "BCDEpicSession.swift", "BCDEpicSigned.swift",
         "BCDEpicStart.swift", "BCDLiveStoreEpic.swift"]
M2_FILES = ["BCDEpicAudit.swift", "BCDEpicCredentials.swift", "BCDEpicHostFiles.swift", "BCDEpicSigned.swift", "BCDEpicStart.swift"]
M2_HOST_FILES = ["BCDEpicSigned.swift", "BCDEpicCredentials.swift", "BCDEpicHostFiles.swift", "BCDEpicAudit.swift"]
HOST_FILES = ["BCDEpicLog.swift", "BCDEpicAuth.swift", "BCDEpicSession.swift", "BCDEpicCatalog.swift"]
INSTALL_HOST_FILES = HOST_FILES + ["BCDEpicManifest.swift", "BCDEpicRules.swift", "BCDEpicContent.swift", "BCDEpicInstall.swift"]
M1_FILES = ["BCDEpicManifest.swift", "BCDEpicContent.swift", "BCDEpicRules.swift", "BCDEpicInstall.swift", "BCDEpicDriver.swift"]
LAUNCH_HOST_FILES = INSTALL_HOST_FILES + M2_HOST_FILES
CONTENT_HOST_FILES = ["BCDContentPlan.swift", "BCDContentCodec.swift", "BCDContentJournal.swift", "BCDContentFetch.swift",
                      "BCDContentEngine.swift", "BCDContentQueue.swift", "BCDContentSecrets.swift"]
CLIENT_SECRET = "daafbccc737745039dffe53d94fc76cf"

# ---- source checks ------------------------------------------------------------------------
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
_start = (EPIC / "BCDEpicStart.swift").read_text()
check("tell(BCDEpicHostFiles.notStartedWords)" in _start and "couldn't start this game signed in to Epic" not in _start,
      "round 624 (Discord forum item 7): a host that stopped before the game shows BCD Host's words, not an Epic sign-in message")
check(sorted(p.name for p in EPIC.glob("*.swift")) == sorted(FILES), "app/Madeira/BCDEpic holds exactly Epic's files",
      sorted(p.name for p in EPIC.glob("*.swift")))
sources = {}
for name in FILES:
    text = (EPIC / name).read_text()
    sources[name] = text
    check(text.startswith(HEADER), f"{name}: BCD's licence header, exactly")
    build = re.findall(r"(A1000(?:D5|E5|F5)[0-9A-F]) /\* BCDEpic/" + re.escape(name) + r" in Sources \*/ = \{isa = PBXBuildFile; fileRef = (A2000(?:D5|E5|F5)[0-9A-F])", pbx)
    ref = re.findall(r"(A2000(?:D5|E5|F5)[0-9A-F]) /\* " + re.escape(name) + r' \*/ = \{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "BCDEpic/' + re.escape(name) + '"', pbx)
    ok = len(build) == 1 and len(ref) == 1 and build[0][1] == ref[0] \
        and pbx.count(f"{build[0][0]} /* BCDEpic/{name} in Sources */,") == 1 and pbx.count(f"{ref[0]} /* {name} */,") == 1
    check(ok, f"{name}: built by the Xcode project (Epic's ID ranges, group and Sources phase)")
    if name != "BCDEpicAuth.swift":
        check(CLIENT_SECRET not in text, f"{name}: Epic's client secret only in BCDEpicAuth.swift")
ASK = APP / "BCDAsk.swift"
check(ASK.read_text().startswith(HEADER) and len(re.findall(r"A1000F55 /\* BCDAsk\.swift in Sources \*/ = \{isa = PBXBuildFile; fileRef = A2000F55 /\* BCDAsk\.swift \*/; \};", pbx)) == 1
      and len(re.findall(r'A2000F55 /\* BCDAsk\.swift \*/ = \{isa = PBXFileReference; lastKnownFileType = sourcecode\.swift; path = BCDAsk\.swift; sourceTree = "<group>"; \};', pbx)) == 1
      and pbx.count("A2000F55 /* BCDAsk.swift */,") == 1 and pbx.count("A1000F55 /* BCDAsk.swift in Sources */,") == 1,
      "BCDAsk.swift (the generic question alert, shared with the preview app): BCD's licence header, built by the Xcode project (F55)")
check(not re.search(r"(?i)epic|BCDEpic", code(ASK.read_text())), "...and it names no store")
check(len(set(re.findall(r"A[12]000(?:D5|E5|F5)[0-9A-F]\b", pbx))) == (len(FILES) + 1) * 2, "the Epic ID ranges (D50-D5F, E50-E5F, F50-F5F) hold only Epic's files and BCDAsk.swift (a build ID and a file ID each)")

preview = (R / "tools/ui-preview.py").read_text()
# The cache recursively hashes resource bundles; only the compiler's source
# selection must stay top-level. Keep the engine-file guard scoped to that build.
preview_build = preview.split("def _build(out):", 1)[-1].split("\ndef ", 1)[0]
check('APP.glob("BCD*.swift")' in preview_build and "rglob" not in preview_build,
      "the preview build compiles app/Madeira's top-level BCD*.swift only, so the engine-bound Epic files stay out")
screens = [p for p in APP.glob("BCD*.swift") if p.name not in ("BCDLiveStore.swift", "BCDLiveFrontEnd.swift", "BCDSetupView.swift")
           and not p.name.endswith("Live.swift")]
named = sorted(p.name for p in screens if re.search(r"\bBCDEpic\w*|\bBCDLiveStoreEpic\b|\bBCDWebSignIn\b", code(p.read_text())))
check(not named, "no screen names Epic's code or the sign-in sheet", named)

# Tokens.
session = code(sources["BCDEpicSession.swift"])
auth = code(sources["BCDEpicAuth.swift"])
check('static let itemName = "bcd.epic.tokens"' in session and session.count("secrets.write(") == 1 and session.count("JSONEncoder().encode(fresh)") == 1
      and session.count("secrets.delete(") == 1, "the tokens are written in one place and deleted in one: the store's one secret item")
encoders = {n: code(t).count("JSONEncoder().encode(") for n, t in sources.items()}
check(encoders == {"BCDEpic.swift": 1, "BCDEpicAudit.swift": 0, "BCDEpicAuth.swift": 0, "BCDEpicCatalog.swift": 0, "BCDEpicContent.swift": 0, "BCDEpicCredentials.swift": 0, "BCDEpicDriver.swift": 1,
                   "BCDEpicHostFiles.swift": 0, "BCDEpicInstall.swift": 0, "BCDEpicLog.swift": 0, "BCDEpicManifest.swift": 0, "BCDEpicRules.swift": 0, "BCDEpicSession.swift": 1,
                   "BCDEpicSigned.swift": 0, "BCDEpicStart.swift": 0, "BCDLiveStoreEpic.swift": 0},
      "nothing is encoded to storage but the session's item, the list's cache and an install's plan facts (no token)", encoders)
facade = code(sources["BCDEpic.swift"])
check("JSONEncoder().encode(cache).write(to: Self.folder.appendingPathComponent(\"library.json\"), options: .atomic)" in facade
      and facade.count(".write(to:") == 1, "the facade writes the list's cache and nothing else")
driver = code(sources["BCDEpicDriver.swift"])
check(driver.count(".write(to:") == 3 and "JSONEncoder().encode(info).write(to: BCDEpicInstall.pendingInfoFile(info.app, base: base), options: .atomic)" in driver
      and "manifest.write(to: BCDEpicInstall.pendingManifestFile(info.app, base: base), options: .atomic)" in driver
      and "data.write(to: file, options: .atomic)" in driver and 'url.scheme == "https"' in driver,
      "the driver writes the install's pending facts, the manifest it was made from and a game's public pictures (https only), nothing with a token")
check("BCDKeychain.shared" in facade and "BCDEpicSession(secrets: BCDKeychain.shared" in facade, "the session keeps its tokens in the Keychain")
secrets_src = (CONTENT / "BCDContentSecrets.swift").read_text()
check("kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly" in secrets_src and "kSecAttrSynchronizable as String: false" in secrets_src,
      "the Keychain item is this device's only, never synced, readable after the first unlock")
check('var description: String { "BCDEpicTokens(<redacted>)" }' in auth and "var debugDescription: String { description }" in auth,
      "printing the tokens shows nothing of them")
holders = sorted(n for n, t in sources.items() if re.search(r"\b(accessToken|refreshToken)\b", code(t)))
check(holders == ["BCDEpicAuth.swift", "BCDEpicCredentials.swift", "BCDEpicSession.swift"],
      "the session's tokens' fields are named only where they are made and kept, and where a start passes the bearer token to the calls for its code (BCDEpicCredentials: a parameter, never stored)", holders)
SETENV_LINES = sorted([
    'setenv("BCD_HOST_REQUEST", BCDEpicHostFiles.guestPath(file), 1)',
    'setenv("MADEIRA_EXE", "explorer.exe", 1)',
    'setenv("MADEIRA_ARGS", "/desktop=shell,\\(entry.resolution) \\"\\(Self.hostProgram)\\" --epic", 1)',
    'setenv("MADEIRA_EXE", Self.hostProgram, 1)',
    'setenv("MADEIRA_ARGS", "--epic", 1)'])
FILE_ACCESS = {"BCDEpic.swift", "BCDEpicDriver.swift", "BCDEpicInstall.swift", "BCDLiveStoreEpic.swift", "BCDEpicHostFiles.swift", "BCDEpicAudit.swift"}
for name, text in sources.items():
    c = code(text)
    check(not re.search(r"UserDefaults|AppStorage|SceneStorage|putenv|NSLog|os_log|Logger\(|debugPrint|dump\(|\bprint\(|fputs|standardError|NSUbiquitous|HTTPCookie", c),
          f"{name}: nothing Epic keeps goes to UserDefaults, the console or a cookie")
    if name != "BCDEpicStart.swift":
        check(not re.search(r"\bsetenv|\bunsetenv", c), f"{name}: the environment is not touched")
    if name != "BCDEpicLog.swift":
        check("BCDContentLog" not in c.replace("BCDContentLog.sink = { LogStore.shared.log($0) }", ""), f"{name}: logs only through BCDEpicLog")
    if name not in FILE_ACCESS:
        check("LogStore" not in c and "FileManager" not in c, f"{name}: no LogStore, no file access")
    elif name != "BCDEpic.swift":
        check("LogStore" not in c, f"{name}: no LogStore (logs go through BCDEpicLog)")
start_code = code(sources["BCDEpicStart.swift"])
check(sorted(l.strip() for l in start_code.splitlines() if re.search(r"(?<![un])\bsetenv\(", l)) == SETENV_LINES,
      "a signed-in start sets the environment in five places only: the request's path, and the session's program and arguments (BCD Host, --epic): never a code, an ID, a name or a token")
check(sorted(set(re.findall(r'unsetenv\("(\w+)"\)', start_code))) == ["BCD_HOST_REQUEST"], "...and removes only BCD_HOST_REQUEST")

cache = re.search(r"struct BCDEpicItem: .*?\n}\n", code(sources["BCDEpicCatalog.swift"]), re.S).group(0)
cache += re.search(r"struct Cache: Codable \{.*?\n    }\n", code(sources["BCDEpicCatalog.swift"]), re.S).group(0)
check(not re.search(r"(?i)accessToken|refreshToken|password|secret|cookie|displayName|accountID|exchange|email", cache)
      and "var account: String" in cache, "the cached list's fields hold no token, name or account ID (an account hash)", cache[-400:])

# Log lines.
ALLOWED = [r"status", r"verifyStatus", r"BCDEpicLog\.millis\(since: \w+\)", r"BCDEpicLog\.mask\(account\)", r"BCDEpicLog\.reason\(error\)",
           r"BCDEpicLog\.statuses\(statuses\)", r"BCDEpicLog\.shape\(object\)", r"BCDEpicAuth\.pageLabel\(url\)", r"data\.count", r"shape", r"made\.displayName != nil \? 1 : 0", r"signedIn \? 1 : 0", r"next \? 1 : 0",
           r"items\.count", r"games\.count", r"text\.count", r"action", r"BCDEpicAuth\.keys\(of: text\)",
           r"listed\.filter \{ \$0\.(tall|wide) != nil \}\.count", r'sites\.sorted\(\)\.prefix\(4\)\.joined\(separator: ","\)',
           r"records|pages|games|dlc|mods|extras|notWindows|unknown|thirdParty|droppedUnreal|droppedPrivate|droppedInvalid|duplicates|namespaces|"
           r"catalogRequests|catalogFailed|reused|milliseconds",
           # Milestone 1: app names (a word, or a masked identifier), counts, sizes, levels and short reasons.
           r"BCDEpicLog\.app\((key\.id|app|info\.app|record\.app)\)", r"index \+ 1", r"build\.manifestURLs\.count", r"failed", r"mismatched", r"manifest\.featureLevel",
           r"manifest\.files\.count", r"manifest\.chunks\.count", r"build\.manifestHash == nil \? 0 : 1", r"bases\.count", r"plan\.files\.count", r"plan\.chunks\.count",
           r"plan\.downloadBytes", r"plan\.totalBytes", r"skipped", r"info\.eos \? 1 : 0", r"info\.prerequisiteNote == nil \? 0 : 1", r"info\.deploymentID == nil \? 0 : 1",
           r"installs\.count", r"refusals\.entries\.count", r"refusal\.code", r"BCDEpicRules\.sanitized\(refusal\.code\)", r"record\.deploymentID == nil \? 0 : 1",
           r'hosts\.prefix\(4\)\.joined\(separator: ","\)', r"shape", r"found\.downloadBytes", r"found\.installBytes", r"found\.eos \? 1 : 0",
           r'BCDEpicRules\.sanitized\(found\.refusal\?\.code \?\? "none"\)', r"why", r"total", r"wanted\.count", r'own \? "epic" : "yours"', r'action == \.update \? "update" : "verify"', r"data\.count", r"info\.featureLevel",
           # Milestone 2: steps, statuses, timings, counts and short reasons of a signed-in start; the host's report (numbers only, pinned by the harness).
           r"step", r"error\.code\.rawValue", r"name", r"token \? 1 : 0", r"credentials\.ownership == nil \? 0 : 1", r"failure\.logReason", r"removed",
           r"report\.logText", r"BCDEpicRules\.sanitized\(reason\)", r"ovtWindows == nil \? 0 : 1", r"flags\.contains\(\.tree\) \? 1 : 0", r"flags\.contains\(\.services\) \? 1 : 0",
           r"desktop \? 1 : 0", r"result\.summary", r"files", r"bytes", r"total", r"\$0", r"counts\[\$0\] \?\? 0", r"truncated \? 1 : 0", r"BCDEpicRules\.sanitized\(hit\.label\)", r"hit\.file", r"BCDEpicRules\.sanitized\(info\.exceptionID \?\? \"none\"\)"]


def interpolations(line):
    out, i = [], 0
    while True:
        i = line.find("\\(", i)
        if i < 0:
            return out
        depth, j = 1, i + 2
        while j < len(line) and depth:
            depth += (line[j] == "(") - (line[j] == ")")
            j += 1
        out.append(line[i + 2:j - 1])
        i = j


def log_calls(c):
    """Every BCDEpicLog.write(...) call as one string, a call that continues on the next line (a trailing or a leading +) joined."""
    rows = c.splitlines()
    calls = []
    for i, row in enumerate(rows):
        if "BCDEpicLog.write(" not in row:
            continue
        joined, j = row, i
        while j + 1 < len(rows) and (joined.rstrip().endswith("+") or rows[j + 1].lstrip().startswith("+")):
            j += 1
            joined += " " + rows[j].strip()
        calls.append(joined)
    return calls


log_sites = 0
for name, text in sources.items():
    c = code(text)
    lines = log_calls(c)
    summary = re.search(r"var summary: String \{.*?\n    }\n", c, re.S)
    if summary:
        lines.append(summary.group(0))
    for line in lines:
        log_sites += 1
        for expr in interpolations(line):
            check(any(re.fullmatch(a, expr.strip()) for a in ALLOWED), f"{name}: a log line may carry {expr.strip()!r}", line.strip()[:160])
        for literal in re.findall(r'"(\[epic-[a-z]+\][^"]*)"', line):
            check(not re.search(r"(?i)secret|cookie|password|\bcode=|https?:|eg1~|egoc1~|@", literal), f"{name}: log text carries no secret or address: {literal[:60]}")
check(log_sites >= 20, f"the log sites were found ({log_sites})")
tags = set(re.findall(r'"(\[epic-[a-z]+\])', "".join(sources.values())))
check(tags == {"[epic-auth]", "[epic-library]", "[epic-install]", "[epic-cdn]", "[epic-launch]", "[epic-audit]"},
      "Epic logs under [epic-auth], [epic-library], [epic-install], [epic-cdn], [epic-launch] and [epic-audit] (and the host's [bcd-host] report) only", tags)
check(set(re.findall(r'"(\[bcd-host\])', "".join(sources.values()))) == {"[bcd-host]"}, "the host's numeric report is logged as [bcd-host]")

# The sign-in sheet.
sheet = code((CONTENT / "BCDWebSignIn.swift").read_text())
check("configuration.websiteDataStore = .nonPersistent()" in sheet and sheet.count("evaluateJavaScript(") == 1
      and 'guard completion != nil, let read = page.matchPage, let url = webView.url, page.readsPage?(url) == true else { return }' in sheet
      and 'webView.evaluateJavaScript("document.body ? document.body.innerText.slice(0, 65536) : \'\'")'.replace("\\'", "'") in sheet.replace('\\"', '"'),
      "the sheet reads a page's text only where the store says (one script, bounded, after the guard), in a store that dies with it")
fac = sources["BCDEpic.swift"]
check("readsPage: BCDEpicAuth.isRedirectPage(_:)" in fac and "matchPasted: BCDEpicAuth.code(fromPasted:)" in fac and "userAgent: Self.userAgentOverride" in fac
      and "return BCDEpicAuth.code(from: url) }" in fac, "Epic's page: the redirect page is the one page read; a local redirect or a pasted text also gives the code")
check('MadeiraConfig.flag("BCD_EPIC", fallback: true)' in facade and 'MadeiraConfig.get("epic-user-agent")' in facade,
      "the switches read as the catalog lists them")
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
check('ConfigOption(key: "env.BCD_EPIC", title: "", kind: .bool, defaultValue: "1"' in catalog and 'ConfigOption(key: "epic-user-agent", title: "", kind: .text' in catalog
      and 'ConfigOption(key: "env.BCD_EPIC_INSTALL", title: "", kind: .bool, defaultValue: "1"' in catalog and 'ConfigOption(key: "env.BCD_EPIC_CDN", title: "", kind: .text' in catalog,
      "Settings › All settings lists env.BCD_EPIC (on), env.BCD_EPIC_INSTALL (on), env.BCD_EPIC_CDN and epic-user-agent")
check('MadeiraConfig.flag("BCD_EPIC_INSTALL", fallback: true)' in facade and 'MadeiraConfig.get("env.BCD_EPIC_CDN")' in facade, "...read where the catalog says")

# The live store's hooks.
live = code(sources["BCDLiveStoreEpic.swift"])
HOOK_FUNCS = ["epicMerge(_ games: inout [BCDGame])", "epicAccount() -> BCDAccount", "epic(_ action: BCDEpicAction, _ id: String, place: BCDInstallPlace = .device) -> Bool",
              "epicAccountAction(signIn: Bool, _ front: BCDStoreFront) -> Bool", "epicRefresh()", "epicDownloads() -> [BCDDownload]",
              "epicPlayEntry(_ game: BCDGame) -> LibraryEntry?", "epicInstallAdvice(_ id: String) -> (blocker: String?, warning: String?)",
              "epicNote(_ id: String) -> String?", "epicEntryID(_ id: String) -> UUID?", "epicOpened(_ id: String)"]
check(all(f"func {h}" in live for h in HOOK_FUNCS)
      and all("BCDEpic.enabled" in live[live.index(f"func {h}"):live.index(f"func {h}") + 330] for h in HOOK_FUNCS if not h.startswith("epicAccount() "))
      and "BCDEpic.enabled ? BCDEpic.shared.changes : Empty" in live,
      "the live store's Epic hooks exist, each doing nothing with env.BCD_EPIC = 0")
check("BCDEpic.enabled ? BCDEpic.shared.$looks.dropFirst().map { _ in () }.eraseToAnyPublisher() : Empty<Void, Never>().eraseToAnyPublisher()" in live,
      "...and the page's change publisher is empty with env.BCD_EPIC = 0")
check("BCDEpicRules.claimedApp(id, enabled: BCDEpic.enabled)" in live and "BCDEpicRules.claimedApp(game.id, enabled: BCDEpic.enabled)" in live
      and "guard BCDEpic.enabled else { return [] }" in live and "guard BCDEpic.enabled else { return }" in live,
      "...an id is Epic's only through the one routing rule the harness pins (epic_install_checks.swift)")
store = code((APP / "BCDLiveStore.swift").read_text())
wired = {
    "changes": "epicChanges," in store,
    "games": "epicMerge(&games)" in store,
    "account": "epicAccount()," in store and "BCDAccount(store: .epic" not in store,
    "install": "if epic(.install, game.id, place: place) { return }" in store,
    "sign-in": "if epicAccountAction(signIn: true, front) { return }" in store and "if epicAccountAction(signIn: false, front) { return }" in store,
    "refresh": "epicRefresh()" in store,
}
check(all(wired.values()), "BCDLiveStore calls every Epic hook (a merge must not drop one)", [k for k, v in wired.items() if not v])


def epic_code_lines(text):
    """The code lines (comments dropped) that name Epic, stripped."""
    return sorted(l.strip() for l in code(text).splitlines() if re.search(r"(?<![A-Za-z])[eE]pic|BCDEpic", l))


STORE_HOOKS = sorted([
    "epicChanges,", "epicMerge(&games)", "let downloads = downloadList(names: names) + gogDownloads() + epicDownloads()", "epicAccount(),",
    "if let entry = epicPlayEntry(game) { begin(entry, launch); return }", "if epic(.install, game.id, place: place) { return }", "if epic(.update, game.id) { return }",
    "if epic(.verify, game.id) { return }", "if epic(.uninstall, game.id) { return }", "if epic(.pause, id) { return }", "if epic(.resume, id) { return }",
    "if epic(.cancel, id) { return }", "if epicAccountAction(signIn: true, front) { return }", "if epicAccountAction(signIn: false, front) { return }", "epicRefresh()"])
check(epic_code_lines((APP / "BCDLiveStore.swift").read_text()) == STORE_HOOKS, "BCDLiveStore holds exactly these Epic lines (every shared-file hook is one line; nothing else names Epic)",
      sorted(set(epic_code_lines((APP / "BCDLiveStore.swift").read_text())) ^ set(STORE_HOOKS)))
PAGE_HOOKS = sorted([
    "if let id = BCDLiveStore.shared.epicEntryID(game.id) { return library.entries.first { $0.id == id } }",
    "let advice = BCDLiveStore.shared.epicInstallAdvice(game.id)",
    "BCDLiveStore.shared.epicOpened(game.id)",
    "BCDLiveStore.shared.epicDetailChanges,",
    "d.storeNote = BCDLiveStore.shared.gogNote(game.id) ?? BCDLiveStore.shared.epicNote(game.id)",
    "if !desktop { d.groups.append(libraryGroup(entry, gog: game.store == .gog || game.store == .epic)) }",
    "} else if game.store == .gog || game.store == .epic {"])
check(epic_code_lines((APP / "BCDGamePageLive.swift").read_text()) == PAGE_HOOKS, "the game page's backend holds exactly these Epic lines",
      sorted(set(epic_code_lines((APP / "BCDGamePageLive.swift").read_text())) ^ set(PAGE_HOOKS)))
page = code((APP / "BCDGamePage.swift").read_text())
check("details.installBlocker" in page and "details.installWarning" in page and not re.search(r"(?i)epic", page.replace("details.installBlocker", "")),
      "the game page itself names no store's code: it shows a blocker and a warning it is given (a store's words)")
engine = code((CONTENT / "BCDContentEngine.swift").read_text())
check("case .stored: try emit(raw)" in engine and "case .zlib: try inflater!.feed(raw) { try emit($0) }" in engine
      and "if chunk.codec == .zlib, inflater == nil { inflater = try BCDInflater() }" in engine
      and "guard received + Int64(data.count) <= limit else { throw BCDContentError.tooLarge }" in engine
      and "let limit = chunk.codec == .epic ? (1 << 30) + 1024 : chunk.fetchSize" in engine,
      "the engine's stored and zlib paths are the lines they were, and only the Epic codec gets another size limit")
check("case .diskFull, .io, .refused:" in engine and not re.search(r"BCDGOG\w*.*\.refused|\.refused\(", code("".join((APP / "BCDGOG" / n).read_text() for n in ["BCDGOGContent.swift", "BCDGOGAuth.swift"]))),
      "...and GOG throws no refused error that the engine now stops on")
wire_start = driver.index("func wireInstalls()")
check(driver.index("guard Self.installEnabled else { return }", wire_start) < driver.index("queue.register(self)", wire_start)
      < driver.index("BCDContentBackground.shared.attach(queue)", wire_start),
      "env.BCD_EPIC_INSTALL = 0 wires no queue, no background task, no observer")

# Where games install, and what Play passes.
root_sites = [p.name for p in sorted(APP.rglob("*.swift")) if '"Program Files/Epic Games"' in code(p.read_text())]
check(root_sites == ["BCDEpicInstall.swift"] and code(sources["BCDEpicInstall.swift"]).count('BCDInstallRoot(drive: "C", folder: "Program Files/Epic Games")') == 1,
      "the install root (C:\\Program Files\\Epic Games) is named in one place", root_sites)
check(not re.search(r"\bExternalDrive\w*|hostDrive", "".join(code(sources[n]) for n in FILES)),
      "no external drive code (the ExternalDrive type of the ext-ssd branch) is imported or depended on")
for name in M1_FILES:
    c = code(sources[name])
    check(not re.search(r"AUTH_LOGIN|AUTH_PASSWORD|AUTH_TYPE|epicuserid|epicusername|epicovt|exchange_?code|exchangeCode|oauth/exchange|ownershipToken\(|accountID|displayName", c),
          f"{name}: no sign-in argument, account ID or name, exchange code or ownership token (those live in the signed-start files only)")
STARTWORDS = r"AUTH_LOGIN|AUTH_PASSWORD|AUTH_TYPE|epicuserid|epicusername|epicovt|exchange_?code|exchangeCode|oauth/exchange|exchangeRequest|ownershipRequest|ownershipReply"
named = sorted(n for n, t in sources.items() if re.search(STARTWORDS, code(t)))
check(named == ["BCDEpicAudit.swift", "BCDEpicCredentials.swift", "BCDEpicLog.swift", "BCDEpicSigned.swift", "BCDEpicStart.swift"],
      "Epic's start arguments and the exchange code's calls are named only in the files that build the arguments, fetch the code, start the game, search for what it leaves or mask it in a log", named)
check(not re.search(STARTWORDS, code(sources["BCDEpicHostFiles.swift"])) and not re.search(r"BCDEpicSigned|BCDEpicCredentials|exchangeCode", code(sources["BCDEpicHostFiles.swift"])),
      "the request's and the token file's writer knows no code: it is given bytes")
check(set(re.findall(r"\.arguments = ([\w.()]+)", driver + code(sources["BCDEpicInstall.swift"]))) == {"launch.arguments", "launch(record).arguments"}
      and code(sources["BCDEpicInstall.swift"]).count("BCDEpicLaunch.arguments(") == 1,
      "a game's arguments come from BCDEpicLaunch.arguments and nowhere else")
for name in ["BCDEpicManifest.swift", "BCDEpicRules.swift", "BCDEpicContent.swift", "BCDEpicInstall.swift"]:
    imports = set(re.findall(r"^import (\w+)", sources[name], re.M))
    check(imports <= {"Foundation"}, f"{name}: Foundation only (the host checks compile it)", str(sorted(imports)))

check("not installed" in fac.lower() or ".notInstalled" in live, "Epic's games are listed as not installed")
steam_files = [APP / "SteamOwnedLibrary.swift", APP / "SteamDownloadBackground.swift", APP / "SteamGames.swift",
               APP / "SteamInstall.swift"] + sorted((APP / "SwiftSteam").rglob("*.swift"))
gog_files = sorted((APP / "BCDGOG").glob("*.swift")) + [p for p in sorted(CONTENT.glob("*.swift")) if p.name != "BCDWebSignIn.swift"]
touched = [p.name for p in steam_files + gog_files if re.search(r"BCDEpic", p.read_text())]
check(not touched, "Steam's, GOG's and the content engine's files name nothing of Epic", touched)
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
check("run: python3 tests/host/check-epic.py --require-swift --require-preview" in workflow, "CI runs this check")
scrub = (APP / "LogUploadCore.swift").read_text()
check('(#"\\beg1~[A-Za-z0-9._~+/=-]+"#, "eg1~<token>")' in scrub and '(#"\\begoc1~[A-Za-z0-9._~+/=-]+"#, "egoc1~<token>")' in scrub
      and '"authorizationCode"' in scrub, "Send Log's scrub knows Epic's two token families and the sign-in page's code")

# ---- milestone 2: a signed-in start --------------------------------------------------------
hostfiles = code(sources["BCDEpicHostFiles.swift"])
check(hostfiles.count("open(url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)") == 1 and hostfiles.count("try file.write(contentsOf: data)") == 1
      and hostfiles.count(".write(to:") == 0 and "try? FileManager.default.removeItem(at: file)" in hostfiles,
      "every file of a start is created exclusively, never through a link, readable by the app alone (0600), by one function; an earlier one goes first")
check("FileProtectionType.complete" in hostfiles and "isExcludedFromBackup = true" in hostfiles and hostfiles.count(".posixPermissions: 0o700") == 1,
      "...the request's folder is private (0700), completely protected and kept out of backups")
check('"\\\\\\\\?\\\\unix"' in hostfiles, "the request's path goes through Wine's Unix namespace (\\\\?\\unix), as BCD Dock's transfer does")
check(re.search(r"static func cleanup\(base: URL, drive: URL\?, report: Bool = false\) -> Int", hostfiles) and "remove(requestFile(base))" in hostfiles and 'hasSuffix(".ovt")' in hostfiles,
      "a clean-up removes the request, every token file, and the report on request")
start = code(sources["BCDEpicStart.swift"])
check(start.index('unsetenv("BCD_HOST_REQUEST")', start.index("func applyLaunch")) < start.index("guard let record = record(forEntry: entry.id) else { return }", start.index("func applyLaunch"))
      < start.index("try startThroughHost(", start.index("func applyLaunch")),
      "applyLaunch clears any request path, then returns for an entry that is no Epic install, before it sets anything: every other start is untouched")
check(start.index("guard let record = record(forEntry: entry.id) else { return true }", start.index("func preLaunch")) < start.index("sign(entry, record: record, retry: retry)", start.index("func preLaunch")),
      "preLaunch lets every entry that is no Epic install go straight on")
check(start.count("startSignedStarts()") == 1 and "startSignedStarts()" in code(sources["BCDEpic.swift"]) and "guard Self.enabled, !started else { return }" in code(sources["BCDEpic.swift"])
      and code(sources["BCDEpic.swift"]).index("guard Self.enabled, !started else { return }") < code(sources["BCDEpic.swift"]).index("startSignedStarts()"),
      "the leftovers' clean-up and the session watch start with Epic's first look, and only when Epic is on")
check(start.count("BCDEpicHostFiles.cleanup(") == 4 and start.count("unsetenv(\"BCD_HOST_REQUEST\")") == 5,
      "a start's files are cleaned up at the first look (leftovers), when a start cannot be made, when the session ends and at sign-out")
check("$current.removeDuplicates().sink" in start and "self.sessionEnded()" in start and "guard let self, current == nil, self.hostRan else { return }" in start,
      "the end of a signed-in session is seen through the library's published session (no hook in the session code)")
check(start.count("BCDEpicAudit.run(") == 1 and "Task.detached(priority: .utility)" in start and "auditSecrets = nil" in start,
      "the container search runs once per signed-in session, off the main thread, and the values it looks for are forgotten at once")
check('flags.insert(.tree)' in start and 'flags.insert(.services)' in start and 'entry.startServices == true' in start and "root: record.root.windowsPath()" in start,
      "the request asks for the tree wait (env.BCD_EPIC_TREE), services.exe when the entry starts services, and names the install root")
check('if Self.flag("BCD_EPIC_SERVICES", entry: entry, fallback: entry.startServices == true || BCDEpicRules.startsRockstarLauncher(record.exceptionID)) {' in start
      and start.count('flags.insert(.services)') == 1,
      "round 624 (R S24): services.exe also for Epic's GTA V (Rockstar's launcher needs the service manager); env.BCD_EPIC_SERVICES = 0 turns it off")
check('Self.flag("BCD_EPIC_SIGNIN", entry: entry)' in start and 'Self.flag("BCD_EPIC_HOST", entry: entry)' in start and 'Self.flag("BCD_EPIC_TREE", entry: entry)' in start,
      "each M2 switch is read for the game (its own config first, then bcd.cfg)")
fac2 = code(sources["BCDEpic.swift"])
check('MadeiraConfig.flag("BCD_EPIC_SIGNIN", fallback: true)' in fac2 and 'MadeiraConfig.flag("BCD_EPIC_HOST", fallback: true)' in fac2 and 'MadeiraConfig.flag("BCD_EPIC_TREE", fallback: true)' in fac2
      and '"/arm64ec-windows/bcdhost.exe"' in fac2, "the switches are read where the catalog says; the host is looked for in the bundle's arm64ec-windows folder")
ctx = code((APP / "ContentView.swift").read_text())
check(sorted(l.strip().split("   //")[0] for l in ctx.splitlines() if re.search(r"(?<![A-Za-z])[eE]pic|BCDEpic", l)) == sorted([
    "guard !BCDEpic.enabled || BCDEpic.shared.preLaunch(entry, retry: { startLibraryEntry(entry) }) else { return }",
    "if BCDEpic.enabled { BCDEpic.shared.applyLaunch(entry) }",
    "let epicSession = profile.map { BCDEpic.shared.owns(entryID: $0.id) } ?? false",
    "if epicSession {",
    'logStore.log("[msiexec64] Epic game: msiexec in system32 for this session")']),
      "ContentView holds exactly these Epic lines (the two hooks of a signed-in start and milestone 1's msiexec, read on the main thread): every shared-file hook is one line")
cstart = ctx.index("private func startLibraryEntry(")
check(ctx.index("guard !BCDEpic.enabled || BCDEpic.shared.preLaunch(") - cstart < 120 and ctx.index("entry.configureLaunch()", cstart) < ctx.index("if BCDEpic.enabled { BCDEpic.shared.applyLaunch(entry) }")
      < ctx.index("BCDLaunch.applyLibrary(entry, sessionLog: false)", cstart),
      "...the first is the first line of startLibraryEntry, the second follows configureLaunch and comes before the game's own options")
fe = code((APP / "BCDLiveFrontEnd.swift").read_text())
check(sorted(l.strip().split("   //")[0] for l in fe.splitlines() if re.search(r"(?<![A-Za-z])[eE]pic|BCDEpic", l)) == [".modifier(BCDEpicPromptAlert())"],
      "BCDLiveFrontEnd holds exactly one Epic line: the alert for the sign-in question")
prompt_use = [p.name for p in sorted(APP.rglob("*.swift")) if "BCDEpicPromptAlert" in code(p.read_text())]
check(prompt_use == ["BCDEpicStart.swift", "BCDLiveFrontEnd.swift"], "BCDEpicPromptAlert is defined in BCDEpicStart.swift and used only by the front end", prompt_use)
bridge_m = (APP / "WineProcessBridge.m").read_text()
check("MADEIRA_MSIEXEC_SYSTEM32" in bridge_m and "BCD_HOST_REQUEST" not in bridge_m and "BCD_HOST_REQUEST" not in (R / "build/ntdll-unix/process_ios.c").read_text(),
      "the bridge and the runtime know nothing of the host's request path: it is an ordinary environment variable only the host reads")
check(not re.search(r"(?i)epic", (R / "app/Madeira/MadeiraDock.swift").read_text()) and not re.search(r"(?i)epic", (R / "app/Madeira/SteamGames.swift").read_text()),
      "BCD Dock's and Steam's files name nothing of Epic (milestone 2 changes neither)")
scrub2 = (APP / "LogUploadCore.swift").read_text()
check(all(x in scrub2 for x in ['(-AUTH_PASSWORD=)(?![*<])', '(-epicuserid=)(?![*<])', '(-epicusername=)(?![*<])', '(?:exchangeCode|userCode)=|[?&]code=']),
      "Send Log's scrub knows Epic's start arguments and a code in an address (tests/host/check-log-upload.py holds the details)")
catalog2 = (APP / "ConfigCatalog.generated.swift").read_text()
check(all(f'ConfigOption(key: "{k}", title: "", kind: .bool, defaultValue: "1"' in catalog2 for k in ("env.BCD_EPIC_SIGNIN", "env.BCD_EPIC_HOST", "env.BCD_EPIC_TREE")),
      "Settings › All settings lists env.BCD_EPIC_SIGNIN, env.BCD_EPIC_HOST and env.BCD_EPIC_TREE (all on)")
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("run: python3 tests/host/check-epic-mask.py" in wf and "run: python3 tests/host/check-bcd-host.py" in wf
      and re.search(r"\n\s+(run: |)bash build/bcd-host/build.sh", wf) is not None,
      "CI runs the host's and the masks' checks and builds bcdhost.exe")

# ---- the Swift harness ---------------------------------------------------------------------
swiftc = shutil.which("swiftc")
if not swiftc:
    check(not args.require_swift, "Swift compiler available")
else:
    work = Path(tempfile.mkdtemp(prefix="bcd-epic-"))
    try:
        exe = work / "harness"
        build = subprocess.run([swiftc, "-parse-as-library", "-swift-version", "5", "-Onone", "-o", str(exe), str(R / "tests/host/epic_checks.swift")]
                               + [str(CONTENT / f) for f in CONTENT_HOST_FILES] + [str(EPIC / f) for f in HOST_FILES],
                               capture_output=True, text=True)
        check(build.returncode == 0, "the production Epic and engine files compile on the host", build.stderr[-4000:])
        if build.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600, env=dict(os.environ, SWIFT_BACKTRACE="enable=no"))
            lines = [l for l in r.stdout.splitlines() if not l.startswith("LOG ")]
            passed = sum(1 for l in lines if l.startswith("PASS"))
            for l in lines:
                if l.startswith("FAIL"):
                    print("  " + l)
            check(r.returncode == 0 and passed >= 150 and lines and lines[-1] == "EPIC-HARNESS: all passed",
                  f"the Swift harness passed ({passed} checks)", ((r.stderr or "")[-2000:] + "\n".join(lines[-6:])).strip())
        # Milestone 1: the install harness, over synthetic builds on a local CDN.
        sys.path.insert(0, str(R / "tests/host"))
        import epic_fixtures
        install_exe = work / "install-harness"
        sanitize = ["-sanitize=address"] if os.environ.get("BCD_EPIC_ASAN", "1") == "1" else []
        build = subprocess.run([swiftc, "-parse-as-library", "-swift-version", "5", "-Onone"] + sanitize + ["-o", str(install_exe), str(R / "tests/host/epic_install_checks.swift")]
                               + [str(CONTENT / f) for f in CONTENT_HOST_FILES] + [str(EPIC / f) for f in INSTALL_HOST_FILES],
                               capture_output=True, text=True)
        if build.returncode != 0 and sanitize:
            sanitize = []
            build = subprocess.run([swiftc, "-parse-as-library", "-swift-version", "5", "-Onone", "-o", str(install_exe), str(R / "tests/host/epic_install_checks.swift")]
                                   + [str(CONTENT / f) for f in CONTENT_HOST_FILES] + [str(EPIC / f) for f in INSTALL_HOST_FILES],
                                   capture_output=True, text=True)
        check(build.returncode == 0, "the install harness compiles against the production files" + (" (with AddressSanitizer)" if sanitize else ""), build.stderr[-6000:])
        if build.returncode == 0:
            cdn = epic_fixtures.Cdn()
            cdn.start()
            try:
                import json
                fx_dir = work / "fixture"
                fx_dir.mkdir()
                fixture = epic_fixtures.write_fixtures(str(fx_dir), cdn)
                (work / "fixture.json").write_text(json.dumps(fixture))
                r = subprocess.run([str(install_exe)], capture_output=True, text=True, timeout=900,
                                   env=dict(os.environ, SWIFT_BACKTRACE="enable=no", EPIC_FIXTURE=str(work / "fixture.json")))
            finally:
                cdn.stop()
            lines = [l for l in r.stdout.splitlines() if not l.startswith("LOG ")]
            passed = sum(1 for l in lines if l.startswith("PASS"))
            for l in lines:
                if l.startswith("FAIL"):
                    print("  " + l)
            check(r.returncode == 0 and passed >= 240 and lines and lines[-1] == "EPIC-INSTALL-HARNESS: all passed",
                  f"the install harness passed ({passed} checks)", ((r.stderr or "")[-3000:] + "\n".join(lines[-8:])).strip())
        # Milestone 2: the launch harness, and BCD Host's own reader on the request files it writes.
        launch_exe = work / "launch-harness"
        build = subprocess.run([swiftc, "-parse-as-library", "-swift-version", "5", "-Onone"] + sanitize + ["-o", str(launch_exe), str(R / "tests/host/epic_launch_checks.swift")]
                               + [str(CONTENT / f) for f in CONTENT_HOST_FILES] + [str(EPIC / f) for f in LAUNCH_HOST_FILES], capture_output=True, text=True)
        check(build.returncode == 0, "the launch harness compiles against the production files" + (" (with AddressSanitizer)" if sanitize else ""), build.stderr[-6000:])
        if build.returncode == 0:
            out = work / "requests"
            out.mkdir()
            r = subprocess.run([str(launch_exe)], capture_output=True, text=True, timeout=600, env=dict(os.environ, SWIFT_BACKTRACE="enable=no", EPIC_LAUNCH_OUT=str(out)))
            lines = [l for l in r.stdout.splitlines() if not l.startswith("LOG ")]
            passed = sum(1 for l in lines if l.startswith("PASS"))
            for l in lines:
                if l.startswith("FAIL"):
                    print("  " + l)
            check(r.returncode == 0 and passed >= 120 and lines and lines[-1] == "EPIC-LAUNCH-HARNESS: all passed",
                  f"the launch harness passed ({passed} checks)", ((r.stderr or "")[-3000:] + "\n".join(lines[-8:])).strip())
            # The preview app's sample words are the app's (the engine's files are not in the preview build).
            ask_src = (R / "app/BCDPreview/BCDPreviewEpicAsk.swift").read_text()
            page_src = (R / "app/BCDPreview/BCDGamePageSample.swift").read_text()
            samples_seen = [l[len("SAMPLE "):].split("|") for l in r.stdout.splitlines() if l.startswith("SAMPLE ")]
            words_seen = dict(l[len("WORDS "):].split("|", 1) for l in r.stdout.splitlines() if l.startswith("WORDS "))
            check(len(samples_seen) == 3 and all(f'"{m}"' in ask_src and f'title = "{t}"' in ask_src for _, t, m, _a in samples_seen),
                  "the preview's three sample questions (BCDPreviewEpicAsk.swift) say what BCDEpicPrompt says", samples_seen)
            check(all(f'title: "{a}"' in ask_src for _, _t, _m, answers in samples_seen for a in answers.split(",")), "...with the same buttons")
            check(len(words_seen) == 4 and all(f'"{words_seen[k]}"' in page_src for k in ("token", "online", "gta")) and f'"{words_seen["eos"]}"' in page_src,
                  "the preview's game-page notes (BCDGamePageSample.swift) are BCDEpicRules' words: the token note, the online note, GTA V's reason, the Epic Online Services note", words_seen)
            cc = shutil.which(os.environ.get("CC_HOST", "cc"))
            if cc and (out / "samples.json").exists():
                import json
                reader = work / "parse-file"
                built = subprocess.run([cc, "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-I", str(R / "bcd-host/src"), "-o", str(reader),
                                        str(R / "bcd-host/tests/parse_file.c"), str(R / "bcd-host/src/request.c")], capture_output=True, text=True)
                check(built.returncode == 0, "bcd-host's own reader (request.c) builds for the cross-check", built.stderr[-2000:])
                if built.returncode == 0:
                    samples = json.loads((out / "samples.json").read_text())
                    for sample in samples:
                        got = subprocess.run([str(reader), str(out / (sample["name"] + ".req"))], capture_output=True, text=True).stdout
                        fields = dict(l.split("=", 1) for l in got.splitlines() if "=" in l)
                        if sample["parses"]:
                            program = sample["program"]
                            folder = sample["cwd"] or (program.rsplit("\\", 1)[0] if program.count("\\") > 1 else program[:3])
                            cmdline = '"' + program + '"' + (" " + sample["arguments"] if sample["arguments"] else "")
                            check(fields.get("flags") == str(sample["flags"]) and fields.get("program") == program and fields.get("cwd") == folder
                                  and fields.get("cmdline") == cmdline and fields.get("ovt") == sample["ovt"],
                                  f"the C reader understands the Swift writer's {sample['name']!r} request exactly", got[:600])
                        else:
                            check(re.fullmatch(r"error=\d+", got.strip()) is not None, f"the C reader refuses the {sample['name']!r} request the writer made on purpose", got[:200])
    finally:
        shutil.rmtree(work, ignore_errors=True)

# ---- the preview app -----------------------------------------------------------------------
if shutil.which("xcrun"):
    if args.require_preview or os.environ.get("BCD_EPIC_PREVIEW"):
        with tempfile.TemporaryDirectory(prefix="bcd-epic-preview-") as tmp:
            built = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp], capture_output=True, text=True)
            check(built.returncode == 0, "the preview app builds with the Epic card in Settings › Accounts", (built.stderr or built.stdout)[-2000:])
else:
    check(not args.require_preview, "the preview app builds (needs xcrun)")

print(f"check-epic: {failures} FAILED" if failures else "check-epic: all passed")
sys.exit(1 if failures else 0)
