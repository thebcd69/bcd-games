#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright the Wine authors (LGPL-2.1-or-later): the head of get_timezone_info quoted below as it is in Wine 11.4
# (dlls/ntdll/unix/system.c). BCD's terms above do not apply to those lines. The eight lines of time zone rules are the tz database's
# (public domain).
"""GetTimeZoneInformation is no longer one hour off in Singapore, Kuala Lumpur, Santo Domingo and Danmarkshavn (build 660; BCD's fork commit
"a time zone is inverse only when its daylight reading is exactly one hour later"); the real function runs here, on the Mac's C library.

get_timezone_info (wine/dlls/ntdll/unix/system.c) decides whether a zone has inverse daylight saving (standard time in summer, as Irish law
has it) with `mktime(tm, tm_isdst = 0) < mktime(tm, tm_isdst = 1)` and then adds 60 minutes to the bias (Wine bfae6eb8b4f, in 11.4 and in
build 659). Apple's C library, the same code on macOS and iOS, answers the tm_isdst = 1 reading of a zone that has no daylight saving now
with a daylight type from the zone's history, which for four zones lies later than the standard reading: Asia/Singapore and
Asia/Kuala_Lumpur by 2400 s (the +07:20 of 1933), America/Santo_Domingo by 1800 s, America/Danmarkshavn by 7200 s. Wine's test took them
for inverse zones: a game in Singapore or Malaysia was told the zone is UTC+7 (Bias -420 instead of -480), in Santo Domingo UTC-5, in
Danmarkshavn UTC-1. The C runtime takes its _timezone from that answer at first use, so localtime() was an hour slow there, while
GetLocalTime (the server's bias) was right: a game saw two local times an hour apart. No log line shows it, so no auto-tune rule.
Found by BCD's review of 2026-11-14; it is a bug of build 659, not of this round.

A real inverse zone gives exactly +3600 s in both seasons, so the test is now "both readings valid and the daylight one exactly 3600 s
later". Keyless: it changes the answer only for the zones that were wrong.

Checks (the Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none, or when the one there
lacks the change and is not this checkout's pin): Wine 11.4's function (its head quoted below) and the fork's, compiled side by side and
run in a process per zone for every zone of /usr/share/zoneinfo:
  1. the two differ in exactly the zones named above (Singapore has two names) and give the same bytes in every other zone;
  2. in those zones the new Bias is the zone's real standard offset, computed independently (Python's zoneinfo), and the old one was
     60 minutes more; in ALL zones the new Bias is that independent value;
  3. a real inverse zone, Europe/Dublin built with negative daylight saving by zic, is still recognised: Bias 0 with daylight rules,
     the same bytes as before.
The CI runs this. Needs python3 (zoneinfo) and a C compiler; part 3 needs zic.
"""
from pathlib import Path
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import zoneinfo

root = Path(__file__).resolve().parents[2]
ok = True
WRONG = {'Asia/Singapore': -480, 'Singapore': -480, 'Asia/Kuala_Lumpur': -480, 'America/Santo_Domingo': 240, 'America/Danmarkshavn': 0}


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
    if candidate and (Path(candidate) / 'dlls/ntdll/unix/system.c').is_file():
        wine = Path(candidate)
        break
system_c = (wine / 'dlls/ntdll/unix/system.c').read_text() if wine else ''
new_func = function(system_c, 'static void get_timezone_info( RTL_DYNAMIC_TIME_ZONE_INFORMATION *tzi )\n{') if wine else ''
has_change = 't2 - t1 == 3600' in new_func
is_pinned = bool(wine and pin and git('rev-parse', 'HEAD', cwd=wine) == pin)
if wine and not has_change and not is_pinned:
    print('SKIP the Wine source at %s does not carry the change (this checkout pins %s; BCD_WINE_SRC can name a tree that does)' % (wine, pin[:12] or '?'))
    wine = None
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')

# The head of the function as it is in Wine 11.4 and in build 659 (dlls/ntdll/unix/system.c, LGPL-2.1-or-later, copyright the Wine
# authors), quoted so that the old answer can be computed beside the new one. The rest of the function is the fork's own text.
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
static int timezone_mutex;
static void mutex_lock( int *m ) { }
static void mutex_unlock( int *m ) { }
/* the registry side is the same code in both and needs a Wine registry: left out of both, the same way */
static BOOL get_system_config_tz_info( RTL_DYNAMIC_TIME_ZONE_INFORMATION *tzi, int year ) { return 0; }
static BOOL match_tz_info( const RTL_DYNAMIC_TIME_ZONE_INFORMATION *a, const RTL_DYNAMIC_TIME_ZONE_INFORMATION *b ) { return 0; }
static void find_reg_tz_info( RTL_DYNAMIC_TIME_ZONE_INFORMATION *tzi, int year ) { }
FIND_DST_CHANGE
#define get_timezone_info old_get_timezone_info
OLD_FUNCTION
#undef get_timezone_info
#define get_timezone_info new_get_timezone_info
NEW_FUNCTION
#undef get_timezone_info
int main(void)
{
    RTL_DYNAMIC_TIME_ZONE_INFORMATION a, b;
    tzset();
    memset( &a, 0x11, sizeof(a) ); memset( &b, 0x22, sizeof(b) );
    old_get_timezone_info( &a ); new_get_timezone_info( &b );
    printf( "%d %d %d %d\n", a.Bias, b.Bias, memcmp( &a, &b, sizeof(a) ) != 0, b.DaylightDate.wMonth );
    return 0;
}
'''

if wine:
    print('Wine source: %s (%s)' % (wine, git('rev-parse', '--short=12', 'HEAD', cwd=wine)))
    check('the fork tests "both readings valid and the daylight one exactly 3600 s later", and Wine\'s "<" test is gone',
          'inverted_dst = (t1 != (time_t)-1 && t2 != (time_t)-1 && t2 - t1 == 3600);' in new_func
          and 'inverted_dst = mktime(&tm1) < mktime(&tm2);' not in new_func and 'if (inverted_dst) bias += 60;' in new_func)
    tail = new_func[new_func.index('    tzi->Bias = bias;\n') + len('    tzi->Bias = bias;\n'):]
    find = function(system_c, 'static time_t find_dst_change(time_t start, time_t end, int *is_dst)\n{')
    src = HARNESS.replace('FIND_DST_CHANGE', find).replace('OLD_FUNCTION', OLD_HEAD + tail).replace('NEW_FUNCTION', new_func)
    cc = os.environ.get('CC', 'cc')
    tmp = Path(tempfile.mkdtemp(prefix='timezone-inverse-'))
    try:
        (tmp / 't.c').write_text(src)
        r = subprocess.run([cc, '-std=gnu11', '-O2', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-but-set-variable',
                            '-o', str(tmp / 't'), str(tmp / 't.c')], capture_output=True, text=True)
        check('Wine 11.4\'s function and the fork\'s compile on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            zi = Path('/usr/share/zoneinfo')
            zones = sorted(str(p.relative_to(zi)) for p in zi.rglob('*')
                           if p.is_file() and p.name[0].isupper() and '.' not in p.name and p.name not in ('Factory',)
                           and not str(p.relative_to(zi)).startswith(('posix', 'right')))
            check('this Mac has time zone data to run over (%d zones)' % len(zones), len(zones) > 300)
            now = datetime.datetime.now(datetime.timezone.utc)
            changed, wrong_new, independent_bad, garbage = {}, [], [], []
            for z in zones:
                out = subprocess.run([str(tmp / 't')], env=dict(os.environ, TZ=z), capture_output=True, text=True).stdout.split()
                if len(out) != 4:
                    garbage.append(z)
                    continue
                old_bias, new_bias, differs = int(out[0]), int(out[1]), out[2] == '1'
                if differs:
                    changed[z] = (old_bias, new_bias)
                try:
                    local = now.astimezone(zoneinfo.ZoneInfo(z))
                    std = local.utcoffset() - (local.dst() or datetime.timedelta(0))
                    want = -int(std.total_seconds() // 60)
                    if new_bias != want:
                        independent_bad.append((z, new_bias, want))
                except Exception as e:   # a name Python's zoneinfo does not take
                    independent_bad.append((z, new_bias, repr(e)[:40]))
            check('every zone gave an answer', not garbage, str(garbage[:5]))
            check('the answer changes in exactly these zones: %s; the other %d get the same bytes from Wine 11.4\'s function and the fork\'s'
                  % (', '.join(sorted(WRONG)), len(zones) - len(WRONG)), sorted(changed) == sorted(WRONG), str(sorted(changed.items())))
            check('there the old Bias was 60 minutes too much and the new one is the zone\'s real standard offset: '
                  + ', '.join('%s %d -> %d' % (z, changed[z][0], changed[z][1]) for z in sorted(changed) if z in WRONG),
                  all(z in changed and changed[z] == (WRONG[z] + 60, WRONG[z]) for z in WRONG), str(changed))
            check('in all %d zones the new Bias is the standard offset Python\'s zoneinfo computes independently' % len(zones),
                  not independent_bad, str(independent_bad[:6]))
            zic = shutil.which('zic') or ('/usr/sbin/zic' if os.path.exists('/usr/sbin/zic') else None)
            if zic:
                (tmp / 'eire').write_text(EIRE)
                r = subprocess.run([zic, '-d', str(tmp / 'zi'), str(tmp / 'eire')], capture_output=True, text=True)
                out = subprocess.run([str(tmp / 't')], env=dict(os.environ, TZ=str(tmp / 'zi/Europe/Dublin')), capture_output=True, text=True).stdout.split()
                check('a real inverse zone (Europe/Dublin built with negative daylight saving) is still recognised: Bias 0 from both, the same bytes, daylight rules present',
                      r.returncode == 0 and out[:3] == ['0', '0', '0'] and out[3] != '0', str(out) + r.stderr[-300:])
            else:
                print('note: no zic on this machine; the inverse-zone part is not run')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-timezone-inverse-zones.py' in ci)

print('PASS: time zone inversion test' if ok else 'FAILED: time zone inversion test')
sys.exit(0 if ok else 1)
