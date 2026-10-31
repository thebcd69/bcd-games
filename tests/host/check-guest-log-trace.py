#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-guest-log-trace.py: the managed stack trace under an exception line is mirrored too; no Wine runs.

Blasphemous (JPZBGJ, build 622): "Ups! There was an unhandled exception: NullReferenceException" reached the session log, the
frames under it ("  at Foo.Bar () ...") did not (no keyword), so nothing named the call that failed.

1. Applies the patch to the mirror as tools/patch-wine-guest-log.py writes it (a skeleton file.c), with and without
   tools/patch-wine-guest-log-service.py applied first (both forms of the keyword filter), and, when the wine checkout is present,
   to the pinned dlls/ntdll/unix/file.c: once, then "already patched"; a file without the mirror fails by name and is not
   written; a moved filter is refused; only the helper, the call and its label change.
2. Compiles the patched helper region and writes real files: after an exception line the frames ("  at ...", "(Filename: ...") are
   kept, 12 after each and 36 a process, inside the line budget; a frame with no exception line before it, or after a line of
   another kind, is not; MADEIRA_GUEST_LOG=all and =0 are as before; the workflow applies it after the Ren'Py patch.
"""
from pathlib import Path
import os, re, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
mirror = root / "tools/patch-wine-guest-log.py"
service = root / "tools/patch-wine-guest-log-service.py"
trace = root / "tools/patch-wine-guest-log-trace.py"
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
real = wine / "dlls/ntdll/unix/file.c"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if (detail and not cond) else ""))
    ok = ok and bool(cond)


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


def without_patch(text):
    """The file with the trace helper, its call and its label taken out: it must equal the unpatched file."""
    if "static int ios_guest_log_trace(" in text:
        a = text.index("/* madeira-bcd: [guest-log] trace")
        b = text.index("/* A BOM can be its own NtWriteFile call.")
        text = text[:a] + text[b:]
    text = re.sub(r"        if \(m == 1 && ios_guest_log_trace\( \(const char \*\)text \+ start, len \)\) goto keep_line;   /\* madeira-bcd: \[guest-log\] trace \*/\n", "", text)
    return text.replace("continue;\nkeep_line:\n", "continue;\n")


with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    sources = [("skeleton", skeleton, False), ("skeleton + service patch", skeleton, True)]
    if real.exists():
        sources.append(("wine file.c", real.read_text(), False))
    else:
        print("note: no wine checkout (%s); only the skeleton is patched" % real)
    patched = None
    for name, text, with_service in sources:
        p = t / "file.c"
        p.write_text(text)
        if "static void ios_guest_log_mirror" not in text:
            r = run(trace, p)
            check(name + ": a file without the mirror fails by name and is not written",
                  r.returncode != 0 and "mirror is not in" in r.stderr and p.read_text() == text, r.stderr)
            r = run(mirror, p)
            check(name + ": the base mirror applies", r.returncode == 0 and "mirror added" in r.stdout, r.stdout + r.stderr)
        if with_service and "svc" not in p.read_text():
            r = run(service, p)
            check(name + ": the service patch applies first", r.returncode == 0, r.stderr)
        before = p.read_text()
        r1, r2 = run(trace, p), run(trace, p)
        after = p.read_text()
        check(name + ": applies once, then says it is already patched",
              r1.returncode == 0 and "applied" in r1.stdout and r2.returncode == 0 and "already patched" in r2.stdout, r1.stdout + r1.stderr)
        check(name + ": only the helper, the call before the keyword filter and its label change",
              without_patch(after) == before and after.count("goto keep_line;") == 1 and after.count("\nkeep_line:\n") == 1
              and after.index("goto keep_line;") < after.index("ios_guest_log_keep( (const char *)text + start, len )) continue;\nkeep_line:"))
        q = t / "moved.c"
        q.write_text(before.replace("ios_guest_log_keep( (const char *)text + start, len )) continue;", "ios_guest_log_keep( text, len )) continue;"))
        r = run(trace, q)
        check(name + ": a moved keyword filter is refused, the file is not written",
              r.returncode != 0 and "keyword filter was not found" in r.stderr and "ios_guest_log_trace" not in q.read_text(), r.stderr)
        # The compiled region is the skeleton's (the real file's region uses Wine's PEB/WCHAR types once the service patch is in).
        if not with_service and patched is None:
            patched = after

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
    int o = open_file( dir, "output_log.txt" ), s = open_file( dir, "settings.txt" ), i;
    put( o, "  at Lone.Frame () [0x00000] in <filename unknown>:0\n" );           /* no exception line before it */
    put( o, "Ups! There was an unhandled exception: NullReferenceException\n" );
    put( o, "NullReferenceException: Object reference not set to an instance of an object\n" );
    put( o, "  at A.B () [0x00000] in <filename unknown>:0\n" );
    put( o, "   at C.D()\n" );                                                       /* .NET's three blanks */
    put( o, "(Filename: <filename unknown> Line: 0)\n" );
    put( o, "a plain line ends the trace\n" );
    put( o, "  at E.F () [0x00000] in <filename unknown>:0\n" );                  /* after the trace ended */
    put( s, "NullReferenceException: in a settings file\n  at S.T ()\n" );           /* not a log file: nothing */
    for (i = 0; i < 20; i++)                                                      /* 36 frame lines a process at most */
    {
        char l[160];
        snprintf( l, sizeof(l), "E%dException: boom\n  at X%d.Y () [0x00000] in <filename unknown>:0\n"
                                "  at Z%d.W () [0x00000] in <filename unknown>:0\n", i, i, i );
        put( o, l );
    }
    return 0;
}
"""
    c = t / "gl.c"; c.write_text(harness)
    exe = t / "gl"
    cc = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-fsanitize=address,undefined", str(c), "-o", str(exe)],
                        capture_output=True, text=True)
    check("the patched mirror compiles on the host (-Wall)", cc.returncode == 0 and "warning" not in cc.stderr, cc.stderr[-2000:])

    def run_exe(env_extra):
        d = tempfile.mkdtemp(dir=t)
        env = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_GUEST_LOG")}
        env.update(env_extra)
        r = subprocess.run([str(exe), d], capture_output=True, text=True, env=env)
        assert r.returncode == 0, (env_extra, r.returncode, r.stderr)
        return [re.sub(r"^\[guest-log\] #\d+ tid=0024 ", "", l) for l in r.stderr.splitlines() if re.match(r"\[guest-log\] #\d+", l)]

    if cc.returncode == 0:
        body = run_exe({"MADEIRA_GUEST_LOG_LIMIT": "4000"})
        frames = [b for b in body if b.lstrip().startswith("at ") or b.startswith("(Filename:")]
        check("after an exception line its frames are kept (two blanks or three, and Unity's (Filename: ...) trailer)",
              body[:5] == ["Ups! There was an unhandled exception: NullReferenceException",
                           "NullReferenceException: Object reference not set to an instance of an object",
                           "  at A.B () [0x00000] in <filename unknown>:0", "   at C.D()", "(Filename: <filename unknown> Line: 0)"], body[:6])
        check("a frame with no exception line before it, or after a line of another kind, is not; a settings file stays out",
              not any("Lone.Frame" in b or "E.F ()" in b or "plain line" in b or "S.T ()" in b or "settings" in b for b in body), body)
        check("36 frame lines a process at most", len(frames) == 36 and len(body) == 2 + 20 + 36, (len(frames), len(body)))
        check("the frames count against the line budget", len(run_exe({"MADEIRA_GUEST_LOG_LIMIT": "5"})) == 5)
        check("MADEIRA_GUEST_LOG=0 turns it off; =all mirrors every line as before",
              run_exe({"MADEIRA_GUEST_LOG": "0"}) == [] and "a plain line ends the trace" in run_exe({"MADEIRA_GUEST_LOG": "all"}))

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = "python3 tools/patch-wine-guest-log-trace.py wine/dlls/ntdll/unix/file.c"
check("the workflow checks it and applies it after the Ren'Py patch (and so after the service patch)",
      step in wf and "python3 tests/host/check-guest-log-trace.py" in wf
      and wf.index("python3 tools/patch-wine-guest-log-renpy.py wine/dlls/ntdll/unix/file.c") < wf.index(step))
print("PASS: guest-log trace" if ok else "FAIL: guest-log trace")
raise SystemExit(0 if ok else 1)
