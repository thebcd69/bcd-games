#!/usr/bin/env python3
# Copyright 2026 intraducine. This file was taken from https://github.com/willfaust/Madeira (pull request #607)
# under GPL-3.0-or-later with the Madeira Converter Exception, and modified for BCD in November 2026 (the source checks
# Madeira keeps in check-ci-component-keys.py, which BCD does not have, are here). See THIRD-PARTY-NOTICES.md.
"""Exercise FFmpeg source and configure reuse without building FFmpeg.

build/ffmpeg/build.sh builds FFmpeg from the tracked release tarball (build/ffmpeg/src). Each FFmpeg version builds in
its own object folder, the source is extracted into a folder named after the tarball's SHA-256 (so a changed tarball is
extracted again), and the configure stamp holds that checksum (so a changed tarball is configured again). The run below
uses stub xcrun/sysctl/make and a stub configure: no compiler runs; a make distclean that fails (an interrupted configure)
empties the version's build folder with one line and the build goes on. The notices name the version that is built and the
checksum SHA256SUMS pins, and CI runs this check.
"""
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

repo = Path(__file__).resolve().parents[2]
source = repo / 'build/ffmpeg/build.sh'
ffmpeg_build = source.read_text()
assert 'BUILD="$OBJ_DIR/build-$FFMPEG_VERSION"' in ffmpeg_build, \
    'FFmpeg versions must not share build objects'
assert 'WANT_STAMP="$(printf \'%s\\n\' "$GOT" "${CONFIG_ARGS[@]}")"' in ffmpeg_build, \
    'FFmpeg configure stamp must change with the verified source archive'
ffmpeg_version = re.search(r'^FFMPEG_VERSION=([^\s]+)$', ffmpeg_build, re.M)[1]
assert f'FFmpeg {ffmpeg_version} (' in (repo / 'app/Madeira/licenses/THIRD-PARTY-NOTICES.txt').read_text(), \
    'bundled notices must name the FFmpeg version being built'
sums = (repo / 'build/ffmpeg/src/SHA256SUMS').read_text().split()
assert sums[1:] == [f'ffmpeg-{ffmpeg_version}.tar.xz'] and len(sums[0]) == 64, ('one checksum, for the version built', sums)
assert sorted(p.name for p in (repo / 'build/ffmpeg/src').iterdir() if not p.name.startswith('.')) == ['SHA256SUMS', f'ffmpeg-{ffmpeg_version}.tar.xz'], \
    'build/ffmpeg/src holds the tarball that is built and its checksum only'
for notices in ('THIRD-PARTY-NOTICES.md', 'app/Madeira/legal/THIRD-PARTY-NOTICES.md'):
    text = (repo / notices).read_text()
    assert f'**FFmpeg** {ffmpeg_version} ' in text and f'`build/ffmpeg/src/ffmpeg-{ffmpeg_version}.tar.xz`' in text \
        and f'(`{sums[0]}`)' in text, (notices, 'names the FFmpeg version, its tarball and the checksum SHA256SUMS pins')
assert 'python3 tests/host/check-ffmpeg-source-reuse.py' in (repo / '.github/workflows/build-ipa.yml').read_text(), 'CI runs this check'
with tempfile.TemporaryDirectory(prefix='madeira-ffmpeg-reuse-') as tmp:
    root = Path(tmp)
    build = root / 'build/ffmpeg'
    src = build / 'src'
    src.mkdir(parents=True)
    (root / 'app/Madeira').mkdir(parents=True)
    shutil.copy(source, build / 'build.sh')
    bins = root / 'bin'
    bins.mkdir()
    for name, body in {
        'xcrun': 'echo /usr/bin/true',
        'sysctl': 'echo 2',
        'make': '''if [ "$1" = distclean ]; then [ -z "$TASK_DISTCLEAN_FAIL" ] || exit 1; rm -f config.h Makefile; exit 0; fi
if [ "$1" = install ]; then
  mkdir -p "$TASK_PREFIX/lib"
  for lib in avformat avcodec swresample avutil; do : > "$TASK_PREFIX/lib/lib$lib.a"; done
fi''',
    }.items():
        path = bins / name
        path.write_text('#!/bin/sh\nset -e\n' + body + '\n')
        path.chmod(0o755)
    env = dict(os.environ, PATH=str(bins) + os.pathsep + os.environ['PATH'],
               TASK_LOG=str(root / 'configured'), TASK_PREFIX=str(root / 'toolchains/ffmpeg-ios'))
    archive = src / f'ffmpeg-{ffmpeg_version}.tar.xz'

    def write_source(label):
        script = ('#!/bin/sh\necho ' + label + ' >> "$TASK_LOG"\n'
                  'touch config.h Makefile\n'
                  'printf "#define CONFIG_X_DECODER 1\\n#define CONFIG_X_DEMUXER 1\\n'
                  '#define CONFIG_X_PARSER 1\\n#define CONFIG_X_PROTOCOL 1\\n'
                  '#define CONFIG_X_MUXER 1\\n" > config_components.h\n').encode()
        with tarfile.open(archive, 'w:xz') as tar:
            item = tarfile.TarInfo(f'ffmpeg-{ffmpeg_version}/configure')
            item.mode, item.size = 0o755, len(script)
            tar.addfile(item, io.BytesIO(script))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (src / 'SHA256SUMS').write_text(digest + '  ' + archive.name + '\n')
        return digest

    def run():
        return subprocess.run(['bash', str(build / 'build.sh')], env=env, capture_output=True, text=True)

    first = write_source('first')
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / 'configured').read_text().splitlines() == ['first']
    second = write_source('second')
    assert first != second
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / 'configured').read_text().splitlines() == ['first', 'second']
    assert (build / 'obj' / (f'ffmpeg-{ffmpeg_version}-' + second) / '.madeira-extracted').is_file()
    # BCD (U1 review F2): an interrupted configure leaves a Makefile and no config.h, and distclean fails on that tree: the build folder
    # is emptied with one line, configure runs again and the build succeeds, as it healed itself before.
    bdir = build / 'obj' / f'build-{ffmpeg_version}'
    (bdir / 'config.h').unlink()
    (bdir / 'stale.o').write_text('an object of the interrupted tree')
    env['TASK_DISTCLEAN_FAIL'] = '1'
    result = run()
    del env['TASK_DISTCLEAN_FAIL']
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('=== make distclean failed: starting from an empty build folder ===') == 1, result.stdout
    assert (root / 'configured').read_text().splitlines() == ['first', 'second', 'second'] and not (bdir / 'stale.o').exists()
    (src / 'SHA256SUMS').write_text('0' * 64 + '  ' + archive.name + '\n')
    result = run()
    assert result.returncode != 0 and 'sha256 mismatch' in result.stderr
print('PASS: FFmpeg source reuse tracks verified archive content; a failing distclean empties the build folder and the build goes on')
