#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 125hz
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
# Portions copyright 2026 meshoklv: code taken from https://github.com/willfaust/Madeira (pull request #389)
# under GPL-3.0-or-later and modified for BCD in October 2026. See THIRD-PARTY-NOTICES.md.
"""Madeira Dock's client-component setup (SteamRuntime.swift): the production ZIP
reader and Steam discovery-registry writer, compiled on the host with synthetic
archives. Never downloads anything and never runs Steam or Wine.

Also checks the pins: one HTTPS Valve host, well-formed sizes and SHA-256 sums,
and a client DLL hash the pinned Dock host supports (madeira-dock).
Pass real package files as arguments to also unpack them.
"""
from pathlib import Path
import hashlib, io, os, re, shutil, struct, subprocess, sys, tempfile, zipfile
root = Path(__file__).resolve().parents[2]
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
source = root / 'app/Madeira/SteamRuntime.swift'
text = source.read_text()
assert 'static let origin = "https://client-update.akamai.steamstatic.com/"' in text
pins = re.findall(r'Package\(file: "([^"]+)", bytes: ([0-9_]+),\s*sha256: "([0-9a-f]+)"\)', text)
assert len(pins) == 3 and all(len(s) == 64 and int(b.replace('_', '')) > 0 and '/' not in f for f, b, s in pins), pins
client = re.search(r'static let clientSHA256 = "([0-9a-f]{64})"', text).group(1)
layouts = root / 'madeira-dock/src/client_layout.c'
if layouts.exists():
    assert f'"{client}"' in layouts.read_text(), 'the pinned client DLL is not a build the Dock host supports'
    print('PASS: pinned client DLL is a build the pinned Dock host supports')
else:
    print('SKIP: madeira-dock not checked out; client pin cross-check not run')
assert 'url?.host == "client-update.akamai.steamstatic.com"' in text and 'url?.scheme == "https"' in text
print('PASS: three pinned packages from one HTTPS Valve host; redirects stay on it')
with tempfile.TemporaryDirectory(prefix='madeira-runtime-') as tmp:
    tmp = Path(tmp)
    # Linux has no zlib module; macOS's SDK has its own (Modified by BCD, 2026-10-25: the shim only where /usr/include/zlib.h exists).
    (tmp/'module.modulemap').write_text('module zlib [system] { header "/usr/include/zlib.h" export * link "z" }'
                                        if Path('/usr/include/zlib.h').exists() else '')
    # The two Wine session checks and the prefix seeder SteamRuntime.swift calls are the app's, not this host's (Modified by BCD, 2026-10-25).
    (tmp/'stubs.swift').write_text('func wine_process_is_running() -> Int32 { 0 }\nfunc wineserver_is_running() -> Int32 { 0 }\n'
                                   'func madeira_seed_prefix_if_needed(_ path: UnsafePointer<CChar>?) {}\n'
                                   'enum SteamLog { static func event(_ m: String) {} }\n')   # night round 2026-11-11: the client replacement logs through it
    main = r'''
import Foundation
let fm = FileManager.default
let mode = CommandLine.arguments[1]
if mode == "registry" {
    let original = "WINE REGISTRY Version 2\n\n[Software\\\\Keep]\n\"Unrelated\"=\"preserved\"\n"
    for machine in [false, true] {
        let result = try SteamRuntimeFiles.registry(original, machine: machine)
        assert(result.contains("\"Unrelated\"=\"preserved\""))
        let repeated = try SteamRuntimeFiles.registry(result, machine: machine)
        assert(repeated == result)
        if machine { assert(result.contains("Wow6432Node")) }
        else { assert(result.contains("ActiveProcess") && result.contains("SteamExe")) }
    }
    let conflict = "WINE REGISTRY Version 2\n\n[Software\\\\Valve\\\\Steam]\n\"SteamPath\"=\"another location\"\n"
    do { _ = try SteamRuntimeFiles.registry(conflict, machine: false); fatalError("conflict accepted") }
    catch SteamRuntimeFiles.Failure.conflict { }
    // A Steam emulator's leftover (SteamPath and the client DLL in folders without steam.exe) is replaced.
    let leftover = "WINE REGISTRY Version 2\n\n[Software\\\\Valve\\\\Steam]\n\"SteamPath\"=\"C:\\\\Games\\\\Emu\\\\Steam\"\n\n" +
        "[Software\\\\Valve\\\\Steam\\\\ActiveProcess]\n\"pid\"=dword:00000020\n\"SteamClientDll64\"=\"C:\\\\Program Files\\\\Steam\\\\steamclient64.dll\"\n"
    var asked: [String] = []
    let replaced = try SteamRuntimeFiles.registry(leftover, machine: false, stale: { asked.append($0); return true })
    assert(asked.sorted() == ["C:\\Games\\Emu\\Steam", "C:\\Program Files\\Steam\\steamclient64.dll"], "\(asked)")
    assert(!replaced.contains("Emu") && !replaced.contains("Program Files\\\\Steam\\\\") && replaced.contains("\"pid\"=dword:00000020"))
    let again = try SteamRuntimeFiles.registry(replaced, machine: false)
    assert(again == replaced)
    // A value naming a client that is there stays a conflict.
    do { _ = try SteamRuntimeFiles.registry(leftover, machine: false, stale: { _ in false }); fatalError("live client replaced") }
    catch SteamRuntimeFiles.Failure.conflict { assert(SteamRuntimeFiles.lastConflict?.hasSuffix("SteamPath") == true) }
    do { _ = try SteamRuntimeFiles.registry("invalid", machine: true); fatalError("invalid hive accepted") }
    catch SteamRuntimeFiles.Failure.prefixMissing { }
    for path in ["../escape", "/absolute", "c:/other", "a\\b", "a//b", "a/./b", "a/../b", "a/space "] {
        assert(!SteamRuntimeFiles.validPath(path))
    }
    let folder = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try fm.createDirectory(at: folder.appendingPathComponent("Bin"), withIntermediateDirectories: true)
    defer { try? fm.removeItem(at: folder) }
    _ = try SteamRuntimeFiles.destination("Bin/new.dll", under: folder)
    do { _ = try SteamRuntimeFiles.destination("bin/new.dll", under: folder); fatalError("case collision accepted") }
    catch SteamRuntimeFiles.Failure.conflict { }
    try fm.createSymbolicLink(at: folder.appendingPathComponent("link"), withDestinationURL: folder.appendingPathComponent("Bin"))
    do { _ = try SteamRuntimeFiles.destination("link/new.dll", under: folder); fatalError("symlink accepted") }
    catch SteamRuntimeFiles.Failure.conflict { }
    print("PASS: discovery keys, idempotence, unrelated settings, conflicts, stale leftovers and path rules")
    // Night round 2026-11-11 (G10 S1; GNWWRT, RAC3KD, GWX5H2, DP9TVR): repairRegistry puts the Steam discovery sections back before a Dock start.
    do {
        let prefix = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? fm.removeItem(at: prefix) }
        let clientFolder = prefix.appendingPathComponent("drive_c/" + SteamRuntimeFiles.relativeRoot)
        let system = prefix.appendingPathComponent("system.reg"), user = prefix.appendingPathComponent("user.reg")
        try fm.createDirectory(at: clientFolder, withIntermediateDirectories: true)
        var lines: [String] = []
        func repair() -> [String] { SteamRuntimeFiles.repairRegistry(prefix: prefix, log: { lines.append($0) }) }
        func text(_ url: URL) -> String { (try? String(contentsOf: url, encoding: .utf8)) ?? "" }
        func bytes(_ url: URL) -> Data { (try? Data(contentsOf: url)) ?? Data() }
        // No client files: nothing is read or written (a prefix without Valve's client is not a Dock prefix).
        try Data("WINE REGISTRY Version 2\n\n[Software\\\\Keep] 1\n\"Unrelated\"=\"preserved\"\n".utf8).write(to: system)
        try Data("WINE REGISTRY Version 2\n".utf8).write(to: user)
        assert(repair().isEmpty && lines.isEmpty)
        assert(!fm.fileExists(atPath: system.appendingPathExtension("dock-setup-bak").path))
        try Data("client".utf8).write(to: clientFolder.appendingPathComponent("steamclient64.dll"))
        // Both hives lack the sections: put back, a backup of the originals is kept, unrelated entries stay.
        let before = bytes(system)
        var state = repair()
        assert(state.count == 2 && state.allSatisfy { $0.contains("=put-back(") }, "\(state)")
        assert(lines.last?.hasPrefix("[dock-registry] system.reg=put-back(sections 2, values 2) user.reg=put-back(sections 2, values 4)") == true, "\(lines)")
        let machine = text(system), userText = text(user)
        assert(machine.contains("[Software\\\\Wow6432Node\\\\Valve\\\\Steam]") && machine.contains("[Software\\\\Valve\\\\Steam]"))
        assert(machine.contains("\"Unrelated\"=\"preserved\"") && userText.contains("[Software\\\\Valve\\\\Steam\\\\ActiveProcess]"))
        assert(bytes(system.appendingPathExtension("dock-setup-bak")) == before)
        // A second run changes nothing: the files keep their bytes and the backup is not renewed.
        let written = bytes(system)
        state = repair()
        assert(state == ["system.reg=ok", "user.reg=ok"], "\(state)")
        assert(bytes(system) == written && bytes(system.appendingPathExtension("dock-setup-bak")) == before)
        // The hive's header with Wine's timestamp (the form Wine writes) counts as there; a missing VALUE of a present section is added.
        let stamped = "WINE REGISTRY Version 2\n\n[Software\\\\Wow6432Node\\\\Valve\\\\Steam] 1700000000\n#time=1d9\n\"Other\"=\"kept\"\n\n" +
            "[Software\\\\Valve\\\\Steam] 1700000000\n\"InstallPath\"=\"C:\\\\Program Files (x86)\\\\Steam\"\n"
        try Data(stamped.utf8).write(to: system)
        state = repair()
        let patched = text(system)
        assert(state[0] == "system.reg=put-back(sections 0, values 1)" && patched.contains("\"Other\"=\"kept\"") && patched.contains("#time=1d9"), "\(state)")
        assert(patched.components(separatedBy: "[Software\\\\Wow6432Node\\\\Valve\\\\Steam]").count == 2, "no second section header")
        // A value that differs is left alone: the hive keeps its bytes, the log names the value.
        let differing = "WINE REGISTRY Version 2\n\n[Software\\\\Valve\\\\Steam]\n\"InstallPath\"=\"D:\\\\Other\"\n"
        try Data(differing.utf8).write(to: system)
        state = repair()
        assert(state[0] == "system.reg=left-alone(Software\\\\Valve\\\\Steam\\InstallPath)", "\(state)")
        assert(text(system) == differing)
        // A file that is not a Wine registry is left as it is.
        try Data("garbage".utf8).write(to: system)
        state = repair()
        assert(state[0] == "system.reg=not-a-registry-file" && text(system) == "garbage", "\(state)")
        print("PASS: repairRegistry puts missing Steam discovery sections back, writes nothing when they are there, leaves a differing value alone")
    }
} else {
    let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2]))
    do {
        var files: [String: Data] = [:]
        try SteamRuntimeFiles.unpack(data) { files[$0] = $1 }
        if mode == "reject" { fatalError("malformed archive accepted") }
        let output = URL(fileURLWithPath: CommandLine.arguments[3])
        for (name, data) in files {
            let url = output.appendingPathComponent(name)
            try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: url)
        }
        print("PASS: extracted \(files.count) files")
    } catch {
        if mode != "reject" { throw error }
        print("PASS: rejected malformed archive")
    }
}
'''
    (tmp/'main.swift').write_text(main)
    exe = tmp/'check'
    subprocess.run([SWIFTC, '-I', str(tmp), str(source), str(tmp/'stubs.swift'), str(tmp/'main.swift'), '-o', str(exe)], check=True)
    subprocess.run([str(exe), 'registry'], check=True)
    def archive(items, compression=zipfile.ZIP_DEFLATED):
        out = io.BytesIO()
        with zipfile.ZipFile(out,'w',compression=compression) as z:
            for name,data in items: z.writestr(name,data)
        return out.getvalue()
    def run(data, reject=False):
        p=tmp/'fixture.zip'; p.write_bytes(data)
        output=tmp/'out'
        subprocess.run([str(exe),'reject' if reject else 'extract',str(p),str(output)],check=True,stdout=subprocess.DEVNULL)
        if not reject:
            with zipfile.ZipFile(p) as z:
                for e in z.infolist():
                    name=e.filename.replace('\\','/')
                    if not name.endswith('/'): assert (output/name).read_bytes()==z.read(e)
    good=archive([('bin/',b''),('bin/library.dll',b'abcdefgh'*4096),('empty',b'')],zipfile.ZIP_STORED)
    run(good)
    good=archive([('library.dll',b'abcdefgh'*4096),('empty',b'')])
    run(good)
    run(archive([('bin\\module.dll',b'windows path')]))
    for path in ['../outside','/outside','c:/outside','a\\..\\b','a/../b']:
        run(archive([(path,b'x')]),True)
    run(archive([('Name',b'x'),('name',b'y')]),True)
    entry=zipfile.ZipInfo('link'); entry.create_system=3; entry.external_attr=(0o120777 << 16)
    run(archive([(entry,b'../outside')]),True)
    run(good[:-10],True)
    bad=bytearray(good); central=bad.index(b'PK\x01\x02'); bad[central+16]^=1; run(bad,True)
    bad=bytearray(good); struct.pack_into('<I',bad,central+24,512*1024*1024); run(bad,True)
    bad=bytearray(good); bad[6]|=1; bad[central+8]|=1; run(bad,True)
    bad=bytearray(good); struct.pack_into('<I',bad,central+42,0xffffffff); run(bad,True)
    print('PASS: stored/deflate/empty content, traversal, duplicates, symlinks, CRC, sizes, flags, offsets and truncation')
    for path in sys.argv[1:]:
        p=Path(path); data=p.read_bytes(); run(data)
        print('PASS: real package SHA-256',hashlib.sha256(data).hexdigest())

# Modified by BCD (2026-10-25): the model that runs the setup logs what stopped it (the error and the registry value or file).
model = (root / 'app/Madeira/MadeiraDockModel.swift').read_text()
assert 'SteamLog.event("[dock-setup] client components failed: \\(error.localizedDescription)"' in model and \
    '(SteamRuntimeFiles.lastConflict.map { " (at \\($0))" } ?? "")' in model
print('PASS: the failure line names the error and the value or file that stopped the setup')
# Night round 2026-11-11 (G10 S1): the Dock start puts the Steam sections back after the installs plan, before the service preparation and the session.
content = (root / 'app/Madeira/ContentView.swift').read_text()
repair = content.index('SteamRuntimeFiles.repairRegistry(prefix: MadeiraDock.prefix')
assert content.index('DockInstallers.prepare(game, drive: MadeiraDock.drive') < repair < content.index('DockCEGService.prepare(game, drive: MadeiraDock.drive') < content.index('runWineFullSequence(profile: profile)', repair)
assert 'if !DockInstallers.installsOnly, SteamSignIn.flag("MADEIRA_DOCK_REGISTRY_REPAIR", default: true)' in content
print('PASS: startDock repairs the Steam registry sections after the installs plan and before the CEG service preparation')
