#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A volume whose "important usage" free space reads 0 reports its statfs free space (build 660; Wine commit c8b71506800 by Tim Clem, made
after Wine 11.4 and cherry-picked into BCD's fork); no Wine runs.

On Apple systems Wine asks CoreFoundation for a volume's free space (kCFURLVolumeAvailableCapacityForImportantUsageKey) and, when it gets an
answer, reports that instead of statfs's numbers. Wine's commit says the call "fails silently and returns 0 free bytes if the volume is not
APFS": every such volume then looks full to a Windows program (GetDiskFreeSpaceEx: 0 bytes free), which matters for a game or an installer
that checks the space before it writes. BCD supports exFAT drives (docs/EXTERNAL-STORAGE.md). With the change a 0 answer is treated as "no
answer" and statfs's value stands.

NOT proven on a device: that iOS gives 0 for an exFAT drive, as macOS does, is Wine's statement about macOS, not a BCD measurement, and no
BCD log has a session with an external drive (0 logs with an "[external] added" line in 2026-10-20 to 2026-11-10). So there is no auto-tune
rule. Keyless: on a volume where the call gives a non-zero number (the phone's own APFS storage) nothing changes; a volume that truly has
0 bytes left for important usage is read with statfs, which is then 0 or close to it.

Checks (the Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none, or when the one there
lacks the change and is not this checkout's pin): get_free_bytes_for_important_data cut from the Wine fork and compiled on the host with
stand-ins for the CoreFoundation calls: a non-zero answer is returned as it is; 0 becomes -1; a failing call, a failing URL and a failing
F_GETPATH are -1 as before; nothing leaks (every created object is released once). get_full_size_info still overrides statfs only when the
answer is not -1. BCD's 15 file.c patch scripts still apply. The CI runs this.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def git(*args, cwd=root):
    r = subprocess.run(['git', '-C', str(cwd)] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


pin = ''
tree = git('ls-tree', 'HEAD', 'wine')
if tree:
    pin = tree.split()[2]
wine = None
for candidate in (os.environ.get('BCD_WINE_SRC'), root / 'wine', Path.home() / 'madeira-bcd/wine'):
    if candidate and (Path(candidate) / 'dlls/ntdll/unix/file.c').is_file():
        wine = Path(candidate)
        break
file_c = (wine / 'dlls/ntdll/unix/file.c').read_text() if wine else ''
has_change = "It's unlikely that a writeable disk has exactly 0 free bytes" in file_c
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef long long LONGLONG; typedef unsigned char UInt8;
typedef struct fake_obj { int kind; long long value; } *CFURLRef, *CFNumberRef;
#define MAXPATHLEN 1024
#define F_GETPATH 50
#define false 0
#define kCFNumberLongLongType 11
static const char *kCFURLVolumeAvailableCapacityForImportantUsageKey = "important";
static int getpath_fails, url_fails, property_fails, created, released; static long long answer;
static int fake_fcntl( int fd, int cmd, char *path ) { if (cmd != F_GETPATH) abort(); if (getpath_fails) return -1; strcpy( path, "/Volumes/X" ); return 0; }
static CFURLRef CFURLCreateFromFileSystemRepresentation( void *alloc, const UInt8 *path, size_t len, int dir )
{ struct fake_obj *o; if (url_fails) return NULL; if (strcmp( (const char *)path, "/Volumes/X" ) || len != 10) abort(); o = calloc( 1, sizeof(*o) ); o->kind = 1; created++; return o; }
static int CFURLCopyResourcePropertyForKey( CFURLRef url, const char *key, CFNumberRef *num, void *err )
{ struct fake_obj *o; if (key != kCFURLVolumeAvailableCapacityForImportantUsageKey) abort(); if (property_fails) return 0; o = calloc( 1, sizeof(*o) ); o->kind = 2; o->value = answer; created++; *num = o; return 1; }
static int CFNumberGetValue( CFNumberRef num, int type, void *out ) { if (type != kCFNumberLongLongType) abort(); *(long long *)out = num->value; return 1; }
static void CFRelease( void *o ) { if (!o) abort(); released++; free( o ); }
#define fcntl fake_fcntl
CODE
int main( int argc, char **argv )
{
    LONGLONG r;
    answer = atoll( argv[1] ); getpath_fails = atoi( argv[2] ); url_fails = atoi( argv[3] ); property_fails = atoi( argv[4] );
    r = get_free_bytes_for_important_data( 5 );
    printf( "%lld %d %d\n", r, created, released );
    return 0;
}
'''

if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    start = file_c.index('static LONGLONG get_free_bytes_for_important_data(int fd)\n{')
    func = file_c[start:file_c.index('\n}\n', start) + 3]
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='free-space-fallback-'))
    try:
        (tmp / 't.c').write_text(HARNESS.replace('CODE', func))
        r = subprocess.run([cc, '-std=gnu11', '-O1', '-o', str(tmp / 't'), str(tmp / 't.c')], capture_output=True, text=True)
        check('get_free_bytes_for_important_data compiles on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            def run(*a):
                return subprocess.run([str(tmp / 't')] + [str(x) for x in a], capture_output=True, text=True).stdout.strip()
            check('a non-zero answer is returned as it is', run(123456789012, 0, 0, 0) == '123456789012 2 2', run(123456789012, 0, 0, 0))
            check('one byte free is still an answer', run(1, 0, 0, 0) == '1 2 2', run(1, 0, 0, 0))
            check('0 is "no answer": -1, so statfs stands', run(0, 0, 0, 0) == '-1 2 2', run(0, 0, 0, 0))
            check('a failing property call is -1, as before', run(77, 0, 0, 1) == '-1 1 1', run(77, 0, 0, 1))
            check('a failing URL is -1, as before', run(77, 0, 1, 0) == '-1 0 0', run(77, 0, 1, 0))
            check('a failing F_GETPATH is -1, as before', run(77, 1, 0, 0) == '-1 0 0', run(77, 1, 0, 0))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check('get_full_size_info overrides statfs only when the answer is not -1',
          'important_free_bytes = get_free_bytes_for_important_data( fd );\n    if (important_free_bytes != -1) stfs.f_bavail = stfs.f_bfree = important_free_bytes / bsize;' in file_c)

    scripts = ['patch-wine-guest-log.py', 'patch-wine-guest-log-tag.py', 'patch-wine-guest-log-service.py', 'patch-wine-guest-log-renpy.py',
               'patch-wine-guest-log-trace.py', 'patch-wine-guest-log-redact.py', 'patch-wine-guest-log-secrets.py', 'patch-wine-guest-log-ring.py', 'patch-wine-file-trace.py',
               'patch-wine-file-census.py', 'patch-wine-globalroot.py', 'patch-wine-app-local-dll.py', 'patch-wine-file-info-fstat.py',
               'patch-wine-hide-host-version-plist.py', 'patch-wine-dir-storm.py', 'patch-wine-file-xattr-one-list.py']
    tmp = Path(tempfile.mkdtemp(prefix='free-space-fallback-scripts-'))
    try:
        copy = tmp / 'file.c'
        shutil.copy(wine / 'dlls/ntdll/unix/file.c', copy)
        failed = []
        for s in scripts:
            r = subprocess.run([sys.executable, str(root / 'tools' / s), str(copy)], capture_output=True, text=True)
            if r.returncode:
                failed.append(s + ': ' + (r.stdout + r.stderr)[-200:])
        check('BCD\'s %d file.c patch scripts apply to the changed file in the build\'s order' % len(scripts), not failed, '; '.join(failed))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-wine-free-space-fallback.py' in ci)

print('PASS: free space fallback' if ok else 'FAILED: free space fallback')
sys.exit(0 if ok else 1)
