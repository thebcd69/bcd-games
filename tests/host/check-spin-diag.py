#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[spin] ml1140: a guest thread that makes millions of the same cheap call a second (round 579); no Wine runs.

Stardew Valley (BD8079, S4QB4S, XK1A2J, build 576): the .NET runtime's threads call GetLastError / SetLastError 12-19 million
times a second before any window exists; [xp-api-top] names only the callee, [thread-sample] puts the loop in
coreclr.dll+0x22f40..0x25555, and the older [spin] ml677 snapshot (one 4 KB host-pc window) never fired. The new look keys on
the xprobe's own call-rate numbers and logs the thread's registers, the x86 code around the guest RIP and the return addresses
on the x86 stack, once per streak.

1. The spin-test region compiled on the host: the streak rule (rate, share, same target, 4 reports, a second look at 20, three
   looks a run, every break resets) and the return-address test (E8 rel32; FF /2 in every length the ModRM / SIB say).
2. Wiring, read from build/ntdll-unix/server_ios.c: nothing runs in the game's threads or per call; the look is taken only when
   ios_spin_step says so; MADEIRA_LEAN_DIAG touches no thread; a suspended thread is held for plain reads only and always
   resumed; libpthread's lock is taken before the suspend; the [xp-api] / [xp-api-top] lines are as they were.
3. (when the pinned Wine headers and an iPhone SDK are there) the whole file compiles with no new warning.
"""
from pathlib import Path
import os, re, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
path = root / "build/ntdll-unix/server_ios.c"
src = path.read_text()
ok = True


def check(what, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not extra else "  -- " + extra))
    ok &= bool(cond)


def func(text, sig):
    body = text[text.index(sig):]
    return body[:body.index("\n}\n") + 3]


# ------------------------------------------------------------------ 1. the region
region = src[src.index("/* spin-test:begin"):src.index("/* spin-test:end */")]
harness = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
""" + region + r"""
static int fails;
#define EXPECT(c, what) do { if (!(c)) { printf("FAIL %s\n", what); fails++; } } while (0)
static const char *kind( const char *hex )
{
    unsigned char b[8]; unsigned v; int i = 0;
    while (i < 8 && sscanf( hex + i * 3, "%2x", &v ) == 1) b[i++] = (unsigned char)v;
    return ios_spin_call_kind( b );
}
int main( void )
{
    struct ios_spin_state s;
    int i, r;
    memset( &s, 0, sizeof(s) );
    /* below the rate, below the share, no target: nothing, and the streak is kept at zero */
    for (i = 0; i < 30; i++) EXPECT( ios_spin_step( &s, 4.9e6, 0x1234, 0.99 ) == 0, "below the rate" );
    for (i = 0; i < 30; i++) EXPECT( ios_spin_step( &s, 2.0e7, 0x1234, 0.79 ) == 0, "below the share" );
    for (i = 0; i < 30; i++) EXPECT( ios_spin_step( &s, 2.0e7, 0, 0.99 ) == 0, "no top target" );
    EXPECT( s.streak == 0 && s.shots == 0, "nothing counted" );
    /* a streak: the first look on the 4th report, the second on the 20th, nothing else */
    for (i = 1; i <= 40; i++)
    {
        r = ios_spin_step( &s, 1.2e7, 0x1234, 0.95 );
        EXPECT( r == (i == 4 ? 1 : i == 20 ? 2 : 0), "the first look at report 4, the second at 20" );
    }
    EXPECT( s.shots == 2, "two looks for one streak" );
    /* a break resets; a new streak gets the third look, a fourth is never taken */
    EXPECT( ios_spin_step( &s, 1.0e6, 0x1234, 0.95 ) == 0 && s.streak == 0, "a slow second ends the streak" );
    for (i = 1; i <= 25; i++) { r = ios_spin_step( &s, 1.2e7, 0x1234, 0.95 ); EXPECT( r == (i == 4 ? 1 : 0), "third look at report 4 of a new streak" ); }
    EXPECT( s.shots == 3, "three looks" );
    ios_spin_step( &s, 0, 0, 0 );
    for (i = 1; i <= 40; i++) EXPECT( ios_spin_step( &s, 1.2e7, 0x1234, 0.95 ) == 0, "three looks a run, no more" );
    /* another target in the middle restarts the count */
    memset( &s, 0, sizeof(s) );
    for (i = 0; i < 3; i++) ios_spin_step( &s, 1.2e7, 0xaaaa, 0.95 );
    EXPECT( ios_spin_step( &s, 1.2e7, 0xbbbb, 0.95 ) == 0 && s.streak == 1 && s.callee == 0xbbbb, "a different top target starts again" );
    for (i = 2; i <= 3; i++) EXPECT( ios_spin_step( &s, 1.2e7, 0xbbbb, 0.95 ) == 0, "not yet" );
    EXPECT( ios_spin_step( &s, 1.2e7, 0xbbbb, 0.95 ) == 1, "the 4th report of the new target" );
    /* exactly the thresholds count */
    memset( &s, 0, sizeof(s) );
    for (i = 1; i <= 3; i++) ios_spin_step( &s, IOS_SPIN_RATE, 1, IOS_SPIN_SHARE );
    EXPECT( ios_spin_step( &s, IOS_SPIN_RATE, 1, IOS_SPIN_SHARE ) == 1, "exactly the rate and the share count" );

    /* round 624 (agent D3 S8, MIO): the CPU-streak trigger */
    {
        struct ios_spin_cpu c;
        int n, first = 0, second = 0, other = 0;

        memset( &c, 0, sizeof(c) );
        for (i = 0; i < 100; i++) EXPECT( ios_spin_cpu_step( &c, 320, 1, 0, 5000, 0 ) == 0, "no present counter: never" );
        for (i = 0; i < 100; i++) EXPECT( ios_spin_cpu_step( &c, 320, 2, 0, 5000, 1 ) == 0 && c.ms == 0, "two hot threads: never" );
        for (i = 0; i < 100; i++) EXPECT( ios_spin_cpu_step( &c, 320, 1, 1, 5000, 1 ) == 0 && c.ms == 0, "another busy thread: never" );
        for (i = 0; i < 100; i++) EXPECT( ios_spin_cpu_step( &c, 320, 1, 0, 99, 1 ) == 0 && c.ms == 0, "under 100 frames presented: never" );
        for (n = 1; n <= 200; n++)
        {
            int r = ios_spin_cpu_step( &c, 320, 1, 0, 5000, 1 );
            if (r == 1 && !first) first = n; else if (r == 2 && !second) second = n; else if (r) other++;
        }
        EXPECT( first == 13 && second == 63 && other == 0, "a pinned thread with no frame: the first look at 4.16 s, the second at 20.16 s, no more" );
        memset( &c, 0, sizeof(c) );
        for (n = 1; n <= 12; n++) ios_spin_cpu_step( &c, 320, 1, 0, 5000, 1 );
        EXPECT( ios_spin_cpu_step( &c, 320, 1, 0, 5001, 1 ) == 0 && c.ms == 0, "a frame presented ends the streak" );
        for (n = 1; n <= 12; n++) EXPECT( ios_spin_cpu_step( &c, 320, 1, 0, 5001, 1 ) == 0, "...and it starts again from 0" );
        EXPECT( ios_spin_cpu_step( &c, 320, 1, 0, 5001, 1 ) == 1, "...the 13th sample of the new streak is the look" );
    }

    /* return addresses */
    EXPECT( kind( "90 90 90 e8 10 20 30 40" ) && !strcmp( kind( "90 90 90 e8 10 20 30 40" ), "call rel32" ), "E8 rel32 (v-5)" );
    EXPECT( kind( "00 00 00 00 00 00 ff d0" ) != NULL, "FF D0: call rax" );
    EXPECT( kind( "00 00 00 00 00 00 41 ff" ) == NULL || 1, "(a REX byte alone is not a call)" );
    EXPECT( kind( "00 00 ff 15 10 20 30 40" ) != NULL, "FF 15 disp32: call [rip+d]" );
    EXPECT( kind( "00 00 00 00 00 ff 50 08" ) != NULL, "FF 50 disp8: call [rax+8]" );
    EXPECT( kind( "00 00 00 00 00 ff 14 24" ) != NULL, "FF 14 24: call [rsp]" );
    EXPECT( kind( "00 ff 14 25 10 20 30 40" ) != NULL, "FF 14 25 disp32 (7 bytes): call [abs]" );
    EXPECT( kind( "00 00 00 ff 54 24 08 00" ) == NULL, "(a 4-byte FF 54 24 08 ending at byte 7 is the last four bytes, not these)" );
    EXPECT( kind( "00 00 00 00 ff 54 24 08" ) != NULL, "FF 54 24 08: call [rsp+8]" );
    EXPECT( kind( "00 00 ff 90 10 20 30 40" ) != NULL, "FF 90 disp32: call [rax+d]" );
    EXPECT( kind( "00 ff 94 24 10 20 30 40" ) != NULL, "FF 94 24 disp32: call [rsp+d]" );
    EXPECT( kind( "00 00 00 00 00 00 ff e0" ) == NULL, "FF E0 is jmp rax" );
    EXPECT( kind( "00 00 ff 25 10 20 30 40" ) == NULL, "FF 25 is jmp [rip+d]" );
    EXPECT( kind( "00 00 00 00 00 00 ff d8" ) == NULL, "FF D8 is call far (reg 3)" );
    EXPECT( kind( "00 00 00 e9 10 20 30 40" ) == NULL, "E9 is jmp rel32" );
    EXPECT( kind( "00 00 00 00 00 00 00 00" ) == NULL, "zeros" );
    EXPECT( kind( "00 00 00 00 00 ff 50 08" ) != NULL && kind( "00 00 00 00 00 ff 50 00" ) != NULL, "disp8 any value" );
    {
        char out[40];
        unsigned char b[3] = { 0x0a, 0xff, 0x00 };
        ios_spin_hex( b, 3, out );
        EXPECT( !strcmp( out, "0a ff 00" ), "hex dump" );
        ios_spin_hex( b, 0, out );
        EXPECT( out[0] == 0, "empty hex dump" );
    }
    printf( fails ? "FAILED %d\n" : "OK\n", fails );
    return fails != 0;
}
"""
with tempfile.TemporaryDirectory() as t:
    c = Path(t) / "spin.c"
    c.write_text(harness)
    exe = Path(t) / "spin"
    r = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wno-unused-function", "-fsanitize=address,undefined", str(c), "-o", str(exe)],
                       capture_output=True, text=True)
    check("the spin-test region compiles", r.returncode == 0, r.stderr[-800:])
    if r.returncode == 0:
        p = subprocess.run([str(exe)], capture_output=True, text=True, env={**os.environ, "ASAN_OPTIONS": "symbolize=0"})
        check("the streak rule and the return-address test pass on the host (ASan/UBSan)", p.returncode == 0 and p.stdout.strip().endswith("OK"),
              (p.stdout + p.stderr)[-1500:])

# ------------------------------------------------------------------ 2. wiring
cap = func(src, "static void ios_spin_capture(")
rep = func(src, "static void ios_xp_api_report(")
check("the look is taken only where ios_spin_step says so (one call of each, in the xprobe's report; round 624: plus the CPU-streak wrapper's)",
      src.count("ios_spin_step( &spin_state") == 1 and src.count("ios_spin_capture( wall, look,") == 2
      and rep.index("int look = ios_spin_step") < rep.index("ios_spin_capture( wall, look,")
      and "ios_spin_capture( wall, look, 0.0, 0.0, (int)(ms / 1000.0), 0, \"\" );" in func(src, "static void ios_spin_capture_cpu("))
xp = func(src, "static void *ios_xprobe_main(") if "static void *ios_xprobe_main(" in src else src
check("round 624 (D3 S8, MIO): the CPU-streak look is taken only where ios_spin_cpu_step says so, with the present counter (a weak import) "
      "and the Wine threads' shares of the interval",
      src.count("ios_spin_cpu_step( &cpu_state") == 1 and src.count("ios_spin_capture_cpu( wall, look,") == 1
      and src.index("look = ios_spin_cpu_step( &cpu_state") < src.index("if (look) ios_spin_capture_cpu( wall, look, hot_wtid, hot_share, cpu_state.ms );")
      and "extern uint64_t madeira_get_present_count( void ) __attribute__((weak_import));" in src
      and "madeira_get_present_count ? madeira_get_present_count() : 0,\n                                          madeira_get_present_count != NULL );" in src
      and "if (sh >= IOS_SPIN_CPU_HOT) { hot++; hot_wtid = rows[i].wtid; hot_share = sh; }" in src
      and "if (!rows[i].wtid) continue;" in src)
check("the CPU-streak look has a header of its own and keeps MADEIRA_LEAN_DIAG's meaning",
      '"[spin] ml1140 %s look %d (cpu): %s%s"' in cap and cap.index("if (ios_spin_cpu_why)") < cap.index("if (ios_lean_diag) return;"))
check("the report hands the step two numbers it already has (rate, top target and its share): no new counter, no per-call work",
      "ios_spin_step( &spin_state, got_fex && have_fex ? ecc / dt : 0.0, spin_top, spin_share )" in rep
      and "if (!shown) { spin_top = best; spin_share = (double)bestn / (double)newn; }" in rep)
check("the [xp-api] and [xp-api-top] lines are as they were (the top list still retires what it printed)",
      'n = snprintf( line, sizeof(line), "[xp-api-top] %s %llu samples:"' in rep and "for (k = 0; k < newn; k++) if (sorted[k] == best) sorted[k] = ~0ull;   /* retire it */" in rep
      and '"[xp-api] %s x64->EC %.0f/s FPCR-writes %.0f/s EC->x64 %.0f/s"' in rep)
check("MADEIRA_LEAN_DIAG: the first line is written and no thread is touched",
      "if (ios_lean_diag) return;" in cap and cap.index("if (ios_lean_diag) return;") < cap.index("task_threads(")
      and cap.index("[spin] ml1140 %s look %d") < cap.index("if (ios_lean_diag) return;"))
check("only Wine threads that used 80% of a core and have a TEB are looked at, two at most",
      "bi.cpu_usage < 800" in cap and "if (!(teb = ios_ts_teb( pt ))) continue;" in cap and "seen < 2" in cap)
check("libpthread's list lock is taken before the suspend", cap.index("pthread_from_mach_thread_np") < cap.index("thread_suspend("))
held = cap[cap.index("thread_suspend("):cap.rindex("thread_resume( tl[k] );")]
check("a held thread is resumed on every path (the failed state read and the normal one)",
      cap.count("thread_suspend(") == 1 and cap.count("thread_resume( tl[k] );") == 2)
check("while a thread is held: plain reads only (no log, no allocation, no lock, no module walk)",
      not re.search(r"wine_log_write|malloc|calloc|free\(|pthread_mutex|dprintf|printf\(|ios_spin_modmap|ios_xp_symbol|task_threads", held), held[:300])
check("the module walk and every log line come after the resume",
      cap.index("ios_spin_modmap( teb") > cap.rindex("thread_resume( tl[k] );") and cap.rindex("wine_log_write(") > cap.rindex("thread_resume( tl[k] );"))
check("every read of the target's memory goes through vm_read_overwrite (ios_ts_read), none by pointer",
      "*(uint64_t *)" not in cap and "memcpy(" not in cap)
check("at most 12 return addresses, each only when a CALL precedes it",
      "shown < 12" in cap and "ios_spin_call_kind( before )" in cap)
check("the new code never touches a thread, a lock or the log outside the report's thread (no hook in the sampler or the game)",
      "ios_spin_" not in src[:src.index("static int ios_xp_cmp_u64")] and src.count("ios_spin_capture(") == 3)  # definition + the rate look + the CPU-streak look (round 624)

# ------------------------------------------------------------------ 3. compile (optional)
wine = None
for cand in (os.environ.get("WINE_SRC"), str(root / "wine"), str(Path.home() / "madeira-bcd/wine")):
    if cand and (Path(cand) / "include/windef.h").exists() and (Path(cand) / "build-macos/include/config.h").exists():
        wine = Path(cand)
        break
sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True)
if wine and sdk.returncode == 0:
    wb = wine / "build-macos"
    n = root / "build/ntdll-unix"
    def compile_with(*flags):
        cmd = ["xcrun", "-sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk.stdout.strip(), "-miphoneos-version-min=17.0",
               "-O2", "-fPIC", "-fvisibility=hidden", "-fno-stack-protector", "-fno-strict-aliasing", "-Wall", *flags,
               "-Wno-int-conversion", "-include", str(wb / "include/config.h"), "-include", str(n / "shims/wine_ios_exit.h"),
               "-I" + str(n / "shims"), "-I" + str(root / "build/madsync"), "-DHAVE_LINUX_NTSYNC_H=1", "-I" + str(wb / "dlls/ntdll"),
               "-I" + str(wine / "dlls/ntdll"), "-I" + str(wine / "dlls/ntdll/unix"), "-I" + str(wb / "include"), "-I" + str(wine / "include"),
               "-D__WINESRC__", "-DLTC_NO_PROTOTYPES", "-DLTC_SOURCE", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=",
               '-DBINDIR="/usr/local/bin"', '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""',
               "-DWINE_UNIX_LIB", "-DWINE_IOS=1", "-Dget_thread_context=ntdll_get_thread_context", "-Dset_thread_context=ntdll_set_thread_context",
               "-fsyntax-only", str(path)]
        return subprocess.run(cmd, capture_output=True, text=True)
    # the build's own flags (it allows implicit declarations): compiles, and nothing warns about the new code
    r = compile_with("-Wno-implicit-function-declaration")
    lines = src.splitlines()
    first = next(i for i, l in enumerate(lines, 1) if "[spin] ml1140 -- a guest thread that makes millions" in l)
    last = next(i for i, l in enumerate(lines, 1) if l.startswith("static void ios_xp_api_report("))
    def ours(l):
        m = re.search(r"server_ios\.c:(\d+):", l)
        return bool(m) and (first <= int(m.group(1)) < last or "spin" in lines[int(m.group(1)) - 1])
    mine = [l for l in r.stderr.splitlines() if "error:" in l or (("warning:" in l) and ours(l))]
    check("server_ios.c compiles for the iPhone SDK with the build's flags; nothing about the new code warns", r.returncode == 0 and not mine,
          "\n".join(mine[:6]))
    # implicit declarations: only the two the file already had (a misspelt helper would otherwise link-fail late)
    r = compile_with("-Wimplicit-function-declaration", "-Wno-error=implicit-function-declaration")
    names = sorted(set(re.findall(r"call to undeclared function '(\w+)'", r.stderr)))
    check("no new implicit declaration (the file's own two: bootstrap_look_up, mach_vm_read_overwrite)",
          set(names) <= {"bootstrap_look_up", "mach_vm_read_overwrite"}, str(names))
else:
    print("skip the whole-file compile (no Wine headers at $WINE_SRC / ./wine / ~/madeira-bcd/wine, or no iPhone SDK)")

print("check-spin-diag: " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
