#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 625 (the owner's item 23, agent E2 S18): a Windows program may ask BCD to open a web page.

Wine's registry opens a page with "C:\\windows\\system32\\winebrowser.exe" "<url>", Unreal Engine's LaunchURL and ShellExecute follow
it, and BCD had no winebrowser.exe that could open anything on iOS (Satisfactory's Epic sign-in SAZFJQ: "CreateProc failed: File not
found"; BeamNG's crash page and Hitman's Steam link went nowhere). BCD's own winebrowser.exe (bcd-openurl/) leaves an http or https
address in drive_c/ProgramData/BCD/OpenURL; while a session runs the app asks the player (Open inside BCD, Open in Safari, Copy link,
Not now). This checks:
  - bcd-openurl/core.h: the command lines, the page test, the host for the log, the file name and the switch (core_test.c, with the
    host's clang and the sanitizers);
  - the three tracked programs: the machine of each farm, a GUI program, imports of kernel32 and the C runtime only, the marker line,
    and (with llvm-mingw at hand, as in CI and the local build) the same bytes as a build of the source;
  - the build step: the three farms, the 32-bit one after the i386 farm in place of Wine's own, and the farms a session links;
  - BCDOpenURL.swift, compiled with a driver: the page test again, the oldest-first requests (a .part file is left), the read limit,
    one question at a time and three a minute, the words;
  - Library.swift: the watcher's guards and order, the question's four answers, nothing opened without a tap, the host only in the log,
    the leftovers cleared at a start; the Xcode project, the catalog (default on), the auto-tune rule of the code fix, CI.
"""
from pathlib import Path
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


src = root / 'bcd-openurl'
core = (src / 'core.h').read_text()
prog = (src / 'openurl.c').read_text()

# --- 1. the rules, on the host ------------------------------------------------------------------------------------------------------
clang = shutil.which('clang') or shutil.which('cc')
check('a C compiler is at hand', clang is not None)
if clang:
    with tempfile.TemporaryDirectory() as d:
        exe = Path(d) / 'core_test'
        b = subprocess.run([clang, '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined',
                            '-fno-sanitize-recover=undefined', '-I', str(src), '-o', str(exe), str(src / 'core_test.c')],
                           capture_output=True, text=True)
        check('core_test.c compiles with the sanitizers', b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            m = re.search(r'(\d+) checks, (\d+) failures', r.stdout)
            check('core.h: the command lines, the page test, the host, the file name and the switch (%s checks)' % (m.group(1) if m else '?'),
                  r.returncode == 0 and m is not None and m.group(2) == '0' and int(m.group(1)) >= 30, r.stdout[-1500:])

# --- 2. the program -----------------------------------------------------------------------------------------------------------------
check('the program writes the request finished (a .part renamed) and only an http or https page',
      'CreateFileA(part, GENERIC_WRITE' in prog and 'MoveFileExA(part, done, MOVEFILE_REPLACE_EXISTING)' in prog
      and prog.index('if (!ou_is_page(url))') < prog.index('CreateDirectoryA(FOLDER_PARENT, NULL);')
      and '#define FOLDER        "C:\\\\ProgramData\\\\BCD\\\\OpenURL"' in prog)
check('MADEIRA_OPEN_URL=0 makes it fail as before, before anything is read or written',
      'GetEnvironmentVariableA("MADEIRA_OPEN_URL", env, sizeof env)' in prog
      and prog.index('ou_off(env)') < prog.index('GetCommandLineW') < prog.index('CreateFileA(')
      or prog.index('ou_off(env)') < prog.index('WideCharToMultiByte'))
_says = re.findall(r'\n\s+say\(([^;]*)\);', prog)   # the calls (the definition starts its line)
_texts = re.findall(r'snprintf\(text, sizeof text, ("(?:[^"\\]|\\.)*"(?:\s*"(?:[^"\\]|\\.)*")*),([^;]*)\);', prog)
check('its log lines name the host only (the rest of an address can carry a sign-in code)',
      'ou_host(url, host, sizeof host);' in prog and _says and all(a.strip().startswith('"') or a.strip() == 'text' for a in _says)
      and len(_texts) == 3 and all(not re.search(r'\burl\b(?!\))', args.replace('strlen(url)', '')) for _fmt, args in _texts)
      and 'snprintf(text, sizeof text, "[open-url] madeira-bcd: a program asks to open a page on %s (%u characters); BCD asks the player\\n",' in prog,
      str(_says) + ' | ' + str(_texts))
check('it opens no window and ends at once (no wait for the player)',
      'WinMain' in prog and 'CreateWindow' not in prog and 'WaitFor' not in prog and 'Sleep(' not in prog)


def pe_info(path):
    data = path.read_bytes()
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        return None
    machine = struct.unpack_from('<H', data, pe + 4)[0]
    opt = pe + 24
    magic = struct.unpack_from('<H', data, opt)[0]
    sub = struct.unpack_from('<H', data, opt + 68)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    secs = pe + 24 + struct.unpack_from('<H', data, pe + 20)[0]
    sections = [struct.unpack_from('<8sIIII', data, secs + 40 * i) for i in range(nsec)]

    def rva2off(rva):
        for _name, vsize, vaddr, rsize, roff in sections:
            if vaddr <= rva < vaddr + max(vsize, rsize):
                return roff + rva - vaddr
        raise ValueError(rva)
    dd = opt + (112 if magic == 0x20B else 96)
    o = rva2off(struct.unpack_from('<I', data, dd + 8)[0])
    names = []
    while True:
        f = struct.unpack_from('<IIIII', data, o)
        if f[3] == 0:
            break
        n = rva2off(f[3])
        names.append(data[n:data.index(b'\0', n)].decode().lower())
        o += 20
    return machine, magic, sub, names, data


farms = [('arm64ec-windows (x86-64)', root / 'app/Madeira/arm64ec-windows/winebrowser.exe', 0x8664, 0x20B, 'x86_64'),
         ('aarch64-windows (ARM64)', root / 'app/Madeira/aarch64-windows/winebrowser.exe', 0xAA64, 0x20B, 'aarch64'),
         ('the 32-bit farm (i686, tracked as bcd-openurl/winebrowser-x86.exe)', src / 'winebrowser-x86.exe', 0x14C, 0x10B, 'i686')]
mingw = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
for what, path, machine, magic, triple in farms:
    info = pe_info(path) if path.exists() else None
    check('the tracked program for %s: the machine, a GUI program, kernel32 and the C runtime only, the marker line' % what,
          info is not None and info[0] == machine and info[1] == magic and info[2] == 2
          and all(n == 'kernel32.dll' or n.startswith('api-ms-win-crt-') for n in info[3])
          and b'[open-url] madeira-bcd' in info[4], str(info[:4]) if info else 'missing')
    cc = mingw / ('%s-w64-mingw32-clang' % triple)
    if cc.exists():
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'wb.exe'
            b = subprocess.run([str(cc), '-O2', '-Wall', '-Wextra', '-Werror', '-mwindows', '-Wl,--no-insert-timestamp', '-Wl,--strip-all',
                                '-I', str(src), '-o', str(out), str(src / 'openurl.c')], capture_output=True, text=True)
            check('... and it is the build of the source, byte for byte (%s)' % triple,
                  b.returncode == 0 and out.read_bytes() == path.read_bytes(), b.stderr[-800:])
    else:
        print('note: no llvm-mingw here (%s); the tracked %s is not rebuilt' % (mingw, triple))

# --- 3. the build step and the farms ------------------------------------------------------------------------------------------------
sh = (root / 'tools/build-bcd-openurl.sh').read_text()
check('the build puts x86-64 into arm64ec-windows, aarch64 into aarch64-windows and i686 into the 32-bit farm in place of Wine\'s',
      'build x86_64 8664 "$EC/winebrowser.exe"' in sh and 'build aarch64 AA64 "$AA/winebrowser.exe"' in sh
      and 'build i686 14C "$TRACKED_X86"' in sh and 'cp "$TRACKED_X86" "$X86/winebrowser.exe"' in sh
      and sh.index('cp "$TRACKED_X86" "$X86/winebrowser.exe"') > sh.index('build i686 14C'))
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI builds them after the i386 farm and says whether each farm has BCD\'s',
      'run: |\n          bash tools/build-bcd-openurl.sh' in wf
      and wf.index("- name: Build BCD's winebrowser.exe (three farms)") > wf.index('- name: Save i386 farm')
      and 'is not BCD\'s winebrowser.exe' in wf)
check('CI runs this check', 'python3 tests/host/check-open-url.py' in wf)
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
check('a session links every file of its farm into system32 and the 32-bit farm into syswow64 (so C:\\windows\\system32\\winebrowser.exe '
      'is BCD\'s in each session)',
      'NSString *sys32Dir = [prefix stringByAppendingPathComponent:@"drive_c/windows/system32"];' in bridge
      and 'NSString *farmDir = [prefix stringByAppendingPathComponent:@"drive_c/windows/syswow64"];' in bridge
      and 'NSString *source = [bundle stringByAppendingPathComponent:@"i386-windows"];' in bridge)
gi = (root / '.gitignore').read_text()
check('the 32-bit farm stays untracked; the build directory is ignored', 'app/Madeira/i386-windows/*' in gi and 'build/bcd-openurl/' in gi)

# --- 4. BCDOpenURL.swift ------------------------------------------------------------------------------------------------------------
swiftc = shutil.which('swiftc')
swift_src = root / 'app/Madeira/BCDOpenURL.swift'
check('BCDOpenURL.swift is plain Foundation', 'import UIKit' not in swift_src.read_text() and 'import Foundation' in swift_src.read_text())
driver = r'''
import Foundation
var failures = 0
func expect(_ c: Bool, _ what: String) { if c { print("ok   " + what) } else { failures += 1; print("FAIL " + what) } }
@main struct Driver { static func main() {
    let P = BCDOpenURL.self
    expect(P.page("https://www.epicgames.com/activate?userCode=ABCD1234")?.host == "www.epicgames.com", "an https page")
    expect(P.page("  http://go.beamng.com/x\n") != nil, "an http page, its line ending trimmed")
    expect(P.page("HTTPS://Example.com") != nil, "the scheme in capitals")
    expect(P.page("file:///C:/readme.html") == nil && P.page("mailto:a@b.c") == nil && P.page("ftp://a.b/") == nil
           && P.page("javascript:alert(1)") == nil && P.page("steam://run/1") == nil, "anything but http and https")
    expect(P.page("https://user:pass@example.com/") == nil && P.page("https://user@example.com/") == nil, "a user name or password")
    expect(P.page("https://") == nil && P.page("https:///x") == nil && P.page("") == nil, "no host")
    expect(P.page("https://exa mple.com") == nil && P.page("https://example.com/\u{e9}") == nil, "a space or a byte above 0x7e")
    expect(P.page("https://" + String(repeating: "a", count: 2040)) != nil && P.page("https://" + String(repeating: "a", count: 2041)) == nil,
           "2048 characters pass, 2049 do not")
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("bcd-openurl-\(getpid())", isDirectory: true)
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    for (name, text) in [("00000000000000002000-7.url", "https://b.example/"), ("00000000000000001000-7.url", "https://a.example/"),
                         ("00000000000000003000-7.url.part", "https://c.example/"), ("notes.txt", "x")] {
        try? text.write(to: dir.appendingPathComponent(name), atomically: true, encoding: .utf8)
    }
    let found = P.requests(in: dir).map(\.lastPathComponent)
    expect(found == ["00000000000000001000-7.url", "00000000000000002000-7.url"], "finished requests oldest first; a .part file is left (\(found))")
    expect(P.requests(in: dir.appendingPathComponent("none")).isEmpty, "no folder: nothing")
    let big = dir.appendingPathComponent("00000000000000004000-7.url")
    try? String(repeating: "x", count: 9000).write(to: big, atomically: true, encoding: .utf8)
    expect(P.read(big)?.utf8.count == P.maxFileBytes, "a request is read 4096 bytes far")
    try? FileManager.default.removeItem(at: dir)
    var g = BCDOpenURL.Gate()
    let t0 = Date(timeIntervalSince1970: 1000)
    expect(g.admit(now: t0) && !g.admit(now: t0.addingTimeInterval(1)), "one question at a time")
    g.answered()
    expect(g.admit(now: t0.addingTimeInterval(2)), "the next one after an answer")
    g.answered()
    expect(g.admit(now: t0.addingTimeInterval(3)), "a third within the minute")
    g.answered()
    expect(!g.admit(now: t0.addingTimeInterval(4)), "not a fourth within the minute")
    expect(g.admit(now: t0.addingTimeInterval(61)), "again a minute after the first")
    let long = URL(string: "https://example.com/" + String(repeating: "p", count: 300))!
    expect(P.shown(long).count == 160 && P.shown(long).hasSuffix("..."), "a long address is shown cut to 160 characters")
    let m = P.message(game: "Satisfactory", url: URL(string: "https://www.epicgames.com/activate")!)
    expect(m.hasPrefix("Satisfactory wants to open a web page:\n\nhttps://www.epicgames.com/activate")
           && m.hasSuffix("Open shows it inside BCD, so the game keeps running.") && P.title == "Open a web page?", "the words")
    print(failures == 0 ? "DRIVER OK" : "DRIVER FAILED \(failures)")
    exit(failures == 0 ? 0 : 1)
} }
'''
if swiftc:
    with tempfile.TemporaryDirectory() as d:
        drv = Path(d) / 'driver.swift'
        drv.write_text(driver)
        exe = Path(d) / 'openurl'
        b = subprocess.run([swiftc, '-parse-as-library', '-swift-version', '5', '-o', str(exe), str(swift_src), str(drv)],
                           capture_output=True, text=True)
        check('BCDOpenURL.swift compiles with the driver', b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            n = r.stdout.count('ok   ')
            check('BCDOpenURL: the page test, the requests, the read limit, the gate and the words (%d checks)' % n,
                  r.returncode == 0 and 'DRIVER OK' in r.stdout, r.stdout[-1500:])
else:
    print('note: no swiftc here; BCDOpenURL.swift is not run')

# --- 5. the app ---------------------------------------------------------------------------------------------------------------------
lib = (root / 'app/Madeira/Library.swift').read_text()
fn = lib[lib.index('    private func watchOpenURL() {'):]
fn = fn[:fn.index('\n    }\n') + 7]
check('watched once a poll while the Wine process runs, after the hints',
      lib.count('watchOpenURL()') == 2 and '            watchWindowedHint()\n            watchOpenURL()\n' in lib)
check('only while the app is in front; each request is taken (its file deleted) before it is checked',
      fn.startswith('    private func watchOpenURL() {\n        guard UIApplication.shared.applicationState == .active else { return }')
      and fn.index('try? FileManager.default.removeItem(at: file)') < fn.index('guard let url = BCDOpenURL.page(text) else {'))
check('the switch, then one question at a time and three a minute; anything left gets a log line with the host only',
      fn.index('BCDOpenURL.page(text)') < fn.index('MadeiraConfig.flag("MADEIRA_OPEN_URL")') < fn.index('openURLGate.admit(now: Date())')
      and not re.search(r'log\([^)]*\\\(url', fn) and not re.search(r'log\([^)]*\\\(text\)', fn))
ask = lib[lib.index('    private func askOpenURL(_ url: URL, game: String) {'):]
ask = ask[:ask.index('\n    }\n') + 7]
check('the question: Open (inside BCD), Open in Safari, Copy link, Not now; Open is the preferred answer',
      'UIAlertController(title: BCDOpenURL.title, message: BCDOpenURL.message(game: game, url: url), preferredStyle: .alert)' in ask
      and 'over.present(SFSafariViewController(url: url), animated: true)' in ask
      and ask.count('UIApplication.shared.open(url)') == 2 and 'UIPasteboard.general.string = url.absoluteString' in ask
      and 'UIAlertAction(title: "Not now", style: .cancel)' in ask and 'alert.preferredAction = open' in ask)
check('nothing opens without a tap: the page is opened only inside an answer\'s handler',
      fn.count('UIApplication.shared.open') == 0 and ask.index('UIApplication.shared.open(url)') > ask.index('let open = UIAlertAction(')
      and 'self?.openURLGate.answered()' in ask)
check('the log names the host, never the address', not re.search(r'log\([^)]*absoluteString', ask) and '\\(host)' in ask)
check('a start clears requests left by an earlier session',
      'Self.clearOpenURLRequests()' in lib[lib.index('MetalBackedView.touchesSinceLaunch = 0'):][:400]
      and 'for file in BCDOpenURL.requests(in: folder) { try? FileManager.default.removeItem(at: file) }' in lib)
check('Library.swift imports SafariServices', 'import SafariServices' in lib)
pbx = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text()
check('BCDOpenURL.swift is in the app target (build file, file reference, group, sources)',
      sum('BCDOpenURL.swift' in line for line in pbx.splitlines()) == 4 and 'A1F4600B /* BCDOpenURL.swift in Sources */,' in pbx
      and 'A2F4600B /* BCDOpenURL.swift */,' in pbx)
cat = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the settings catalog knows the switch (on by default; 0: fails as before)',
      re.search(r'"env\.MADEIRA_OPEN_URL": \{[^}]*?"kind": "bool", "default": "1"', cat, re.S) is not None)
rules = (root / 'build/tools/autotune_rules.py').read_text()
r = rules[rules.index('"id": "eos-sign-in-browser-missing"'):]
r = r[:r.index('\n    },')]
check('auto-tune: Satisfactory\'s Epic sign-in that found no browser is fixed in 625 (the interim noFix rule retired)',
      '"fixedIn": 625' in r and 's("LogEOSAuth: LaunchAccountPortalWithPlatformBrowser: Failed to launch with platform browser")' in r
      and 's("[open-url] ")' in r and '"id": "eos-login-needs-browser"' not in rules)
print('PASS: web pages from Windows programs' if ok else 'FAIL: web pages from Windows programs')
raise SystemExit(0 if ok else 1)
