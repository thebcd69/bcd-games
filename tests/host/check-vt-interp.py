#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The VideoToolbox frame interpolation experiment (tools/patch-winemetal-vt-interp.py, the app side). No iPhone, no GPU, no model.

1. The workflow runs the script after the Lossless Scaling pacing patch (it builds on present-fx), and this check.
2. The script applies to an unpatched tree behind the whole DXMT patch chain and to a tree a build already patched, is idempotent,
   and changes the file by its own hunks and nothing else.
3. Off path, on the source: both hooks in present-fx's code start with madeira_vti_want(), which reads MADEIRA_VT_INTERP once and is
   0 without it, without MADEIRA_PRESENT_FX or below iOS 26; the three changed lines of madeira_pfx_next read as before while the
   worker has no frame; the game's thread never waits for the model or the GPU; the locks are only tried; the probe is called by
   nothing in the engine.
4. The plain C functions on the host: the key, how many pictures a frame gets, when they are shown, which pixel format is taken.
5. Behaviour (tests/host/vt_interp_harness.m around the script's block, on top of present-fx's harness, with stand-ins for
   VideoToolbox and Metal and real pixel buffers): without the key present-fx's frame is what it was, to the letter; with it the
   session loads in the background, frames go to the worker in order, pictures are made only when the screen can show them and the
   worker is free; limits, a slow model, errors, a size change, the menu's switch, a layer that goes away; 4:2:0 and BGRA; and the
   device probe from end to end (it finds its own test picture's bar in what the stand-in model made).
6. The patched file compiles for iOS (arm64) without a warning in the new code, and the two shader sources compile with the Metal
   compiler when it is there.
7. The app: the game's line, what a session's start does with it, the menu's rows and the test's words
   (tests/host/bcd_vt_interp_checks.swift); the wiring in the app, the catalog and the project.
SKIP (exit 0) without the dxmt sources.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import winemetal_fx_kit as kit   # noqa: E402

if not kit.have_sources():
    print(f"SKIP: no dxmt sources at {kit.SRC} (check out the submodule or set DXMT_SRC_DIR)")
    sys.exit(0)
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)
    return cond


SCRIPT = "tools/patch-winemetal-vt-interp.py"
APP = R / "app/Madeira"
wf = (R / ".github/workflows/build-ipa.yml").read_text()
chain = kit.chain()
check("the workflow runs the script after the Lossless Scaling pacing patch, and this check",
      SCRIPT in chain and chain.index(SCRIPT) > chain.index("tools/patch-winemetal-lsfg-pacing.py") and "run: python3 tests/host/check-vt-interp.py" in wf)
check("the kit knows the script as the last of its family (the checks of the scripts before it undo its hunks in a patched tree)", kit.FAMILY[-1] == SCRIPT)
if SCRIPT not in chain:
    print("FAILED: vt-interp")
    sys.exit(1)

stage = kit.Stage(SCRIPT)
mod = stage.mod
body = kit.body
for problem in stage.problems:
    print("  " + problem)
check("the DXMT patch chain before the script applies on the copy (or the copy came patched)", not stage.problems)
check("the script applies (or finds its own work in a tree a build already patched)", stage.first.returncode == 0 and mod.MARKER in stage.after)
check("a second run changes nothing", stage.idempotent and "already patched" in stage.second.stdout)
if stage.first.returncode:
    print(stage.first.stdout + stage.first.stderr)
    print("FAILED: vt-interp")
    sys.exit(1)
check("the script changes the file by its own hunks and nothing else (undoing them gives the file before back, byte for byte)", stage.exact())
bare = stage.before
for earlier in ("tools/patch-winemetal-lsfg-pacing.py", "tools/patch-winemetal-present-fx.py", "tools/patch-winemetal-framegen-live.py"):
    bare = kit.undo(bare, earlier) if bare is not None else None
if check("the file before the script, without the three patches it builds on, for the two cases below", bare is not None):
    rc, said, untouched = kit.older_tree(SCRIPT, bare)
    check("an older dxmt checkout (which those patches leave alone) is left as it is too, with a line that says so, and is no error",
          rc == 0 and untouched and "is left out" in said and "::error::" not in said)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / kit.REL).parent.mkdir(parents=True)
        (Path(tmp) / kit.REL).write_text(bare)
        p = subprocess.run([sys.executable, str(R / SCRIPT)], cwd=tmp, capture_output=True, text=True)
        check("on the pin's tree without them it is an error by name (the order of the patch steps), and nothing is written",
              p.returncode == 1 and "have to run first" in p.stdout and (Path(tmp) / kit.REL).read_text() == bare)
src = stage.after

# ---- 3. off path, on the source
want = body(src, "static int madeira_vti_want(void) {")
check("madeira_vti_want: the key is read once; 0 without it, below iOS 26, in the simulator, or without MADEIRA_PRESENT_FX",
      want.index("if (w >= 0) return w;") < want.index('madeira_vti_parse(getenv("MADEIRA_VT_INTERP"))') and "if (MADEIRA_VTI_AVAILABLE) ok = MADEIRA_VTI_BUILT;" in want
      and "} else if (!madeira_fgl_mode()) {" in want and want.count("w = 0;") == 2 and src.count('getenv("MADEIRA_VT_INTERP")') == 1
      and "#define MADEIRA_VTI_AVAILABLE @available(iOS 26.0, macOS 26.0, *)" in src)
_ended = body(src, "void madeira_vti_session_ended(void) {")
check("a session's end clears the key's reading, also for a session that never asked for it, and the app always calls it",
      "  }\n  atomic_store_explicit(&g_vti.want, -1, memory_order_relaxed);   /* the next session reads its own MADEIRA_VT_INTERP */" in _ended
      and "if (atomic_load_explicit(&g_vti.want, memory_order_relaxed) > 0) {" in _ended
      and "        madeira_vti_session_ended()   // its reading of MADEIRA_VT_INTERP goes too" in (APP / "BCDVTInterpLive.swift").read_text())
nxt = body(src, "static int madeira_pfx_next(CAMetalLayer *layer, id *out) {")
check("madeira_pfx_next: the experiment is asked only with the key, and without a frame at its worker the three lines decide as before",
      "int vti_busy = madeira_vti_want() ? madeira_vti_poll(e) : 0;" in nxt and nxt.count("vti_busy") == 4
      and "if (pthread_mutex_trylock(&g_pfx_mu) && !(vti_busy && madeira_pfx_lock())) return (enlarged || vti_busy) ? -1 : 0;" in nxt
      and "if (!tex) return (enlarged || vti_busy) ? -1 : 0;" in nxt)
after = body(src, "static void madeira_pfx_after_commit(id<MTLCommandBuffer> cb) {")
check("madeira_pfx_after_commit: a frame goes to the worker only with the key, before anything of the present is done here",
      "if (madeira_vti_want() && madeira_vti_take(cb, px)) {" in after
      and after.index("madeira_vti_take(cb, px)") < after.index("id<MTLCommandBuffer> out = [cb.commandQueue commandBuffer];"))
block = src[src.index("/* madeira-bcd: vt-interp (tools/patch-winemetal-vt-interp.py). An experiment, inert unless"):src.index("static NTSTATUS\n_MTLCommandBuffer_presentDrawable(void *obj) {")]
outside = src.replace(block, "")
check("outside its own block the file asks the experiment only through the two hooks (and their declarations)",
      outside.count("madeira_vti_") == 7 and outside.count("madeira_vti_want()") == 2 and outside.count("madeira_vti_take(cb, px)") == 1
      and outside.count("madeira_vti_poll(e)") == 1 and "g_vti" not in outside)
take = body(src, "static int madeira_vti_take(id<MTLCommandBuffer> cb, MadeiraPFXDrawable *px) {")
job = body(src, "static void madeira_vti_job(")
kick = body(src, "static void madeira_vti_kick(")
check("the game's thread never waits for the GPU or for VideoToolbox: the copy is waited for, and the model run, on the worker",
      "waitUntilCompleted" not in take and "processWithParameters" not in take and "madeira_vti_open(" not in take
      and "[copy waitUntilCompleted];" in job and block.count("waitUntilCompleted") == 1 and block.count("processWithParameters:params error:&err]") == 2
      and "dispatch_async(madeira_vti_queue()" in take and "dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0)" in kick and "madeira_vti_open(" in kick)
check("its lock is only ever tried, never waited on without a bound", "pthread_mutex_lock(&g_vti_mu)" not in src and src.count("pthread_mutex_trylock(&g_vti_mu)") == 1)
check("a frame is handed over in order: while the worker has frames, every frame of the followed layer goes to it",
      take.index("busy = atomic_load_explicit(&g_vti.outstanding") < take.index("if (!busy && !gen) return 0;")
      and "if (busy || w < 640 || h < 360 ||" in take)
check("three buffers go round, and a third frame while two are with the worker gets none (it would be written over one still read)",
      "#define MADEIRA_VTI_RING 3" in src and "atomic_load_explicit(&g_vti.outstanding, memory_order_acquire) <= 1) {" in take
      and "src = (s->g_last + 1) % MADEIRA_VTI_RING;" in take)
check("the present call's start stays on the game's thread (pause while inactive, the governor's cap, the frame boundary), and a limit holds the game's frames there",
      take.index('madeira_app_wait_active("present");') < take.index("ios_fps_cap_dxmt_pace()") < take.index("ios_frame_encode_present(0)")
      and "if (capped && (s || madeira_present_limiter_enabled()))\n    ios_frame_limiter(madeira_present_limit_to(period));" in take)
check("each picture goes out from a command buffer of its own, committed before the next drawable is taken; the layer is held for the job and released on the main thread",
      job.index("madeira_vti_encode_out(g, s, i);") < job.index("d = [layer nextDrawable];") < job.index("madeira_pfx_commit(g);")
      < job.index("madeira_pfx_resolve(real, px)") and "dispatch_async(dispatch_get_main_queue(), ^{ [layer release]; });" in job
      and "objc_loadWeak(&e->weak)" in job)
check("the probe is called by nothing in the engine (the app's button only)", src.count("madeira_vti_probe(") == 1 and "int madeira_vti_probe(char *out, int cap) {" in src)
check("VideoToolbox is told what the numbers mean (sRGB, BT.709), and the processor's own buffer attributes are used",
      "kCVImageBufferTransferFunction_sRGB" in src and "kCVImageBufferYCbCrMatrix_ITU_R_709_2" in src
      and "config.sourcePixelBufferAttributes" in src and "config.destinationPixelBufferAttributes" in src and "CVPixelBufferCreateResolvedAttributesDictionary" in src)

with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    # ---- 4. the plain functions
    if not cc:
        print("SKIP: no C compiler for the behaviour parts")
    else:
        pure = src[src.index("/* vti-pure-begin"):src.index("/* vti-pure-end */")]
        (t / "pure.c").write_text(r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
''' + pure + r'''
static int fails;
static void expect(int c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
static int near(double a, double b) { return fabs(a - b) < 1e-9; }
int main(void) {
  unsigned f1[3] = { MADEIRA_VTI_420V, MADEIRA_VTI_BGRA, MADEIRA_VTI_420F }, f2[2] = { MADEIRA_VTI_420V, MADEIRA_VTI_420F }, f3[1] = { 0x78343230u };
  int linear = 7;
  char four[8];
  expect(madeira_vti_parse(NULL) == 0 && madeira_vti_parse("") == 0 && madeira_vti_parse("0") == 0 && madeira_vti_parse("on") == 0, "the key: nothing, empty, 0 and words are off");
  expect(madeira_vti_parse("1") == 1 && madeira_vti_parse("3") == 3 && madeira_vti_parse("2") == 0 && madeira_vti_parse("4") == 0 && madeira_vti_parse("-1") == 0, "the key: 1 or 3 pictures, nothing else");
  expect(madeira_vti_count(1, 1.0 / 30, 120, 0) == 1 && madeira_vti_count(1, 1.0 / 60, 120, 0) == 1 && madeira_vti_count(1, 1.0 / 62, 120, 0) == 1, "one picture: a game at 30, 60 or 62 on a 120 Hz screen gets it");
  expect(madeira_vti_count(1, 1.0 / 70, 120, 0) == 0 && madeira_vti_count(1, 1.0 / 120, 120, 0) == 0 && madeira_vti_count(1, 1.0 / 60, 60, 0) == 0, "a game at 70 or 120 on 120 Hz, or at 60 on 60 Hz: none (the screen cannot show twice as many)");
  expect(madeira_vti_count(1, 1.0 / 30, 60, 0) == 1 && madeira_vti_count(1, 1.0 / 40, 60, 0) == 0, "a 60 Hz screen: a game at 30 gets one, at 40 none");
  expect(madeira_vti_count(3, 1.0 / 30, 120, 0) == 3 && madeira_vti_count(3, 1.0 / 31, 120, 0) == 3 && madeira_vti_count(3, 1.0 / 20, 120, 0) == 3, "three pictures: a game at 20, 30 or 31 on 120 Hz gets all three");
  expect(madeira_vti_count(3, 1.0 / 40, 120, 0) == 1 && madeira_vti_count(3, 1.0 / 60, 120, 0) == 1 && madeira_vti_count(3, 1.0 / 30, 60, 0) == 1, "three asked for, but only room for one more (40 or 60 on 120 Hz, 30 on 60 Hz): the middle one");
  expect(madeira_vti_count(3, 1.0 / 90, 120, 0) == 0, "three asked for at 90 on 120 Hz: none");
  expect(madeira_vti_count(1, 1.0 / 30, 120, 1) == 0 && madeira_vti_count(3, 1.0 / 30, 120, 2) == 0, "a frame that finds another waiting at the worker gets none (the worker catches up first)");
  expect(madeira_vti_count(1, 1.0 / 30, 0, 0) == 1 && madeira_vti_count(1, 1.0 / 60, 0, 0) == 0, "a screen whose rate is not known counts as 60 Hz");
  expect(madeira_vti_count(0, 1.0 / 30, 120, 0) == 0 && madeira_vti_count(1, 0.0, 120, 0) == 0 && madeira_vti_count(1, -1.0, 120, 0) == 0, "nothing asked for, or no frame time yet: none");
  expect(near(madeira_vti_offset(1, 1, 0.032), 0.0) && near(madeira_vti_offset(2, 1, 0.032), 0.016), "one picture: it goes out first, the game's frame half a frame later");
  expect(near(madeira_vti_offset(1, 3, 0.032), 0.0) && near(madeira_vti_offset(2, 3, 0.032), 0.008) && near(madeira_vti_offset(3, 3, 0.032), 0.016)
         && near(madeira_vti_offset(4, 3, 0.032), 0.024), "three pictures: a quarter of a frame apart, the game's frame last");
  expect(near(madeira_vti_offset(9, 3, 0.032), 0.024) && near(madeira_vti_offset(1, 0, 0.032), 0.0) && near(madeira_vti_offset(0, 3, 0.032), 0.0), "numbers out of range do no harm");
  expect(madeira_vti_pick(f1, 3) == MADEIRA_VTI_BGRA && madeira_vti_pick(f2, 2) == MADEIRA_VTI_420F && madeira_vti_pick(f2, 1) == MADEIRA_VTI_420V
         && madeira_vti_pick(f3, 1) == 0 && madeira_vti_pick(f1, 0) == 0, "the pixel format: BGRA before 4:2:0 full range before video range; none of them is none");
  expect(madeira_vti_layer_format(80, &linear) && linear == 0 && madeira_vti_layer_format(81, &linear) && linear == 1
         && madeira_vti_layer_format(70, &linear) && linear == 0 && madeira_vti_layer_format(71, &linear) && linear == 1, "the four 8-bit layer formats are handled; the sRGB ones read as linear");
  expect(!madeira_vti_layer_format(115, &linear) && !madeira_vti_layer_format(94, &linear) && !madeira_vti_layer_format(0, &linear), "16-bit float, 10-bit and nothing are not");
  expect(!strcmp(madeira_vti_fourcc(MADEIRA_VTI_BGRA, four), "BGRA") && !strcmp(madeira_vti_fourcc(MADEIRA_VTI_420F, four), "420f")
         && !strcmp(madeira_vti_fourcc(MADEIRA_VTI_420V, four), "420v") && !strcmp(madeira_vti_fourcc(7, four), "????"), "a pixel format's four letters; bytes that are no letters read as ?");
  expect(madeira_vti_bar_x(0, 640, 40, 20) == 20 && madeira_vti_bar_x(10, 640, 40, 20) == 220 && madeira_vti_bar_x(27, 640, 40, 20) == 560
         && madeira_vti_bar_x(28, 640, 40, 20) == 20 && madeira_vti_bar_x(3, 8, 40, 20) == 20, "the test picture's bar moves a step a frame, wraps before the edge, and survives a tiny picture");
  return fails ? 1 : 0;
}
''')
        b = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-o", str(t / "pure"), str(t / "pure.c")], capture_output=True, text=True)
        if check("the plain functions compile on the host", b.returncode == 0):
            r = subprocess.run([str(t / "pure")], capture_output=True, text=True)
            print(r.stdout.rstrip())
            check("the key, the number of pictures, their times, the pixel format", r.returncode == 0)
        else:
            print(b.stderr[-2000:])

    # ---- 5. behaviour, with stand-ins (Objective-C on a Mac)
    if sys.platform != "darwin" or not cc:
        print("SKIP: the behaviour harness is Objective-C and needs a Mac")
    else:
        pfx = kit.load("tools/patch-winemetal-present-fx.py")
        h = (R / "tests/host/present_fx_harness.m").read_text()
        parts = {
            "EARLY": pfx.EARLY, "BLOCK_E": pfx.BLOCK_E, "BLOCK_L": pfx.BLOCK_L, "COMMIT": pfx.COMMIT,
            "PRESENT_TOP": pfx.PRESENT_TOP[len(pfx.A_PRESENT_TOP):], "PRESENT_FG": pfx.PRESENT_FG[len(pfx.A_PRESENT_FG):],
            "PRESENT_AFTER": pfx.PRESENT_AFTER, "NEXT": pfx.NEXT, "SETPROPS": pfx.SETPROPS, "GETPROPS": pfx.GETPROPS,
            "CTL": pfx.CTL[:-len(pfx.A_CTL)],
        }
        for name, text in parts.items():
            assert h.count(f"/*@{name}@*/") == 1, name
            h = h.replace(f"/*@{name}@*/", text)
        h = h[:h.index("int main(int argc, char **argv) {")]
        hooks_ok = True
        for name, anchor, new in mod.PAIRS[:-1]:   # the hook lines, into present-fx's code; the block itself goes in below
            hooks_ok &= h.count(anchor) == 1
            h = h.replace(anchor, new, 1)
        check("the script's hook lines find present-fx's code in the harness as they do in winemetal_unix.c", hooks_ok and mod.PAIRS[-1][2] == mod.BLOCK)
        mine = (R / "tests/host/vt_interp_harness.m").read_text()
        prelude = mine[mine.index("//@@PRELUDE"):mine.index("//@@AFTER")]
        after_block = mine[mine.index("//@@AFTER"):mine.index("//@@TESTS")]
        tests = mine[mine.index("//@@TESTS"):]
        spot = "// The four thunks, cut down to the old behaviour plus the script's own hook lines.\n"
        assert h.count(spot) == 1
        h = h.replace(spot, prelude + mod.BLOCK[len(mod.A_BLOCK):] + after_block + spot)
        # present-fx's stand-ins, for two threads and for passes: where a pass draws to, command buffers numbered atomically, and
        # the recorded operations printed with a failing check
        for a, b in (
            ('- (void)setFragmentTexture:(id)t atIndex:(NSUInteger)i { (void)i; op(@"%@: bilinear from %@", self.cb.name, texname(t)); }',
             '- (void)setFragmentTexture:(id)t atIndex:(NSUInteger)i { if (self.target) op(@"%@: pass %@ -> %@", self.cb.name, texname(t), texname(self.target)); '
             'else op(@"%@: bilinear from %@", self.cb.name, texname(t)); }'),
            ('- (id)renderCommandEncoderWithDescriptor:(id)d { (void)d; return [self enc]; }',
             '- (id)renderCommandEncoderWithDescriptor:(id)d { FakeEncoder *e = [self enc]; id t = [[[d colorAttachments] objectAtIndexedSubscript:0] texture]; '
             'e.target = g_vti_passes ? t : nil; return e; }'),
            ('@property (nonatomic, assign) FakeCommandBuffer *cb;\n@end',
             '@property (nonatomic, assign) FakeCommandBuffer *cb;\n@property (nonatomic, assign) id target;\n@end\nstatic int g_vti_passes;'),
            ('cb.name = [NSString stringWithFormat:@"own%d", ++g_cb_number];', 'cb.name = [NSString stringWithFormat:@"own%d", __sync_add_and_fetch(&g_cb_number, 1)];'),
            (r'static void expect(int ok, const char *what) { printf("%s %s\n", ok ? "ok  " : "FAIL", what); if (!ok) g_fails++; }',
             'static NSString *ops_text(void);\n'
             r'static void expect(int ok, const char *what) { printf("%s %s\n", ok ? "ok  " : "FAIL", what); if (!ok) { g_fails++; printf("     ops: %s\n", ops_text().UTF8String); } }'),
        ):
            assert h.count(a) == 1, a[:60]
            h = h.replace(a, b)
        (t / "h.m").write_text(h + tests)
        flags = ["-fno-objc-arc", "-fblocks", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-objc-method-access",
                 "-Wno-incompatible-pointer-types", "-Wno-deprecated-declarations", "-Wno-objc-protocol-method-implementation",
                 "-Wno-unguarded-availability-new", "-DMADEIRA_VTI_AVAILABLE=1",
                 "-framework", "Foundation", "-framework", "QuartzCore", "-framework", "Metal", "-framework", "MetalFX", "-framework", "CoreGraphics",
                 "-framework", "CoreVideo", "-framework", "CoreMedia", "-framework", "IOSurface", "-framework", "VideoToolbox"]
        b = subprocess.run([cc, "-x", "objective-c", str(t / "h.m"), "-o", str(t / "h")] + flags, capture_output=True, text=True)
        if check("the script's block and hook lines compile on the host with the stand-ins", b.returncode == 0):
            for phase, what in ((0, "the key is not set"), (1, "the key without present-fx"), (2, "one picture: the whole life of a session"),
                                (3, "three pictures, 4:2:0 buffers, an sRGB layer, MetalFX upscaling"), (4, "what VideoToolbox or the picture do not allow"),
                                (5, "the device probe")):
                r = subprocess.run([str(t / "h"), str(phase)], capture_output=True, text=True, timeout=300)
                print("\n".join(l for l in r.stdout.splitlines() if l.startswith(("ok  ", "FAIL", "     "))))
                if r.returncode:
                    print(r.stderr[-1500:])
                check(f"harness phase {phase} ({what})", r.returncode == 0 and "ok  " in r.stdout and "FAIL" not in r.stdout)
        else:
            print("\n".join(l for l in b.stderr.splitlines() if "error" in l or "Undefined" in l)[:4000])

    # ---- 6. compiles for iOS; the shaders compile
    ran, ok_c, err = stage.compile_ios()
    if not ran:
        print("SKIP: no iOS SDK (xcrun) or no dxmt/include for the compile part")
    else:
        if not check("the patched winemetal_unix.c compiles for iOS arm64", ok_c):
            print("\n".join(l for l in err.splitlines() if "error" in l)[:3000])
        check("no compiler warning names the new code (availability included: every VideoToolbox call is inside its iOS 26 test)",
              not any(("vti" in l or "VTI" in l or "VTFrame" in l or "VTLowLatency" in l or "CVPixelBuffer" in l) for l in err.splitlines() if "warning" in l))
    metal = subprocess.run(["xcrun", "-sdk", "iphoneos", "-f", "metal"], capture_output=True, text=True) if shutil.which("xcrun") else None
    if not metal or metal.returncode:
        print("SKIP: no Metal compiler (xcrun metal) for the shader part")
    else:
        for name in ("g_vti_msl", "g_pfx_msl"):
            a = src.index(f"static const char *{name} =")
            text = re.sub(r"/\*.*?\*/", "", src[a:src.index(";\n", a)])
            shader = "".join(bytes(m, "utf-8").decode("unicode_escape") for m in re.findall(r'"((?:[^"\\]|\\.)*)"', text))
            (t / f"{name}.metal").write_text(shader)
            c = subprocess.run(["xcrun", "-sdk", "iphoneos", "metal", "-c", str(t / f"{name}.metal"), "-o", str(t / f"{name}.air")], capture_output=True, text=True)
            if not check(f"the shader source {name} compiles with the Metal compiler (iOS), without a warning", c.returncode == 0 and "warning" not in c.stderr):
                print(c.stderr[-2000:])
stage.close()

# ---- 7. the app
plain = (APP / "BCDVTInterp.swift").read_text()
live = (APP / "BCDVTInterpLive.swift").read_text()
content = (APP / "ContentView.swift").read_text()
check("the plain file never reaches the engine; the live file is the only caller of madeira_vti_*",
      re.search(r"madeira_\w+\(", plain) is None and "getenv" not in plain and "setenv" not in plain
      and not [p.name for p in APP.rglob("*.swift") if p.name != "BCDVTInterpLive.swift" and re.search(r"madeira_vti_\w+\(", p.read_text())])
launch = kit.body(live.replace("\n    }\n", "\n}\n"), "    nonisolated static func launch() -> String? {")
check("a session's start: the variable is cleared first and exported only for a game whose own line asks; MetalFX frame generation is switched off with it",
      launch.index("unsetenv(BCDVTInterp.variable)") < launch.index("MadeiraConfig.gameValue(BCDVTInterp.key)") < launch.index("setenv(BCDVTInterp.variable, String(frames), 1)")
      and launch.count("setenv(") == 2 and "MadeiraConfig.get(BCDVTInterp.key)" not in live and "_ = madeira_fgl_set(0)" in launch
      and "if case .on(let frames) = start {" in launch)
check("ContentView asks it after Lossless Scaling's start (which decides whether that generator runs), and logs its line",
      content.index("BCDLosslessLive.launch()") < content.index("if let line = BCDVTInterpLive.launch() { logStore.log(line) }")
      and content.count("BCDVTInterpLive.launch()") == 1)
check("no string literal names the engine's variables in a reading call (the catalog's scan would take the line above it for the note)",
      re.search(r'getenv\(\s*"MADEIRA', live) is None and 'static let variable = "MADEIRA_VT_INTERP"' in plain)
menu = (APP / "BCDMenuMore.swift").read_text()
check("the in-game menu: the section, its choice, and the end of a session",
      "BCDVTInterp.section(wanted:" in menu and 'case "mm.vti":' in menu and "BCDVTInterpLive.switched(on:" in menu and "BCDVTInterpLive.sessionEnded()" in menu)
check("the menu shows it on iOS 26 only, not on the Windows desktop, and not beside Lossless Scaling's section",
      re.search(r"private static var vtInterpSection: BCDMenuSection\? \{.*?\n    \}\n", menu, re.S) is not None
      and all(x in re.search(r"private static var vtInterpSection: BCDMenuSection\? \{.*?\n    \}\n", menu, re.S).group(0)
              for x in ("#available(iOS 26.0, *)", "entry.desktop != true", "if BCDLosslessLive.shared.live { return nil }", "guard wanted > 0 || live else { return nil }"))
      and "if BCDVTInterpLive.live { return nil }" in menu)
page = (APP / "BCDGamePageLive.swift").read_text()
check("the game's page: the line on iOS 26, and choosing it switches the live present path on for the game (its switch shows it)",
      "Self.line(BCDVTInterp.key, BCDVTInterp.title, profile, BCDVTInterp.choices, note: BCDVTInterp.note)" in page
      and re.search(r"case BCDVTInterp\.key:.*?setLine\(game, BCDVTInterp\.key, value\).*?setLine\(game, BCDVTInterp\.presentKey, \"1\"\)", page, re.S) is not None)
check("the screen is held at its top rate while the session adds frames", "if BCDVTInterpLive.holdsPanel { return panelMaxFPS }" in (APP / "ProMotionIntent.swift").read_text())
sections = (APP / "BCDSettingsSections.swift").read_text()
settings_live = (APP / "BCDSettingsLive.swift").read_text()
check("Settings > Graphics > Experimental has the device test: on iOS 26, not while a game runs, with its result on the page",
      "BCDVTInterp.Probe.title" in sections and "backend.vtProbeRun()" in sections and "var vtProbe: BCDVTInterp.Probe { get }" in sections
      and "func vtProbeRun()" in settings_live and "BCDVTInterpLive.probe" in settings_live)
header = (APP / "Madeira-Bridging-Header.h").read_text()
check("the bridging header declares the engine's functions as the script defines them",
      all(d in header and d[:-1] + " {" in src for d in ("int madeira_vti_set(int on);", "int madeira_vti_status(int *width, int *height, double *game_fps, double *generated_fps, double *pair_ms);",
                                                           "int madeira_vti_why(char *out, int cap);", "void madeira_vti_session_ended(void);", "int madeira_vti_probe(char *out, int cap);")))
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
check("the catalog has the game's line (off by default) and the engine's variable",
      re.search(r'key: "vt-interpolation", title: "[^"]*", kind: \.choice, defaultValue: ""', catalog) is not None
      and 'ConfigOption(key: "env.MADEIRA_VT_INTERP"' in catalog)
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
preview = (R / "tools/ui-preview.py").read_text()
check("both files are in the project; the preview build leaves the engine-bound one out",
      project.count("BCDVTInterp.swift in Sources") >= 1 and project.count("BCDVTInterpLive.swift in Sources") >= 1 and '"BCDVTInterpLive.swift"' in preview)

if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift part", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "stubs.swift").write_text("""import Foundation
enum BCDSessionTone: Equatable { case normal, soft, good, warm, hot }
struct BCDChoice: Identifiable, Equatable { var id: String; var title: String }
struct BCDMenuRow: Identifiable, Equatable {
    enum Kind: Equatable { case toggle(Bool); case choice([BCDChoice], selected: String); case note(BCDSessionTone) }
    var id: String; var title: String; var detail: String? = nil; var symbol: String? = nil; var kind: Kind
}
struct BCDMenuSection: Identifiable, Equatable { var id: String; var title: String? = nil; var rows: [BCDMenuRow] }
""")
        shutil.copy(R / "tests/host/bcd_vt_interp_checks.swift", t / "main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "run"), str(t / "main.swift"), str(t / "stubs.swift"),
                            str(APP / "BCDVTInterp.swift")], capture_output=True, text=True)
        if check("BCDVTInterp.swift and its harness compile on the host", r.returncode == 0):
            r = subprocess.run([str(t / "run")], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print(line)
            check(f"the Swift harness: {sum(1 for l in r.stdout.splitlines() if l.startswith('PASS'))} checks, none failing",
                  r.returncode == 0 and "ALL PASS" in r.stdout)
        else:
            print((r.stdout + r.stderr)[-2500:])

print("FAILED: vt-interp" if failures else "PASS: vt-interp")
sys.exit(1 if failures else 0)
