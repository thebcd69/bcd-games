#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Night round 2026-11-11 (A1 S7, A2 S7; SQDE4K, T12JFC, AT5CSS): which Valve client build is in the prefix (SteamRuntimeFiles.clientStatus), the set
of builds the Dock host supports, and the rename-aside of an unsupported client ("Replace client components"). SteamRuntime.swift compiled on the
host with a stub for the log and the Wine session checks; nothing is downloaded and no Wine runs.

Checks:
  - the supported set is the pinned build plus the 202609 build, and equals the table of the Dock host's client_layout.c (madeira-dock of this tree or
    ~/madeira-bcd/madeira-dock; skipped without it);
  - clientStatus: no file is .missing; a file whose hash is in the set is .supported; another is .unsupported(sha) with the file's own SHA-256 (as
    shasum reports it), also for a file of several megabytes read in pieces; the words name the first 12 digits and Settings;
  - moveClientAside renames the folder (nothing deleted, the files are in the new place), a second unsupported folder gets a number, and
    moveSteamapps puts steamapps into the new client folder, but never over one that is there;
  - source invariants: validate() asks clientStatus before the host starts, the result-30 words name Replace client components, the Settings card
    calls replaceComponents, and prepare(replacing:) renames only after the download was verified (the pinned hash check comes first).
"""
from pathlib import Path
import hashlib, os, re, shutil, subprocess, sys, tempfile
root = Path(__file__).resolve().parents[2]
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
app = root / 'app/Madeira'
runtime = (app / 'SteamRuntime.swift').read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


pinned = re.search(r'static let clientSHA256 = "([0-9a-f]{64})"', runtime).group(1)
second = re.search(r'supportedClientSHA256: Set<String> = \[clientSHA256, "([0-9a-f]{64})"\]', runtime)
check("the supported set is the pinned build and one more", second is not None)
layouts = next((d / 'src/client_layout.c' for d in (root / 'madeira-dock', Path.home() / 'madeira-bcd/madeira-dock') if (d / 'src/client_layout.c').is_file()), None)
if layouts and second:
    table = re.findall(r'^\s*\{\s*\n?\s*"([0-9a-f]{64})"', layouts.read_text(), re.M)
    check("the supported set equals the Dock host's client_layout.c table", set(table) == {pinned, second.group(1)}, table)
else:
    print("skip: the Dock source is not at hand (madeira-dock submodule)")

main = r'''
import Foundation
let fm = FileManager.default
let drive = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
defer { try? fm.removeItem(at: drive) }
let root = drive.appendingPathComponent(SteamRuntimeFiles.relativeRoot)
try fm.createDirectory(at: root, withIntermediateDirectories: true)
assert(SteamRuntimeFiles.clientStatus(drive: drive) == .missing)
let dll = root.appendingPathComponent("steamclient64.dll")
var big = Data(); for i in 0..<(3 << 20) { big.append(UInt8(truncatingIfNeeded: i &* 31 &+ (i >> 8))) }   // 3 MB: read in pieces
try big.write(to: dll)
let sha = CommandLine.arguments[1]
guard case .unsupported(let got) = SteamRuntimeFiles.clientStatus(drive: drive) else { fatalError("a client outside the set must be unsupported") }
assert(got == sha, "sha \(got) vs \(sha)")
assert(SteamRuntimeFiles.clientStatus(drive: drive, supported: [sha]) == .supported)
assert(SteamRuntimeFiles.clientStatus(drive: drive, supported: SteamRuntimeFiles.supportedClientSHA256) == .unsupported(sha: sha))
assert(SteamRuntimeFiles.supportedClientSHA256.count == 2 && SteamRuntimeFiles.supportedClientSHA256.contains(SteamRuntimeFiles.clientSHA256))
let words = SteamRuntimeFiles.unsupportedClientWords(sha: sha)
assert(words.contains(String(sha.prefix(12))) && words.contains("Replace client components") && words.contains("Settings"))
// the rename: the folder moves, nothing is deleted, steamapps follows into the new client folder
try fm.createDirectory(at: root.appendingPathComponent("steamapps/common/Game"), withIntermediateDirectories: true)
try Data("manifest".utf8).write(to: root.appendingPathComponent("steamapps/appmanifest_1.acf"))
let aside = try SteamRuntimeFiles.moveClientAside(root: root, sha: sha)
assert(aside.lastPathComponent == "Steam.unsupported-" + sha.prefix(12) && !fm.fileExists(atPath: root.path))
assert(fm.fileExists(atPath: aside.appendingPathComponent("steamclient64.dll").path) && fm.fileExists(atPath: aside.appendingPathComponent("steamapps/appmanifest_1.acf").path))
try fm.createDirectory(at: root, withIntermediateDirectories: true)
try Data("new".utf8).write(to: root.appendingPathComponent("steam.exe"))
assert(SteamRuntimeFiles.moveSteamapps(from: aside, into: root))
assert(fm.fileExists(atPath: root.appendingPathComponent("steamapps/appmanifest_1.acf").path) && !fm.fileExists(atPath: aside.appendingPathComponent("steamapps").path))
assert(!SteamRuntimeFiles.moveSteamapps(from: aside, into: root), "nothing to move twice")
// a second unsupported folder of the same build gets a number and the first is kept
let second = try SteamRuntimeFiles.moveClientAside(root: root, sha: sha)
assert(second.lastPathComponent == "Steam.unsupported-\(sha.prefix(12))-2" && fm.fileExists(atPath: aside.path) && fm.fileExists(atPath: second.appendingPathComponent("steam.exe").path))
// steamapps is never moved over one that is there
let again = drive.appendingPathComponent("Program Files (x86)/Steam")
try fm.createDirectory(at: again.appendingPathComponent("steamapps"), withIntermediateDirectories: true)
try fm.createDirectory(at: second.appendingPathComponent("steamapps"), withIntermediateDirectories: true)
assert(!SteamRuntimeFiles.moveSteamapps(from: second, into: again))
print("PASS: clientStatus, the supported set, the rename aside and the steamapps move")
'''
with tempfile.TemporaryDirectory(prefix='madeira-client-status-') as tmp:
    tmp = Path(tmp)
    (tmp / 'module.modulemap').write_text('module zlib [system] { header "/usr/include/zlib.h" export * link "z" }' if Path('/usr/include/zlib.h').exists() else '')
    (tmp / 'stubs.swift').write_text('func wine_process_is_running() -> Int32 { 0 }\nfunc wineserver_is_running() -> Int32 { 0 }\n'
                                     'func madeira_seed_prefix_if_needed(_ path: UnsafePointer<CChar>?) {}\n'
                                     'enum SteamLog { static func event(_ m: String) {} }\n')
    (tmp / 'main.swift').write_text(main)
    # the test file is the same 3 MB pattern as main.swift writes
    data = bytearray()
    for i in range(3 << 20):
        data.append(((i * 31) + (i >> 8)) & 0xff)
    sha = hashlib.sha256(bytes(data)).hexdigest()
    exe = tmp / 'check'
    b = subprocess.run([SWIFTC, '-I', str(tmp), str(app / 'SteamRuntime.swift'), str(tmp / 'stubs.swift'), str(tmp / 'main.swift'), '-o', str(exe)],
                       capture_output=True, text=True)
    check("SteamRuntime.swift compiles on the host with the client check", b.returncode == 0, b.stderr[-2500:])
    if b.returncode == 0:
        r = subprocess.run([str(exe), sha], capture_output=True, text=True)
        check("clientStatus, the supported set, the rename aside and the steamapps move", r.returncode == 0 and "PASS" in r.stdout, r.stdout + r.stderr)

dock = (app / 'MadeiraDock.swift').read_text()
validate = dock[dock.index('static func validate(_ game: DockGame'):]
validate = validate[:validate.index('    // MARK: Sign-in transfer')]
check("validate asks the client status before the host starts and refuses an unsupported build with the words and a [dock-client] line",
      'switch clientStatus(drive)' in validate and 'case .unsupported(let sha):' in validate and '[dock-client] unsupported sha256=' in validate
      and 'SteamRuntimeFiles.unsupportedClientWords(sha: sha)' in validate)
check("the host's code 30 words name Replace client components", 'choose Replace client components, then try again.' in dock)
live = (app / 'BCDDockLive.swift').read_text()
view = (app / 'BCDDock.swift').read_text()
check("Settings > BCD Dock offers Replace client components for an unsupported build and the model runs the installer in replace mode",
      'backend.replaceComponents()' in view and 'parts.unsupportedBuild' in view and 'prepareClient(replacing: true)' in live
      and 'prepare(prefix: MadeiraDock.prefix, replacing: replacing)' in (app / 'MadeiraDockModel.swift').read_text())
prep = runtime[runtime.index('func prepare(prefix: URL, replacing: Bool = false'):]
check("prepare(replacing:) renames the old folder only after the download passed its pinned hashes and before the install",
      prep.index('SteamRuntimeFiles.clientSHA256') < prep.index('moveClientAside(root: root, sha: sha)') < prep.index('for (url, bytes) in pending'))
print("PASS: Dock client status" if ok else "FAILED: Dock client status")
sys.exit(0 if ok else 1)
