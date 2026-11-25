#!/usr/bin/env python3
# Portions copyright 2026 spenstars1: the stray-byte cases of the Swift part (the byte that is not UTF-8 in bcd.cfg) follow
# tests/host/check-runtime-settings.py of https://github.com/willfaust/Madeira (pull request #629, commit 0d057f1b) under
# GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
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
   A byte that is not UTF-8 in bcd.cfg (a comment saved as Windows-1252 by an editor on a PC; Madeira #629): all() and get()
   still see every key (the native reader applies them), set() keeps that line byte for byte and every other line, a file that
   cannot be read is not rewritten, a UTF-8 file with non-ASCII text and a byte order mark comes back unchanged apart from the
   key. The same bytes go through the Objective-C reader of WineProcessBridge.m (compiled out of the app, on the host): its env.*
   lines are exported, a UTF-8 line is not re-encoded, and an ordinary UTF-8 file reads as before.
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
import shutil
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

// A byte that is not UTF-8 (a comment saved as Windows-1252, 0xE9 = e acute) neither hides bcd.cfg from Settings nor lets set()
// rewrite it with only the new key (Madeira #629).
func bytes(_ parts: [Any]) -> Data {
    var d = Data()
    for p in parts { if let b = p as? UInt8 { d.append(b) } else if let s = p as? String { d.append(contentsOf: Array(s.utf8)) } }
    return d
}
let latin = bytes(["# r", UInt8(0xE9), "glages\r\nswap-mb = 4096\r\nvram-mb = 3072\nenv.MADEIRA_STEAM_LANGUAGE = french\n"])
try! latin.write(to: cfg)
expect(MadeiraConfig.present && MadeiraConfig.get("swap-mb") == "4096" && MadeiraConfig.get("env.MADEIRA_STEAM_LANGUAGE") == "french",
       "a byte that is not UTF-8 does not hide bcd.cfg")
expect(MadeiraConfig.all().count == 3, "Settings sees all three keys of such a file (the CRLF line too)")
expect(MadeiraConfig.set("env.MADEIRA_STEAM_CLOUD", "0"), "set() on such a file succeeds")
let rewritten = try! Data(contentsOf: cfg)
expect(rewritten.starts(with: bytes(["# r", UInt8(0xE9), "glages\n"])), "set() keeps the comment byte for byte")
expect(MadeiraConfig.get("swap-mb") == "4096" && MadeiraConfig.get("vram-mb") == "3072"
       && MadeiraConfig.get("env.MADEIRA_STEAM_LANGUAGE") == "french" && MadeiraConfig.get("env.MADEIRA_STEAM_CLOUD") == "0",
       "set() keeps every other key of such a file")
expect(rewritten == bytes(["# r", UInt8(0xE9), "glages\nswap-mb = 4096\nvram-mb = 3072\nenv.MADEIRA_STEAM_LANGUAGE = french\nenv.MADEIRA_STEAM_CLOUD = 0\n"]),
       "the file is the old one with its CR bytes gone and the new line last: nothing else changed")
MadeiraConfig.set("swap-mb", nil)
MadeiraConfig.set("vram-mb", "2048")
let edited = try! Data(contentsOf: cfg)
expect(edited.starts(with: bytes(["# r", UInt8(0xE9), "glages\n"])) && MadeiraConfig.get("swap-mb") == nil && MadeiraConfig.get("vram-mb") == "2048",
       "removing and replacing keys of such a file keeps the comment byte")

// The stray byte inside a value line: that value reads with U+FFFD, and set() still writes the line back byte for byte.
let inValue = bytes(["wx = 1\nenv.MADEIRA_NOTE = caf", UInt8(0xE9), "\nswap-mb = 1024\n"])
try! inValue.write(to: cfg)
expect(MadeiraConfig.get("wx") == "1" && MadeiraConfig.get("swap-mb") == "1024" && MadeiraConfig.get("env.MADEIRA_NOTE") == "caf\u{FFFD}",
       "a stray byte in a value reads as U+FFFD and costs the other lines nothing")
MadeiraConfig.set("wx", "0")
expect(try! Data(contentsOf: cfg) == bytes(["env.MADEIRA_NOTE = caf", UInt8(0xE9), "\nswap-mb = 1024\nwx = 0\n"]),
       "set() writes the line with the stray byte back byte for byte")

// A UTF-8 file with non-ASCII text, a byte order mark and no final newline: only the key changes.
let utf8 = bytes([UInt8(0xEF), UInt8(0xBB), UInt8(0xBF), "# notes: \u{E9}\u{FC}\u{4E2D}\nswap-mb = 1024\nwx = 1"])
try! utf8.write(to: cfg)
expect(MadeiraConfig.get("swap-mb") == "1024" && MadeiraConfig.get("wx") == "1", "a UTF-8 file with a byte order mark still reads")
MadeiraConfig.set("wx", "0")
expect(try! Data(contentsOf: cfg) == bytes([UInt8(0xEF), UInt8(0xBB), UInt8(0xBF), "# notes: \u{E9}\u{FC}\u{4E2D}\nswap-mb = 1024\nwx = 0\n"]),
       "a UTF-8 file keeps its byte order mark and its non-ASCII text; the last line gets its newline")

// A file that exists but cannot be read is neither read nor rewritten (set() returns false).
try! latin.write(to: cfg)
try! FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: cfg.path)
if !FileManager.default.isReadableFile(atPath: cfg.path) {
    expect(MadeiraConfig.all().isEmpty, "a file that cannot be read shows no keys")
    expect(MadeiraConfig.set("wx", "1") == false, "set() on a file that cannot be read returns false")
    try! FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: cfg.path)
    expect(try! Data(contentsOf: cfg) == latin, "and leaves it as it was (not rewritten with only the new key)")
} else {
    try! FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: cfg.path)
    print("SKIP: this user can read a file with mode 0 (root): the unreadable-file case is not run")
}
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

# The Objective-C reader of bcd.cfg's env.* lines (WineProcessBridge.m, the block between the "bcd-cfg-bytes-v1" markers) is cut out of the
# source and compiled on the host with Foundation: the same bytes as the Swift cases above (Madeira #629).
def chk(what, cond, detail=''):
    check(cond, what + ((' -- ' + str(detail)[:600]) if (detail and not cond) else ''))


bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
b0 = bridge.index('NSString *cfg = nil;\n                    NSData *cfgData')
b1 = bridge.index('/* end madeira-bcd: bcd-cfg-bytes-v1 */')
check(bridge.count('/* madeira-bcd: bcd-cfg-bytes-v1') == 1 and bridge.count('end madeira-bcd: bcd-cfg-bytes-v1') == 1
      and 'stringWithContentsOfFile:[docs stringByAppendingPathComponent:@MADEIRA_CFG_FILE]' not in bridge
      and bridge.count('stringByAppendingPathComponent:@MADEIRA_CFG_FILE]') == 1,
      'WineProcessBridge.m reads bcd.cfg as bytes, once (check-bcd-file-names.py counts that call)')
clang = shutil.which('clang')
if not clang:
    print('SKIP: no clang: the Objective-C reader of bcd.cfg is not compiled')
else:
    HARNESS_HEAD = r"""
#import <Foundation/Foundation.h>
#define MADEIRA_CFG_FILE "bcd.cfg"
int main(int argc, char **argv) {
    @autoreleasepool {
        NSString *docs = [NSString stringWithUTF8String:argv[1]];
        int old = argc > 2;
        NSMutableString *acc = [NSMutableString string];
        NSString *cfg = nil;
        if (old) {
            cfg = [NSString stringWithContentsOfFile:[docs stringByAppendingPathComponent:@MADEIRA_CFG_FILE] encoding:NSUTF8StringEncoding error:nil];
        } else {
"""
    HARNESS_TAIL = r"""
        }
        for (NSString *raw in [cfg componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
            NSString *line = [raw stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
            NSRange eq = [line rangeOfString:@"="];
            if (![line hasPrefix:@"env."] || eq.location == NSNotFound) continue;
            NSString *k = [[line substringWithRange:NSMakeRange(4, eq.location - 4)] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
            NSString *v = [[line substringFromIndex:eq.location + 1] stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
            if (k.length) [acc appendFormat:@"%@=%@\n", k, v];
        }
        fputs(acc.UTF8String, stdout);
    }
    return 0;
}
"""
    # the block assigns the outer `cfg`: drop its own declaration
    body = bridge[b0:b1].replace('NSString *cfg = nil;\n', '', 1)
    harness = HARNESS_HEAD + body + HARNESS_TAIL
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'reader.m').write_text(harness)
        cr = subprocess.run([clang, '-fobjc-arc', '-Wall', '-Werror', str(tmp / 'reader.m'), '-framework', 'Foundation', '-o', str(tmp / 'reader')],
                            capture_output=True, text=True)
        chk('the Objective-C reader of bcd.cfg compiles with Foundation (-Wall -Werror)', cr.returncode == 0, cr.stderr)
        if cr.returncode == 0:
            def run(data, old=False):
                d = tmp / 'docs'
                d.mkdir(exist_ok=True)
                (d / 'bcd.cfg').write_bytes(data)
                r = subprocess.run([str(tmp / 'reader'), str(d)] + (['old'] if old else []), capture_output=True)
                return r.stdout.decode('utf-8', 'replace')
            stray = b'# r\xe9glages\r\nswap-mb = 4096\r\nenv.MADEIRA_STEAM_LANGUAGE = french\nenv.MADEIRA_NAME = caf\xc3\xa9\n'
            chk('the old reader exports nothing from a file with a byte that is not UTF-8 (the fault)', run(stray, old=True) == '')
            chk('a byte that is not UTF-8: every env line is exported, and a UTF-8 value is not re-encoded',
                  run(stray) == 'MADEIRA_STEAM_LANGUAGE=french\nMADEIRA_NAME=café\n', run(stray))
            chk('a stray byte inside a value line: that line reads as Latin 1, the others as they are',
                  run(b'env.MADEIRA_A = 1\nenv.MADEIRA_B = caf\xe9\nenv.MADEIRA_C = \xc3\xa9\n') == 'MADEIRA_A=1\nMADEIRA_B=café\nMADEIRA_C=é\n')
            ok_file = b'\xef\xbb\xbf# n\nenv.MADEIRA_A = 1\r\nenv.MADEIRA_B = x = y\nswap-mb = 1\n'
            chk('a UTF-8 file reads exactly as before (the new code is not reached)',
                  run(ok_file) == run(ok_file, old=True) and 'MADEIRA_B=x = y' in run(ok_file), run(ok_file))
            chk('an empty file and a file without a final newline',
                  run(b'') == '' and run(b'env.MADEIRA_A = 1\xe9') == 'MADEIRA_A=1é\n', run(b'env.MADEIRA_A = 1\xe9'))

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
