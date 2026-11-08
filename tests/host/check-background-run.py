#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 ft7t8drgwf-wq
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
# madeira-bcd: adapted from Madeira's tests/host/check-background-run.py (#498) for BCD's feature, which is OFF until
# Settings > Memory & performance > In the background turns it on. The BCD additions are the "off" scenarios, the lazy
# Metal gate, the Settings rows and the host runs of the C code.
# Portions copyright 2026 ft7t8drgwf-wq: code taken from https://github.com/willfaust/Madeira (pull request #498)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""BCD keeps games and downloads running when it leaves the screen, if the player asks it to
(app/Madeira/BackgroundRun.swift, build/ntdll-unix/audio_null_ios.c, Winios/BackgroundCPUCap.c, Winios/MetalBackgroundGate.m).

What it proves, and the point of most of it: with the setting OFF (the default, no key in bcd.cfg) BCD behaves as it did
before the "audio" background mode was declared.

Source checks (every host): the "audio" background mode in Info.plist; the audio driver's keepalive / mute / halt switch
and its restart paths (all three zero until the app sets them); the bridging header; the app starts BackgroundRun; both
download helpers (Steam, the stores) take their background time through BackgroundRun instead of UIApplication; the
catalog lines; the project file; the Settings rows (the default writes nothing); the CPU governor's and the Metal gate's
invariants.

Host runs (macOS or Linux with a C compiler): the audio driver's real code against stubbed AudioToolbox (halt stops the
endpoint and a client start cannot restart it; releasing it starts it for a playing client; a keep-alive makes the
endpoint at the driver's own format; the last client stopping keeps a kept endpoint; mute silences after mixing while the
clock epoch advances; a stalled endpoint restarts; MADEIRA_AUDIO_RESUME's held endpoint is not started by it); the CPU
governor on four busy threads (macOS); the Metal gate on a real command buffer (macOS with a Metal device).

The Swift fixture compiles the real BackgroundRun.swift against stand-ins for UIKit, AVFoundation, UserNotifications and
the driver, and drives it through the app's notifications, in separate runs:
  off      the defaults: nothing but the halt/release of the audio endpoint, no category change, no CPU cap, no Metal gate, no
           interruption handling, no notification; downloads get iOS's grace period exactly as before;
  on       a game keeps running (muted, gate made once, CPU held) and stops when its minutes are used up; per-game keys win;
           a download keeps going past iOS's grace period while BCD runs, and pauses when nothing keeps it;
  dlgame   downloads on, games off, a game running: the download keeps BCD running and the Metal gate holds the game's GPU work;
  nogate   the Metal gate cannot be made: the game is not kept running;
  gateoff  background-gpu-gate = 0: kept without the gate.
Linux hosts without Swift run the rest; --require-swift turns a missing compiler into a failure.
"""
from pathlib import Path
import argparse
import os
import platform
import re
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--require-swift', action='store_true')
parser.add_argument('--require-metal', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
run_src = (app / 'BackgroundRun.swift').read_text()
driver = (root / 'build/ntdll-unix/audio_null_ios.c').read_text()
plist = (app / 'Info.plist').read_text()
bridging = (app / 'Madeira-Bridging-Header.h').read_text()
madeira_app = (app / 'MadeiraApp.swift').read_text()
catalog = (app / 'ConfigCatalog.generated.swift').read_text()
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
project = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text()
workflow = (root / '.github/workflows/build-ipa.yml').read_text()
sections = (app / 'BCDSettingsSections.swift').read_text()
live = (app / 'BCDSettingsLive.swift').read_text()
preview = (root / 'app/BCDPreview/BCDSettingsPreviewData.swift').read_text()
cap = (app / 'Winios/BackgroundCPUCap.c').read_text()
gate = (app / 'Winios/MetalBackgroundGate.m').read_text()
failures = 0


def check(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


def body(source, start):
    i = source.index(start)
    return source[i:source.index('\n}\n', i)]


def cut(source, start, end):
    a = source.index(start)
    return source[a:source.index(end, a)]


# ---- Info.plist, the driver, the bridge ---------------------------------------------------------------------------------
check('<key>UIBackgroundModes</key>\n\t<array>\n\t\t<string>audio</string>\n\t</array>' in plist,
      'Info.plist declares the audio background mode')
bg = body(driver, 'int madeira_audio_background(int keepalive, int mute, int halt)')
check('g_bg_keepalive = keepalive && !halt;' in bg and 'g_bg_halt = halt ? 1 : 0;' in bg, 'halt wins over keepalive')
check('ios_dev_create_locked(&fmt)' in bg and 'fmt.sample_rate = IOS_AUDIO_SAMPLE_RATE;' in bg,
      'keepalive makes the endpoint at the driver mix format when no client has')
check('if (g_dev.au && !g_dev.running && !g_bg_halt && (any || g_bg_keepalive)' in bg
      and '!atomic_load_explicit(&g_dev_suspended, memory_order_acquire)' in bg, 'starts unless halted or held for MADEIRA_AUDIO_RESUME')
check('(g_bg_halt || (!any && !g_bg_keepalive))' in bg, 'stops when halted, or when nothing wants it')
check('pthread_mutex_lock(&g_dev_lock);' in bg and 'pthread_mutex_unlock(&g_dev_lock);' in bg, 'under g_dev_lock')
start = body(driver, 'static void ios_dev_start(void)')
check('!g_dev.running && !g_bg_halt' in start, 'a client start does not restart a halted endpoint')
idle = body(driver, 'static void ios_dev_stop_if_idle(void)')
check('!any && !g_bg_keepalive' in idle, 'the last client stopping keeps a keepalive endpoint running')
cb = body(driver, 'static OSStatus ios_audio_render_cb(')
check(cb.index('ios_mix_stream(s, out') < cb.index('g_bg_mute') < cb.rindex('cb_epoch'),
      'mute silences after mixing, so rings drain and clocks keep time')
check(re.search(r'^static int g_bg_keepalive;\nstatic int g_bg_halt;\nstatic _Atomic int g_bg_mute;$', driver, re.M) is not None,
      'keepalive, halt and mute are zero until the app sets them (no initialiser)')
resume = body(driver, 'void madeira_audio_resume_after_interruption(void)')
check('AudioOutputUnitStop(g_dev.au);' in resume and 'AudioOutputUnitStart(g_dev.au);' in resume
      and 'g_dev.running' in resume, 'a running endpoint is restarted after an interruption')
check('int madeira_audio_background(int keepalive, int mute, int halt);' in bridging
      and 'void madeira_audio_resume_after_interruption(void);' in bridging
      and 'int madeira_audio_check(void);' in bridging and 'void madeira_bg_cpu_cap(int percent);' in bridging
      and 'void madeira_bg_cpu_stats(double *cpu_s, double *wall_s, double *held_s);' in bridging
      and 'int madeira_metal_gate_install(void);' in bridging and 'void madeira_metal_gate_hold(int inactive);' in bridging,
      'bridging header')
check('BackgroundRun.shared.start()' in madeira_app, 'the app starts BackgroundRun')
check('int madeira_audio_check(void)' in driver and 'if (last_valid && e == last_epoch)' in driver,
      'audio: a running endpoint that rendered nothing between two checks is restarted')
check('if (ios_dev_create_locked(&fmt) && !failed_before) g_dev.failed = 0;' in driver,
      "audio: a keep-alive that cannot make the endpoint does not take the game's sound away")

# ---- the download helpers ----------------------------------------------------------------------------------------------
for name, label in [('SteamDownloadBackground.swift', 'Steam'), ('BCDContent/BCDContentBackground.swift', 'the stores')]:
    src = (app / name).read_text()
    check('BackgroundRun.shared.beginTask(' in src and 'BackgroundRun.shared.endTask(' in src
          and 'beginBackgroundTask' not in src and 'endBackgroundTask' not in src,
          f'{label} downloads take their background time through BackgroundRun')

# ---- BackgroundRun's defaults, the catalog ----------------------------------------------------------------------------------
check('minutes(MadeiraConfig.gameValue("background-run") ?? MadeiraConfig.get("background-run"), default: 0)' in run_src
      and 'minutes(MadeiraConfig.get("background-download"), default: 0)' in run_src,
      'BackgroundRun: a game and a download are kept for 0 minutes (off) unless a key says more')
for key, kind, default in [('background-run', '.int', '0'), ('background-download', '.int', '0'),
                           ('background-sound', '.bool', '0'), ('background-gpu-gate', '.bool', '1'),
                           ('background-cpu', '.int', '50')]:
    check(f'ConfigOption(key: "{key}", ' in catalog and f'kind: {kind}, defaultValue: "{default}", category: "App & front end"'
          in catalog.split(f'ConfigOption(key: "{key}", ')[1].split('\n')[0] and f'"{key}": {{' in gen,
          f'catalog: {key} ({default})')
    check(f'MadeiraConfig.get("{key}")' in run_src, f'BackgroundRun reads {key}')
check(sum('BackgroundRun.swift' in line for line in project.splitlines()) == 4, 'BackgroundRun.swift is in the app target')
check(sum('Winios/BackgroundCPUCap.c' in line for line in project.splitlines()) == 4, 'BackgroundCPUCap.c is in the app target')
check(sum('Winios/MetalBackgroundGate.m' in line for line in project.splitlines()) == 4, 'MetalBackgroundGate.m is in the app target')
check('run: python3 tests/host/check-background-run.py' in workflow, 'CI runs this check')

# ---- the CPU governor, the Metal gate (invariants; the host runs below prove the behaviour) -----------------------------
loop = body(cap, 'static void *cap_main(void *arg)')
check(loop.index('cap_resume_all()') < loop.index('pthread_mutex_lock(&cap_lock);\n            stop')
      and loop.index('if (stop) break;') < loop.index('if (credit < 0) cap_hold_all();'),
      'CPU cap: held threads run again first each tick, and nothing is held once it is told to stop')
check('if (credit > 2.0) credit = 2.0;' in loop and 'credit += dt * percent / 100.0 - (cpu - last_cpu);' in loop,
      'CPU cap: budget of percent of one core with at most 2 s of credit')
hold = body(cap, 'static void cap_hold_all(void)')
check(hold.index('cap_exempt(list[i])') < hold.index('thread_suspend('), 'CPU cap: names and policies are read before any thread is held')
ex = body(cap, 'static int cap_exempt(thread_act_t t)')
check('t == cap_main_port' in ex and 'THREAD_TIME_CONSTRAINT_POLICY' in ex and '!get_default' in ex and 'AURemoteIO' in ex,
      'CPU cap: the main thread, real-time threads and the audio threads keep running')
check('list[i] == self' in hold, 'CPU cap: the governor never holds itself')
stopfn = body(cap, 'void madeira_bg_cpu_cap(int percent)')
check('while (!cap_idle && pthread_cond_timedwait' in stopfn, 'CPU cap: turning it off waits until every thread runs again')
commit = body(gate, 'static void mbg_commit(id self, SEL cmd)')
check('if (![NSThread isMainThread]) mbg_wait();' in commit and commit.index('mbg_wait') < commit.index('(self, cmd)'),
      'Metal gate: off the main thread a commit waits first, then the original runs')
check('UIApplicationWillResignActiveNotification' in gate and 'UIApplicationDidBecomeActiveNotification' in gate
      and 'while (mbg_inactive) pthread_cond_wait' in gate, 'Metal gate: waits from WillResignActive until DidBecomeActive')
wrap = body(gate, 'static void mbg_wrap(Class cls)')
check(wrap.index('mbg_orig[mbg_count] = orig;') < wrap.index('__atomic_store_n(&mbg_count') < wrap.index('class_addMethod(cls, @selector(commit), (IMP)mbg_commit')
      and 'method_setImplementation(m, (IMP)mbg_commit)' in wrap,
      'Metal gate: the original is published before the class dispatches to the wrapper (an inherited commit is overridden on the class, its own replaced)')
check('MadeiraConfig.get("background-gpu-gate") == "0"' in run_src
      and 'madeira_metal_gate_install()' in cut(run_src, '    private func gateAllows() -> Bool {', '    // MARK: Leaving and coming back')
      and 'madeira_metal_gate_install' not in cut(run_src, '    func start() {', '    // MARK: Downloads'),
      'Metal gate: made when a game is first kept running, never at start; background-gpu-gate = 0 leaves it out')

# ---- Settings -------------------------------------------------------------------------------------------------------------------
check('var backgroundGame: String { get }' in sections and 'var backgroundDownload: String { get }' in sections
      and 'var backgroundSound: Bool { get }' in sections, 'Settings: the memory backend carries the three values')
check('BCDSettingsGroup(title: "In the background"' in sections
      and 'BCDSettingsID.control(.memory, "background-game")' in sections and 'BCDSettingsID.control(.memory, "background-download")' in sections
      and 'BCDSettingsID.control(.memory, "background-sound")' in sections, 'Settings: Memory & performance has the In the background group')
check('BCDSettingsChoice(value: "", label: "Off")' in sections.split('static func choices(current:')[1].split('}')[0],
      'Settings: every minutes row offers Off (no line) first')
check('MadeiraConfig.set(BCDLiveKey.backgroundRun, Self.minutes(minutes).isEmpty ? nil : Self.minutes(minutes))' in live
      and 'MadeiraConfig.set(BCDLiveKey.backgroundDownload, Self.minutes(minutes).isEmpty ? nil : Self.minutes(minutes))' in live
      and 'MadeiraConfig.set(BCDLiveKey.backgroundSound, on ? "1" : nil)' in live,
      'Settings: Off and muted write no line at all (the engine default)')
check('backgroundGame = Self.minutes(MadeiraConfig.get(BCDLiveKey.backgroundRun))' in live
      and 'guard let raw, let m = Double(raw), m > 0 else { return "" }' in live, 'Settings: reads the keys as the engine does (0 or junk is off)')
check('func setBackgroundGame' in preview and 'var backgroundGame' in preview or '@Published private(set) var backgroundGame' in preview,
      'Settings: the preview app has the rows too')
check('Limits, the swap file, sync, eco mode and the background' in (app / 'BCDSettingsCore.swift').read_text(),
      'Settings: the Memory & performance list line names the background')

# ---- host runs of the C code ----------------------------------------------------------------------------------------------------
cc = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
stream = cut(driver, 'struct ios_stream {', '/* ml739: one stream object per client')
device = cut(driver, 'struct ios_audio_device {', 'static struct ios_stream *stream_from_handle(')
create = cut(driver, 'static int ios_dev_create_locked(const struct ios_stream *s)', "/* Describe this stream's ring for the mixer")
held = cut(driver, '/* madeira-bcd MADEIRA_AUDIO_RESUME (opt-in, off by default; 2026-10-18,', '/* ml1026: "this client feeds the real endpoint"')
callback = cut(driver, '/* Core Audio real-time thread. See ios_mix_stream for the constraints. */', '/* Parse the WASAPI format into')

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdatomic.h>
#include <pthread.h>
typedef unsigned char BYTE; typedef uint32_t UINT32, DWORD, UInt32; typedef uint64_t UINT64; typedef void *HANDLE;
typedef int32_t OSStatus; typedef int BOOL;
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
#define IOS_AUDIO_CHANNELS 2u

typedef struct { uint32_t numer, denom; } mach_timebase_info_data_t;
static void mach_timebase_info(mach_timebase_info_data_t *t) { t->numer = 1; t->denom = 1; }
static uint64_t fake_now = 1000000000ull;
#define mach_absolute_time() (fake_now)
static mach_timebase_info_data_t g_timebase;

static struct FakeUnit units[8]; static int next_unit;
static int n_start, n_stop, n_dispose, n_new, start_fails, unit_isrunning, create_fails;
static AudioComponent AudioComponentFindNext(AudioComponent c, const AudioComponentDescription *d) { (void)c; (void)d; return (AudioComponent)1; }
static OSStatus AudioComponentInstanceNew(AudioComponent c, AudioUnit *out) { (void)c; if (create_fails) return -1; *out = &units[next_unit++ % 8]; n_new++; return 0; }
static OSStatus AudioComponentInstanceDispose(AudioUnit u) { (void)u; n_dispose++; return 0; }
static OSStatus AudioUnitSetProperty(AudioUnit u, uint32_t id, uint32_t sc, uint32_t el, const void *d, uint32_t sz)
{ (void)u; (void)id; (void)sc; (void)el; (void)d; (void)sz; return 0; }
static OSStatus AudioUnitGetProperty(AudioUnit u, uint32_t id, uint32_t sc, uint32_t el, void *d, uint32_t *sz)
{ (void)u; (void)sc; (void)el; (void)sz; if (id == kAudioOutputUnitProperty_IsRunning) *(UInt32 *)d = (UInt32)unit_isrunning; return 0; }
static OSStatus AudioUnitInitialize(AudioUnit u) { (void)u; return 0; }
static OSStatus AudioOutputUnitStart(AudioUnit u) { (void)u; n_start++; if (start_fails) return -12985; unit_isrunning = 1; return 0; }
static OSStatus AudioOutputUnitStop(AudioUnit u) { (void)u; n_stop++; unit_isrunning = 0; return 0; }
static _Atomic int g_ios_audio_paused;
''' + stream + device + r'''
static void ios_mix_stream(struct ios_stream *s, float *out, UInt32 nframes, UInt32 ch, UInt32 rate)
{ UInt32 k; (void)s; (void)rate; for (k = 0; k < nframes * ch; k++) out[k] += 0.25f; }
''' + callback + create + held + r'''
static int fails;
#define EXPECT(cond, what) do { if (!(cond)) { printf("FAIL %s (line %d)\n", what, __LINE__); fails++; } } while (0)
static struct ios_stream st;

static void reset_all(void)
{
    int i;
    memset((void *)&g_dev, 0, sizeof(g_dev));
    for (i = 0; i < IOS_MAX_STREAMS; i++) atomic_store(&g_mix[i], NULL);
    atomic_store(&g_dev_suspended, 0); g_dev_suspend_mach = 0;
    g_bg_keepalive = 0; g_bg_halt = 0; atomic_store(&g_bg_mute, 0);
    n_start = n_stop = n_dispose = n_new = 0; start_fails = 0; create_fails = 0; unit_isrunning = 0;
    memset((void *)&st, 0, sizeof(st));
}
/* One started float32 stereo client at 48 kHz on a made endpoint; start != 0: the client starts it, as ios_start does. */
static void live_stream(int start)
{
    struct ios_stream fmt;
    memset(&fmt, 0, sizeof(fmt)); fmt.sample_rate = 48000; fmt.channels = 2;
    pthread_mutex_lock(&g_dev_lock); ios_dev_create_locked(&fmt); pthread_mutex_unlock(&g_dev_lock);
    st.valid = 1; st.started = 1; st.mixable = 1; st.sample_rate = 48000; st.channels = 2; st.buffer_frames = 9600;
    atomic_store(&g_mix[0], &st);
    if (start) ios_dev_start();
}
static float render_once(uint64_t *epoch)
{
    float buf[64]; AudioBufferList l; memset(buf, 0, sizeof buf);
    l.mNumberBuffers = 1; l.mBuffers[0].mNumberChannels = 2; l.mBuffers[0].mDataByteSize = sizeof buf; l.mBuffers[0].mData = buf;
    ios_audio_render_cb(NULL, NULL, NULL, 0, 32, &l);
    *epoch = atomic_load(&g_dev.cb_epoch);
    return buf[0];
}

int main(void)
{
    uint64_t e0, e1;

    /* -- nothing set by the app: the driver is what it was -- */
    reset_all(); live_stream(1);
    EXPECT(g_dev.running == 1 && n_start == 1 && g_bg_keepalive == 0 && g_bg_halt == 0, "default: a client's start starts the endpoint");
    ios_dev_stop_if_idle();
    EXPECT(g_dev.running == 0 || st.started, "default: with a started client the endpoint stays");
    st.started = 0; ios_dev_stop_if_idle();
    EXPECT(g_dev.running == 0 && n_stop == 1, "default: the last client stopping stops the endpoint");
    reset_all(); live_stream(1);
    render_once(&e0);
    EXPECT(render_once(&e1) == 0.25f && e1 == e0 + 1, "default: the render callback mixes and is not muted");

    /* -- halt: the app, entering the background with nothing to keep -- */
    reset_all(); live_stream(1);
    EXPECT(madeira_audio_background(0, 1, 1) == 0, "halt: the endpoint is not running afterwards");
    EXPECT(g_dev.running == 0 && n_stop == 1 && g_bg_halt == 1 && g_bg_keepalive == 0, "halt: stopped, flagged");
    ios_dev_start();
    EXPECT(n_start == 1 && g_dev.running == 0, "halt: a client's start cannot restart the endpoint");
    EXPECT(madeira_audio_background(0, 1, 1) == 0 && n_start == 1 && n_stop == 1, "halt: asking again changes nothing (no second stop)");
    /* release: BCD is active again */
    EXPECT(madeira_audio_background(0, 0, 0) == 1, "release: the endpoint runs for the playing client");
    EXPECT(g_dev.running == 1 && n_start == 2 && g_bg_halt == 0 && atomic_load(&g_bg_mute) == 0, "release: started once, unmuted");
    render_once(&e0);
    EXPECT(render_once(&e1) == 0.25f, "release: the game is heard again");
    /* release without a playing client starts nothing */
    reset_all(); live_stream(0); st.started = 0;
    madeira_audio_background(0, 1, 1);
    EXPECT(madeira_audio_background(0, 0, 0) == 0 && n_start == 0, "release: no client plays, nothing starts");
    /* halt without an endpoint: only the flags */
    reset_all();
    EXPECT(madeira_audio_background(0, 1, 1) == 0 && n_new == 0 && n_start == 0 && n_stop == 0 && g_dev.au == NULL,
           "halt: no endpoint exists, none is made");
    EXPECT(madeira_audio_background(0, 0, 0) == 0 && n_new == 0, "release: no endpoint, none is made");

    /* -- keepalive: a download, or a game between its sounds -- */
    reset_all();
    EXPECT(madeira_audio_background(1, 1, 0) == 1, "keepalive: no client, the endpoint is made and runs");
    EXPECT(n_new == 1 && g_dev.rate == 48000 && g_dev.channels == 2 && n_start == 1 && g_dev.running == 1, "keepalive: 48 kHz stereo, started once");
    EXPECT(madeira_audio_background(1, 1, 0) == 1 && n_start == 1 && n_new == 1, "keepalive: asking again changes nothing");
    ios_dev_stop_if_idle();
    EXPECT(g_dev.running == 1 && n_stop == 0, "keepalive: the idle check leaves a kept endpoint running");
    EXPECT(madeira_audio_background(0, 0, 0) == 0 && g_dev.running == 0 && n_stop == 1, "keepalive released with no client: the endpoint stops");
    reset_all(); create_fails = 1;
    EXPECT(madeira_audio_background(1, 1, 0) == 0 && g_dev.failed == 0, "keepalive: an endpoint that cannot be made is not a failure for the game's own first client");
    create_fails = 0;
    live_stream(1);
    EXPECT(g_dev.running == 1, "keepalive: ...which makes it later");
    reset_all(); live_stream(0);
    atomic_store(&g_dev_suspended, 1);
    madeira_audio_background(1, 1, 0);
    EXPECT(n_start == 0 && g_dev.running == 0, "keepalive: an endpoint iOS holds stopped (MADEIRA_AUDIO_RESUME) is left to the session event (1dbd877)");

    /* -- mute -- */
    reset_all(); live_stream(1);
    madeira_audio_background(1, 1, 0);
    EXPECT(render_once(&e0) == 0.0f, "mute: the output is silent");
    EXPECT(render_once(&e1) == 0.0f && e1 == e0 + 1, "mute: the callback still runs, its epoch advances (rings drain, clocks keep time)");
    madeira_audio_background(0, 0, 0);
    EXPECT(render_once(&e0) == 0.25f, "unmute: sound again");
    atomic_store(&g_ios_audio_paused, 1);
    EXPECT(render_once(&e0) == 0.0f, "the in-game Pause still silences (unchanged)");
    atomic_store(&g_ios_audio_paused, 0);

    /* -- madeira_audio_check -- */
    reset_all(); live_stream(1);
    EXPECT(madeira_audio_check() == 0, "check: the first call records the epoch");
    render_once(&e0);
    EXPECT(madeira_audio_check() == 0, "check: it rendered, left alone");
    EXPECT(madeira_audio_check() == 1 && n_stop == 1 && n_start == 2, "check: no render pass since the last call, restarted");
    madeira_audio_background(0, 1, 1);
    EXPECT(madeira_audio_check() == 0 && n_start == 2, "check: a halted endpoint is not restarted");
    reset_all();
    EXPECT(madeira_audio_check() == 0, "check: no endpoint, nothing");

    /* -- after an interruption -- */
    reset_all(); live_stream(1);
    madeira_audio_resume_after_interruption();
    EXPECT(n_stop == 1 && n_start == 2 && g_dev.running == 1, "interruption ended: a running endpoint is restarted");
    reset_all(); live_stream(0);
    madeira_audio_resume_after_interruption();
    EXPECT(n_start == 0, "interruption ended: an endpoint that does not run is left alone");

    if (fails) return 1;
    puts("ok");
    return 0;
}
'''

if not cc:
    print('SKIP: no C compiler for the audio driver harness')
else:
    with tempfile.TemporaryDirectory(prefix='bcd-bg-audio-') as t:
        c = Path(t) / 'bg_audio.c'
        c.write_text(harness)
        exe = Path(t) / 'bg_audio'
        b = subprocess.run([cc, '-std=c11', '-Wall', '-O1', '-o', str(exe), str(c), '-lpthread'], capture_output=True, text=True)
        check(b.returncode == 0, "the audio driver's background code compiles against the stubs")
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            if r.returncode:
                print(r.stdout[-3000:])
            check(r.returncode == 0 and r.stdout.strip().endswith('ok'),
                  'the audio driver: default, halt, release, keepalive, mute, check and interruption scenarios behave')

if platform.system() == 'Darwin' and cc:
    cap_test = r'''
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>
#include <mach/mach.h>
extern void madeira_bg_cpu_cap(int percent);
extern void madeira_bg_cpu_stats(double *cpu_s, double *wall_s, double *held_s);
static volatile int quit;
static void *busy(void *a) { volatile unsigned long x = 0; (void)a; while (!quit) x++; return NULL; }
static double now(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec / 1e9; }
static double cpu(void) { struct rusage r; getrusage(RUSAGE_SELF, &r);
    return r.ru_utime.tv_sec + r.ru_utime.tv_usec / 1e6 + r.ru_stime.tv_sec + r.ru_stime.tv_usec / 1e6; }
int main(void)
{
    pthread_t th[4];
    int i, fails = 0;
    double t0, c0, base, capped, after, gap = 0, last, w, u, h;
    for (i = 0; i < 4; i++) pthread_create(&th[i], NULL, busy, NULL);
    usleep(200000);
    t0 = now(); c0 = cpu(); usleep(1000000); base = (cpu() - c0) / (now() - t0);
    madeira_bg_cpu_cap(50);
    usleep(500000);
    t0 = now(); c0 = cpu(); last = t0;
    while (now() - t0 < 5.0) {              /* the main thread keeps ticking while the workers are held */
        double n;
        usleep(2000);
        n = now(); if (n - last > gap) gap = n - last; last = n;
    }
    capped = (cpu() - c0) / (now() - t0);
    madeira_bg_cpu_stats(&u, &w, &h);
    madeira_bg_cpu_cap(0);                  /* returns once every held thread runs again */
    t0 = now(); c0 = cpu(); usleep(1000000); after = (cpu() - c0) / (now() - t0);
    quit = 1;
    for (i = 0; i < 4; i++) pthread_join(th[i], NULL);
    printf("cpu per second: uncapped %.2f, capped %.2f, after %.2f; main thread's longest gap %.0f ms; stats wall %.1f s held %.1f s\n",
           base, capped, after, gap * 1000, w, h);
    if (base < 1.5) { puts("skip: fewer than two free cores, the cap cannot be shown"); return 0; }
    if (!(capped < 0.85)) { puts("FAIL capped CPU is not near 50% of one core"); fails++; }
    if (!(capped > 0.15)) { puts("FAIL the process was frozen, not capped"); fails++; }
    if (!(gap < 0.08)) { puts("FAIL the main thread was held"); fails++; }
    if (!(after > base * 0.6)) { puts("FAIL the threads did not run again after the cap was turned off"); fails++; }
    if (!(w > 4.0 && h > 1.0)) { puts("FAIL the stats do not show the run"); fails++; }
    if (fails) return 1;
    puts("ok");
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='bcd-bg-cap-') as t:
        src = Path(t) / 'cap_test.c'
        src.write_text(cap_test)
        exe = Path(t) / 'cap_test'
        b = subprocess.run([cc, '-std=gnu11', '-Wall', '-O1', '-o', str(exe), str(src), str(app / 'Winios/BackgroundCPUCap.c'), '-lpthread'],
                           capture_output=True, text=True)
        check(b.returncode == 0, 'the CPU governor compiles on the host')
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
            print(r.stdout.strip())
            check(r.returncode == 0 and r.stdout.strip().endswith(('ok', 'the cap cannot be shown')),
                  'the CPU governor holds four busy threads near 50% of one core, never holds the main thread, and lets go')

    gate_fake = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <pthread.h>
#include <stdatomic.h>
#include <unistd.h>
extern int madeira_metal_gate_install(void);
extern void madeira_metal_gate_hold(int inactive);
/* A device with no GPU behind it, so the gate's logic is run on any host: the gate asks bcd_fake_device() for the device
 * (-DMTLCreateSystemDefaultDevice=bcd_fake_device). commandBuffer hands out a subclass that inherits -commit, the other kinds the
 * class that has its own: both ways of wrapping. */
static _Atomic int original_commits;
@interface FakeCB : NSObject
- (void)commit;
@end
@implementation FakeCB
- (void)commit { atomic_fetch_add(&original_commits, 1); }
@end
@interface FakeCBSub : FakeCB
@end
@implementation FakeCBSub
@end
@interface FakeQueue : NSObject
- (id)commandBuffer;
- (id)commandBufferWithUnretainedReferences;
- (id)commandBufferWithDescriptor:(MTLCommandBufferDescriptor *)d;
@end
@implementation FakeQueue
- (id)commandBuffer { return [FakeCBSub new]; }
- (id)commandBufferWithUnretainedReferences { return [FakeCB new]; }
- (id)commandBufferWithDescriptor:(MTLCommandBufferDescriptor *)d { (void)d; return [FakeCB new]; }
@end
@interface FakeDevice : NSObject
- (id)newCommandQueue;
@end
@implementation FakeDevice
- (id)newCommandQueue { return [FakeQueue new]; }
@end
id<MTLDevice> bcd_fake_device(void) { return (id<MTLDevice>)[FakeDevice new]; }

static FakeQueue *queue;
static _Atomic int calls, spinning = 1, done;
static void *spin(void *a) {
    (void)a;
    while (spinning) {
        @autoreleasepool {
            [[queue commandBuffer] commit];
            [[queue commandBufferWithUnretainedReferences] commit];
            atomic_fetch_add(&calls, 2);
        }
        usleep(100);
    }
    return NULL;
}
static void *once(void *a) {
    (void)a;
    @autoreleasepool { [[queue commandBuffer] commit]; atomic_fetch_add(&calls, 1); }
    atomic_store(&done, 1);
    return NULL;
}
static int finished(int ms) { for (int i = 0; i < ms / 10 && !atomic_load(&done); i++) usleep(10000); return atomic_load(&done); }
int main(void) {
    @autoreleasepool {
        int fails = 0;
        queue = [FakeQueue new];
        /* before the gate: the original runs */
        [[queue commandBuffer] commit]; atomic_fetch_add(&calls, 1);
        if (atomic_load(&original_commits) != 1) { puts("FAIL the fake -commit did not run"); fails++; }
        /* installed late, while another thread commits in a loop: no commit is dropped */
        pthread_t sp;
        pthread_create(&sp, NULL, spin, NULL);
        usleep(100000);
        int n = madeira_metal_gate_install();
        usleep(300000);
        atomic_store(&spinning, 0);
        pthread_join(sp, NULL);
        printf("wrapped %d class(es); %d commits asked, %d reached the original\n", n, atomic_load(&calls), atomic_load(&original_commits));
        if (n != 2) { puts("FAIL expected the inheriting subclass and the class with its own commit"); fails++; }
        if (atomic_load(&calls) != atomic_load(&original_commits)) { puts("FAIL a commit was dropped around the install"); fails++; }
        if (madeira_metal_gate_install() != n) { puts("FAIL a second install changed the count"); fails++; }
        /* active: off the main thread a commit goes straight through */
        pthread_t th;
        atomic_store(&done, 0);
        pthread_create(&th, NULL, once, NULL);
        if (!finished(2000)) { puts("FAIL a commit waited while the app was active"); fails++; }
        pthread_join(th, NULL);
        /* inactive: off the main thread it waits; the main thread's own commit does not */
        [[NSNotificationCenter defaultCenter] postNotificationName:@"UIApplicationWillResignActiveNotification" object:nil];
        atomic_store(&done, 0);
        int before = atomic_load(&original_commits);
        pthread_create(&th, NULL, once, NULL);
        usleep(400000);
        if (atomic_load(&done) || atomic_load(&original_commits) != before) { puts("FAIL a commit off the main thread did not wait while inactive"); fails++; }
        [[queue commandBuffer] commit]; atomic_fetch_add(&calls, 1);
        if (atomic_load(&original_commits) != before + 1) { puts("FAIL the main thread's commit did not go through while inactive"); fails++; }
        [[NSNotificationCenter defaultCenter] postNotificationName:@"UIApplicationDidBecomeActiveNotification" object:nil];
        if (!finished(2000)) { puts("FAIL the waiting commit was not released when the app became active"); fails++; }
        pthread_join(th, NULL);
        if (atomic_load(&calls) != atomic_load(&original_commits)) { puts("FAIL a commit was lost"); fails++; }
        /* by hand */
        madeira_metal_gate_hold(1);
        atomic_store(&done, 0);
        pthread_create(&th, NULL, once, NULL);
        usleep(300000);
        if (atomic_load(&done)) { puts("FAIL hold(1) did not hold"); fails++; }
        madeira_metal_gate_hold(0);
        if (!finished(2000)) { puts("FAIL hold(0) did not release"); fails++; }
        pthread_join(th, NULL);
        if (atomic_load(&calls) != atomic_load(&original_commits)) { puts("FAIL a commit was lost after the hold"); fails++; }
        if (fails) return 1;
        puts("ok");
    }
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='bcd-bg-gate-') as t:
        src = Path(t) / 'gate_fake.m'
        src.write_text(gate_fake)
        exe = Path(t) / 'gate_fake'
        b = subprocess.run([cc, '-fobjc-arc', '-Wall', '-O1', '-DMTLCreateSystemDefaultDevice=bcd_fake_device', '-o', str(exe), str(src),
                            str(app / 'Winios/MetalBackgroundGate.m'), '-framework', 'Foundation', '-framework', 'Metal', '-lpthread'],
                           capture_output=True, text=True)
        check(b.returncode == 0, 'the Metal gate compiles on the host (against a fake device)')
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
            print(r.stdout.strip())
            if r.returncode:
                print(r.stderr[-1500:])
            check(r.returncode == 0 and r.stdout.strip().endswith('ok'),
                  'the Metal gate: wraps an inherited and an own commit, drops none around a late install, holds off the main thread only, releases on active and by hand')

    gate_real = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <pthread.h>
#include <stdatomic.h>
#include <unistd.h>
extern int madeira_metal_gate_install(void);
static id<MTLCommandQueue> queue;
static _Atomic int blocked_done;
static void *blocked(void *a) {
    (void)a;
    @autoreleasepool {
        id<MTLCommandBuffer> cb = [queue commandBuffer];
        [cb commit];
        [cb waitUntilCompleted];
    }
    atomic_store(&blocked_done, 1);
    return NULL;
}
int main(void) {
    @autoreleasepool {
        int fails = 0;
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) { puts("skip: no Metal device on this host"); return 0; }
        queue = [device newCommandQueue];
        int n = madeira_metal_gate_install();
        printf("the device's command buffers: %d class(es) wrapped\n", n);
        if (n < 1) { puts("FAIL no command buffer class of the device was wrapped"); fails++; }
        pthread_t th;
        pthread_create(&th, NULL, blocked, NULL);
        for (int i = 0; i < 200 && !atomic_load(&blocked_done); i++) usleep(10000);
        if (!atomic_load(&blocked_done)) { puts("FAIL a real command buffer waited while the app was active"); fails++; }
        pthread_join(th, NULL);
        [[NSNotificationCenter defaultCenter] postNotificationName:@"UIApplicationWillResignActiveNotification" object:nil];
        atomic_store(&blocked_done, 0);
        pthread_create(&th, NULL, blocked, NULL);
        usleep(400000);
        if (atomic_load(&blocked_done)) { puts("FAIL a real command buffer was committed off the main thread while inactive"); fails++; }
        id<MTLCommandBuffer> mine = [queue commandBuffer];
        [mine commit];
        [mine waitUntilCompleted];
        if (mine.status != MTLCommandBufferStatusCompleted) { puts("FAIL the main thread's real commit did not complete while inactive"); fails++; }
        [[NSNotificationCenter defaultCenter] postNotificationName:@"UIApplicationDidBecomeActiveNotification" object:nil];
        for (int i = 0; i < 200 && !atomic_load(&blocked_done); i++) usleep(10000);
        if (!atomic_load(&blocked_done)) { puts("FAIL the waiting real commit was not released when the app became active"); fails++; }
        pthread_join(th, NULL);
        if (fails) return 1;
        puts("ok");
    }
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='bcd-bg-gate-real-') as t:
        src = Path(t) / 'gate_real.m'
        src.write_text(gate_real)
        exe = Path(t) / 'gate_real'
        b = subprocess.run([cc, '-fobjc-arc', '-Wall', '-O1', '-o', str(exe), str(src), str(app / 'Winios/MetalBackgroundGate.m'),
                            '-framework', 'Foundation', '-framework', 'Metal', '-lpthread'], capture_output=True, text=True)
        check(b.returncode == 0, 'the Metal gate compiles on the host (against the real Metal)')
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
            print(r.stdout.strip())
            if r.returncode:
                print(r.stderr[-1500:])
            no_metal = 'skip: no Metal device' in r.stdout
            if no_metal and args.require_metal:
                check(False, 'a Metal device is available')
            else:
                check(r.returncode == 0 and (no_metal or r.stdout.strip().endswith('ok')),
                      'the Metal gate on the real device (skipped where there is none): wraps its command buffer classes, holds while inactive, releases on active')
else:
    print('SKIP: the CPU governor and the Metal gate run on macOS only (CI runs them)')

# ---- the Swift fixture -------------------------------------------------------------------------------------------------------------
stubs = r'''
import Foundation

struct UIBackgroundTaskIdentifier: Equatable { let raw: Int; static let invalid = UIBackgroundTaskIdentifier(raw: 0) }
final class UIApplication {
    nonisolated(unsafe) static let shared = UIApplication()
    static let willResignActiveNotification = Notification.Name("willResign")
    static let didEnterBackgroundNotification = Notification.Name("didEnterBackground")
    static let didBecomeActiveNotification = Notification.Name("didBecomeActive")
    var next = 1
    var tasks: [Int: () -> Void] = [:]
    var refuse = false
    func beginBackgroundTask(withName: String, expirationHandler: (() -> Void)?) -> UIBackgroundTaskIdentifier {
        if refuse { return .invalid }
        next += 1; tasks[next] = expirationHandler ?? {}; return UIBackgroundTaskIdentifier(raw: next)
    }
    func endBackgroundTask(_ id: UIBackgroundTaskIdentifier) { tasks[id.raw] = nil }
    /// iOS's grace period ends for every task still begun.
    func expireAll() { let t = tasks; for (_, h) in t.sorted(by: { $0.key < $1.key }) { h() } }
}

let AVAudioSessionInterruptionTypeKey = "type"
final class AVAudioSession {
    nonisolated(unsafe) static let shared = AVAudioSession()
    static func sharedInstance() -> AVAudioSession { shared }
    static let interruptionNotification = Notification.Name("interruption")
    enum Category { case playback }
    struct CategoryOptions: OptionSet { let rawValue: Int; static let mixWithOthers = CategoryOptions(rawValue: 1) }
    struct SetActiveOptions: OptionSet { let rawValue: Int; static let notifyOthersOnDeactivation = SetActiveOptions(rawValue: 1) }
    enum InterruptionType: UInt { case began = 1, ended = 0 }
    var options: CategoryOptions = []
    var active = false
    var changes = 0
    func setCategory(_ c: Category, options: CategoryOptions) throws { self.options = options; changes += 1 }
    func setActive(_ a: Bool) throws { active = a; changes += 1 }
    func setActive(_ a: Bool, options: SetActiveOptions) throws { active = a; changes += 1 }
}

enum UNAuthorizationStatus { case authorized, provisional, denied }
final class UNNotificationSettings { let authorizationStatus = UNAuthorizationStatus.authorized }
final class UNMutableNotificationContent { var title = ""; var body = "" }
final class UNNotificationRequest { let content: UNMutableNotificationContent
    init(identifier: String, content: UNMutableNotificationContent, trigger: Any?) { self.content = content } }
final class UNUserNotificationCenter {
    nonisolated(unsafe) static let shared = UNUserNotificationCenter()
    static func current() -> UNUserNotificationCenter { shared }
    var posted: [String] = []
    var bodies: [String] = []
    func getNotificationSettings(_ done: @escaping (UNNotificationSettings) -> Void) { done(UNNotificationSettings()) }
    func add(_ r: UNNotificationRequest) { posted.append(r.content.title); bodies.append(r.content.body) }
}

enum MadeiraConfig {
    nonisolated(unsafe) static var values: [String: String] = [:]
    nonisolated(unsafe) static var game: [String: String] = [:]
    static func get(_ key: String) -> String? { values[key] }
    static func gameValue(_ key: String) -> String? { game[key].flatMap { $0.isEmpty ? nil : $0 } }
}
@MainActor final class SteamDownloadBackground {
    static var notificationsEnabled = true
}
final class LogStore {
    nonisolated(unsafe) static let shared = LogStore()
    var lines: [String] = []
    func log(_ m: String) { lines.append(m); print("  log: " + m) }
}
nonisolated(unsafe) var wineRunning: Int32 = 0
func wine_process_is_running() -> Int32 { wineRunning }
nonisolated(unsafe) var audioCalls: [[Int32]] = []
nonisolated(unsafe) var endpointStarts = true
func madeira_audio_background(_ k: Int32, _ m: Int32, _ h: Int32) -> Int32 {
    audioCalls.append([k, m, h]); return (k == 1 && endpointStarts) ? 1 : 0
}
nonisolated(unsafe) var gateClasses: Int32 = 1
nonisolated(unsafe) var gateInstalls = 0
nonisolated(unsafe) var gateHolds: [Int32] = []
func madeira_metal_gate_install() -> Int32 { gateInstalls += 1; return gateClasses }
func madeira_metal_gate_hold(_ i: Int32) { gateHolds.append(i) }
nonisolated(unsafe) var capCalls: [Int32] = []
func madeira_bg_cpu_cap(_ p: Int32) { capCalls.append(p) }
func madeira_bg_cpu_stats(_ c: UnsafeMutablePointer<Double>, _ w: UnsafeMutablePointer<Double>, _ h: UnsafeMutablePointer<Double>) {
    c.pointee = 30; w.pointee = 60; h.pointee = 20
}
nonisolated(unsafe) var checks = 0
func madeira_audio_check() -> Int32 { checks += 1; return 0 }
nonisolated(unsafe) var resumes = 0
func madeira_audio_resume_after_interruption() { resumes += 1 }
'''

main_swift = r'''
import Foundation

nonisolated(unsafe) var failed = 0
func check(_ ok: Bool, _ label: String) { print((ok ? "PASS: " : "FAIL: ") + label); if !ok { failed += 1 } }
func post(_ name: Notification.Name, _ info: [AnyHashable: Any]? = nil) {
    NotificationCenter.default.post(name: name, object: nil, userInfo: info)
    RunLoop.main.run(until: Date().addingTimeInterval(0.02))
}
func reset() { audioCalls = []; capCalls = []; gateHolds = []; resumes = 0; UNUserNotificationCenter.shared.posted = []; AVAudioSession.shared.changes = 0 }

let scenario = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "off"

MainActor.assumeIsolated {
    let run = BackgroundRun.shared
    run.start()
    let session = AVAudioSession.shared
    let app = UIApplication.shared

    if scenario == "off" {
        // ---- the defaults: no key at all ----
        wineRunning = 1
        post(UIApplication.willResignActiveNotification)
        check(audioCalls.isEmpty && session.changes == 0 && gateInstalls == 0 && capCalls.isEmpty, "off, game: leaving touches nothing (no endpoint call, no audio session, no gate, no cap)")
        post(UIApplication.didEnterBackgroundNotification)
        check(audioCalls == [[0, 1, 1]], "off, game: entering the background halts the endpoint (the one call that keeps the audio mode from keeping BCD alive)")
        check(session.changes == 0 && gateInstalls == 0 && capCalls.isEmpty && !LogStore.shared.lines.contains { $0.contains("keeping") },
              "off, game: no category change, no gate, no CPU cap, nothing kept")
        post(AVAudioSession.interruptionNotification, [AVAudioSessionInterruptionTypeKey: UInt(0)])
        check(resumes == 0 && !session.active, "off, game: an interruption that ended is not handled here (MADEIRA_AUDIO_RESUME's own business)")
        post(UIApplication.didBecomeActiveNotification)
        check(audioCalls == [[0, 1, 1], [0, 0, 0]], "off, game: active again releases the endpoint, nothing more")
        check(session.changes == 0 && capCalls.isEmpty && gateInstalls == 0 && UNUserNotificationCenter.shared.posted.isEmpty, "off, game: no session change, no cap, no gate, no notification")
        RunLoop.main.run(until: Date().addingTimeInterval(2))
        check(checks == 2, "off, game: the endpoint is looked at once at once and once 1.5 s later")

        // a Control Center pull
        reset(); checks = 0
        post(UIApplication.willResignActiveNotification)
        post(UIApplication.didBecomeActiveNotification)
        check(audioCalls.isEmpty && session.changes == 0 && gateInstalls == 0, "off, game: a pull of Control Center touches nothing")

        // no game, nothing held
        wineRunning = 0; reset(); checks = 0
        post(UIApplication.willResignActiveNotification)
        post(UIApplication.didEnterBackgroundNotification)
        check(audioCalls == [[0, 1, 1]], "off, no game: the same halt (a stray endpoint must not keep the app alive)")
        post(UIApplication.didBecomeActiveNotification)
        RunLoop.main.run(until: Date().addingTimeInterval(2))
        check(audioCalls == [[0, 1, 1], [0, 0, 0]] && checks == 0 && session.changes == 0, "off, no game: released, no audio check, no session change")
        reset()
        post(UIApplication.didBecomeActiveNotification)
        check(audioCalls.isEmpty, "off: becoming active without having left changes nothing")

        // a download, off: iOS's grace period exactly as before
        var paused = 0
        post(UIApplication.didEnterBackgroundNotification)
        let a = run.beginTask("Steam download") { paused += 1 }
        let b = run.beginTask("store download") { paused += 1 }
        check(app.tasks.count == 2 && session.changes == 0 && capCalls.isEmpty && !session.active, "off, download: iOS background tasks, no session, no cap")
        run.endTask(a)
        check(paused == 0 && app.tasks.count == 1, "off, download: one finishing does not pause the other (no early pause)")
        app.expireAll()
        check(paused == 1, "off, download: the grace period ends: paused once, as before")
        run.endTask(b)
        post(UIApplication.didBecomeActiveNotification)
        // a game running while a download ends in the background: no "your game is paused"
        wineRunning = 1; reset()
        post(UIApplication.didEnterBackgroundNotification)
        let c = run.beginTask("Steam download") { paused += 1 }
        run.endTask(c)
        check(UNUserNotificationCenter.shared.posted.isEmpty, "off, game + download ending: no game-paused notification")
        post(UIApplication.didBecomeActiveNotification)
        // iOS refuses the task: the download is left alone, as before
        app.refuse = true; paused = 0
        post(UIApplication.didEnterBackgroundNotification)
        let d = run.beginTask("itch.io download") { paused += 1 }
        check(paused == 0, "off, download: iOS refused a task: nothing pauses it early")
        run.endTask(d); app.refuse = false
        post(UIApplication.didBecomeActiveNotification)
    }

    if scenario == "on" {
        MadeiraConfig.values["background-run"] = "10"
        wineRunning = 1
        post(UIApplication.willResignActiveNotification)
        check(gateInstalls == 1 && gateHolds == [1], "on: the Metal gate is made when the game is first kept, and held by hand")
        check(session.options.contains(.mixWithOthers), "on: leaving with a game: other apps' audio may play")
        check(audioCalls.last == [1, 0, 0], "on: leaving with a game: endpoint started while still in front, not muted")
        post(UIApplication.didEnterBackgroundNotification)
        check(audioCalls.last == [1, 1, 0], "on: game in the background: endpoint kept running, muted")
        check(capCalls.last == 50, "on: game in the background: CPU held under 50% of one core")
        check(LogStore.shared.lines.last?.contains("keeping BCD running in the background for: game") == true, "on: log names the game")
        post(AVAudioSession.interruptionNotification, [AVAudioSessionInterruptionTypeKey: UInt(0)])
        check(resumes == 1, "on: an interruption that ended restarts the endpoint")
        post(UIApplication.didBecomeActiveNotification)
        check(audioCalls.last == [0, 0, 0] && session.options.isEmpty, "on: back: endpoint as the game wants it, category restored")
        check(capCalls.last == 0, "on: back: CPU cap off")
        let checksBefore = checks
        RunLoop.main.run(until: Date().addingTimeInterval(2))
        check(checks == checksBefore + 1, "on: back: the endpoint is checked again 1.5 s later")
        check(LogStore.shared.lines.contains { $0.contains("BCD is active again: 50% of one core") }, "on: back: log gives the CPU use")
        // a second trip: the gate is not made again
        post(UIApplication.willResignActiveNotification)
        check(gateInstalls == 1, "on: the gate is made once")
        post(UIApplication.didBecomeActiveNotification)

        MadeiraConfig.values["background-cpu"] = "0"
        post(UIApplication.didEnterBackgroundNotification)
        check(capCalls.last == 0 && LogStore.shared.lines.last?.contains("no CPU cap") == true, "on, background-cpu = 0: no cap")
        post(UIApplication.didBecomeActiveNotification)
        MadeiraConfig.values["background-cpu"] = nil

        MadeiraConfig.values["background-sound"] = "1"
        post(UIApplication.didEnterBackgroundNotification)
        check(audioCalls.last == [1, 0, 0], "on, background-sound = 1: the game stays audible")
        post(UIApplication.didBecomeActiveNotification)
        MadeiraConfig.values["background-sound"] = nil

        // the game's own file wins
        MadeiraConfig.game["background-run"] = "0"
        reset()
        post(UIApplication.willResignActiveNotification)
        post(UIApplication.didEnterBackgroundNotification)
        check(audioCalls == [[0, 1, 1]] && capCalls.isEmpty, "on, the game's own background-run = 0: this game is not kept (halted as with the feature off)")
        post(UIApplication.didBecomeActiveNotification)
        MadeiraConfig.game["background-run"] = nil

        // notifications off
        SteamDownloadBackground.notificationsEnabled = false
        MadeiraConfig.values["background-run"] = "0.001"
        reset()
        post(UIApplication.didEnterBackgroundNotification)
        RunLoop.main.run(until: Date().addingTimeInterval(16))
        check(audioCalls.last == [0, 1, 1] && capCalls.last == 0, "on, minutes used up: cap off, endpoint halted so iOS suspends BCD")
        check(UNUserNotificationCenter.shared.posted.isEmpty, "on, notifications off: no game-paused notification")
        post(UIApplication.didBecomeActiveNotification)
        SteamDownloadBackground.notificationsEnabled = true

        // the game's minutes are used up: stop, halt, notify
        reset()
        post(UIApplication.didEnterBackgroundNotification)
        check(audioCalls.last == [1, 1, 0], "on, short limit: kept running at first")
        RunLoop.main.run(until: Date().addingTimeInterval(16))
        check(audioCalls.last == [0, 1, 1] && capCalls.last == 0, "on, minutes used up: cap off, endpoint halted")
        check(UNUserNotificationCenter.shared.posted == ["Your game is paused"], "on: a notification says the game is paused")
        check(UNUserNotificationCenter.shared.bodies.first?.contains("BCD") == true && UNUserNotificationCenter.shared.bodies.first?.contains("Madeira") == false,
              "on: ...and says BCD")
        post(UIApplication.didBecomeActiveNotification)
        check(audioCalls.last == [0, 0, 0], "on: back: halt lifted")
        MadeiraConfig.values["background-run"] = "10"

        // the game ends in the background
        reset()
        post(UIApplication.didEnterBackgroundNotification)
        wineRunning = 0
        RunLoop.main.run(until: Date().addingTimeInterval(16))
        check(audioCalls.last == [0, 1, 1] && UNUserNotificationCenter.shared.posted.isEmpty, "on: the game ended in the background: stopped, no notification")
        post(UIApplication.didBecomeActiveNotification)
        MadeiraConfig.values["background-run"] = nil

        // downloads: on
        MadeiraConfig.values["background-download"] = "60"
        reset()
        var paused = 0
        post(UIApplication.didEnterBackgroundNotification)
        check(audioCalls.last == [0, 1, 1], "on, nothing running: halted")
        var token: BackgroundRun.Token? = run.beginTask("GOG download") { paused += 1 }
        check(session.active && audioCalls.last == [1, 1, 0], "on, a download in the background: session active, endpoint running")
        check(gateInstalls == 1, "on, a download with no game needs no new gate")
        app.expireAll()
        check(paused == 0, "on: iOS's grace period ends: the download goes on while BCD runs")
        run.endTask(token!); token = nil
        check(audioCalls.last == [0, 1, 1], "on: download finished: endpoint halted")
        post(UIApplication.didBecomeActiveNotification)
        check(!session.active, "on, back: the session made active for the download is inactive again")

        token = run.beginTask("EA download") { paused += 1 }
        check(audioCalls.last == [0, 0, 0], "on: in front: nothing started")
        post(UIApplication.didEnterBackgroundNotification)
        check(audioCalls.last == [1, 1, 0], "on: a download begun in front keeps BCD running once it leaves")
        post(UIApplication.didBecomeActiveNotification)
        run.endTask(token!)

        endpointStarts = false
        post(UIApplication.didEnterBackgroundNotification)
        token = run.beginTask("itch.io download") { paused += 1 }
        check(LogStore.shared.lines.last?.contains("cannot keep running") == true, "on, endpoint refused: logged")
        app.expireAll()
        check(paused == 1, "on, endpoint refused: the download pauses when iOS's grace period ends")
        post(UIApplication.didBecomeActiveNotification)
        endpointStarts = true

        // background-download = 0 again: iOS's grace period only
        MadeiraConfig.values["background-download"] = "0"
        post(UIApplication.didEnterBackgroundNotification)
        token = run.beginTask("Steam download") { paused += 1 }
        check(audioCalls.last == [0, 1, 1], "on, background-download = 0: nothing kept running")
        app.expireAll()
        check(paused == 2, "on, background-download = 0: paused at the end of iOS's grace period, as before")
        post(UIApplication.didBecomeActiveNotification)
    }

    if scenario == "dlgame" {
        // downloads on, games off: a download held while a game runs keeps BCD running, and the game runs on beside it, so the gate is needed
        MadeiraConfig.values["background-download"] = "60"
        wineRunning = 1
        var paused = 0
        post(UIApplication.willResignActiveNotification)
        check(audioCalls.isEmpty && gateInstalls == 0, "download + game: leaving with no download held yet touches nothing")
        post(UIApplication.didEnterBackgroundNotification)
        check(audioCalls == [[0, 1, 1]] && gateInstalls == 0, "download + game: nothing to keep at first (halted like the feature off)")
        let token = run.beginTask("Steam download") { paused += 1 }
        check(gateInstalls == 1 && gateHolds == [1], "download + game: the gate is made and holds the game's GPU work")
        check(audioCalls.last == [1, 1, 0], "download + game: BCD is kept running for the download")
        app.expireAll()
        check(paused == 0, "download + game: the download goes on past iOS's grace period")
        run.endTask(token)
        check(audioCalls.last == [0, 1, 1], "download + game: finished, halted")
        post(UIApplication.didBecomeActiveNotification)
    }

    if scenario == "nogate" {
        MadeiraConfig.values["background-run"] = "10"
        gateClasses = 0
        wineRunning = 1
        post(UIApplication.willResignActiveNotification)
        check(gateInstalls == 1 && gateHolds.isEmpty && audioCalls.isEmpty && session.changes == 0, "no gate: leaving keeps nothing")
        post(UIApplication.didEnterBackgroundNotification)
        check(audioCalls == [[0, 1, 1]] && capCalls.isEmpty, "no gate: the game is not kept running (halted like the feature off)")
        check(LogStore.shared.lines.contains { $0.contains("Metal gate could not be made") }, "no gate: logged")
        post(UIApplication.didBecomeActiveNotification)
        post(UIApplication.willResignActiveNotification)
        check(gateInstalls == 1, "no gate: not tried again every time")
        post(UIApplication.didBecomeActiveNotification)
    }

    if scenario == "gateoff" {
        MadeiraConfig.values["background-run"] = "10"
        MadeiraConfig.values["background-gpu-gate"] = "0"
        wineRunning = 1
        post(UIApplication.willResignActiveNotification)
        post(UIApplication.didEnterBackgroundNotification)
        check(gateInstalls == 0 && gateHolds.isEmpty, "gate off: no gate is made")
        check(audioCalls.last == [1, 1, 0] && capCalls.last == 50, "gate off: the game is kept running without it")
        post(UIApplication.didBecomeActiveNotification)
    }
}
if failed > 0 { print("\(failed) failure(s)"); exit(1) }
'''

with tempfile.TemporaryDirectory(prefix='bcd-bg-run-') as directory:
    temporary = Path(directory)
    compiler = os.environ.get('SWIFTC') or shutil.which('swiftc')
    if compiler:
        source = run_src.replace('import AVFoundation\nimport UIKit\nimport UserNotifications\n', 'import Foundation\n')
        check(source != run_src, 'fixture: BackgroundRun.swift imports as expected')
        (temporary / 'BackgroundRun.swift').write_text(source)
        (temporary / 'Stubs.swift').write_text(stubs)
        (temporary / 'main.swift').write_text(main_swift)
        binary = temporary / 'bgrun'
        built = subprocess.run([compiler, str(temporary / 'BackgroundRun.swift'), str(temporary / 'Stubs.swift'),
                                str(temporary / 'main.swift'), '-o', str(binary)], capture_output=True, text=True)
        check(built.returncode == 0, 'fixture: BackgroundRun.swift compiles against the stand-ins')
        if built.returncode:
            print(built.stderr[-4000:])
        else:
            for scenario in ('off', 'on', 'dlgame', 'nogate', 'gateoff'):
                result = subprocess.run([str(binary), scenario], capture_output=True, text=True)
                print(result.stdout, end='')
                if result.returncode:
                    print(result.stderr[-1500:])
                check(result.returncode == 0, f'Swift fixture: {scenario}')
    elif args.require_swift:
        raise SystemExit('Swift compiler required for the background-run fixture')
    else:
        print('SKIP: Swift compiler unavailable locally; macOS CI must run this check with --require-swift')

if failures:
    raise SystemExit(f'{failures} check(s) failed')
print('All background-run checks passed')
