#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Just Cause 3's world stall, made readable (round 624, agent D2 S15; tools/patch-winemetal-gpu-stall.py); no Wine, no Metal.

The patch adds MADEIRA_GPU_TRACE=slow=<ms>,S,N (the frame trace starts after 3 presents in a row more than <ms> apart) and
MADEIRA_GPU_STALL_MS=<ms> (a command buffer unfinished <ms> after its commit is named once per stall, then how long it waited to be
scheduled and how long the GPU ran it). This test copies dxmt/src as committed into a temporary tree, runs every dxmt / airconv /
winemetal patch the workflow runs before it, this one twice, then every one after it, and checks:
  - it applies once, then says "already patched"; the later patches still apply;
  - a moved anchor fails by name and leaves the file alone;
  - the code it adds, cut from the patched file and compiled as Objective-C on the host with a fake command buffer and a fake
    clock: the old trace form T,S,N works as before; slow mode ignores a single long gap, starts a trace on the third slow present
    in a row, keeps 60 s between traces and starts the value dumps with the first trace; off for an empty, zero or unreadable value;
    the stall watch is off (no thread, no handler) outside 100-60000 ms, names the oldest unfinished buffer once per stall with its
    state, prints the scheduled/GPU split when that buffer completes, counts what the 256 slots cannot hold;
  - the workflow runs it after the GPU trace patch and before winemetal is built; the list policy takes the new switch.
SKIP (exit 0) when no dxmt sources are available.
"""
import importlib.util
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

_spec = importlib.util.spec_from_file_location("dxmt_base", pathlib.Path(__file__).resolve().parent / "dxmt_base.py")
dxmt_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dxmt_base)

R = pathlib.Path(__file__).resolve().parents[2]
src = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (R / ".github/workflows/build-ipa.yml").read_text()
scripts = re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", wf)
me = "tools/patch-winemetal-gpu-stall.py"
check("the workflow runs the patch after the GPU trace patch and before winemetal is built",
      me in scripts and "tools/patch-winemetal-gpu-trace.py" in scripts
      and scripts.index(me) == scripts.index("tools/patch-winemetal-gpu-trace.py") + 1
      and wf.index("python3 " + me) < wf.index("name: Build dxmt-ios"))
check("CI runs this check", "python3 tests/host/check-gpu-stall.py" in wf)
pol = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the list policy takes MADEIRA_GPU_STALL_MS, a whole number from 100 to 60000",
      '"MADEIRA_GPU_STALL_MS"' in pol and 'case "MADEIRA_GPU_STALL_MS":' in pol and "(100...60000).contains(n)" in pol)

if not (src / "winemetal" / "unix" / "winemetal_unix.c").is_file():
    print(f"SKIP: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)")
    print("PASS: gpu stall (static part)" if ok else "FAIL: gpu stall")
    sys.exit(0 if ok else 1)

with tempfile.TemporaryDirectory() as tmp:
    t = pathlib.Path(tmp)
    arc = subprocess.run(["git", "-C", str(src.parent), "archive", "--format=tar", dxmt_base.rev(src.parent), "src"],
                         capture_output=True)
    if arc.returncode == 0 and arc.stdout:
        (t / "dxmt").mkdir()
        with tarfile.open(fileobj=io.BytesIO(arc.stdout)) as tf:
            try:
                tf.extractall(t / "dxmt", filter="data")
            except TypeError:
                tf.extractall(t / "dxmt")
    else:
        shutil.copytree(src, t / "dxmt/src")
    (t / "build").mkdir()
    shutil.copy(R / "build/madeira_cfg.h", t / "build/madeira_cfg.h")
    shutil.copytree(R / "research/remote-metal", t / "research/remote-metal")
    wm = t / "dxmt/src/winemetal/unix/winemetal_unix.c"
    for s in scripts[:scripts.index(me)]:
        p = subprocess.run([sys.executable, str(R / s)], cwd=t, capture_output=True, text=True)
        if p.returncode:
            check(f"earlier patch {s} applies", False, (p.stdout + p.stderr).strip())
    before = wm.read_text()
    p1 = subprocess.run([sys.executable, str(R / me)], cwd=t, capture_output=True, text=True)
    p2 = subprocess.run([sys.executable, str(R / me)], cwd=t, capture_output=True, text=True)
    check("applies once, then says already patched",
          p1.returncode == 0 and "gpu-stall patched" in p1.stdout and p2.returncode == 0 and "already patched" in p2.stdout,
          p1.stdout + p1.stderr + p2.stdout + p2.stderr)
    after = wm.read_text()
    later_ok = True
    for s in scripts[scripts.index(me) + 1:]:
        p = subprocess.run([sys.executable, str(R / s)], cwd=t, capture_output=True, text=True)
        if p.returncode:
            later_ok = False
            check(f"later patch {s} still applies", False, (p.stdout + p.stderr).strip())
    check("every later dxmt/airconv/winemetal patch still applies", later_ok)

    spec = importlib.util.spec_from_file_location("gpu_stall_patch", R / me)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    undone = after
    for _, old, new in reversed(mod.PAIRS):
        undone = undone.replace(new, old, 1)
    check("nothing else in the file changed (undoing the insertions gives the file back)", undone == before)
    commit = after[after.index("static NTSTATUS\n_MTLCommandBuffer_commit(void *obj) {"):]
    commit = commit[:commit.index("\n}\n")]
    check("the buffer is tracked before its commit (Metal takes handlers only before it), the remote path untouched",
          commit.index("madeira_gs_track((id<MTLCommandBuffer>)params->handle);") < commit.index("[(id<MTLCommandBuffer>)params->handle commit];")
          and commit.index("return STATUS_SUCCESS;\n  }") < commit.index("madeira_gs_track("))

    # a moved anchor fails by name, the file unchanged
    wm.write_text(before.replace("    madeira_gt_full_left--;\n    madeira_gt_next_full = now + 60.0;\n",
                                 "    madeira_gt_full_left -= 1;\n    madeira_gt_next_full = now + 60.0;\n", 1))
    moved = wm.read_text()
    p3 = subprocess.run([sys.executable, str(R / me)], cwd=t, capture_output=True, text=True)
    check("a moved anchor fails by name, file unchanged",
          p3.returncode != 0 and "full-trace trigger" in p3.stdout + p3.stderr and wm.read_text() == moved, p3.stdout + p3.stderr)

    def cut(start, end_marker, text=after):
        a = text.index(start)
        b = text.index(end_marker, a)
        return text[a:b]

    statics = cut("static int madeira_gt_mode = -1;", "\nstatic int madeira_gt_on(void) {")
    statics = statics[statics.index("static int madeira_gt_mode"):]
    gt_on = cut("static int madeira_gt_on(void) {", "\n}\n") + "\n}\n"
    note = cut("static void madeira_gt_note_present(obj_handle_t cb) {", "\n}\n") + "\n}\n"
    after_commit = cut("static void madeira_gt_after_commit(obj_handle_t cb) {", "\n}\n") + "\n}\n"
    gs = cut("#define MADEIRA_GS_SLOTS 256", "static NTSTATUS\n_MTLCommandBuffer_commit(void *obj) {")
    gs = gs.replace("(double)clock_gettime_nsec_np(CLOCK_UPTIME_RAW) / 1e9", "fake_now")

    harness = r'''
#import <Foundation/Foundation.h>
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
typedef uint64_t obj_handle_t;
@protocol MTLCommandBuffer <NSObject>
- (void)addScheduledHandler:(void (^)(id<MTLCommandBuffer>))block;
- (void)addCompletedHandler:(void (^)(id<MTLCommandBuffer>))block;
@property (readonly) double GPUStartTime;
@property (readonly) double GPUEndTime;
@property (readonly) long status;
@property (readonly) id error;
@end
@interface FakeCB : NSObject <MTLCommandBuffer>
@property (copy) void (^sched)(id<MTLCommandBuffer>);
@property (copy) void (^done)(id<MTLCommandBuffer>);
@property double start, end;
@end
@implementation FakeCB
- (void)addScheduledHandler:(void (^)(id<MTLCommandBuffer>))block { self.sched = block; }
- (void)addCompletedHandler:(void (^)(id<MTLCommandBuffer>))block { self.done = block; }
- (double)GPUStartTime { return self.start; }
- (double)GPUEndTime { return self.end; }
- (long)status { return 4; }
- (id)error { return nil; }
@end
static double fake_now = 1000.0;
static int threads_made, dumps_full, dumps_small;
static int fake_pthread_create(pthread_t *t, const pthread_attr_t *a, void *(*f)(void *), void *arg)
{ (void)t; (void)a; (void)f; (void)arg; threads_made++; return 0; }
#define pthread_create fake_pthread_create
static double madeira_gt_now(void) { return fake_now; }
static unsigned madeira_gt_dumped, madeira_gt_unknown;
static void madeira_gt_dump(id<MTLCommandBuffer> cb, int full) { (void)cb; if (full) dumps_full++; else dumps_small++; }
static pthread_mutex_t madeira_gt_lock = PTHREAD_MUTEX_INITIALIZER;
@STATICS@
@GT_ON@
@NOTE@
@AFTER@
@GS@
#undef pthread_create
static void frame(double gap_ms) {
  static FakeCB *cb;
  if (!cb) cb = [FakeCB new];
  fake_now += gap_ms / 1000.0;
  madeira_gt_note_present((obj_handle_t)(uintptr_t)(__bridge void *)cb);
  madeira_gt_after_commit((obj_handle_t)(uintptr_t)(__bridge void *)cb);
}
int main(int argc, char **argv) {
  @autoreleasepool {
    const char *mode = argv[1];
    if (!strcmp(mode, "trace")) {
      if (argc > 2) setenv("MADEIRA_GPU_TRACE", argv[2], 1);
      const char *script = argc > 3 ? argv[3] : "";
      /* script: comma-separated gaps in ms; "Nx<ms>" repeats */
      char buf[4096]; strncpy(buf, script, sizeof buf - 1); buf[sizeof buf - 1] = 0;
      frame(0);
      for (char *tok = strtok(buf, ","); tok; tok = strtok(NULL, ",")) {
        char *x = strchr(tok, 'x');
        int n = x ? atoi(tok) : 1;
        double ms = atof(x ? x + 1 : tok);
        while (n-- > 0) frame(ms);
      }
      printf("dumps full=%d small=%d frames=%u\n", dumps_full, dumps_small, madeira_gt_frame);
      return 0;
    }
    if (!strcmp(mode, "stall")) {
      if (argc > 2) setenv("MADEIRA_GPU_STALL_MS", argv[2], 1);
      FakeCB *a = [FakeCB new], *b = [FakeCB new], *c = [FakeCB new];
      madeira_gs_track(a);
      printf("on=%d threads=%d handlers=%d\n", madeira_gs_limit > 0, threads_made, a.sched != nil && a.done != nil);
      if (madeira_gs_limit <= 0) return 0;
      fake_now += 0.1; madeira_gs_track(b);
      fake_now += 0.1; madeira_gs_track(c);
      a.sched(a);                                  /* a reaches the GPU */
      fake_now += 1.0; madeira_gs_scan(fake_now);  /* a: 1200 ms, under the limit */
      fprintf(stderr, "--- 1.2 s\n");
      fake_now += 0.5; madeira_gs_scan(fake_now);  /* a: 1700 ms: named */
      fake_now += 0.5; madeira_gs_scan(fake_now);  /* still the same stall: nothing new */
      b.start = 5.0; b.end = 5.5; b.done(b);       /* another buffer completes: no line */
      a.start = 7.0; a.end = 7.25; fake_now += 0.3; a.done(a);   /* the named one completes: the split */
      fake_now += 0.5; madeira_gs_scan(fake_now);  /* c is the oldest now, never scheduled: the next stall */
      c.done(c);
      fprintf(stderr, "--- table\n");
      NSMutableArray *keep = [NSMutableArray array];
      for (int i = 0; i < 257; i++) { FakeCB *x = [FakeCB new]; [keep addObject:x]; madeira_gs_track(x); }
      fake_now += 2.0; madeira_gs_scan(fake_now);
      printf("untracked=%u\n", madeira_gs_untracked);
      return 0;
    }
  }
  return 2;
}
'''
    code = (harness.replace("@STATICS@", statics).replace("@GT_ON@", gt_on).replace("@NOTE@", note)
            .replace("@AFTER@", after_commit).replace("@GS@", gs))
    cc = shutil.which("clang") or shutil.which("cc")
    (t / "h.m").write_text(code)
    # manual reference counting, as winemetal is built (meson: -ObjC, no ARC): a handle is cast to an object there
    b = subprocess.run([cc, "-x", "objective-c", "-fblocks", "-Wall", "-Wno-unused-function",
                        "-Wno-unused-variable", "-framework", "Foundation", "-o", str(t / "h"), str(t / "h.m")],
                       capture_output=True, text=True)
    check("the added code compiles as Objective-C on the host (fake command buffer, fake clock)", b.returncode == 0,
          b.stderr[-2500:])
    if b.returncode == 0:
        def run(*args):
            r = subprocess.run([str(t / "h")] + list(args), capture_output=True, text=True, timeout=60)
            return r.returncode, r.stdout, r.stderr

        # the old form: a time, unchanged
        rc, out, err = run("trace", "95,30,2", "6000x16,100x16")
        begins = [l for l in err.splitlines() if "frame begin" in l]
        check("the old form T,S,N: the first trace 95 s after the first present, value dumps from then on",
              rc == 0 and "frame traces from 95 s after the first frame (2, 60 s apart), value dumps every 30.00 s" in err
              and len(begins) == 1 and "(t=95.0 s)" in begins[0] and "slow" not in begins[0], err[-800:])
        # slow mode: a single long gap is not enough; the third slow present in a row starts it
        rc, out, err = run("trace", "slow=150,30,3", "200x16,2000,200x16,340,16,340,340,340,16,16")
        begins = [l for l in err.splitlines() if "frame begin" in l]
        check("slow mode: on with its own line; a single 2 s hitch and two slow presents in a row start nothing; the third does",
              rc == 0 and "frame traces after 3 presents in a row more than 150 ms apart (3, 60 s apart)" in err
              and len(begins) == 1 and "(slow: 3 presents in a row more than 150 ms apart, the last 340 ms;" in begins[0]
              and "frame end" in err and "dumps full=1 small=0" in out, err[-1200:] + out)
        # 60 s between traces; value dumps every 30 s only after the first trace
        rc, out, err = run("trace", "slow=150,30,3", "100x16,3x400,16,16,50x1000,3x400,16,16,40x1000,3x400,16,16,200x1000,3x400,16")
        begins = [l for l in err.splitlines() if "frame begin" in l]
        check("slow mode: at most N traces, 60 s apart; value dumps every 30 s from the first trace",
              rc == 0 and len(begins) == 3 and "full=3" in out
              and int(re.search(r"small=(\d+)", out).group(1)) >= 9, "\n".join(begins) + out)
        rc, out, err = run("trace", "slow=150,30,3", "2000x16")
        check("slow mode: no slow present, no trace and no value dump", rc == 0 and "frame begin" not in err and "full=0 small=0" in out,
              err[-500:] + out)
        for v in ("", "slow=0", "slow=abc", "0"):
            rc, out, err = run("trace", v, "10x400")
            check(f"MADEIRA_GPU_TRACE={v!r}: off", rc == 0 and "[gpu-trace]" not in err and "full=0 small=0" in out, err + out)

        for v in ("", "50", "abc", "70000"):
            rc, out, err = run("stall", v)
            check(f"MADEIRA_GPU_STALL_MS={v!r}: off, no thread, no handler", rc == 0 and "on=0 threads=0 handlers=0" in out
                  and "[gpu-stall]" not in err, out + err)
        rc, out, err = run("stall", "1500")
        lines = err.splitlines()
        check("on: one line naming the switch, one watch thread, both handlers on each tracked buffer",
              rc == 0 and "on=1 threads=1 handlers=1" in out
              and lines[0] == "[gpu-stall] madeira-bcd on: a command buffer unfinished 1500 ms after its commit is named once per stall (MADEIRA_GPU_STALL_MS)",
              out + err)
        seg = err[:err.index("--- table")]
        named = [l for l in seg.splitlines() if "unfinished" in l and "#" in l]
        check("under the limit nothing; past it the oldest buffer named once, with its state and the counts",
              "--- 1.2 s\n[gpu-stall] madeira-bcd #1: command buffer 1 unfinished 1700 ms after its commit "
              "(scheduled: on the GPU or waiting for it); 3 in flight, 1 of them scheduled, 0 not tracked" in seg
              and len(named) == 2, seg)
        check("when the named buffer completes: how long until it was scheduled and how long the GPU ran it; another buffer's end prints nothing",
              "[gpu-stall] madeira-bcd command buffer 1 done 2500 ms after its commit: 200 ms until it was scheduled, the GPU ran it 250 ms (status 4)" in seg
              and "command buffer 2 done" not in seg, seg)
        check("the next stall: the oldest left, never scheduled; its end says what is unknown instead of a number",
              "[gpu-stall] madeira-bcd #2: command buffer 3 unfinished 2800 ms after its commit (committed, not yet scheduled); 1 in flight, 0 of them scheduled" in seg
              and "[gpu-stall] madeira-bcd command buffer 3 done 2800 ms after its commit: no scheduled time, no GPU time (status 4)" in seg
              and seg.count(" done ") == 2, seg)
        tab = err[err.index("--- table"):]
        check("256 slots: the 257th buffer is counted, not tracked", "untracked=1" in out and "1 not tracked" in tab, tab[-600:] + out)

print("PASS: gpu stall" if ok else "FAIL: gpu stall")
sys.exit(0 if ok else 1)
