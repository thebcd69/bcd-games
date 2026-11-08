#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_AUDIO_RESUME: game audio after an interruption (audio_null_ios.c, WineProcessBridge.m).

iOS stops the audio output for a phone call, Siri, an alarm, another app's sound or when it
suspends the app, and nothing started it again (docs/research/ios18-2026-10-18/gameplay.md
section 2.1). With the switch, the app reports the audio session's events to the driver, which
plays its clients' rings out by the clock while the output is stopped and starts the output again.

Compiles the driver's real code (the stream and endpoint state, ios_dev_create_locked, the switch,
ios_dev_start, ios_stream_drain, madeira_audio_session_event, the clock helpers) against stubbed
AudioToolbox calls and a fake clock, and checks:
  - without the switch: an event does nothing (-1), a start that fails is the old null-mode line
    and nothing else, the drain never moves a position;
  - an interruption stops the output; the position then advances at the stream's rate, never past
    what the client wrote, and time with an empty ring is lost as an underrun is;
  - while iOS holds the output, a client's start and a route change start nothing;
  - the session's return starts the output (2) and the drain stops; a failed start keeps the clock
    running (0) until the next event; with no client playing nothing starts (3);
  - an output stopped behind the driver's back, or running with no render pass, starts again;
  - media services reset: a new unit in the same format, started;
  - with the switch, a start that fails at a client's start runs the clock as well.
And, from the sources: the timer loop drains only while the output is held; the bridge registers
its observers only when a switch is on, after stderr goes to the log, each checking its switch; the
log lines; the catalog entry (off by default); CI runs this check.
"""
from pathlib import Path
import re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/audio_null_ios.c").read_text()
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
catalog = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
workflow = (root / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


def cut(start, end):
    a = src.index(start)
    return src[a:src.index(end, a)]


def body(start):
    a = src.index(start)
    return src[a:src.index("\n}\n", a) + 3]


stream = cut("struct ios_stream {", "/* ml739: one stream object per client")
device = cut("struct ios_audio_device {", "static struct ios_stream *stream_from_handle(")
create = cut("static int ios_dev_create_locked(const struct ios_stream *s)", "/* Describe this stream's ring for the mixer")
resume = cut("/* madeira-bcd MADEIRA_AUDIO_RESUME (opt-in, off by default; 2026-10-18,", "static void ios_dev_stop_if_idle(void)")
live = cut("static int ios_stream_is_live(", "/* ---------------------------------------------------------------- */\n\n/* Render-buffer ownership")
clock = body("static uint64_t mach_to_ns(uint64_t mach) {") + body("static uint64_t ns_to_mach(uint64_t ns) {")
loop = body("static NTSTATUS ios_timer_loop(void *args) {")

# ---- sources ---------------------------------------------------------------------------------
check("the switch is read from the environment, and only \"1\" turns it on",
      'const char *e = getenv("MADEIRA_AUDIO_RESUME");' in resume and "return e && e[0] == '1' && !e[1];" in resume)
check("the output is held only by the event function and by a failed start with the switch",
      src.count("ios_dev_suspend_locked();") == 3
      and src.count("atomic_store_explicit(&g_dev_suspended, 1,") == 1
      and src.count("atomic_store_explicit(&g_dev_suspended, 0,") == 1
      and re.search(r"if \(err\) \{\n.*null-mode.*\n\s*if \(ios_audio_resume_on\(\)\) \{[^\n]*\n\s*ios_dev_suspend_locked\(\);", resume) is not None)
check("a client's start starts nothing while iOS holds the output",
      "if (g_dev.au && !g_dev.running && !g_bg_halt\n        && !atomic_load_explicit(&g_dev_suspended, memory_order_acquire)) {" in resume)   # BackgroundRun's halt (check-background-run.py) is the only other thing that holds a start
first = loop[loop.index("while (s->valid) {"):]
check("the timer loop drains only while the output is held, before its other branches",
      "if (atomic_load_explicit(&g_dev_suspended, memory_order_acquire))\n            ios_stream_drain(s);" in first
      and first.index("ios_stream_drain(s);") < first.index("if (!s->event || !s->started"))
check("the event function is exported (the app calls it) and does nothing without the switch",
      "\nint madeira_audio_session_event(int what)\n" in live and "if (!ios_audio_resume_on()) return -1;" in live)
check("a drain anchor is set at each start (ios_start)", "s->drain_mach = s->start_mach;" in src)

inst = bridge[bridge.index("static void madeira_engine_observers_install(void)"):]
inst = inst[:inst.index("\n}\n")]
check("the bridge registers nothing unless a switch is on, then once per app run",
      inst.index("if (!audio && !pause) return;") < inst.index("dispatch_once(&once, ^{"))
blocks = re.findall(r"addObserverForName:AVAudioSession\w+Notification object:session queue:q\s+usingBlock:\^\(NSNotification \*n\) \{\n(.*\n.*)\n", inst)


def first_statement(block):
    return next(l.strip() for l in block.split("\n") if l.strip() != "(void)n;")


check("every audio session observer checks the switch first (interruption, route, media reset)",
      len(blocks) == 3 and all(first_statement(b) == "if (!madeira_audio_resume_switch()) return;" for b in blocks))
check("an interruption that began stops the output, its end and the app's return restart it",
      "madeira_audio_session_event(0);" in inst and 'madeira_audio_resume("interruption ended", 1);' in inst
      and 'madeira_audio_resume("app active again", 0);' in inst and "madeira_audio_session_event(3)" in inst
      and "madeira_audio_session_event(2);" in inst)
check("the log says [audio] interruption began / ended, resumed=",
      '"[audio] interruption began (reason %lu)\\n"' in inst and '"[audio] %s, resumed=%d (%s%s)\\n"' in bridge)
thread = bridge[bridge.index("static void *wine_process_thread(void *arg) {"):]
check("the observers are installed after stderr goes to the session log",
      thread.index("dup2(logfd, STDERR_FILENO);") < thread.index("madeira_engine_observers_install();"))
check("the switch's own reads: the bridge's matches the driver's",
      'const char *v = getenv("MADEIRA_AUDIO_RESUME");' in bridge and "return v && v[0] == '1' && !v[1];" in bridge)
m = re.search(r'ConfigOption\(key: "env\.MADEIRA_AUDIO_RESUME", [^\n]*kind: \.bool, defaultValue: "0", category: "Audio"', catalog)
check("the settings catalog lists env.MADEIRA_AUDIO_RESUME, off by default", m is not None)
check("CI runs this check", "run: python3 tests/host/check-audio-resume.py" in workflow)

# ---- the driver's code against stubs --------------------------------------------------------
harness = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdatomic.h>
#include <pthread.h>
typedef unsigned char BYTE; typedef uint32_t UINT32, DWORD, UInt32; typedef uint64_t UINT64; typedef void *HANDLE;
typedef int32_t OSStatus;
typedef struct FakeUnit { int id; } *AudioUnit;
typedef struct FakeComponent *AudioComponent;
typedef uint32_t AudioUnitRenderActionFlags;
typedef struct { double mSampleTime; } AudioTimeStamp;
typedef struct { uint32_t mNumberChannels, mDataByteSize; void *mData; } AudioBuffer;
typedef struct { uint32_t mNumberBuffers; AudioBuffer mBuffers[1]; } AudioBufferList;
typedef struct { uint32_t componentType, componentSubType, componentManufacturer, componentFlags, componentFlagsMask; } AudioComponentDescription;
typedef struct { double mSampleRate; uint32_t mFormatID, mFormatFlags, mBytesPerPacket, mFramesPerPacket, mBytesPerFrame,
                 mChannelsPerFrame, mBitsPerChannel, mReserved; } AudioStreamBasicDescription;
typedef OSStatus (*AURenderCallback)(void *, AudioUnitRenderActionFlags *, const AudioTimeStamp *, UInt32, UInt32, AudioBufferList *);
typedef struct { AURenderCallback inputProc; void *inputProcRefCon; } AURenderCallbackStruct;
enum { noErr = 0, kAudioUnitType_Output = 1, kAudioUnitSubType_RemoteIO = 2, kAudioUnitManufacturer_Apple = 3,
       kAudioFormatLinearPCM = 4, kAudioFormatFlagIsFloat = 1, kAudioFormatFlagIsPacked = 8,
       kAudioUnitProperty_StreamFormat = 8, kAudioUnitScope_Global = 0, kAudioUnitScope_Input = 1,
       kAudioUnitProperty_SetRenderCallback = 23, kAudioOutputUnitProperty_IsRunning = 2001 };
#define IOS_MAX_STREAMS 16
#define IOS_AUDIO_SAMPLE_RATE 48000u

/* The clock: mach units are nanoseconds here. */
typedef struct { uint32_t numer, denom; } mach_timebase_info_data_t;
static void mach_timebase_info(mach_timebase_info_data_t *t) { t->numer = 1; t->denom = 1; }
static uint64_t fake_now = 1000000000ull;
#define mach_absolute_time() (fake_now)
static mach_timebase_info_data_t g_timebase;

/* AudioToolbox: counted, with a unit that iOS may have stopped (unit_isrunning) */
static struct FakeUnit units[8]; static int next_unit;
static int n_start, n_stop, n_dispose, n_new, start_fails, unit_isrunning;
static AudioComponent AudioComponentFindNext(AudioComponent c, const AudioComponentDescription *d) { (void)c; (void)d; return (AudioComponent)1; }
static OSStatus AudioComponentInstanceNew(AudioComponent c, AudioUnit *out) { (void)c; *out = &units[next_unit++ % 8]; n_new++; return 0; }
static OSStatus AudioComponentInstanceDispose(AudioUnit u) { (void)u; n_dispose++; return 0; }
static OSStatus AudioUnitSetProperty(AudioUnit u, uint32_t id, uint32_t sc, uint32_t el, const void *d, uint32_t sz)
{ (void)u; (void)id; (void)sc; (void)el; (void)d; (void)sz; return 0; }
static OSStatus AudioUnitGetProperty(AudioUnit u, uint32_t id, uint32_t sc, uint32_t el, void *d, uint32_t *sz)
{ (void)u; (void)sc; (void)el; (void)sz; if (id == kAudioOutputUnitProperty_IsRunning) *(UInt32 *)d = (UInt32)unit_isrunning; return 0; }
static OSStatus AudioUnitInitialize(AudioUnit u) { (void)u; return 0; }
static OSStatus AudioOutputUnitStart(AudioUnit u) { (void)u; n_start++; if (start_fails) return -12985; unit_isrunning = 1; return 0; }
static OSStatus AudioOutputUnitStop(AudioUnit u) { (void)u; n_stop++; unit_isrunning = 0; return 0; }
static OSStatus ios_audio_render_cb(void *r, AudioUnitRenderActionFlags *f, const AudioTimeStamp *t, UInt32 b, UInt32 n, AudioBufferList *io)
{ (void)r; (void)f; (void)t; (void)b; (void)n; (void)io; return 0; }
""" + stream + device + r"""
/* usleep: the liveness probe's wait; render passes happen meanwhile when passes_on_sleep */
static int sleeps, passes_on_sleep;
static void fake_usleep(unsigned us) { (void)us; sleeps++; if (passes_on_sleep) atomic_fetch_add(&g_dev.cb_epoch, 3); }
#define usleep fake_usleep
""" + clock + create + resume + live + r"""
static int fails;
#define EXPECT(cond, what) do { if (!(cond)) { printf("FAIL %s (line %d)\n", what, __LINE__); fails++; } } while (0)
static struct ios_stream st;

static void reset_all(void)
{
    int i;
    memset((void *)&g_dev, 0, sizeof(g_dev));
    for (i = 0; i < IOS_MAX_STREAMS; i++) atomic_store(&g_mix[i], NULL);
    atomic_store(&g_dev_suspended, 0); g_dev_suspend_mach = 0;
    n_start = n_stop = n_dispose = n_new = 0; start_fails = 0; unit_isrunning = 0; sleeps = 0; passes_on_sleep = 1;
    memset((void *)&st, 0, sizeof(st));
}
/* One started float32 stereo client at 48 kHz with 100 ms written, on a running endpoint. */
static void live_stream(int start)
{
    struct ios_stream fmt;
    memset(&fmt, 0, sizeof(fmt)); fmt.sample_rate = 48000; fmt.channels = 2;
    pthread_mutex_lock(&g_dev_lock); ios_dev_create_locked(&fmt); pthread_mutex_unlock(&g_dev_lock);
    st.valid = 1; st.started = 1; st.mixable = 1; st.sample_rate = 48000; st.channels = 2; st.buffer_frames = 9600;
    atomic_store(&st.write_pos, 4800); atomic_store(&st.play_pos, 0);
    st.drain_mach = fake_now;
    atomic_store(&g_mix[0], &st);
    if (start) ios_dev_start();
}
static uint64_t pos(void) { return atomic_load(&st.play_pos); }

int main(void)
{
    /* -- without the switch: nothing changes -- */
    unsetenv("MADEIRA_AUDIO_RESUME");
    reset_all(); live_stream(1);
    EXPECT(g_dev.running == 1 && n_start == 1, "off: the endpoint starts as before");
    EXPECT(madeira_audio_session_event(0) == -1 && g_dev.running == 1 && n_stop == 0, "off: an event does nothing");
    EXPECT(madeira_audio_session_event(1) == -1 && madeira_audio_session_event(2) == -1 && n_new == 1, "off: no restart, no rebuild");
    fake_now += 200000000ull; ios_stream_drain(&st);
    EXPECT(pos() == 0, "off: the drain never moves a position");
    reset_all(); live_stream(0); start_fails = 1; ios_dev_start();
    EXPECT(n_start == 1 && g_dev.running == 0 && !atomic_load(&g_dev_suspended), "off: a failed start is null-mode as before, no clock");

    /* -- an interruption -- */
    setenv("MADEIRA_AUDIO_RESUME", "1", 1);
    reset_all(); live_stream(1);
    EXPECT(madeira_audio_session_event(0) == 0, "began: 0");
    EXPECT(g_dev.running == 0 && n_stop == 1 && atomic_load(&g_dev_suspended) && g_dev_suspend_mach == fake_now,
           "began: the output stops and is held from now");
    fake_now += 50000000ull; ios_stream_drain(&st);
    EXPECT(pos() == 2400, "50 ms play 2400 frames at 48 kHz");
    fake_now += 100000000ull; ios_stream_drain(&st);
    EXPECT(pos() == 4800, "never past what the client wrote");
    atomic_store(&st.write_pos, 4800 + 960); ios_stream_drain(&st);
    EXPECT(pos() == 4800, "time with an empty ring is lost, not caught up");
    fake_now += 10000000ull; ios_stream_drain(&st);
    EXPECT(pos() == 5280, "then 10 ms play 480 frames");
    ios_dev_start();
    EXPECT(n_start == 1, "a client's start starts nothing while iOS holds the output");
    EXPECT(madeira_audio_session_event(3) == 0 && n_start == 1, "a route change starts nothing while iOS holds the output");
    EXPECT(madeira_audio_session_event(1) == 2, "the session is back: started again (2)");
    EXPECT(n_start == 2 && g_dev.running == 1 && !atomic_load(&g_dev_suspended), "running, no longer held");
    fake_now += 100000000ull; ios_stream_drain(&st);
    EXPECT(pos() == 5280, "the render callback owns the position again");
    EXPECT(madeira_audio_session_event(1) == 1 && n_start == 2 && n_stop == 1, "running and rendering: left alone (1)");

    /* -- a start that fails keeps the clock running until the next event -- */
    reset_all(); live_stream(1); madeira_audio_session_event(0); start_fails = 1;
    EXPECT(madeira_audio_session_event(1) == 0 && atomic_load(&g_dev_suspended) && g_dev.running == 0, "a failed restart: 0, still held");
    fake_now += 20000000ull; ios_stream_drain(&st);
    EXPECT(pos() == 960, "and the clock runs");
    start_fails = 0;
    EXPECT(madeira_audio_session_event(1) == 2 && g_dev.running == 1, "the next event starts it");

    /* -- nothing plays -- */
    reset_all(); live_stream(1); madeira_audio_session_event(0); st.started = 0;
    EXPECT(madeira_audio_session_event(1) == 3 && n_start == 1 && !atomic_load(&g_dev_suspended), "no client plays: 3, nothing started");
    st.started = 1; ios_dev_start();
    EXPECT(n_start == 2 && g_dev.running == 1, "the next client's start starts it");

    /* -- stopped behind the driver's back -- */
    reset_all(); live_stream(1); unit_isrunning = 0;
    EXPECT(madeira_audio_session_event(1) == 2 && n_stop == 1 && n_start == 2, "IsRunning 0: started again");
    reset_all(); live_stream(1); passes_on_sleep = 0;
    EXPECT(madeira_audio_session_event(1) == 2 && sleeps == 1 && n_stop == 1 && n_start == 2, "no render pass in the probe: started again");

    /* -- media services reset -- */
    reset_all(); live_stream(1);
    {
        AudioUnit old = g_dev.au;
        EXPECT(madeira_audio_session_event(2) == 2, "media reset: 2");
        EXPECT(n_dispose == 1 && n_new == 2 && g_dev.au != old && g_dev.rate == 48000 && g_dev.channels == 2 && g_dev.running == 1,
               "a new unit in the same format, running");
    }

    /* -- with the switch, a start that fails at a client's start runs the clock -- */
    reset_all(); live_stream(0); start_fails = 1; ios_dev_start();
    EXPECT(g_dev.running == 0 && atomic_load(&g_dev_suspended), "a failed start holds the output: the clock runs");
    fake_now += 10000000ull; ios_stream_drain(&st);
    EXPECT(pos() == 480, "the ring plays out by the clock");
    start_fails = 0;
    EXPECT(madeira_audio_session_event(1) == 2 && g_dev.running == 1, "the session event starts it");

    if (fails) return 1;
    puts("ok");
    return 0;
}
"""

cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP: no C compiler for the driver harness")
else:
    with tempfile.TemporaryDirectory() as t:
        c = Path(t) / "resume.c"
        c.write_text(harness)
        exe = Path(t) / "resume"
        b = subprocess.run([cc, "-std=c11", "-Wall", "-O1", "-o", str(exe), str(c), "-lpthread"], capture_output=True, text=True)
        check("the driver's resume code compiles against the stubs", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            if r.returncode:
                print(r.stdout[-3000:])
            check("every interruption scenario behaves (off: unchanged; on: held, drained, restarted)",
                  r.returncode == 0 and r.stdout.strip().endswith("ok"))

print("PASS: audio resume" if ok else "FAILED: audio resume")
sys.exit(0 if ok else 1)
