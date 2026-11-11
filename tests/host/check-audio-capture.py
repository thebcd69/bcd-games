#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_AUDIO_CAPTURE: one silent recording device for a game that wants one; and the first-audible-buffer line (night round 2026-11-11, G2 S2 and T2 S-AUD1).

BioShock's Steam forum thread says the game stays without sound after the intro on a PC with only an HDMI sound output, and that enabling "Stereo Mix" in the
recording devices fixes it. BCD's audio driver has never offered a recording device (the game's DirectSoundCaptureEnumerate calls back zero times). The key is
an EXPERIMENT for that game, OFF unless set: the driver then offers one eCapture endpoint, "iOS Null Input", that records nothing, never starts the output
endpoint and never touches the microphone.

The driver's real code (build/ntdll-unix/audio_null_ios.c: the key's reader, ios_get_endpoint_ids, ios_create_stream, ios_start, ios_stop,
ios_get_capture_buffer, ios_release_render_buffer and the first-audible helper) is cut out and compiled on the host against stubs, one process per value of the
key (it is read once):
  - unset, "", "0", "true", "11": a recording list of 0 endpoints, the render list exactly as before ("iOS Null", "ios-null"), one ml2302 line for the first
    request (and the 10th and the 100th), no capture stream is ever made, and nothing says the key is on;
  - "1": one capture endpoint "iOS Null Input" ("ios-null-input", default), the render endpoint unchanged, a buffer too small for it gets the "more space"
    answer, one line says the key is on;
  - a capture stream (only a keyed session has one) is made without being attached to the mixer, its start starts no output endpoint and its stop stops none (a
    render stream's start and stop still do), and its buffer is always "no data yet" (AUDCLNT_S_BUFFER_EMPTY: with S_OK and 0 frames Wine's dsound capture thread
    polls again at once, on a core);
  - the first audible buffer of a live stream (a sample of 0.001 or more): one ml2303 line per stream with the seconds since create_stream, the peak, and
    whether the endpoint is running, muted, paused, held by iOS or halted; silence and a whisper under the threshold print nothing; a stream with no endpoint
    (null mode) prints nothing; a second loud buffer prints nothing more.
And from the sources: the settings catalog lists the key (off by default, category Audio), the 32-bit (wow64) table forwards the capture buffer's result, CI runs
this check.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/audio_null_ios.c").read_text()
catalog = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-2500:]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


def cut(start, end):
    a = src.index(start)
    return src[a:src.index(end, a)]


def body(start):
    a = src.index(start)
    return src[a:src.index("\n}\n", a) + 3]


types = src[src.index("typedef int NTSTATUS;"):src.index("};", src.index("struct get_prop_value_params {")) + 2]
stream = cut("struct ios_stream {", "/* ml739: one stream object per client")
device = cut("struct ios_audio_device {", "static struct ios_stream *stream_from_handle(")
registry = cut("static struct ios_stream *stream_from_handle(", "/* ml738: this driver is a documented singleton")
live = body("static int ios_stream_is_live(")
capture_on = cut("static int ios_audio_capture_on(void)", "static NTSTATUS ios_get_endpoint_ids(void *args) {")
endpoints = body("static NTSTATUS ios_get_endpoint_ids(void *args) {")
create = body("static NTSTATUS ios_create_stream(void *args) {")
start = body("static NTSTATUS ios_start(void *args) {")
stop = body("static NTSTATUS ios_stop(void *args) {")
capbuf = cut("#define IOS_AUDCLNT_S_BUFFER_EMPTY", "static NTSTATUS ios_release_capture_buffer(void *args) {")
audible = cut("#define IOS_FIRST_AUDIBLE_PEAK", "static NTSTATUS ios_release_render_buffer(void *args) {") + body("static NTSTATUS ios_release_render_buffer(void *args) {")
names = (src[src.index("static const char IOS_DEVICE_NAME[]"):src.index("\n", src.index("static const char IOS_CAPTURE_DEVICE_NAME[]")) + 1])

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdatomic.h>
#include <pthread.h>
#include <time.h>
#include <unistd.h>
#define LOG_FN_CALL(n, s) do { } while (0)
#define IOS_AUDIO_SAMPLE_RATE 48000u
#define IOS_AUDIO_CHANNELS 2u
#define IOS_AUDIO_BITS 32u
typedef int OSStatus;
typedef struct FakeUnit { int id; } *AudioUnit;
typedef uint32_t UInt32;
''' + types + "\n" + names + "\n" + stream + r'''
#define IOS_MAX_STREAMS 16
static struct ios_stream *g_streams[IOS_MAX_STREAMS];
static pthread_mutex_t g_streams_lock = PTHREAD_MUTEX_INITIALIZER;
''' + device + registry + r'''
/* the clock: mach units are nanoseconds here */
static uint64_t fake_now = 1000000000ull;
#define mach_absolute_time() (fake_now)
static uint64_t mach_to_ns(uint64_t m) { return m; }
static unsigned int g_stream_gen;
static _Atomic int g_ios_audio_paused;
static _Atomic int g_dev_suspended;
''' + live + r'''
/* stubs for what the code under test calls */
static int n_attach, n_dev_start, n_dev_stop;
static uint64_t elapsed_frames(const struct ios_stream *s) { return s->accumulated_frames; }
static UINT32 ios_fmt_channel_mask(const struct WAVEFORMATEX_stub *fmt) { (void)fmt; return 3; }
static void ios_build_mix_gains(struct ios_stream *s, UINT32 mask) { (void)s; (void)mask; }
static BYTE *ios_audio_alloc_scratch(UINT32 frames, UINT32 frame_bytes, int *g) { *g = 0; return (BYTE *)calloc(frames, frame_bytes); }
static void ios_audio_free_scratch(BYTE *p, int g) { (void)g; free(p); }
static int ios_dev_attach(struct ios_stream *s, const struct WAVEFORMATEX_stub *fmt) { (void)fmt; n_attach++; s->mixable = 1; s->is_float = 1; s->sample_bits = 32; return 0; }
static void ios_dev_detach(struct ios_stream *s) { (void)s; }
static void ios_dev_start(void) { n_dev_start++; }
static void ios_dev_stop_if_idle(void) { n_dev_stop++; }
''' + capture_on + endpoints + "\n" + create + start + stop + capbuf + audible + r'''
static int fails;
#define EXPECT(cond, what) do { if (!(cond)) { printf("FAIL %s (line %d)\n", what, __LINE__); fails++; } } while (0)

static void wide(const char *buf, unsigned off, char *out, size_t n)
{
    const uint16_t *w = (const uint16_t *)(buf + off);
    size_t i;
    for (i = 0; i + 1 < n && w[i]; i++) out[i] = (char)w[i];
    out[i] = 0;
}

static void endpoint_report(const char *tag, int flow)
{
    char raw[256]; struct get_endpoint_ids_params p; char name[64];
    memset(raw, 0, sizeof raw);
    memset(&p, 0, sizeof p);
    p.flow = flow; p.endpoints = (struct endpoint *)raw; p.size = sizeof raw; p.result = -1; p.num = 99; p.default_idx = 99;
    ios_get_endpoint_ids(&p);
    if (p.num) wide(raw, p.endpoints[0].name, name, sizeof name); else name[0] = 0;
    printf("RESULT %s num=%u default=%u result=%#x name='%s' device='%s'\n", tag, p.num, p.default_idx, (unsigned)p.result, name,
           p.num ? raw + p.endpoints[0].device : "");
}

static struct ios_stream *make_stream(int flow, HANDLE ev)
{
    struct WAVEFORMATEX_stub f = { 0xFFFE, 2, 48000, 48000 * 8, 8, 32, 22 };
    struct create_stream_params p;
    stream_handle h = 0; UINT32 chans = 0;
    memset(&p, 0, sizeof p);
    p.name = NULL; p.device = "dev"; p.flow = flow; p.share = 0; p.flags = 0x40000; p.duration = 300000; p.period = 100000; p.fmt = &f;
    p.channel_count = &chans; p.stream = &h; p.result = -1;
    ios_create_stream(&p);
    (void)ev;
    return p.result == 0 ? (struct ios_stream *)(uintptr_t)h : NULL;
}

int main(int argc, char **argv)
{
    int scenario = argc > 1 ? atoi(argv[1]) : 0;
    pthread_mutex_init(&g_streams_lock, NULL);
    if (scenario == 0) {
        /* the lists, then a capture stream if one can exist */
        int i;
        endpoint_report("render", eRender);
        endpoint_report("capture", eCapture);
        for (i = 0; i < 120; i++) { struct get_endpoint_ids_params p; char raw[64]; memset(&p, 0, sizeof p); p.flow = eCapture; p.endpoints = (struct endpoint *)raw; p.size = sizeof raw; ios_get_endpoint_ids(&p); }
        { /* a buffer too small for one endpoint: the "more space" answer */
            char raw[16]; struct get_endpoint_ids_params p; memset(&p, 0, sizeof p);
            p.flow = eCapture; p.endpoints = (struct endpoint *)raw; p.size = 20; p.result = -1;
            ios_get_endpoint_ids(&p);
            printf("RESULT small num=%u result=%#x\n", p.num, (unsigned)p.result);
        }
        if (getenv("MADEIRA_AUDIO_CAPTURE") && !strcmp(getenv("MADEIRA_AUDIO_CAPTURE"), "1")) {
            struct ios_stream *cs = make_stream(eCapture, NULL), *rs = make_stream(eRender, NULL);
            struct stream_handle_params sp; struct get_capture_buffer_params gb; UINT32 frames = 77; BYTE *data = (BYTE *)1; UINT fl = 5;
            EXPECT(cs && cs->capture == 1 && cs->mixable == 0, "a capture stream is marked and never mixable");
            EXPECT(rs && rs->capture == 0, "a render stream is not a capture stream");
            EXPECT(n_attach == 1, "only the render stream is attached to the mixer");
            sp.stream = (stream_handle)(uintptr_t)cs; ios_start(&sp);
            EXPECT(cs->started == 1 && n_dev_start == 0, "a capture stream's start starts no output endpoint");
            sp.stream = (stream_handle)(uintptr_t)rs; ios_start(&sp);
            EXPECT(n_dev_start == 1, "a render stream's start still starts it");
            sp.stream = (stream_handle)(uintptr_t)cs; ios_stop(&sp);
            EXPECT(cs->started == 0 && n_dev_stop == 0, "a capture stream's stop stops none");
            sp.stream = (stream_handle)(uintptr_t)rs; ios_stop(&sp);
            EXPECT(n_dev_stop == 1, "a render stream's stop still does");
            memset(&gb, 0, sizeof gb);
            gb.stream = (stream_handle)(uintptr_t)cs; gb.frames = &frames; gb.data = &data; gb.flags = &fl; gb.result = -1;
            ios_get_capture_buffer(&gb);
            EXPECT(gb.result == 0x08890001 && frames == 0 && data == NULL && fl == 0, "the buffer is always 'no data yet' (AUDCLNT_S_BUFFER_EMPTY)");
        }
        printf("%s\n", fails ? "FAILED" : "scenario 0 passed");
        return fails ? 1 : 0;
    }
    if (scenario == 1) {
        /* the first audible buffer */
        struct ios_stream *s, *s2, *s3;
        struct release_render_buffer_params rp;
        float *f;
        UINT32 n = 480, i;
        g_dev.au = (AudioUnit)&(struct FakeUnit){ 1 }; g_dev.running = 1;
        s = make_stream(eRender, NULL);
        EXPECT(s && s->mixable && s->create_mach == fake_now, "a render stream remembers when it was created");
        atomic_store(&g_mix[0], s);
        f = (float *)s->render_scratch;
        memset(&rp, 0, sizeof rp); rp.stream = (stream_handle)(uintptr_t)s; rp.written_frames = n;
        s->pending_frames = n;
        memset(s->render_scratch, 0, (size_t)n * s->frame_bytes);
        ios_release_render_buffer(&rp);
        s->pending_frames = n;
        for (i = 0; i < n * 2; i++) f[i] = 0.0005f;   /* a whisper under the threshold */
        ios_release_render_buffer(&rp);
        printf("RESULT audible_after_quiet=%d\n", s->audible_logged);
        fake_now += 2500000000ull;   /* 2.5 s after create_stream */
        s->pending_frames = n;
        for (i = 0; i < n * 2; i++) f[i] = 0.3f;
        ios_release_render_buffer(&rp);
        printf("RESULT audible_after_loud=%d\n", s->audible_logged);
        s->pending_frames = n;
        ios_release_render_buffer(&rp);
        /* a second stream, the endpoint stopped and muted */
        atomic_store(&g_mix[0], NULL);
        s2 = make_stream(eRender, NULL);
        atomic_store(&g_mix[1], s2);
        g_dev.running = 0; atomic_store(&g_bg_mute, 1);
        s2->pending_frames = n; f = (float *)s2->render_scratch;
        for (i = 0; i < n * 2; i++) f[i] = 0.05f;
        rp.stream = (stream_handle)(uintptr_t)s2;
        ios_release_render_buffer(&rp);
        /* a third: paused, held, halted; and the line for a stream with no endpoint at all */
        atomic_store(&g_mix[1], NULL);
        s3 = make_stream(eRender, NULL);
        atomic_store(&g_mix[2], s3);
        g_dev.running = 1; atomic_store(&g_bg_mute, 0); atomic_store(&g_ios_audio_paused, 1); atomic_store(&g_dev_suspended, 1); g_bg_halt = 1;
        s3->pending_frames = n; f = (float *)s3->render_scratch;
        for (i = 0; i < n * 2; i++) f[i] = -0.7f;
        rp.stream = (stream_handle)(uintptr_t)s3;
        ios_release_render_buffer(&rp);
        {
            struct ios_stream *s4 = make_stream(eRender, NULL);
            g_dev.au = NULL;   /* null mode: no endpoint */
            atomic_store(&g_mix[3], s4);
            s4->pending_frames = n; f = (float *)s4->render_scratch;
            for (i = 0; i < n * 2; i++) f[i] = 0.9f;
            rp.stream = (stream_handle)(uintptr_t)s4;
            ios_release_render_buffer(&rp);
            printf("RESULT audible_null_mode=%d\n", s4->audible_logged);
        }
        printf("%s\n", fails ? "FAILED" : "scenario 1 passed");
        return fails ? 1 : 0;
    }
    return 2;
}
'''

cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP host run: no C compiler")
else:
    with tempfile.TemporaryDirectory() as tmp:
        cfile = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        open(cfile, "w").write(harness)
        build = subprocess.run([cc, "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-but-set-variable", "-Wno-macro-redefined", "-o", exe, cfile, "-lpthread"],
                               capture_output=True, text=True)
        check("the driver's real endpoint, stream and first-audible code builds on the host, -Wall -Werror", build.returncode == 0, build.stderr)
        if build.returncode == 0:
            def run(scenario, key):
                env = {k: v for k, v in os.environ.items() if k != "MADEIRA_AUDIO_CAPTURE"}
                if key is not None:
                    env["MADEIRA_AUDIO_CAPTURE"] = key
                r = subprocess.run([exe, str(scenario)], capture_output=True, text=True, timeout=60, env=env)
                res = {}
                for line in r.stdout.split("\n"):
                    if line.startswith("RESULT "):
                        k, _, v = line[7:].partition(" ")
                        res[k.split("=")[0]] = line[7:]
                return r, res

            render_expected = "render num=1 default=0 result=0 name='iOS Null' device='ios-null'"
            for key in (None, "", "0", "true", "11"):
                r, res = run(0, key)
                label = "unset" if key is None else repr(key)
                check(f"MADEIRA_AUDIO_CAPTURE {label}: no recording device, the render list exactly as before, nothing says the key is on",
                      r.returncode == 0 and "scenario 0 passed" in r.stdout and res.get("render") == render_expected
                      and res.get("capture") == "capture num=0 default=0 result=0 name='' device=''" and "MADEIRA_AUDIO_CAPTURE=1" not in r.stderr, r.stdout + r.stderr)
                ml = [l for l in r.stderr.split("\n") if "ml2302" in l]
                check(f"MADEIRA_AUDIO_CAPTURE {label}: ml2302 is printed for the 1st, 10th and 100th request only",
                      len(ml) == 3 and "call 1" in ml[0] and "call 10" in ml[1] and "call 100" in ml[2], r.stderr)
            r, res = run(0, "1")
            check("MADEIRA_AUDIO_CAPTURE=1: one capture endpoint 'iOS Null Input', the default; the render endpoint unchanged; the 'more space' answer",
                  r.returncode == 0 and "scenario 0 passed" in r.stdout and res.get("render") == render_expected
                  and res.get("capture") == "capture num=1 default=0 result=0 name='iOS Null Input' device='ios-null-input'"
                  and res.get("small") == "small num=1 result=0x80070057", r.stdout + r.stderr)
            check("MADEIRA_AUDIO_CAPTURE=1: the line says so once and no refusal is printed",
                  r.stderr.count("MADEIRA_AUDIO_CAPTURE=1: one silent recording device 'iOS Null Input' is offered") == 1 and "ml2302" not in r.stderr, r.stderr)
            check("MADEIRA_AUDIO_CAPTURE=1: the capture stream is never mixed, starts and stops no output endpoint, and its buffer is always empty",
                  "FAIL" not in r.stdout, r.stdout)
            r, res = run(1, None)
            lines = [l for l in r.stderr.split("\n") if "ml2303" in l]
            check("first audible buffer: silence and a whisper print nothing, the first loud buffer prints one line, the second loud buffer prints nothing more",
                  r.returncode == 0 and "scenario 1 passed" in r.stdout and res.get("audible_after_quiet") == "audible_after_quiet=0"
                  and res.get("audible_after_loud") == "audible_after_loud=1" and len(lines) == 3, r.stdout + r.stderr)
            check("the first line: slot, 2.50 s after create_stream, the peak, endpoint running and nothing muted, paused, held or halted",
                  len(lines) >= 1 and "ml2303 slot=0 first audible buffer 2.50 s after create_stream (peak 0.300; endpoint running, mute=0, paused=0, held=0, halt=0)" in lines[0],
                  lines)
            check("a muted stopped endpoint is said (endpoint stopped, mute=1)", len(lines) >= 2 and "endpoint stopped, mute=1, paused=0, held=0, halt=0" in lines[1], lines)
            check("paused, held by iOS and halted are said", len(lines) >= 3 and "peak 0.700; endpoint running, mute=0, paused=1, held=1, halt=1" in lines[2], lines)
            check("a stream with no endpoint (null mode) prints no line", res.get("audible_null_mode") == "audible_null_mode=0", r.stdout)

# ---- from the sources ----------------------------------------------------------------------------------------------------------------
check("the settings catalog lists env.MADEIRA_AUDIO_CAPTURE, off by default, in Audio",
      'ConfigOption(key: "env.MADEIRA_AUDIO_CAPTURE", title: "Offer one silent recording device", kind: .bool, defaultValue: "0", category: "Audio"' in catalog)
w = src[src.index("static NTSTATUS ios_wow64_get_capture_buffer(void *args)"):]
w = w[:w.index("\n}\n")]
check("the 32-bit table forwards the capture buffer's result (BioShock is a 32-bit game)",
      "NTSTATUS status = ios_get_capture_buffer(&params);" in w and "params32->result = params.result;" in w)
check("the capture branch is the only place the key is read, once",
      src.count('getenv("MADEIRA_AUDIO_CAPTURE")') == 1 and "static int cached = -1;" in capture_on)
check("CI runs this check", "run: python3 tests/host/check-audio-capture.py" in workflow)

print("PASS: audio capture endpoint and first audible buffer" if ok else "FAIL: audio capture endpoint and first audible buffer")
sys.exit(0 if ok else 1)
