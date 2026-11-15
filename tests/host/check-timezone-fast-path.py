#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright the Wine authors (LGPL-2.1-or-later): the head of get_timezone_info quoted below as it is in Wine 11.4
# (dlls/ntdll/unix/system.c). BCD's terms above do not apply to those lines. The eight lines of time zone rules are the tz database's
# (public domain).
"""A time zone query that hits ntdll's cache no longer calls mktime() three times (build 660; Wine commit e3394292156 by Zhou Qiankang, made
after Wine 11.4 and cherry-picked into BCD's fork; Wine bug 59514); the real function runs here, on the Mac's own C library.

NtQuerySystemInformation(SystemCurrentTimeZoneInformation / SystemDynamicTimeZoneInformation) ends in get_timezone_info
(wine/dlls/ntdll/unix/system.c). That is GetTimeZoneInformation, GetDynamicTimeZoneInformation and what calls them:
SystemTimeToTzSpecificLocalTime with no zone given, and in the Wine 11.4 C runtime every plain time() call (msvcrt's _time64 goes through
_ftime64, which asks for the time zone to fill a field time() does not return; the shipped 64-bit ucrtbase.dll and msvcrt.dll still do).
NOT GetLocalTime, FileTimeToLocalFileTime or RtlSystemTimeToLocalTime: those take the bias from the shared user data, not from this
function. Since Wine's change for time zones with inverse daylight saving (bfae6eb8b4f, in 11.4) the function computed "is this zone
inverted" with two mktime() calls BEFORE looking at its cache, on top of the one mktime() the cache key needs. Wine moved those two calls
behind the cache miss: a hit now costs one mktime() instead of three. No log measures how often a game makes these calls, so how much of
this a player notices is not shown; it undoes a slowdown Wine itself introduced in 11.4.

It is in by default because the answer is proven to be the same. Here the function with the cache test where Wine 11.4 has it (Wine
11.4's text, quoted below) and the function as it is in the fork are compiled side by side and run in a process of their own for every
time zone this Mac has (/usr/share/zoneinfo, several hundred), at four instants each (now, in three and nine months, in a year: both
halves of a daylight-saving year and a year change). For every zone and instant the two fill the caller's structure with the same bytes,
on the first call (cache miss) and on the second (cache hit); the old order calls mktime() three times on a hit and the new one once.
(BCD's review of 2026-11-14 ran the same comparison over 24.6 million simulated clock steps per C library, on this Mac's and on the
iOS 18.1 and iOS 26.3 simulators' libsystem_c: 0 differences.)

The inversion test itself is BCD's corrected one in both (fork commit "a time zone is inverse only when its daylight reading is exactly
one hour later", tests/host/check-timezone-inverse-zones.py): the old test misfired for four zones on Apple's C library. With the
corrected test no zone of Apple's data is inverse (Europe/Dublin is shipped without negative daylight saving), so the two cache keys
are the same on a device. They can differ only for a real inverse zone, and only if the device's time zone is changed while a game
runs between such a zone and a normal one with the same raw offset. A real inverse zone is checked here as well: a negative-daylight
build of Europe/Dublin made with zic (the form the tz database's main file has had since 2018) gets the same bytes from both orders.

No auto-tune rule: this is not a failure. The timing printed at the end is this Mac's, as information; the proof is the call count.

The Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none, or when the one there lacks the
change and is not this checkout's pin. The CI runs this. Needs python3 and a C compiler; the inverse-zone part needs zic.
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


def before(text, a, b):
    return a in text and b in text and text.index(a) < text.index(b)


def git(*args, cwd=root):
    r = subprocess.run(['git', '-C', str(cwd)] + list(args), capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


pin = ''
tree = git('ls-tree', 'HEAD', 'wine')
if tree:
    pin = tree.split()[2]
wine = None
for candidate in (os.environ.get('BCD_WINE_SRC'), root / 'wine', Path.home() / 'madeira-bcd/wine'):
    if candidate and (Path(candidate) / 'dlls/ntdll/unix/system.c').is_file():
        wine = Path(candidate)
        break
system_c = (wine / 'dlls/ntdll/unix/system.c').read_text() if wine else ''
new_func = function(system_c, 'static void get_timezone_info( RTL_DYNAMIC_TIME_ZONE_INFORMATION *tzi )\n{') if wine else ''
has_change = before(new_func, 'if (current_year == tm->tm_year && current_bias == bias)', '    if (inverted_dst) bias += 60;')
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

# The head of the function as it is in Wine 11.4 (BCD's base; dlls/ntdll/unix/system.c, LGPL-2.1-or-later, copyright the Wine authors),
# quoted so that the two orders can run side by side. Its inversion line is replaced below by the fork's (BCD's corrected test), so that
# the only difference between the two functions is where the cache test stands.
OLD_HEAD = r'''static void get_timezone_info( RTL_DYNAMIC_TIME_ZONE_INFORMATION *tzi )
{
    static RTL_DYNAMIC_TIME_ZONE_INFORMATION cached_tzi;
    static int current_year = -1, current_bias = 65535;
    RTL_DYNAMIC_TIME_ZONE_INFORMATION reg_tzi;
    struct tm *tm, tm1, tm2;
    time_t year_start, year_end, tmp, dlt = 0, std = 0;
    int is_dst, bias;
    BOOL inverted_dst;

    mutex_lock( &timezone_mutex );

    year_start = time(NULL);
    tm = gmtime(&year_start);
    bias = (LONG)(mktime(tm) - year_start) / 60;

    tm = localtime(&year_start);
    tm1 = tm2 = *tm;
    tm1.tm_isdst = 0;
    tm2.tm_isdst = 1;
    inverted_dst = mktime(&tm1) < mktime(&tm2);
    if (inverted_dst) bias += 60;

    if (current_year == tm->tm_year && current_bias == bias)
    {
        *tzi = cached_tzi;
        mutex_unlock( &timezone_mutex );
        return;
    }

    memset(tzi, 0, sizeof(*tzi));
    TRACE("tz data will be valid through year %d, bias %d, inverted_dst %d\n", tm->tm_year + 1900, bias, inverted_dst);
    current_year = tm->tm_year;
    current_bias = bias;

    tzi->Bias = bias;
'''

# Europe/Dublin with negative daylight saving, as the tz database's main "europe" file has it since 2018a (public domain)
EIRE = '''Rule	Eire	1971	only	-	Oct	31	 2:00u	-1:00	-
Rule	Eire	1972	1980	-	Mar	Sun>=16	 2:00u	0	-
Rule	Eire	1972	1980	-	Oct	Sun>=23	 2:00u	-1:00	-
Rule	Eire	1981	max	-	Mar	lastSun	 1:00u	0	-
Rule	Eire	1981	1989	-	Oct	Sun>=23	 1:00u	-1:00	-
Rule	Eire	1990	1995	-	Oct	Sun>=22	 1:00u	-1:00	-
Rule	Eire	1996	max	-	Oct	lastSun	 1:00u	-1:00	-
Zone	Europe/Dublin	1:00	Eire	IST/GMT
'''

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <mach/mach_time.h>
typedef int BOOL; typedef int LONG; typedef unsigned short WCHAR, WORD; typedef unsigned char BOOLEAN; typedef unsigned long long ULONGLONG;
typedef unsigned int ULONG;
typedef struct { WORD wYear, wMonth, wDayOfWeek, wDay, wHour, wMinute, wSecond, wMilliseconds; } SYSTEMTIME;
typedef struct
{
    LONG Bias; WCHAR StandardName[32]; SYSTEMTIME StandardDate; LONG StandardBias;
    WCHAR DaylightName[32]; SYSTEMTIME DaylightDate; LONG DaylightBias;
    WCHAR TimeZoneKeyName[128]; BOOLEAN DynamicDaylightTimeDisabled;
} RTL_DYNAMIC_TIME_ZONE_INFORMATION;
#define TRACE(...) do { } while (0)
#define WARN(...) do { } while (0)
static int timezone_mutex, locks, unlocks;
static void mutex_lock( int *m ) { locks++; }
static void mutex_unlock( int *m ) { unlocks++; }
/* the registry side is the same code in both and needs a Wine registry: left out of both, the same way */
static BOOL get_system_config_tz_info( RTL_DYNAMIC_TIME_ZONE_INFORMATION *tzi, int year ) { return 0; }
static BOOL match_tz_info( const RTL_DYNAMIC_TIME_ZONE_INFORMATION *a, const RTL_DYNAMIC_TIME_ZONE_INFORMATION *b ) { return 0; }
static void find_reg_tz_info( RTL_DYNAMIC_TIME_ZONE_INFORMATION *tzi, int year ) { }
/* the clock both versions read, and a count of their mktime() calls */
static time_t fake_now; static long mktimes;
static time_t fake_time( time_t *t ) { if (t) *t = fake_now; return fake_now; }
static time_t counted_mktime( struct tm *tm ) { mktimes++; return mktime( tm ); }
#define time fake_time
FIND_DST_CHANGE
#define mktime counted_mktime
#define get_timezone_info old_get_timezone_info
OLD_FUNCTION
#undef get_timezone_info
#define get_timezone_info new_get_timezone_info
NEW_FUNCTION
#undef get_timezone_info
#undef mktime
#undef time

int main( int argc, char **argv )
{
    RTL_DYNAMIC_TIME_ZONE_INFORMATION a, b;
    time_t real_now = time( NULL );
    long offsets[4] = { 0, 91L * 86400, 274L * 86400, 366L * 86400 };
    int i, bad = 0;
    long miss_old = 0, miss_new = 0, hit_old = 0, hit_new = 0;

    tzset();
    if (argc > 1 && !strcmp( argv[1], "--inverted" ))
    {
        struct tm tm1, tm2; time_t t1, t2;
        tm1 = tm2 = *localtime( &real_now ); tm1.tm_isdst = 0; tm2.tm_isdst = 1;
        t1 = mktime( &tm1 ); t2 = mktime( &tm2 );
        printf( "inverted=%d\n", t1 != (time_t)-1 && t2 != (time_t)-1 && t2 - t1 == 3600 );
        return 0;
    }
    for (i = 0; i < 4; i++)
    {
        fake_now = real_now + offsets[i];
        memset( &a, 0x11, sizeof(a) ); memset( &b, 0x22, sizeof(b) );
        mktimes = 0; old_get_timezone_info( &a ); miss_old += mktimes;
        mktimes = 0; new_get_timezone_info( &b ); miss_new += mktimes;
        if (memcmp( &a, &b, sizeof(a) )) bad |= 1;
        memset( &a, 0x33, sizeof(a) ); memset( &b, 0x44, sizeof(b) );
        mktimes = 0; old_get_timezone_info( &a ); hit_old = mktimes;
        mktimes = 0; new_get_timezone_info( &b ); hit_new = mktimes;
        if (memcmp( &a, &b, sizeof(a) )) bad |= 2;
        if (hit_old != 3 || hit_new != 1) bad |= 4;
    }
    if (locks != unlocks) bad |= 8;
    printf( "%d bias=%d dst=%d/%d hit=%ld/%ld", bad, a.Bias, a.DaylightDate.wMonth, a.StandardDate.wMonth, hit_old, hit_new );
    if (argc > 1)
    {
        mach_timebase_info_data_t tb; unsigned long long t0, t1, t2; int n = atoi( argv[1] );
        mach_timebase_info( &tb );
        t0 = mach_absolute_time(); for (i = 0; i < n; i++) old_get_timezone_info( &a );
        t1 = mach_absolute_time(); for (i = 0; i < n; i++) new_get_timezone_info( &b );
        t2 = mach_absolute_time();
        printf( " ns=%llu/%llu", (t1 - t0) * tb.numer / tb.denom / n, (t2 - t1) * tb.numer / tb.denom / n );
    }
    printf( "\n" );
    return 0;
}
'''

if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    check('the fork\'s function tests its cache before the two inversion mktime() calls, and still applies the inversion to the answer',
          has_change and 'if (inverted_dst) bias += 60;' in new_func and new_func.index('if (inverted_dst) bias += 60;') < new_func.index('tzi->Bias = bias;'))
    # the old function = Wine 11.4's head + the fork's own tail (the part after "tzi->Bias = bias;" is the same text in both)
    tail = new_func[new_func.index('    tzi->Bias = bias;\n') + len('    tzi->Bias = bias;\n'):]
    criterion = new_func[new_func.index('    tm2.tm_isdst = 1;\n') + len('    tm2.tm_isdst = 1;\n'):new_func.index('    if (inverted_dst) bias += 60;')]
    wine_line = '    inverted_dst = mktime(&tm1) < mktime(&tm2);\n'
    check('the quoted head has Wine 11.4\'s inversion line once, and the fork\'s test is BCD\'s corrected one (exactly one hour)',
          OLD_HEAD.count(wine_line) == 1 and 't2 - t1 == 3600' in criterion and wine_line not in new_func)
    old_func = OLD_HEAD.replace(wine_line, criterion) + tail
    check('apart from the moved lines the two functions are the same text',
          sorted(l for l in old_func.split('\n') if l.strip()) == sorted(l for l in new_func.split('\n') if l.strip()))
    find = function(system_c, 'static time_t find_dst_change(time_t start, time_t end, int *is_dst)\n{')
    src = HARNESS.replace('FIND_DST_CHANGE', find).replace('OLD_FUNCTION', old_func).replace('NEW_FUNCTION', new_func)
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='timezone-fast-path-'))
    try:
        (tmp / 't.c').write_text(src)
        r = subprocess.run([cc, '-std=gnu11', '-O2', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-but-set-variable',
                            '-o', str(tmp / 't'), str(tmp / 't.c')], capture_output=True, text=True)
        check('both functions compile on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            zi = Path('/usr/share/zoneinfo')
            zones = sorted(str(p.relative_to(zi)) for p in zi.rglob('*')
                           if p.is_file() and p.name[0].isupper() and '.' not in p.name and p.name not in ('Factory',)
                           and not str(p.relative_to(zi)).startswith(('posix', 'right')))
            check('this Mac has time zone data to run over (%d zones)' % len(zones), len(zones) > 300)
            bad = []
            dst_zones = inverted = 0
            for z in zones:
                env = dict(os.environ, TZ=z)
                out = subprocess.run([str(tmp / 't')], env=env, capture_output=True, text=True).stdout.split()
                if not out or out[0] != '0':
                    bad.append((z, out))
                elif out[2] != 'dst=0/0':
                    dst_zones += 1
            check('in each of the %d zones, at four instants, the old and the new function fill the same bytes on a miss and on a hit; a hit '
                  'calls mktime() three times before and once now (%d of the zones have daylight saving rules this year)'
                  % (len(zones), dst_zones), not bad and dst_zones > 20, str(bad[:4]))
            # a real inverse zone: Europe/Dublin with negative daylight saving, built here (the system's copy is the form without it)
            zic = shutil.which('zic') or ('/usr/sbin/zic' if os.path.exists('/usr/sbin/zic') else None)
            if zic:
                (tmp / 'eire').write_text(EIRE)
                r = subprocess.run([zic, '-d', str(tmp / 'zi'), str(tmp / 'eire')], capture_output=True, text=True)
                inv = str(tmp / 'zi/Europe/Dublin')
                out = subprocess.run([str(tmp / 't')], env=dict(os.environ, TZ=inv), capture_output=True, text=True).stdout.split()
                check('a real inverse zone (Europe/Dublin built with negative daylight saving): the same bytes from both orders, Bias 0, with daylight rules',
                      r.returncode == 0 and bool(out) and out[0] == '0' and out[1] == 'bias=0' and out[2] != 'dst=0/0', str(out) + r.stderr[-300:])
                out = subprocess.run([str(tmp / 't'), '--inverted'], env=dict(os.environ, TZ=inv), capture_output=True, text=True).stdout.split()
                check('and there the inversion test is true (the branch the two mktime() calls exist for is run)', bool(out) and out[-1] == 'inverted=1', str(out))
                out = subprocess.run([str(tmp / 't'), '--inverted'], env=dict(os.environ, TZ='Europe/Dublin'), capture_output=True, text=True).stdout.split()
                check('while the system\'s own Europe/Dublin is not an inverse zone on Apple\'s data', bool(out) and out[-1] == 'inverted=0', str(out))
            else:
                print('note: no zic on this machine; the inverse-zone part is not run')
            for z in ('Europe/Istanbul', 'America/Los_Angeles', 'Asia/Singapore'):
                out = subprocess.run([str(tmp / 't'), '200000'], env=dict(os.environ, TZ=z), capture_output=True, text=True).stdout.split()
                if out and out[-1].startswith('ns='):
                    o, n = out[-1][3:].split('/')
                    print('note: %s: a cache hit takes %s ns before and %s ns now on this Mac (200,000 calls each)' % (z, o, n))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-timezone-fast-path.py' in ci)

print('PASS: time zone fast path' if ok else 'FAILED: time zone fast path')
sys.exit(0 if ok else 1)
