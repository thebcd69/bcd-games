#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""present-fx (tools/patch-winemetal-present-fx.py, env.MADEIRA_PRESENT_FX = 1): winemetal enlarges the picture itself and frame
generation works on the game's resolution. No Metal device, no Wine, no iPhone.

1. The script applies after the workflow's DXMT chain on a copy of dxmt/src, is idempotent, and changes winemetal_unix.c by exactly
   its own hunks (on an unpatched submodule and on a tree a build already patched; tests/host/winemetal_fx_kit.py).
2. Off path, on the source: every hook starts with madeira_pfx_on() (the key) or with a test for a stand-in, and a stand-in can only
   come from madeira_pfx_next, which the nextDrawable hook reaches only with the key; the old lines are all still there.
3. Behaviour on the host, with stand-ins for the layer, drawables, textures, command buffers and MetalFX's scaler
   (tests/host/present_fx_harness.m, compiled with the script's own blocks and hook lines): key off = the old order of operations and
   nothing else; a factor and "to the screen", chosen and changed while the game runs; the real drawable is taken only after the
   game's buffer is committed; MetalFX, the copy and the bilinear pass each where they belong; a generated frame goes out from a buffer
   of its own before the real frame's drawable is taken; dropped frames; picture slots; a lock that never comes free.
4. The sizes: the pure functions (factor, "to the screen", the key's values) compiled and run on the host.
5. The patched file compiles for iOS (arm64, the flags of build/dxmt-ios/build.sh) when xcrun and the SDK are there.
6. The Direct3D 12 runtime: no scaler of its own with present-fx, `metalfx-upscale = fit` without it; the old values untouched.
7. The app: what a session's start exports (tests/host/bcd_present_fx_checks.swift), the menu, the catalog, the project.
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


SCRIPT = "tools/patch-winemetal-present-fx.py"
wf = (R / ".github/workflows/build-ipa.yml").read_text()
chain = kit.chain()
check("the workflow runs the script after the frame generation live-control patch and after the pause-while-inactive patch",
      SCRIPT in chain and chain.index(SCRIPT) > chain.index("tools/patch-winemetal-framegen-live.py")
      and chain.index(SCRIPT) > chain.index("tools/patch-winemetal-pause-inactive.py"))
check("the workflow runs this check", "tests/host/check-present-fx.py" in wf)
if SCRIPT not in chain:
    print("FAILED: present-fx")
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
    print("FAILED: present-fx")
    sys.exit(1)
check("the script changes the file by its own hunks and nothing else (undoing them gives the file before back, byte for byte)", stage.exact())
bare = kit.undo(stage.before, "tools/patch-winemetal-framegen-live.py") if stage.before is not None else None
if check("the file before the script, without the live-control patch, for the two cases below", bare is not None):
    rc, said, untouched = kit.older_tree(SCRIPT, bare)
    check("an older dxmt checkout (which the live-control patch leaves alone) is left as it is too, with a line that says so, and is no error",
          rc == 0 and untouched and "is left out" in said and "::error::" not in said)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / kit.REL).parent.mkdir(parents=True)
        (Path(tmp) / kit.REL).write_text(bare)
        p = subprocess.run([sys.executable, str(R / SCRIPT)], cwd=tmp, capture_output=True, text=True)
        check("on the pin's tree without the live-control patch it is an error by name (the order of the patch steps), and nothing is written",
              p.returncode == 1 and "patch-winemetal-framegen-live.py has to run first" in p.stdout and (Path(tmp) / kit.REL).read_text() == bare)
src = stage.after

# ---- 2. off path, on the source
on = body(src, "static int madeira_pfx_on(void) {")
check("madeira_pfx_on: the key (read once by madeira_fgl_mode), not the remote Metal path, not after it gave up",
      "return madeira_fgl_mode() && !wmtr_enabled() && !atomic_load_explicit(&g_pfx_broken, memory_order_relaxed);" in on)
check("the key itself is MADEIRA_PRESENT_FX, off without it", 'madeira_fgl_env_int("MADEIRA_PRESENT_FX", 0) > 0 ? 1 : 0' in src)
# The present-fx keys per session (the port's known gap): the end of a session clears the upscaling reading and a give-up; the app calls it.
_reset = body(src, "void madeira_pfx_session_reset(void) {")
_app = R / "app/Madeira"
check("a session's end clears the upscaling reading and a give-up; the app calls it when the session ends",
      "atomic_store_explicit(&g_pfx_up_mode, -1, memory_order_relaxed);" in _reset and "atomic_store_explicit(&g_pfx_broken, 0, memory_order_relaxed);" in _reset
      and "madeira_pfx_session_reset()" in (_app / "BCDPresentFXLive.swift").read_text()
      and "BCDPresentFXLive.sessionEnded()" in (_app / "BCDMenuMore.swift").read_text()
      and "void madeira_pfx_session_reset(void);" in (_app / "Madeira-Bridging-Header.h").read_text())
proxy = body(src, "static int madeira_pfx_proxy(id obj) {")
check("a stand-in is known by its class, and no object can be one before the first was made",
      "return obj && g_pfx_class && object_getClass(obj) == g_pfx_class;" in proxy
      and src.count("g_pfx_class = ") == 1 and "if (!g_pfx_class) g_pfx_class = object_getClass(px);" in body(src, "static int madeira_pfx_next("))
check("stand-ins are made in one place only", src.count("[[[MadeiraPFXDrawable alloc] init] autorelease]") == 1 and src.count("MadeiraPFXDrawable alloc") == 1)
nxt = body(src, "_MetalLayer_nextDrawable(void *obj) {")
hook = nxt[nxt.index("  if (madeira_pfx_on()) {   /* madeira-bcd: present-fx"):nxt.index("  struct timespec t0, t1;")]
check("nextDrawable: the stand-in only inside `if (madeira_pfx_on())`, after the uncapped mode's skip; the old acquisition follows unchanged",
      "madeira_pfx_next((CAMetalLayer *)params->handle, &pfx_stand_in)" in hook and nxt.count("madeira_pfx") == 2
      and nxt.index("presentSkipped") < nxt.index("if (madeira_pfx_on())")
      and "  struct timespec t0, t1;\n  clock_gettime(CLOCK_MONOTONIC, &t0);\n  params->ret = (obj_handle_t)[(CAMetalLayer *)params->handle nextDrawable];" in nxt)
present = body(src, "_MTLCommandBuffer_presentDrawable(void *obj) {")
check("presentDrawable: both added blocks start with the test for a stand-in; the old lines are as they were",
      present.count("madeira_pfx") == 4 and "if (madeira_pfx_proxy((id)params->arg) && !g_pfx_tls.deferred) {" in present
      and "if (madeira_pfx_proxy((id)params->arg)) {   /* madeira-bcd: present-fx: the picture goes into a real drawable now" in present
      and "mode != 2 && madeira_fg_enabled() &&" in present
      and '    madeira_log_present_cadence("presentDrawable", 0.0);\n    [(id<MTLCommandBuffer>)params->handle presentDrawable:(id<MTLDrawable>)params->arg];' in present)
after = body(src, "_MTLCommandBuffer_presentDrawableAfterMinimumDuration(void *obj) {")
check("presentDrawable:afterMinimumDuration: the same test first", "if (madeira_pfx_proxy((id)params->arg0)) {" in after and after.count("madeira_pfx") == 2)
commit = body(src, "_MTLCommandBuffer_commit(void *obj) {")
check("commit: one test of a counter after the commit; the counter is raised only when a stand-in is presented",
      "  [(id<MTLCommandBuffer>)params->handle commit];\n  if (atomic_load_explicit(&g_pfx_pending, memory_order_relaxed))" in commit
      and src.count("atomic_fetch_add_explicit(&g_pfx_pending, 1, memory_order_relaxed)") == 1
      and "atomic_fetch_add_explicit(&g_pfx_pending, 1, memory_order_relaxed)" in body(src, "static void madeira_pfx_defer("))
size = body(src, "static CGSize madeira_pfx_layer_size(")
check("setProps: without the key the layer gets exactly the size it was given",
      size.index("if (!madeira_pfx_on() ||") < size.index("madeira_pfx_entry(layer, 1)")
      and "return CGSizeMake(w, h);" in size.split("madeira_pfx_entry(layer, 1)")[0]
      and "layer.drawableSize = madeira_pfx_layer_size(layer, props->drawable_width, props->drawable_height);" in src
      and src.count("layer.drawableSize = CGSizeMake(props->drawable_width, props->drawable_height)") == 0)
virt = body(src, "static void madeira_pfx_virtual_size(")
check("getProps: without the key the answer is the layer's own size", virt.index("if (!madeira_pfx_on()) return;") < virt.index("madeira_pfx_entry("))
inner0 = body(src, "static int madeira_fg_present_inner0(")
check("frame generation: each new branch asks for a stand-in first and the old lines are its other branch",
      "madeira_pfx_proxy((id)drawable) ? 2 : 3, now);" in inner0
      and "if (!extra || (!madeira_pfx_proxy((id)drawable) &&" in inner0
      and "(extra.texture.width != g_fg.w || extra.texture.height != g_fg.h))) {" in inner0
      and "  } else {\n    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];\n    [blit copyFromTexture:g_fg.out toTexture:extra.texture];\n    [blit endEncoding];\n  }" in inner0
      and "  } else {\n  madeira_fgl_track(extra); madeira_fgl_track(drawable);" in inner0
      and "  [cb presentDrawable:extra];\n  [cb presentDrawable:drawable atTime:CACurrentMediaTime() + g_fg.dt * 0.5];\n  }" in inner0)
wrap = body(src, "static int madeira_fg_present_inner(id<MTLCommandBuffer> cb")
check("frame generation's wrapper: a real drawable is counted as before; only a stand-in's pair is finished from buffers of ours",
      "  } else\n  if (!r) madeira_fgl_track((id<MTLDrawable>)drawable);" in wrap
      and "if (r) madeira_pfx_finish_pair(cb, (MadeiraPFXDrawable *)drawable);" in wrap)
ctl = src[src.index("static NTSTATUS _madeira_ctl(void *args) {"):]
ops = re.findall(r"^\s*case (\d+):", ctl[:ctl.index("\n}\n")], re.M)
check("MadeiraCtl op 30 is new and every op is used once", ops.count("30") == 1 and len(ops) == len(set(ops)))
check("the app's setters only store numbers (they never read the key, so a call before a session starts cannot fix its answer)",
      "madeira_fgl_mode" not in body(src, "void madeira_pfx_set_upscale(int mode, int milli) {")
      and "madeira_fgl_mode" not in body(src, "void madeira_pfx_set_screen(int a, int b) {")
      and "madeira_pfx_on" not in body(src, "int madeira_pfx_status("))
check("the lock is only ever tried, never waited on without a bound",
      "pthread_mutex_lock(&g_pfx_mu)" not in src and src.count("pthread_mutex_trylock(&g_pfx_mu)") == 2)

with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    # ---- 4. the sizes
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        print("SKIP: no C compiler for the behaviour parts")
    else:
        pure = src[src.index("/* pfx-pure-begin"):src.index("/* pfx-pure-end */")]
        (t / "pure.c").write_text(r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
''' + pure + r'''
static int fails;
static void expect(int c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
static int is(int mode, int milli, unsigned sl, unsigned ss, unsigned w, unsigned h, unsigned ew, unsigned eh) {
  unsigned ow = 1, oh = 1;
  madeira_pfx_target(mode, milli, sl, ss, w, h, &ow, &oh);
  return ow == ew && oh == eh;
}
int main(void) {
  int milli = 7;
  expect(is(0, 2000, 2622, 1206, 1408, 648, 1408, 648), "off: the picture's own size");
  expect(is(1, 1500, 2622, 1206, 1408, 648, 2112, 972), "1.5: 1408x648 -> 2112x972");
  expect(is(1, 2000, 0, 0, 1280, 720, 2560, 1440), "2: 1280x720 -> 2560x1440 (no screen size needed)");
  expect(is(1, 1500, 0, 0, 1281, 721, 1922, 1082), "odd sizes come out even, as bcd.cfg's metalfx-upscale makes them (1281x721 at 1.5 -> 1922x1082)");
  expect(is(1, 3000, 0, 0, 1280, 720, 2560, 1440), "a factor over 2 is 2");
  expect(is(1, 1050, 0, 0, 1280, 720, 1280, 720), "a factor under 1.1 is none");
  expect(is(1, 2000, 0, 0, 4200, 2000, 4200, 2000), "nothing over 8192: 4200x2000 at 2 stays");
  expect(is(2, 0, 2622, 1206, 1408, 648, 2620, 1206), "to the screen: 1408x648 on 2622x1206 -> 2620x1206 (the height decides: 1.861)");
  expect(is(2, 0, 2622, 1206, 1280, 720, 2144, 1206), "to the screen: 1280x720 -> 2144x1206 (16:9 keeps its shape)");
  expect(is(2, 0, 2622, 1206, 960, 540, 1920, 1080), "to the screen: at most twice (960x540 -> 1920x1080)");
  expect(is(2, 0, 2622, 1206, 2622, 1206, 2622, 1206), "to the screen: a picture of the screen's size is left alone");
  expect(is(2, 0, 2622, 1206, 2400, 1104, 2400, 1104), "to the screen: under 1.1 (2400x1104) is left alone");
  expect(is(2, 0, 2622, 1206, 648, 1408, 1206, 2620), "to the screen: a portrait picture goes with the screen's short side across");
  expect(is(2, 0, 0, 0, 1280, 720, 1280, 720), "to the screen without a screen size: left alone");
  expect(is(2, 0, 2752, 2064, 1152, 800, 2304, 1600), "to the screen on an iPad (2752x2064): 1152x800 -> 2304x1600, at most twice");
  expect(is(1, 1500, 0, 0, 0, 720, 0, 720), "a size of zero: nothing");
  expect(is(7, 1500, 2622, 1206, 1280, 720, 1280, 720), "a mode that does not exist: nothing");
  expect(madeira_pfx_parse(NULL, &milli) == 0 && madeira_pfx_parse("", &milli) == 0 && madeira_pfx_parse("off", &milli) == 0, "the key: nothing, empty and off are off");
  expect(madeira_pfx_parse("fit", &milli) == 2, "the key: fit is to the screen");
  expect(madeira_pfx_parse("1.5", &milli) == 1 && milli == 1500 && madeira_pfx_parse("2", &milli) == 1 && milli == 2000
         && madeira_pfx_parse("9", &milli) == 1 && milli == 2000, "the key: a factor, at most 2");
  expect(madeira_pfx_parse("1.05", &milli) == 0 && madeira_pfx_parse("abc", &milli) == 0, "the key: under 1.1 or no number is off");
  return fails ? 1 : 0;
}
''')
        b = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-o", str(t / "pure"), str(t / "pure.c")], capture_output=True, text=True)
        if check("the size functions compile on the host", b.returncode == 0):
            r = subprocess.run([str(t / "pure")], capture_output=True, text=True)
            print(r.stdout.rstrip())
            check("the sizes (factor, to the screen, the key's values)", r.returncode == 0)
        else:
            print(b.stderr[-2000:])

    # ---- 3. behaviour, with stand-ins (Objective-C on a Mac)
    if sys.platform != "darwin" or not cc:
        print("SKIP: the behaviour harness is Objective-C and needs a Mac")
    else:
        h = (R / "tests/host/present_fx_harness.m").read_text()
        parts = {
            "EARLY": mod.EARLY,
            "BLOCK_E": mod.BLOCK_E,
            "BLOCK_L": mod.BLOCK_L,
            "COMMIT": mod.COMMIT,
            "PRESENT_TOP": mod.PRESENT_TOP[len(mod.A_PRESENT_TOP):],
            "PRESENT_FG": mod.PRESENT_FG[len(mod.A_PRESENT_FG):],
            "PRESENT_AFTER": mod.PRESENT_AFTER,
            "NEXT": mod.NEXT,
            "SETPROPS": mod.SETPROPS,
            "GETPROPS": mod.GETPROPS,
            "CTL": mod.CTL[:-len(mod.A_CTL)],
        }
        for name, text in parts.items():
            assert h.count(f"/*@{name}@*/") == 1, name
            h = h.replace(f"/*@{name}@*/", text)
        (t / "h.m").write_text(h)
        flags = ["-fno-objc-arc", "-fblocks", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-objc-method-access",
                 "-Wno-incompatible-pointer-types", "-Wno-deprecated-declarations", "-Wno-objc-protocol-method-implementation",
                 "-framework", "Foundation", "-framework", "QuartzCore", "-framework", "Metal", "-framework", "MetalFX", "-framework", "CoreGraphics"]
        b = subprocess.run([cc, "-x", "objective-c", str(t / "h.m"), "-o", str(t / "h")] + flags, capture_output=True, text=True)
        if check("the script's blocks and hook lines compile on the host with the stand-ins", b.returncode == 0):
            for phase, what in ((0, "the key is off"), (1, "upscaling: a factor, to the screen, changed and switched off while the game runs"),
                                (2, "frame generation with a stand-in"), (3, "a lock that never comes free")):
                r = subprocess.run([str(t / "h"), str(phase)], capture_output=True, text=True, timeout=180)
                print(r.stdout.rstrip())
                if r.returncode:
                    print(r.stderr[-1500:])
                check(f"harness phase {phase} ({what})", r.returncode == 0 and "ok  " in r.stdout)
        else:
            print("\n".join(l for l in b.stderr.splitlines() if "error" in l or "Undefined" in l)[:4000])

# ---- 5. compiles for iOS
ran, ok_c, err = stage.compile_ios()
if not ran:
    print("SKIP: no iOS SDK (xcrun) or no dxmt/include for the compile part")
else:
    if not check("the patched winemetal_unix.c compiles for iOS arm64", ok_c):
        print("\n".join(l for l in err.splitlines() if "error" in l)[:3000])
    check("no compiler warning names the new code", not any(("pfx" in l or "PFX" in l) for l in err.splitlines() if "warning" in l))
stage.close()

# ---- 6. the Direct3D 12 runtime
d3d12 = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
fx = body(d3d12, "static void mad_swap_make_fx(struct mad_swapchain *s) {")
up = body(d3d12, "static int mad_pfx_upscale(const struct mad_swapchain *s, const char *v, double *f) {")
check("Direct3D 12: the scaler setup asks winemetal first (MadeiraCtl op 30) and makes none when the presenter enlarges",
      "a.op = 30; a.ptr = (UINT64)(ULONG_PTR)pi; a.len = sizeof pi;" in up and "if (!a.ret) return 0;" in up
      and up.index("if (!a.ret) return 0;") < up.index("if (pi[0]) {")
      and "    f = 0.0;\n    if (mad_pfx_upscale(s, v, &f)) return;" in fx)
check("Direct3D 12: a winemetal without the op, or a number as before, takes the old lines unchanged",
      "    if (f == 0.0) f = strtod(v, NULL);\n    if (!(f >= 1.1)) return;\n    if (f > 2.0) f = 2.0;   /* as DXMT's D3D11 MetalFX swapchain, which takes 1 to 2 */" in fx
      and '!strcmp(v, "fit")' in up and up.count("*f = ") == 3)
if cc:
    # the "fit" rule of mad_pfx_upscale, run on the host with a stand-in for MadeiraCtl
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "d.c").write_text(r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int UINT; typedef uint32_t UINT32; typedef uint64_t UINT64; typedef uintptr_t ULONG_PTR; typedef long LONG;
struct madeira_ctl_args { uint32_t op, ret; uint64_t ptr, len; char name[160]; };
struct mad_swapchain { struct { UINT Width, Height; } desc; };
static uint32_t g_known = 1, g_on, g_long = 2622, g_short = 1206;
static void MadeiraCtl(struct madeira_ctl_args *a) {
  uint32_t *o = (uint32_t *)(uintptr_t)a->ptr;
  if (a->op != 30 || !g_known) return;
  o[0] = g_on; o[3] = g_long; o[4] = g_short; a->ret = 1;
}
static LONG InterlockedIncrement(LONG *v) { return ++*v; }
static void d3d12_log(const char *fmt, ...) { (void)fmt; }
''' + up + r'''
static int fails;
static void expect(int c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
int main(void) {
  struct mad_swapchain s = { { 1408, 648 } };
  double f = 0.0;
  expect(mad_pfx_upscale(&s, "1.5", &f) == 0 && f == 0.0, "a number: not handled here (the old lines parse it)");
  expect(mad_pfx_upscale(&s, "fit", &f) == 0 && f > 1.8610 && f < 1.8612, "fit: 1408x648 on 2622x1206 is 1.861");
  s.desc.Width = 2622; s.desc.Height = 1206; f = 0.0;
  expect(mad_pfx_upscale(&s, "fit", &f) == 0 && f == 1.0, "fit on a swap chain of the screen's size: 1 (the caller makes no scaler under 1.1)");
  s.desc.Width = 648; s.desc.Height = 1408; f = 0.0;
  expect(mad_pfx_upscale(&s, "fit", &f) == 0 && f > 1.8610 && f < 1.8612, "fit, portrait: the same factor");
  g_long = 0; f = 0.0;
  expect(mad_pfx_upscale(&s, "fit", &f) == 0 && f == 0.0, "fit without a screen size: nothing (strtod then reads 0: no scaler)");
  g_long = 2622; g_on = 1; f = 0.0;
  expect(mad_pfx_upscale(&s, "2", &f) == 1 && mad_pfx_upscale(&s, "fit", &f) == 1, "present-fx on: the swap chain makes no scaler, whatever the value");
  g_known = 0; g_on = 1; f = 0.0;
  expect(mad_pfx_upscale(&s, "2", &f) == 0 && mad_pfx_upscale(&s, "fit", &f) == 0 && f == 0.0, "a winemetal without op 30: everything as before");
  return fails ? 1 : 0;
}
''')
        b = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-Wno-unused-function", "-o", str(t / "d"), str(t / "d.c")], capture_output=True, text=True)
        if check("mad_pfx_upscale compiles on the host", b.returncode == 0):
            r = subprocess.run([str(t / "d")], capture_output=True, text=True)
            print(r.stdout.rstrip())
            check("Direct3D 12: fit, present-fx, an older winemetal", r.returncode == 0)
        else:
            print(b.stderr[-2000:])

# ---- 7. the app
APP = R / "app/Madeira"
hdr = (APP / "Madeira-Bridging-Header.h").read_text()
check("the bridging header declares the three functions",
      all(d in hdr for d in ("void madeira_pfx_set_screen(int a, int b);", "void madeira_pfx_set_upscale(int mode, int milli);",
                             "int madeira_pfx_status(int *game_w, int *game_h, int *shown_w, int *shown_h, int *kind);")))
live = (APP / "BCDPresentFXLive.swift").read_text()
content = (APP / "ContentView.swift").read_text()
launch = (APP / "LibraryBCD.swift").read_text()
check("a session's start: present-fx or \"to the screen\" is decided in one place, and every other choice takes the old lines unchanged",
      "if let fx = BCDPresentFXLive.launch() {" in content
      and "} else\n            if let txt = MadeiraConfig.gameValue(\"metalfx-upscale\") ?? MadeiraConfig.get(\"metalfx-upscale\"),\n"
          "               let factor = Double(txt), factor >= 1.1 {\n                setenv(\"DXMT_METALFX_SPATIAL_SWAPCHAIN\", \"1\", 1)\n"
          "                dxmtOptions.append(\"d3d11.metalSpatialUpscaleFactor=\\(min(factor, 2))\")" in content
      and "guard let route = BCDUpscale.route(value: value, presentFX: on, desktop: desktop, screenLong: long, screenShort: short) else { return nil }" in live
      and "if BCDPresentFXLive.decidesAtStart(profile) {\n            unsetenv(\"DXMT_METALFX_SPATIAL_SWAPCHAIN\")\n        } else\n        if let f = profile?.metalFXFactor {" in launch)
check("with present-fx the old Direct3D 11 swap chain scaler is not asked for and the presenter gets the choice",
      'case .presenter(let choice):\n            unsetenv("DXMT_METALFX_SPATIAL_SWAPCHAIN")\n            setenv("MADEIRA_PFX_UPSCALE", choice, 1)' in live)
menu = (APP / "BCDMenuMore.swift").read_text()
check("the in-game menu: the Upscaling section only in a present-fx session; its choice is written to the game's file and takes effect at once",
      "guard BCDPresentFXLive.live, let entry = model.activeEntry, entry.desktop != true else { return nil }" in menu
      and 'case "mm.upscale":' in menu and "BCDPresentFXLive.chosen(value)" in menu
      and "guard live else { return }" in live.split("static func chosen(_ value: String) {")[1].split("\n    }")[0])
check("\"To the screen\" is offered on a game's page, in Settings and in the catalog",
      '("fit", "To the screen")' in (APP / "GameProfiles.swift").read_text()
      and 'BCDSettingsChoice(value: BCDUpscale.fit, label: "To the screen")' in (APP / "BCDSettingsSections.swift").read_text()
      and '("fit", "To the screen")' in (R / "build/tools/gen-config-catalog.py").read_text())
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
check("the catalog: env.MADEIRA_PRESENT_FX is off by default and names both scripts; env.MADEIRA_PFX_UPSCALE is the app's",
      re.search(r'key: "env\.MADEIRA_PRESENT_FX", title: "[^"]*", kind: \.bool, defaultValue: "0"', catalog) is not None
      and 'ConfigOption(key: "env.MADEIRA_PFX_UPSCALE"' in catalog)
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the two Swift files are in the Xcode project; the preview build leaves the engine-bound one out",
      all(pbx.count(f"/* {n} in Sources */") == 2 for n in ("BCDPresentFX.swift", "BCDPresentFXLive.swift"))
      and '"BCDPresentFXLive.swift"' in (R / "tools/ui-preview.py").read_text())
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
        shutil.copy(R / "tests/host/bcd_present_fx_checks.swift", t / "main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "run"), str(t / "main.swift"), str(t / "stubs.swift"),
                            str(APP / "BCDPresentFX.swift")], capture_output=True, text=True)
        if check("BCDPresentFX.swift and its harness compile on the host", r.returncode == 0):
            r = subprocess.run([str(t / "run")], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print(line)
            check(f"the Swift harness: {sum(1 for l in r.stdout.splitlines() if l.startswith('PASS'))} checks, none failing",
                  r.returncode == 0 and "ALL PASS" in r.stdout)
        else:
            print((r.stdout + r.stderr)[-2500:])

print("FAILED: present-fx" if failures else "PASS: present-fx")
sys.exit(1 if failures else 0)
