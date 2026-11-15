#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright the Wine authors (LGPL-2.1-or-later): the two functions quoted below as they are in Wine 11.4
# (dlls/msvcrt/time.c). BCD's terms above do not apply to those lines.
"""The C runtime's time() no longer asks for the time zone (build 660; BCD's fork commit, from Wine 0497d0ff62f by Benjamin Fischer, made
after Wine 11.4; Wine bug 59514); no Wine runs.

In Wine 11.4 msvcrt's _time64 and _time32 (every time() of a program) called _ftime64 and returned one field of its answer. _ftime64 also
fills "dstflag", for which it calls GetTimeZoneInformation: a system call into ntdll's unix side, a mutex and several C-library time
conversions (tests/host/check-timezone-fast-path.py measures that side), on every time() call, for a value time() throws away. Wine made
_time64 compute the seconds itself. BCD takes that with one difference: Wine's version also drops _ftime64's first-use _tzset (the
runtime's time zone variables); BCD's keeps that call, so that the only thing that changes is the time zone query that was never used.

Who gets it, exactly:
  - 32-bit programs: every runtime DLL of the i386 farm that compiles dlls/msvcrt/time.c (crtdll, msvcr70, msvcr71, msvcr80, msvcr90,
    msvcr100, msvcr110, msvcr120, msvcrt, msvcrtd, ucrtbase); msvcr120_app, msvcrt20 and msvcrt40 forward to one of them. The farm is
    rebuilt when the Wine pin changes, locally and in CI.
  - 64-bit programs: almost nobody. ucrtbase-x64math.dll (opt-in, rebuilt by every build) gets it. msvcr70 to msvcr110 would get it
    only in a build tree that has not built them yet: tools/build-wine-extra-dlls.sh skips a DLL that is already in arm64ec-windows,
    and the local build keeps them from an earlier build. The committed 64-bit ucrtbase.dll, msvcrt.dll and msvcr120.dll (ARM64EC and
    aarch64) keep the old code, and the 64-bit msvcrt20/msvcrt40 forward to that msvcrt.dll. For all of those only the cheaper query
    of the other check helps.

It is in by default because it is proven to return the same: the old functions (Wine 11.4's text, quoted below, LGPL-2.1-or-later,
copyright the Wine authors) and the fork's, compiled side by side with one stand-in clock, over 2,000 system times from 1601 to past 2038
and 2106: the same return value, the same value stored through the pointer, the same single first-use time zone initialisation per call;
the old ones ask for the time zone once per call, the new ones never. With the repo's llvm-mingw and generated headers (the full tree)
time.c compiles for i686 and x86_64. No auto-tune rule: this is not a failure.

The Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none, or when the one there lacks the
change and is not this checkout's pin. The CI runs this. Needs python3 and a C compiler.
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


def function(text, signature):
    start = text.find(signature)
    if start < 0:
        return ''
    return text[start:text.index('\n}\n', start) + 3]


def git(*args, cwd=root):
    r = subprocess.run(['git', '-C', str(cwd)] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


pin = ''
tree = git('ls-tree', 'HEAD', 'wine')
if tree:
    pin = tree.split()[2]
wine = None
for candidate in (os.environ.get('BCD_WINE_SRC'), root / 'wine', Path.home() / 'madeira-bcd/wine'):
    if candidate and (Path(candidate) / 'dlls/msvcrt/time.c').is_file():
        wine = Path(candidate)
        break
time_c = (wine / 'dlls/msvcrt/time.c').read_text() if wine else ''
new64 = function(time_c, '__time64_t CDECL _time64(__time64_t *buf)\n{') if wine else ''
has_change = bool(new64) and '_ftime64(&tb);' not in new64
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

# The two functions as they were in Wine 11.4 (BCD's base; dlls/msvcrt/time.c, LGPL-2.1-or-later, copyright the Wine authors), quoted so
# that the two versions can run side by side.
OLD = r'''__time64_t CDECL _time64(__time64_t *buf)
{
    __time64_t curtime;
    struct __timeb64 tb;

    _ftime64(&tb);

    curtime = tb.time;
    return buf ? *buf = curtime : curtime;
}
__time32_t CDECL _time32(__time32_t *buf)
{
    __time32_t curtime;
    struct __timeb64 tb;

    _ftime64(&tb);

    curtime = (tb.time == (__time32_t)tb.time) ? tb.time : -1;
    return buf ? *buf = curtime : curtime;
}
'''

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef long long __time64_t; typedef int __time32_t; typedef unsigned long long ULONGLONG; typedef unsigned int DWORD; typedef int BOOL;
typedef struct { DWORD dwLowDateTime, dwHighDateTime; } FILETIME;
typedef struct { int x[43]; } TIME_ZONE_INFORMATION;
struct __timeb64 { __time64_t time; unsigned short millitm; short timezone; short dstflag; };
#define CDECL
#define TICKSPERSEC 10000000
#define TICKSPERMSEC 10000
#define SECS_1601_TO_1970 ((369 * 365 + 89) * (ULONGLONG)86400)
#define TIME_ZONE_ID_DAYLIGHT 2
static ULONGLONG now; static long tz_queries, tz_inits; static int MSVCRT___timezone = -10800;
static void GetSystemTimeAsFileTime( FILETIME *ft ) { ft->dwLowDateTime = (DWORD)now; ft->dwHighDateTime = (DWORD)(now >> 32); }
static DWORD GetTimeZoneInformation( TIME_ZONE_INFORMATION *tz ) { tz_queries++; return 1; }
static void _tzset_init(void) { tz_inits++; }
FTIME
#define _time64 old_time64
#define _time32 old_time32
OLD
#undef _time64
#undef _time32
NEW
int main(void)
{
    static const ULONGLONG fixed[] = { 0, 1, 9999999, 10000000, 116444736000000000ull - 1, 116444736000000000ull, 116444736000000000ull + 1,
        116444736000000000ull + 2147483647ull * 10000000, 116444736000000000ull + 2147483648ull * 10000000,
        116444736000000000ull + 4294967295ull * 10000000, 116444736000000000ull + 4294967296ull * 10000000, 0x7fffffffffffffffull };
    unsigned long long seed = 0x9e3779b97f4a7c15ull; int i, bad = 0, n = 0;
    long old_q = 0, new_q = 0, old_i = 0, new_i = 0;
    for (i = 0; i < 2000; i++)
    {
        __time64_t a64, b64, s64a = 7, s64b = 7; __time32_t a32, b32, s32a = 7, s32b = 7;
        if (i < (int)(sizeof(fixed) / sizeof(fixed[0]))) now = fixed[i];
        else { seed = seed * 6364136223846793005ull + 1442695040888963407ull; now = (seed >> 1) % (116444736000000000ull + 5000000000ull * 10000000); }
        tz_queries = tz_inits = 0; a64 = old_time64( &s64a ); a32 = old_time32( &s32a ); old_q += tz_queries; old_i += tz_inits;
        tz_queries = tz_inits = 0; b64 = _time64( &s64b ); b32 = _time32( &s32b ); new_q += tz_queries; new_i += tz_inits;
        if (a64 != b64 || s64a != s64b || a32 != b32 || s32a != s32b) bad++;
        if (old_time64( NULL ) != _time64( NULL ) || old_time32( NULL ) != _time32( NULL )) bad++;
        n++;
    }
    printf( "%d %d %ld %ld %ld %ld\n", bad, n, old_q, new_q, old_i, new_i );
    return 0;
}
'''

if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    new32 = function(time_c, '__time32_t CDECL _time32(__time32_t *buf)\n{')
    ftime = function(time_c, 'void CDECL _ftime64(struct __timeb64 *buf)\n{')
    check('_ftime64 itself is as it was (it still fills dstflag from GetTimeZoneInformation, for the programs that ask for it)',
          'buf->dstflag = GetTimeZoneInformation( &tzinfo) == TIME_ZONE_ID_DAYLIGHT;' in ftime and '_tzset_init();' in ftime
          and 'buf->time = time / TICKSPERSEC - SECS_1601_TO_1970;' in ftime)
    check('_time64 keeps the first-use time zone initialisation, reads the system time and does the same arithmetic, with no time zone query',
          new64.index('_tzset_init();') < new64.index('GetSystemTimeAsFileTime(&ft);') and 'curtime = time / TICKSPERSEC - SECS_1601_TO_1970;' in new64
          and 'GetTimeZoneInformation' not in new64.split('*/')[-1])
    check('_time32 is _time64 with the range test it had', 'time = _time64(NULL);' in new32 and 'curtime = (time == (__time32_t)time) ? time : -1;' in new32)
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='msvcrt-time-'))
    try:
        (tmp / 't.c').write_text(HARNESS.replace('FTIME', ftime).replace('OLD', OLD).replace('NEW', new64 + new32))
        r = subprocess.run([cc, '-std=gnu11', '-O1', '-Wno-unused-function', '-o', str(tmp / 't'), str(tmp / 't.c')], capture_output=True, text=True)
        check('the old and the new functions compile on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            out = subprocess.run([str(tmp / 't')], capture_output=True, text=True).stdout.split()
            bad, n, old_q, new_q, old_i, new_i = (int(x) for x in out)
            check('over %d system times (1601, 1970, the 2038 and 2106 edges, random ones) time() returns and stores the same value as before, as '
                  '__time64_t and as __time32_t' % n, bad == 0 and n == 2000, str(out))
            check('the old functions asked for the time zone on every call (%d queries); the new ones never do (%d)' % (old_q, new_q),
                  old_q == 2 * n and new_q == 0)
            check('the first-use time zone initialisation is reached exactly as often as before (%d and %d)' % (old_i, new_i), old_i == new_i == 2 * n)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # the whole file, with the flags Wine's own build uses for msvcrt.dll (wine/build-i386/compile_commands.json), for the two targets
    tc = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
    if (tc / 'clang').exists() and (wine / 'build-macos/include').is_dir():
        for tgt in ('i686', 'x86_64'):
            r = subprocess.run([str(tc / 'clang'), '-target', tgt + '-windows', '--no-default-config', '-fsyntax-only', '-O2', '-D__STDC__', '-D_MSVCR_VER=0',
                                '-D__WINESRC__', '-D_CRTIMP=', '-D__WINE_PE_BUILD', '-fno-builtin', '-mlong-double-64', '-I' + str(wine / 'dlls/msvcrt'),
                                '-I' + str(wine / 'build-macos/include'), '-I' + str(wine / 'include'), '-I' + str(wine / 'include/msvcrt'),
                                str(wine / 'dlls/msvcrt/time.c')], capture_output=True, text=True)
            check('msvcrt/time.c compiles for %s' % tgt, r.returncode == 0 and ' error' not in r.stderr, r.stderr[-1500:])
    else:
        print('note: no llvm-mingw or generated headers in this tree; the compile runs in the full tree')

ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-msvcrt-time-no-tz-query.py' in ci)

print('PASS: time() without the time zone query' if ok else 'FAILED: time() without the time zone query')
sys.exit(0 if ok else 1)
