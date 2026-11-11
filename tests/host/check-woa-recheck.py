#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Russell Barton
"""env.MADEIRA_WOA_RECHECK = 1 (tools/patch-wine-woa-recheck.py): an infinite RtlWaitOnAddress re-checks its entry every second.

Why: Ubisoft Connect's browser (upc.exe, 32-bit) stopped in 2 of 11 Far Cry 3 starts with threads parked for 80-100 s on
SRW locks that were free; a waker removes the waiter's entry and then alerts the thread, and an alert that never arrives
leaves the waiter asleep for good.

Checks:
  1. the patch (both files) changes the stock text, a second run changes nothing, undoing the edits gives the stock
     text back, a moved anchor fails;
  2. RtlWaitOnAddress cut from the patched wine/dlls/ntdll/sync.c with the helper, the queue code and compare_addr, compiled
     with -Wall -Wextra -Werror and under ASan + UBSan (NtWaitForAlertByThreadId is a scripted stand-in):
       - an infinite wait passes the slice (exactly -10000001) and nothing else; a wake (entry removed, ALERTED) returns
         STATUS_SUCCESS with no log line;
       - a lost alert (entry removed, the slice times out) returns STATUS_SUCCESS after one slice, logged as "dequeued";
       - a value changed without a wake keeps waiting until the wake (no early return, no log);
       - a genuine wait (value equal, entry queued) waits slice after slice and returns only when woken, no log;
       - a wait with a timeout passes the caller's timeout once and returns STATUS_TIMEOUT, entry out of the queue;
       - a value that differs on entry returns at once; the log stops after 32 lines;
  3. the unix registry lines (patched NtWaitForAlertByThreadId) in a harness: the slices of one park keep its start
     and count as infinite, another address or a non-slice wait starts a new park; the switch: only 1 turns it on,
     anything else waits a slice out as the infinite wait it stands for; read at each slice;
  4. the i386 guard: the PE change is only compiled for __i386__; CI applies the patch before the i386 farm and runs
     this check; the catalog documents the key (off by default), a list may set it, the session end clears it.
NOT covered: that the iOS alert path loses an alert (the cause stays open; the log names the class), timing on a device.
Needs the wine submodule checked out and `cc` on PATH. In CI this runs after the patch step, so patched files are
first restored to the text before this patch.
"""
from pathlib import Path
import importlib.util, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("woa", root / "tools/patch-wine-woa-recheck.py")
woa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(woa)
pe_path, ux_path = root / "wine/dlls/ntdll/sync.c", root / "wine/dlls/ntdll/unix/sync.c"
for p in (pe_path, ux_path):
    if not p.is_file():
        print(f"FAIL: {p.relative_to(root)} missing (check out the wine submodule)")
        sys.exit(1)
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
ok = True


def check(what, cond, detail=""):
    global ok
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[:2000]) if detail and not cond else ""))
    ok = ok and bool(cond)


pe_text, ux_text = pe_path.read_text(), ux_path.read_text()
pe_stock = woa.unpatch_pe(pe_text) if woa.MARKER in pe_text else pe_text
ux_stock = woa.unpatch_unix(ux_text) if woa.MARKER in ux_text else ux_text
pe, ux = woa.patch_pe(pe_stock), woa.patch_unix(ux_stock)
check("the patch changes both files and adds its marker", pe != pe_stock and ux != ux_stock and woa.MARKER in pe and woa.MARKER in ux)
check("a second run changes nothing", woa.patch_pe(pe) == pe and woa.patch_unix(ux) == ux)
check("undoing the edits gives back the text before the patch", woa.unpatch_pe(pe) == pe_stock and woa.unpatch_unix(ux) == ux_stock)
for name, fn, text, anchor in (("PE", woa.patch_pe, pe_stock, woa.PE_WAIT_OLD), ("unix", woa.patch_unix, ux_stock, woa.UX_REG_OLD)):
    try:
        fn(text.replace(anchor, anchor.replace("(", "( ", 1)))
        check(f"a moved {name} anchor fails", False)
    except SystemExit:
        check(f"a moved {name} anchor fails", True)


def cut(text, start, end="\n}\n"):
    at = text.index(start)
    return text[at:text.index(end, at) + len(end)]


# ---------------------------------------------------------------- 2. the PE side
fn = cut(pe, woa.PE_HEAD)
helper = pe[pe.index(woa.PE_HELPER):pe.index(woa.PE_HELPER) + len(woa.PE_HELPER)]
check("the PE helper and the call are only compiled for __i386__ (the 32-bit ntdll this build compiles)",
      helper.startswith("#ifdef __i386__\n") and helper.rstrip().endswith("#endif") and woa.PE_WAIT_NEW.startswith("#ifdef __i386__\n")
      and woa.PE_WAIT_NEW in fn and fn.count("NtWaitForAlertByThreadId") == 1)
structs = cut(pe, "struct futex_entry\n{", "\n};\n") + cut(pe, "struct futex_queue\n{", "\n};\n")
spin = cut(pe, "static void spin_lock( LONG *lock )") + cut(pe, "static void spin_unlock( LONG *lock )")
cmpf = cut(pe, "static BOOL compare_addr( const void *addr, const void *cmp, SIZE_T size )")
# the harness stands in for the i386 build; the host printf has %zu where Wine's debug printf has %Iu
i386 = lambda t: t.replace("#ifdef __i386__", "#if 1   /* the harness stands in for the i386 build */").replace("%Iu", "%zu")

HEAD = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int32_t NTSTATUS, LONG; typedef uint32_t DWORD, ULONG; typedef unsigned char UCHAR; typedef unsigned short USHORT;
typedef uint64_t ULONG64; typedef int64_t LONGLONG; typedef size_t SIZE_T; typedef uintptr_t ULONG_PTR; typedef int BOOL;
typedef union { struct { DWORD LowPart; LONG HighPart; } u; LONGLONG QuadPart; } LARGE_INTEGER;
#define WINAPI
#define TRUE 1
#define FALSE 0
#define STATUS_SUCCESS ((NTSTATUS)0)
#define STATUS_TIMEOUT ((NTSTATUS)0x102)
#define STATUS_ALERTED ((NTSTATUS)0x101)
#define STATUS_INVALID_PARAMETER ((NTSTATUS)0xc000000d)
struct list { struct list *next, *prev; };
static void list_init( struct list *l ) { l->next = l->prev = l; }
static void list_add_tail( struct list *l, struct list *e ) { e->next = l; e->prev = l->prev; l->prev->next = e; l->prev = e; }
static void list_remove( struct list *e ) { e->next->prev = e->prev; e->prev->next = e->next; }
#define InterlockedCompareExchange(p, x, c) __sync_val_compare_and_swap( (p), (c), (x) )
#define InterlockedExchange(p, x) __sync_lock_test_and_set( (p), (x) )
#define InterlockedIncrement(p) __sync_add_and_fetch( (p), 1 )
#define InterlockedIncrement64(p) __sync_add_and_fetch( (p), 1 )
#define YieldProcessor() do { } while (0)
#define TRACE(...) do { } while (0)
#include <stdarg.h>
/* Wine's debug printf: not format-checked against host types (BCD's own diagnostic lines in RtlWaitOnAddress use Wine's
 * LONG sizes; they only run for marked threads, never here) */
static int n_log; static char last_log[256];
static void err_log( const char *fmt, ... ) { va_list a; va_start( a, fmt ); vsnprintf( last_log, sizeof(last_log), fmt, a ); va_end( a ); n_log++; fputs( last_log, stderr ); }
#define ERR(...) err_log( __VA_ARGS__ )
static const char *debugstr_timeout( const LARGE_INTEGER *t ) { return t ? "fin" : "INF"; }
static DWORD GetCurrentThreadId( void ) { return 0x58; }
static struct { void *Instrumentation[16]; struct { void *UniqueThread; } ClientId; } teb;
#define NtCurrentTeb() (&teb)
'''
HEAD += r'''
static struct { long long woa_waits; } ios_xp_nt;
static const void *ios_pump_wait_addr, *ios_pump_wait_queue;
static void ios_srw_dump_park( const void *a ) { (void)a; }
'''
QUEUE = r'''
static struct futex_queue the_queue, futex_queues[1];
static struct futex_queue *get_futex_queue( const void *addr ) { (void)addr; return &the_queue; }
'''
TESTS = r'''
/* the scripted NtWaitForAlertByThreadId: what the "kernel" does at each call */
enum { WAKE, LOST, CHANGE, SLEEP };
static int script[64], n_calls; static LONGLONG seen[64]; static const LARGE_INTEGER *seen_ptr[64];
static ULONG word;
static void dequeue_all( void )   /* RtlWakeAddressAll's removal, without the alert */
{
    struct list *e = the_queue.queue.next;
    while (e != &the_queue.queue) { struct list *n = e->next; ((struct futex_entry *)e)->addr = NULL; list_remove( e ); e = n; }
}
NTSTATUS NtWaitForAlertByThreadId( const void *addr, const LARGE_INTEGER *timeout )
{
    int i = n_calls++;
    (void)addr;
    seen[i] = timeout ? timeout->QuadPart : 0; seen_ptr[i] = timeout;
    if (i >= 60) abort();   /* a wait that never ends */
    switch (script[i])
    {
    case WAKE: dequeue_all(); return STATUS_ALERTED;
    case LOST: dequeue_all(); return STATUS_TIMEOUT;
    case CHANGE: word++; return STATUS_TIMEOUT;
    default: return STATUS_TIMEOUT;
    }
}
static int fails;
#define T(c) do { if (!(c)) { printf( "FAIL line %d: %s\n", __LINE__, #c ); fails++; } } while (0)
static int queued( void ) { return the_queue.queue.next && the_queue.queue.next != &the_queue.queue; }
static void reset( const int *s, int n ) { int i; for (i = 0; i < 64; i++) script[i] = i < n ? s[i] : WAKE; n_calls = 0; n_log = 0; word = 7; last_log[0] = 0; }

int main( void )
{
    ULONG cmp = 7;
    LARGE_INTEGER t;
    int i;

    { static const int s[] = { WAKE }; reset( s, 1 ); }
    T( RtlWaitOnAddress( &word, &cmp, 4, NULL ) == STATUS_SUCCESS && n_calls == 1 && seen[0] == -10000001 && !n_log && !queued() );
    { static const int s[] = { LOST }; reset( s, 1 ); }
    T( RtlWaitOnAddress( &word, &cmp, 4, NULL ) == STATUS_SUCCESS && n_calls == 1 && n_log == 1 && strstr( last_log, "after 1 s: dequeued" ) && !queued() );
    { static const int s[] = { SLEEP, CHANGE, SLEEP, WAKE }; reset( s, 4 ); }
    T( RtlWaitOnAddress( &word, &cmp, 4, NULL ) == STATUS_SUCCESS && n_calls == 4 && !n_log && !queued() );
    { static const int s[] = { SLEEP, SLEEP, SLEEP, SLEEP, SLEEP, WAKE }; reset( s, 6 ); }
    T( RtlWaitOnAddress( &word, &cmp, 4, NULL ) == STATUS_SUCCESS && n_calls == 6 && !n_log && !queued() );
    for (i = 0; i < 6; i++) T( seen[i] == -10000001 );
    { static const int s[] = { SLEEP }; reset( s, 1 ); }
    t.QuadPart = -500000;
    T( RtlWaitOnAddress( &word, &cmp, 4, &t ) == STATUS_TIMEOUT && n_calls == 1 && seen_ptr[0] == &t && !n_log && !queued() );
    { static const int s[] = { WAKE }; reset( s, 1 ); }
    cmp = 8;
    T( RtlWaitOnAddress( &word, &cmp, 4, NULL ) == STATUS_SUCCESS && n_calls == 0 );
    cmp = 7;
    { static const int s[] = { LOST }; int total = 0; for (i = 0; i < 40; i++) { reset( s, 1 ); RtlWaitOnAddress( &word, &cmp, 4, NULL ); total += n_log; } T( total == 32 - 1 ); }   /* one line above, then 31 more: 32 per process */
    puts( fails ? "FAILED" : "ok" );
    return fails != 0;
}
'''


def build_and_run(src, tmp, name, sanitize):
    c, exe = Path(tmp) / f"{name}.c", Path(tmp) / name
    c.write_text(src)
    flags = [cc, "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-parameter"]
    if sanitize:
        flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-fno-omit-frame-pointer"]
    r = subprocess.run(flags + ["-o", str(exe), str(c)], capture_output=True, text=True)
    if r.returncode:
        return None, r.stderr
    try:
        p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired as e:   # a hang is a failure too (a broken queue can loop forever instead of faulting)
        return subprocess.CompletedProcess(e.cmd, 124, "", "timed out after 20 s"), "timed out after 20 s"
    return p, p.stdout + p.stderr


pe_src = HEAD + structs + spin + cmpf + QUEUE + "NTSTATUS NtWaitForAlertByThreadId( const void *addr, const LARGE_INTEGER *timeout );\n" \
         + i386(helper) + i386(fn) + TESTS
with tempfile.TemporaryDirectory() as tmp:
    for label, san in (("strict (-Wall -Wextra -Werror)", False), ("AddressSanitizer + UndefinedBehaviorSanitizer", True)):
        r, out = build_and_run(pe_src, tmp, "pe_san" if san else "pe", san)
        check(f"RtlWaitOnAddress with the re-check compiles and behaves as documented, {label}", r is not None and r.returncode == 0, out)

# ---------------------------------------------------------------- 3. the unix registry
ux_fn = cut(ux, woa.UX_HEAD)
check("the unix hunks are inside NtWaitForAlertByThreadId, the field in the registry struct",
      all(h in ux_fn for h in (woa.UX_NORM_NEW, woa.UX_REG_NEW, woa.UX_TIMEOUT_NEW)) and woa.UX_FIELD_NEW in ux)
UXH = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int64_t LONGLONG; typedef uint64_t ULONGLONG;
typedef union { LONGLONG QuadPart; } LARGE_INTEGER;
#define TIMEOUT_INFINITE ((LONGLONG)0x7fffffffffffffff)
static struct { void *tid; const void *addr; ULONGLONG since; int inf; const void *slice; } ios_alert_waiters[4];
static LONGLONG clock_now;
static void NtQuerySystemTime( LARGE_INTEGER *t ) { t->QuadPart = clock_now; }
static ULONGLONG get_absolute_timeout( const LARGE_INTEGER *t ) { return (ULONGLONG)(clock_now - t->QuadPart); }
''' + woa.UX_HELPER + r'''
/* one call of NtWaitForAlertByThreadId's registry part; timed_out: the wait ended by its timeout */
static void park( const void *address, const LARGE_INTEGER *timeout, int timed_out )
{
    ULONGLONG end = 0;
    int ios_wslot = 0;
''' + woa.UX_NORM_NEW.replace("        int ios_wslot;\n", "") + r'''
    (void)end;
    {
        LARGE_INTEGER ios_wnow;
        NtQuerySystemTime( &ios_wnow );
''' + woa.UX_REG_NEW + r'''
    }
    if (timed_out)
    {
        int ret = -1;
        if (ret == -1)
        {
            if (ios_wslot >= 0)
            {
                ios_alert_waiters[ios_wslot].slice = ios_woa_slice ? ios_alert_waiters[ios_wslot].addr : NULL;
                ios_alert_waiters[ios_wslot].addr = NULL;
            }
        }
    }
    else ios_alert_waiters[ios_wslot].addr = NULL;
}
static int fails;
#define T(c) do { if (!(c)) { printf( "FAIL line %d: %s\n", __LINE__, #c ); fails++; } } while (0)
int main( int argc, char **argv )
{
    LARGE_INTEGER slice = { -10000001 }, other = { -10000000 };
    static int a, b;

    if (argc > 1) { printf( "%d\n", madeira_woa_recheck() ); return 0; }
    setenv( "MADEIRA_WOA_RECHECK", "1", 1 );
    clock_now = 1000; park( &a, &slice, 1 );
    T( ios_alert_waiters[0].since == 1000 && ios_alert_waiters[0].inf == 1 && ios_alert_waiters[0].slice == &a );
    clock_now = 2000; park( &a, &slice, 1 );
    T( ios_alert_waiters[0].since == 1000 && ios_alert_waiters[0].inf == 1 );           /* the same park goes on */
    clock_now = 3000; park( &b, &slice, 1 );
    T( ios_alert_waiters[0].since == 3000 );                                             /* another address: a new park */
    clock_now = 4000; park( &b, &other, 1 );
    T( ios_alert_waiters[0].since == 4000 && ios_alert_waiters[0].inf == 0 && ios_alert_waiters[0].slice == NULL );   /* not a slice */
    clock_now = 5000; park( &b, &slice, 0 );
    T( ios_alert_waiters[0].slice == NULL && ios_alert_waiters[0].addr == NULL );      /* woken: nothing carried over */
    clock_now = 6000; park( &b, &slice, 1 );
    T( ios_alert_waiters[0].since == 6000 );
    puts( fails ? "FAILED" : "ok" );
    return fails != 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    r, out = build_and_run(UXH, tmp, "ux", True)
    check("the slices of one park keep its start and count as infinite; another address or wait starts a new park", r is not None and r.returncode == 0, out)
    if r is not None and r.returncode == 0:
        exe = Path(tmp) / "ux"
        got = {}
        for val in (None, "", "1", "0", "11", "true"):
            env = dict(os.environ)
            env.pop("MADEIRA_WOA_RECHECK", None)
            if val is not None:
                env["MADEIRA_WOA_RECHECK"] = val
            got[val] = subprocess.run([str(exe), "switch"], capture_output=True, text=True, env=env).stdout.strip()
        check("the switch: only '1' turns it on", got == {None: "0", "": "0", "1": "1", "0": "0", "11": "0", "true": "0"}, got)
    off = UXH.replace('    setenv( "MADEIRA_WOA_RECHECK", "1", 1 );\n', "    (void)b; (void)other;\n    unsetenv( \"MADEIRA_WOA_RECHECK\" );\n")
    off = off.replace("T( ios_alert_waiters[0].since == 1000 && ios_alert_waiters[0].inf == 1 && ios_alert_waiters[0].slice == &a );",
                      "T( ios_alert_waiters[0].inf == 1 && ios_alert_waiters[0].slice == NULL );   /* off: the infinite wait */")
    r, out = build_and_run(off.split("    clock_now = 2000;")[0] + "    puts( fails ? \"FAILED\" : \"ok\" );\n    return fails != 0;\n}\n", tmp, "ux_off", True)
    check("the switch unset: a slice is waited out as an infinite wait (registered as infinite, no slice carried)",
          r is not None and r.returncode == 0, out)

# ---------------------------------------------------------------- 4. CI and the catalog
wf = (root / ".github/workflows/build-ipa.yml").read_text()
patch_step = "python3 tools/patch-wine-woa-recheck.py wine/dlls/ntdll/sync.c wine/dlls/ntdll/unix/sync.c"
check("CI applies the patch before ntdll-unix and the i386 farm are built, and runs this check",
      wf.count(patch_step) == 1 and wf.index(patch_step) < wf.index("name: Build ntdll-unix") < wf.index("name: Build i386 farm (WoW64)")
      and wf.count("python3 tests/host/check-woa-recheck.py") == 1)
gen = (root / "build/tools/gen-config-catalog.py").read_text()
cat = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the catalog documents the key, off by default; a list may set it, 0 or 1; the session end clears it",
      re.search(r'"env\.MADEIRA_WOA_RECHECK": \{[^}]*"default": "0"', gen, re.S) is not None and 'key: "env.MADEIRA_WOA_RECHECK"' in cat
      and '"MADEIRA_WOA_RECHECK",' in pol and 'case "MADEIRA_WOA_RECHECK":' in pol and 'unsetenv("MADEIRA_WOA_RECHECK");' in bridge)
print("PASS: woa-recheck" if ok else "FAILED: woa-recheck")
sys.exit(0 if ok else 1)
