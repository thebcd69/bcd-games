#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_CEF_POOL_IMAGES (X-Plane 12, send log MX3R0X, build 670): a program's own Chromium runs in one process and its four
PartitionAlloc pools get the address band's four slots, only for the programs the key names; no Wine runs.

X-Plane.exe hosts libcef.dll and chrome_elf.dll in its own process and asked what EADesktop.exe asks (jumbo#1 plain 16 GB, #2 guard-style
16 GB + 64 KB, #3 plain, #4 guard, the same hint shapes); a session without layout 2 gave #3 and #4 soft grants on the views of #2 and #1 (a
commit of size 0 at 0x780020d000, c000000d, PartitionAlloc's int3 in libcef.dll) and its helper cefclient.exe --type=gpu-process found no slot.
The EA app's solution (check-ea-cef.py) had its image names in the code; the key names them in the game's own file.

What is checked here (the compiled routing, the EA app's four asks and the byte-identical EA log lines are in check-ea-cef.py):
  - build/ntdll-unix/ios_cef_images.h, compiled and run on the host (AddressSanitizer/UBSan): the list grammar (1 to 4 plain .exe names, blanks
    around a name ignored, anything else lists nothing), which image is the browser, which a helper, which neither (the EA app's two programs are
    never taken from the list), and a differential run of about 19000 random and mutated strings against an independent reference;
  - process_ios.c: cef_one_kind and the browser's rewritten command line on X-Plane's real command lines (the log's), the --type= children of
    either listed program refused, a helper without --type= left alone, nothing taken without the key, and where the block sits;
  - the settings catalog (both keys, text kind, empty default), the downloaded-list policy (the images key may be set, the flags key may not;
    the policy's own validation, compiled with swiftc and compared with the same reference), no other source or launcher names the keys,
    every list row that carries the images key also carries env.MADEIRA_SC_PA_POOLS = 2;
  - auto-tune: the rule xplane-cef-pools, its lines, its fixture cut from MX3R0X and the .expect (the fixture's replay is check-autotune.py's);
  - CI runs this check.
Needs python3 and a C compiler; the policy test needs swiftc (skipped without it, unless --require-swift).
"""
from pathlib import Path
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
require_swift = '--require-swift' in sys.argv
failures = 0


def check(cond, what, detail=''):
    global failures
    print(('PASS: ' if cond else 'FAIL: ') + what + (('\n      ' + str(detail)) if detail and not cond else ''))
    if not cond:
        failures += 1


def read(rel):
    return (root / rel).read_text()


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


header_path = root / 'build/ntdll-unix/ios_cef_images.h'
native = read('build/ntdll-unix/virtual_ios.c')
proc = read('build/ntdll-unix/process_ios.c')

# ---- the list grammar: an independent reference ---------------------------------------------------------------------------
NAME = re.compile(r'[A-Za-z0-9._-]{1,60}\.[eE][xX][eE]')


def ref_parse(text):
    """The names of a valid list, or [] when it lists nothing."""
    if not text or len(text) > 511:
        return []
    names = [t.strip(' \t') for t in text.split(',')]
    if not 1 <= len(names) <= 4 or not all(NAME.fullmatch(n) for n in names):
        return []
    return names


EA_IMAGES = ('eadesktop.exe', 'eacefsubprocess.exe')


def ref_kind(text, image):
    names = ref_parse(text)
    leaf = re.split(r'[\\/]', image)[-1].lower()
    if not names or not image or leaf in EA_IMAGES:
        return 0
    for i, n in enumerate(names):
        if n.lower() == leaf:
            return 2 if i else 1
    return 0


# ---- the header, compiled ---------------------------------------------------------------------------------------------------
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned short WCHAR;
#include "ios_cef_images.h"

static int wide( WCHAR *out, const char *s ) { int n = 0; while (s[n]) { out[n] = (WCHAR)(unsigned char)s[n]; n++; } out[n] = 0; return n; }

int main( int argc, char **argv )
{
    char line[4096];
    (void)argc; (void)argv;
    /* one test per input line: "L<list>" prints the count and the names ("Z" alone: the NULL list), "K<list>\t<image>" the kind, "N<list>" the browser name */
    while (fgets( line, sizeof(line), stdin ))
    {
        size_t n = strlen( line );
        if (n && line[n - 1] == '\n') line[--n] = 0;
        if (line[0] == 'L' || line[0] == 'Z')
        {
            const char *start[IOS_CEF_IMAGES_MAX];
            size_t len[IOS_CEF_IMAGES_MAX];
            int i, k = ios_cef_list_parse( line[0] == 'Z' ? NULL : line + 1, start, len );
            printf( "%d", k );
            for (i = 0; i < k; i++) printf( " %.*s", (int)len[i], start[i] );
            printf( "\n" );
        }
        else if (line[0] == 'K')
        {
            char *tab = strchr( line, '\t' );
            WCHAR img[600];
            int len;
            if (!tab) { printf( "?\n" ); continue; }
            *tab = 0;
            len = wide( img, tab + 1 );
            printf( "%d\n", ios_cef_listed_kind( line + 1, img, (size_t)len ) );
        }
        else if (line[0] == 'N')
        {
            char name[IOS_CEF_NAME_MAX + 1];
            int ok = ios_cef_browser_name( line + 1, name, sizeof(name) );
            printf( "%d %s\n", ok, name );
        }
    }
    return 0;
}
'''


def run_header(exe, lines):
    out = subprocess.run([str(exe)], input='\n'.join(lines) + '\n', capture_output=True, text=True,
                         env=dict(os.environ, ASAN_OPTIONS='symbolize=0'))
    return out.returncode, out.stdout.split('\n')[:-1], out.stderr


with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    c = tmp / 'h.c'
    c.write_text(harness)
    exe = tmp / 'h'
    r = subprocess.run(['cc', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-I', str(root / 'build/ntdll-unix'),
                        str(c), '-o', str(exe)], capture_output=True, text=True)
    check(r.returncode == 0, 'ios_cef_images.h compiles warning-free on the host (-Wall -Wextra -Werror, ASan and UBSan)', r.stderr[-2000:])
    if r.returncode == 0:
        lists = ['x-plane.exe', 'x-plane.exe,cefclient.exe', ' X-Plane.EXE , cefclient.exe ', '\tx-plane.exe\t,\tcefclient.exe\t', 'a.exe', 'A.EXE',
                 '.exe', 'x-plane', 'x-plane.exe,', ',x-plane.exe', 'x-plane.exe,,cefclient.exe', 'a.exe,b.exe,c.exe,d.exe', 'a.exe,b.exe,c.exe,d.exe,e.exe',
                 'dir\\x-plane.exe', 'dir/x-plane.exe', 'x plane.exe', 'x-plane.exe;cefclient.exe', 'x-plane.exe cefclient.exe', 'x-plane.exe,   ',
                 '   ', '', 'x-plane.exe.bak', 'x-plane.exeé', 'é.exe', 'x' * 60 + '.exe',
                 'x' * 61 + '.exe', 'x-plane.exe' + ' ' * 500, 'x-plane.exe' + ' ' * 501, 'x-plane.exe,' + 'y' * 100 + '.exe' + ' ' * 380, 'x-plane.ex', 'x-plane.exe=1', '*.exe', 'x-plane.exe,cefclient.exe,handler.exe']
        rc, got, err = run_header(exe, ['L' + x for x in lists] + ['Z'])
        want = []
        for x in lists + ['']:
            names = ref_parse(x)
            want.append(' '.join([str(len(names))] + names))
        check(rc == 0 and got == want, 'the list grammar: 1 to 4 plain .exe names, blanks around a name ignored, anything else lists nothing (NULL too)',
              (rc, err[-400:], [(l, g, w) for l, g, w in zip(lists, got, want) if g != w]))

        # which image is what
        images = ['C:\\Program Files (x86)\\Steam\\steamapps\\common\\X-Plane 12\\X-Plane.exe',
                  '\\??\\c:\\program files (x86)\\steam\\steamapps\\common\\x-plane 12\\X-PLANE.EXE', 'x-plane.exe', '/x/y/x-plane.exe',
                  'C:\\Program Files (x86)\\Steam\\steamapps\\common\\X-Plane 12\\Resources\\dlls\\64\\cef\\win\\cefclient.exe',
                  'C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EADesktop.exe',
                  'C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EACefSubProcess.exe',
                  'C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe', 'C:\\Games\\GTA5.exe', 'C:\\x\\Not-X-Plane.exe',
                  'C:\\x\\X-Plane.exe.bak', 'C:\\x\\x-plane.ex', 'C:\\x\\cefclient.exe.old', 'C:\\x\\handler.exe', '', 'x-plane.exe\\', '\\x-plane.exe']
        klists = ['x-plane.exe,cefclient.exe', 'x-plane.exe', 'cefclient.exe,x-plane.exe', ' X-PLANE.exe , CefClient.exe ', 'eadesktop.exe,eacefsubprocess.exe',
                  'x-plane.exe,eadesktop.exe', 'x-plane,cefclient.exe', '', 'a.exe,b.exe,c.exe,d.exe,x-plane.exe']
        cases = [(lst, img) for lst in klists for img in images]
        rc, got, err = run_header(exe, ['K%s\t%s' % c for c in cases])
        want = [str(ref_kind(*c)) for c in cases]
        check(rc == 0 and got == want, 'which program is the browser (the first name), a helper (another) or neither (%d list and image pairs)' % len(cases),
              (rc, err[-400:], [(c, g, w) for c, g, w in zip(cases, got, want) if g != w][:5]))
        by = {c: g for c, g in zip(cases, got)}
        xp, cef = images[0], images[4]
        check(by[('x-plane.exe,cefclient.exe', xp)] == '1' and by[('x-plane.exe,cefclient.exe', cef)] == '2'
              and by[('cefclient.exe,x-plane.exe', xp)] == '2' and by[('x-plane.exe', cef)] == '0'
              and by[('x-plane.exe,cefclient.exe', images[5])] == '0' and by[('eadesktop.exe,eacefsubprocess.exe', images[5])] == '0'
              and by[('eadesktop.exe,eacefsubprocess.exe', images[6])] == '0' and by[('x-plane.exe,eadesktop.exe', images[5])] == '0',
              "X-Plane.exe is the browser, cefclient.exe its helper, the EA app's programs are never taken from a list, the first name decides")
        rc, got, err = run_header(exe, ['N' + x for x in ['x-plane.exe,cefclient.exe', ' X-Plane.EXE ,a.exe', 'x-plane', '', 'x' * 60 + '.exe']])
        check(rc == 0 and got == ['1 x-plane.exe', '1 X-Plane.EXE', '0 ?', '0 ?', '1 ' + 'x' * 60 + '.exe'],
              'the browser name for the log: the first name as written, "?" for a list that lists nothing', (rc, got, err[-300:]))

        # differential run: random strings against the reference
        rnd = random.Random(20261118)
        alphabet = ['a', 'x', 'X', '-', '_', '.', 'e', 'E', 'xe', '.exe', '.EXE', ',', ',', ' ', '\t', '\\', '/', ';', '1', 'x-plane', 'cefclient']
        fuzz = []
        for _ in range(12000):
            fuzz.append(''.join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 14))))
        bases = ['x-plane.exe,cefclient.exe', ' a.exe , B.EXE ', 'a.exe,b.exe,c.exe,d.exe', 'x-plane.exe', '\tx.exe\t,y.exe']
        for _ in range(8000):   # one or two edits of a valid list: most stay valid, the rest are the interesting near misses
            f = list(rnd.choice(bases))
            for _ in range(rnd.randint(0, 2)):
                i = rnd.randrange(len(f))
                f[i:i + rnd.choice((0, 1))] = rnd.choice(('', rnd.choice(alphabet), ',', ' '))
            fuzz.append(''.join(f))
        fuzz = [f for f in fuzz if f]   # an empty line is the empty list, tested above
        rc, got, err = run_header(exe, ['L' + f for f in fuzz])
        want = [' '.join([str(len(ref_parse(f)))] + ref_parse(f)) for f in fuzz]
        bad = [(f, g, w) for f, g, w in zip(fuzz, got, want) if g != w]
        valid = sum(1 for w in want if not w.startswith('0'))
        check(rc == 0 and not bad and valid > 500, 'a differential run of %d random strings agrees with the reference (%d of them are valid lists)' % (len(fuzz), valid),
              (rc, err[-300:], bad[:5]))
        kf = [(random.Random(i).choice(['x-plane.exe,cefclient.exe', 'a.exe, b.exe', 'x-plane.exe']), random.Random(i * 7).choice(images)) for i in range(300)]
        rc, got, err = run_header(exe, ['K%s\t%s' % c for c in kf])
        check(rc == 0 and got == [str(ref_kind(*c)) for c in kf], 'kinds agree with the reference on 300 more pairs')

# ---- process_ios.c: cef_one_kind and the browser's command line ----------------------------------------------------------
proc_enum = proc[proc.index('enum { CEF_ONE_NOT = 0'):]
proc_enum = proc_enum[:proc_enum.index(';') + 1] + '\n'
sc_enum = proc[proc.index('enum { SC_NOT_HELPER = 0'):]
sc_enum = sc_enum[:sc_enum.index('\n\n')] + '\n'
proc_helpers = sc_enum + proc_enum + ''.join(function(proc, sig) for sig in (
    'static int sc_switch_end(', 'static int cef_one_kind(', 'static int sc_extra_js_flags(', 'static int sc_browser_cmdline('))

proc_harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned short WCHAR;
#include "ios_cef_images.h"
''' + proc_helpers + r'''
static WCHAR wbuf[3][4096];
static WCHAR *w( int i, const char *s, int *len )
{
    int k;
    for (k = 0; s[k]; k++) wbuf[i][k] = (WCHAR)(unsigned char)s[k];
    wbuf[i][k] = 0;
    *len = k;
    return wbuf[i];
}
static char outbuf[8192];
static const char *a( WCHAR *p, int n ) { int k; for (k = 0; k < n; k++) outbuf[k] = (char)p[k]; outbuf[n] = 0; return outbuf; }

/* one case per input line: <list>\t<image>\t<command line>\t<extra flags> -> the kind and, for a browser, the rewritten line */
int main( void )
{
    char line[8192];
    while (fgets( line, sizeof(line), stdin ))
    {
        char *f[4] = { line, NULL, NULL, NULL };
        int i, n, il, cl, kind, how = 0;
        WCHAR *wi, *wc, out[8192];
        size_t len = strlen( line );

        if (len && line[len - 1] == '\n') line[--len] = 0;
        for (i = 1; i < 4; i++)
        {
            char *tab = strchr( f[i - 1], '\t' );
            if (!tab) break;
            *tab = 0;
            f[i] = tab + 1;
        }
        wi = w( 0, f[1], &il );
        wc = w( 1, f[2], &cl );
        kind = cef_one_kind( f[0][0] ? f[0] : NULL, wi, il, wc, cl );
        printf( "%d", kind );
        if (kind == CEF_ONE_BROWSER)
        {
            n = sc_browser_cmdline( wc, cl, 1, f[3] && f[3][0] ? f[3] : NULL, out, 8192, &how );
            printf( "\t%s", n < 0 ? "-1" : a( out, n ) );
        }
        printf( "\n" );
    }
    return 0;
}
'''

with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    c = tmp / 'p.c'
    c.write_text(proc_harness)
    exe = tmp / 'p'
    r = subprocess.run(['cc', '-std=gnu11', '-O1', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-fsanitize=address,undefined',
                        '-fno-sanitize-recover=all', '-I', str(root / 'build/ntdll-unix'), str(c), '-o', str(exe)], capture_output=True, text=True)
    check(r.returncode == 0, "process_ios.c's cef_one_kind and the browser's command-line builder compile on the host", r.stderr[-2000:])
    if r.returncode == 0:
        XP = 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\X-Plane 12\\X-Plane.exe'
        CEF = 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\X-Plane 12\\Resources\\dlls\\64\\cef\\win\\cefclient.exe'
        EAD = 'C:\\Program Files\\Electronic Arts\\EA Desktop\\13.813.1.6327\\EA Desktop\\EADesktop.exe'
        LIST = 'x-plane.exe,cefclient.exe'
        # the log's own lines (MX3R0X 8858 and 17434): the browser's command line is its path alone; the helper's starts --type=gpu-process
        xp_cl = '"%s"' % XP
        gpu_cl = ('"%s" --type=gpu-process --no-sandbox --user-agent-product=X-Plane/12.4.4-r2 --lang=en-US '
                  '--user-data-dir="C:\\Program Files (x86)\\Steam\\steamapps\\common\\X-Plane 12\\Output\\cache"' % CEF)
        rows = [(LIST, XP, xp_cl, ''), (LIST, CEF, gpu_cl, ''), (LIST, CEF, '"%s"' % CEF, ''), (LIST, XP, '"%s" --type=renderer' % XP, ''),
                (LIST, XP, '"%s" --no-type=x' % XP, ''), ('', XP, xp_cl, ''), (LIST, EAD, '"%s"' % EAD, ''), (LIST, EAD, '"%s" --type=gpu-process' % EAD, ''),
                ('x-plane,cefclient.exe', XP, xp_cl, ''), ('cefclient.exe', XP, xp_cl, ''), (LIST, XP, xp_cl, '--disable-gpu'),
                (LIST, XP, '"%s" --report_crash' % XP, ''), (LIST, 'C:\\Games\\GTA5.exe', '"C:\\Games\\GTA5.exe" --type=x', ''),
                (LIST, 'C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe', 'x --type=renderer', ''),
                (LIST, 'C:\\Program Files (x86)\\Steam\\bin\\cef\\cef.win7x64\\steamwebhelper.exe', 'x --type=renderer', ''),
                (LIST, XP, '"%s" --single-process --disable-features=Foo,Bar --js-flags=--max-old-space-size=512' % XP, '')]
        out = subprocess.run([str(exe)], input='\n'.join('\t'.join(r_) for r_ in rows) + '\n', capture_output=True, text=True,
                             env=dict(os.environ, ASAN_OPTIONS='symbolize=0'))
        got = [l.split('\t', 1) for l in out.stdout.split('\n')[:-1]]
        kinds = [g[0] for g in got]
        check(out.returncode == 0 and kinds == ['1', '2', '0', '2', '1', '0', '0', '0', '0', '0', '1', '1', '0', '0', '0', '1'],
              'X-Plane.exe without --type= is the browser; --type= children of either listed program are refused; a helper without --type= is left alone; '
              'nothing is taken without a valid list, from the EA app, Social Club, Steam or a game', (out.returncode, out.stderr[-400:], kinds))
        if len(got) == len(rows):
            check(got[0][1] == xp_cl + ' --disable-features=PartitionAllocBackupRefPtr --single-process --js-flags=--jitless --no-proxy-server',
                  "the browser's command line on the log's own line: --single-process, BackupRefPtr off, --jitless, --no-proxy-server", got[0])
            check(got[10][1].endswith(' --no-proxy-server --disable-gpu'), 'env.MADEIRA_CEF_FLAGS is appended verbatim', got[10])
            check(got[11][1].startswith(xp_cl + ' --report_crash --disable-features=PartitionAllocBackupRefPtr --single-process'),
                  "the program's own switch (--report_crash, X-Plane's relaunch after a crash) stays in front of the added ones", got[11])
            check(got[15][1] == ('"%s" --single-process --disable-features=PartitionAllocBackupRefPtr,Foo,Bar --js-flags=--max-old-space-size=512 --no-proxy-server' % XP),
                  "a --single-process, a --disable-features= list and a --js-flags= the program already has are kept (BRP spliced in front, no second --single-process, no --jitless over its flags)",
                  got[15])

# ---- the sources: where the key is read ---------------------------------------------------------------------------------------
spawn_start = proc.index("/* madeira-bcd: a program's own Chromium (X-Plane 12) -- see cef_one_kind.")
spawn = proc[spawn_start:proc.index('#endif', spawn_start)]
ea_spawn_start = proc.index("/* madeira-bcd: the EA app's Chromium -- see ea_cef_kind.")
check(ea_spawn_start < spawn_start and proc.count('getenv( "MADEIRA_CEF_POOL_IMAGES" )') == 1 and 'getenv( "MADEIRA_CEF_POOL_IMAGES" )' in spawn,
      "process_ios.c reads the key once, in a block after the EA app's block (which is untouched by it)")
check(spawn.index('getenv( "MADEIRA_CEF_POOL_IMAGES" )') < spawn.index('cef_one_kind(')
      and 'kind = cef_one_kind( list,' in spawn and 'if (list && list[0])' in spawn and spawn.count('return STATUS_ACCESS_DENIED;') == 1
      and 'if (kind == CEF_ONE_CHILD)' in spawn and 'if (kind == CEF_ONE_BROWSER)' in spawn
      and spawn.index('if (kind == CEF_ONE_CHILD)') < spawn.index('return STATUS_ACCESS_DENIED;') < spawn.index('if (kind == CEF_ONE_BROWSER)'),
      'the block looks at the image only when the variable is a valid list; a child is refused with STATUS_ACCESS_DENIED, a browser is rewritten')
check('getenv( "MADEIRA_CEF_FLAGS" )' in spawn and proc.count('getenv( "MADEIRA_CEF_FLAGS" )') == 1,
      'env.MADEIRA_CEF_FLAGS is read in that block only')
check(proc.count('#include "ios_cef_images.h"') == 1 and native.count('#include "ios_cef_images.h"') == 1, 'both engine files include the header once')
check(native.count('getenv( "MADEIRA_CEF_POOL_IMAGES" )') == 1 and 'getenv( "MADEIRA_CEF_POOL_IMAGES" )' in function(native, 'static const char *ios_cef_list(void)'),
      'virtual_ios.c reads the key once, in ios_cef_list')
others = sorted(p.name for p in (root / 'build/ntdll-unix').glob('*') if p.suffix in ('.c', '.h', '.m')
                and p.name not in ('virtual_ios.c', 'process_ios.c', 'ios_cef_images.h') and 'MADEIRA_CEF_POOL_IMAGES' in p.read_text())
check(not others, 'no other engine file names the key', others)
app_users = sorted(p.name for p in (root / 'app/Madeira').glob('*.swift') if 'MADEIRA_CEF_POOL_IMAGES' in p.read_text())
# Branch steam-client (2026-11-21, M1): Steam for Windows' session settings (SteamDesktop.swift, SteamDesktopProfile.lines) name it for
# steamwebhelper.exe, in that session's own file only (tests/host/check-steam-desktop.py); no launcher and no other game's start sets it.
check(app_users == ['AutoTuneRules.generated.swift', 'ConfigCatalog.generated.swift', 'GameRecommendationsList.swift', 'SteamDesktop.swift'],
      'in the app only the catalog, the list policy, the auto-tune rule table and the Steam for Windows session\'s settings name the key '
      '(no launcher sets it)', app_users)
steam_desktop = read('app/Madeira/SteamDesktop.swift')
check(steam_desktop.count('"env.MADEIRA_CEF_POOL_IMAGES = steamwebhelper.exe"') == 1 and '"env.MADEIRA_SC_PA_POOLS = 2"' in steam_desktop
      and 'MADEIRA_CEF_POOL_IMAGES' not in read('app/Madeira/BCDSteamDesktopLive.swift') and 'MADEIRA_CEF_POOL_IMAGES' not in read('app/Madeira/BCDLaunchers.swift'),
      'there it is one line of the session\'s settings, for steamwebhelper.exe, with layout 2 beside it')

# ---- the catalog ------------------------------------------------------------------------------------------------------------------
gen = read('build/tools/gen-config-catalog.py')
catalog = read('app/Madeira/ConfigCatalog.generated.swift')
for key in ('MADEIRA_CEF_POOL_IMAGES', 'MADEIRA_CEF_FLAGS'):
    m = re.search(r'ConfigOption\(key: "env\.%s", title: "([^"]+)", kind: \.text, defaultValue: "", category: "Memory & JIT pool", note: "([^"]+)", '
                  r'choices: \[\], sources: \[([^\]]*)\]\)' % key, catalog)
    check(bool(m) and '"env.%s"' % key in gen and len(m.group(2)) > 80, 'the settings catalog documents env.%s (text, empty default, Memory & JIT pool)' % key)
    if m:
        want = ('"build/ntdll-unix/process_ios.c", "build/ntdll-unix/virtual_ios.c"' if key.endswith('IMAGES') else '"build/ntdll-unix/process_ios.c"')
        check(m.group(3) == want, 'its sources are the files that read it', m.group(3))

# ---- the downloaded-list policy ------------------------------------------------------------------------------------------------
policy = read('app/Madeira/GameRecommendationsList.swift')
allowed = policy.split('static let allowedEnvNames')[1].split('\n    ]\n')[0]
check('"MADEIRA_CEF_POOL_IMAGES"' in allowed and 'MADEIRA_CEF_FLAGS' not in policy,
      'a downloaded list may set env.MADEIRA_CEF_POOL_IMAGES and may not set env.MADEIRA_CEF_FLAGS (no switch of a list reaches a program command line)')
case_start = policy.index('case "MADEIRA_CEF_POOL_IMAGES":')
case_body = policy[case_start:policy.index('case "MADEIRA_WG_VPROC":', case_start)]
check('MADEIRA_CEF_POOL_IMAGES is 1 to 4 program names ending in .exe' in case_body and '(1...4).contains(names.count)' in case_body
      and 'value.utf8.count <= 511' in case_body,
      'the policy checks the value: 1 to 4 names ending in .exe, at most 511 characters')

fuzz_values = ['x-plane.exe', 'x-plane.exe,cefclient.exe', ' x-plane.exe , cefclient.exe ', 'x-plane', 'x-plane.exe,', ',', '', 'a.exe,b.exe,c.exe,d.exe',
               'a.exe,b.exe,c.exe,d.exe,e.exe', 'dir\\x.exe', 'x y.exe', 'x-plane.exe;cefclient.exe', 'x' * 60 + '.exe', 'x' * 61 + '.exe', '.exe', 'a.exe',
               'é.exe', 'x-plane.EXE', 'x-plane.exe\t,\tcefclient.exe', 'x-plane.exe' + ' ' * 500, 'x-plane.exe' + ' ' * 499]
rnd = random.Random(77)
alphabet = ['a', 'x', '-', '_', '.', 'e', 'xe', '.exe', '.EXE', ',', ',', ' ', '\t', '\\', ';', '1', 'x-plane', 'cefclient']
fuzz_values += [''.join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 14))) for _ in range(4000)]
swiftc = shutil.which('swiftc')
if swiftc:
    swift_src = '''import Foundation
enum RecommendationsError: Error { case policy(String) }
func policyCheck(_ name: String, _ value: String, _ path: String) throws {
    switch name {
    %s
    default:
        break
    }
}
while let line = readLine(strippingNewline: true) {
    do { try policyCheck("MADEIRA_CEF_POOL_IMAGES", line, "list"); print("1") } catch { print("0") }
}
''' % case_body.rstrip()
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        (tmp / 'main.swift').write_text(swift_src)
        r = subprocess.run([swiftc, str(tmp / 'main.swift'), '-o', str(tmp / 'pol')], capture_output=True, text=True)
        check(r.returncode == 0, "the policy's own case for the key compiles with swiftc", r.stderr[-1500:])
        if r.returncode == 0:
            vals = [v for v in fuzz_values if '\n' not in v]
            out = subprocess.run([str(tmp / 'pol')], input='\n'.join(vals) + '\n', capture_output=True, text=True)
            want = ['1' if ref_parse(v) else '0' for v in vals]
            got = out.stdout.split('\n')[:-1]
            bad = [(v, g, w) for v, g, w in zip(vals, got, want) if g != w]
            check(out.returncode == 0 and len(got) == len(vals) and not bad,
                  "the policy accepts exactly the values the engine's list grammar takes (%d values, %d accepted)" % (len(vals), want.count('1')), (out.returncode, bad[:5]))
elif require_swift:
    raise SystemExit('Swift compiler required for the policy test')
else:
    print('SKIP: swiftc unavailable locally; macOS CI runs the policy test')

# ---- lists: a row that carries the key carries layout 2 too ----------------------------------------------------------------
recs = json.loads(read('recommended/recommendations.json'))


def row_config_lines(row):
    """A row's config lines: a plain string, or {"line": ..., "minBuild": ...}; the "#" comments are not config."""
    out = []
    for entry in row.get('config', []):
        line = entry.get('line') if isinstance(entry, dict) else entry
        if isinstance(line, str) and line.strip() and not line.strip().startswith('#'):
            out.append(line.strip())
    return out


def rows_with_key(rules):
    """(row id, config lines) of every row that carries the images key."""
    found = []
    for row in rules:
        lines = row_config_lines(row)
        if any(l.startswith('env.MADEIRA_CEF_POOL_IMAGES') for l in lines):
            found.append((row.get('id', '?'), lines))
    return found


carrying = rows_with_key(recs['rules'])
_row_ok = {'id': 'ok', 'config': ['# comment', 'env.MADEIRA_SC_PA_POOLS = 2', {'line': 'env.MADEIRA_CEF_POOL_IMAGES = a.exe,b.exe', 'minBuild': 671}]}
_row_bad = {'id': 'bad', 'config': [{'line': 'env.MADEIRA_CEF_POOL_IMAGES = a.exe'}]}
_found = rows_with_key([_row_ok, _row_bad, {'id': 'other', 'config': ['x = 1']}, {'id': 'none'}])
check([f[0] for f in _found] == ['ok', 'bad'] and 'env.MADEIRA_SC_PA_POOLS = 2' in _found[0][1] and 'env.MADEIRA_SC_PA_POOLS = 2' not in _found[1][1],
      'the row reader finds the rows that carry the key (plain and {"line"} entries, comments skipped) and tells those with layout 2 from those without', _found)
check(len(recs['rules']) > 40, 'the list rows are read (%d rows)' % len(recs['rules']))
check(all('env.MADEIRA_SC_PA_POOLS = 2' in lines for _, lines in carrying),
      'every list row that carries env.MADEIRA_CEF_POOL_IMAGES also carries env.MADEIRA_SC_PA_POOLS = 2 (%d rows today)' % len(carrying), carrying)
for _, lines in carrying:
    for l in lines:
        if l.startswith('env.MADEIRA_CEF_POOL_IMAGES'):
            check(bool(ref_parse(l.split('=', 1)[1].strip())), 'a list row\'s value is a valid list: ' + l)

# ---- auto-tune -----------------------------------------------------------------------------------------------------------------------
rules_src = read('build/tools/autotune_rules.py')
rule_start = rules_src.index('"id": "xplane-cef-pools"')
rule = rules_src[rule_start:rules_src.index('"id": "xplane-vulkan-gl-bridge-sharing"', rule_start)]
check('"lines": ["env.MADEIRA_SC_PA_POOLS = 2", "env.MADEIRA_CEF_POOL_IMAGES = x-plane.exe,cefclient.exe"]' in rule and '"noRetry": True' in rule
      and '[soft-pool] GRANTED soft 0x7800000000' in rule and 'proc=x-plane.exe' in rule and 'status=c000000d' in rule
      and '"none": [s("[madeira-env] game MADEIRA_CEF_POOL_IMAGES=")]' in rule,
      'the rule xplane-cef-pools: the two lines, the soft grant on the first pool and the failed commit of x-plane.exe as signals, not again once the key is in the file')
fixture = root / 'tests/fixtures/autotune/x-plane-12-cef-pools-2026-11-18'
fx_log = fixture.with_suffix('.log').read_text() if fixture.with_suffix('.log').exists() else ''
expect = json.loads(fixture.with_suffix('.expect').read_text()) if fixture.with_suffix('.expect').exists() else {}
check(expect.get('next') == ['env.MADEIRA_SC_PA_POOLS = 2', 'env.MADEIRA_CEF_POOL_IMAGES = x-plane.exe,cefclient.exe']
      and expect.get('matched') == ['xplane-cef-pools'] and expect.get('outcome') == 'crashed',
      "the fixture cut from MX3R0X proposes the two lines (check-autotune.py replays it whole and in chunks)", expect)
check('[soft-pool] GRANTED soft 0x7800000000 (base returned 0x77ffff0000, size=0x400010000)' in fx_log
      and re.search(r'\[nt-fail\] #\d+ NtAllocateVirtualMemory status=c000000d proc=x-plane\.exe .* type=0x1000 ', fx_log),
      "the fixture still has the two lines of the real log the rule reads")

workflow = read('.github/workflows/build-ipa.yml')
check('run: python3 tests/host/check-cef-pool-images.py' in workflow, 'CI runs this check')
check(workflow.index('run: python3 tests/host/check-ea-cef.py') < workflow.index('run: python3 tests/host/check-cef-pool-images.py'),
      'CI runs it after the EA app\'s check')

print('PASS: check-cef-pool-images' if not failures else 'FAIL: %d check(s) failed' % failures)
sys.exit(1 if failures else 0)
