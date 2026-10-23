#!/usr/bin/env python3
"""Exercise the production SSD mount with real folders/bookmarks on macOS.

Only picker permission acquisition is injected; bookmark persistence, symlink
ownership, containment, failed reconnects and file preservation use Foundation.
Run: python3 tests/host/check-external-storage.py
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "app/Madeira/StorageModel.swift").read_text(encoding="utf-8")
manager = source[source.index("final class ExternalGameStorage {"):source.index("struct StorageItem:")]
checks = r'''
enum GameLibrary { static var driveC: URL? }
let fm = FileManager.default
let temp = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try fm.createDirectory(at: temp, withIntermediateDirectories: true)
defer { try? fm.removeItem(at: temp) }
let suite = "bcd-external-test-" + UUID().uuidString
let defaults = UserDefaults(suiteName: suite)!
defer { defaults.removePersistentDomain(forName: suite) }
let drive = temp.appendingPathComponent("drive_c")
let ssd = temp.appendingPathComponent("SSD/Games")
let other = temp.appendingPathComponent("Other")
for dir in [drive, ssd, other] { try fm.createDirectory(at: dir, withIntermediateDirectories: true) }
GameLibrary.driveC = drive
let link = drive.appendingPathComponent("External")
var opens = 0, closes = 0
let storage = ExternalGameStorage(defaults: defaults,
    beginAccess: { _ in opens += 1; return true }, endAccess: { _ in closes += 1 })
func rejected(_ action: () throws -> Void) {
    do { try action(); fatalError("Unexpected successful mount") } catch {}
}
let userFile = link.appendingPathComponent("keep.txt")
try fm.createDirectory(at: link, withIntermediateDirectories: true)
try Data("keep".utf8).write(to: userFile)
rejected { try storage.connect(ssd) }
let preservedText = try String(contentsOf: userFile, encoding: .utf8)
assert(preservedText == "keep")
assert(opens == closes)
try fm.removeItem(at: link)

try storage.connect(ssd)
assert(opens == closes + 1)
let exe = ssd.appendingPathComponent("Game/game.exe")
try fm.createDirectory(at: exe.deletingLastPathComponent(), withIntermediateDirectories: true)
try Data([0x4d, 0x5a]).write(to: exe)
assert(storage.relativePath(for: exe, drive: drive) == "External/Game/game.exe")
assert(storage.relativePath(for: link.appendingPathComponent("Game/game.exe"), drive: drive) == "External/Game/game.exe")
assert(storage.relativePath(for: drive.appendingPathComponent("Local/game.exe"), drive: drive) == "Local/game.exe")
assert(storage.relativePath(for: other, drive: drive) == nil)
let escape = ssd.appendingPathComponent("escape")
try fm.createSymbolicLink(at: escape, withDestinationURL: other)
assert(storage.relativePath(for: escape.appendingPathComponent("game.exe"), drive: drive) == nil)
try Data([0x4d, 0x5a]).write(to: other.appendingPathComponent("game.exe"))
assert(storage.relativePath(for: escape.appendingPathComponent("game.exe"), drive: drive) == nil)
assert(storage.relativePath(for: ssd.appendingPathComponent("../private.exe"), drive: drive) == nil)

rejected { try storage.connect(drive) }
rejected { try storage.connect(temp) }
rejected { try storage.connect(ssd.appendingPathComponent("missing")) }
let preservedExe = try Data(contentsOf: link.appendingPathComponent("Game/game.exe"))
assert(preservedExe == Data([0x4d, 0x5a]))
assert(opens == closes + 1)
try storage.connect(ssd)
assert(opens == closes + 1)
let restored = ExternalGameStorage(defaults: defaults, beginAccess: { _ in true }, endAccess: { _ in })
restored.restore()
assert(restored.relativePath(for: exe, drive: drive) == "External/Game/game.exe")
assert(restored.freeBytes != nil)

// Replacing a link BCD does not own must preserve it and its destination.
try fm.removeItem(at: link)
try fm.createSymbolicLink(at: link, withDestinationURL: other)
rejected { try storage.connect(ssd) }
let preservedDestination = try fm.destinationOfSymbolicLink(atPath: link.path)
assert(preservedDestination == other.path)
assert(storage.relativePath(for: exe, drive: drive) == nil)
try fm.removeItem(at: link)
try fm.createDirectory(at: link, withIntermediateDirectories: true)
assert(storage.relativePath(for: link.appendingPathComponent("Game/game.exe"), drive: drive) == nil)
rejected { try storage.connect(ssd) }
print("External storage: preservation, scope balance, containment, reconnect and bookmark restore passed")
'''

if sys.platform != "darwin" or not shutil.which("swift"):
    print("SKIP: external storage checks need macOS Foundation and Swift (iOS bookmarks).")
    sys.exit(0)
with tempfile.TemporaryDirectory(prefix="bcd-external-") as directory:
    test = Path(directory) / "main.swift"
    test.write_text("import Foundation\n" + manager + checks, encoding="utf-8")
    subprocess.run(["swift", str(test)], check=True)
