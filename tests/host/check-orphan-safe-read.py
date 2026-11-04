#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-orphan-safe-read.py: the orphan-lock monitor never faults on a released window (round 630, agent A2 S2).

The Steam client 9PB1B5 (build 626): the monitor read a lock word in the window of a dead 32-bit program; msync() had accepted
the PROT_NONE page, the plain read faulted on BCD's own thread and the app ended.

1. Applies tools/patch-wine-waiter-read.py, tools/patch-wine-lock-orphan.py and this patch (CI's order) to the pinned
   wine/dlls/ntdll/unix/sync.c (git show HEAD: of $WINE_SRC, ./wine or ~/madeira-bcd/wine, so a patched working copy does not
   matter): applies once, then "already patched"; refuses without the reader.
2. Compiles the patched ios_srw_reap_exclusive, ios_alert_waiter_read and ios_orphan_check with msync() answering 0 (as it does
   for a PROT_NONE page) and runs: three waiters on a held lock in a PROT_NONE page -> no fault, nothing reaped; the reaper
   called on such a page -> returns; the same three waiters on a readable page -> released after one call (the reaper's
   unchanged default); a readable held lock passed to the reaper -> released as before.
A host without a wine checkout says so and passes (CI and the Mac run it).
"""
from pathlib import Path
import importlib.util, os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), root / "tools" / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI applies it after the waiter reader and the orphan-lock patch, with this check first",
      "python3 tests/host/check-orphan-safe-read.py\n          python3 tools/patch-wine-orphan-safe-read.py wine/dlls/ntdll/unix/sync.c" in wf
      and wf.index("tools/patch-wine-waiter-read.py wine/dlls") < wf.index("tools/patch-wine-lock-orphan.py wine/dlls")
      < wf.index("tools/patch-wine-orphan-safe-read.py wine/dlls") < wf.index("bash build/ntdll-unix/build.sh"))

candidates = [Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, root / "wine", Path.home() / "madeira-bcd/wine"]
wine = next((c for c in candidates if c and (c / "dlls/ntdll/unix/sync.c").exists()), None)
if wine is None:
    print("skip: no wine checkout here (CI and the Mac run this check)")
    print("PASS: orphan-lock safe read (skipped here)" if ok else "FAILED: orphan-lock safe read")
    sys.exit(0 if ok else 1)
pinned = subprocess.run(["git", "-C", str(wine), "show", "HEAD:dlls/ntdll/unix/sync.c"], capture_output=True, text=True)
original = pinned.stdout if pinned.returncode == 0 and pinned.stdout else (wine / "dlls/ntdll/unix/sync.c").read_text()

waiter, orphan, safe = load("patch-wine-waiter-read"), load("patch-wine-lock-orphan"), load("patch-wine-orphan-safe-read")
base = orphan.patch(waiter.patch(original))
patched = safe.patch(base)
check("it applies after the reader and the orphan-lock patch, and a second run changes nothing",
      patched != base and safe.patch(patched) == patched and patched.count(safe.MARKER) == 3)
try:
    # The pinned fork already carries the reader (the CI step re-applies it idempotently); take it out to see the refusal.
    safe.patch(base.replace(safe.READER, "static int ios_alert_waiter_read_gone( void *out, const void *addr, size_t size )\n{"))
    check("without the reader it refuses", False)
except ValueError as error:
    check("without the reader it refuses", "ios_alert_waiter_read is missing" in str(error))


def cut(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i) + len(end)]


reap = cut(patched, "void ios_srw_reap_exclusive( unsigned long long lock_addr, unsigned long long dead_teb )", "\n}\n")
reader = cut(patched, "static int ios_alert_waiter_read( void *out, const void *addr, size_t size )\n{", "\n}\n")
waiters = cut(patched, "#define IOS_ALERT_WAITER_MAX 512", "} ios_alert_waiters[IOS_ALERT_WAITER_MAX];")
monitor = cut(patched, "void ios_orphan_check( const unsigned long long *live_stamps, int nstamps )", "\n}\n")
check("the reaper and the monitor read only through the reader",
      "old = *word;" not in reap and "word = *(volatile unsigned int *)(ULONG_PTR)lock;" not in monitor
      and "ios_alert_waiter_read( &old, (const void *)word, sizeof(old) )" in reap
      and "ios_alert_waiter_read( &word, (const void *)(ULONG_PTR)lock, sizeof(word) )" in monitor)

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <signal.h>
#include <unistd.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
typedef uintptr_t ULONG_PTR; typedef int LONG; typedef unsigned long long ULONGLONG;
static int test_msync( void *p, size_t n, int f ) { (void)p; (void)n; (void)f; return 0; }   /* as for a PROT_NONE page */
#define msync test_msync
#define dprintf(fd, ...) printf(__VA_ARGS__)
static LONG InterlockedCompareExchange( LONG *dest, LONG xchg, LONG cmp ) { return __sync_val_compare_and_swap( dest, cmp, xchg ); }
static int wakes;
static void ios_shared_futex_wake( const void *addr, int all ) { (void)addr; (void)all; wakes++; }
static int ios_alert_waiter_read( void *out, const void *addr, size_t size );
''' + reap.replace("void ios_srw_reap_exclusive(", "static void ios_srw_reap_exclusive(", 1) + "\n" + reader + "\n" + waiters + "\n" + monitor + r'''
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)
static void on_fault( int sig ) { (void)sig; static const char m[] = "FAULT: the monitor read the released page\n"; write( 2, m, sizeof(m) - 1 ); _exit( 3 ); }

static unsigned int *page_at( unsigned long long hint, int prot )
{
    void *p = mmap( (void *)(uintptr_t)hint, 16384, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0 );
    if (p == MAP_FAILED || (uintptr_t)p >= 0x8000000000ull) FAIL( "no low page (%p)\n", p );
    ((unsigned int *)p)[64] = 0x00010007u;   /* exclusively held, three parked writers */
    if (prot != (PROT_READ | PROT_WRITE) && mprotect( p, 16384, prot )) FAIL( "mprotect\n" );
    return p;
}

static void park( unsigned long long lock )
{
    int i;
    for (i = 0; i < IOS_ALERT_WAITER_MAX; i++) ios_alert_waiters[i].addr = NULL;
    for (i = 0; i < 3; i++) ios_alert_waiters[i].addr = (void *)(uintptr_t)(lock + 2);
}

int main( int argc, char **argv )
{
    unsigned long long none = 0;
    signal( SIGSEGV, on_fault ); signal( SIGBUS, on_fault );
    if (!strcmp( argv[1], "released" ))
    {
        unsigned int *p = page_at( 0x200000000ull, PROT_NONE );
        unsigned long long lock = (unsigned long long)(uintptr_t)&p[64];
        park( lock );
        ios_orphan_check( &none, 0 );
        ios_srw_reap_exclusive( lock, 0xDEADull );
        if (wakes) FAIL( "something was released on a page that cannot be read\n" );
        printf( "released window: no fault, nothing released\n" );
    }
    else
    {
        unsigned int *p = page_at( 0x240000000ull, PROT_READ | PROT_WRITE );
        unsigned long long lock = (unsigned long long)(uintptr_t)&p[64];
        park( lock );
        ios_orphan_check( &none, 0 );
        if (wakes != 1 || p[64] != 0x00000006u) FAIL( "readable: wakes=%d word=%08x (the default reaper releases at once)\n", wakes, p[64] );
        p[64] = 0x00010003u;
        ios_srw_reap_exclusive( lock, 0xDEADull );
        if (wakes != 2 || p[64] != 0x00000002u) FAIL( "readable: the reaper did not release (%08x)\n", p[64] );
        printf( "readable window: the lock is read and released as before\n" );
    }
    return 0;
}
'''
cc = os.environ.get("CC", "cc")
if not shutil.which(cc):
    print("skip: no C compiler")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-orphan-safe-") as tmp:
        c = Path(tmp) / "t.c"
        exe = Path(tmp) / "t"
        c.write_text(harness)
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function", str(c), "-o", str(exe)], capture_output=True, text=True)
        check("the patched reaper, reader and monitor build: " + b.stderr[-800:], b.returncode == 0)
        if b.returncode == 0:
            for scenario in ("released", "readable"):
                env = dict(os.environ)
                env.pop("MADEIRA_LOCK_ORPHAN", None)
                r = subprocess.run([str(exe), scenario], capture_output=True, text=True, env=env)
                print(r.stdout, end="")
                check("%s: %s" % (scenario, (r.stderr.strip() or "as expected")), r.returncode == 0)

print("PASS: the orphan-lock monitor reads lock words without faulting" if ok else "FAILED: orphan-lock safe read")
sys.exit(0 if ok else 1)
