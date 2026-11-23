#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Steam for Windows (experimental; branch steam-client, milestone M1 "bootstrap"; docs/STEAM_DESKTOP.md).

Valve's own Steam client in a Wine desktop, off unless Settings > BCD Dock > Steam for Windows turns it on. This check
compiles the production code (app/Madeira/SteamDesktop.swift with SteamRuntime.swift) and runs it on fixtures it makes
itself; it downloads nothing, holds no Valve file and runs no Windows program:

  manifest   Valve's client manifest (KeyValues text) read into packages, and the manifests that must be refused;
  install    the installer on a folder that stands in for Valve's host: steam.exe unpacked with the app's ZIP reader, the
             packages into the client's package folder, sizes and SHA-256 checked, a dropped connection taken up again,
             a cancelled install, wrong files, and BCD Dock's folder byte for byte as it was after every run;
  cdn        SteamDesktopCDN against a server on this Mac: the rest of a file by Range, redirects, wrong answers, cancel;
  rules      the folder, the arguments and the session's settings, with the entry's own lines winning;
  registry   the values written before a session, their backup, and BCD Dock's own check putting its values back;
  watch      the session's log lines, on two cuts of real Steam sessions (tests/fixtures/autotune) and the engine's lines;
  wiring     the session flag and the identity branch (the bridge's real block, compiled and run), the pool rule, the
             order of a start, the Steam connection held for the session, the library tile, the card, the catalog, CI.

    tests/host/check-steam-desktop.py
    tests/host/check-steam-desktop.py --manifest <file>     also reads a real manifest (never fetched here)
    tests/host/check-steam-desktop.py --real-host <folder>  also installs steam.exe from a folder that holds a real
                                                            manifest and its bootstrapper package (never fetched here)
"""
import hashlib
import http.server
import io
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
failed = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok or not detail else '  [' + str(detail)[-1500:] + ']'))
    if not ok:
        failed.append(name)


def option(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


def block(source, start_marker):
    """A Swift declaration's text, from its first line to the line that closes it at the same indent."""
    start = source.index(start_marker)
    line_start = source.rfind('\n', 0, start) + 1
    indent = source[line_start:start]
    end = source.index('\n' + indent + '}', start)
    return source[start:end]


core = (app / 'SteamDesktop.swift').read_text()
live = (app / 'BCDSteamDesktopLive.swift').read_text()
card = (app / 'BCDSteamDesktop.swift').read_text()
runtime = (app / 'SteamRuntime.swift').read_text()
content = (app / 'ContentView.swift').read_text()
bridge = (app / 'WineProcessBridge.m').read_text()
library = (app / 'Library.swift').read_text()
store = (app / 'BCDLiveStore.swift').read_text()
owned = (app / 'SteamOwnedLibrary.swift').read_text()
dock = (app / 'BCDDock.swift').read_text()
dock_live = (app / 'BCDDockLive.swift').read_text()
harness = Path(__file__).with_name('steam_desktop_checks.swift')

# ---------------------------------------------------------------- fixtures: nothing of Valve's


def sha1(data):
    return hashlib.sha1(data).hexdigest()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def stand_in_program(tail=b''):
    """The headers SteamDesktop.isWindowsProgram64 reads (MZ, PE, machine AMD64) and nothing that could run."""
    data = bytearray(0x200)
    data[0:2] = b'MZ'
    struct.pack_into('<I', data, 60, 0x80)
    data[0x80:0x84] = b'PE\x00\x00'
    struct.pack_into('<H', data, 0x84, 0x8664)
    return bytes(data) + tail


def archive(items):
    """A ZIP archive with a fixed date, so the same items always give the same bytes (and the same file name on the host)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in items:
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 13, 18, 5, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return out.getvalue()


class Host:
    """A folder that stands in for Valve's update host: a manifest in Valve's format and every file it names."""

    def __init__(self, folder, program, seed=7, version='1794536281', bootstrapper_items=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        rng = random.Random(seed)
        self.version = version
        self.program = program
        self.packages = []   # (name, zip name, zip bytes, vz name or None, vz bytes or None, bootstrapper)
        # The shape of Valve's win64 manifest of 2026-11-13: most packages have both forms, three have the archive alone,
        # and the bootstrapper has one variant per realm inside its block.
        for name, size, has_vz in [('tenfoot_images_all', 9000, True), ('strings_en_all', 2000, False),
                                   ('bins_win64', 60000, True), ('bins_cef_win64', 180000, True),
                                   ('steamui_websrc_movies_all', 12000, False), ('bins_hardware_win64', 7000, False)]:
            data = rng.randbytes(size)
            vz = rng.randbytes(size * 2 // 3) if has_vz else None
            self.packages.append((name, f'{name}.zip.{sha1(data)}', data,
                                  f'{name}.zip.vz.{sha1(vz)}_{len(vz)}' if vz else None, vz, False))
        boot = archive(bootstrapper_items if bootstrapper_items is not None else [('steam.exe', program)])
        boot_vz = rng.randbytes(len(boot) * 2 // 3 + 1)
        china = archive([('steam.exe', stand_in_program(b'china'))])
        self.boot = boot
        self.china = (f'steam_win64_steamchina.zip.{sha1(china)}', china)
        self.packages.append(('steam_win64', f'steam_win64_steamrow.zip.{sha1(boot)}', boot,
                              f'steam_win64_steamrow.zip.vz.{sha1(boot_vz)}_{len(boot_vz)}', boot_vz, True))
        self.write()

    def manifest(self):
        def fields(indent, zip_name, data, vz_name, vz, boot):
            t = '\t' * indent
            lines = [f'{t}"file"\t\t"{zip_name}"', f'{t}"size"\t\t"{len(data)}"', f'{t}"sha2"\t\t"{sha256(data)}"']
            if vz_name:
                lines += [f'{t}"zipvz"\t\t"{vz_name}"', f'{t}"sha2vz"\t\t"{sha256(vz)}"']
            if boot:
                lines.append(f'{t}"IsBootstrapperPackage"\t\t"1"')
            return lines
        out = ['"win64"', '{', f'\t"version"\t\t"{self.version}"', '\t"ostype"\t\t"win10"']
        for name, zip_name, data, vz_name, vz, boot in self.packages:
            out += [f'\t"{name}"', '\t{']
            if boot:
                out += ['\t\t"steamrow"', '\t\t{'] + fields(3, zip_name, data, vz_name, vz, True) + ['\t\t}']
                out += ['\t\t"steamchina"', '\t\t{'] + fields(3, self.china[0], self.china[1], None, None, True) + ['\t\t}']
                out += fields(2, zip_name.replace('_steamrow', ''), data, vz_name.replace('_steamrow', ''), vz, True)
            else:
                out += fields(2, zip_name, data, vz_name, vz, False)
            out.append('\t}')
        out += ['}', '"kvsign2"', '{', '\t"win64"\t\t"' + 'ab' * 64 + '"', '}', '"kvsignatures"', '{', '\t"win64"\t\t"' + 'cd' * 128 + '"', '}', '']
        return '\n'.join(out)

    def write(self):
        (self.folder / 'steam_client_win64').write_text(self.manifest())
        (self.folder / 'expected-steam.exe').write_bytes(self.program)
        for name, zip_name, data, vz_name, vz, boot in self.packages:
            (self.folder / zip_name).write_bytes(data)
            if vz_name:
                (self.folder / vz_name).write_bytes(vz)

    def download(self):
        """What the updater asks for: the compressed form where there is one."""
        return [(vz_name, vz) if vz_name else (zip_name, data) for _, zip_name, data, vz_name, vz, _ in self.packages]


def refused_manifests(good):
    """Manifests the reader must refuse, each one change away from the good one."""
    h = '0' * 40
    s = '0' * 64
    def one(name, block_text):
        return ('"win64"\n{\n\t"version"\t"1"\n\t"%s"\n\t{\n%s\n\t\t"IsBootstrapperPackage"\t"1"\n\t}\n}\n' % (name, block_text)).encode()
    ok = one('steam_win64', f'\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"')
    cases = {
        'empty': b'',
        'not-keyvalues': b'<html><body>404</body></html>',
        'binary': bytes(range(256)) * 4,
        'too-large': good.encode() + b'\n// ' + b'x' * (256 * 1024),
        'never-closes': good[:good.rindex('}', 0, good.index('"kvsign2"'))].encode(),
        'no-platform-block': good.replace('"win64"\n{', '"win32"\n{', 1).encode(),
        'platform-twice': (good + good).encode(),
        'no-version': good.replace('"version"', '"versi0n"', 1).encode(),
        'version-not-a-number': re.sub(r'"version"\t\t"\d+"', '"version"\t\t"1.2"', good, count=1).encode(),
        'unquoted-value': good.replace('"win10"', 'win10', 1).encode(),
        'file-with-a-folder': one('steam_win64', f'\t\t"file"\t"../steam_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"'),
        'file-of-another-package': one('steam_win64', f'\t\t"file"\t"bins_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"'),
        'file-without-its-hash': one('steam_win64', f'\t\t"file"\t"steam_win64.zip"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"'),
        'size-zero': one('steam_win64', f'\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"0"\n\t\t"sha2"\t"{s}"'),
        'size-not-a-number': one('steam_win64', f'\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"1e3"\n\t\t"sha2"\t"{s}"'),
        'size-too-large': one('steam_win64', f'\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"2147483648"\n\t\t"sha2"\t"{s}"'),
        'sha-too-short': one('steam_win64', f'\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s[:63]}"'),
        'sha-upper-case': one('steam_win64', f'\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{"A" * 64}"'),
        'sha-missing': one('steam_win64', f'\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"10"'),
        'compressed-without-its-size': one('steam_win64', f'\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"\n\t\t"zipvz"\t"steam_win64.zip.vz.{h}"\n\t\t"sha2vz"\t"{s}"'),
        'compressed-without-its-sha': one('steam_win64', f'\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"\n\t\t"zipvz"\t"steam_win64.zip.vz.{h}_5"'),
        'a-key-twice': one('steam_win64', f'\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"'),
        'package-name-with-a-dot': one('steam.win64', f'\t\t"file"\t"steam.win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"'),
        'no-package-with-steam-exe': ok.replace(b'\t\t"IsBootstrapperPackage"\t"1"\n', b''),
        'two-packages-with-steam-exe': ok.replace(b'\n}\n', (f'\n\t"steam2_win64"\n\t{{\n\t\t"file"\t"steam2_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"\n'
                                                               '\t\t"IsBootstrapperPackage"\t"1"\n\t}\n}\n').encode()),
        'two-packages-one-file': ok.replace(b'\n}\n', (f'\n\t"steam_win64"\n\t{{\n\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"\n\t}}\n}}\n').encode()),
        'realm-not-a-block': one('steam_win64', f'\t\t"steamrow"\t"x"\n\t\t"file"\t"steam_win64.zip.{h}"\n\t\t"size"\t"10"\n\t\t"sha2"\t"{s}"'),
    }
    return ok, cases


# ---------------------------------------------------------------- a server on this Mac for the update host's rules

class Served(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    folder = None
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def do_GET(self):
        name = self.path.lstrip('/')
        with Served.lock:
            with open(Served.folder / 'requests.log', 'a') as log:
                log.write(f'GET {self.path} {self.headers.get("Range", "-")}\n')
        port = self.server.server_address[1]
        for prefix in ('moved-', 'away-'):
            if name.startswith(prefix):
                self.send_response(302)
                self.send_header('Location', (f'http://localhost:{port}/' if prefix == 'away-' else '/') + name[len(prefix):])
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        kind = ''
        for prefix in ('norange-', 'badrange-', 'cut-', 'slow-', 'chunked-'):
            if name.startswith(prefix):
                kind, name = prefix, name[len(prefix):]
        path = Served.folder / name
        if '/' in name or not path.is_file() or name == 'requests.log':
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        data = path.read_bytes()
        start = 0
        ranged = re.fullmatch(r'bytes=(\d+)-', self.headers.get('Range', ''))
        if ranged and kind != 'norange-':
            start = int(ranged.group(1))
            if start >= len(data):
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{len(data)}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(206)
            shown = start + 1 if kind == 'badrange-' else start
            self.send_header('Content-Range', f'bytes {shown}-{len(data) - 1}/{len(data)}')
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Type', 'application/octet-stream')
        self.end_headers()
        try:
            if kind == 'cut-':
                self.wfile.write(body[:50000])
                self.wfile.flush()
                time.sleep(0.3)
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            if kind == 'chunked-':
                # 16 KB at a time, 10 ms apart: the client gets the file in several reads however loaded this Mac is (written whole,
                # a busy build machine read it in one, and the progress was told once: builds 706 and 707).
                for at in range(0, len(body), 16384):
                    self.wfile.write(body[at:at + 16384])
                    self.wfile.flush()
                    time.sleep(0.01)
                return
            if kind == 'slow-':
                # 64 KB at once, then 1 KB every 100 ms for 20 s at most: never the whole file, however loaded this Mac is.
                self.wfile.write(body[:65536])
                self.wfile.flush()
                for step in range(200):
                    time.sleep(0.1)
                    self.wfile.write(body[65536 + step * 1024:65536 + (step + 1) * 1024])
                    self.wfile.flush()
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True


# ---------------------------------------------------------------- build and run

with tempfile.TemporaryDirectory(prefix='bcd-steam-desktop-') as tmp:
    tmp = Path(tmp)
    (tmp / 'module.modulemap').write_text('module zlib [system] { header "/usr/include/zlib.h" export * link "z" }'
                                          if Path('/usr/include/zlib.h').exists() else '')
    # The two session checks, the prefix seeder and the Steam log SteamRuntime.swift calls are the app's, not this host's.
    (tmp / 'stubs.swift').write_text('func wine_process_is_running() -> Int32 { 0 }\nfunc wineserver_is_running() -> Int32 { 0 }\n'
                                     'func madeira_seed_prefix_if_needed(_ path: UnsafePointer<CChar>?) {}\n'
                                     'enum SteamLog { static func event(_ m: String) {} }\n')
    shutil.copy(harness, tmp / 'main.swift')
    exe = tmp / 'check'
    built = subprocess.run([SWIFTC, '-swift-version', '5', '-I', str(tmp), str(app / 'SteamRuntime.swift'), str(app / 'SteamDesktop.swift'),
                            str(app / 'SteamKeyValues.swift'), str(app / 'ExternalStorage.swift'),
                            str(tmp / 'stubs.swift'), str(tmp / 'main.swift'), '-o', str(exe)], capture_output=True, text=True)
    check('the production code and the harness compile on the host, without a warning in SteamDesktop.swift',
          built.returncode == 0 and 'SteamDesktop.swift' not in built.stderr, built.stderr)
    if built.returncode:
        print('FAILED: Steam for Windows (the harness did not build)')
        sys.exit(1)

    def run(*args, timeout=180):
        r = subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def stage(name, *args, timeout=180):
        code, out = run(*args, timeout=timeout)
        lines = [l for l in out.splitlines() if l.startswith(('ok   ', 'FAIL ', 'SKIP'))]
        for line in lines:
            print(line)
        bad = [l for l in lines if l.startswith('FAIL ')]
        check(f'{name}: {len(lines) - len(bad)} checks ran and passed', code == 0 and not bad and len(lines) > 0, out if code else '')
        return out

    # --- the manifest
    program = stand_in_program(b'BCD host check: a stand-in for steam.exe, not a program')
    host = Host(tmp / 'host', program)
    code, out = run('manifest', host.folder / 'steam_client_win64')
    expected = [f'version {host.version}']
    for name, zip_name, data, vz_name, vz, boot in host.packages:
        expected.append(f'package {name} {zip_name} {len(data)} {sha256(data)} '
                        + (f'{vz_name} {len(vz)} {sha256(vz)}' if vz_name else '- - -') + f' {1 if boot else 0}')
    expected.append(f'bootstrapper steam_win64 {host.packages[-1][1]}')
    expected.append(f'download {sum(len(d) for _, d in host.download())}')
    check('a manifest in Valve\'s format is read: version, every package with both forms, sizes and sums',
          code == 0 and out.splitlines() == expected, out)
    check('the package with steam.exe is its "steamrow" variant (not steamchina, not the block\'s own file line)',
          'steam_win64_steamrow.zip.' in expected[-2] and 'steamchina' not in out and 'steam_win64.zip.' not in out)
    ok_manifest, refused = refused_manifests(host.manifest())
    (tmp / 'ok-one.txt').write_bytes(ok_manifest)
    code, out = run('manifest', tmp / 'ok-one.txt')
    check('the smallest good manifest is read (so each refusal below is its one change)',
          code == 0 and out.startswith('version 1\npackage steam_win64 steam_win64.zip.' + '0' * 40 + ' 10 '), out)
    bad = tmp / 'refused'
    bad.mkdir()
    for name, data in refused.items():
        (bad / (name + '.txt')).write_bytes(data)
    stage('manifests that are refused', 'manifest-refuse', bad)
    if option('--manifest'):
        real = Path(option('--manifest')).read_text()
        code, out = run('manifest', option('--manifest'))
        files = re.findall(r'"file"\s+"([^"]+)"\s+"size"\s+"(\d+)"\s+"sha2"\s+"([0-9a-f]{64})"', real)
        listed = [l.split() for l in out.splitlines() if l.startswith('package ')]
        check(f'the real manifest given is read: {len(listed)} packages, version {out.splitlines()[0].split()[-1] if out else "?"}, '
              f'{out.splitlines()[-1] if out else ""} bytes for the updater',
              code == 0 and len(listed) >= 10 and all((l[2], l[3], l[4]) in files for l in listed)
              and sum(1 for l in listed if l[-1] == '1') == 1, out)

    # --- names, settings, registry, the log
    stage('rules', 'rules')
    stage('libraries', 'libraries')
    stage('dlc', 'dlc')
    stage('registry', 'registry')
    fixtures = root / 'tests/fixtures/autotune'
    stage('watch', 'watch', fixtures / 'steam-ui-jit-pool-exec-exhausted-77vqjk-2026-11-16.log',
          fixtures / 'steam-ui-not-windowed-game-76wzc7-2026-11-16.log')

    # --- the install
    work = tmp / 'work'
    work.mkdir()
    Host(work / 'host-not-a-program', b'MZ this is text, not a program' + bytes(600))
    Host(work / 'host-no-program', program, bootstrapper_items=[('readme.txt', b'no program here')])
    wrong = Host(work / 'host-bad-bootstrapper', program)
    flipped = bytearray(wrong.boot)
    flipped[len(flipped) // 2] ^= 1
    (wrong.folder / wrong.packages[-1][1]).write_bytes(bytes(flipped))
    short = Host(work / 'host-short-bootstrapper', program)
    (short.folder / short.packages[-1][1]).write_bytes(short.boot[:-9])
    (work / 'host-bad-manifest').mkdir()
    (work / 'host-bad-manifest/steam_client_win64').write_bytes(refused['no-version'])
    (work / 'host-empty').mkdir()
    extra = [option('--real-host')] if option('--real-host') else []
    out = stage('install', 'install', host.folder, work, *extra)
    check('the installer was run (not skipped) on this Mac', sys.platform != 'darwin' or 'SKIP' not in out, out)

    # --- the update host
    if sys.platform == 'darwin':
        served = tmp / 'served'
        served.mkdir()
        (served / 'file.bin').write_bytes(random.Random(11).randbytes(400_000))
        (served / 'small.txt').write_text(host.manifest())
        (served / 'requests.log').write_text('')
        Served.folder = served
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Served)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        (tmp / 'cdn-work').mkdir()
        try:
            stage('cdn', 'cdn', server.server_address[1], served, tmp / 'cdn-work', timeout=240)
        finally:
            server.shutdown()
    else:
        print('SKIP: the update host is checked on macOS (URLSession)')

# ---------------------------------------------------------------- wiring: what the app does with it

header = ('// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n'
          '// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n')
check("the new files carry BCD's licence header",
      all(text.startswith(header) for text in (core, live, card, harness.read_text())))

# The folder: the Dock's is never written to.
check("the client's folder is SteamRuntimeFiles.desktopRoot, the one BCD Dock's registry check knows",
      'static let windowsRoot = SteamRuntimeFiles.desktopRoot' in core
      and 'static let desktopRoot = "C:\\\\Program Files (x86)\\\\Steam Desktop"' in runtime
      and 'static let relativeRoot = "Program Files (x86)/Steam"' in runtime)
install = block(core, 'static func install(drive: URL')
check('every file the install writes is placed under the client\'s own folder, through the path guard',
      'try SteamRuntimeFiles.destination(SteamDesktop.relativeRoot + "/" + relative, under: drive)' in install
      and 'SteamRuntimeFiles.relativeRoot' not in install and 'windowsRoot' not in install
      and install.count('fm.moveItem(') == 2 and 'try fm.moveItem(at: part, to: target)' in install and 'try fm.moveItem(at: staged, to: target)' in install
      and 'guard !SteamDesktop.isDockPath(relative: SteamDesktop.relativeRoot) else { throw Failure.conflict("folder") }' in install)
check('steam.exe goes in last, after the packages, with no suspension in between',
      install.index('try fm.moveItem(at: part, to: target)') < install.index('progress(Progress(step: .installing))') < install.index('try fm.moveItem(at: staged, to: target)')
      and 'await' not in install[install.index('progress(Progress(step: .installing))'):])
check('sizes and SHA-256 come from the manifest, for the package with steam.exe and for every other package',
      'guard hash(archive) == boot.sha256 else { throw Failure.verification(boot.name) }' in install
      and 'guard SteamRuntimeFiles.sha256(ofFile: part) == file.sha256 else {' in install
      and 'if size(target) == file.bytes, SteamRuntimeFiles.sha256(ofFile: target) == file.sha256 {' in install
      and 'try SteamRuntimeFiles.unpack(archive)' in install and 'SteamDesktop.isWindowsProgram64(programData)' in install)
check('the app asks Valve\'s host only: no other policy is made outside the host check',
      'SteamDesktopCDN()' in live and 'Policy(' not in live and 'URLSession' not in live
      and 'static let valve = Policy(scheme: "https", host: "client-update.akamai.steamstatic.com", port: nil, base: SteamRuntimeFiles.origin)' in core
      and 'static let origin = "https://client-update.akamai.steamstatic.com/"' in runtime
      and 'completionHandler(policy.allows(request.url) ? request : nil)' in core)
tracked = subprocess.run(['git', '-C', str(root), 'ls-files'], capture_output=True, text=True).stdout.splitlines()
if tracked:
    valve_names = re.compile(r'(^|/)(steam\.exe|steamwebhelper\.exe|steamservice\.exe|steamclient(64)?\.dll|libcef\.dll|steam_client_win(32|64))$', re.I)
    check('no Valve file is in the repository', not [f for f in tracked if valve_names.search(f)])

# The session flag and the identity.
start = bridge.index('        const char *dock_session = getenv("MADEIRA_DOCK_SESSION");')
end = bridge.index('        unsetenv("MADEIRA_STEAM_APPPATH");', start) + len('        unsetenv("MADEIRA_STEAM_APPPATH");')
identity = bridge[start:end]
check('the bridge reads the session flag beside the Dock\'s and tests it after the Dock branch, before a direct start and the fixed identity',
      'const char *desktop_session = getenv("MADEIRA_STEAM_DESKTOP_SESSION");' in identity
      and identity.index("dock_session[0] == '1'") < identity.index("desktop_session[0] == '1'") < identity.index('direct_app && direct_app[0]')
      < identity.index('setenv("SteamAppId",  "356400", 1);'))
c_harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define dprintf(fd, ...) printf(__VA_ARGS__)
static void show(void) {
    const char *names[] = {"SteamAppPath", "SteamGameId", "SteamAppId"};
    for (int i = 0; i < 3; i++) { const char *v = getenv(names[i]); printf("%s=%s\n", names[i], v ? v : "(unset)"); }
}
int main(void) {
    setenv("SteamAppId", "999", 1);
BLOCK
    show();
    return 0;
}
'''.replace('BLOCK', identity)
with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / 'h.c'
    src.write_text(c_harness)
    exe = Path(tmp) / 'h'
    b = subprocess.run(['cc', '-w', '-o', str(exe), str(src)], capture_output=True, text=True)
    check("the bridge's real identity block compiles in a harness", b.returncode == 0, b.stderr)
    if b.returncode == 0:
        def identity_of(env):
            e = {k: v for k, v in os.environ.items() if not k.startswith(('MADEIRA_', 'Steam'))}
            e.update(env)
            return subprocess.run([str(exe)], capture_output=True, text=True, env=e).stdout
        fixed = 'SteamAppPath=C:\\Program Files\\Thumper\nSteamGameId=356400\nSteamAppId=356400\n'
        none = 'SteamAppPath=(unset)\nSteamGameId=(unset)\nSteamAppId=(unset)\n'
        session = identity_of({'MADEIRA_STEAM_DESKTOP_SESSION': '1'})
        check('a Steam for Windows session publishes no Steam game identity (even one set earlier), and says so',
              session.endswith(none) and '[steam-env] Steam for Windows session: no fixed Steam game identity published' in session, session)
        check('every other launch is as before: unset and 0 give the fixed identity, a Dock session none with its own words',
              identity_of({}).endswith(fixed) and identity_of({'MADEIRA_STEAM_DESKTOP_SESSION': '0'}).endswith(fixed)
              and identity_of({'MADEIRA_STEAM_DESKTOP_SESSION': ''}).endswith(fixed)
              and 'fixed Steam identity published (app 356400)' in identity_of({})
              and identity_of({'MADEIRA_DOCK_SESSION': '1'}).endswith(none)
              and 'BCD Dock session: no fixed Steam game identity published' in identity_of({'MADEIRA_DOCK_SESSION': '1', 'MADEIRA_STEAM_DESKTOP_SESSION': '1'}))
        check("a direct Steam start keeps the game's own identity",
              identity_of({'MADEIRA_STEAM_APPID': '271590', 'MADEIRA_STEAM_APPPATH': 'C:\\Games\\GTAV'})
              .endswith('SteamAppPath=C:\\Games\\GTAV\nSteamGameId=271590\nSteamAppId=271590\n'))
sequence = block(content, 'private func runWineFullSequence(profile: LibraryEntry? = nil)')
check('the launch knows a Steam for Windows session by its entry, on the main thread, and a Dock start is never one',
      'let steamDesktopSession = profile.map(BCDSteamDesktopLive.isSession) ?? false' in sequence
      and 'entry.desktop != true && entry.steamAppID == nil && SteamDesktop.isClient(windowsPath: entry.launchWindowsPath)' in block(live, 'nonisolated static func isSession('))
flag = sequence[sequence.index('if steamDesktopSession && !dockLaunch.dock {'):]
check('the session flag is set for that session only and cleared for every other launch',
      flag.startswith('if steamDesktopSession && !dockLaunch.dock {\n                setenv(SteamDesktop.sessionVariable, "1", 1)\n'
                      '            } else {\n                unsetenv(SteamDesktop.sessionVariable)\n            }')
      and sequence.count('SteamDesktop.sessionVariable') == 2
      and 'static let sessionVariable = "MADEIRA_STEAM_DESKTOP_SESSION"' in core)

# The session's settings: after the entry's own file is chosen, before the pool is asked for; the pool line counts by itself.
check("the session's settings are applied after the entry's environment and before the JIT pool request",
      sequence.index('profile.applyEnvironment()') < sequence.index('if steamDesktopSession { for line in BCDSteamDesktopLive.applySessionSettings() { logStore.log(line) } }')
      < sequence.index('MadeiraConfig.gameValue("pool-above-window")') < sequence.index('StikJITHelper.allocatePool(poolSize: poolSizeMB * 1024 * 1024)'))
check("in that session the pool line of the session's file counts by itself; for every other launch the rule is the old one",
      'let gamePool = (poolAbove || steamDesktopSession) ? MadeiraConfig.gameValue("pool") : nil' in sequence
      and 'let poolAbove = ["1", "on", "true", "yes"].contains((MadeiraConfig.gameValue("pool-above-window") ?? MadeiraConfig.get("pool-above-window") ?? "")' in sequence
      and sequence.count('steamDesktopSession') == 5 and content.count('steamDesktopSession') == 5)
settings = block(live, 'nonisolated static func applySessionSettings()')
check("the session's file is BCD's block above the text of the file the launch chose, exported for the session; nothing goes to bcd.cfg or the entry",
      'let chosen = getenv("MADEIRA_CFG_GAME").map { String(cString: $0) } ?? ""' in settings
      and 'SteamDesktopProfile.sessionFile(own: own)' in settings and 'setenv("MADEIRA_CFG_GAME", url.path, 1)' in settings
      and 'MadeiraConfig.set(' not in live and '.config = ' not in live and 'entry.config' not in live)
check('the three pool keys and Social Club\'s layout are read from that file by the app, as from any game\'s file',
      all(f'MadeiraConfig.gameValue("{key}") ?? MadeiraConfig.get("{key}")' in (app / 'StikJITHelper.swift').read_text()
          for key in ('pool-split', 'pool-low', 'env.MADEIRA_SC_PA_POOLS')))
cfg = (root / 'build/madeira_cfg.h').read_text()
check("and the engine reads the game's file after bcd.cfg, the last line of a key winning",
      'game = getenv("MADEIRA_CFG_GAME");' in cfg and 'found = 1;   /* keep going: the last occurrence wins */' in cfg)
virtual = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
check('each key of the settings is one the engine reads: swap, avail-phys and the four environment names',
      all(needle in virtual for needle in ('madeira_cfg_int( "swap-mode", 1 )', 'madeira_cfg_bool( "swap-images", 0 )',
                                           'madeira_cfg_bool( "avail-phys", 0 )', 'madeira_cfg_int( "avail-phys-cef-mb", 0 )',
                                           'getenv( "MADEIRA_X64_IMAGE_NOCOPY" )', 'getenv( "MADEIRA_SC_PA_POOLS" )', 'getenv( "MADEIRA_CEF_POOL_IMAGES" )',
                                           '"steamwebhelper.exe" };'))
      and 'madeira_cfg_int("swap-mb", 0)' in bridge
      and 'getenv( "MADEIRA_BG_QOS_PROCS" )' in (root / 'build/ntdll-unix/server_ios.c').read_text())
# Build 690 (2026-11-21 21:57): Breakneck, a 32-bit game Steam started three minutes in, was refused its 4 GB window with 6797 MB of
# the map free ("plus MADEIRA_WOW_MIN_FREE_GB=8 left over"), and SteamService.exe the same way in 689. The session's 2 GB floor is the EA
# app's line, which runs the same 64-bit Chromium layout (MADEIRA_SC_PA_POOLS = 2) with 32-bit children.
check("the session's 2 GB floor for a 32-bit window is the engine's rule, read at every window, and the EA app's line",
      'min_free = (ULONG_PTR)ios_wow_env_ulong( "MADEIRA_WOW_MIN_FREE_GB", 8 ) << 30;' in block(virtual, 'static int ios_wow_map_allows_window(void)')
      and 'if (free_va < IOS_WOW_WINDOW_SIZE + min_free)' in virtual
      and '"env.MADEIRA_WOW_MIN_FREE_GB = 2",' in (app / 'BCDLaunchers.swift').read_text()
      and '"env.MADEIRA_SC_PA_POOLS = 2"' in (app / 'BCDLaunchers.swift').read_text())
# Build 691 (22:32, two runs): env.MADEIRA_WOW_PLACEHOLDER_SLOTS = 1 and env.MADEIRA_WOW_HOLD_FOR without the engine's cap. Layout 2's
# floor is the held slot's base, so every furniture view first failed a full scan below it, and the band above reached the top of the
# map: steam.exe's top-down RWX page landed at 0x7bffff0000, the web helper's fourth pool could not be reserved, the helper stopped
# on a breakpoint and Steam restarted it every 10 s. Build 692 (23:05) with the floor alone: no 4 GB-aligned slot was free three
# minutes in. So the held window comes back only with env.MADEIRA_WOW_HIGH_SIDE_CAP = 1 (virtual_ios.c, check-lazy-windows), and
# never as the all-slots form.
profile_lines = core[core.index('    static let lines: [String] = ['):core.index('    static let fileName = "bcd-steam-desktop.cfg"')]
check("one window held for the client's games, only with the engine's cap; never every slot",
      '"env.MADEIRA_WOW_MIN_FREE_GB = 2",' in profile_lines
      and '"env.MADEIRA_WOW_PLACEHOLDER_SLOTS = 1",' in profile_lines
      and '"env.MADEIRA_WOW_HOLD_FOR = " + SteamDesktop.windowsRoot + "\\\\steamapps\\\\common\\\\",' in profile_lines
      and '"env.MADEIRA_WOW_HIGH_SIDE_CAP = 1",' in profile_lines
      and 'MADEIRA_WOW_PLACEHOLDERS' not in profile_lines and 'MADEIRA_WOW_HOLD_FOR_GAME' not in profile_lines)
wow_start = block(virtual, 'static void ios_wow_session_start(void)')
check("the held window's lines are the engine's: one placeholder for a 64-bit main image, held for the folder, and the cap, "
      "read at the session start",
      'unsigned long slots = ios_wow_env_ulong( "MADEIRA_WOW_PLACEHOLDER_SLOTS", 0 );' in wow_start
      and 'const char *folder = getenv( "MADEIRA_WOW_HOLD_FOR" );' in wow_start
      and 'ios_wow_high_side_cap = ios_wow_env_ulong( "MADEIRA_WOW_HIGH_SIDE_CAP", 0 ) == 1;' in wow_start
      and wow_start.index('ios_wow_high_side_cap = ') < wow_start.index('else return;')
      and 'hi_end = ios_wow_high_side_end( end, hi_end, ceiling_relaxable );' in virtual
      and 'if (ios_wow_high_side_first( excl_start, excl_end, wow_hi_start, wow_hi_end, view_size, ceiling_relaxable ))' in virtual)
gdi = root / 'wine/dlls/win32u/gdiobj.c'
if gdi.exists():
    check('the GDI table is a section when the line says so, whatever the session started as (wine win32u)',
          'const char *env = getenv( "MADEIRA_GDI_SHARED_SECTION" );\n\n    if (env && env[0]) return env[0] != \'0\';' in gdi.read_text())
else:
    print('SKIP: wine not checked out; the GDI section rule in win32u was not read')
check("BCD Dock's sessions ask for the same section, for the same reason (a 64-bit start, 32-bit programs later)",
      'setenv("MADEIRA_GDI_SHARED_SECTION", "1", 0)' in content or 'setenv("MADEIRA_GDI_SHARED_SECTION", "1", 1)' in content)
process = (root / 'build/ntdll-unix/process_ios.c').read_text()
check("steamwebhelper.exe's own rule (single process, --type= children refused) runs before the list's rewrite, which then adds no second switch",
      process.index('[proc-gate] REFUSING steamwebhelper --type= child') < process.index('const char *list = getenv( "MADEIRA_CEF_POOL_IMAGES" );')
      and 'if (sc_switch_end( cl, cl_len, "--single-process" ) < 0)' in process
      and 'if (sc_switch_end( cl, cl_len, "--js-flags=" ) >= 0) *how |= SC_OWN_JS_FLAGS;' in process)

# A start: the account first, then the registry, then the session; the hold ends with the session.
begin = block(content, 'private func startLibraryEntry(_ entry: LibraryEntry)')
check('a start goes through preLaunch last, after the entry was validated and right before the launch is set up',
      begin.index('try entry.validate()') < begin.index('guard BCDSteamDesktopLive.preLaunch(&entry, retry: { startLibraryEntry($0) }) else { return }')
      < begin.index('entry.configureLaunch()') < begin.index('library.begin(entry') < begin.index('runWineFullSequence(profile: entry)')
      and 'return' not in begin[begin.index('guard BCDSteamDesktopLive.preLaunch('):].split('\n', 1)[1])
pre = block(live, 'static func preLaunch(_ entry: inout LibraryEntry')
check("preLaunch leaves every other entry alone, and for the client waits for BCD's own Steam connection, then writes the registry, then starts",
      pre.index('guard isSession(entry) else { return true }') < pre.index('await SteamOwnedLibrary.shared.prepareDesktopClient()')
      < pre.index('SteamDesktopRegistry.seed(prefix: prefix') < pre.index('prepared = start.id') < pre.index('retry(start)')
      and 'SteamOwnedLibrary.shared.desktopClientEnded()' in pre and 'sessionRunning: false' in pre)
check('a start that does not become a session gives the account back; the log never carries the arguments as typed',
      pre.index('retry(start)') < pre.index('if LibraryModel.shared.current == nil {') < pre.rindex('SteamOwnedLibrary.shared.desktopClientEnded()')
      and 'arguments=\\(SteamDesktop.argumentsForLog(start.arguments))' in pre and 'start.arguments)\\"' not in pre)
check('nothing of BCD\'s sign-in is handed to the client', 'credentialsForDock' not in live and 'writeHandoff' not in live and 'refreshToken' not in live
      and 'Keychain' not in live and 'credentialsForDock' not in core)
gate = block(owned, '@MainActor final class SteamConnectionGate')
check("the connection gate has a hold of its own for the session, and reopens only when neither it nor Dock holds the account",
      'private(set) var heldForDesktopClient = false' in gate and 'func holdForDesktopClient() async {\n        heldForDesktopClient = true\n        await close().value' in gate
      and 'func releaseDesktopClient() { heldForDesktopClient = false }' in gate
      and 'guard !open, !sessionRunning, !heldForDock, !heldForDesktopClient else { return false }' in gate)
prepare = block(owned, 'func prepareDesktopClient() async')
check('prepareDesktopClient pauses downloads, waits for the running one and for the connection to close, each wait bounded',
      prepare.index('sessionChanged(active: true)') < prepare.index('{ await running.value }') < prepare.index('{ await gate.holdForDesktopClient() }')
      and prepare.count('Self.dockStepLimit') == 4 and 'connection closed for Steam for Windows' in prepare)
ended = block(owned, 'func desktopClientEnded()')
check('the hold ends with the session, and the connection comes back through the gate',
      'guard gate.heldForDesktopClient else { return }' in ended and 'gate.releaseDesktopClient()' in ended and 'reconcileSession()' in ended
      and 'SteamOwnedLibrary.shared.desktopClientEnded()' in block(live, 'nonisolated static func sessionEnded()')
      and 'BCDSteamDesktopLive.sessionEnded()' in block(library, 'private func finish()')
      and 'BCDSteamDesktopLive.sessionBegan(entry)' in block(library, 'func begin(_ entry: LibraryEntry'))
check('every session still pauses downloads and closes the connection, as before',
      'SteamOwnedLibrary.shared.sessionChanged(active: true)' in sequence)
# BCD Dock afterwards: its start puts the values back (the registry stage above ran it), and so does its client download or switch.
dock_setup = block(runtime, 'func prepare(prefix: URL, replacing: Bool = false')
check("BCD Dock's client setup puts a value that names Steam for Windows' folder back too, instead of stopping on it as a conflict",
      'let stale: (String) -> Bool = { value in\n            if SteamRuntimeFiles.namesDesktopClient(value) { return true }\n' in dock_setup
      and 'var reclaimed = 0' in block(runtime, 'static func repairRegistry(prefix: URL') and 'guard namesDesktopClient(value) else { return false }' in runtime)

# BCD Dock's games in the client's library (2026-11-22, owner: "BCD Dock ile oyun paylaşımını aç xplane 12 zaten yüklü onu görsün").
pre_block = block(live, 'static func preLaunch(_ entry: inout LibraryEntry')
share_block = block(live, 'nonisolated static func shareLibraries() -> String')
backup_block = block(live, 'nonisolated private static func backupDockLibraryOnce(')
check("before a session, after the registry and while none runs, BCD Dock's libraries go into the client's own list",
      pre_block.index('let seeded = SteamDesktopRegistry.seed(prefix: prefix') < pre_block.index('Self.shareLibraries()')
      < pre_block.index('prepared = start.id') < pre_block.index('retry(start)')
      and 'let list = drive.appendingPathComponent(SteamDesktopLibraries.listRelative)' in share_block
      and 'try SteamDesktopLibraries.list(existing, share: share, drives: drives)' in share_block
      and 'try next.write(to: list, options: .atomic)' in share_block and share_block.count('.write(') == 1)
check("BCD writes nothing into BCD Dock's folders: it reads them, and copies their small files once into the client's own folder",
      '.write(' not in backup_block and 'copyItem(at: folder.appendingPathComponent(name), to: partial.appendingPathComponent(name))' in backup_block
      and 'drive.appendingPathComponent(SteamDesktopLibraries.backupFolder, isDirectory: true)' in backup_block
      and 'guard !fm.fileExists(atPath: backup.path) else { return }' in backup_block
      and 'try fm.moveItem(at: partial, to: backup)' in backup_block
      and 'removeItem' not in share_block)
check("the card has the switch, on unless cleared, and it only changes what the next start writes",
      'BCDSectionKit.SwitchRow(title: BCDSteamDesktopWords.shareTitle, detail: BCDSteamDesktopWords.shareDetail,' in card
      and 'isOn: state.sharesDock, id: Self.prefix + "share-dock") { backend.setSharesDock($0) }' in card
      and 'static var sharesDock: Bool { UserDefaults.standard.object(forKey: shareKey) as? Bool ?? true }' in core
      and 'UserDefaults.standard.set(on, forKey: SteamDesktopLibraries.shareKey)' in live
      and 'func setSharesDock(_ on: Bool) { state.sharesDock = on }' in (root / 'app/BCDPreview/BCDPreviewDock.swift').read_text())

# The entry and the tile.
entry = block(live, 'static func entry(stored: LibraryEntry?)')
check("BCD's entry starts the client's steam.exe in a Wine desktop (explorer first), with the default arguments when it is new",
      'entry.relativePath = SteamDesktop.relativeProgram' in entry and 'entry.launchMode = "desktop"' in entry
      and 'entry.arguments = SteamDesktop.defaultArguments' in entry and 'entry.id = SteamDesktop.entryID' in entry
      and 'return ("explorer.exe", "/desktop=shell,\\(resolution) " + withExtra(batch ? "cmd /c \\(cmdTarget(path))" : quoted(path)))' in library)
check('the tile shows only while the switch is on, and the switch or an install rebuilds the library',
      'nonisolated static func hidesEntry(_ id: UUID) -> Bool { id == SteamDesktop.entryID && !SteamDesktop.isEnabled }' in live
      and 'if BCDSteamDesktopLive.hidesEntry(entry.id) { continue }' in block(store, 'private func rebuild()')
      and 'NotificationCenter.default.publisher(for: SteamDesktop.didChange)' in store
      and live.count('NotificationCenter.default.post(name: SteamDesktop.didChange, object: nil)') == 2
      and 'static var isEnabled: Bool { UserDefaults.standard.bool(forKey: enabledKey) }' in core)
made = block(live, 'private func ensureEntry()')
check("the tile's entry is made once, only for an installed client, and auto-tune is left as for any entry",
      'guard Self.installed else { return }' in made and 'guard let stored = Self.storedEntry() else {' in made
      and 'LibraryModel.shared.save(Self.entry(stored: nil))' in made
      and 'AutoTune' not in live and 'AutoTune' not in core)
# Build 689 (2026-11-21 21:01, iPhone18,2): steam.exe asked for \pipe\svcctl 21 times and quit with -1 after 9 s; the tile started no
# Windows services. With the entry's "Start Windows services first" switched on (21:05) it updated itself and signed in.
pre = block(live, 'static func preLaunch(_ entry: inout LibraryEntry')
check('every Steam for Windows start runs the services batch first (services.exe, then steam.exe), on both passes of its start',
      'entry.startServices = true' in entry
      and 'entry.startServices = true' in pre and pre.index('entry.startServices = true') < pre.index('if prepared == entry.id {')
      and pre.index('guard isSession(entry) else { return true }') < pre.index('entry.startServices = true')
      and 'startServices == true || wantsRockstarServices' in library and 'if servicesScript != nil { path = servicesScriptPath' in library)
check("a tile made by build 689 gets the services switch once, and the start line says what it does",
      'guard stored.startServices != true || stored.launchMode != "desktop" || stored.relativePath != SteamDesktop.relativeProgram else { return }' in made
      and 'LibraryModel.shared.save(Self.entry(stored: stored))' in made
      and '"services=\\(start.startServices == true ? 1 : 0) "' in pre)
check('the sign-in milestone does not need the English title: a titled Steam window before the main window, the known others left out',
      'else if !milestones.contains("main-window"), Self.namesOwnWindow(line) { name = "sign-in-window" }' in core
      and 'static let otherWindowTitles: Set<String> = ["", "Steam", "SteamWebhelper", "SDL_app", "Menu", "Steam Big Picture Mode"]' in core
      and '!title.hasPrefix("notificationtoasts_")' in core)
check('a download asks for the bytes as they are on the host',
      'request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")' in core)
check('an install is refused while a session runs or with too little room, and Open respects one session a run',
      'guard !Self.sessionRunning else { state.error = SteamDesktopInstaller.Failure.activeSession.errorDescription; return }' in live
      and 'free < Self.wantedFreeBytes' in live and 'LibraryModel.sessionsThisRun > 0, MadeiraConfig.flag("MADEIRA_ONE_SESSION_PER_RUN")' in live)

# The watch reads only.
watch = block(live, 'final class SteamDesktopSessionWatch')
check('the watch reads the process table and the log and writes lines; it asks no window anything and changes nothing',
      'madeira_child_report(' in watch and 'madeira_child_ends_since(' in watch and 'LogStore.shared.logFileURL' in watch
      and all(word not in watch for word in ('winios_window_census', 'winios_windows_', 'setenv(', 'wine_session', 'NtTerminate', 'kill(')))

# The card.
check('the card is under Settings > BCD Dock, through a hook that is empty for a copy without it',
      'var extraCards: AnyView { get }' in dock and 'extension BCDDockBackend {\n    var extraCards: AnyView { AnyView(EmptyView()) }\n}' in dock
      and 'backend.extraCards' in block(dock, 'var body: some View')
      and 'var extraCards: AnyView { AnyView(BCDSteamDesktopCard(backend: BCDSteamDesktopLive.shared)) }' in dock_live)
check('with the switch off the card is the switch alone',
      card.index('BCDSectionKit.SwitchRow(title: BCDSteamDesktopWords.switchTitle') < card.index('if state.on {') < card.index('backend.install()')
      and 'static let title = "Steam for Windows (experimental)"' in card and 'static let title = "Steam for Windows (experimental)"' in core)
engine_words = ['LibraryModel', 'MadeiraConfig', 'LogStore', 'SteamDesktop.', 'SteamRuntimeFiles', 'MadeiraDock', 'SteamOwnedLibrary', 'wine_']
check('the card names no engine type, and its engine side stays out of the preview build',
      not [w for w in engine_words if w in card]
      and 'ENGINE_BOUND |= {"BCDSteamDesktopLive.swift"}' in (root / 'tools/ui-preview.py').read_text()
      and 'BCDSteamDesktopCard(backend: steamDesktop)' in (root / 'app/BCDPreview/BCDPreviewDock.swift').read_text())
project = (app.parent / 'Madeira.xcodeproj/project.pbxproj').read_text()
check('the three files are in the Xcode project',
      all(project.count(f'/* {name} in Sources */') == 2 and project.count(f'/* {name} */') >= 2
          for name in ('SteamDesktop.swift', 'BCDSteamDesktop.swift', 'BCDSteamDesktopLive.swift')))

check("the card says it is not for playing games, under the switch, whether it is on or off (owner, 2026-11-22)",
      'static let notForPlaying = "Not for playing games.' in card and 'Play your games through BCD Dock."' in card
      and card.index('BCDSectionKit.Note(text: BCDSteamDesktopWords.notForPlaying') < card.index('            if state.on {\n')
      and card.index('BCDSectionKit.SwitchRow(title: BCDSteamDesktopWords.switchTitle') < card.index('BCDSteamDesktopWords.notForPlaying, symbol'))

# BCD Dock's DLC choices in the client (2026-11-22).
dlc_service = (app / 'BCDSteamDLC' / 'BCDSteamDLCService.swift').read_text()
pre_dlc = block(live, 'static func preLaunch(_ entry: inout LibraryEntry')
check("before a session the owned DLC of BCD Dock's games are asked while BCD's own connection may still connect, then written after the share",
      pre_dlc.index('let dockGames = SteamDesktopLibraries.sharesDock')
      < pre_dlc.index('owned = await SteamOwnedLibrary.shared.dlc.ownedWithContent(dockGames.map(\\.id), seconds: SteamDesktopDLC.lookupSeconds)')
      < pre_dlc.index('await SteamOwnedLibrary.shared.prepareDesktopClient()')
      < pre_dlc.index('Self.shareLibraries()') < pre_dlc.index('Self.keepDockDLC(records, owned: owned, wanted: wanted, seconds: dlcSeconds)')
      < pre_dlc.index('sessionDLC = kept.games') < pre_dlc.index('retry(start)')
      and 'MadeiraDock.games(drive: drive).filter { $0.installed && SteamInstallPaths.isManaged(library: $0.library) }' in pre_dlc
      and 'choices.wanted(game.id).map { (game.id, $0) }' in pre_dlc
      and pre_dlc.count('sessionDLC = [:]') == 1)
ended = block(live, 'nonisolated static func sessionEnded()')
check("the other way: at the session's end BCD's DLC choice takes what was done in the client, before BCD's connection comes back",
      ended.index('if let line = adoptSessionDLC() { LogStore.shared.log(line) }') < ended.index('SteamOwnedLibrary.shared.desktopClientEnded()')
      and 'SteamDesktopDLC.adopted(wanted: before, installedBefore: game.installed, written: game.written,' in live
      and 'choices.set(id, wanted: after)' in live and 'sessionDLC = [:]\n        guard !games.isEmpty else { return nil }' in live)
check("the other way for games: BCD Dock's own list gets the client's library before each session and when the switch moves",
      'return head + " list=written " + writeDockList(share: share)' in live and 'writeDockList(share: share)' in block(live, 'nonisolated static func shareLibraries()')
      and 'if !Self.sessionRunning, Self.installed {' in block(live, 'func setSharesDock(') and 'Self.writeDockList(share: on)' in live
      and 'SteamDesktopLibraries.dockList(existing, share: share)' in block(live, 'nonisolated static func writeDockList('))
check("the share switch says both ways", 'static let shareTitle = "Share games with BCD Dock"' in card
      and 'Games you install in Steam for Windows show in your library and start through BCD Dock.' in card
      and "Steam downloads no DLC you didn't choose in BCD, and BCD keeps the DLC you add or remove in" in card)
keep = block(live, 'nonisolated static func keepDockDLC(')
check("the records get only the DisabledDLC value: through SteamDesktopDLC.record, written atomically, one log line",
      'SteamDesktopDLC.record(data, disabled: want)' in keep and 'next.write(to: record.url, options: .atomic)' in keep
      and keep.count('.write(') == 1 and '[steam-desktop] dlc: games=' in keep and '(the client may download their DLC)' in keep)
check("the lookups use this run's reports, ask nothing when the account may not connect, and start none after the time is up",
      'guard !missing.isEmpty, canConnect() else { return out }' in dlc_service and 'guard Date() < deadline else { break }' in dlc_service
      and 'if let report = reports[id] { out[id] = Set(report.owned.map(\\.appID)) }' in dlc_service)

# The catalog, the docs, CI.
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
catalog = (app / 'ConfigCatalog.generated.swift').read_text()
check('the catalog explains the session flag as one BCD sets itself', '"env.MADEIRA_STEAM_DESKTOP_SESSION":' in gen
      and 'key: "env.MADEIRA_STEAM_DESKTOP_SESSION"' in catalog)
doc = root / 'docs/STEAM_DESKTOP.md'
check('docs/STEAM_DESKTOP.md describes it', doc.exists() and all(word in doc.read_text() for word in
      ('Steam for Windows', 'Steam Desktop', 'steam_client_win64', 'MADEIRA_STEAM_DESKTOP_SESSION', '[steam-desktop]', 'pool = 1152')))
check('CI runs this check', 'python3 tests/host/check-steam-desktop.py' in (root / '.github/workflows/build-ipa.yml').read_text())

if failed:
    print(f'FAILED: Steam for Windows ({len(failed)})')
    for name in failed:
        print('  - ' + name)
    sys.exit(1)
print('PASS: Steam for Windows')
