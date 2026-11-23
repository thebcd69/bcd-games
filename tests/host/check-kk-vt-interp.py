#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Apple frame interpolation in KosmicKrisp's present (builds 703-705; build/mesa-ios/playport-patches/0036-*.patch).

Checks, on the code the patch adds (taken out of the patch itself):
  1. the patch: BCD's, last in the series, it touches only the Metal WSI and its two build files, and without MADEIRA_VT_INTERP
     the swapchain is made and presented as before (framebufferOnly as before, wsi_metal_layer_present as before);
  2. the plain C rules, compiled on the host: the key, how many pictures the screen can show, when each goes out, the formats;
  3. the module compiles for iOS 26 (arm64, the iPhone SDK) with -Wall -Wextra and no warning, and exports the app's four
     functions;
  4. on this Mac, a real run (Metal, a Metal 4 queue for the event, VideoToolbox's own processor), kk_vt_interp_harness.m standing
     in for a 30 FPS Vulkan game on a 120 Hz screen with a moving bar:
       - without the key nothing is taken; with it the session loads and pictures are made, with no error;
       - MADEIRA_KK_VTI_SELFTEST: every picture lies between its two frames at its phase (1/2; 1/4, 1/2, 3/4), its bar white and
         its background black after the conversions;
       - the menu's switch off: no more pictures and every later frame presents as without the key; the end of the session;
       - a game whose GPU is 150 ms behind (X-Plane 12's flight on build 704, where one frame in five got its picture): every
         frame gets its picture, at its place, and the game keeps its pace; the switch off there too;
     (skipped where this Mac has no Metal 4 or VideoToolbox's processor says no);
  5. wiring: the series check, the app (start rule, status, switch, the end of a session), the docs and CI.

    tests/host/check-kk-vt-interp.py [--require-run]   --require-run: fail instead of skipping part 4
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
REQUIRE_RUN = "--require-run" in sys.argv
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1800:]) if detail and not cond else ""))
    ok = ok and bool(cond)


def skip(what):
    global ok
    if REQUIRE_RUN:
        print("FAIL " + what + " (--require-run)")
        ok = False
    else:
        print("SKIP " + what)


series = R / "build/mesa-ios/playport-patches"
patch_path = next(iter(sorted(series.glob("0036-*.patch"))), None)
check("0036 is in the Playport series", patch_path is not None and patch_path.name == "0036-wsi-metal-apple-frame-interpolation-MADEIRA_VT_INTERP.patch")
if patch_path is None:
    sys.exit(1)
patch = patch_path.read_text()
head, diff = patch.split("\n---\n", 1)
check("BCD's patch, with its message", head.startswith("From: BCD <190844990+thebcd69@users.noreply.github.com>\nSubject: [PATCH] wsi/metal: Apple frame interpolation"))
files = re.findall(r"^diff --git a/(\S+) b/", diff, re.M)
check("it touches the Metal WSI, its two build files and KosmicKrisp's queue only",
      sorted(files) == ["src/kosmickrisp/vulkan/kk_queue.c", "src/vulkan/meson.build", "src/vulkan/wsi/meson.build",
                        "src/vulkan/wsi/wsi_common_metal.c", "src/vulkan/wsi/wsi_common_metal_lsfg.h", "src/vulkan/wsi/wsi_common_metal_lsfg.m",
                        "src/vulkan/wsi/wsi_common_metal_vti.h", "src/vulkan/wsi/wsi_common_metal_vti.m"], files)
check("the last patch of the series", sorted(p.name for p in series.glob("*.patch"))[-1] == patch_path.name)


def new_file(name):
    """The content of a file the patch adds."""
    part = diff[diff.index(f"diff --git a/{name} b/{name}"):]
    nxt = part.find("\ndiff --git ", 1)
    part = part if nxt < 0 else part[:nxt + 1]
    body = part[part.index("\n@@ "):]
    lines = body.split("\n")[2:]
    return "\n".join(l[1:] for l in lines if l.startswith("+")) + "\n"


def hunks(name):
    part = diff[diff.index(f"diff --git a/{name} b/{name}"):]
    nxt = part.find("\ndiff --git ", 1)
    return part if nxt < 0 else part[:nxt + 1]


vti_m, vti_h = new_file("src/vulkan/wsi/wsi_common_metal_vti.m"), new_file("src/vulkan/wsi/wsi_common_metal_vti.h")
lsfg_m, lsfg_h = new_file("src/vulkan/wsi/wsi_common_metal_lsfg.m"), new_file("src/vulkan/wsi/wsi_common_metal_lsfg.h")   # tests/host/check-kk-lsfg.py
metal_c = hunks("src/vulkan/wsi/wsi_common_metal.c")
added = [l[1:] for l in metal_c.split("\n") if l.startswith("+") and not l.startswith("+++")]
removed = [l[1:] for l in metal_c.split("\n") if l.startswith("-") and not l.startswith("---")]
check("wsi_common_metal.c: only with the key are drawables readable and stand-ins acquired; a drawable is acquired, waited on and presented as before",
      sorted(removed) == sorted(["      CAMetalDrawable *drawable = wsi_metal_layer_acquire_drawable(chain->surface->pLayer);",
                                 "      wsi_metal_layer_add_presented_handler(", "         image->drawable, wsi_metal_present_complete, chain->present_info,",
                                 "         present_id);", "      wsi_metal_layer_present(&image->drawable);", "   const bool framebuffer_only = !wsi_device->sw;"])
      and "   const bool framebuffer_only = !wsi_device->sw && !wsi_metal_vti_wanted();" in added
      and "   chain->vti = !wsi_device->sw && wsi_metal_vti_wanted();   /* madeira-bcd: MADEIRA_VT_INTERP (framebufferOnly is off then too) */" in added
      and "      if (chain->vti) {" in added and "      if (!drawable && vti_status == 0)" in added
      and "         drawable = wsi_metal_layer_acquire_drawable(chain->surface->pLayer);" in added
      and "         wsi_metal_layer_add_presented_handler(" in added and "         wsi_metal_layer_present(&image->drawable);" in added
      and "   const bool vti_stand_in = !wsi_chain->wsi->sw && wsi_metal_vti_is_stand_in(image->drawable);" in added
      and added.count("         wsi_metal_vti_unpresented(image->drawable);   /* madeira-bcd: a stand-in goes back to its ring */") == 2
      and '#include "wsi_common_metal_vti.h"' in added, (added, removed))
queue_c = hunks("src/kosmickrisp/vulkan/kk_queue.c")
q_added = [l[1:] for l in queue_c.split("\n") if l.startswith("+") and not l.startswith("+++")]
check("kk_queue.c: a stand-in is neither waited for nor signalled as a drawable; a drawable is, as before",
      "      if (cmd_buffer->drawable && !stand_in) {" in q_added and "         if (!stand_in)" in q_added
      and "bool wsi_metal_vti_is_stand_in(void *drawable);" in q_added
      and "            mtl_command_queue_signal_drawable(queue->mtl_handle," in q_added, q_added)
check("the build files: the new file and the three frameworks it calls",
      "+  files_vulkan_wsi += files('wsi_common_metal.c', 'wsi_common_metal_layer.m', 'wsi_common_metal_vti.m', 'wsi_common_metal_lsfg.m')"
      in hunks("src/vulkan/wsi/meson.build")
      and "+    modules : ['QuartzCore', 'Metal', 'CoreGraphics', 'CoreVideo', 'CoreMedia', 'VideoToolbox']" in hunks("src/vulkan/meson.build"))
want = vti_m[vti_m.index("static int kk_vti_want(void) {"):]
want = want[:want.index("\n}\n")]
acquire = vti_m[vti_m.index("wsi_metal_vti_acquire(void *mtl4_queue, const void *metal_layer"):]
acquire = acquire[:acquire.index("\n}\n")]
present = vti_m[vti_m.index("wsi_metal_vti_present(void *mtl4_queue, void *drawable_ptr, void (*handler)"):]
present = present[:present.index("\n}\n")]
check("without the key (or Lossless Scaling's): wanted is false, and the acquire gives no stand-in before it looks at anything",
      'kk_vti_parse(getenv("MADEIRA_VT_INTERP"))' in want
      and "   return kk_vti_want() != 0 || wsi_metal_lsfg_wanted();" in vti_m
      and acquire.index("if (!wsi_metal_vti_wanted() || !metal_layer || width < 640 || height < 360) return NULL;") < acquire.index("@autoreleasepool"))
check("a swapchain made before the key (framebufferOnly) or a format the passes do not take gets drawables as before",
      "if (layer.framebufferOnly || !kk_vti_layer_format(fmt, &linear)) return NULL;" in acquire)
check("the game's thread never takes a drawable and never waits for the model: no nextDrawable, waitUntilCompleted or "
      "processWithParameters in the acquire or the present (the acquire waits for a stand-in only, at most a second)",
      all(w not in re.sub(r"/\*.*?\*/", "", part, flags=re.S) for part in (acquire, present)
          for w in ("nextDrawable", "waitUntilCompleted", "processWithParameters"))
      and "pthread_cond_timedwait(&ring->_cv, &ring->_mu, &until)" in acquire and "until.tv_sec += 1;" in acquire)
job = vti_m[vti_m.index("static void kk_vti_job("):]
job = job[:job.index("\n}\n")]
check("the worker alone takes the layer's drawables, one for each picture and one for the game's frame, copied as it is; "
      "never before the frame it scheduled last",
      job.count("[layer nextDrawable]") == 2 and "kk_vti_copy(g, st->_texture, d.texture);" in job
      and job.index("[g presentDrawable:d atTime:at]") < job.index("kk_vti_copy(g, st->_texture, d.texture);")
      and "due = shown ? base + kk_vti_offset(made + 1, made, dt) : base;" in job)
check("the stand-in always goes back: after the frame's copy completed, or at once when no drawable came; a present wait "
      "always gets its call", job.count("kk_vti_slot_free(st);") == 2 and "[d addPresentedHandler:" in job
      and "if (handler) handler(info, present_id);" in job)
check("every read of a stand-in waits for the device's Metal 4 queue (an event it signals after the game's copy into it)",
      "@selector(signalEvent:value:)" in present and "[copy encodeWaitForEvent:event value:wait_value];" in present
      and "if (wait_value) [g encodeWaitForEvent:event value:wait_value];" in job)
present_body = re.sub(r"/\*.*?\*/", "", present, flags=re.S)
drain = vti_m[vti_m.index("static void kk_vti_drain(void) {"):]
drain = drain[:drain.index("\n}\n")]
check("the worker never waits for the GPU: a frame reaches it from its copy's completion handler (one without a copy at once), "
      "in the order of the presents, the head only once it is ready",
      "[copy addCompletedHandler:^(id<MTLCommandBuffer> done) { (void)done; kk_vti_ready(job, 1); }];" in present_body
      and present_body.index("[copy addCompletedHandler:") < present_body.index("[copy commit];")
      and present_body.index("[g_kk_vti_jobs addObject:job];") < present_body.index("[copy commit];")
      and "kk_vti_ready(job, 0);" in present_body and present_body.count("[copy commit];") == 1
      and "atomic_load_explicit(&((KKVTIJob *)g_kk_vti_jobs[0])->_ready, memory_order_acquire)" in drain
      and "[g_kk_vti_jobs removeObjectAtIndex:0];" in drain and "dispatch_async(kk_vti_worker(), ^{ kk_vti_drain(); });" in vti_m
      and "dispatch_async(kk_vti_worker()" not in present_body)
check("the worker's pictures and frames go on a queue of their own (never behind a later frame's copy waiting for the game's)",
      "g_kk_vti_gpu.out = [dev newCommandQueue];" in vti_m and "kk_vti_job(out, st, event, wait_value," in present_body
      and "copy = [queue commandBuffer];" in present_body)
check("six source buffers, a frame written only while at most four are with the worker (three stand-ins keep it to three); "
      "a frame is skipped only when another is ready behind it",
      "#define KK_VTI_RING 6" in vti_m and "atomic_load_explicit(&g_kk_vti.outstanding, memory_order_acquire) <= KK_VTI_RING - 2" in present_body
      and "int backlog = g_kk_vti.backlog;" in job and "g_kk_vti.backlog = behind;" in drain
      and "if (g_kk_vti_last_out) [g_kk_vti_last_out waitUntilCompleted];" in job
      and job.index("if (g_kk_vti_last_out) [g_kk_vti_last_out waitUntilCompleted];") < job.index("made = kk_vti_run(")
      and job.index("made = kk_vti_run(") < job.index("g_kk_vti_last_out = [g retain];"))
check("the stand-ins are resident on the Metal 4 queue (a residency set added to it)",
      "newResidencySetWithDescriptor:" in vti_m and "@selector(addResidencySet:)" in vti_m and "[set requestResidency];" in vti_m)
check("the app's four functions are exported", all(f'__attribute__((visibility("default"))) {sig}' in vti_h for sig in
      ("int madeira_kk_vti_set(int on);", "void madeira_kk_vti_session_ended(void);")) and "madeira_kk_vti_status(" in vti_h and "madeira_kk_vti_why(" in vti_h)
check("BCD's MIT header on both new files of the interpolation (the files of Mesa it joins)",
      all(t.startswith("/*\n * Copyright 2026 BCD (thebcd69)\n *\n * SPDX-License-Identifier: MIT\n */") for t in (vti_m, vti_h)))

with tempfile.TemporaryDirectory(prefix="bcd-kk-vti-") as tmp_name:
    tmp = Path(tmp_name)
    (tmp / "wsi_common_metal_vti.m").write_text(vti_m)
    (tmp / "wsi_common_metal_vti.h").write_text(vti_h)
    (tmp / "wsi_common_metal_lsfg.m").write_text(lsfg_m)
    (tmp / "wsi_common_metal_lsfg.h").write_text(lsfg_h)

    # 2. the plain C rules
    pure = vti_m[vti_m.index("/* kkvti-pure-begin"):vti_m.index("/* kkvti-pure-end */")]
    (tmp / "pure.c").write_text("#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n" + pure + r'''
static int bad;
static void expect(int c, const char *w) { if (!c) { printf("FAIL %s\n", w); bad++; } }
int main(void) {
  char four[8];
  unsigned offered1[2] = { 0x34323076u, 0x42475241u }, offered2[2] = { 0x34323076u, 0x34323066u }, none[1] = { 0x31303276u };
  int lin;
  expect(kk_vti_parse(NULL) == 0 && kk_vti_parse("") == 0 && kk_vti_parse("0") == 0 && kk_vti_parse("on") == 0, "the key: nothing, empty, 0 and words are off");
  expect(kk_vti_parse("1") == 1 && kk_vti_parse("3") == 3 && kk_vti_parse("2") == 0 && kk_vti_parse("-1") == 0, "the key: 1 or 3, nothing else");
  expect(kk_vti_count(1, 1.0 / 30, 120, 0) == 1 && kk_vti_count(3, 1.0 / 30, 120, 0) == 3, "30 FPS on 120 Hz: one, or three");
  expect(kk_vti_count(3, 1.0 / 60, 120, 0) == 1 && kk_vti_count(1, 1.0 / 60, 120, 0) == 1, "60 FPS on 120 Hz: room for one only");
  expect(kk_vti_count(1, 1.0 / 60, 60, 0) == 0 && kk_vti_count(1, 1.0 / 90, 120, 0) == 0, "a game the screen shows at its rate: none");
  expect(kk_vti_count(1, 1.0 / 15, 0, 0) == 1 && kk_vti_count(3, 1.0 / 15, 0, 0) == 3, "an unknown screen counts as 60 Hz");
  expect(kk_vti_count(3, 1.0 / 30, 120, 1) == 0 && kk_vti_count(0, 1.0 / 30, 120, 0) == 0 && kk_vti_count(1, 0.0, 120, 0) == 0, "a busy worker, no key, no frame time: none");
  expect(kk_vti_offset(1, 1, 0.04) == 0.0 && kk_vti_offset(2, 1, 0.04) == 0.02, "one picture: now, then the game's frame half a frame later");
  expect(kk_vti_offset(1, 3, 0.04) == 0.0 && kk_vti_offset(2, 3, 0.04) == 0.01 && kk_vti_offset(4, 3, 0.04) == 0.03 && kk_vti_offset(9, 3, 0.04) == 0.03, "three: a quarter apart, the frame last");
  expect(kk_vti_pick(offered1, 2) == 0x42475241u && kk_vti_pick(offered2, 2) == 0x34323066u && kk_vti_pick(none, 1) == 0, "BGRA first, then 420f, then 420v");
  expect(kk_vti_layer_format(80, &lin) && !lin && kk_vti_layer_format(81, &lin) && lin && kk_vti_layer_format(70, &lin) && kk_vti_layer_format(71, &lin) && lin
         && !kk_vti_layer_format(115, &lin), "8-bit BGRA and RGBA, plain and sRGB; nothing else");
  expect(!strcmp(kk_vti_fourcc(0x34323076u, four), "420v"), "four letters for the log");
  printf("%d\n", bad);
  return bad != 0;
}
''')
    cc = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(tmp / "pure"), str(tmp / "pure.c")], capture_output=True, text=True)
    run = subprocess.run([str(tmp / "pure")], capture_output=True, text=True) if cc.returncode == 0 else None
    check("the plain rules, compiled on the host (12 groups)", run is not None and run.returncode == 0 and run.stdout.strip() == "0",
          cc.stderr if run is None else run.stdout)

    # 3. iOS 26
    have_ios = shutil.which("xcrun") and subprocess.run(["xcrun", "-sdk", "iphoneos", "--show-sdk-path"], capture_output=True).returncode == 0
    if have_ios:
        r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", "-arch", "arm64", "-miphoneos-version-min=26.0", "-fno-objc-arc",
                            "-fvisibility=hidden", "-Wall", "-Wextra", "-Wno-unused-parameter", "-Werror", "-c", str(tmp / "wsi_common_metal_vti.m"),
                            "-o", str(tmp / "ios.o")], capture_output=True, text=True)
        check("it compiles for iOS 26 arm64 with -Wall -Wextra -Werror (no ARC, hidden by default, as Mesa builds it)", r.returncode == 0, r.stderr)
        if r.returncode == 0:
            syms = subprocess.run(["xcrun", "nm", "-gU", str(tmp / "ios.o")], capture_output=True, text=True).stdout
            exported = subprocess.run(["xcrun", "nm", "-gUm", str(tmp / "ios.o")], capture_output=True, text=True).stdout
            check("...the app's four functions are visible outside the library, the WSI's two stay private to it",
                  all(re.search(rf"\(__TEXT,__text\) external _{n}\b", exported) for n in
                      ("madeira_kk_vti_set", "madeira_kk_vti_status", "madeira_kk_vti_why", "madeira_kk_vti_session_ended"))
                  and re.search(r"private external _wsi_metal_vti_present\b", exported) is not None, exported)
    else:
        skip("no iPhone SDK here: the iOS compile")

    # 4. a real run on this Mac
    harness = R / "tests/host/kk_vt_interp_harness.m"
    mac = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip() if shutil.which("sw_vers") else ""
    if not mac or int(mac.split(".")[0]) < 26:
        skip(f"the run needs macOS 26 or later (this is {mac or 'not macOS'})")
    else:
        b = [subprocess.run(["xcrun", "clang", "-fno-objc-arc", "-mmacosx-version-min=26.0", "-c", str(tmp / "wsi_common_metal_vti.m"), "-o", str(tmp / "vti.o")], capture_output=True, text=True),
             subprocess.run(["xcrun", "clang", "-fno-objc-arc", "-mmacosx-version-min=26.0", "-c", str(tmp / "wsi_common_metal_lsfg.m"), "-o", str(tmp / "lsfg.o")], capture_output=True, text=True),
             subprocess.run(["xcrun", "clang", "-fobjc-arc", "-mmacosx-version-min=26.0", "-c", str(harness), "-o", str(tmp / "h.o")], capture_output=True, text=True),
             subprocess.run(["xcrun", "clang", str(tmp / "vti.o"), str(tmp / "lsfg.o"), str(tmp / "h.o"), "-framework", "Metal", "-framework", "QuartzCore", "-framework", "CoreVideo",
                             "-framework", "CoreMedia", "-framework", "VideoToolbox", "-framework", "Foundation", "-o", str(tmp / "kkh")], capture_output=True, text=True)]
        check("the module and the harness build for this Mac", all(x.returncode == 0 for x in b), "\n".join(x.stderr for x in b))

        def harness_run(env, *args):
            e = {k: v for k, v in os.environ.items() if not k.startswith("MADEIRA_")}
            e.update(env)
            r = subprocess.run([str(tmp / "kkh"), *map(str, args)], capture_output=True, text=True, env=e, timeout=180)
            kv = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l and " " not in l.split("=", 1)[0])
            return r, kv

        if all(x.returncode == 0 for x in b):
            r0, k0 = harness_run({}, 30, 20, -1, 2)
            check("without MADEIRA_VT_INTERP: not wanted, no stand-in, every frame a drawable as before, nothing in the log, no frame made counted",
                  k0.get("wanted") == "0" and k0.get("stand_ins") == "0" and k0.get("drawables") == "30" and "[vt-interp]" not in r0.stderr
                  and k0.get("made_pushed") == "0", r0.stdout + r0.stderr)
            r1, k1 = harness_run({"MADEIRA_VT_INTERP": "1", "MADEIRA_VT_PANEL_HZ": "120", "MADEIRA_KK_VTI_SELFTEST": "1"}, 100, 33, -1, 2)
            if k1.get("mtl4") != "1":
                skip("this Mac has no Metal 4 queue")
            elif k1.get("final_status") == "4":
                skip("VideoToolbox's processor says no here: " + k1.get("why", ""))
            else:
                check("one picture: stand-ins, the session loads, pictures are made, with no error and no missing drawable",
                      k1.get("wanted") == "1" and k1.get("stand_ins") == "100" and k1.get("made_seen") == "1"
                      and float(k1.get("generated_fps_max", "0")) > 5 and "session ready" in r1.stderr and "errors 0" in r1.stderr
                      and "no drawable 0," in r1.stderr and "made no picture" not in r1.stderr, r1.stdout + r1.stderr[-1500:])
                rf, kf = harness_run({"MADEIRA_VT_INTERP": "1", "MADEIRA_VT_PANEL_HZ": "120"}, 150, 12, -1, 2)
                check(f"a fast game holding two frames (as on the device where 703 stalled at 3.6 FPS) keeps its pace: "
                      f"{kf.get('loop_fps')} FPS with pictures, slowest acquire {kf.get('slowest_acquire_ms')} ms, every present wait answered",
                      kf.get("timeouts") == "0" and float(kf.get("loop_fps", "0")) >= 35 and float(kf.get("slowest_acquire_ms", "999")) < 40
                      and kf.get("presented_waits") == kf.get("presents") and float(kf.get("generated_fps_max", "0")) > 20
                      and "no drawable 0," in rf.stderr, rf.stdout + rf.stderr[-800:])

                def pictures(text):
                    out = []
                    for m in re.finditer(r"selftest: picture (\d)/(\d) bar at (-?[\d.]+) between (-?[\d.]+) and (-?[\d.]+), bar (\d+), background (\d+)", text):
                        i, n, c, p, q, hi, lo = int(m[1]), int(m[2]), float(m[3]), float(m[4]), float(m[5]), int(m[6]), int(m[7])
                        if p >= 0 and q > p and q - p < 40:   # neighbours; the bar's wrap at the right edge is no pair to judge
                            out.append((i, n, c, p + (q - p) * i / (n + 1), hi, lo))
                    return out
                shown1 = len(re.findall(r"selftest: picture \d/\d bar at", r1.stderr))
                check(f"build 707: every picture shown goes to the app's ring of frames made, found in the process ({k1.get('made_pushed')} of {shown1})",
                      shown1 > 20 and k1.get("made_pushed") == str(shown1), k1)
                one = pictures(r1.stderr)
                check(f"one picture: each lies halfway between its frames, white on black ({len(one)} pictures)",
                      len(one) >= 20 and all(abs(c - want) <= 3 and hi >= 230 and lo <= 24 for _, _, c, want, hi, lo in one),
                      [x for x in one if not (abs(x[2] - x[3]) <= 3 and x[4] >= 230 and x[5] <= 24)][:8])
                r3, k3 = harness_run({"MADEIRA_VT_INTERP": "3", "MADEIRA_VT_PANEL_HZ": "120", "MADEIRA_KK_VTI_SELFTEST": "1"}, 60, 50, -1, 2)
                three = pictures(r3.stderr)
                check(f"three pictures: at a quarter, a half and three quarters, white on black ({len(three)} pictures)",
                      len(three) >= 30 and {i for i, *_ in three} == {1, 2, 3}
                      and all(abs(c - want) <= 3 and hi >= 230 and lo <= 24 for _, _, c, want, hi, lo in three),
                      [x for x in three if not (abs(x[2] - x[3]) <= 3)][:8])
                r4, k4 = harness_run({"MADEIRA_VT_INTERP": "1", "MADEIRA_VT_PANEL_HZ": "120"}, 90, 20, 45, 2)
                check("the menu's switch off: it answers off, the status says off, the frames go on at their pace without pictures",
                      k4.get("switch_off_returns") == "0" and "status_at_45=0" in r4.stdout and k4.get("timeouts") == "0"
                      and float(k4.get("loop_fps", "0")) >= 25 and "live switch: OFF" in r4.stderr, r4.stdout + r4.stderr[-800:])
                check("the end of the session: off, the model given back", k1.get("after_end_status") == "0" and "session for 1280x720 closed" in r1.stderr)
                rl, kl = harness_run({"MADEIRA_VT_INTERP": "1", "MADEIRA_VT_PANEL_HZ": "120", "MADEIRA_KK_VTI_SELFTEST": "1"}, 100, 50, -1, 1, 150)
                late = pictures(rl.stderr)
                gen, game = float(kl.get("generated_fps_last", "0")), float(kl.get("game_fps_last", "0"))
                check(f"a game 150 ms behind on the GPU: a picture for every frame ({gen} a second for a game at {game} FPS), "
                      f"{len(late)} of them halfway between their frames, the game at {kl.get('loop_fps')} FPS",
                      game >= 12 and gen >= 0.85 * game and float(kl.get("loop_fps", "0")) >= 14 and kl.get("timeouts") == "0"
                      and kl.get("presented_waits") == kl.get("presents") and len(late) >= 60
                      and all(abs(c - want) <= 3 and hi >= 230 and lo <= 24 for _, _, c, want, hi, lo in late)
                      and re.search(r"[1-9]\d\d\.\d ms from a present to its frame done on the GPU", rl.stderr) is not None
                      and "no drawable 0," in rl.stderr and "errors 0" in rl.stderr, rl.stdout + rl.stderr[-1200:])
                ro, ko = harness_run({"MADEIRA_VT_INTERP": "1", "MADEIRA_VT_PANEL_HZ": "120"}, 120, 50, 60, 1, 150)
                check("...the switch off there: the frames go on at their pace without pictures, every present wait answered",
                      ko.get("switch_off_returns") == "0" and "status_at_60=0" in ro.stdout and ko.get("timeouts") == "0"
                      and float(ko.get("loop_fps", "0")) >= 14 and ko.get("generated_fps_last") == "0.0"
                      and ko.get("presented_waits") == ko.get("presents"), ro.stdout + ro.stderr[-800:])
                r5, k5 = harness_run({"MADEIRA_VT_INTERP": "1", "MADEIRA_VT_PANEL_HZ": "60"}, 60, 8, -1, 2)
                check("a game faster than half the screen's rate gets no pictures", k5.get("made_seen") == "0" and k5.get("generated_fps_max") == "0.0",
                      r5.stdout + r5.stderr[-600:])

# 5. wiring
kk = (R / "tests/host/check-kosmickrisp.py").read_text()
check("the series check knows 0036", "0036-wsi-metal-apple-frame-interpolation-MADEIRA_VT_INTERP.patch" in kk and "range(1, 37)" in kk)
readme = (series / "README.md").read_text()
check("the series' README says what 0036 is, stand-ins included", "0036-wsi-metal-apple-frame-interpolation-MADEIRA_VT_INTERP.patch" in readme
      and "stand-in" in readme)
plain = (R / "app/Madeira/BCDVTInterp.swift").read_text()
live = (R / "app/Madeira/BCDVTInterpLive.swift").read_text()
check("the app: a KosmicKrisp game starts it without the live present path",
      "guard presentFX || kosmickrisp else" in plain and "kosmickrisp: kosmickrisp)" in live
      and "MadeiraConfig.gameValue(BCDVTInterp.vulkanKey) == BCDVTInterp.kosmickrispValue" in live)
check("the app: status, switch and the session's end also reach KosmicKrisp's copy, found in the process",
      'kosmickrisp("madeira_kk_vti_status", KKStatus.self)' in live and 'kosmickrisp("madeira_kk_vti_set", KKSet.self)' in live
      and 'kosmickrisp("madeira_kk_vti_why", KKWhy.self)' in live and 'kosmickrisp("madeira_kk_vti_session_ended", KKEnded.self)?()' in live
      and "dlsym(UnsafeMutableRawPointer(bitPattern: -2), name)" in live)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check before KosmicKrisp is built", "python3 tests/host/check-kk-vt-interp.py" in wf
      and wf.index("python3 tests/host/check-kk-vt-interp.py") < wf.index("run: bash build/mesa-ios/build-kosmickrisp-bcd.sh"))
doc = (R / "docs/VULKAN.md").read_text()
check("docs/VULKAN.md has the section", "Apple frame interpolation" in doc and "0036" in doc)

print("PASS: Apple frame interpolation in KosmicKrisp" if ok else "FAIL: Apple frame interpolation in KosmicKrisp")
sys.exit(0 if ok else 1)
