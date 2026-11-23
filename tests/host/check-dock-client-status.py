#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Night round 2026-11-11 (A1 S7, A2 S7; SQDE4K, T12JFC, AT5CSS): which Valve client build is in the prefix (SteamRuntimeFiles.clientStatus), the set
of builds the Dock host supports, and the rename-aside of an unsupported client ("Replace client components"). SteamRuntime.swift compiled on the
host with a stub for the log and the Wine session checks; nothing is downloaded and no Wine runs.

Checks:
  - the supported set is the three builds BCD installs (202601, 202609, 202611), and equals the table of the Dock host's client_layout.c
    (madeira-dock of this tree or ~/madeira-bcd/madeira-dock; skipped without it); the host pins every method of 202601 and 202611, the four
    slots that differ between builds are the ones inspected, and Settings lists the same three builds;
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


# Round 660 (M139, owner 2026-11-15): the supported set is the client DLLs of the builds BCD can install (January 2026, the default, and
# September 2026, chosen in Settings > BCD Dock). 2026-11-21: November 2026 (Valve's current client) is the third.
builds = re.findall(r'static let (january|september|november) = Build\(id: (\d+), title: "([^"]+)"', runtime)
clients = re.findall(r'\], clientSHA256: "([0-9a-f]{64})"\)', runtime)
check("three client builds, 202601, 202609 and 202611, each with its client DLL",
      builds == [('january', '202601', 'January 2026'), ('september', '202609', 'September 2026'), ('november', '202611', 'November 2026')]
      and len(clients) == 3 and len(set(clients)) == 3, (builds, clients))
check("the supported set is every build's client DLL", 'static let supportedClientSHA256: Set<String> = Set(builds.map(\\.clientSHA256))' in runtime
      and 'static let builds = [january, september, november]' in runtime)
layouts = next((d / 'src/client_layout.c' for d in (root / 'madeira-dock', Path.home() / 'madeira-bcd/madeira-dock') if (d / 'src/client_layout.c').is_file()), None)
if layouts and len(clients) == 3 and '"' + clients[2] + '"' not in layouts.read_text():
    # the build tree beside this one may still hold the pin before 202611: its table cannot be compared with this tree's three builds
    print("skip: the Dock source at hand (%s) is an earlier pin without 202611" % layouts.parent.parent)
    layouts = None
if layouts and len(clients) == 3:
    table = re.findall(r'"([0-9a-f]{64})",\s*(\d+),([^}]+)\}', layouts.read_text())   # every entry: its DLL, its adapter number, its numbers
    check("the supported set equals the Dock host's client_layout.c table", {t[0] for t in table} == set(clients) and len(table) == 3,
          [t[:2] for t in table])
    # each build's id in Settings is the host's adapter number for the same DLL (the report's session-client-adapter)
    check("each build's id is the host's adapter number for the same client DLL",
          {t[0]: t[1] for t in table} == {c: b[1] for c, b in zip(clients, builds)}, [t[:2] for t in table])
    rows = {t[1]: [int(x, 0) for x in re.findall(r'\b(?:0x[0-9a-f]+|\d+)\b', t[2])] for t in table}
    check("every row is 34 method addresses and the four slots that differ between builds", all(len(r) == 38 for r in rows.values()),
          {k: len(v) for k, v in rows.items()})
    if all(len(r) == 38 for r in rows.values()):
        # 202601 and 202611 pin every method (Steam Friends included); 202609 keeps its 18 and has no Friends, as before
        check("the host pins all 34 methods of 202601 and 202611, and the first 18 of 202609 (no Steam Friends there, as before)",
              all(rows['202601'][:34]) and all(rows['202611'][:34]) and all(rows['202609'][:18]) and not any(rows['202609'][18:34]),
              rows.get('202609'))
        check("the earlier builds keep slots 181, 182, 214 and 215; the November 2026 client has the four methods one slot later",
              rows['202601'][34:] == [181, 182, 214, 215] and rows['202609'][34:] == [181, 182, 214, 215]
              and rows['202611'][34:] == [182, 183, 215, 216], {k: v[34:] for k, v in rows.items()})
        check("no method address of one build is another build's (a row cannot have been copied)",
              not (set(rows['202601'][:34]) & set(rows['202611'][:34])) and not (set(rows['202609'][:18]) & set(rows['202611'][:34])))
    session = (layouts.parent / 'session.c').read_text()
    check("the host's session asks through the build's own slots", 'v[layout->slot_subscribed]' in session and 'v[layout->slot_can_offline]' in session
          and not re.search(r'v\[(181|182|214|215)\]', session))
elif not layouts:
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
assert(SteamRuntimeFiles.supportedClientSHA256.count == 3 && SteamRuntimeFiles.supportedClientSHA256.contains(SteamRuntimeFiles.clientSHA256))
// Round 660 (M139): January 2026 unless Settings chose September 2026; 2026-11-21: or November 2026. The builds' DLLs map back to them.
UserDefaults.standard.removeObject(forKey: SteamRuntimeFiles.buildKey)
assert(SteamRuntimeFiles.selected == SteamRuntimeFiles.january && SteamRuntimeFiles.packages.count == 3
       && SteamRuntimeFiles.clientSHA256 == SteamRuntimeFiles.january.clientSHA256)
UserDefaults.standard.set(202609, forKey: SteamRuntimeFiles.buildKey)
assert(SteamRuntimeFiles.selected == SteamRuntimeFiles.september && SteamRuntimeFiles.packages.count == 2
       && SteamRuntimeFiles.clientSHA256 == SteamRuntimeFiles.september.clientSHA256)
UserDefaults.standard.set(202611, forKey: SteamRuntimeFiles.buildKey)
assert(SteamRuntimeFiles.selected == SteamRuntimeFiles.november && SteamRuntimeFiles.packages.count == 2
       && SteamRuntimeFiles.clientSHA256 == SteamRuntimeFiles.november.clientSHA256
       && SteamRuntimeFiles.clientSHA256 == "669fa08e259277fbf5a1dfe15ce88afe734e89151194304b44d92f418dcc985b")
assert(SteamRuntimeFiles.november.packages.map(\.bytes) == [64_658_652, 2_696_423]
       && SteamRuntimeFiles.november.packages.allSatisfy { $0.bytes <= 64 * 1024 * 1024 }, "each package fits the ZIP reader's 64 MiB")
for other in [1, 202610, 202612, 202501, -202611, 0] {
    UserDefaults.standard.set(other, forKey: SteamRuntimeFiles.buildKey)
    assert(SteamRuntimeFiles.selected == SteamRuntimeFiles.january, "any other value (\(other)) is January")
}
UserDefaults.standard.removeObject(forKey: SteamRuntimeFiles.buildKey)
assert(SteamRuntimeFiles.builds.map(\.id) == [202601, 202609, 202611] && SteamRuntimeFiles.builds.map(\.title) == ["January 2026", "September 2026", "November 2026"])
assert(SteamRuntimeFiles.build(sha: SteamRuntimeFiles.january.clientSHA256) == SteamRuntimeFiles.january
       && SteamRuntimeFiles.build(sha: SteamRuntimeFiles.september.clientSHA256) == SteamRuntimeFiles.september
       && SteamRuntimeFiles.build(sha: SteamRuntimeFiles.november.clientSHA256) == SteamRuntimeFiles.november && SteamRuntimeFiles.build(sha: sha) == nil)
// the installed build is read from the client DLL's own hash: a folder holding a file with November's hash would be November (no such
// file can be made here; the three hashes are distinct and none is this test file's)
assert(Set(SteamRuntimeFiles.builds.map(\.clientSHA256)).count == 3 && !SteamRuntimeFiles.supportedClientSHA256.contains(sha))
assert(SteamRuntimeFiles.installedBuild(drive: drive) == nil, "an unsupported client is no build")
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
// Round 660 (M139): a supported build that makes way for the other one is "Steam.<id>-<sha12>"; the player's folders move along, never over
// one that is there.
let third = try SteamRuntimeFiles.moveClientAside(root: again, sha: sha, label: "202601")
assert(third.lastPathComponent == "Steam.202601-" + sha.prefix(12) && !fm.fileExists(atPath: again.path))
// 2026-11-21: the same rename for a change away from the November 2026 client (its folder is named by its build, like the others)
try fm.createDirectory(at: again, withIntermediateDirectories: true)
let fourth = try SteamRuntimeFiles.moveClientAside(root: again, sha: SteamRuntimeFiles.november.clientSHA256, label: String(SteamRuntimeFiles.november.id))
assert(fourth.lastPathComponent == "Steam.202611-669fa08e2592" && !fm.fileExists(atPath: again.path) && fm.fileExists(atPath: third.path))
for folder in ["userdata/123/730", "config", "depotcache"] {
    try fm.createDirectory(at: third.appendingPathComponent(folder), withIntermediateDirectories: true)
}
try Data("cloud".utf8).write(to: third.appendingPathComponent("userdata/123/730/remote.vdf"))
try fm.createDirectory(at: again.appendingPathComponent("config"), withIntermediateDirectories: true)   // the new client made its own config already
assert(SteamRuntimeFiles.playerFolders == ["steamapps", "userdata", "config", "depotcache"])
assert(SteamRuntimeFiles.moveFolder("steamapps", from: third, into: again), "steamapps moves")
assert(SteamRuntimeFiles.moveFolder("userdata", from: third, into: again)
       && fm.fileExists(atPath: again.appendingPathComponent("userdata/123/730/remote.vdf").path), "userdata moves with its files")
assert(!SteamRuntimeFiles.moveFolder("config", from: third, into: again) && fm.fileExists(atPath: third.appendingPathComponent("config").path),
       "config is never moved over the new client's own")
assert(SteamRuntimeFiles.moveFolder("depotcache", from: third, into: again))
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
      prep.index('== chosen.clientSHA256') < prep.index('moveClientAside(root: root, sha: sha, label:') < prep.index('for (url, bytes) in pending'))
# Round 660 (M139): replace mode also changes between the two supported builds, only to the one Settings chose and never to the same one;
# a fresh install takes the chosen build; the change between supported builds moves the player's folders, the unsupported case steamapps only.
check("replace mode takes an unsupported client, or a supported one of the other build, never the chosen build itself",
      'case .unsupported(let sha):\n                unsupportedSHA = sha' in prep
      and 'guard let installed = SteamRuntimeFiles.installedBuild(drive: drive), installed != chosen else {' in prep
      and 'let chosen = SteamRuntimeFiles.selected' in prep and 'for (index, package) in chosen.packages.enumerated()' in prep)
check("between supported builds the old folder is named by its build and the player's folders follow; an unsupported one keeps the old name",
      'label: switchingFrom.map { String($0.id) } ?? "unsupported"' in prep and 'if let renamedClient, switchingFrom == nil {' in prep
      and 'for folder in SteamRuntimeFiles.playerFolders {' in prep)
check("Settings > BCD Dock: the Steam client choice and the Switch button run replace mode",
      'func setClientBuild(_ id: Int)' in live and 'SteamRuntimeFiles.buildKey' in live and view.count('clientBuildChoice(parts)') == 2
      and 'id: Self.prefix + "switch") { backend.replaceComponents() }' in view)
# 2026-11-21: three builds. Settings lists exactly the builds SteamRuntime.swift installs, by the same ids and titles, oldest first, and a
# choice BCD does not install is January 2026 (the default stays January 2026).
listed = re.findall(r'^\s*\((\d{6}), "([^"]+)", "([^"]+)",\s*$', view[view.index('static let builds: ['):view.index('static func build(_ id: Int)')], re.M)
check("Settings lists the builds SteamRuntime.swift installs, by id and title, oldest first",
      [(i, t) for i, t, _ in listed] == [(b[1], b[2]) for b in builds] and len(listed) == 3, listed)
check("the words on the choice are short enough for three in a row and name the month", [s for _, _, s in listed] == ["Jan 2026", "Sep 2026", "Nov 2026"], listed)
check("the choice is one of three (Segments), dimmed while a download or switch runs, and changes nothing by itself",
      'BCDSectionKit.Segments(options: BCDDockComponents.builds.map { (value: $0.id, title: $0.short) }, selected: chosen.id,' in view
      and '{ if !parts.working { backend.setClientBuild($0) } }' in view and '.disabled(parts.working)' in view)
check("the live choice saves only a build BCD installs (anything else is January 2026) and logs it",
      'let build = SteamRuntimeFiles.builds.first { $0.id == id } ?? SteamRuntimeFiles.january' in live
      and 'UserDefaults.standard.set(build.id, forKey: SteamRuntimeFiles.buildKey)' in live and '[dock-client] chosen build' in live)
check("January 2026 stays the default: the state starts on it and nothing chooses another build by itself",
      'var chosenBuild = 202601' in view and 'return builds.first { $0.id == id } ?? january' in runtime
      and len(re.findall(r'forKey: SteamRuntimeFiles\.buildKey\)', ''.join((app / f).read_text() for f in
                         ('BCDDockLive.swift', 'MadeiraDockModel.swift', 'MadeiraDock.swift', 'ContentView.swift')))) == 1)
preview = (root / 'app/BCDPreview/BCDPreviewDock.swift').read_text()
check("the preview app's backend chooses by build id too", 'func setClientBuild(_ id: Int)' in preview and 'setNewerClient' not in preview + live + view)
print("PASS: Dock client status" if ok else "FAILED: Dock client status")
sys.exit(0 if ok else 1)
