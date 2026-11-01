#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_REPORT_HZ (opt-in, round 626, agent H S2; Hollow Knight: Silksong DF0537 = GXX35R, build 625, iPad17,1).

BCD's virtual monitor tells every Windows program 60 Hz, also when the session holds a ProMotion panel at 120 Hz (MAX, uncapped, the
40 cap); a game that paces itself by the monitor's rate then runs its own step for 60. With env.MADEIRA_REPORT_HZ = 1 win32u reports
the rate the app published in BCD_SESSION_HZ (ProMotionIntent.sessionHz: 60 for the 60 and 30 caps, the panel's maximum otherwise)
for the current and registry modes, and lists every size twice, 60 Hz first. Off (the default) the answers are what they were.

Checks: the real functions, cut from build/win32u-unix/sysparams_ios.c (the mode table, ios_mode_at_index, the region between
"hz-test:begin" and "hz-test:end", ios_virtual_enum_display_settings) and compiled on the host against stand-ins, for the key unset,
"0", "yes", "1" with no published rate, "1" with 60 published, "1" with 300 published (out of range): every listed mode and the two
current answers are the size the mode table gives at 60 Hz, as before; "1" with 120 published: twice the modes, 60 Hz first, current
and registry at 120, one log line; the app publishes the rate after the pacing call and logs what Windows is told; the list policy
and the catalog know the key (off); CI runs this check.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
SRC = (root / "build/win32u-unix/sysparams_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def cut(sig):
    """A function's definition (a declaration ending in ';' before any '{' is skipped)."""
    i = SRC.index(sig)
    while SRC.index(";", i) < SRC.index("{", i):
        i = SRC.index(sig, i + 1)
    j = SRC.index("{", i)
    depth = 0
    for k in range(j, len(SRC)):
        if SRC[k] == "{":
            depth += 1
        elif SRC[k] == "}":
            depth -= 1
            if depth == 0:
                return SRC[i:k + 1]
    raise SystemExit("unbalanced " + sig)


table = SRC[SRC.index("static const struct { short w, h; } ios_standard_modes[] ="):]
table = table[:table.index("};") + 2]
modes = cut("static BOOL ios_mode_at_index( UINT index, int *w, int *h )")
region = SRC[SRC.index("/* hz-test:begin"):SRC.index("/* hz-test:end */")]
enum = cut("static BOOL ios_virtual_enum_display_settings( DWORD index, DEVMODEW *devmode, DWORD flags )")
check("the key is read once and only \"1\" with a published rate above 60 (up to 240) turns it on",
      'hz = (on && !strcmp( on, "1" ) && v > 60 && v <= 240) ? v : 60;' in region and "static int hz = -1;" in region)
check("the enumeration starts from that rate and lists through ios_listed_mode",
      "int sw, sh, hz = ios_report_hz();" in enum and "devmode->dmDisplayFrequency = hz;" in enum
      and "ios_listed_mode( index, hz, &size_index, &freq );" in enum and "devmode->dmDisplayFrequency = freq;" in enum
      and "devmode->dmDisplayFrequency = 60;" not in enum)
lib = (root / "app/Madeira/Library.swift").read_text()
check("the app publishes the session's rate right after the pacing call, before the game's own lines",
      'madeira_set_vsync_locked(effectiveFPSMode)\n        // madeira-bcd (round 626, agent H S2)' in lib
      and 'setenv("BCD_SESSION_HZ", String(ProMotionIntent.sessionHz(for: effectiveFPSMode)), 1)' in lib)
pm = (root / "app/Madeira/ProMotionIntent.swift").read_text()
check("the session's rate: 60 for the 60 and 30 caps, the panel's maximum otherwise",
      "static func sessionHz(for mode: Int32) -> Int { (mode == 1 || mode == 3) ? 60 : max(60, panelMaxFPS) }" in pm
      and 'guard let on = getenv("MADEIRA_REPORT_HZ"), String(cString: on) == "1" else { return 60 }' in pm)
_srcs = list((root / "build").rglob("*.c")) + list((root / "app/Madeira").rglob("*.swift")) + list((root / "app/Madeira").rglob("*.m"))
_all = "".join(f.read_text(errors="ignore") for f in _srcs)
_bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
check("nothing else reads the published rate: one getenv (win32u), one setenv (applyEnvironment); the bridge only clears it per launch",
      _all.count('getenv( "BCD_SESSION_HZ" )') + _all.count('getenv("BCD_SESSION_HZ")') == 1
      and _all.count('setenv("BCD_SESSION_HZ"') - _all.count('unsetenv("BCD_SESSION_HZ"') == 1
      and _all.count('"BCD_SESSION_HZ"') == 4   # getenv, setenv, the bridge's per-launch entry and its unsetenv
      and '"BCD_SESSION_HZ",' in _bridge and 'unsetenv("BCD_SESSION_HZ");' in _bridge)
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a list may set the key, 0 or 1", '"MADEIRA_REPORT_HZ",' in pol and 'case "MADEIRA_REPORT_HZ":' in pol)
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the catalog documents it, off by default",
      re.search(r'key: "env\.MADEIRA_REPORT_HZ"[^\n]*kind: \.bool, defaultValue: "0"', cat) is not None)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-display-hz.py" in wf)

HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef unsigned int DWORD; typedef unsigned int UINT; typedef int BOOL; typedef long long INT64;
#define TRUE 1
#define FALSE 0
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define ENUM_CURRENT_SETTINGS ((DWORD)-1)
#define ENUM_REGISTRY_SETTINGS ((DWORD)-2)
#define WINE_ENUM_PHYSICAL_SETTINGS ((DWORD)-3)
#define DM_POSITION 0x20
#define DM_DISPLAYORIENTATION 0x80
#define DM_BITSPERPEL 0x40000
#define DM_PELSWIDTH 0x80000
#define DM_PELSHEIGHT 0x100000
#define DM_DISPLAYFLAGS 0x200000
#define DM_DISPLAYFREQUENCY 0x400000
#define DMDO_DEFAULT 0
#define ERROR_NO_MORE_FILES 18
typedef struct { int x, y; } POINTL;
typedef struct { DWORD dmFields, dmDisplayOrientation, dmBitsPerPel, dmDisplayFrequency, dmDisplayFlags, dmPelsWidth, dmPelsHeight; POINTL dmPosition; } DEVMODEW;
static int g_lines, g_errors;
static int test_dprintf( int fd, const char *fmt, ... ) { (void)fd; if (strstr( fmt, "MADEIRA_REPORT_HZ" )) g_lines++; return 0; }   /* the report line only */
#define dprintf test_dprintf
static void RtlSetLastWin32Error( DWORD e ) { (void)e; g_errors++; }
static int ios_screen_def_w = 2420, ios_screen_def_h = 1668;
static void ios_screen_size( int *w, int *h ) { *w = 2420; *h = 1668; }
''' + table + "\n" + modes + "\n" + region + "\n" + enum + r'''
int main( void )
{
    DWORD i; int listed = 0;
    for (i = 0; i < 400; i++)
    {
        DEVMODEW d; memset( &d, 0, sizeof d );
        if (!ios_virtual_enum_display_settings( i, &d, 0 )) break;
        printf( "%u %ux%u@%u\n", i, d.dmPelsWidth, d.dmPelsHeight, d.dmDisplayFrequency );
        listed++;
    }
    for (i = 0; i < 2; i++)
    {
        DEVMODEW d; memset( &d, 0, sizeof d );
        ios_virtual_enum_display_settings( i ? ENUM_REGISTRY_SETTINGS : ENUM_CURRENT_SETTINGS, &d, 0 );
        printf( "%s %ux%u@%u\n", i ? "REGISTRY" : "CURRENT", d.dmPelsWidth, d.dmPelsHeight, d.dmDisplayFrequency );
    }
    /* the sizes as the mode table gives them, for the expectations */
    for (i = 0; i < 400; i++)
    {
        int w, h;
        if (!ios_mode_at_index( i, &w, &h )) break;
        printf( "SIZE %u %dx%d\n", i, w, h );
    }
    printf( "LISTED %d LINES %d\n", listed, g_lines );
    return 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the enumeration and the mode helpers compile on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            base = {k: v for k, v in os.environ.items() if k not in ("MADEIRA_REPORT_HZ", "BCD_SESSION_HZ", "MADEIRA_EXTENDED_MODES")}

            def run(key, held):
                env = dict(base)
                if key is not None:
                    env["MADEIRA_REPORT_HZ"] = key
                if held is not None:
                    env["BCD_SESSION_HZ"] = held
                out = subprocess.run([str(exe)], capture_output=True, text=True, env=env).stdout.splitlines()
                sizes = [l.split()[2] for l in out if l.startswith("SIZE ")]
                listed = [l.split()[1] for l in out if l[:1].isdigit()]
                cur = [l.split()[1] for l in out if l.startswith(("CURRENT", "REGISTRY"))]
                tail = [l for l in out if l.startswith("LISTED")][0].split()
                return sizes, listed, cur, int(tail[1]), int(tail[3])

            for key, held, label in ((None, None, "unset"), ("0", "120", "0"), ("yes", "120", "\"yes\""), ("1", None, "1, nothing published"),
                                     ("1", "60", "1, 60 published"), ("1", "300", "1, 300 published")):
                sizes, listed, cur, n, lines = run(key, held)
                check("key %s: every listed mode is the mode table's size at 60 Hz (%d modes), current and registry at 60, no line"
                      % (label, n), listed == [s + "@60" for s in sizes] and cur == [sizes[0] + "@60"] * 2 and lines == 0, (listed[:4], cur))
            sizes, listed, cur, n, lines = run("1", "120")
            want = [x for s in sizes for x in (s + "@60", s + "@120")]
            check("key 1 with 120 published: every size twice, 60 Hz first (%d modes), current and registry at 120, one line" % n,
                  listed == want and n == 2 * len(sizes) and cur == [sizes[0] + "@120"] * 2 and lines == 1, (listed[:4], cur, lines))
else:
    print("note: no host C compiler; the enumeration was not run")
print("PASS: reported refresh rate" if ok else "FAILED: reported refresh rate")
sys.exit(0 if ok else 1)
