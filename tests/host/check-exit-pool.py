#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A Wine thread that exits with a freed object in its autorelease pool (night round 2026-11-11, agent T1 S3; Alan Wake D6F62Q, build 634, and 22 archive
logs of 18 games): BCD itself died at the game's exit.

    objc_release+0x10 <- objc_autoreleasePoolPop+0xf4 <- libobjc +0x9c (AutoreleasePoolPage::tls_dealloc) <- libsystem_pthread +0x25c
      <- pthread_exit+0x58 <- Madeira exit_thread          (D6F62Q L47883-L47897; x0 = the pool entry, x2 = a garbage class word)

libobjc's TSD destructor pops the pool at pthread_exit and releases an object that was freed (and whose memory was reused). Two opt-in switches in
build/ntdll-unix/thread_ios.c, both off by default:
  MADEIRA_EXIT_POOL_DIAG = 1   the first three exits print "[exit-pool] ..." and libobjc's own pool dump;
  MADEIRA_EXIT_POOL_SKIP = 1   the exiting Wine thread clears libobjc's autorelease-pool TSD slot (found, not assumed: the one direct slot of 40..49 that
                               goes from NULL to non-NULL on an objc_autoreleasePoolPush) before pthread_exit, so the destructor never walks the pool.

Held here, on this Mac (arm64 macOS has the same direct TSD slots and the same libobjc; skipped elsewhere):
  1. Source shape: both switches default off and are read once; nothing is done without them (the exit hook returns first); the key is looked up with dlsym
     (no link dependency) and used only when exactly one slot changed and the push was cleared again; the slot is cleared last, after ios_thread_qos_exit.
  2. The real helpers, extracted from thread_ios.c and compiled against a stand-in TEB: the key is found on a fresh thread (a slot in 40..49, NULL again after
     the pop); with neither switch nothing is looked up and nothing touched.
  3. The bug and the mitigation, run in a child process with MallocScribble=1: a thread pushes a pool, autoreleases an NSObject, frees it behind the pool's back
     (object_dispose) and exits. WITHOUT the switch the process dies with a signal in libobjc's destructor (the log's crash); with MADEIRA_EXIT_POOL_SKIP=1 the
     same thread exits cleanly and says so; with DIAG alone a healthy pool is printed and the thread exits cleanly.
Needs python3 and a C compiler on a Mac. On an x86_64 Mac (no arm64 code can run there) the only change to the extracted code is the way the TSD base is read
(`mrs x, TPIDRRO_EL0` becomes a helper that finds the thread's TSD array from %gs:0 and proves it with a dynamic key: the same direct TSD slots, the
same libobjc); the logic under test is the same."""
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    if not cond and detail:
        print(str(detail)[:2500])
    ok = ok and bool(cond)


src = (R / "build/ntdll-unix/thread_ios.c").read_text()
a = src.find("static volatile int ios_exit_pool_key = -1;")
b = src.find("/***********************************************************************\n *           pthread_exit_wrapper")
block = src[a:b]
check("the helpers are found in thread_ios.c", a > 0 and b > a and "static void ios_exit_pool_at_exit( void )" in block)
check("both switches are read once, each by a literal getenv (the settings catalog scans for the name and takes its note from the comment), and 0 / unset / empty is off",
      block.count('on = e && *e && strcmp( e, "0" ) != 0;') == 2 and 'getenv( "MADEIRA_EXIT_POOL_DIAG" );   /* 1:' in block and 'getenv( "MADEIRA_EXIT_POOL_SKIP" );   /* 1:' in block
      and block.count("static volatile int on = -1;") == 2)
check("nothing is looked up or touched without a switch (find_key and the exit hook return first)",
      "if (ios_exit_pool_key != -1 || (!ios_exit_pool_diag_on() && !ios_exit_pool_skip_on())) return;" in block
      and "if (!ios_exit_pool_diag_on() && !ios_exit_pool_skip_on()) return;" in block)
check("libobjc's functions come from dlsym (no link dependency, no header), and the key is used only for exactly one changed slot that the pop cleared again",
      block.count("dlsym( RTLD_DEFAULT,") == 3 and "if (n == 1 && !tsd[found])" in block and "if (ios_exit_pool_skip_on() && key >= 40 && tsd[key])" in block)
check("the exit wrapper calls the hook once, after ios_thread_qos_exit and just before pthread_exit; start_thread calls find_key once, after the TEB is published",
      src.count("ios_exit_pool_at_exit();") == 1 and src.count("ios_exit_pool_find_key();") == 1
      and src.index("ios_thread_qos_exit( (unsigned)(ULONG_PTR)NtCurrentTeb()->ClientId.UniqueThread );") < src.index("ios_exit_pool_at_exit();")
      < src.index("pthread_exit( UIntToPtr(status) );\n}\n", src.index("ios_exit_pool_at_exit();"))
      and src.index("pthread_setspecific( ios_teb_tls_key, teb );") < src.index("ios_exit_pool_find_key();"))

cc = shutil.which("clang") or shutil.which("cc")
ARM_ASM = '__asm__ volatile("mrs %0, TPIDRRO_EL0" : "=r"(tsd_base));'
X86_ASM = 'tsd_base = x86_tsd_base();'
check("the TSD base is read with the arm64 instruction in both places", block.count(ARM_ASM) == 2)
if not cc or sys.platform != "darwin" or platform.machine() not in ("arm64", "x86_64"):
    print("note: the compiled runs need a Mac and a C compiler; skipped")
else:
    if platform.machine() == "x86_64":
        print("note: x86_64 Mac: the TSD base is found with x86_tsd_base() in the extracted code (no arm64 code can run here)")
        block = block.replace(ARM_ASM, X86_ASM)
    HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <dlfcn.h>
#include <objc/runtime.h>
typedef uintptr_t ULONG_PTR;
#if defined(__x86_64__)
/* the thread's TSD array: pthread_t's slot 0 holds the pthread_t itself, a dynamic key's slot holds what pthread_setspecific stored */
static uintptr_t x86_tsd_base( void )
{
    uintptr_t p;
    pthread_key_t k;
    void **q;
    int o;
    __asm__ volatile("movq %%gs:0, %0" : "=r"(p));
    q = (void **)p;
    pthread_key_create( &k, NULL );
    pthread_setspecific( k, (void *)0x5a5a5a5a5a5a5a5aULL );
    for (o = 0; o < 256; o++)
        if ((uintptr_t)q[o] == p && ((void **)&q[o])[k] == (void *)0x5a5a5a5a5a5a5a5aULL) { pthread_setspecific( k, NULL ); pthread_key_delete( k ); return (uintptr_t)&q[o]; }
    pthread_setspecific( k, NULL );
    pthread_key_delete( k );
    fprintf( stderr, "x86_tsd_base: not found\n" );
    abort();
}
#endif
static struct { struct { void *UniqueThread; } ClientId; } fake_teb = { { (void *)0xec } };
#define NtCurrentTeb() (&fake_teb)
''' + block + r'''
extern id objc_autorelease( id );
static int scenario;
static void *worker( void *arg )
{
    (void)arg;
    ios_exit_pool_find_key();                                  /* what start_thread does */
    if (scenario == 9)                                         /* the key search only */
    {
        uintptr_t b; void **tsd;
        @TSDBASE@
        tsd = (void **)(b & ~7ULL);
        printf( "key=%d slot_after=%p\n", ios_exit_pool_key, ios_exit_pool_key >= 40 ? tsd[ios_exit_pool_key] : (void *)-1 );
        fflush( stdout );
        return NULL;
    }
    void *(*push)( void ) = (void *(*)( void ))dlsym( RTLD_DEFAULT, "objc_autoreleasePoolPush" );
    void *token = push();
    (void)token;
    id obj = class_createInstance( objc_getClass( "NSObject" ), 0 );
    objc_autorelease( obj );                                   /* the pool now holds obj ... */
    if (scenario != 3) object_dispose( obj );                  /* ... which is freed behind the pool's back (scenario 3: a healthy pool) */
    ios_exit_pool_at_exit();                                   /* what pthread_exit_wrapper does last */
    return NULL;                                               /* the thread ends: pthread's TSD cleanup runs libobjc's destructor */
}
int main( int argc, char **argv )
{
    pthread_t t;
    scenario = atoi( argv[1] );
    pthread_create( &t, NULL, worker, NULL );
    pthread_join( t, NULL );
    puts( "survived" );
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "t.c"
        c.write_text(HARNESS.replace("@TSDBASE@", "b = x86_tsd_base();" if platform.machine() == "x86_64"
                                                     else '__asm__ volatile("mrs %0, TPIDRRO_EL0" : "=r"(b));'))
        exe = Path(d) / "t"
        r = subprocess.run([cc, "-O0", "-fno-objc-arc", "-o", str(exe), str(c), "-lobjc"], capture_output=True, text=True)
        check("the extracted helpers compile on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            import os

            def run(scen, **env):
                e = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_EXIT_POOL") and k != "MallocScribble"}
                e["MallocScribble"] = "1"
                e.update(env)
                return subprocess.run([str(exe), str(scen)], capture_output=True, text=True, env=e)

            f = run(9, MADEIRA_EXIT_POOL_SKIP="1")
            m = re.search(r"key=(-?\d+) slot_after=(\S+)", f.stdout)
            print("     (this Mac: key %s)" % (m.group(1) if m else "?"))
            check("on a fresh thread the key is found: a direct TSD slot of 40..49, and the pop cleared it again",
                  f.returncode == 0 and m and 40 <= int(m.group(1)) <= 49 and m.group(2) in ("(nil)", "0x0", "0"), f.stdout + f.stderr)
            check("...and the log line says which slot", "[exit-pool] key=%s:" % (m.group(1) if m else "?") in f.stderr, f.stderr)
            g = run(9)
            check("with neither switch the key is not looked for and nothing is printed", "key=-1" in g.stdout and "[exit-pool]" not in g.stderr, g.stdout + g.stderr)
            bug = run(1)
            check("the bug: a thread that exits with a freed object in its autorelease pool kills the process with a signal in libobjc's destructor (no switch)",
                  bug.returncode < 0 and "survived" not in bug.stdout, "rc=%d %s" % (bug.returncode, bug.stdout + bug.stderr))
            fix = run(1, MADEIRA_EXIT_POOL_SKIP="1")
            check("with MADEIRA_EXIT_POOL_SKIP=1 the same thread exits cleanly, and the log says it cleared the slot (the pool's pages leak)",
                  fix.returncode == 0 and "survived" in fix.stdout and "clears libobjc's autorelease-pool slot" in fix.stderr, "rc=%d %s" % (fix.returncode, fix.stderr))
            diag = run(3, MADEIRA_EXIT_POOL_DIAG="1")
            check("with MADEIRA_EXIT_POOL_DIAG=1 a healthy pool is described (tid, stack bounds, key and slot) and the thread exits cleanly",
                  diag.returncode == 0 and "survived" in diag.stdout and re.search(r"\[exit-pool\] tid=00ec exiting: stack=\[0x[0-9a-f]+,0x[0-9a-f]+\)", diag.stderr), "rc=%d %s" % (diag.returncode, diag.stderr))
            both = run(1, MADEIRA_EXIT_POOL_DIAG="1", MADEIRA_EXIT_POOL_SKIP="1")
            check("with both switches on a damaged pool DIAG prints first (the libobjc dump may itself fault on the garbage entry, as the log's destructor does)",
                  "[exit-pool] tid=00ec exiting" in both.stderr, "rc=%d %s" % (both.returncode, both.stderr[-800:]))

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "tests/host/check-exit-pool.py" in wf)
print("PASS: the exit-time autorelease pool switches" if ok else "FAILED")
sys.exit(0 if ok else 1)
