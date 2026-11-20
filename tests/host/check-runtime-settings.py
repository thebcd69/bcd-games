#!/usr/bin/env python3
"""Front end Settings: display rate, swap tier and the sync engine.

1. Swift: compiles the production MadeiraConfig (app/Madeira/MadeiraConfig.swift)
   with HOME pointed at a scratch directory and checks MadeiraConfig.set():
   it keeps comments and other keys, replaces earlier lines for the key,
   removes a key for nil, creates bcd.cfg with only that key (an old
   madeira.cfg or madeira-<key>.txt is never read, migrated or merged), and
   MadeiraConfig.flag() reads env.NAME lines. With the
   production SyncEngine (Library.swift): no key is Fastsync (the default);
   Fastsync removes both keys; Madsync writes inproc-sync = 1 and removes
   env.MADEIRA_FASTSYNC; Wine standard sync writes inproc-sync = 0 and removes
   env.MADEIRA_FASTSYNC; a hand-edited bcd.cfg reads back as the engine
   Wine will run (madsync wins over any MADEIRA_FASTSYNC value).
2. Source checks on Library.swift, ProMotionIntent.swift and BCD's Settings and game page: the
   defaults are swap tier off, Fastsync and display-rate hold off, the
   Sync engine picker writes through SyncEngine.apply, game details offer the
   Fastsync-only switches greyed out unless Fastsync is chosen, launches export
   them only with Fastsync, and MADEIRA_RUNTIME_SETTINGS=0 hides both sections.

Run from anywhere; needs `swift` on PATH.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
config = (root / 'app/Madeira/MadeiraConfig.swift').read_text()
lib = (root / 'app/Madeira/Library.swift').read_text()
fps = (root / 'app/Madeira/ProMotionIntent.swift').read_text()
failures = []


def block(text, header):
    p = text.index(header)
    a = text.index('{', p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == '{') - (text[b] == '}')
        b += 1
    return text[p:b]


def check(cond, what):
    print(('PASS: ' if cond else 'FAIL: ') + what)
    if not cond:
        failures.append(what)


swift = config + '\n' + block(lib,'enum SyncEngine: String, CaseIterable, Identifiable') + r'''
var failed = 0
func expect(_ cond: Bool, _ what: String) { print((cond ? "PASS: " : "FAIL: ") + what); if !cond { failed += 1 } }
let docs = MadeiraConfig.documents!
try? FileManager.default.createDirectory(at: docs, withIntermediateDirectories: true)
let cfg = docs.appendingPathComponent("bcd.cfg")
expect(MadeiraConfig.fileName == "bcd.cfg" && MadeiraConfig.url == cfg, "the config file is Documents/bcd.cfg")

// No bcd.cfg; the old names are there. Nothing reads them, nothing moves them.
let oldCfg = docs.appendingPathComponent("madeira.cfg"), oldOne = docs.appendingPathComponent("madeira-vram-mb.txt")
try! "pool = 512\nwx = 0\n".write(to: oldCfg, atomically: true, encoding: .utf8)
try! "2048\n".write(to: oldOne, atomically: true, encoding: .utf8)
expect(!MadeiraConfig.present && MadeiraConfig.all().isEmpty, "no bcd.cfg at the start: nothing is set")
expect(MadeiraConfig.get("pool") == nil && MadeiraConfig.get("vram-mb") == nil && !MadeiraConfig.bool("wx", default: false),
       "madeira.cfg and madeira-<key>.txt are not read")
expect(MadeiraConfig.set("swap-mb", "1024"), "set() creates bcd.cfg")
expect((try? String(contentsOf: cfg, encoding: .utf8)) == "swap-mb = 1024\n", "bcd.cfg holds only the key written (nothing migrated)")
expect((try? String(contentsOf: oldCfg, encoding: .utf8)) == "pool = 512\nwx = 0\n"
       && FileManager.default.fileExists(atPath: oldOne.path), "the old files are left as they were")

// Comments and other keys survive; the key's earlier lines are replaced.
try! "# my notes\nswap-mb = 4096\nwx = 1\nswap-mb=2048\nenv.MADEIRA_PROMOTE = 0\n".write(to: cfg, atomically: true, encoding: .utf8)
MadeiraConfig.set("swap-mb", "1024")
let text = try! String(contentsOf: cfg, encoding: .utf8)
expect(text.contains("# my notes") && text.contains("wx = 1"), "comments and other keys are kept")
expect(text.components(separatedBy: "swap-mb").count == 2 && MadeiraConfig.get("swap-mb") == "1024", "one line for the key, new value")
expect(!MadeiraConfig.flag("MADEIRA_PROMOTE", fallback: false), "env.MADEIRA_PROMOTE = 0 reads as off")
MadeiraConfig.set("env.MADEIRA_PROMOTE", "1")
expect(MadeiraConfig.flag("MADEIRA_PROMOTE", fallback: false), "env.MADEIRA_PROMOTE = 1 reads as on")
MadeiraConfig.set("env.MADEIRA_PROMOTE", nil)
expect(!MadeiraConfig.flag("MADEIRA_PROMOTE", fallback: false), "removed key: the fallback (off)")
expect(MadeiraConfig.flag("MADEIRA_SOMETHING_ELSE"), "unset flags use their fallback (on)")
MadeiraConfig.set("inproc-sync", "0")
expect(!MadeiraConfig.bool("inproc-sync", default: true), "madsync off is inproc-sync = 0")
MadeiraConfig.set("inproc-sync", nil)
expect(MadeiraConfig.bool("inproc-sync", default: true), "a removed key reads as the default")

// Sync engine: exactly one engine, and the keys Wine reads for it.
expect(SyncEngine.current == .fastsync, "no sync keys: Fastsync (the default)")
SyncEngine.apply(.madsync)
SyncEngine.apply(.fastsync)
expect(MadeiraConfig.get("inproc-sync") == nil && MadeiraConfig.get("env.MADEIRA_FASTSYNC") == nil
       && SyncEngine.current == .fastsync, "Fastsync: both keys removed (the default)")
SyncEngine.apply(.wine)
expect(MadeiraConfig.get("inproc-sync") == "0" && MadeiraConfig.get("env.MADEIRA_FASTSYNC") == nil
       && SyncEngine.current == .wine, "Wine standard sync: inproc-sync = 0, no MADEIRA_FASTSYNC")
SyncEngine.apply(.madsync)
expect(MadeiraConfig.get("inproc-sync") == "1" && MadeiraConfig.get("env.MADEIRA_FASTSYNC") == nil
       && SyncEngine.current == .madsync, "Madsync: inproc-sync = 1, no MADEIRA_FASTSYNC")
let kept = try! String(contentsOf: cfg, encoding: .utf8)
expect(kept.contains("# my notes") && kept.contains("wx = 1"), "engine changes keep comments and other keys")
MadeiraConfig.set("env.MADEIRA_FASTSYNC", "1")
expect(SyncEngine.current == .madsync, "hand-edited: madsync on wins over MADEIRA_FASTSYNC (Wine runs madsync)")
MadeiraConfig.set("inproc-sync", "0")
expect(SyncEngine.current == .fastsync, "hand-edited: inproc-sync = 0 with MADEIRA_FASTSYNC = 1 is Fastsync")
MadeiraConfig.set("env.MADEIRA_FASTSYNC", "0")
expect(SyncEngine.current == .wine, "hand-edited: MADEIRA_FASTSYNC = 0 without madsync is Wine standard sync")
MadeiraConfig.set("env.MADEIRA_FASTSYNC", nil)
expect(SyncEngine.current == .wine, "inproc-sync = 0 without MADEIRA_FASTSYNC stays Wine standard sync (as written before)")
MadeiraConfig.set("inproc-sync", nil)
expect(SyncEngine.current == .fastsync, "neither key: Fastsync")
exit(failed == 0 ? 0 : 1)
'''

with tempfile.TemporaryDirectory() as tmp:
    sp = Path(tmp) / 'settings.swift'
    sp.write_text(swift)
    home = Path(tmp) / 'home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home), CFFIXED_USER_HOME=str(home))
    r = subprocess.run(['swift', str(sp)], capture_output=True, text=True, env=env)
    sys.stdout.write(r.stdout)
    if r.returncode:
        sys.stdout.write(r.stderr[-4000:])
        failures.append('swift harness')

# Wave 3 (2026-10-19): the classic Settings form (Memory & sync, the display-rate row) and Game details (the fastsync
# switches) are deleted. Settings › Memory & performance and Graphics (BCDSettingsLive.swift) and the game page's
# Compatibility group (BCDGamePageLive.swift) do what they did, through the same engine calls; the tables stay in Library.swift.
live = (root / 'app/Madeira/BCDSettingsLive.swift').read_text()
page = (root / 'app/Madeira/BCDGamePageLive.swift').read_text()
settings = block(lib, 'enum RuntimeMemorySyncSettings')
engine = block(lib, 'enum SyncEngine: String, CaseIterable, Identifiable')
apply_env = block(lib, 'func applyEnvironment()')
entry = block(lib, 'struct LibraryEntry: Codable, Identifiable')
check('return inproc == nil ? .fastsync : .wine' in engine, 'fastsync is the engine unless bcd.cfg says otherwise')
check('guard let engine = SyncEngine(rawValue: value) else { return }' in live and 'SyncEngine.apply(engine)' in live
      and 'SyncEngine.fastsync.rawValue] + SyncEngine.allCases.filter { $0 != .fastsync }' in live
      and 'out[.syncEngine] = SyncEngine.current.rawValue' in live,
      'Settings: the Sync engine choice writes through SyncEngine.apply, Fastsync first')
check(['Madsync', 'Fastsync (default)', 'Wine standard sync'] == re.findall(r'case \.\w+: return "([^"]+)"', engine),
      'the choices are Madsync, Fastsync (default) and Wine standard sync')
check('"env.MADEIRA_FASTSYNC"' in settings.split('static let featuredKeys')[1].split(']')[0],
      'All settings leaves env.MADEIRA_FASTSYNC to the engine choice')
check('var fastSync: Bool?' in entry and 'var semaphoreFastPath: Bool?' in entry,
      'per-game fastsync switches are optional, so older library files decode')
check('Self.toggle("fastsync", "Fast synchronization"' in page and 'Self.toggle("semaphores", "Fast semaphore waits (experimental)"' in page
      and 'let fastsync = SyncEngine.current == .fastsync' in page and page.count(', locked: lock),') == 2,
      'game page: Fast synchronization and Fast semaphore waits, locked unless the engine is Fastsync')
check('choose it in Settings › Memory & performance to change it.' in page, 'game page: a note says where to choose Fastsync')
check('if SyncEngine.current == .fastsync {' in apply_env and 'setenv("MADEIRA_FASTSYNC", fastSync == false ? "0" : mode, 1)' in apply_env
      and 'if let sem = semaphoreFastPath { setenv("MADEIRA_FASTSYNC_SEM", sem ? "1" : "0", 1) }' in apply_env
      and 'else { unsetenv("MADEIRA_FASTSYNC_SEM") }' in apply_env,
      'a launch exports the fastsync switches only when the engine is Fastsync')
check('static let swapChoices = [0, 1024, 2048, 3072, 4096]' in settings and 'MadeiraConfig.set(name, mb > 0 ? String(mb) : nil)' in live,
      'swap tier: Off removes swap-mb (off by default)')
check('func setHoldMaximumRate(_ on: Bool) {\n        MadeiraConfig.set(BCDLiveKey.promote, on ? "1" : nil)' in live
      and 'static let promote = "env.MADEIRA_PROMOTE"' in live, 'display-rate hold writes env.MADEIRA_PROMOTE')
check('static var holdMaximum: Bool { MadeiraConfig.flag("MADEIRA_PROMOTE", fallback: false) }' in fps,
      'display-rate hold is off by default')
# Both sections are hidden by MADEIRA_RUNTIME_SETTINGS=0 (RuntimeMemorySyncSettings.shown, read with a literal key so All
# settings keeps listing the switch).
check('static var shown: Bool { MadeiraConfig.flag("MADEIRA_RUNTIME_SETTINGS") }' in settings
      and 'let switchesShown = RuntimeMemorySyncSettings.shown' in live and 'let displaySwitchesShown = RuntimeMemorySyncSettings.shown' in live,
      'MADEIRA_RUNTIME_SETTINGS=0 hides both sections')
print('check-runtime-settings:', 'FAIL' if failures else 'PASS')
sys.exit(1 if failures else 0)
