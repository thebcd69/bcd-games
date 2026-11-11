#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Russell Barton
"""env.MADEIRA_SLEEP_KERNEL_APC = 1 (tools/patch-wine-sleep-kernel-apc.py): a non-alertable NtDelayExecution waits in the server.

Why: on iOS send_thread_signal cannot reach a thread, so a system APC (an I/O completion) queued for a thread that sleeps
non-alertably (a host select()) never runs until the thread enters the server. Far Cry 3 behind Ubisoft Connect reads its
launcher pipe overlapped, sleeps, never sees the completion and exits after 15 s. On Windows that completion runs in any wait.

Checks:
  1. the patch adds the helper before NtDelayExecution and changes only its two non-alertable paths (iOS only); undoing the
     edits gives back the stock text; a second run changes nothing; a moved anchor fails;
  2. NtDelayExecution compiled on the host from the stock and the patched text (server_wait, NtYieldExecution,
     NtQuerySystemTime and select are stand-ins that record their calls):
       - patched, switch 1: a timed non-alertable sleep yields first (as before), then makes exactly one
         server_wait(NULL, 0, SELECT_INTERRUPTIBLE, <the caller's timeout>) and no select(); an infinite one makes
         server_wait(..., SELECT_INTERRUPTIBLE, NULL); a zero timeout (Sleep(0)) returns the yield's status and never
         reaches the server; an alertable sleep is unchanged (SELECT_INTERRUPTIBLE | SELECT_ALERTABLE);
       - patched, switch unset, 0 or anything but 1: exactly the stock behaviour (select(), no server_wait for a
         non-alertable sleep); the switch is read at each sleep;
       - stock: the same cases, for comparison;
  3. CI applies the patch before ntdll-unix is built and runs this check; the catalog documents the key (off by default), a list
     may set it, the session end clears it.
NOT covered: that the iOS server delivers the APC during the wait (server_select's STATUS_KERNEL_APC loop, which the
alertable path already uses), the cost per sleep, timing on a device.
Needs the wine submodule checked out and `cc` on PATH. In CI this runs after the patch step, so a patched sync.c is
first restored to the stock text.
"""
from pathlib import Path
import importlib.util, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ska", root / "tools/patch-wine-sleep-kernel-apc.py")
ska = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ska)
src_path = root / "wine/dlls/ntdll/unix/sync.c"
if not src_path.is_file():
    print("FAIL: wine/dlls/ntdll/unix/sync.c missing (check out the wine submodule)")
    sys.exit(1)
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
ok = True


def check(what, cond, detail=""):
    global ok
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[:1500]) if detail and not cond else ""))
    ok = ok and bool(cond)


text = src_path.read_text()
stock = ska.unpatch(text) if ska.MARKER in text else text
patched = ska.patch(stock)
check("the patch changes the stock text and adds its marker", patched != stock and ska.MARKER in patched)
check("a second run changes nothing", ska.patch(patched) == patched)
check("undoing the edits gives back the stock text", ska.unpatch(patched) == stock)
try:
    ska.patch(stock.replace(ska.TIMED_OLD, ska.TIMED_OLD.replace("if (!when)", "if ( !when)")))
    check("a moved anchor fails", False)
except SystemExit:
    check("a moved anchor fails", True)
fn_at = patched.index(ska.HEAD)
fn = patched[fn_at:patched.index("\n}\n", fn_at) + 3]
check("only NtDelayExecution's non-alertable paths change (the alertable branch is untouched)",
      "server_wait( NULL, 0, SELECT_INTERRUPTIBLE | SELECT_ALERTABLE, timeout )" in fn
      and fn.count("SELECT_ALERTABLE") == 1 and fn.count("madeira_sleep_kernel_apc()") == 2)


def function_with_helper(t):
    start = t.index(ska.HELPER) if ska.HELPER in t else t.index(ska.HEAD)
    end = t.index("\n}\n", t.index(ska.HEAD)) + 3
    return t[start:end]


HARNESS = r'''
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#define WINE_IOS 1
#define WINAPI
typedef unsigned char BOOLEAN;
typedef unsigned int NTSTATUS;
typedef long long timeout_t;
typedef union { long long QuadPart; } LARGE_INTEGER;
typedef unsigned int data_size_t;
union select_op;
#define STATUS_SUCCESS 0
#define STATUS_TIMEOUT 0x102
#define STATUS_NO_YIELD_PERFORMED 0x40000024
#define TIMEOUT_INFINITE 0x7fffffffffffffffLL
#define SELECT_ALERTABLE 1
#define SELECT_INTERRUPTIBLE 2
#define IOS_ECO_POLL() do {} while (0)
static long ios_xp_delay, ios_xp_delay_hist[6];
static int n_wait, n_select, n_yield, last_flags; static const LARGE_INTEGER *last_timeout; static jmp_buf out;
static NTSTATUS NtYieldExecution( void ) { n_yield++; return STATUS_NO_YIELD_PERFORMED; }
static NTSTATUS NtQuerySystemTime( LARGE_INTEGER *t ) { t->QuadPart = 1000000000LL; return 0; }
static unsigned int server_wait( const union select_op *op, data_size_t size, unsigned int flags, const LARGE_INTEGER *t )
{
    (void)op; (void)size; n_wait++; last_flags = (int)flags; last_timeout = t;
    if (!t) longjmp( out, 1 );   /* an infinite wait: leave the loop */
    return STATUS_TIMEOUT;
}
#define select my_select
static int my_select( int n, fd_set *r, fd_set *w, fd_set *e, struct timeval *tv )
{
    (void)n; (void)r; (void)w; (void)e; n_select++;
    if (!tv) longjmp( out, 2 );
    return 0;
}
FUNCTION
static void reset( void ) { n_wait = n_select = n_yield = last_flags = 0; last_timeout = NULL; }
int main( int argc, char **argv )
{
    LARGE_INTEGER rel = { -100000 }, zero = { 0 }, inf = { TIMEOUT_INFINITE };
    NTSTATUS st;
    (void)argc;
    if (strcmp( argv[1], "-" )) setenv( "MADEIRA_SLEEP_KERNEL_APC", argv[1], 1 ); else unsetenv( "MADEIRA_SLEEP_KERNEL_APC" );
    reset(); st = NtDelayExecution( 0, &rel );
    printf( "timed wait=%d flags=%d timeout_is_callers=%d select=%d yield=%d status=%x\n", n_wait, last_flags,
            last_timeout == &rel, n_select, n_yield, st );
    reset(); st = NtDelayExecution( 0, &zero );
    printf( "zero wait=%d select=%d yield=%d status=%x\n", n_wait, n_select, n_yield, st );
    reset(); st = NtDelayExecution( 1, &rel );
    printf( "alertable wait=%d flags=%d select=%d status=%x\n", n_wait, last_flags, n_select, st );
    reset(); if (!setjmp( out )) NtDelayExecution( 0, &inf );
    printf( "infinite wait=%d flags=%d null_timeout=%d select=%d\n", n_wait, last_flags, last_timeout == NULL, n_select );
    reset(); if (!setjmp( out )) NtDelayExecution( 0, NULL );
    printf( "null wait=%d select=%d\n", n_wait, n_select );
    return 0;
}
'''


def run(source_text, arg):
    code = HARNESS.replace("FUNCTION", function_with_helper(source_text))
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "t.c"; c.write_text(code); exe = Path(tmp) / "t"
        r = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-unused-variable",
                            "-fsanitize=address,undefined", "-o", str(exe), str(c)], capture_output=True, text=True)
        if r.returncode:
            return None, r.stderr[-1500:]
        p = subprocess.run([str(exe), arg], capture_output=True, text=True)
        return dict(l.split(" ", 1) for l in p.stdout.splitlines()), p.stdout + p.stderr


if not cc:
    check("a C compiler is on PATH", False)
else:
    on, out_on = run(patched, "1")
    offs = {v: run(patched, v) for v in ("-", "0", "11", "")}
    base, out_base = run(stock, "1")
    check("the stock and the patched NtDelayExecution compile on the host (-Werror, ASAN/UBSAN)",
          on is not None and base is not None and all(r[0] is not None for r in offs.values()), out_on if on is None else out_base)
    if on and base and all(r[0] for r in offs.values()):
        check("patched: a timed non-alertable sleep yields, then one server_wait(SELECT_INTERRUPTIBLE, the caller's timeout), no select()",
              on["timed"] == "wait=1 flags=2 timeout_is_callers=1 select=0 yield=1 status=0", out_on)
        check("patched: an infinite non-alertable sleep waits in the server with SELECT_INTERRUPTIBLE and no timeout",
              on["infinite"] == "wait=1 flags=2 null_timeout=1 select=0" and on["null"] == "wait=1 select=0", out_on)
        check("patched: Sleep(0) returns the yield's status and never reaches the server", on["zero"] == base["zero"] and "wait=0" in on["zero"], out_on)
        check("patched: an alertable sleep is unchanged (SELECT_INTERRUPTIBLE | SELECT_ALERTABLE)", on["alertable"] == base["alertable"] and "flags=3" in on["alertable"], out_on)
        check("switch unset, 0, 11 or empty: exactly the stock behaviour", all(r[0] == base for r in offs.values()),
              {k: r[1] for k, r in offs.items() if r[0] != base})
        check("stock: a timed non-alertable sleep uses select() and not the server", base["timed"].startswith("wait=0") and "select=1" in base["timed"], out_base)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI applies the patch to sync.c and runs this check",
      "python3 tools/patch-wine-sleep-kernel-apc.py wine/dlls/ntdll/unix/sync.c" in wf and "python3 tests/host/check-sleep-kernel-apc.py" in wf)
gen = (root / "build/tools/gen-config-catalog.py").read_text()
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the catalog documents the key, off by default; a list may set it, 0 or 1; the session end clears it",
      re.search(r'"env\.MADEIRA_SLEEP_KERNEL_APC": \{[^}]*"default": "0"', gen, re.S) is not None
      and 'key: "env.MADEIRA_SLEEP_KERNEL_APC"' in cat and '"MADEIRA_SLEEP_KERNEL_APC",' in pol
      and 'case "MADEIRA_SLEEP_KERNEL_APC":' in pol and 'unsetenv("MADEIRA_SLEEP_KERNEL_APC");' in bridge)
sys.exit(0 if ok else 1)
