#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The files BCD writes carry BCD's names (owner, 2026-10-18: no "Madeira" in what a person
sees; the Files app shows Documents as "On My iPhone > BCD").

Every device installs BCD fresh (its own bundle ID), so there is no migration and no old name
is read: Documents/bcd.cfg is the one configuration file (madeira.cfg and the one-value
madeira-<key>.txt files before it are never read), bcd-log.txt / bcd-log.prev.txt the log,
bcd-library.json the library, bcd-art/ its covers, bcd-controls.json, bcd-control-presets.json
and bcd-input.json the controls, bcd-reset-backup/ the Dock reset's backups, BCD-saves-<date>.zip
the saves export; in the prefix bcd-dock-installs.json, *.reg.bcd-bak, C:\\bcd-games,
C:\\bcd-dock-installers.cmd/.result and C:\\bcd-cs.

1. build/madeira_cfg.h compiled into a small reader: bcd.cfg is read; madeira.cfg and
   madeira-<key>.txt are not, alone or beside bcd.cfg; the game's own file still wins.
2. app/Madeira/MadeiraConfig.swift compiled on its own (swiftc): the same from Swift, and set()
   writes bcd.cfg without touching (or reading) the old files.
3. Each reader and writer names the new file (a table of source lines).
4. No string literal in the app, build/ or madeira-d3d12/ sources names a file by an old name;
   the few madeira names that stay are listed below with the reason.

Run with python3; --require-swift fails when swiftc is missing (the macOS build runner).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CC = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc")
failures = []


def check(ok, what, detail=None):
    print(("ok:   " if ok else "FAIL: ") + what + ("" if ok or detail is None else "\n      " + str(detail)))
    if not ok:
        failures.append(what)


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


# ---- 1. the native reader -------------------------------------------------------------------
HARNESS_C = r'''
#include <stdio.h>
#include "madeira_cfg.h"
int main(int argc, char **argv)
{
    int i;
    printf("present=%d\n", madeira_cfg_present());
    for (i = 1; i < argc; i++) {
        char v[256];
        int found = madeira_cfg_get(argv[i], v, sizeof v);
        printf("%s=%s\n", argv[i], found ? v : "<unset>");
    }
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="bcd-file-names-") as tmp:
    tmp = Path(tmp)
    (tmp / "c.c").write_text(HARNESS_C)
    cc = subprocess.run([CC, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                         "-I", str(ROOT / "build"), str(tmp / "c.c"), "-o", str(tmp / "c")],
                        capture_output=True, text=True) if CC else None
    check(cc is not None and cc.returncode == 0, "build/madeira_cfg.h compiles into a reader on the host",
          cc.stderr[-2000:] if cc else "no C compiler")
    check('#define MADEIRA_CFG_FILE "bcd.cfg"' in read("build/madeira_cfg.h"), "the native reader's file is bcd.cfg")

    if cc is not None and cc.returncode == 0:
        def native(docs, game=None, keys=("pool", "wx", "vram-mb")):
            env = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_") and k != "CFFIXED_USER_HOME"}
            env.update(MADEIRA_DOCS_DIR=str(docs), HOME=str(tmp))
            if game is not None:
                env["MADEIRA_CFG_GAME"] = str(game)
            out = subprocess.run([str(tmp / "c")] + list(keys), capture_output=True, text=True, env=env, check=True).stdout
            return dict(line.split("=", 1) for line in out.split())

        def documents(name, files):
            d = tmp / name
            d.mkdir()
            for file, text in files.items():
                (d / file).write_text(text)
            return d

        old_files = {"madeira.cfg": "pool = 512\nwx = 0\n", "madeira-vram-mb.txt": "3072\n", "madeira-pool.txt": "640\n"}
        r = native(documents("new-only", {"bcd.cfg": "pool = 896\nwx = 1\n"}))
        check(r == {"present": "1", "pool": "896", "wx": "1", "vram-mb": "<unset>"}, "bcd.cfg alone: read", r)
        r = native(documents("old-only", old_files))
        check(r == {"present": "0", "pool": "<unset>", "wx": "<unset>", "vram-mb": "<unset>"},
              "only madeira.cfg and madeira-<key>.txt: not read, every key unset", r)
        both = documents("both", dict(old_files, **{"bcd.cfg": "wx = 1\n"}))
        r = native(both)
        check(r == {"present": "1", "pool": "<unset>", "wx": "1", "vram-mb": "<unset>"},
              "bcd.cfg beside the old files: bcd.cfg only, nothing merged from them", r)
        check((both / "madeira.cfg").read_text() == old_files["madeira.cfg"] and (both / "madeira-pool.txt").exists(),
              "the native reader leaves the old files as they were")
        (tmp / "game.cfg").write_text("pool = 384\n")
        r = native(both, game=tmp / "game.cfg")
        check(r["pool"] == "384" and r["wx"] == "1", "the game's own file still wins over bcd.cfg", r)
        r = native(documents("none", {}))
        check(r == {"present": "0", "pool": "<unset>", "wx": "<unset>", "vram-mb": "<unset>"}, "no file: every key unset", r)

    # ---- 2. the Swift reader and writer ---------------------------------------------------------
    config_swift = read("app/Madeira/MadeiraConfig.swift")
    check('static let fileName = "bcd.cfg"' in config_swift, "MadeiraConfig.fileName is bcd.cfg")
    check(not any(s in config_swift for s in ("migrateLegacy", "deleteLegacyFiles", "legacyKeys", '"madeira-\\(')),
          "no migration and no madeira-<key>.txt reader left in MadeiraConfig")
    harness_swift = r'''
import Foundation
var failed = 0
func expect(_ cond: Bool, _ what: String) { print((cond ? "ok:   " : "FAIL: ") + "Swift: " + what); if !cond { failed += 1 } }
let docs = MadeiraConfig.documents!
try? FileManager.default.createDirectory(at: docs, withIntermediateDirectories: true)
let old = docs.appendingPathComponent("madeira.cfg"), oldOne = docs.appendingPathComponent("madeira-pool.txt")
try! "pool = 512\nwx = 0\n".write(to: old, atomically: true, encoding: .utf8)
try! "640\n".write(to: oldOne, atomically: true, encoding: .utf8)
expect(MadeiraConfig.url?.lastPathComponent == "bcd.cfg", "the file is Documents/bcd.cfg")
expect(!MadeiraConfig.present && MadeiraConfig.get("pool") == nil && MadeiraConfig.get("wx") == nil,
       "only the old files: nothing is read")
expect(MadeiraConfig.set("swap-mb", "2048"), "set() writes")
let new = docs.appendingPathComponent("bcd.cfg")
expect((try? String(contentsOf: new, encoding: .utf8)) == "swap-mb = 2048\n", "set() creates bcd.cfg with that one line")
expect((try? String(contentsOf: old, encoding: .utf8)) == "pool = 512\nwx = 0\n" && FileManager.default.fileExists(atPath: oldOne.path),
       "set() leaves madeira.cfg and madeira-pool.txt as they were")
expect(MadeiraConfig.present && MadeiraConfig.get("swap-mb") == "2048" && MadeiraConfig.get("pool") == nil,
       "bcd.cfg is read; the old files still are not")
expect(MadeiraConfig.gameURL?.lastPathComponent == "bcd-game.cfg", "a library game's merged lines go to bcd-game.cfg")
// The merged game config (the entry's lines, then the game's own GameConfigs file): its own
// comment line says BCD. Only inside the scratch home (CFFIXED_USER_HOME), never the real one.
let home = ProcessInfo.processInfo.environment["HOME"]!
if let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first,
   support.standardizedFileURL.path.hasPrefix(URL(fileURLWithPath: home).standardizedFileURL.path) {
    let configs = support.appendingPathComponent("GameConfigs", isDirectory: true)
    try! FileManager.default.createDirectory(at: configs, withIntermediateDirectories: true)
    let own = configs.appendingPathComponent("0123456789abcdef.cfg")
    try! "pool = 384\n".write(to: own, atomically: true, encoding: .utf8)
    setenv("MADEIRA_CFG_GAME", own.path, 1)
    let pairs = (try? MadeiraConfig.applyGame("fence-chain = 6\n")) ?? [:]
    let merged = (try? String(contentsOf: MadeiraConfig.gameURL!, encoding: .utf8)) ?? ""
    expect(pairs["pool"] == "384" && pairs["fence-chain"] == "6"
           && getenv("MADEIRA_CFG_GAME").map { String(cString: $0) } == MadeiraConfig.gameURL!.path,
           "applyGame merges the entry's lines and the game's file into bcd-game.cfg")
    expect(merged.contains("# BCD: the game's own file (0123456789abcdef.cfg)")
           && merged.range(of: "madeira", options: .caseInsensitive) == nil,
           "the merged game config says nothing of Madeira")
} else {
    print("skip: Application Support is outside the scratch home here; the merged game config is checked by source")
}
exit(failed == 0 ? 0 : 1)
'''
    if SWIFTC:
        swift_copy = config_swift
        docs_expr = "FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first"
        check(docs_expr in swift_copy, "MadeiraConfig.documents is the app's Documents folder")
        # As check-cfg-early-docs.py: Linux Foundation takes .documentDirectory from the passwd home.
        swift_copy = swift_copy.replace(docs_expr, 'URL(fileURLWithPath: ProcessInfo.processInfo.environment["HOME"]! + "/Documents")')
        (tmp / "MadeiraConfig.swift").write_text(swift_copy)
        (tmp / "main.swift").write_text(harness_swift)
        build = subprocess.run([SWIFTC, str(tmp / "MadeiraConfig.swift"), str(tmp / "main.swift"), "-o", str(tmp / "s")],
                               capture_output=True, text=True)
        check(build.returncode == 0, "MadeiraConfig.swift compiles on its own", build.stderr[-2000:])
        if build.returncode == 0:
            home = tmp / "swift-home"
            home.mkdir()
            env = dict(os.environ, HOME=str(home), CFFIXED_USER_HOME=str(home))
            r = subprocess.run([str(tmp / "s")], capture_output=True, text=True, env=env)
            sys.stdout.write(r.stdout)
            check(r.returncode == 0, "the Swift reader and writer use bcd.cfg only", r.stderr[-1000:])
    elif "--require-swift" in sys.argv:
        check(False, "swiftc is required (--require-swift)")
    else:
        print("skip: swiftc not found, the Swift reader is checked by source only")

# ---- 3. every reader and writer names the new file ---------------------------------------------
NEW = [
    ("app/Madeira/MadeiraConfig.swift", '.appendingPathComponent("bcd-game.cfg")', 1),
    ("app/Madeira/LogStore.swift", 'logFileURL = docs.appendingPathComponent("bcd-log.txt")', 1),
    ("app/Madeira/LogStore.swift", 'appendingPathComponent("bcd-log.prev.txt")', 2),
    ("app/Madeira/WineProcessBridge.m", '[docs stringByAppendingPathComponent:@"bcd-log.txt"]', 2),
    ("app/Madeira/WineProcessBridge.m", 'stringByAppendingPathComponent:@MADEIRA_CFG_FILE]', 1),
    ("app/Madeira/WineServerBridge.m", '[docs stringByAppendingPathComponent:@"bcd-log.txt"]', 1),
    ("build/ntdll-unix/process_ios.c", '"%s/bcd-log.txt", docs', 1),
    ("app/Madeira/AutoTune.swift", '[docs.appendingPathComponent("bcd-log.prev.txt")]', 1),
    ("app/Madeira/LogUpload.swift", '["bcd-log.txt", "bcd-log.prev.txt"]', 1),
    ("app/Madeira/StorageModel.swift", 'appendingPathComponent("bcd-log.prev.txt")', 1),
    ("app/Madeira/Library.swift", 'Self.documents.appendingPathComponent("bcd-library.json")', 1),
    # Wave 3 (2026-10-19): the classic library's cover reads and Game details' cover chooser are deleted; BCD's
    # session art (BCDSessionLive) and game page (BCDGamePageLive) name the same folder.
    ("app/Madeira/BCDSessionLive.swift", 'appendingPathComponent("bcd-art/" + URL(fileURLWithPath: name).lastPathComponent)', 1),
    ("app/Madeira/BCDGamePageLive.swift", 'LibraryModel.documents.appendingPathComponent("bcd-art", isDirectory: true)', 1),
    ("app/Madeira/Library.swift", 'var servicesScriptPath: String { "C:\\\\bcd-games\\\\', 1),
    ("app/Madeira/BCDLiveStore.swift", 'appendingPathComponent("bcd-art/" + URL(fileURLWithPath: name).lastPathComponent)', 1),
    ("app/Madeira/TouchControlPresets.swift", '.appendingPathComponent("bcd-control-presets.json")', 1),
    ("app/Madeira/ContentView.swift", '.appendingPathComponent("bcd-controls.json")', 1),
    ("app/Madeira/ContentView.swift", '.appendingPathComponent("bcd-input.json")', 1),
    # (bcd-exe.txt and bcd-args.txt were read by the developer interface's Custom exe button, deleted in wave 3.)
    ("app/Madeira/ContentView.swift", 'appendingPathComponent("bcd-d3d12-canary.log")', 1),
    ("app/Madeira/DockReset.swift", 'documents.appendingPathComponent("bcd-reset-backup", isDirectory: true)', 2),
    ("app/Madeira/DockReset.swift", 'summary.backup = "bcd-reset-backup/" + name', 1),
    # (The classic Dock sheet's Reset question named the folder; BCD's, in BCDDock.swift, says "a backup folder in BCD's Documents".)
    ("app/Madeira/DockInstallers.swift", 'moved to Documents/bcd-reset-backup;', 1),
    ("app/Madeira/DockInstallers.swift", 'static let fileName = "bcd-dock-installs.json"', 1),
    ("app/Madeira/DockInstallers.swift", 'static let scriptName = "bcd-dock-installers.cmd"', 1),
    ("app/Madeira/DockInstallers.swift", 'static let resultName = "bcd-dock-installers.result"', 1),
    ("app/Madeira/DockInstallers.swift", 'directory.appendingPathComponent("bcd-game-installs.cfg")', 1),
    ("app/Madeira/DockInstallers.swift", 'registry.appendingPathExtension("bcd-bak")', 3),   # 578: the CEG service key's backup too; 591: the install-script registry values
    ("app/Madeira/SavesAndShortcuts.swift", 'appendingPathComponent("BCD-saves-\\(BCDFormat.fileStamp(Date(), seconds: false)).zip")', 1),
    ("app/Madeira/GameLibrary.swift", 'static let backupSuffix = ".bcd-orig"', 1),
    ("build/ntdll-unix/virtual_ios.c", 'const char *f = "/bcd-retire-trace.txt";', 1),
    ("madeira-d3d12/src/unix/madeira_ir_unix.mm", '"%s/bcd-failed-shaders", docs', 1),
    ("madeira-d3d12/src/unix/madeira_ir_unix.mm", '"%s/Documents/bcd-failed-shaders", home', 1),
    ("madeira-d3d12/src/pe/madeira_d3d12.c", 'CreateDirectoryA("C:\\\\bcd-cs", NULL);', 2),
]
for rel, text, count in NEW:
    n = read(rel).count(text)
    check(n == count, f"{rel}: {text} ({count}x)", f"found {n}x")
# The comment lines BCD writes into bcd.cfg, a game's own file, the merged game config and
# auto-tune's launch file name no Madeira (owner, 2026-10-18; the keys and env.MADEIRA_* names
# stay). GameRecommendations.swift's "# Madeira's recommended settings for ..." lines are the
# recommendations work's to change.
WRITERS = {
    "app/Madeira/GameProfiles.swift": '["# BCD settings for \\(windowsPath)", "# bcd.cfg syntax; keys here win over bcd.cfg."]',
    "app/Madeira/MadeiraConfig.swift": '"# BCD: the game\'s own file (\\(profileURL.lastPathComponent)), after the lines above so it wins\\n"',
    "app/Madeira/AutoTune.swift": '"# BCD auto-tune, attempt \\(s.attempts.count) for \\(entry.title)\\n"',
}
for rel, line in WRITERS.items():
    check(line in read(rel), f"{rel}: the config comment it writes is {line}")
bridge = read("app/Madeira/WineProcessBridge.m")
check("madeira-env.txt" not in bridge, "WineProcessBridge.m: env lines come from bcd.cfg only (no madeira-env.txt)")
check(bridge.count('LOG("bcd.cfg env: %{public}s=%{public}s"') == 1, "the log names bcd.cfg for each env line it exports")
check('logStore.log("bcd.cfg: absent")' in read("app/Madeira/ContentView.swift"),
      "the launch log names bcd.cfg when it is absent (no 'legacy files apply')")

# ---- 4. no old name in a string literal ---------------------------------------------------------
def literals(text, c_like):
    """Yield (line, content) of each string literal, comments skipped. Swift: nested block
    comments, multi-line and raw strings, \\( ) interpolation (its strings yielded too)."""
    i, n, line = 0, len(text), 1
    out = []

    def string(i, line, hashes, multi):
        quote = '"""' if multi else '"'
        close = quote + "#" * hashes
        start_line, buf = line, []
        while i < n:
            if text.startswith(close, i):
                out.append((start_line, "".join(buf)))
                return i + len(close), line
            ch = text[i]
            if ch == "\\" and text.startswith("#" * hashes, i + 1):
                j = i + 1 + hashes
                if not c_like and j < n and text[j] == "(":
                    out.append((start_line, "".join(buf)))
                    buf = []
                    i, line = code(j + 1, line, depth=1)
                    continue
                buf.append(text[i:j + 1])
                line += text[i:j + 1].count("\n")
                i = j + 1
                continue
            if ch == "\n":
                line += 1
                if not multi and c_like:
                    pass
            buf.append(ch)
            i += 1
        out.append((start_line, "".join(buf)))
        return i, line

    def code(i, line, depth=0):
        while i < n:
            ch = text[i]
            if ch == "\n":
                line += 1
                i += 1
            elif text.startswith("//", i):
                j = text.find("\n", i)
                i = n if j < 0 else j
            elif text.startswith("/*", i):
                level, i = 1, i + 2
                while i < n and level:
                    if not c_like and text.startswith("/*", i):
                        level, i = level + 1, i + 2
                    elif text.startswith("*/", i):
                        level, i = level - 1, i + 2
                    else:
                        line += text[i] == "\n"
                        i += 1
            elif c_like and ch == "'":
                j = i + 1
                while j < n and text[j] != "'" and text[j] != "\n":
                    j += 2 if text[j] == "\\" else 1
                i = j + 1
            elif ch == '"' or (not c_like and ch == "#" and re.match(r'#+"', text[i:i + 8])):
                hashes = 0
                while text[i + hashes] == "#":
                    hashes += 1
                j = i + hashes
                multi = not c_like and text.startswith('"""', j)
                i, line = string(j + (3 if multi else 1), line, hashes, multi)
            elif c_like and ch == "@" and text.startswith('@"', i):
                i += 1
            elif depth and ch == "(":
                depth += 1
                i += 1
            elif depth and ch == ")":
                depth -= 1
                i += 1
                if not depth:
                    return i, line
            else:
                i += 1
        return i, line

    code(0, 1)
    return out


# A file or folder named after Madeira in a string literal: madeira.cfg, madeira-log.txt,
# madeira-art/, Madeira-saves-..., the legacy madeira-<key>.txt builders, C:\madeira-games ...
OLD = re.compile(r'(?i)\bmadeira(?:[-_.][a-z0-9_.-]*)?\.(cfg|txt|log|json|zip|bak|cmd|result|bin|flag|csv|dat|orig)(?![a-z0-9_-])'
                 r'|\bmadeira-(art|reset-backup|failed-shaders|games|cs|bak|orig|saves)(?![a-z0-9_-])'
                 r'|(?-i:\bmadeira-(\\\(|%s|$))')
# The madeira names that stay, each with its reason.
KEPT = {
    "madeira-dock.txt": "C:\\madeira-dock.txt, BCD Dock's report: the Dock program (madeira-dock submodule) writes it",
    "madeira-continue.flag": "C:\\madeira-continue.flag: the prebuilt tests/net/winhttp-test.exe waits for it (developer net test)",
    "madeira-swap.bin": "the swap tier's backing file in tmp/, not in Documents",
    "madeira-games": "SwiftItch/ItchInstall.swift's skip list: a folder name the search for an installer's new folder passes over "
                     "(upstream Madeira's games folder in a shared prefix); BCD writes no such folder (round 632)",
}
SCAN = [("app/Madeira", (".swift",), False), ("app/Madeira", (".m", ".mm", ".c", ".h"), True),
        ("build", (".c", ".h", ".m", ".mm", ".cpp"), True), ("madeira-d3d12/src", (".c", ".h", ".mm", ".cpp"), True)]
found, kept_seen, scanned = [], set(), 0
listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "app/Madeira", "build", "madeira-d3d12/src"],
                        capture_output=True, text=True).stdout.split()
for top, exts, c_like in SCAN:
    for rel in listed:
        if not rel.startswith(top + "/") or not rel.endswith(exts) or rel.endswith("ConfigCatalog.generated.swift"):
            continue
        scanned += 1
        for line, content in literals(read(rel), c_like):
            for m in OLD.finditer(content):
                keep = next((k for k in KEPT if k in content[max(0, m.start() - 24):m.end() + 24]), None)
                if keep:
                    kept_seen.add(keep)
                else:
                    found.append(f"{rel}:{line}: \"{content[max(0, m.start() - 30):m.end() + 30]}\"")
check(scanned > 200, f"string literals of {scanned} source files scanned")
LEFT_TO_OTHERS = {"app/Madeira/GameRecommendations.swift": "the recommended lists (the recommendations work)"}
comments, left = [], set()
for rel in listed:
    if not rel.startswith("app/Madeira/") or not rel.endswith(".swift") or rel.endswith("ConfigCatalog.generated.swift"):
        continue
    for line, content in literals(read(rel), False):
        for text in content.split("\n"):
            if text.strip().startswith("#") and re.search(r"(?i)madeira(?!_)", text.replace("MADEIRA_", "")):
                if rel in LEFT_TO_OTHERS:
                    left.add(rel)
                else:
                    comments.append(f"{rel}:{line}: {text.strip()[:90]}")
check(not comments, "no config comment line BCD writes names Madeira", "\n      ".join(comments[:20]))
for rel in sorted(left):
    print(f"note: {rel} still writes '# Madeira ...' comment lines: {LEFT_TO_OTHERS[rel]}")
check(not found, "no string literal names a file by an old madeira name", "\n      ".join(found[:40]))
for name, why in KEPT.items():
    print(f"kept: {name} -- {why}" + ("" if name in kept_seen else " (not used any more: drop it from KEPT)"))

# The scanner itself: it must see a literal and must not see a comment.
probe = literals('let a = "x" // "madeira.cfg"\n/* "madeira-log.txt" /* nested */ */ let b = "madeira-art/\\(c("madeira.cfg"))"\n', False)
check([c for _, c in probe] == ["x", "madeira-art/", "madeira.cfg", ""], "the literal scanner skips comments and reads interpolations",
      probe)
probe = literals("char q = '\"'; const char *p = \"madeira-log.txt\"; /* \"madeira.cfg\" */\n", True)
check([c for _, c in probe] == ["madeira-log.txt"], "the C literal scanner skips char literals and comments", probe)
check(all(OLD.search(s) for s in ("madeira.cfg", "madeira-log.prev.txt", "madeira-art/", "Madeira-saves-2026.zip",
                                  "madeira-\\(key).txt", "C:\\\\madeira-games\\\\", "system.reg.madeira-bak"))
      and not any(OLD.search(s) for s in ("bcd.cfg", "madeira-bcd: x", "[madeira-env] game", "madeira_d3d12.dll",
                                          "madeira.library.avx", "madeira-jit-request", "madeira.bcd.log-upload",
                                          "X-Madeira-", "madeira-template-", "MADEIRA_SWAP_MIN_KB, x")),
      "the old-name pattern finds file names and leaves identifiers and log tags")

print("PASS: BCD's file names" if not failures else f"FAILED: {len(failures)} check(s)")
sys.exit(1 if failures else 0)
