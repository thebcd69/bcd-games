#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 625 (the owner's item 26): Microsoft's 32-bit MFC for 32-bit games, downloaded on the device.

What Lies in the Multiverse's Razer Chroma plug-in (T3CXNQ) and Muv-Luv photonflowers' Ages3ResT.dll (4FD24J) stopped for want of the
32-bit mfc140u.dll, which BCD did not have. The app now downloads Microsoft's vc_redist.x86.exe the way it downloads the x64 package
(VCRuntimeDownload.swift, VCRuntime.x86), keeps its four MFC DLLs in Library/Application Support/VCRuntime/x86, and a 32-bit game gets
them in its syswow64 when its config says env.MADEIRA_MFC32 = 1 (WineProcessBridge.m madeira_mfc32); auto-tune adds that after such a
start. Checks:
  1. VCRuntimeDownload.swift compiled on the host with BCDCab.c, on a synthetic x86 package (stored cabinets in a fake installer):
     exactly the four <name>.dll_x86 DLLs, byte for byte; an x64 decoy and a language DLL are not taken; an x64 DLL under an x86 name,
     an unsigned one, or a missing one is refused by name; the x64 package's code reads the same installer for its own fourteen;
     the folders and file names of both packages;
  2. with Microsoft's real package (VCRUNTIME_X86_INSTALLER, or /tmp/vcr-x86.exe that CI downloads), the same four DLLs as
     cabextract gives, each i386, PE32, a DLL, signed, with its version;
  3. madeira_mfc32 compiled on the host: links only when asked and downloaded, never over a real file, removes only its own links,
     MADEIRA_MFC = 0 keeps MFC out, one log line;
  4. the readiness note, Settings, the two auto-tune rules, the two list rows and the policy, the catalog, the Xcode project, CI.
check-vcruntime-download.py still checks the x64 path as before.
"""
import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / 'app/Madeira'
ap = argparse.ArgumentParser()
ap.add_argument('--require-swift', action='store_true')
args = ap.parse_args()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)[-1500:]) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- synthetic PE files and cabinets
def pe_dll(machine, pe32plus, signed=True, body=b''):
    """A DLL the app's PEImage reads: the headers, no sections, an Authenticode table at the end when signed."""
    opt_size = 240 if pe32plus else 224
    dd_at = 112 if pe32plus else 96
    head = bytearray(b'MZ' + bytes(58) + struct.pack('<I', 0x40))
    coff = struct.pack('<HHIIIHH', machine, 0, 0, 0, 0, opt_size, 0x2102)
    opt = bytearray(opt_size)
    struct.pack_into('<H', opt, 0, 0x20B if pe32plus else 0x10B)
    struct.pack_into('<I', opt, dd_at - 4, 16)
    data = head + b'PE\0\0' + coff
    end = len(data) + opt_size + len(body)
    cert = struct.pack('<IHH', 24, 0x0200, 0x0002) + bytes(16)
    if signed:
        struct.pack_into('<II', opt, dd_at + 8 * 4, end, len(cert))
    return bytes(data + opt + body + (cert if signed else b''))


def cab_checksum(data, seed=0):
    s = seed
    n = len(data) // 4
    for (w,) in struct.iter_unpack('<I', data[:n * 4]):
        s ^= w
    rest = 0
    for b in data[n * 4:]:
        rest = (rest << 8) | b
    return s ^ rest


def stored_cab(files):
    """One folder, no compression; files: [(name, bytes)]."""
    blob = b''.join(d for _, d in files)
    blocks = [blob[i:i + 32768] for i in range(0, len(blob), 32768)] or [b'']
    files_at = 36 + 8
    entries, off = b'', 0
    for name, d in files:
        entries += struct.pack('<IIHHHH', len(d), off, 0, 0x5A52, 0x6000, 0x20) + name.encode() + b'\0'
        off += len(d)
    pos = files_at + len(entries)
    folder = struct.pack('<IHH', pos, len(blocks), 0)
    data = b''
    for b in blocks:
        tail = struct.pack('<HH', len(b), len(b))
        data += struct.pack('<I', cab_checksum(tail, cab_checksum(b))) + tail + b
    total = pos + len(data)
    header = struct.pack('<4sIIIIIBBHHHHH', b'MSCF', 0, total, 0, files_at, 0, 3, 1, 1, len(files), 0, 0x4243, 0)
    return header + folder + entries + data


def installer(inner_files):
    inner = stored_cab(inner_files)
    outer = stored_cab([('a11', inner), ('u0', b'not a cabinet ' * 10)])
    return b'MZ' + bytes(62) + b'stub of a setup program ' * 40 + outer + b'trailing bytes'


X86 = {n: pe_dll(0x14C, False, body=('%s 32-bit' % n).encode() * 40) for n in ('mfc140.dll', 'mfc140u.dll', 'mfcm140.dll', 'mfcm140u.dll')}
good = [(n + '_x86', d) for n, d in X86.items()] + [('mfc140u.dll_amd64', pe_dll(0x8664, True)), ('mfc140enu.dll_x86', pe_dll(0x14C, False))]

T = Path(tempfile.mkdtemp(prefix='bcd-mfc32-'))
(T / 'good.exe').write_bytes(installer(good))
(T / 'x64named.exe').write_bytes(installer([(n, (pe_dll(0x8664, True) if n.startswith('mfc140u.') else d)) for n, d in good]))
(T / 'unsigned.exe').write_bytes(installer([(n, (pe_dll(0x14C, False, signed=False) if n.startswith('mfcm140.') else d)) for n, d in good]))
(T / 'missing.exe').write_bytes(installer([(n, d) for n, d in good if not n.startswith('mfcm140u.')]))

# ---------------------------------------------------------------- 1, 2: the Swift installer on the host
STUBS = r'''
import Foundation
final class LogStore {
    static let shared = LogStore()
    func log(_ message: String) { FileHandle.standardError.write(Data((message + "\n").utf8)) }
}
func wine_process_is_running() -> Int32 { 0 }
'''
MAIN = r'''
import Foundation
@main struct Driver { static func main() {
    let a = CommandLine.arguments
    let package: VCRuntimePackage = a.last == "x64" ? .x64 : .x86
    switch a[1] {
    case "extract":
        let out = URL(fileURLWithPath: a[3])
        do {
            try VCRuntimeInstaller.extract(installer: URL(fileURLWithPath: a[2]), into: out, package: package)
            let names = ((try? FileManager.default.contentsOfDirectory(atPath: out.path)) ?? []).sorted()
            print("ok " + names.joined(separator: " "))
        } catch { print("error " + ((error as? LocalizedError)?.errorDescription ?? "\(error)")) }
    case "install":
        let root = URL(fileURLWithPath: a[3])
        do {
            let v = try VCRuntimeInstaller.install(installer: URL(fileURLWithPath: a[2]), downloadedFrom: nil, root: root, package: package)
            let folder = VCRuntimeInstaller.folder(in: root, package: package)
            print("ok version=\(v) folder=\(folder.lastPathComponent) installed=\(VCRuntimeInstaller.installedVersion(in: folder, package: package) != nil)")
            try VCRuntimeInstaller.remove(root: root, package: package)
            print("removed=\(!FileManager.default.fileExists(atPath: folder.path))")
        } catch { print("error " + ((error as? LocalizedError)?.errorDescription ?? "\(error)")) }
    case "pe":
        for path in a[2...] where path != "x86" && path != "x64" {
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)), let pe = PEImage(data) else { print("nope"); continue }
            print("machine=0x\(String(pe.machine, radix: 16)) pe32plus=\(pe.isPE32Plus ? 1 : 0) dll=\(pe.isDLL ? 1 : 0) signed=\(pe.hasCertificate ? 1 : 0) version=\(pe.fileVersion ?? "-")")
        }
    case "names":
        let root = URL(fileURLWithPath: "/r")
        for p in [VCRuntimePackage.x64, VCRuntimePackage.x86] {
            print("\(p.name) \(VCRuntimeInstaller.folder(in: root, package: p).path) \(VCRuntimeInstaller.downloadFile(in: root, package: p).path) \(p.url.absoluteString) \(p.marker) \(p.entrySuffix) \(p.dllNames.count)")
        }
        print("default \(VCRuntimeInstaller.folder(in: root).path) \(VCRuntimeInstaller.downloadFile(in: root).path) \(VCRuntimeSource.url.absoluteString)")
    default: print("?")
    }
} }
'''
swiftc = shutil.which('swiftc')
cc = shutil.which('clang') or shutil.which('cc')
swift_ok = False
if not swiftc or not cc:
    check('Swift and C compilers available', not args.require_swift)
else:
    (T / 'stubs.swift').write_text(STUBS)
    (T / 'main.swift').write_text(MAIN)
    wm = (APP / 'WineMono.swift').read_text()
    (T / 'winemono.swift').write_text(wm[wm.index('import Foundation'):wm.index('// MARK: - Model')])
    b1 = subprocess.run([cc, '-c', '-O2', '-I', str(APP), str(APP / 'BCDCab.c'), '-o', str(T / 'BCDCab.o')], capture_output=True, text=True)
    b2 = subprocess.run([swiftc, '-parse-as-library', '-swift-version', '5', '-Onone', '-import-objc-header', str(APP / 'BCDCab.h'),
                         str(APP / 'VCRuntimeDownload.swift'), str(T / 'winemono.swift'), str(T / 'stubs.swift'), str(T / 'main.swift'),
                         str(T / 'BCDCab.o'), '-lz', '-o', str(T / 'drv')], capture_output=True, text=True) if b1.returncode == 0 else b1
    swift_ok = b2.returncode == 0
    check('VCRuntimeDownload.swift builds on the host with BCDCab.c', swift_ok, b2.stderr)


def drv(*argv):
    r = subprocess.run([str(T / 'drv')] + [str(x) for x in argv], capture_output=True, text=True)
    return r.stdout.strip()


if swift_ok:
    names = drv('names').splitlines()
    check('the x64 package keeps its folder, file name and link; the x86 one has its own (VCRuntime/x86, vc_redist.x86.exe, _x86, 4 DLLs)',
          names == ['x64 /r/x86_64 /r/vc_redist.x64.exe.download https://aka.ms/vs/17/release/vc_redist.x64.exe vcruntime140.dll _amd64 14',
                    'x86 /r/x86 /r/vc_redist.x86.exe.download https://aka.ms/vs/17/release/vc_redist.x86.exe mfc140u.dll _x86 4',
                    'default /r/x86_64 /r/vc_redist.x64.exe.download https://aka.ms/vs/17/release/vc_redist.x64.exe'], names)
    out = drv('extract', T / 'good.exe', T / 'out-good', 'x86')
    check('the four mfc DLLs of the x86 package, and nothing else (not the x64 decoy, not the language DLL)',
          out == 'ok mfc140.dll mfc140u.dll mfcm140.dll mfcm140u.dll', out)
    check('... byte for byte as the package holds them', all((T / 'out-good' / n).read_bytes() == d for n, d in X86.items()))
    out = drv('extract', T / 'x64named.exe', T / 'out-x64named', 'x86')
    check('an x64 DLL under an x86 name is refused by name', out == 'error mfc140u.dll in the installer is not a 32-bit (x86) DLL.', out)
    out = drv('extract', T / 'unsigned.exe', T / 'out-unsigned', 'x86')
    check('a DLL without Microsoft\'s signature is refused', out == "error mfcm140.dll in the installer has lost Microsoft's signature.", out)
    out = drv('extract', T / 'missing.exe', T / 'out-missing', 'x86')
    check('a package without one of the four says which', out == "error Microsoft's installer did not hold mfcm140u.dll.", out)
    out = drv('extract', T / 'good.exe', T / 'out-as-x64', 'x64')
    check('the x64 package\'s code wants its own fourteen from the same installer (the 64-bit path as before)',
          out.startswith("error Microsoft's installer did not hold concrt140.dll, msvcp140.dll"), out)
    out = drv('install', T / 'good.exe', T / 'root', 'x86')
    check('installed into VCRuntime/x86 (its marker mfc140u.dll), then removed', out.splitlines() == ['ok version= folder=x86 installed=true', 'removed=true'], out)

    real = Path(os.environ.get('VCRUNTIME_X86_INSTALLER', '/tmp/vcr-x86.exe'))
    if real.is_file() and real.stat().st_size > 1_000_000:
        out = drv('extract', real, T / 'real', 'x86')
        check('Microsoft\'s package (%s, %d bytes): the four DLLs' % (real.name, real.stat().st_size),
              out == 'ok mfc140.dll mfc140u.dll mfcm140.dll mfcm140u.dll', out)
        pe = drv('pe', *[T / 'real' / n for n in sorted(X86)], 'x86').splitlines()
        check('... each i386, PE32, a DLL, Microsoft\'s signature attached, with a 14.x version',
              len(pe) == 4 and all(re.fullmatch(r'machine=0x14c pe32plus=0 dll=1 signed=1 version=14\.\d+\.\d+\.\d+', p) for p in pe), pe)
        cab = shutil.which('cabextract')
        if cab:
            ce = T / 'cabextract'
            ce.mkdir()
            subprocess.run([cab, '-q', '-d', str(ce / 'outer'), str(real)], capture_output=True)
            subprocess.run([cab, '-q', '-d', str(ce / 'inner'), str(ce / 'outer' / 'a11')], capture_output=True)
            check('... byte for byte what cabextract takes out of the package',
                  all((ce / 'inner' / (n + '_x86')).is_file() and (ce / 'inner' / (n + '_x86')).read_bytes() == (T / 'real' / n).read_bytes()
                      for n in X86))
        else:
            print('note: no cabextract here; the real package is not compared with it')
    else:
        print('note: no Microsoft package here (VCRUNTIME_X86_INSTALLER or /tmp/vcr-x86.exe); the synthetic one was checked')

# ---------------------------------------------------------------- 3: madeira_mfc32 on the host
bridge = (APP / 'WineProcessBridge.m').read_text()
fn = bridge[bridge.index('static void madeira_mfc32(NSFileManager *fm, NSString *syswow64, NSString *support)'):]
fn = fn[:fn.index('\n}\n') + 3]
OBJC = r'''
#import <Foundation/Foundation.h>
#include <unistd.h>
''' + fn + r'''
int main(int argc, char **argv) {
    @autoreleasepool {
        NSFileManager *fm = NSFileManager.defaultManager;
        NSString *t = [NSString stringWithUTF8String:argv[1]];
        NSString *sys = [t stringByAppendingPathComponent:@"syswow64"], *sup = [t stringByAppendingPathComponent:@"support"];
        NSString *step = [NSString stringWithUTF8String:argv[2]];
        if ([step isEqualToString:@"setup"]) {
            [fm createDirectoryAtPath:sys withIntermediateDirectories:YES attributes:nil error:nil];
            [fm createDirectoryAtPath:[sup stringByAppendingPathComponent:@"VCRuntime/x86"] withIntermediateDirectories:YES attributes:nil error:nil];
            for (NSString *n in @[ @"mfc140.dll", @"mfc140u.dll", @"mfcm140.dll", @"mfcm140u.dll" ])
                [[n dataUsingEncoding:NSUTF8StringEncoding] writeToFile:[sup stringByAppendingPathComponent:[@"VCRuntime/x86" stringByAppendingPathComponent:n]] atomically:YES];
            return 0;
        }
        madeira_mfc32(fm, sys, [step isEqualToString:@"none"] ? [t stringByAppendingPathComponent:@"nothing"] : sup);
        for (NSString *n in [[fm contentsOfDirectoryAtPath:sys error:nil] sortedArrayUsingSelector:@selector(compare:)]) {
            NSString *p = [sys stringByAppendingPathComponent:n];
            NSString *dest = [fm destinationOfSymbolicLinkAtPath:p error:nil];
            printf("%s -> %s\n", n.UTF8String, dest ? [dest hasPrefix:sup] ? "ours" : "elsewhere" : "file");
        }
    }
    return 0;
}
'''
if cc and sys.platform == 'darwin':
    (T / 'mfc32.m').write_text(OBJC)
    b = subprocess.run([cc, '-fobjc-arc', '-framework', 'Foundation', '-Wall', '-Werror', '-o', str(T / 'mfc32'), str(T / 'mfc32.m')],
                       capture_output=True, text=True)
    check('madeira_mfc32 compiles on the host with -Wall -Werror', b.returncode == 0, b.stderr)
    if b.returncode == 0:
        H = T / 'h'
        H.mkdir()

        def run(step, **env):
            e = {k: v for k, v in os.environ.items() if k not in ('MADEIRA_MFC32', 'MADEIRA_MFC')}
            e.update(env)
            r = subprocess.run([str(T / 'mfc32'), str(H), step], capture_output=True, text=True, env=e)
            return r.stdout.strip().splitlines(), r.stderr.strip()
        run('setup')
        out, err = run('go')
        check('unset: nothing linked and nothing said', out == [] and err == '', (out, err))
        out, err = run('go', MADEIRA_MFC32='1')
        check('MADEIRA_MFC32=1 with the download: the four DLLs linked into syswow64, one line',
              out == ['mfc140.dll -> ours', 'mfc140u.dll -> ours', 'mfcm140.dll -> ours', 'mfcm140u.dll -> ours']
              and err == '[WineProc] MADEIRA_MFC32=1: mfc140.dll mfc140u.dll mfcm140.dll mfcm140u.dll linked into syswow64 (the downloaded 32-bit MFC)',
              (out, err))
        out, err = run('go', MADEIRA_MFC32='1')
        check('... again: the same links (a stale one is renewed)', len(out) == 4 and all(o.endswith('ours') for o in out), out)
        os.symlink('/elsewhere/other.dll', H / 'syswow64' / 'other.dll')
        out, err = run('go')
        check('the next session that does not ask: its own links go, another link stays',
              out == ['other.dll -> elsewhere'] and err.count('removed from syswow64') == 4, (out, err))
        (H / 'syswow64' / 'mfc140u.dll').write_text('a game put its own file here')
        out, err = run('go', MADEIRA_MFC32='1')
        check('a real file of the same name is never replaced',
              'mfc140u.dll -> file' in out and 'mfc140u.dll in syswow64 is a file, not a link: left as it is' in err, (out, err))
        (H / 'syswow64' / 'mfc140u.dll').unlink()
        run('go')
        out, err = run('go', MADEIRA_MFC32='1', MADEIRA_MFC='0')
        check('MADEIRA_MFC=0 keeps the 32-bit MFC out too', out == ['other.dll -> elsewhere'] and 'MADEIRA_MFC=0: the 32-bit MFC stays out' in err, (out, err))
        out, err = run('none', MADEIRA_MFC32='1')
        check('not downloaded: nothing linked, and the line says where to get it',
              out == ['other.dll -> elsewhere'] and 'the 32-bit MFC is not downloaded (Settings > Visual C++): nothing linked' in err, (out, err))
        out, err = run('go', MADEIRA_MFC32='yes')
        check('only "1" asks', out == ['other.dll -> elsewhere'] and err == '', (out, err))
else:
    print('note: not on macOS; madeira_mfc32 is checked by its source only')
check('the session calls it after the Dock MFC, with syswow64 and Application Support, in every session',
      re.search(r'madeira_mfc_dock\(fm, \[prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"\][^;]*;\s*\n(?:\s*/\*.*?\*/\s*\n)?\s*'
                r'madeira_mfc32\(fm, \[prefix stringByAppendingPathComponent:@"drive_c/windows/syswow64"\],\s*'
                r'NSSearchPathForDirectoriesInDomains\(NSApplicationSupportDirectory, NSUserDomainMask, YES\)\.firstObject\);', bridge, re.S) is not None)

# ---------------------------------------------------------------- 4: the rest of the app, rules, lists, catalog, CI
dl = (APP / 'VCRuntimeDownload.swift').read_text()
check('VCRuntime.x86 is the 32-bit package\'s model, with no bundled copy',
      'static let x86 = VCRuntime(root: VCRuntimeInstaller.defaultRoot,' in dl and 'appendingPathComponent("i386-mfc", isDirectory: true), package: .x86)' in dl)
rl = (APP / 'BCDReadinessLive.swift').read_text()
rc = (APP / 'BCDReadinessCore.swift').read_text()
check('readiness: a 32-bit game is told to download the 32-bit MFC until it is there',
      'p.vcRuntimeX86 = VCRuntime.x86.isAvailable' in rl and 'Download the 32-bit MFC in Settings › Visual C++ first.' in rc
      and 'static let mfc32Names: Set<String> = ["mfc140.dll", "mfc140u.dll", "mfcm140.dll", "mfcm140u.dll"]' in rc
      and 'BCD does not carry the 32-bit one yet' not in rc)
st = (APP / 'BCDSettings.swift').read_text()
sl = (APP / 'BCDSettingsLive.swift').read_text()
ss = (APP / 'BCDSettingsSections.swift').read_text()
check('Settings › Visual C++ shows the 32-bit MFC under the 64-bit runtime, with its own controls',
      'vcRuntime32: BCDVCRuntimeLive(runtime: .x86, name: "32-bit MFC")' in sl and 'var vcRuntime32: (any BCDVCRuntimeBackend)? = nil' in st
      and 'case .vcruntime: Self.vcRuntime(model.backends.vcRuntime, model.backends.vcRuntime32)' in st
      and 'BCDMFC32Settings(backend: mfc32)' in st and 'BCDSettingsGroup(title: "MFC, 32-bit",' in ss
      and all('BCDSettingsID.control(.vcruntime, "mfc32-%s")' % n in ss for n in ('download', 'reinstall', 'remove', 'cancel')))
rules = (R / 'build/tools/autotune_rules.py').read_text()
for rid, fixture in (('mfc140-razer-chroma-gamemaker', 'what-lies-in-the-multiverse-mfc32-2026-10-29'),
                     ('mfc32-missing-program', 'muv-luv-photonflowers-mfc32-2026-10-23')):
    r = rules[rules.index('"id": "%s"' % rid):]
    r = r[:r.index('\n    },')]
    exp = (R / 'tests/fixtures/autotune' / (fixture + '.expect')).read_text()
    check('auto-tune %s gives env.MADEIRA_MFC32 = 1 now (fixture %s), not once the session had it linked' % (rid, fixture),
          '"lines": ["env.MADEIRA_MFC32 = 1"]' in r and '"noFix"' not in r and 's("MADEIRA_MFC32=1:", "linked into syswow64")' in r
          and '"next": [\n  "env.MADEIRA_MFC32 = 1"\n ]' in exp)
recs = (R / 'recommended/recommendations.json').read_text()
pol = (APP / 'GameRecommendationsList.swift').read_text()
check('the two games\' lists give it from build 625; a downloaded list may set it, 0 or 1',
      recs.count('{"line": "env.MADEIRA_MFC32 = 1", "minBuild": 625}') == 2 and '"MADEIRA_MFC32",' in pol
      and 'case "MADEIRA_MFC32":' in pol and 'MADEIRA_MFC32 is 0 or 1' in pol)
cat = (R / 'build/tools/gen-config-catalog.py').read_text()
check('the settings catalog knows the switch (bool, default 0)', re.search(r'"env\.MADEIRA_MFC32": \{[^}]*?"kind": "bool", "default": "0"', cat, re.S) is not None)
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check after downloading Microsoft\'s 32-bit package for it (never shipped)',
      'python3 tests/host/check-mfc32.py' in wf and 'https://aka.ms/vs/17/release/vc_redist.x86.exe' in wf and '/tmp/vcr-x86.exe' in wf
      and wf.index('/tmp/vcr-x86.exe') < wf.index('python3 tests/host/check-mfc32.py'))
shutil.rmtree(T, ignore_errors=True)
print('PASS: 32-bit MFC' if ok else 'FAIL: 32-bit MFC')
raise SystemExit(0 if ok else 1)
