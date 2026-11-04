#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A process the storm breaker ends while it is exiting ends with the status it chose (round 630, agent T1 S30).

Marvel's Spider-Man Remastered and Miles Morales (569HA2), Palworld, RoboCop and Ride call ExitProcess(0); a fault storm in their
DLL shutdown then made the storm breaker (signal_arm64_ios.c ios_storm_end_child_only) end the process with c0000005, the Dock
printed "ended with status 0xc0000005" and auto-tune read a quit as a crash. NtTerminateProcess( NULL, code ), ExitProcess's first
step, now records the code (build/ntdll-unix/process_ios.c ios_exiting_record), and the storm breaker reports it.

Compiles the production table and checks: a recorded process is found with its code, an unknown one (and NULL) is not, the ninth
record evicts the oldest; plus source checks: the record is the first thing NtTerminateProcess does for a NULL handle, and the storm
breaker passes the recorded code (c0000005 only for a process that was not exiting).
"""
from pathlib import Path
import os, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
proc = (root / "build/ntdll-unix/process_ios.c").read_text()
sig = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if (detail and not cond) else ""))
    ok = ok and bool(cond)


start = proc.index("static struct { void *peb; LONG code; } ios_exiting_tab[8];")
end = proc.index("NTSTATUS WINAPI NtTerminateProcess( HANDLE handle, LONG exit_code )")
table = proc[start:proc.rindex("#endif", start, end)]
body = proc[end:end + 400]
check("NtTerminateProcess records a NULL handle's code first",
      "#ifdef WINE_IOS\n    if (!handle) ios_exiting_record( NtCurrentTeb()->Peb, exit_code );" in body)
storm = sig[sig.index("static int ios_storm_end_child_only("):]
storm = storm[:storm.index("\n}\n")]
check("the storm breaker ends an exiting process with its own status, any other with c0000005",
      "int exiting = ios_exiting_status( peb, &chosen );" in storm
      and "ios_end_child_process_async( peb, exiting ? (int)chosen : (int)STATUS_ACCESS_VIOLATION )" in storm)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-exiting-status.py" in wf)

code = "#include <stdio.h>\n#include <assert.h>\ntypedef int LONG;\n" + table + r'''
int main( void )
{
    int a, b, c[10];
    LONG code = 0;
    assert( !ios_exiting_status( &a, &code ) && !ios_exiting_status( NULL, &code ) );
    ios_exiting_record( &a, 0 );
    ios_exiting_record( &b, (LONG)0xdeadc0de );
    assert( ios_exiting_status( &a, &code ) && code == 0 );
    assert( ios_exiting_status( &b, &code ) && code == (LONG)0xdeadc0de );
    for (int i = 0; i < 7; i++) ios_exiting_record( &c[i], i + 1 );   /* nine records: the first (a) is evicted */
    assert( !ios_exiting_status( &a, &code ) );
    assert( ios_exiting_status( &b, &code ) && code == (LONG)0xdeadc0de );
    assert( ios_exiting_status( &c[6], &code ) && code == 7 );
    puts( "ok: recorded processes are found with their codes, the ninth record evicts the oldest" );
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="madeira-exiting-") as tmp:
    src = Path(tmp) / "t.c"
    src.write_text(code)
    exe = Path(tmp) / "t"
    b = subprocess.run([os.environ.get("CC", "cc"), "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-o", str(exe), str(src)],
                       capture_output=True, text=True)
    check("the table builds: " + b.stderr[-600:], b.returncode == 0)
    if b.returncode == 0:
        t = subprocess.run([str(exe)], capture_output=True, text=True)
        print(t.stdout, end="")
        check("the table: " + (t.stderr[-300:] or "all cases"), t.returncode == 0)

print("PASS: an exiting process ends with the status it chose" if ok else "FAILED: exiting status")
sys.exit(0 if ok else 1)
