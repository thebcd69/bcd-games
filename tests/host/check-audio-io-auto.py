#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The audio driver asks iOS for a smaller IO buffer once, when a stream's queue is shorter than one render pass (night round 2026-11-11, T3 S5).

An XAudio2/FAudio-style client (Quake II, Counter-Strike 2, Deadlock, Resonance, MARVEL Cosmic Invasion, Mina the Hollower, ...) keeps two 10 ms
periods queued, 960 frames (882 at 44.1 kHz). RemoteIO pulls one pass of about 21 ms, 1024 frames, so every pass finds the ring too short: the sound runs
6 % slow with a gap in every pass. With the audio session's IO buffer asked for at the start (env.MADEIRA_AUDIO_IO_MS = 5) the same kind of client plays
cleanly (passes of 240-256 frames, Dead Rising and Mewgenics logs). The driver now recognises the failure in its own ml1230 pacing windows and asks once.

  1. ios_audio_underrun_window and ios_audio_io_auto_window, cut out of build/ntdll-unix/audio_null_ios.c and run on the host with the numbers of
     real pacing lines (log codes in the case names): the signature in A3ZNFE / H1SBJJ / PTMY3S (Quake II), A82ECP (Resonance, passes of 1115), AJJ4RC and
     4VVX0Z (44.1 kHz), a 48.7 % window (nwjs) and a 4096-frame pass (jazzpunk) is one; windows that are not: healthy streams (BioShock, Mewgenics with the key,
     Dead Rising with the key), an idle client (0.0 % written), a client with a full ring, Horizon's 12-channel spatial bed (23 %), too few passes, 89 % short;
  2. the state machine: nothing at the first window, the request at the second in a row, never twice in a run (two streams, many windows), a good window
     in between starts the count again, env.MADEIRA_AUDIO_IO_MS set (any value, 0 too) asks nothing and says so once, an empty value is not "set", and with
     the app side absent (a weak symbol) nothing is called;
  3. from the sources: the timer loop calls it only from the pacing report block and only while nothing has been asked; the bridge's
     madeira_audio_request_io_slice refuses a range outside 1..100 ms and the game's own key, asks once, works on a background queue (never the caller's thread),
     publishes no MADEIRA_AUDIO_IO_US; the driver has no Objective-C in it; the settings catalog note says it; the rule's signals are the pacing line's own words;
     CI runs this check.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
drv = (R / "build/ntdll-unix/audio_null_ios.c").read_text()
bridge = (R / "app/Madeira/WineProcessBridge.m").read_text()
catalog_py = (R / "build/tools/gen-config-catalog.py").read_text()
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
rules_py = (R / "build/tools/autotune_rules.py").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1800:]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


a = drv.index("extern int madeira_audio_request_io_slice(double ms) __attribute__((weak));")
b = drv.index("static NTSTATUS ios_timer_loop(void *args) {")
chunk = drv[a:b]

cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP host run: no C compiler")
else:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdatomic.h>
''' + chunk + r'''
#ifndef NO_APP
static int asks; static double last_ms;
int madeira_audio_request_io_slice(double ms) { asks++; last_ms = ms; return 1; }
#endif
static int fails;
#define EXPECT(cond, what) do { if (!(cond)) { printf("FAIL %s (line %d)\n", what, __LINE__); fails++; } } while (0)

struct win { const char *name; uint64_t pad_max; uint32_t cb_max, passes, shorts; double wrote, secs; uint32_t rate, dev, ch; int want; };
static const struct win W[] = {
    /* the signature: numbers of real ml1230 pacing lines */
    { "A3ZNFE Quake II 48k",            960, 1024, 468, 468,  9.35, 10.00, 48000, 48000, 2, 1 },
    { "H1SBJJ/PTMY3S Quake II",         960, 1024, 469, 469,  9.38, 10.00, 48000, 48000, 2, 1 },
    { "A82ECP Resonance, pass 1115",    960, 1115, 431, 431,  8.62, 10.00, 48000, 48000, 2, 1 },
    { "AJJ4RC cs2 44.1k on 44.1k",      882,  941, 469, 469,  9.35, 10.00, 44100, 44100, 2, 1 },
    { "44.1k client on a 48k endpoint", 882, 1024, 469, 469,  9.35, 10.00, 44100, 48000, 2, 1 },
    { "nwjs 48.7 % (one period)",       960, 1024, 469, 469,  4.87, 10.00, 48000, 48000, 2, 1 },
    { "jazzpunk 4096-frame pass",      1440, 4096, 117, 117,  3.51, 10.01, 48000, 48000, 2, 1 },
    { "89.9 % of passes short is not enough? (90 % is)", 960, 1024, 1000, 900, 9.30, 10.00, 48000, 48000, 2, 1 },
    /* not the signature */
    { "BioShock healthy (ring 480..1440, pass 480)",  1440,  480, 1000,   0, 10.00, 10.00, 48000, 48000, 2, 0 },
    { "Mewgenics with the key (pass 256, 14 short)",  1024,  256, 1875,  14,  9.92, 10.00, 48000, 48000, 2, 0 },
    { "Dead Rising with the key (pass 240)",          4560,  240, 2001,   0, 10.00, 10.00, 48000, 48000, 6, 0 },
    { "4VVX0Z cs2 after iOS shrank the passes",        882,  941, 1875,   0, 10.01, 10.00, 44100, 44100, 2, 0 },
    { "DVWGG3 idle client (0.0 % written, ring 0..0)",   0, 1024,  469, 469,  0.00, 10.00, 48000, 48000, 2, 0 },
    { "BC2YZW idle client (0.1 %, ring 0..256)",       256,  480, 1001,1001,  0.01, 10.00, 48000, 48000, 2, 0 },
    { "a client that wrote 20 % is not feeding",       960, 1024,  469, 469,  2.00, 10.00, 48000, 48000, 2, 0 },
    { "HZD main stream with a full ring (0..4800)",   4800, 1024,  469, 469,  0.84, 10.00, 48000, 48000, 2, 0 },
    { "HZD spatial bed, 12 channels (23.5 %)",         480, 1024,  468, 468,  2.35, 10.00, 48000, 48000, 12, 0 },
    { "the same 23.5 % window on a stereo stream",     480, 1024,  468, 468,  2.35, 10.00, 48000, 48000, 2, 0 },
    { "99 passes are too few",                         960, 1024,   99,  99,  9.35, 10.00, 48000, 48000, 2, 0 },
    { "89 % of the passes short",                      960, 1024, 1000, 890,  9.30, 10.00, 48000, 48000, 2, 0 },
    { "queue as long as the pass (1024 of 1024)",     1024, 1024,  469, 469, 10.00, 10.00, 48000, 48000, 2, 0 },
    { "44.1k client whose queue covers its pass",      941, 1024,  469, 469,  9.90, 10.00, 44100, 48000, 2, 0 },
    { "no pass seen yet",                              960,    0,  469, 469,  9.35, 10.00, 48000, 48000, 2, 0 },
    { "zero seconds",                                  960, 1024,  469, 469,  9.35,  0.00, 48000, 48000, 2, 0 },
    { "8 channels is still a plain stream",            960, 1024,  469, 469,  9.35, 10.00, 48000, 48000, 8, 1 },
    { "9 channels is not",                             960, 1024,  469, 469,  9.35, 10.00, 48000, 48000, 9, 0 },
};

int main(void)
{
    size_t i;
    uint32_t n1 = 0, n2 = 0, n3 = 0;
    int stream_a, stream_b;

    for (i = 0; i < sizeof(W) / sizeof(W[0]); i++) {
        int got = ios_audio_underrun_window(W[i].pad_max, W[i].cb_max, W[i].passes, W[i].shorts, W[i].wrote, W[i].secs, W[i].rate, W[i].dev, W[i].ch);
        if (got != W[i].want) { printf("FAIL window '%s': got %d want %d\n", W[i].name, got, W[i].want); fails++; }
    }

    unsetenv("MADEIRA_AUDIO_IO_MS");
#ifndef NO_APP
    /* the state machine: the first window asks nothing, the second in a row asks once, the rest never again */
    atomic_store(&g_io_auto_asked, 0); asks = 0; n1 = 0;
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.35, 10.0, 48000, 48000, 2);
    EXPECT(asks == 0 && n1 == 1 && !atomic_load(&g_io_auto_asked), "one signature window asks nothing");
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    EXPECT(asks == 1 && last_ms == 5.0 && atomic_load(&g_io_auto_asked), "the second window in a row asks for 5 ms");
    for (i = 0; i < 20; i++) ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    EXPECT(asks == 1, "never twice in a run, however many windows follow");

    /* a good window in between starts the count again */
    atomic_store(&g_io_auto_asked, 0); asks = 0; n1 = 0;
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    ios_audio_io_auto_window(&stream_a, &n1, 2880, 1024, 468, 0, 10.0, 10.0, 48000, 48000, 2);
    EXPECT(n1 == 0, "a healthy window resets the count");
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    EXPECT(asks == 0 && n1 == 1, "signature, good, signature: nothing asked");
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    EXPECT(asks == 1, "then the next signature window completes two in a row");

    /* two streams in the signature: one request. Another stream's healthy window does not reset this stream's count (each has its own counter) */
    atomic_store(&g_io_auto_asked, 0); asks = 0; n1 = n2 = 0;
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    ios_audio_io_auto_window(&stream_b, &n2, 4800, 1024, 469, 0, 10.0, 10.0, 48000, 48000, 2);
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    EXPECT(asks == 1 && n2 == 0, "interleaved windows of two streams: the counts are separate");
    ios_audio_io_auto_window(&stream_b, &n2, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    ios_audio_io_auto_window(&stream_b, &n2, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    EXPECT(asks == 1, "a second stream in the signature asks nothing more");

    /* the game's own value (any, 0 too) wins, and is said once */
    setenv("MADEIRA_AUDIO_IO_MS", "5", 1);
    atomic_store(&g_io_auto_asked, 0); asks = 0; n1 = 0;
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    EXPECT(asks == 0 && atomic_load(&g_io_auto_asked) == 1, "env.MADEIRA_AUDIO_IO_MS=5: nothing asked, and the run does not look again");
    setenv("MADEIRA_AUDIO_IO_MS", "0", 1);
    atomic_store(&g_io_auto_asked, 0); asks = 0; n1 = 0;
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    EXPECT(asks == 0, "env.MADEIRA_AUDIO_IO_MS=0 (the system's slice, chosen on purpose): nothing asked");
    setenv("MADEIRA_AUDIO_IO_MS", "", 1);
    atomic_store(&g_io_auto_asked, 0); asks = 0; n1 = 0;
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.38, 10.0, 48000, 48000, 2);
    EXPECT(asks == 1, "an empty value is not a choice: asked");
    unsetenv("MADEIRA_AUDIO_IO_MS");
    (void)n3;
#else
    /* the app side is absent (a weak symbol that nothing defines): the window code must not call it */
    atomic_store(&g_io_auto_asked, 0); n1 = 0;
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.35, 10.0, 48000, 48000, 2);
    ios_audio_io_auto_window(&stream_a, &n1, 960, 1024, 468, 468, 9.35, 10.0, 48000, 48000, 2);
    EXPECT(madeira_audio_request_io_slice == NULL, "the weak symbol is NULL without the app");
    EXPECT(atomic_load(&g_io_auto_asked) == 1, "the signature is still recognised (and said) without the app side");
    (void)n2; (void)n3; (void)stream_b;
#endif
    printf("%s\n", fails ? "FAILED" : "all cases passed");
    return fails ? 1 : 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        cfile = os.path.join(tmp, "t.c")
        open(cfile, "w").write(harness)
        for label, define, extra_link, expect_lines in (
                ("with the app side", [], [], None),
                ("without the app side (weak, undefined)", ["-DNO_APP"], ["-Wl,-U,_madeira_audio_request_io_slice"], None)):
            exe = os.path.join(tmp, "t_" + str(len(define)))
            build = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function"] + define + ["-o", exe, cfile] + extra_link,
                                   capture_output=True, text=True)
            check(f"the driver's window code builds on the host, -Wall -Wextra -Werror ({label})", build.returncode == 0, build.stderr)
            if build.returncode:
                continue
            env = {k: v for k, v in os.environ.items() if k != "MADEIRA_AUDIO_IO_MS"}
            run = subprocess.run([exe], capture_output=True, text=True, timeout=60, env=env)
            fails = [l for l in run.stdout.split("\n") if l.startswith("FAIL")]
            check(f"the window cases and the state machine hold ({label})", run.returncode == 0 and not fails and "all cases passed" in run.stdout,
                  run.stdout + run.stderr)
            lines = run.stderr.split("\n")
            if not define:
                check("the request says ml2300 once, the skip ml2301 twice (the values 5 and 0), with the numbers",
                      sum("ml2300" in l for l in lines) == 4 and sum("ml2301" in l for l in lines) == 2
                      and any("ml2300 stream=" in l and "queue at most 960 frames, largest pass 1024 frames, 468 of 468 passes short, client wrote 93.8%" in l
                              and "asking iOS for a 5 ms IO buffer" in l for l in lines),
                      run.stderr)
            else:
                check("without the app side the line is printed and nothing is called", sum("ml2300" in l for l in lines) == 1, run.stderr)

# ---- from the sources ----------------------------------------------------------------------------------------------------------------
loop = drv[drv.index("static NTSTATUS ios_timer_loop(void *args) {"):]
loop = loop[:loop.index("\n}\n")]
check("the timer loop calls the window code only in the pacing report block and only while nothing has been asked",
      loop.count("ios_audio_io_auto_window(") == 1
      and loop.index("ml1230 pacing stream=") < loop.index("ios_audio_io_auto_window(") < loop.index("rep_ns = now; rep_wr = wr; rep_short = shrt;")
      and "if (!atomic_load_explicit(&g_io_auto_asked, memory_order_relaxed))" in loop
      and "&underrun_windows" in loop)
check("the pacing line keeps the words the auto-tune rule reads (largest pass N frames); ring A..B frames",
      "passes (largest pass %u frames); \"\n                    \"ring %llu..%llu frames;" in drv)
check("the driver has no Objective-C and does not touch the audio session itself (the app does, on a queue)",
      "[AVAudioSession" not in drv and "setPreferredIOBufferDuration" not in drv and "extern int madeira_audio_request_io_slice(double ms) __attribute__((weak));" in drv)
fn = bridge[bridge.index("int madeira_audio_request_io_slice(double ms)"):]
fn = fn[:fn.index("\n}\n")]
check("the bridge refuses a range outside 1..100 ms and the game's own key, and asks once per app run",
      "if (ms < 1 || ms > 100 || (own && *own)) return 0;" in fn and "if (atomic_exchange(&asked, 1)) return 0;" in fn
      and 'const char *own = getenv("MADEIRA_AUDIO_IO_MS");' in fn)
check("the bridge works on a background queue, sets the preferred IO buffer and reactivates the session, as the session-start call does",
      "dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0)" in fn and "setPreferredIOBufferDuration:ms / 1000.0" in fn
      and "[session setActive:YES error:&err];" in fn and "pthread_main_np" not in fn)
check("the bridge publishes no MADEIRA_AUDIO_IO_US (the engine period is read once, before this can run)", "MADEIRA_AUDIO_IO_US" not in fn and "setenv" not in fn)
check("the bridge logs what it asked, what was granted at once and what the session settled on a second later",
      "[audio] io buffer asked %.1f ms (the stream's queue is shorter than one pass), was %.2f ms, granted %.2f ms" in fn
      and "[audio] io buffer is now %.2f ms (asked %.1f ms)" in fn)
check("the session-start call (env.MADEIRA_AUDIO_IO_MS) is unchanged: it still returns at once without the key",
      "static void madeira_audio_io_slice(void)\n{\n    const char *io = getenv(\"MADEIRA_AUDIO_IO_MS\");\n    double ms = io && *io ? atof(io) : 0;\n    if (ms <= 0 || ms > 100) return;" in bridge)
note = catalog_py[catalog_py.index('"env.MADEIRA_AUDIO_IO_MS"'):catalog_py.index('"env.MADEIRA_AUDIO_PERIOD_MS"')]
check("the settings catalog's note for env.MADEIRA_AUDIO_IO_MS tells the automatic request and that any value of the key switches it off",
      "automatic request" in note and "Any value of this key in the game's own file, 0 included, switches the automatic request off" in note)
check("the auto-tune rule audio-short-passes: fixedIn 652, no lines, spoken for a session that played, silent once the request was made",
      '"id": "audio-short-passes"' in rules_py and '"none": [s("[ios_audio] ml2300"), s("[audio] io buffer asked")]' in rules_py)
check("CI runs this check", "run: python3 tests/host/check-audio-io-auto.py" in workflow)

print("PASS: audio IO buffer request" if ok else "FAIL: audio IO buffer request")
sys.exit(0 if ok else 1)
