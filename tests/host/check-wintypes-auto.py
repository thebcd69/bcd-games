#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's wintypes.dll for a program that imports the WinRT buffer API set (round 677, the owner's decision of 2026-11-19; agent S1 S1-S8).

Unity IL2CPP's GameAssembly.dll imports api-ms-win-core-winrt-robuffer-l1-1-0.dll, which Wine maps to wintypes.dll, and BCD's 64-bit
set has wintypes.dll only with env.MADEIRA_WINTYPES = 1: every archive log with "import_dll Library api-ms-win-core-winrt-robuffer...
not found" had no frame (36 of 36). BCDWinTypesAuto (app/Madeira/LibraryBCD.swift) reads the program folder's 64-bit DLLs and EXEs
before a start and turns the key on for that session when one imports it.

Checks (the enum cut out of LibraryBCD.swift and compiled on the host with synthetic PE files):
  1. imports(of:) reads a PE32+ import table; a PE32 (32-bit) file, a file that is not a PE, an empty file give nothing;
  2. importer(in:) finds the API set or wintypes.dll, GameAssembly.dll first, top level only, not in a folder without them;
  3. prepare: on with an importer and nothing set; the game's own value (0) wins; a value already in the environment wins;
     the next start without an importer takes back the key it set; a key it did not set is never removed;
  4. both start paths call it (BCDLaunch.applyLibrary, ContentView.startDock) and the session end unsets the key; CI runs this check.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    if not cond:
        failures += 1


lib = (APP / "LibraryBCD.swift").read_text()
a = lib.index("enum BCDWinTypesAuto {")
enum = lib[a:lib.index("\n}\n", a) + 3]


def pe(imports, magic=0x20B):
    """A minimal PE with one section holding the import descriptors and the names."""
    b = bytearray(0x800)
    b[0:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, 0x80)
    b[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", b, 0x84, 0x8664 if magic == 0x20B else 0x14C, 1)          # machine, one section
    opt_size = 0xF0 if magic == 0x20B else 0xE0
    struct.pack_into("<H", b, 0x84 + 16, opt_size)
    opt = 0x98
    struct.pack_into("<H", b, opt, magic)
    dirs = opt + (112 if magic == 0x20B else 96)
    struct.pack_into("<II", b, dirs + 8, 0x2000, 20 * (len(imports) + 1))             # import directory
    sec = opt + opt_size
    struct.pack_into("<IIII", b, sec + 8, 0x1000, 0x2000, 0x400, 0x400)                # VirtualSize, VA, SizeOfRawData, PointerToRawData
    name_at = 0x2000 + 20 * (len(imports) + 1)
    for i, name in enumerate(imports):
        struct.pack_into("<I", b, 0x400 + 20 * i + 12, name_at)
        raw = 0x400 + (name_at - 0x2000)
        b[raw:raw + len(name) + 1] = name.encode() + b"\0"
        name_at += len(name) + 1
    return bytes(b)


swiftc = shutil.which("xcrun") and ["xcrun", "swiftc"] or (shutil.which("swiftc") and ["swiftc"])
if not swiftc:
    print("skip: no swiftc")
else:
    harness = '''import Foundation
final class LogStore { static let shared = LogStore(); func log(_ s: String) { print("LOG " + s) } }
''' + enum + '''
let a = CommandLine.arguments
switch a[1] {
case "imports": print(BCDWinTypesAuto.imports(of: URL(fileURLWithPath: a[2])).joined(separator: ","))
case "importer": if let r = BCDWinTypesAuto.importer(in: URL(fileURLWithPath: a[2])) { print("\\(r.file):\\(r.name)") } else { print("none") }
case "prepare":
    // a[2]: folder with an importer, a[3]: folder without one
    func env() -> String { getenv("MADEIRA_WINTYPES").map { String(cString: $0) } ?? "unset" }
    unsetenv("MADEIRA_WINTYPES")
    BCDWinTypesAuto.prepare(folder: URL(fileURLWithPath: a[2]), gameValue: nil); let s1 = env()
    BCDWinTypesAuto.prepare(folder: URL(fileURLWithPath: a[3]), gameValue: nil); let s2 = env()
    BCDWinTypesAuto.prepare(folder: URL(fileURLWithPath: a[2]), gameValue: "0"); let s3 = env()
    setenv("MADEIRA_WINTYPES", "0", 1)
    BCDWinTypesAuto.prepare(folder: URL(fileURLWithPath: a[2]), gameValue: nil); let s4 = env()
    BCDWinTypesAuto.prepare(folder: URL(fileURLWithPath: a[3]), gameValue: nil); let s5 = env()
    print("\\(s1) \\(s2) \\(s3) \\(s4) \\(s5)")
default: break
}
'''
    with tempfile.TemporaryDirectory(prefix="wintypes-auto-") as t:
        t = Path(t)
        (t / "main.swift").write_text(harness)
        b = subprocess.run(swiftc + ["-O", str(t / "main.swift"), "-o", str(t / "m")], capture_output=True, text=True)
        check(b.returncode == 0, "BCDWinTypesAuto builds on the host", b.stderr[-1500:])
        if b.returncode == 0:
            def run(*args):
                return subprocess.run([str(t / "m"), *map(str, args)], capture_output=True, text=True).stdout.strip()
            files = t / "files"
            files.mkdir()
            (files / "ga.dll").write_bytes(pe(["KERNEL32.dll", "api-ms-win-core-winrt-robuffer-l1-1-0.dll", "USER32.dll"]))
            (files / "plain.dll").write_bytes(pe(["KERNEL32.dll", "USER32.dll"]))
            (files / "x86.dll").write_bytes(pe(["api-ms-win-core-winrt-robuffer-l1-1-0.dll"], magic=0x10B))
            (files / "text.dll").write_bytes(b"not a program at all")
            (files / "empty.dll").write_bytes(b"")
            check(run("imports", files / "ga.dll") == "KERNEL32.dll,api-ms-win-core-winrt-robuffer-l1-1-0.dll,USER32.dll",
                  "a 64-bit program's import names are read in order", run("imports", files / "ga.dll"))
            check(run("imports", files / "x86.dll") == "" and run("imports", files / "text.dll") == "" and run("imports", files / "empty.dll") == "",
                  "a 32-bit program, a file that is not a PE and an empty file give nothing")
            game = t / "game"
            game.mkdir()
            (game / "Game.exe").write_bytes(pe(["KERNEL32.dll"]))
            (game / "UnityPlayer.dll").write_bytes(pe(["KERNEL32.dll", "wintypes.dll"]))
            (game / "GameAssembly.dll").write_bytes(pe(["api-ms-win-core-winrt-robuffer-l1-1-0.dll"]))
            check(run("importer", game) == "GameAssembly.dll:api-ms-win-core-winrt-robuffer-l1-1-0.dll",
                  "GameAssembly.dll is read first and names the API set", run("importer", game))
            other = t / "other"
            other.mkdir()
            (other / "Other.exe").write_bytes(pe(["KERNEL32.dll"]))
            (other / "sub").mkdir()
            (other / "sub" / "GameAssembly.dll").write_bytes(pe(["api-ms-win-core-winrt-robuffer-l1-1-0.dll"]))
            (other / "x86.dll").write_bytes(pe(["wintypes.dll"], magic=0x10B))
            check(run("importer", other) == "none", "top level only, 64-bit only: a folder without such a program gives nothing", run("importer", other))
            out = subprocess.run([str(t / "m"), "prepare", str(game), str(other)], capture_output=True, text=True).stdout.splitlines()
            states = out[-1] if out else ""
            check(states == "1 unset unset 0 0",
                  "prepare: on with an importer; taken back by the next start without one; the game's 0 wins; a value already set wins "
                  "and is never removed", out)
            check(sum(l.startswith("LOG [wintypes-auto] GameAssembly.dll imports api-ms-win-core-winrt-robuffer-l1-1-0.dll") for l in out) == 1,
                  "one [wintypes-auto] line, only when it turned the key on", out)

cv = (APP / "ContentView.swift").read_text()
bridge = (APP / "WineProcessBridge.m").read_text()
check("BCDWinTypesAuto.prepare(folder: LibraryEntry.hostURL(ofWindowsPath: entry.launchWindowsPath)?.deletingLastPathComponent(),\n"
      "                                gameValue: profile.get(\"env.MADEIRA_WINTYPES\"))" in lib
      and "BCDWinTypesAuto.prepare(folder: installFolder," in cv,
      "BCDLaunch.applyLibrary and ContentView.startDock call it, with the game's own value")
check('unsetenv("MADEIRA_WINTYPES");' in bridge, "the session's end unsets the key (so it never reaches the next game)")
check("run: python3 tests/host/check-wintypes-auto.py" in (R / ".github/workflows/build-ipa.yml").read_text(), "CI runs this check")

print(("PASS" if not failures else "FAILED") + ": wintypes for a program that imports the WinRT buffer API set")
sys.exit(1 if failures else 0)
