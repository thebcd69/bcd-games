#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's graphics, sound, video and input test programs of round 602 (tests/dxtests, the Start menu's "BCD Tests" folder).

Programs (C for Windows, llvm-mingw, tools/build-dx-tests.sh): d3d9 (the 64-bit and the 32-bit Direct3D 9 cube, one source), d3d8
(32-bit), ddraw (32-bit), frametime (Direct3D 11), resolution, sound (64-bit and 32-bit), video (64-bit and 32-bit), controller, input.
Checks:
  1. bcdstats.h, the pure arithmetic, is compiled on the host (clang, ASan/UBSan when they work) and run against known answers: frame-time
     statistics and the frame-time verdict, the refresh-rate classes, the stereo tone of the sound test (left only, right only, both; the
     frequencies counted from the zero crossings), colours packed into pixel formats and back, YUV to RGB, and the video test's
     measurements (the progress bar's length, the hue of the halves) on pictures made from the clip generator's own frames in NV12, I420,
     YV12, YUY2 and RGB32.
  2. The clips (tests/dxtests/clips, tools/gen-video-test-clips.sh): video_clips.h is the two files, byte for byte, with their SHA-256; the
     MP4's boxes (H.264 Baseline in avcC with a 160x96 sequence parameter set, AAC-LC in esds at 44100 Hz, 30 video samples at 15 fps, a sync
     sample, the sizes and offsets inside mdat); the WMV's ASF objects (header, file properties, a WMV2 video stream of 160x96, a WMA2 audio stream,
     the data object, the sizes adding up to the file). On a Mac the MP4 is also decoded with AVAssetReader (tools/video-test-clips/checkmp4.swift)
     and the progress bars and colours are measured; with FFMPEG=<an ffmpeg with the asf demuxer and wmv2> the WMV is decoded and measured too.
  3. The sources: the licence headers, what each program calls (the API it tests), that each loads what can be missing at run time, writes
     PASS/FAIL checks and one RESULT line, installs the crash line, and that the shared log helper writes one line to stderr (the way a Windows
     program's output reaches BCD's session log, like dxtest.h's and tests/x86/hello-x86.c) and to a .log file in the temp folder (what the
     session log's [guest-log] mirror reads).
  4. With llvm-mingw: every program builds under -Wall -Wextra -Werror; the tracked binary is what the source builds to (apart from the PE time
     stamps); the PE is the right machine, a GUI program, large-address-aware when 32-bit, with imports of Windows' own DLLs only (and none of
     the graphics DLLs a test wants to load at run time); the programs build to the same bytes twice.
  5. Where they go: tools/build-dx-tests.sh builds each, the 64-bit ones are tracked in app/Madeira/arm64ec-windows and the 32-bit ones in
     app/Madeira/i386-windows, the Start menu names them (BCDStartMenu.c), the workflow runs this check.
"""
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
DX = R / "tests/dxtests"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-3000:]) if detail and not cond else ""))
    ok = ok and bool(cond)


def note(text):
    print("note " + text)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


HEADER = "SPDX-License-Identifier: GPL-3.0-only"
NEW_FILES = ["bcdtest.h", "bcdstats.h", "d3d8_cube.c", "ddraw_test.c", "frametime_test.c", "resolution_test.c", "sound_test.c", "video_test.c",
             "controller_test.c", "input_test.c", "video_clips.h", "midi_test.c"]
PROGRAMS = {   # name: (source, arch, ship folder, imports allowed beyond kernel32/user32/gdi32/msvcrt)
    "d3d9-cube-x64.exe": ("d3d9_cube.c", "x86_64", "arm64ec-windows", set()),
    "d3d9-test-x86.exe": ("d3d9_cube.c", "i686", "i386-windows", set()),
    "d3d8-test-x86.exe": ("d3d8_cube.c", "i686", "i386-windows", set()),
    "ddraw-test-x86.exe": ("ddraw_test.c", "i686", "i386-windows", set()),
    "frametime-test-x64.exe": ("frametime_test.c", "x86_64", "arm64ec-windows", set()),
    "resolution-test-x64.exe": ("resolution_test.c", "x86_64", "arm64ec-windows", set()),
    "sound-test-x64.exe": ("sound_test.c", "x86_64", "arm64ec-windows", {"winmm.dll", "ole32.dll"}),
    "sound-test-x86.exe": ("sound_test.c", "i686", "i386-windows", {"winmm.dll", "ole32.dll"}),
    "video-test-x64.exe": ("video_test.c", "x86_64", "arm64ec-windows", {"ole32.dll"}),
    "video-test-x86.exe": ("video_test.c", "i686", "i386-windows", {"ole32.dll"}),
    "controller-test-x64.exe": ("controller_test.c", "x86_64", "arm64ec-windows", {"dinput8.dll"}),
    "input-test-x64.exe": ("input_test.c", "x86_64", "arm64ec-windows", set()),
    "midi-test-x64.exe": ("midi_test.c", "x86_64", "arm64ec-windows", {"winmm.dll"}),      # round 603 (ajan48's source, wired in by ajan49)
    "midi-test-x86.exe": ("midi_test.c", "i686", "i386-windows", {"winmm.dll"}),
    # 2026-11-16: the OpenGL and Vulkan tests (their own checks: tests/host/check-vk-gl-tests.py); opengl32 and vulkan-1 load at run time
    "gl-test-x64.exe": ("gl_test.c", "x86_64", "arm64ec-windows", set()),
    "gl-test-x86.exe": ("gl_test.c", "i686", "i386-windows", set()),
    "vulkan-test-x64.exe": ("vulkan_test.c", "x86_64", "arm64ec-windows", set()),
    "vulkan-test-x86.exe": ("vulkan_test.c", "i686", "i386-windows", set()),
}
# Wine's own Vulkan header (wine/include/wine/vulkan.h) for the Vulkan test: this tree's wine submodule, or the build tree's
WINE_INC = next((str(c) for c in (R / "wine/include", Path.home() / "madeira-bcd/wine/include") if (c / "wine/vulkan.h").is_file()), str(R / "wine/include"))
LIBS = {
    "d3d9_cube.c": ["-luuid"], "d3d8_cube.c": ["-luuid"], "ddraw_test.c": ["-ldxguid", "-luuid"], "frametime_test.c": ["-ldxguid", "-luuid"],
    "resolution_test.c": ["-ldxguid", "-luuid"], "sound_test.c": ["-lwinmm", "-luuid", "-lole32"], "video_test.c": ["-lmfuuid", "-luuid", "-lole32"],
    "controller_test.c": ["-ldinput8", "-ldxguid", "-luuid"], "input_test.c": ["-luuid"], "midi_test.c": ["-lwinmm", "-luser32", "-lgdi32"],
    "gl_test.c": ["-lgdi32"], "vulkan_test.c": ["-iquote", WINE_INC],
}
# graphics, sound, video and input DLLs a program loads at run time (so that a session without one gets a message, not a loader failure)
RUNTIME_DLL = re.compile(r"^(d3d\d+|ddraw|dxgi|dsound|xaudio2_\d|mfplat|mfreadwrite|mf|xinput[0-9_]*|opengl32|vulkan-1)\.dll$")
LOADS = {   # source: the LoadLibraryA names it must have
    "d3d9_cube.c": ["d3d9.dll"], "d3d8_cube.c": ["d3d8.dll"], "ddraw_test.c": ["ddraw.dll"], "frametime_test.c": ["d3d11.dll"], "resolution_test.c": ["d3d11.dll"],
    "sound_test.c": ["dsound.dll", "xaudio2_9.dll"], "video_test.c": ["mfplat.dll", "mfreadwrite.dll"], "controller_test.c": ["xinput1_4.dll", "xinput1_3.dll"],
}

# ---------------------------------------------------------------- 1. the arithmetic (bcdstats.h)
DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "bcdstats.h"

static void print_stats(const double *ms, int n, double refresh, int vsync)
{
    bt_ft_stats_t s;
    char why[256];
    int ok = 0, have = bt_ft_stats(ms, n, &s);
    if (have) ok = bt_ft_verdict(&s, refresh, vsync, why, sizeof why); else snprintf(why, sizeof why, "no data");
    printf("n=%d min=%.3f max=%.3f avg=%.3f median=%.3f p99=%.3f fps_min=%.2f fps_avg=%.2f fps_max=%.2f low1=%.2f stutters=%d class=%d verdict=%d why=%s\n",
           s.n, s.min_ms, s.max_ms, s.avg_ms, s.median_ms, s.p99_ms, s.fps_min, s.fps_avg, s.fps_max, s.fps_1pct_low, s.stutters, have ? bt_refresh_class(s.median_ms) : 0, ok, why);
}

static long crossings(const int16_t *x, size_t n, size_t stride)
{
    long c = 0;
    size_t i;
    for (i = 1; i < n; i++)
        if ((x[(i - 1) * stride] < 0) != (x[i * stride] < 0)) c++;
    return c;
}

int main(int argc, char **argv)
{
    const char *cmd = argc > 1 ? argv[1] : "";
    if (!strcmp(cmd, "ft")) {
        static double ms[8192];
        int n = atoi(argv[3]), i, spikes = atoi(argv[4]);
        double base = atof(argv[5]), spike = atof(argv[6]), refresh = atof(argv[7]);
        int vsync = atoi(argv[8]);
        for (i = 0; i < n; i++) ms[i] = base + ((i % 7) - 3) * 0.02;
        for (i = 0; i < spikes; i++) ms[(i * 97 + 13) % n] = spike;
        printf("%s: ", argv[2]);
        print_stats(ms, n, refresh, vsync);
        return 0;
    }
    if (!strcmp(cmd, "refresh")) {
        int i;
        for (i = 2; i < argc; i++) printf("%s=%d ", argv[i], bt_refresh_class(atof(argv[i])));
        printf("\n");
        return 0;
    }
    if (!strcmp(cmd, "tone")) {
        int rate = atoi(argv[2]);
        size_t total = (size_t)rate * 3, seg = (size_t)rate, s;
        int16_t *buf = malloc(total * 2 * sizeof(int16_t));
        bt_tone_stereo(buf, rate);
        for (s = 0; s < 3; s++) {
            int ch;
            for (ch = 0; ch < 2; ch++) {
                double sq = 0;
                int peak = 0;
                size_t i;
                for (i = s * seg; i < (s + 1) * seg; i++) {
                    int v = buf[2 * i + ch];
                    sq += (double)v * v;
                    if (abs(v) > peak) peak = abs(v);
                }
                printf("seg%zu ch%d rms=%.1f peak=%d crossings=%ld\n", s, ch, sqrt(sq / seg), peak, crossings(buf + 2 * s * seg + ch, seg, 2));
            }
        }
        printf("first=%d,%d last=%d,%d\n", buf[0], buf[1], buf[2 * total - 2], buf[2 * total - 1]);
        {
            int16_t *m = malloc((size_t)rate * 2 * sizeof(int16_t));
            size_t n = bt_tone_mono(m, rate, 2);
            printf("mono n=%zu crossings=%ld first=%d\n", n, crossings(m, n, 1), m[0]);
            free(m);
        }
        free(buf);
        return 0;
    }
    if (!strcmp(cmd, "pack")) {
        int r, g, b;
        printf("565 red=%04x green=%04x blue=%04x white=%04x\n", bt_pack_rgb(0xF800, 0x07E0, 0x001F, 255, 0, 0), bt_pack_rgb(0xF800, 0x07E0, 0x001F, 0, 255, 0),
               bt_pack_rgb(0xF800, 0x07E0, 0x001F, 0, 0, 255), bt_pack_rgb(0xF800, 0x07E0, 0x001F, 255, 255, 255));
        printf("555 white=%04x mid=%04x\n", bt_pack_rgb(0x7C00, 0x03E0, 0x001F, 255, 255, 255), bt_pack_rgb(0x7C00, 0x03E0, 0x001F, 128, 64, 8));
        printf("888 value=%06x\n", bt_pack_rgb(0xFF0000, 0xFF00, 0xFF, 1, 2, 3));
        printf("bgr value=%06x\n", bt_pack_rgb(0x0000FF, 0x00FF00, 0xFF0000, 1, 2, 3));
        bt_unpack_rgb(0xFF0000, 0xFF00, 0xFF, 0x0A141E, &r, &g, &b);
        printf("unpack888 %d,%d,%d\n", r, g, b);
        bt_unpack_rgb(0xF800, 0x07E0, 0x001F, 0xF800, &r, &g, &b);
        printf("unpack565red %d,%d,%d\n", r, g, b);
        bt_unpack_rgb(0xF800, 0x07E0, 0x001F, 0xFFFF, &r, &g, &b);
        printf("unpack565white %d,%d,%d\n", r, g, b);
        bt_unpack_rgb(0x7C00, 0x03E0, 0x001F, 0x7FFF, &r, &g, &b);
        printf("unpack555white %d,%d,%d\n", r, g, b);
        return 0;
    }
    if (!strcmp(cmd, "yuv")) {
        int r, g, b;
        bt_yuv_to_rgb(16, 128, 128, &r, &g, &b); printf("black %d,%d,%d\n", r, g, b);
        bt_yuv_to_rgb(235, 128, 128, &r, &g, &b); printf("white %d,%d,%d\n", r, g, b);
        bt_yuv_to_rgb(82, 90, 240, &r, &g, &b); printf("red %d,%d,%d\n", r, g, b);
        bt_yuv_to_rgb(41, 240, 110, &r, &g, &b); printf("blue %d,%d,%d\n", r, g, b);
        printf("hue red=%d blue=%d grey=%d\n", bt_hue_class(200, 40, 40), bt_hue_class(40, 40, 200), bt_hue_class(128, 128, 128));
        printf("expected bars %d %d %d\n", bt_clip_expected_bar(0), bt_clip_expected_bar(14), bt_clip_expected_bar(29));
        return 0;
    }
    if (!strcmp(cmd, "picture")) {   /* picture <format> <file> <w> <h> <pitch>: convert and measure */
        const char *fmt = argv[2];
        int w = atoi(argv[4]), h = atoi(argv[5]), pitch = atoi(argv[6]);
        FILE *f = fopen(argv[3], "rb");
        static uint8_t in[1 << 20], out[BT_CLIP_W * BT_CLIP_H * 4];
        size_t n = f ? fread(in, 1, sizeof in, f) : 0;
        if (f) fclose(f);
        if (!n) { puts("unreadable"); return 1; }
        if (!strcmp(fmt, "nv12")) bt_nv12_to_bgra(in, pitch, in + (size_t)pitch * h, pitch, w, h, out);
        else if (!strcmp(fmt, "i420")) bt_planar_to_bgra(in, pitch, in + (size_t)pitch * h, in + (size_t)pitch * h + (size_t)(pitch / 2) * (h / 2), pitch / 2, w, h, out);
        else if (!strcmp(fmt, "yv12")) bt_planar_to_bgra(in, pitch, in + (size_t)pitch * h + (size_t)(pitch / 2) * (h / 2), in + (size_t)pitch * h, pitch / 2, w, h, out);
        else if (!strcmp(fmt, "yuy2")) bt_yuy2_to_bgra(in, pitch, w, h, out);
        else bt_rgb32_to_bgra(in, pitch, w, h, out);
        printf("bar=%d top=%d left=%d right=%d\n", bt_bgra_bar_length(out, w, h - 4), bt_bgra_bar_length(out, w, 3), bt_bgra_hue(out, w, 4, 4), bt_bgra_hue(out, w, w - 5, 4));
        return 0;
    }
    return 2;
}
'''

tmp = Path(tempfile.mkdtemp(prefix="bcd-tests-check-"))
try:
    drv_c = tmp / "stats.c"
    drv_c.write_text(DRIVER)
    drv = tmp / "stats"
    flags = ["-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O1", "-g", "-I", str(DX)]
    san = []
    probe = sh(["clang", "-fsanitize=address,undefined", "-x", "c", "-", "-o", str(tmp / "probe")], input="int main(void){return 0;}")
    if probe.returncode == 0 and sh([str(tmp / "probe")]).returncode == 0:
        san = ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
    else:
        note("clang has no working sanitizers here; the driver runs without them")
    build = sh(["clang", *flags, *san, str(drv_c), "-o", str(drv), "-lm"])
    check("bcdstats.h builds on the host with -Wall -Wextra -Werror" + (" and ASan/UBSan" if san else ""), build.returncode == 0, build.stderr)

    def run(*a):
        return sh([str(drv), *map(str, a)]).stdout.strip()

    def kv(line):
        d = {}
        for tok in re.findall(r"(\w+)=(\S+)", line):
            d[tok[0]] = tok[1]
        return d

    if build.returncode == 0:
        # frame times: name, n, spikes, base ms, spike ms, refresh, vsync
        steady = kv(run("ft", "steady", 600, 0, 16.667, 0, 60, 1))
        check("steady 60 fps: 60 fps, no stutter, PASS, classed as 60 Hz",
              abs(float(steady["fps_avg"]) - 60.0) < 0.2 and steady["stutters"] == "0" and steady["verdict"] == "1" and steady["class"] == "60", steady)
        check("... with the minimum, maximum, median and 1 % low in the right place",
              abs(float(steady["min"]) - 16.627) < 0.05 and abs(float(steady["max"]) - 16.707) < 0.05 and abs(float(steady["median"]) - 16.667) < 0.05
              and abs(float(steady["low1"]) - 60.0) < 0.3 and abs(float(steady["fps_min"]) - 59.85) < 0.3 and abs(float(steady["fps_max"]) - 60.15) < 0.3, steady)
        s120 = kv(run("ft", "s120", 1200, 0, 8.333, 0, 120, 1))
        check("steady 120 fps with VSync on a 120 Hz display: PASS, classed as 120 Hz", s120["verdict"] == "1" and s120["class"] == "120" and abs(float(s120["fps_avg"]) - 120.0) < 0.5, s120)
        few = kv(run("ft", "few", 6, 0, 16.667, 0, 60, 1))
        check("six stutters in 600 frames (1 %) still PASS and are counted", kv(run("ft", "x", 600, 6, 16.667, 50.0, 60, 1))["stutters"] == "6"
              and kv(run("ft", "x", 600, 6, 16.667, 50.0, 60, 1))["verdict"] == "1")
        check("a stutter shows in the minimum fps and the 1 % low",
              abs(float(kv(run("ft", "x", 600, 6, 16.667, 50.0, 60, 1))["fps_min"]) - 20.0) < 0.2
              and float(kv(run("ft", "x", 600, 6, 16.667, 50.0, 60, 1))["low1"]) < 40, run("ft", "x", 600, 6, 16.667, 50.0, 60, 1))
        bad = kv(run("ft", "stutter", 600, 60, 16.667, 40.0, 60, 1))
        check("60 slow frames in 600 (10 %) FAIL as stutter", bad["verdict"] == "0" and "stutter" in run("ft", "stutter", 600, 60, 16.667, 40.0, 60, 1), bad)
        slow = run("ft", "slow", 300, 0, 50.0, 0, 60, 1)
        check("20 fps FAILs as slow", kv(slow)["verdict"] == "0" and "slow" in slow, slow)
        late = run("ft", "late", 600, 0, 16.667, 0, 120, 1)
        check("60 fps with VSync on a 120 Hz display FAILs: it does not hold the display's rate", kv(late)["verdict"] == "0" and "does not hold" in late, late)
        check("... but with VSync off the same frame times PASS", kv(run("ft", "novsync", 600, 0, 16.667, 0, 120, 0))["verdict"] == "1")
        check("30 frames are too few for a verdict", kv(run("ft", "few", 30, 0, 16.667, 0, 60, 1))["verdict"] == "0" and "only 30 frames" in run("ft", "few", 30, 0, 16.667, 0, 60, 1))
        refresh = dict(p.split("=") for p in run("refresh", 16.667, 8.333, 33.333, 11.111, 6.944, 25.0, 20.0, 41.7, 4.17, 15.2).split())
        check("frame intervals are classed as 60, 120, 30, 90, 144, 50, 24 and 240 Hz (the nearest, within 6 %); 25 ms (40 Hz) and 15.2 ms (66 Hz) as none",
              [refresh[k] for k in ("16.667", "8.333", "33.333", "11.111", "6.944", "20.0", "41.7", "4.17", "25.0", "15.2")] == ["60", "120", "30", "90", "144", "50", "24", "240", "0", "0"], refresh)

        # the tone
        tone = run("tone", 48000).splitlines()
        seg = {}
        for line in tone:
            m = re.match(r"seg(\d) ch(\d) rms=([\d.]+) peak=(\d+) crossings=(\d+)", line)
            if m:
                seg[(int(m[1]), int(m[2]))] = (float(m[3]), int(m[4]), int(m[5]))
        check("the tone: second 1 is the left channel only, second 2 the right channel only, second 3 both",
              seg[(0, 0)][0] > 4000 and seg[(0, 1)][0] == 0 and seg[(1, 0)][0] == 0 and seg[(1, 1)][0] > 4000 and seg[(2, 0)][0] > 4000 and seg[(2, 1)][0] > 4000, seg)
        check("... at a quarter of full scale (peak 8192) and an RMS of 0.177 of full scale",
              all(abs(seg[k][1] - 8191) <= 2 for k in ((0, 0), (1, 1), (2, 0), (2, 1))) and abs(seg[(0, 0)][0] - 8192 / 2 ** 0.5) < 60, seg)
        check("... the left tone is 440 Hz and the right one 660 Hz (zero crossings in a second, counted)",
              abs(seg[(0, 0)][2] - 880) <= 3 and abs(seg[(2, 0)][2] - 880) <= 3 and abs(seg[(1, 1)][2] - 1320) <= 3 and abs(seg[(2, 1)][2] - 1320) <= 3, seg)
        check("... with faded edges (the first and last samples are silent)", "first=0,0" in tone[-2] and re.search(r"last=-?\d,-?\d$", tone[-2]) is not None, tone[-2])
        t44 = run("tone", 44100).splitlines()
        check("the same at 44.1 kHz: 440 Hz left, 660 Hz right", any("seg0 ch0" in l and abs(int(l.split("crossings=")[1]) - 880) <= 3 for l in t44)
              and any("seg1 ch1" in l and abs(int(l.split("crossings=")[1]) - 1320) <= 3 for l in t44), t44)
        check("the mono tone for the pan test: 440 Hz for two seconds", tone[-1].startswith("mono n=96000") and abs(int(tone[-1].split("crossings=")[1].split()[0]) - 1760) <= 4 and "first=0" in tone[-1], tone[-1])

        # pixel formats
        pk = run("pack").splitlines()
        check("565: red f800, green 07e0, blue 001f, white ffff", pk[0] == "565 red=f800 green=07e0 blue=001f white=ffff", pk[0])
        check("555: white 7fff, (128, 64, 8) = 4101", pk[1] == "555 white=7fff mid=4101", pk[1])
        check("888 and BGR masks", pk[2] == "888 value=010203" and pk[3] == "bgr value=030201", pk[2:4])
        check("unpacking gives the colour back (8-bit exact, 565 and 555 with the low bits filled)",
              pk[4] == "unpack888 10,20,30" and pk[5] == "unpack565red 255,0,0" and pk[6] == "unpack565white 255,255,255" and pk[7] == "unpack555white 255,255,255", pk[4:])

        # YUV
        yv = run("yuv").splitlines()
        check("BT.601 limited range: black, white, red and blue come out right",
              yv[0] == "black 0,0,0" and yv[1] == "white 255,255,255" and all(abs(int(a) - b) <= 4 for a, b in zip(yv[2].split()[1].split(","), (255, 0, 0)))
              and all(abs(int(a) - b) <= 4 for a, b in zip(yv[3].split()[1].split(","), (0, 0, 255))), yv)
        check("the hue classes and the expected progress bars", yv[4] == "hue red=1 blue=2 grey=0" and yv[5] == "expected bars 5 80 160", yv[4:])

        # the clip generator's own frames, converted to each format the video test accepts and measured
        gen = R / "tools/video-test-clips/genframes.py"
        src = tmp / "src"
        g = sh([sys.executable, "-I", str(gen), str(src)])
        check("tools/video-test-clips/genframes.py makes 30 frames of 160x96 and the tone", g.returncode == 0 and (src / "frames.bgra").stat().st_size == 30 * 160 * 96 * 4
              and (src / "tone.pcm").stat().st_size == 44100 * 2 * 2, g.stderr)
        if g.returncode == 0:
            frames = (src / "frames.bgra").read_bytes()
            W, H = 160, 96
            fsz = W * H * 4

            def to_planes(bgra):
                y = bytearray(W * H)
                u = bytearray((W // 2) * (H // 2))
                v = bytearray((W // 2) * (H // 2))
                cu = [[0, 0, 0] for _ in range((W // 2) * (H // 2))]
                for row in range(H):
                    for col in range(W):
                        b, gg, r = bgra[(row * W + col) * 4:(row * W + col) * 4 + 3]
                        y[row * W + col] = (66 * r + 129 * gg + 25 * b + 128 >> 8) + 16
                        k = (row // 2) * (W // 2) + col // 2
                        cu[k][0] += r; cu[k][1] += gg; cu[k][2] += b
                for k, (r, gg, b) in enumerate(cu):
                    r, gg, b = r / 4, gg / 4, b / 4
                    u[k] = int(((-38 * r - 74 * gg + 112 * b + 128) / 256) + 128)
                    v[k] = int(((112 * r - 94 * gg - 18 * b + 128) / 256) + 128)
                return bytes(y), bytes(u), bytes(v)

            fails = []
            for fmt in ("nv12", "i420", "yv12", "yuy2", "rgb32"):
                bad = []
                for i in range(30):
                    frame = frames[i * fsz:(i + 1) * fsz]
                    y, u, v = to_planes(frame)
                    if fmt == "nv12":
                        data, pitch = y + b"".join(bytes((a, b)) for a, b in zip(u, v)), W
                    elif fmt == "i420":
                        data, pitch = y + u + v, W
                    elif fmt == "yv12":
                        data, pitch = y + v + u, W
                    elif fmt == "yuy2":
                        data = bytearray()
                        for row in range(H):
                            for col in range(0, W, 2):
                                k = (row // 2) * (W // 2) + col // 2
                                data += bytes((y[row * W + col], u[k], y[row * W + col + 1], v[k]))
                        data, pitch = bytes(data), W * 2
                    else:
                        data, pitch = frame, W * 4
                    (tmp / "pic.bin").write_bytes(data)
                    out = kv(run("picture", fmt, tmp / "pic.bin", W, H, pitch))
                    want = (i + 1) * 160 // 30
                    if not (abs(int(out["bar"]) - want) <= 2 and out["top"] != "" and out["left"] == "1" and out["right"] == "2"):
                        bad.append((i, want, out))
                if bad:
                    fails.append((fmt, bad[:3]))
            check("the video test's measurements on the generator's frames (30 each) in NV12, I420, YV12, YUY2 and RGB32: bar length within 2 px, red left, blue right",
                  not fails, fails)

    # ---------------------------------------------------------------- 2. the clips
    clips = DX / "clips"
    mp4 = (clips / "test.mp4").read_bytes()
    wmv = (clips / "test.wmv").read_bytes()
    hdr = (DX / "video_clips.h").read_text()
    for name, data in (("mp4", mp4), ("wmv", wmv)):
        m = re.search(r"// clip-%s-sha256: ([0-9a-f]{64})" % name, hdr)
        arr = re.search(r"static const unsigned char clip_%s\[\] = \{(.*?)\n\};" % name, hdr, re.S)
        got = bytes(int(x, 16) for x in re.findall(r"0x([0-9a-f]{2})", arr.group(1))) if arr else b""
        check("video_clips.h carries test.%s byte for byte, with its SHA-256 (%d bytes)" % (name, len(data)),
              m is not None and m[1] == hashlib.sha256(data).hexdigest() and got == data, (len(got), len(data)))
    check("the clips are small (tiny, synthetic, no third-party media): MP4 under 40 KB, WMV under 60 KB", len(mp4) < 40000 and len(wmv) < 60000, (len(mp4), len(wmv)))

    def boxes(d, off, end):
        while off + 8 <= end:
            size, typ = struct.unpack_from(">I4s", d, off)
            hl = 8
            if size == 1:
                size = struct.unpack_from(">Q", d, off + 8)[0]
                hl = 16
            if size == 0:
                size = end - off
            yield typ.decode("latin1"), off, off + hl, off + size
            off += size

    def find(d, path, off=0, end=None):
        end = len(d) if end is None else end
        for typ, start, body, stop in boxes(d, off, end):
            if typ == path[0]:
                if len(path) == 1:
                    return start, body, stop
                got = find(d, path[1:], body, stop)
                if got:
                    return got
        return None

    def traks(d):
        moov = find(d, ["moov"])
        return [(b, e) for t, s, b, e in boxes(d, moov[1], moov[2]) if t == "trak"]

    top = [t for t, *_ in boxes(mp4, 0, len(mp4))]
    check("MP4: ftyp, mdat and moov, boxes that add up to the file", top[0] == "ftyp" and set(top) == {"ftyp", "mdat", "moov"} and sum(e - s for _, s, _, e in boxes(mp4, 0, len(mp4))) == len(mp4), top)
    tk = traks(mp4)
    check("MP4: two tracks", len(tk) == 2)
    vtrak = atrak = None
    for b, e in tk:
        hd = find(mp4, ["mdia", "hdlr"], b, e)
        kind = mp4[hd[1] + 8:hd[1] + 12]
        if kind == b"vide":
            vtrak = (b, e)
        elif kind == b"soun":
            atrak = (b, e)
    check("MP4: one video track and one sound track", vtrak is not None and atrak is not None)
    if vtrak and atrak:
        stsd = find(mp4, ["mdia", "minf", "stbl", "stsd"], *vtrak)
        entry = stsd[1] + 8
        etype = mp4[entry + 4:entry + 8]
        w, h = struct.unpack_from(">HH", mp4, entry + 8 + 24)
        check("MP4 video: H.264 (avc1), 160x96", etype == b"avc1" and (w, h) == (160, 96), (etype, w, h))
        avcc = mp4.find(b"avcC", entry)
        profile, compat, level = mp4[avcc + 5], mp4[avcc + 6], mp4[avcc + 7]
        nsps = mp4[avcc + 9] & 31
        sps_len = struct.unpack_from(">H", mp4, avcc + 10)[0]
        sps = mp4[avcc + 12:avcc + 12 + sps_len]
        check("MP4 video: Baseline profile (66), one parameter set", profile == 66 and nsps == 1 and (sps[0] & 31) == 7 and sps[1] == 66, (profile, nsps, sps[:4].hex()))

        def rbsp(nal):
            return bytes(b for i, b in enumerate(nal) if not (i >= 2 and nal[i - 2:i] == b"\0\0" and b == 3))

        class Bits:
            def __init__(self, data):
                self.d, self.p = data, 0

            def u(self, n):
                v = 0
                for _ in range(n):
                    v = (v << 1) | (self.d[self.p >> 3] >> (7 - (self.p & 7)) & 1)
                    self.p += 1
                return v

            def ue(self):
                z = 0
                while self.u(1) == 0:
                    z += 1
                return (1 << z) - 1 + self.u(z)

        bits = Bits(rbsp(sps)[1:])
        pi, _, li = bits.u(8), bits.u(8), bits.u(8)
        bits.ue()   # sps id
        log2_fn = bits.ue()
        poc_type = bits.ue()
        if poc_type == 0:
            bits.ue()
        bits.ue()   # max ref frames
        bits.u(1)
        mbw, mbh = bits.ue() + 1, bits.ue() + 1
        frame_mbs_only = bits.u(1)
        check("MP4 video: the sequence parameter set says 10 x 6 macroblocks (160x96), progressive", (mbw, mbh) == (10, 6) and frame_mbs_only == 1, (mbw, mbh, frame_mbs_only))
        mdhd = find(mp4, ["mdia", "mdhd"], *vtrak)
        timescale = struct.unpack_from(">I", mp4, mdhd[1] + 12)[0]
        stts = find(mp4, ["mdia", "minf", "stbl", "stts"], *vtrak)
        n_entries = struct.unpack_from(">I", mp4, stts[1] + 4)[0]
        count, delta = struct.unpack_from(">II", mp4, stts[1] + 8)
        check("MP4 video: 30 samples, 1/15 s each", n_entries == 1 and count == 30 and abs(delta / timescale - 1 / 15) < 1e-6, (n_entries, count, delta, timescale))
        stsz = find(mp4, ["mdia", "minf", "stbl", "stsz"], *vtrak)
        nsz = struct.unpack_from(">I", mp4, stsz[1] + 8)[0]
        sizes = struct.unpack_from(">%dI" % nsz, mp4, stsz[1] + 12) if struct.unpack_from(">I", mp4, stsz[1] + 4)[0] == 0 else ()
        check("MP4 video: a size for each of the 30 samples, all of them inside the file", nsz == 30 and len(sizes) == 30 and sum(sizes) < len(mp4), (nsz, len(sizes)))
        check("MP4 video: sync samples are listed (the first picture is one)", find(mp4, ["mdia", "minf", "stbl", "stss"], *vtrak) is not None)
        astsd = find(mp4, ["mdia", "minf", "stbl", "stsd"], *atrak)
        aentry = astsd[1] + 8
        channels, bits_per = struct.unpack_from(">HH", mp4, aentry + 8 + 16)
        rate = struct.unpack_from(">I", mp4, aentry + 8 + 24)[0] >> 16
        esds = mp4.find(b"esds", aentry)
        # the AudioSpecificConfig: the first byte's top five bits are the audio object type (2 = AAC-LC)
        cfg = mp4.find(b"\x05\x80\x80\x80", esds)
        asc = (mp4[cfg + 5] << 8 | mp4[cfg + 6]) if cfg > 0 else 0
        aot, fidx, chcfg = asc >> 11, (asc >> 7) & 15, (asc >> 3) & 15
        check("MP4 audio: AAC (mp4a), AAC-LC, one channel in the AudioSpecificConfig (the sample entry may say 2), 44100 Hz", mp4[aentry + 4:aentry + 8] == b"mp4a" and esds > 0 and aot == 2
              and fidx == 4 and chcfg == 1 and channels in (1, 2) and rate == 44100, (mp4[aentry + 4:aentry + 8], aot, fidx, chcfg, channels, rate))

    # the ASF file
    def guid(b):
        return "%08X-%04X-%04X-%s-%s" % (struct.unpack_from("<IHH", b, 0) + (b[8:10].hex().upper(), b[10:16].hex().upper()))

    G_HEADER = "75B22630-668E-11CF-A6D9-00AA0062CE6C"
    G_DATA = "75B22636-668E-11CF-A6D9-00AA0062CE6C"
    G_FILEPROP = "8CABDCA1-A947-11CF-8EE4-00C00C205365"
    G_STREAM = "B7DC0791-A9B7-11CF-8EE6-00C00C205365"
    G_VIDEO = "BC19EFC0-5B4D-11CF-A8FD-00805F5C442B"
    G_AUDIO = "F8699E40-5B4D-11CF-A8FD-00805F5C442B"
    check("ASF: starts with the header object, its size and its object count are what the file holds", guid(wmv[:16]) == G_HEADER, guid(wmv[:16]))
    hsize, hcount = struct.unpack_from("<QI", wmv, 16)
    objs = []
    off = 30
    for _ in range(hcount):
        g = guid(wmv[off:off + 16])
        sz = struct.unpack_from("<Q", wmv, off + 16)[0]
        objs.append((g, off, sz))
        off += sz
    check("ASF: the header's objects add up to the header's size", off == hsize and len(objs) == hcount, (off, hsize, hcount))
    dataobj = wmv[hsize:hsize + 16]
    dsize = struct.unpack_from("<Q", wmv, hsize + 16)[0]
    G_INDEX = "33000890-E5B1-11CF-89F4-00A0C90349CB"
    tail = wmv[hsize + dsize:hsize + dsize + 16]
    check("ASF: the data object follows the header, and what is left of the file after it is the simple index object",
          guid(dataobj) == G_DATA and hsize + dsize < len(wmv) and guid(tail) == G_INDEX and hsize + dsize + struct.unpack_from("<Q", wmv, hsize + dsize + 16)[0] == len(wmv), (guid(dataobj), hsize, dsize, len(wmv)))
    fp = [o for o in objs if o[0] == G_FILEPROP]
    if fp:
        packets = struct.unpack_from("<Q", wmv, fp[0][1] + 56)[0]
        play_dur = struct.unpack_from("<Q", wmv, fp[0][1] + 64)[0] / 1e7
        preroll = struct.unpack_from("<Q", wmv, fp[0][1] + 80)[0] / 1000.0
        check("ASF: file properties give a play time of 2 s plus the preroll, and a packet count", 1.9 < play_dur - preroll < 2.3 and packets > 0, (play_dur, preroll, packets))
    streams = [o for o in objs if o[0] == G_STREAM]
    kinds = {}
    for g, o, sz in streams:
        stype = guid(wmv[o + 24:o + 40])
        tsd_len = struct.unpack_from("<I", wmv, o + 24 + 40)[0]
        tsd = wmv[o + 24 + 54:o + 24 + 54 + tsd_len]
        kinds[stype] = tsd
    check("ASF: a video stream and an audio stream", G_VIDEO in kinds and G_AUDIO in kinds, list(kinds))
    if G_VIDEO in kinds:
        vw, vh = struct.unpack_from("<II", kinds[G_VIDEO], 0)
        fourcc = kinds[G_VIDEO][4 + 1 + 2 + 4 + 16:4 + 1 + 2 + 4 + 16 + 4]   # image info header (11 bytes), then BITMAPINFOHEADER up to biCompression at +16
        bih = kinds[G_VIDEO][11:]
        bw, bh = struct.unpack_from("<ii", bih, 4)
        comp = bih[16:20]
        check("ASF video: WMV2, 160x96", comp == b"WMV2" and (bw, abs(bh)) == (160, 96) and (vw, vh) == (160, 96), (comp, bw, bh, vw, vh))
    if G_AUDIO in kinds:
        tag, ch, rate = struct.unpack_from("<HHI", kinds[G_AUDIO], 0)
        check("ASF audio: Windows Media Audio 2 (0x0161), 1 channel, 44100 Hz", (tag, ch, rate) == (0x161, 1, 44100), (hex(tag), ch, rate))

    # decoding the clips: the MP4 with macOS's decoder, the WMV with an FFmpeg that has the asf demuxer
    swiftc = shutil.which("swiftc")
    expected_bars = [(i + 1) * 160 // 30 for i in range(30)]
    if sys.platform == "darwin" and swiftc:
        tool = tmp / "checkmp4"
        c = sh([swiftc, "-O", str(R / "tools/video-test-clips/checkmp4.swift"), "-o", str(tool)])
        if c.returncode == 0:
            r = sh([str(tool), str(clips / "test.mp4")])
            out = r.stdout
            bars = [int(x) for x in (re.search(r"^bars ([\d,]+)$", out, re.M) or [None, ""])[1].split(",") if x]
            check("the MP4 decodes (AVAssetReader) to 160x96, 30 pictures, 2.0 s, one sound track",
                  re.search(r"^size 160x96$", out, re.M) and re.search(r"^frames 30$", out, re.M) and re.search(r"^duration 2\.0", out, re.M) and re.search(r"^audio tracks 1$", out, re.M), out + r.stderr)
            check("... the progress bar of picture i is (i + 1) * 160 / 30 pixels long, within 2", len(bars) == 30 and all(abs(a - b) <= 2 for a, b in zip(bars, expected_bars)), (bars, expected_bars))
            m = re.search(r"left (\d+),(\d+),(\d+) right (\d+),(\d+),(\d+)", out)
            check("... red on the left half and blue on the right", m is not None and int(m[1]) > 150 and int(m[3]) < 80 and int(m[6]) > 150 and int(m[4]) < 80, out)
        else:
            note("swiftc could not build checkmp4.swift; the MP4 was not decoded: " + c.stderr[-200:])
    else:
        note("not a Mac with swiftc: the MP4 was not decoded")
    ffmpeg = os.environ.get("FFMPEG")
    if ffmpeg and Path(ffmpeg).exists():
        raw = tmp / "wmv.gray"
        r = sh([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(clips / "test.wmv"), "-f", "rawvideo", "-pix_fmt", "gray", str(raw)])
        data = raw.read_bytes() if raw.exists() else b""
        n = len(data) // (160 * 96)
        bars = [sum(1 for v in data[i * 160 * 96 + (96 - 4) * 160:i * 160 * 96 + (96 - 3) * 160] if v > 130) for i in range(n)]
        # the encoder repeats the first picture once; each picture is either the expected bar or the one before it
        good = sum(1 for i, b in enumerate(bars) if abs(b - expected_bars[min(i, 29)]) <= 2 or (i > 0 and abs(b - expected_bars[min(i - 1, 29)]) <= 2))
        check("the WMV decodes (FFmpeg: asf demuxer, wmv2 decoder) to 160x96 pictures with the right progress bars", r.returncode == 0 and 30 <= n <= 32 and good >= n - 1, (r.stderr[-200:], n, bars))
    else:
        note("FFMPEG=<an ffmpeg with the asf demuxer and wmv2> was not set: the WMV was not decoded")

    # ---------------------------------------------------------------- 3. the sources
    for f in NEW_FILES + ["d3d9_cube.c"]:
        head = (DX / f).read_text()[:400]
        check("%s has the BCD licence header" % f, HEADER in head and "LICENSE-BCD.md" in head)
    for f in ("tools/gen-video-test-clips.sh", "tools/video-test-clips/genframes.py", "tools/video-test-clips/mkmp4.swift", "tools/video-test-clips/checkmp4.swift", "tests/host/check-bcd-tests.py"):
        head = (R / f).read_text()[:400]
        check("%s has the BCD licence header" % f, HEADER in head and "LICENSE-BCD.md" in head)
    common = (DX / "bcdtest.h").read_text()
    check("the log helper writes one line to stderr in one WriteFile and the same line to <temp>\\bcd-test.log",
          'GetStdHandle(STD_ERROR_HANDLE)' in common and "WriteFile(e, line" in common and '"bcd-test.log"' in common and "GetTempPathA" in common and '"[bcd-test] %s (%d-bit): "' in common)
    check("a check is logged as PASS or FAIL with its numbers and counted; bt_result() writes the one RESULT line with the failed count and the program's headline numbers",
          'bt_log("%s %s%s%s", ok ? "PASS" : "FAIL"' in common and 'bt_log("RESULT %s (%d of %d checks passed, %d failed)%s%s"' in common and "bt_headline" in common)
    # A program started from the Start menu has no standard error (shell32 passes CREATE_NEW_CONSOLE, kernelbase then passes no standard
    # handles and a GUI program gets no console), so the session log gets its lines through the [guest-log] mirror of a *.log file, which
    # copies only the lines that say one of a few words. The lines that matter must say one of them.
    mirror = (R / "tools/patch-wine-guest-log.py").read_text()
    words = re.search(r"static const char \*const words\[\] =\s*\{(.*?)\};", mirror, re.S)
    words = re.findall(r'"([^"]+)"', words.group(1)) if words else []
    def mirrored(line):
        return any(w in line.lower() for w in words)
    check("the mirror's word list was read from tools/patch-wine-guest-log.py (error, fail, exception, gpu, driver, adapter, display, ...)", {"error", "fail", "exception", "adapter", "display"} <= set(words), words)
    sample = {
        "the RESULT line, PASS or FAIL": "[bcd-test] sound (64-bit): RESULT PASS (14 of 14 checks passed, 0 failed): 3.00 s of audio played in 3.02 s",
        "a FAIL check line": "[bcd-test] video (32-bit): FAIL raw video output accepted: none of NV12, YUY2, RGB32, I420, YV12",
        "the crash line": "[bcd-test] video (32-bit): CRASH exception 0xc0000005 at 0x1234 in mfplat.dll+0x1c3a; the test stops here",
        "the cube verdict line": '[dx-test] Direct3D 9 (32-bit): PASS drawing: 118 frames in 2.0 s (59 fps), 0 Present calls failed, adapter "Apple M1"',
        "the Direct3D 8 RESULT line": "[dx-test] Direct3D 8 (32-bit): RESULT PASS (9 of 9 checks passed, 0 failed, drawing PASS)",
    }
    for what, line in sample.items():
        check("%s reaches the session log through the [guest-log] mirror (it says one of the mirror's words)" % what, mirrored(line), line)
    check("a plain PASS check line does not need to be mirrored (the RESULT line carries the numbers): the format of a PASS line is not relied on", True)
    check("every program puts its headline numbers on the RESULT line (bt_headline)", all("bt_headline(" in (DX / f).read_text() for f in
          ("ddraw_test.c", "frametime_test.c", "resolution_test.c", "sound_test.c", "video_test.c", "controller_test.c", "input_test.c")))
    check("the cube tests' log helper also writes <temp>\\bcd-test.log, and their verdict and RESULT lines say 'failed' and the adapter",
          "dxt_logfile" in (DX / "dxtest.h").read_text() and "bcd-test.log" in (DX / "dxtest.h").read_text() and 'Present calls failed, adapter' in (DX / "dxtest.h").read_text()
          and "%u failed, drawing %s" in (DX / "d3d8_cube.c").read_text())
    check("an unhandled exception is a log line (code, address, module) and a RESULT FAIL, then the program ends", "SetUnhandledExceptionFilter(bt_crash_filter)" in common and "CRASH exception" in common
          and 'bt_log("RESULT FAIL (crash, 1 failed)")' in common and "ExitProcess(70)" in common)
    dxh = (DX / "dxtest.h").read_text()
    check("the cube tests' header has the verdict and the crash line (information only: it does not handle the exception)",
          "static inline int dxt_verdict" in dxh and "EXCEPTION_CONTINUE_SEARCH" in dxh and "dxt_crash_log" in dxh and "#define DXT_BITS" in dxh)
    stats_h = (DX / "bcdstats.h").read_text()
    check("bcdstats.h has no Windows header (the host check compiles it)", "windows.h" not in stats_h)

    srcs = {f: (DX / f).read_text() for f in ("d3d9_cube.c", "d3d8_cube.c", "ddraw_test.c", "frametime_test.c", "resolution_test.c", "sound_test.c", "video_test.c", "controller_test.c", "input_test.c")}
    s = srcs["d3d9_cube.c"]
    check("d3d9: one source for both bitnesses: the title carries the bits, the verdict is logged after two seconds with the frame count and rate, a read-back follows",
          'snprintf(title, sizeof title, "BCD DirectX 9 test (%d-bit)", DXT_BITS)' in s and "dxt_verdict(API, presented, failures" in s and "GetRenderTargetData" in s and "dxt_crash_log();" in s)
    s = srcs["d3d8_cube.c"]
    check("d3d8: LoadLibrary d3d8.dll, Direct3DCreate8, the start-up calls (adapter, display mode, device type and format, caps), CreateDevice, a cube with the fixed-function pipeline, the verdict and a CopyRects read-back",
          all(x in s for x in ('LoadLibraryA("d3d8.dll")', '"Direct3DCreate8"', "GetAdapterIdentifier", "GetAdapterDisplayMode", "CheckDeviceType", "CheckDeviceFormat", "GetDeviceCaps", "CreateDevice",
                               "DrawIndexedPrimitive", "SetVertexShader(dev, FVF8)", "CopyRects", "dxt_verdict", "RESULT")))
    check("d3d8: it never decides beforehand that Direct3D 8 cannot work (it asks for the device and logs the answer)", "UNAVAILABLE" not in s and "MADEIRA_D3D8_TO_9" not in s)
    s = srcs["ddraw_test.c"]
    check("ddraw: the legacy entry points, caps, display modes, primary surface with a clipper, colour fill, locked write, stretch blit, colour key, palette, animation and the full-screen part",
          all(x in s for x in ("DirectDrawEnumerateA", "DirectDrawCreate\"", "DirectDrawCreateEx", "GetCaps", "EnumDisplayModes", "CreateClipper", "DDBLT_COLORFILL", "DDBLT_KEYSRC", "CreatePalette",
                               "DDPF_PALETTEINDEXED8", "SetDisplayMode(dd, 640, 480, 8", "DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN", "DDFLIP_WAIT", "RestoreDisplayMode", "bt_result")))
    check("ddraw: the full-screen part (it changes the display mode) waits for F or a tap", "g_key == 'F' || g_tap" in s and "if (reported && !fs_done)" in s)
    s = srcs["frametime_test.c"]
    check("frametime: Direct3D 11 with the cube shaders, cubes at four loads, a bar a frame in the graph, a text panel from GDI, VSync toggle, ten-second verdicts through bt_ft_verdict",
          all(x in s for x in ("cube_vs_5_0", "static const int loads[5] = { 0, 1, 16, 64, 256 }", "HIST 200", "CreateDIBSection", "Present(swap, vsync ? 1 : 0", "bt_ft_verdict", "bt_ft_stats", "bt_refresh_class", "g_vsync_req")))
    s = srcs["resolution_test.c"]
    check("resolution: EnumDisplaySettingsEx, a window resize checked against GetClientRect and WM_SIZE, maximise, a borderless full-screen window, ChangeDisplaySettingsEx (CDS_TEST then CDS_FULLSCREEN) and back, DXGI SetFullscreenState on and off with ResizeBuffers",
          all(x in s for x in ("EnumDisplaySettingsExA", "SetWindowPos(h, NULL, 0, 0", "SW_MAXIMIZE", "WS_POPUP", "CDS_TEST", "CDS_FULLSCREEN", "WM_DISPLAYCHANGE", "SetFullscreenState(sc, TRUE", "SetFullscreenState(sc, FALSE", "ResizeBuffers")))
    check("resolution: the mode change and DXGI full screen wait for a button (they change the display)", "id == 'M' || id == 'A'" in s and "id == 'F' || id == 'A'" in s)
    s = srcs["sound_test.c"]
    check("sound: XAudio2 (2.9, then 2.8) and DirectSound, the tone from bt_tone_stereo, real-time playback measured, DirectSound pan checked with GetPan",
          all(x in s for x in ('"xaudio2_9.dll"', '"xaudio2_8.dll"', "XAudio2Create", "CreateMasteringVoice", "CreateSourceVoice", "SubmitSourceBuffer", "IXAudio2SourceVoice_Start", "GetState", "DirectSoundCreate8",
                               "CreateSoundBuffer", "IDirectSoundBuffer_Lock", "IDirectSoundBuffer_Play", "GetCurrentPosition", "SetPan", "GetPan", "bt_tone_stereo", "bt_tone_mono")))
    s = srcs["video_test.c"]
    check("video: Media Foundation source reader on the two embedded clips, raw output types asked for in turn, every picture measured (bar, order, colours, upside down), the audio level and length checked",
          all(x in s for x in ("MFCreateSourceReaderFromURL", "clip_mp4", "clip_wmv", "MFVideoFormat_NV12", "bt_bgra_bar_length", "bt_bgra_hue", "bt_clip_expected_bar", "MFAudioFormat_Float", "IMF2DBuffer_Lock2D", "pictures decoded",
                               "upside down")))
    s = srcs["controller_test.c"]
    check("controller: XInputGetState for pads 0 to 3, capabilities, rumble with XInputSetState, DirectInput 8 enumeration and live state; the result at the end",
          all(x in s for x in ("XInputGetState", "XInputSetState", "XInputGetCapabilities", "wLeftMotorSpeed", "DirectInput8Create", "DI8DEVCLASS_GAMECTRL", "c_dfDIJoystick2", "Poll", "the pad responded", "an XInput pad is connected")))
    s = srcs["input_test.c"]
    check("input: SendInput self-test (move, click, wheel, key), raw input, touch (RegisterTouchWindow, WM_TOUCH, WM_POINTER, from-touch detection), mouse look, and the live state",
          all(x in s for x in ("SendInput", "MOUSEEVENTF_ABSOLUTE", "WM_MOUSEWHEEL", "WM_CHAR", "RegisterRawInputDevices", "WM_INPUT", "GetRawInputDeviceList", "RegisterTouchWindow", "WM_TOUCH", "WM_POINTERDOWN",
                               "0xFF515700", "SetCursorPos", "toggle_look")))
    for f, text in srcs.items():
        if f in ("d3d9_cube.c",):
            continue
        if f == "d3d8_cube.c":
            check("%s: ends with a RESULT line" % f, "RESULT %s" in text or "RESULT FAIL" in text)
            continue
        check("%s: starts with bt_init, counts checks, ends with a RESULT line (bt_result), and the window ends the program when closed" % f,
              "bt_init(" in text and ("bt_result()" in text) and ("bt_report(" in text or "bt_check(" in text) and ("PostQuitMessage" in text or "bt_panel_new" in text))
    for f, names in LOADS.items():
        text = srcs[f]
        check("%s loads %s at run time (LoadLibraryA), so that a session without it gets a message, not a loader failure" % (f, " and ".join(names)),
              all(('"%s"' % n) in text for n in names) and "LoadLibraryA(" in text)

    # ---------------------------------------------------------------- 4. the builds
    toolchain = None
    for cand in (R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin", Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"):
        if (cand / "x86_64-w64-mingw32-clang").exists():
            toolchain = cand
            break

    def pe_info(path):
        data = path.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        assert data[pe:pe + 4] == b"PE\0\0"
        machine = struct.unpack_from("<H", data, pe + 4)[0]
        chars = struct.unpack_from("<H", data, pe + 22)[0]
        opt = pe + 24
        magic = struct.unpack_from("<H", data, opt)[0]
        subsystem = struct.unpack_from("<H", data, opt + 68)[0]
        nsections = struct.unpack_from("<H", data, pe + 6)[0]
        soh = struct.unpack_from("<H", data, pe + 20)[0]
        dirs = opt + (112 if magic == 0x20B else 96)
        import_rva = struct.unpack_from("<I", data, dirs + 8)[0]
        sections = []
        for i in range(nsections):
            sec = opt + soh + 40 * i
            sections.append(struct.unpack_from("<IIII", data, sec + 8))

        def rva(x):
            for vsize, va, rawsize, rawptr in sections:
                if va <= x < va + max(vsize, rawsize):
                    return x - va + rawptr
            raise ValueError(x)
        names = []
        p = rva(import_rva)
        while True:
            desc = struct.unpack_from("<IIIII", data, p)
            if desc[3] == 0:
                break
            end = data.index(b"\0", rva(desc[3]))
            names.append(data[rva(desc[3]):end].decode("ascii").lower())
            p += 20
        return machine, subsystem, chars, names, data

    def without_timestamp(data):
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        return data[:pe + 8] + b"\0\0\0\0" + data[pe + 12:pe + 24 + 64] + b"\0\0\0\0" + data[pe + 24 + 68:]

    base_ok = re.compile(r"^(kernel32|user32|gdi32|api-ms-win-crt-[a-z-]+-l1-1-0)\.dll$")
    for exe, (src, arch, folder, extra) in PROGRAMS.items():
        path = APP / folder / exe
        check("%s is tracked in app/Madeira/%s" % (exe, folder), path.is_file())
        if not path.is_file():
            continue
        machine, subsystem, chars, imports, data = pe_info(path)
        want_machine = 0x14c if arch == "i686" else 0x8664
        check("%s is a %s program for the GUI subsystem%s" % (exe, "32-bit x86" if arch == "i686" else "x86-64", ", large-address-aware" if arch == "i686" else ""),
              machine == want_machine and subsystem == 2 and (arch != "i686" or chars & 0x20), (hex(machine), subsystem, hex(chars)))
        bad = [n for n in imports if not (base_ok.match(n) or n in extra)]
        check("%s imports Windows' own DLLs and the C runtime only%s, and none of the graphics, sound, video or input DLLs it tests (those load at run time)" % (exe, " (plus " + ", ".join(sorted(extra)) + ")" if extra else ""),
              not bad and not any(RUNTIME_DLL.match(n) for n in imports), (bad, imports))
        if toolchain is None:
            continue
        with tempfile.TemporaryDirectory(prefix="bcd-testbuild-") as t:
            out1, out2 = Path(t) / "a.exe", Path(t) / "b.exe"
            flags_ = ["-O2", "-Wall", "-Wextra", "-Werror", "-mwindows", "-Wl,--no-insert-timestamp", "-I", str(DX)]
            if arch == "i686":
                flags_.append("-Wl,--large-address-aware")
            cc = str(toolchain / ("%s-w64-mingw32-clang" % arch))
            b1 = sh([cc, *flags_, "-o", str(out1), str(DX / src), *LIBS[src]])
            errs = [l for l in b1.stderr.splitlines() if "error" in l or "warning" in l]
            check("%s builds under -Wall -Wextra -Werror" % exe, b1.returncode == 0, errs or b1.stderr)
            if b1.returncode == 0:
                check("%s is what its source builds to (apart from the PE time stamps)" % exe, without_timestamp(out1.read_bytes()) == without_timestamp(data), "rebuild it with tools/build-dx-tests.sh and commit it")
                b2 = sh([cc, *flags_, "-o", str(out2), str(DX / src), *LIBS[src]])
                check("%s builds to the same bytes twice" % exe, b2.returncode == 0 and out1.read_bytes() == out2.read_bytes())
    if toolchain is None:
        note("llvm-mingw is not here (toolchains/ or ~/madeira-bcd/toolchains): the programs were not built again")

    # ---------------------------------------------------------------- 5. where they go
    script = (R / "tools/build-dx-tests.sh").read_text()
    for exe, (src, arch, folder, extra) in PROGRAMS.items():
        if exe == "d3d9-cube-x64.exe":
            check("tools/build-dx-tests.sh builds d3d9-cube-x64.exe in its first loop", '"$t-cube-x64.exe"' in script and "for t in d3d9 d3d10 d3d11; do" in script)
            continue
        check("tools/build-dx-tests.sh builds %s from %s into %s" % (exe, src, "the i386 folder" if arch == "i686" else "arm64ec-windows"),
              re.search(r"build_one %s +%s +%s +\"\$%s\"" % (arch, re.escape(exe), re.escape(src), "SHIP32" if arch == "i686" else "SHIP"), script) is not None)
    check("the script checks the PE it built (machine 0x14c for 32-bit, large-address-aware) and keeps the tracked program when a build fails", "0x14c" in script and "large-address-aware" in script
          and "keeping the tracked test" in script and 'check_pe "$OUT/$name" "$machine"' in script)
    gi = (R / ".gitignore").read_text()
    check(".gitignore lets the 32-bit test programs through", "!app/Madeira/i386-windows/*-test-x86.exe" in gi)
    tracked = sh(["git", "-C", str(R), "ls-files", "app/Madeira/i386-windows"]).stdout.split()
    check("the 32-bit test programs are tracked", all(("app/Madeira/i386-windows/" + e) in tracked for e, v in PROGRAMS.items() if v[1] == "i686"), tracked)
    wf = (R / ".github/workflows/build-ipa.yml").read_text()
    check("the workflow builds the test programs with tools/build-dx-tests.sh and runs this check", "bash tools/build-dx-tests.sh" in wf and "tests/host/check-bcd-tests.py" in wf)
    pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
    check("the Xcode project copies both the arm64ec-windows and the i386-windows folders into the app", "arm64ec-windows in Resources" in pbx and "i386-windows in Resources" in pbx)
    # the Start menu: BCDStartMenu.c is built on the host, the entries are listed, and an install into a scratch prefix whose app carries exactly these
    # programs writes a shortcut for each, 32-bit programs pointing into C:\\windows\\syswow64 and 64-bit ones into system32
    start_drv = r'''
#include <stdio.h>
#include "BCDStartMenu.c"
int main(int argc, char **argv)
{
    size_t i;
    if (argc > 2) {
        bcd_start_result r = bcd_startmenu_install(argv[1], argv[2]);
        printf("written=%d missing=%d failed=%d x86=%d\n", r.written, r.missing, r.failed, r.x86);
        return 0;
    }
    for (i = 0; i < bcd_start_all_count(); i++) {
        const bcd_start_entry *e = bcd_start_all_entry(i);
        printf("%s|%s|%s|%s|%s|%u\n", e->folder, e->name, e->program, e->description, e->arguments ? e->arguments : "", e->flags);
    }
    return 0;
}
'''
    (tmp / "start.c").write_text(start_drv)
    sb = sh(["clang", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-I", str(APP), str(tmp / "start.c"), "-o", str(tmp / "start")])
    check("BCDStartMenu.c builds on the host with the new entries (-Wall -Wextra -Werror)", sb.returncode == 0, sb.stderr)
    if sb.returncode == 0:
        ents = [l.split("|") for l in sh([str(tmp / "start")]).stdout.splitlines()]
        by_prog = {}
        for e in ents:
            by_prog.setdefault(e[2], []).append(e)
        names = [e[1] for e in ents]
        check("every entry has its own name, none has a character a Windows file name cannot hold", len(set(names)) == len(names) and not any(set('/\\:*?"<>|') & set(n) for n in names), names)
        for exe, (src, arch, folder, extra) in PROGRAMS.items():
            got = by_prog.get(exe, [])
            check("the Start menu has %s, in 'BCD Tests'%s" % (exe, ", without a flag (a 32-bit program is found by where the app carries it)" if arch == "i686" else ""),
                  len(got) == 1 and got[0][0] == "Programs/BCD Tests" and got[0][4] == "" and got[0][5] == "0", got)
        check("DirectX 9 test is the 32-bit program and DirectX 9 test (64-bit) the 64-bit one",
              [e[2] for e in ents if e[1] == "DirectX 9 test"] == ["d3d9-test-x86.exe"] and [e[2] for e in ents if e[1] == "DirectX 9 test (64-bit)"] == ["d3d9-cube-x64.exe"])
        check("the 32-bit and the 64-bit variants say so in their names or descriptions",
              all(("(32-bit)" in e[3]) == e[2].endswith("-x86.exe") and ("(64-bit)" in e[3]) == e[2].endswith("-x64.exe") for e in ents if e[2] in PROGRAMS), [e for e in ents if e[2] in PROGRAMS])
        bundle = tmp / "bundle"
        (bundle / "arm64ec-windows").mkdir(parents=True)
        (bundle / "i386-windows").mkdir(parents=True)
        for exe, (src, arch, folder, extra) in PROGRAMS.items():
            (bundle / folder / exe).write_bytes(b"MZ")
        (bundle / "i386-windows/ntdll.dll").write_bytes(b"MZ")   # a 32-bit entry needs a real 32-bit set (BCDStartMenu.c checks the i386 ntdll.dll)
        prefix = tmp / "prefix"
        (prefix / "drive_c/ProgramData/Microsoft/Windows/Start Menu/Programs").mkdir(parents=True)
        out = sh([str(tmp / "start"), str(prefix), str(bundle)]).stdout
        menu = prefix / "drive_c/ProgramData/Microsoft/Windows/Start Menu"
        wrong = []
        for exe, (src, arch, folder, extra) in PROGRAMS.items():
            e = by_prog[exe][0]
            lnk = menu / e[0] / (e[1] + ".lnk")
            want = ("C:\\windows\\syswow64\\" if arch == "i686" else "C:\\windows\\system32\\") + exe
            if not lnk.is_file() or want.encode() not in lnk.read_bytes():
                wrong.append((exe, lnk.is_file(), want))
        check("an install writes a shortcut for each program the app carries: 32-bit ones to C:\\windows\\syswow64, 64-bit ones to C:\\windows\\system32", not wrong and "failed=0" in out, (wrong, out))
    # without the 32-bit set (no i386 ntdll.dll) the 32-bit entries are left out, the 64-bit ones stay
    if sb.returncode == 0:
        (bundle / "i386-windows/ntdll.dll").unlink()
        prefix2 = tmp / "prefix2"
        (prefix2 / "drive_c/ProgramData/Microsoft/Windows/Start Menu/Programs").mkdir(parents=True)
        out2 = sh([str(tmp / "start"), str(prefix2), str(bundle)]).stdout
        menu2 = prefix2 / "drive_c/ProgramData/Microsoft/Windows/Start Menu/Programs/BCD Tests"
        have = sorted(p.stem for p in menu2.glob("*.lnk")) if menu2.is_dir() else []
        check("a build without the 32-bit set gets the 64-bit tests and none of the 32-bit ones",
              have == sorted(by_prog[e][0][1] for e, v in PROGRAMS.items() if v[1] == "x86_64") and "x86=0" in out2 and "failed=0" in out2, (have, out2))
    start_c = (APP / "BCDStartMenu.c").read_text()
    for exe in PROGRAMS:
        check("the Start menu table names %s" % exe, '"%s"' % exe in start_c)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

sys.exit(0 if ok else 1)
