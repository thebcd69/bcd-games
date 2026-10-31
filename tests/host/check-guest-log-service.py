#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-guest-log-service.py: RockstarService.exe's own text writes reach the session log; no Wine runs.

In the failing Rockstar Launcher runs of builds 574/576 ("Failed to connect to the Rockstar Games Library Service", error
159) the session log held the Launcher's launcher.log lines but nothing the service wrote: the mirror reads only *.log
files, only keyword lines, and 64 lines for the whole session.

1. The patch applies after tools/patch-wine-guest-log.py and patch-wine-guest-log-tag.py to a skeleton file.c and, when the
   wine checkout is there, to the pinned dlls/ntdll/unix/file.c: once, then "already patched"; a file without the mirror
   fails by name and is not written.
2. The patched helper region is compiled and driven with real files:
   - a process that is not RockstarService.exe is mirrored as before: only *.log / output_log.txt, only keyword lines, the
     global budget (an exhausted budget stops it; a text file with another name is not read);
   - RockstarService.exe (any case, any path, either slash): any regular file whatever its name, every text line, its own
     60 lines marked "svc", announced by "[guest-log] file <path>"; the Launcher having used the global budget up changes
     nothing for it and the service using its 60 lines up changes nothing for the Launcher; a binary write is refused;
   - "NotRockstarService.exe", "RockstarService.exe.bak" and an empty image path are not the service;
   - MADEIRA_GUEST_LOG=0 turns it off too.
"""
from pathlib import Path
import os, re, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
mirror = root / "tools/patch-wine-guest-log.py"
tag = root / "tools/patch-wine-guest-log-tag.py"
svc = root / "tools/patch-wine-guest-log-service.py"
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
real = wine / "dlls/ntdll/unix/file.c"

skeleton = """#include <errno.h>
#include <fcntl.h>
#include <limits.h>

/******************************************************************************
 *              NtWriteFile   (NTDLL.@)
 */
NTSTATUS WINAPI NtWriteFile( void )
{
done:
    send_completion = cvalue != 0;

err:
    if (needs_close) close( unix_handle );

    if (type == FD_TYPE_SERIAL && (status == STATUS_SUCCESS || status == STATUS_PENDING))
        set_pending_write( handle );
}
"""


def run(script, path):
    return subprocess.run([sys.executable, str(script), str(path)], capture_output=True, text=True)


with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    sources = [("skeleton", skeleton)]
    if real.exists():
        sources.append(("wine file.c", real.read_text()))
    else:
        print("note: no wine checkout (%s); only the skeleton is patched" % real)
    patched = None
    for name, text in sources:
        p = t / "file.c"
        p.write_text(text)
        if "static void ios_guest_log_mirror" not in text:
            r = run(svc, p)
            assert r.returncode != 0 and "mirror is not in" in r.stderr and p.read_text() == text, (name, r)   # no mirror: fails by name
            assert run(mirror, p).returncode == 0 and run(tag, p).returncode == 0, name
        before = p.read_text()
        r1, r2 = run(svc, p), run(svc, p)
        assert r1.returncode == 0 and "applied" in r1.stdout, (name, r1)
        assert r2.returncode == 0 and "already patched" in r2.stdout, (name, r2)
        after = p.read_text()
        assert after.count("madeira-bcd: [guest-log] service") == 2 and after != before, name
        # a mirror that moved fails by name and writes nothing
        broken = t / "broken.c"
        broken.write_text(before.replace("if (m == 1 && !ios_guest_log_keep(", "if (m == 1 && !other_keep("))
        r = run(svc, broken)
        assert r.returncode != 0 and "anchor not found exactly once (the keyword filter)" in r.stderr \
            and "madeira-bcd: [guest-log] service" not in broken.read_text(), (name, r)
        patched = after
        print("ok   %s: applies once after the mirror and the tag patch, a moved mirror fails by name and writes nothing" % name)

    region = patched[patched.index("/* guest-log-test:begin"):patched.index("/* guest-log-test:end */")]
    harness = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
typedef unsigned short WCHAR;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING ImagePathName; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { RTL_USER_PROCESS_PARAMETERS *ProcessParameters; } PEB;
typedef struct { PEB *Peb; } TEB;
static RTL_USER_PROCESS_PARAMETERS pp_;
static PEB peb_ = { &pp_ };
static TEB teb_ = { &peb_ };
#define NtCurrentTeb() (&teb_)
static WCHAR image_[260];
static void image( const char *s )
{
    size_t i;
    for (i = 0; s[i]; i++) image_[i] = (unsigned char)s[i];
    image_[i] = 0;
    pp_.ImagePathName.Buffer = i ? image_ : NULL;
    pp_.ImagePathName.Length = (unsigned short)(i * 2);
}
static unsigned int GetCurrentThreadId(void) { return 0x24; }
""" + region + r"""
static int open_file( const char *dir, const char *name )
{
    char p[512]; snprintf( p, sizeof(p), "%s/%s", dir, name );
    return open( p, O_CREAT | O_TRUNC | O_WRONLY, 0644 );
}
static void put( int fd, const char *s ) { ios_guest_log_mirror( fd, s, (unsigned)strlen( s ) ); }
int main( int argc, char **argv )
{
    const char *dir = argv[1];
    int rl = open_file( dir, "launcher.log" ), sv = open_file( dir, "service.txt" ), nt = open_file( dir, "notes.txt" );
    int sl = open_file( dir, "svc.log" );
    int i;
    char l[96];
    const char *rs = "C:\\Program Files\\Rockstar Games\\Launcher\\RockstarService.exe";

    /* round 624 (agent R S27, ZTQN18): Rockstar's launcher installer is mirrored like the service */
    if (getenv( "GS_INSTALLER" ))
    {
        int il = open_file( dir, "installer.txt" );
        image( "C:\\Users\\steamuser\\Downloads\\Rockstar-Games-Launcher.exe" ); put( il, "installer step one\n" );
        image( "C:\\x\\Rockstar-Games-Launcher.exe.part" ); put( il, "not the installer\n" );
        return 0;
    }

    /* the Launcher uses the global budget (MADEIRA_GUEST_LOG_LIMIT=3 in the harness's env) */
    image( "C:\\Program Files\\Rockstar Games\\Launcher\\Launcher.exe" );
    for (i = 0; i < 5; i++) { snprintf( l, sizeof(l), "[ ERROR ] [Main ] [launcher] failure number %d\n", i ); put( rl, l ); }
    put( nt, "[ ERROR ] a keyword line in a text file that is not a log\n" );

    /* the service: another name, no keywords, its own budget, binary refused */
    image( rs );
    put( sv, "[2026-10-21 06:48:25.001] Service starting, version 1.0.109.3031\n" );
    put( sv, "[2026-10-21 06:48:25.044] pipe created\n" );
    ios_guest_log_mirror( sv, "\x01\x02\x03\x04\x05\x06 binary\n", 14 );
    image( "/unix/path/ROCKSTARSERVICE.EXE" ); put( sv, "upper case service line\n" );   /* case and slashes */
    image( rs );
    for (i = 0; i < 70; i++) { snprintf( l, sizeof(l), "svc chatter %d\n", i ); put( sv, l ); }
    put( sl, "[ ERROR ] a keyword line in a .log of the service, after its budget\n" );

    /* the Launcher after the service used its 60 lines: still its own rules (the global budget is spent) */
    image( "C:\\Program Files\\Rockstar Games\\Launcher\\Launcher.exe" );
    put( rl, "[ ERROR ] [Main ] [launcher] one more failure after the budget\n" );

    /* what is not the service */
    image( "C:\\x\\NotRockstarService.exe" ); put( sv, "not the service one\n" );
    image( "C:\\x\\RockstarService.exe.bak" ); put( sv, "not the service two\n" );
    image( "" ); put( sv, "not the service three\n" );
    return 0;
}
"""
    c = t / "gs.c"; c.write_text(harness)
    exe = t / "gs"
    b = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-fsanitize=address,undefined", str(c), "-o", str(exe)],
                       capture_output=True, text=True)
    assert b.returncode == 0, b.stderr

    def mirrored(env_extra):
        d = tempfile.mkdtemp(dir=t)
        env = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_GUEST_LOG")}
        env["ASAN_OPTIONS"] = "symbolize=0:detect_leaks=0"   # the symbolizer opens a password dialog on this Mac
        env.update(env_extra)
        r = subprocess.run([str(exe), d], capture_output=True, text=True, env=env)
        assert r.returncode == 0, (env_extra, r.returncode, r.stderr)
        return d, [l for l in r.stderr.splitlines() if l.startswith("[guest-log]")]

    d, lines = mirrored({"MADEIRA_GUEST_LOG_LIMIT": "3"})
    plain = [l for l in lines if re.match(r"\[guest-log\] #\d+", l)]
    svcl = [l for l in lines if re.match(r"\[guest-log\] svc #\d+", l)]
    files = [l for l in lines if l.startswith("[guest-log] file ")]
    assert len(plain) == 3 and all("[launcher] failure number" in l for l in plain), plain
    print("ok   the Launcher is mirrored as before: *.log, keyword lines, the global budget (3 of 6 lines; the text file and the late line are not)")
    assert len(svcl) == 60, len(svcl)
    assert svcl[0].endswith("Service starting, version 1.0.109.3031") and svcl[1].endswith("pipe created"), svcl[:2]
    assert svcl[2].endswith("upper case service line") and svcl[3].endswith("svc chatter 0") and svcl[-1].endswith("svc chatter 56"), \
        (svcl[2], svcl[3], svcl[-1])
    assert re.match(r"\[guest-log\] svc #1 tid=0024 \[2026", svcl[0]) and "#60 " in svcl[-1], (svcl[0], svcl[-1])
    print("ok   RockstarService.exe: every text line of a file named service.txt, 60 lines of its own with the Launcher's budget spent; "
          "binary and the 61st line are refused")
    assert not any("keyword line in a .log of the service" in l for l in lines), "the service's budget holds for .log files too"
    assert not any("not the service" in l for l in lines), [l for l in lines if "not the service" in l]
    print("ok   NotRockstarService.exe, RockstarService.exe.bak and an empty image path are not the service")
    assert any(l == "[guest-log] file %s/service.txt" % os.path.realpath(d) or l.endswith("/service.txt") for l in files), files
    assert not any(l.endswith("/notes.txt") for l in files)
    print("ok   the service's file is announced once: [guest-log] file <path>/service.txt; the image matches in any case, either slash")

    il = mirrored({"GS_INSTALLER": "1"})[1]
    assert any(re.match(r"\[guest-log\] svc #1 tid=\w+ installer step one$", l) for l in il) and not any("not the installer" in l for l in il), il
    print("ok   round 624: Rockstar-Games-Launcher.exe (the installer) is mirrored like the service; a look-alike name is not")

    assert [l for l in mirrored({"MADEIRA_GUEST_LOG": "0"})[1] if "text-log mirror:" not in l] == []
    print("ok   MADEIRA_GUEST_LOG=0 turns the service off too")

print("PASS: guest-log service")
