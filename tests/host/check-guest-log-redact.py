#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-guest-log-redact.py: the [guest-log] mirror never copies the player's account names; no Wine runs.

Rockstar's launcher.log names the player's accounts ("Steam user: <name>", "Saved profile for <name>", "Signed in online to
profile: <name>", "Gamer:"<name>"[handle=SC <number>]"), and with env.MADEIRA_GUEST_LOG = all the mirror copied them into the
session log a Send Log uploads (agent R, round 630).

1. Applies the mirror's patches in the workflow's order (mirror, tag, service, Ren'Py, trace) and then this one to a copy of the
   wine checkout's dlls/ntdll/unix/file.c ($WINE_SRC, ./wine, or ~/madeira-bcd/wine): once, then "already patched"; the line
   print is the only call site that changes.
2. Compiles the patched helper and checks: each account line is cut right after its word (nothing of the name is left), the
   earliest word wins, other lines are untouched, and a line shorter than a word cannot match.
A host without a wine checkout says so and passes (CI and the Mac run it).
"""
from pathlib import Path
import os, re, subprocess, sys, tempfile, shutil

root = Path(__file__).resolve().parents[2]
chain = ["patch-wine-guest-log.py", "patch-wine-guest-log-tag.py", "patch-wine-guest-log-service.py",
         "patch-wine-guest-log-renpy.py", "patch-wine-guest-log-trace.py"]
redact = root / "tools/patch-wine-guest-log-redact.py"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the workflow applies it after the trace patch, with this check first",
      "python3 tests/host/check-guest-log-redact.py\n          python3 tools/patch-wine-guest-log-redact.py wine/dlls/ntdll/unix/file.c" in wf
      and wf.index("tools/patch-wine-guest-log-trace.py wine/dlls") < wf.index("tools/patch-wine-guest-log-redact.py wine/dlls"))

candidates = [Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, root / "wine", Path.home() / "madeira-bcd/wine"]
real = next((c / "dlls/ntdll/unix/file.c" for c in candidates if c and (c / "dlls/ntdll/unix/file.c").exists()), None)
if real is None:
    print("skip: no wine checkout here (CI and the Mac run this check)")
    print("PASS: guest-log account names (skipped here)" if ok else "FAILED: guest-log account names")
    sys.exit(0 if ok else 1)

with tempfile.TemporaryDirectory(prefix="madeira-guest-log-redact-") as tmp:
    copy = Path(tmp) / "file.c"
    shutil.copy(real, copy)
    for script in chain:
        r = subprocess.run([sys.executable, str(root / "tools" / script), str(copy)], capture_output=True, text=True)
        check("%s applies (or is already in): %s" % (script, (r.stdout + r.stderr).strip()[-120:]), r.returncode == 0)
    before = copy.read_text()
    r1 = subprocess.run([sys.executable, str(redact), str(copy)], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, str(redact), str(copy)], capture_output=True, text=True)
    after = copy.read_text()
    check("the patch applies once, then says it is already in",
          ("applied to" in r1.stdout or "already patched" in r1.stdout) and "already patched" in r2.stdout, r1.stdout + r1.stderr)
    if "applied to" in r1.stdout:
        check("only the helper and the line print change",
              after.count("ios_guest_log_redact(") == 2 and after.replace("<account name removed>", "").count("[truncated]") == before.count("[truncated]"))
    start = after.index("static int ios_guest_log_redact( const char *line, size_t *shown )")
    helper = after[start:after.index("\n}\n", start) + 3]
    call = after[after.index("int redacted = ios_guest_log_redact("):]
    check("the print ends a cut line with <account name removed>, else as before",
          'redacted ? " <account name removed>" : shown < len ? " [truncated]" : ""' in call[:600])
    code = "#include <stdio.h>\n#include <string.h>\n#include <assert.h>\n" + helper + r'''
static const char *cut( const char *line )
{
    static char out[512];
    size_t shown = strlen( line );
    int hit = ios_guest_log_redact( line, &shown );
    snprintf( out, sizeof(out), "%.*s%s", (int)shown, line, hit ? " <account name removed>" : "" );
    return out;
}
int main( void )
{
    assert( !strcmp( cut( "[00000001]  Steam user: someplayer123" ), "[00000001]  Steam user: <account name removed>" ) );
    assert( !strcmp( cut( "[00028428] Saved profile for someplayer123" ), "[00028428] Saved profile for <account name removed>" ) );
    assert( !strcmp( cut( "[00029026] Signed in online to profile: someplayer123" ),
                     "[00029026] Signed in online to profile: <account name removed>" ) );
    assert( !strcmp( cut( "[DISPLAY] [Main ] [rline_presence] Gamer:\"someplayer123\"[handle=SC 131136383] SIGNED IN" ),
                     "[DISPLAY] [Main ] [rline_presence] Gamer: <account name removed>" ) );
    assert( !strcmp( cut( "presence handle=SC 131136383 online" ), "presence handle=SC  <account name removed>" ) );
    assert( !strcmp( cut( "[ ERROR ] [Main ] [error] * Code:      113 (LAUNCHER_ERR_STEAM_FAILED)" ),
                     "[ ERROR ] [Main ] [error] * Code:      113 (LAUNCHER_ERR_STEAM_FAILED)" ) );
    assert( !strcmp( cut( "Gamer" ), "Gamer" ) && !strcmp( cut( "" ), "" ) );
    puts( "ok: account lines are cut after their word; other lines are untouched" );
    return 0;
}
'''
    src = Path(tmp) / "t.c"
    src.write_text(code)
    exe = Path(tmp) / "t"
    b = subprocess.run([os.environ.get("CC", "cc"), "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-o", str(exe), str(src)],
                       capture_output=True, text=True)
    check("the helper builds: " + b.stderr[-600:], b.returncode == 0)
    if b.returncode == 0:
        t = subprocess.run([str(exe)], capture_output=True, text=True)
        print(t.stdout, end="")
        check("account names never reach the mirror: " + (t.stderr[-400:] or "all cases"), t.returncode == 0)

print("PASS: the [guest-log] mirror keeps account lines up to their word and drops the name" if ok else "FAILED: guest-log account names")
sys.exit(0 if ok else 1)
