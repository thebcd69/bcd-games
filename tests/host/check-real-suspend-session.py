#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""MADEIRA_REAL_SUSPEND is read per session (upstream Madeira, ml730 follow-up; build/wineserver/mach_ios.c).

ios_real_suspend_enabled() kept the first value it read for the life of the app process, but the wineserver runs once per session and an
app run holds several (125 archive logs have two or more launches in one run; Megabonk, whose list row sets the key, never got real
suspension when another game ran first, and a game that must not have it inherited it). Now every call reads the environment and logs
when the value changes; WineProcessBridge.m clears the variable when a session ends, as for the other per-game switches, so a later game
starts from its own lines.

The production function is cut from the source and run next to a copy of the old cached function (kept here as the reference):
  - the first session of an app run is identical: the same return values and the same single log line, whether the variable is
    unset or 1 at the first call;
  - the old function keeps the first value (the bug); the new one follows the environment: 0 -> 1 -> 0, one log line per change,
    none for a repeated value, exactly a leading '1' means on;
  - the session end clears the variable (the bridge) and the check for the other per-game switches lists it.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
mach = (root / "build/wineserver/mach_ios.c").read_text()
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


start = mach.index("int ios_real_suspend_enabled(void)\n{")
new_fn = mach[start:mach.index("\n}\n", start) + 3]
old_fn = '''int old_real_suspend_enabled(void)
{
    static int env = -1;
    if (env < 0)
    {
        const char *e = getenv( "MADEIRA_REAL_SUSPEND" );
        env = (e && e[0] == '1') ? 1 : 0;
        fprintf( stderr, "[real-susp] ml730 MADEIRA_REAL_SUSPEND=%d\\n", env );
    }
    return env;
}
'''
check("the function reads the environment on every call and logs only a change", 'getenv( "MADEIRA_REAL_SUSPEND" )' in new_fn
      and "static int env = -1" not in new_fn and "__atomic_exchange_n( &logged, env, __ATOMIC_RELAXED )" in new_fn)
end = bridge[bridge.index("static void wine_session_end(void)\n{"):]
end = end[:end.index("wineserver_stop();")]
check("the session end clears the variable, with the other per-game switches", 'unsetenv("MADEIRA_REAL_SUSPEND");' in end
      and end.index('unsetenv("MADEIRA_ANON_LDADDAL32");') < end.index('unsetenv("MADEIRA_REAL_SUSPEND");') < end.index("unsetenv(\"MADEIRA_NTDLL_XSEC\");"))

cc = shutil.which("cc") or shutil.which("clang")
if not cc:
    print("note: no host C compiler; the function was not run")
else:
    code = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
''' + new_fn + old_fn + r'''
int main( int argc, char **argv )
{
    int which = atoi( argv[1] );          /* 0 = new, 1 = old */
    const char *seq = argv[2];            /* u = unset, 1 = "1", 0 = "0", x = "yes" , z = "" , a = "11" */
    for (; *seq; seq++)
    {
        int v;
        switch (*seq)
        {
        case 'u': unsetenv( "MADEIRA_REAL_SUSPEND" ); break;
        case '1': setenv( "MADEIRA_REAL_SUSPEND", "1", 1 ); break;
        case '0': setenv( "MADEIRA_REAL_SUSPEND", "0", 1 ); break;
        case 'x': setenv( "MADEIRA_REAL_SUSPEND", "yes", 1 ); break;
        case 'z': setenv( "MADEIRA_REAL_SUSPEND", "", 1 ); break;
        case 'a': setenv( "MADEIRA_REAL_SUSPEND", "11", 1 ); break;
        case 'c': break;                  /* call again, no change */
        }
        v = which ? old_real_suspend_enabled() : ios_real_suspend_enabled();
        printf( "%d", v );
    }
    printf( "\n" );
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "t.c"
        exe = Path(d) / "t"
        c.write_text(code)
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the production function compiles on the host", r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            def run(which, seq):
                env = {k: v for k, v in os.environ.items() if k != "MADEIRA_REAL_SUSPEND"}
                p = subprocess.run([str(exe), str(which), seq], capture_output=True, text=True, env=env, timeout=30)
                return p.stdout.strip(), [l for l in p.stderr.splitlines() if l.startswith("[real-susp]")]

            for seq, what in (("ucccu", "unset at the first call"), ("1ccc1", "1 at the first call"), ("0ccc", "0 at the first call"),
                              ("xcz", "a word that is not 1"), ("acc", "11 (a leading 1)")):
                a, la = run(0, seq)
                b, lb = run(1, seq)
                check("the first session is identical to the cached function (%s): same results, same log" % what,
                      a == b and la == lb and len(la) == 1, (a, b, la, lb))
            a, la = run(0, "ucc1cc0cu")
            check("the new function follows the environment: off, on, off, off; one log line per change",
                  a == "000111000" and la == ["[real-susp] ml730 MADEIRA_REAL_SUSPEND=0", "[real-susp] ml730 MADEIRA_REAL_SUSPEND=1",
                                              "[real-susp] ml730 MADEIRA_REAL_SUSPEND=0"], (a, la))
            a, la = run(1, "ucc1cc0cu")
            check("the old function kept the first value (the bug this fixes)", a == "000000000" and len(la) == 1, (a, la))
            a, la = run(0, "1cuc1c")
            check("a second session that starts with 1 after a session without it gets it, and the reverse", a == "110011" and len(la) == 3, (a, la))

check("the session-switches check lists the variable", '"MADEIRA_REAL_SUSPEND"' in (root / "tests/host/check-session-switches.py").read_text())
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-real-suspend-session.py" in wf)
print("PASS: REAL_SUSPEND per session" if ok else "FAILED: REAL_SUSPEND per session")
sys.exit(0 if ok else 1)
