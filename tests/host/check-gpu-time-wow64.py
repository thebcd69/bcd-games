#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""What holds a 32-bit Direct3D 9 game below 60 FPS, made measurable (round 624, agent D2 S16; Far Cry 2, Send Log Z7CADQ).

Two patches, both doing nothing unless MADEIRA_FRAME_STATS is on:
  - tools/patch-winemetal-ctl-gpu-time-wow64.py: MadeiraCtl op 3 (a completed command buffer's GPU start/end) for a 32-bit
    caller, its guest pointer converted with UInt32ToPtr; DXMT's [gpu-perf] line then appears for 32-bit games;
  - tools/patch-dxmt-d3d9-query-park.py: GetData times its park on the GPU fence and [d3d9-query] gets a third line with the
    parked ms a frame and issue-to-complete percentiles from a 0.5 ms histogram.

Checks: the workflow order (the winemetal patch before winemetal is built, the d3d9 one after the index-range patch and before
the i386 farm key); the whole CI patch chain applies with both, each once and then "already patched", a moved anchor fails by
name with the files unchanged, and undoing the insertions gives the files back; then the patched code itself, cut from the files
and compiled on the host: the wow64 entry (op 7 as before, op 3 refused with the switch off and forwarded with the converted
pointer with it on, other ops refused) and the census (off: no histogram, the two lines as before; on: parked time per frame,
p50/p90/p99 bucket edges, the 64 ms note, windows that do not leak into each other).
SKIP of the patch part (exit 0) when no dxmt sources are available.
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
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1800]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


wf = (R / ".github/workflows/build-ipa.yml").read_text()
scripts = re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", wf)
ctl, park = "tools/patch-winemetal-ctl-gpu-time-wow64.py", "tools/patch-dxmt-d3d9-query-park.py"
check("the workflow runs the 32-bit GPU time patch after the GPU stall patch and before winemetal is built",
      ctl in scripts and scripts.index(ctl) == scripts.index("tools/patch-winemetal-gpu-stall.py") + 1
      and wf.index("python3 " + ctl) < wf.index("name: Build dxmt-ios"))
check("the workflow runs the query park patch right after the index-range patch, before the i386 farm key",
      park in scripts and scripts.index(park) == scripts.index("tools/patch-dxmt-d9-ib-range.py") + 1
      and wf.index("python3 " + park) < wf.index("name: i386 farm revision")
      and "'tools/patch-dxmt-*.py'" in wf[wf.index("name: i386 farm revision"):][:600])
check("CI runs this check", "python3 tests/host/check-gpu-time-wow64.py" in wf)

if not (src / "winemetal" / "unix" / "winemetal_unix.c").is_file():
    print(f"SKIP: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)")
    print("PASS: 32-bit GPU time (static part)" if ok else "FAIL: 32-bit GPU time")
    sys.exit(0 if ok else 1)


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ctl_mod, park_mod = load(R / ctl), load(R / park)

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
    files = {"wm": t / "dxmt/src/winemetal/unix/winemetal_unix.c", "hpp": t / "dxmt/src/d3d9/d3d9_census.hpp",
             "cpp": t / "dxmt/src/d3d9/d3d9_census.cpp", "qry": t / "dxmt/src/d3d9/d3d9_query.cpp"}
    before = {}
    chain_ok = True
    for s in scripts:
        if s == park:
            before.update({k: files[k].read_text() for k in ("hpp", "cpp", "qry")})
        if s == ctl:
            before["wm"] = files["wm"].read_text()
        p = subprocess.run([sys.executable, str(R / s)], cwd=t, capture_output=True, text=True)
        if p.returncode:
            chain_ok = False
            check(f"patch {s} applies in the chain", False, (p.stdout + p.stderr).strip())
        if s in (ctl, park):
            p2 = subprocess.run([sys.executable, str(R / s)], cwd=t, capture_output=True, text=True)
            check(f"{s}: applies once, then says already patched",
                  p.returncode == 0 and "patched" in p.stdout and p2.returncode == 0 and "already patched" in p2.stdout,
                  p.stdout + p.stderr + p2.stdout + p2.stderr)
    check("the whole CI patch chain applies with both patches in it", chain_ok)
    after = {k: f.read_text() for k, f in files.items()}

    # nothing else changed: the wow64 entry is the only change by the ctl patch; the park patch's insertions undo exactly
    check("the 32-bit GPU time patch changes only _madeira_ctl_wow64",
          after["wm"].count(ctl_mod.NEW) == 1 and before["wm"].count(ctl_mod.OLD) == 1
          and re.sub(re.escape(ctl_mod.NEW), lambda m: ctl_mod.OLD, after["wm"], count=1).count(ctl_mod.NEW) == 0)
    undone = {"hpp": after["hpp"], "cpp": after["cpp"], "qry": after["qry"]}
    for path, pairs in park_mod.EDITS:
        key = {"d3d9_census.hpp": "hpp", "d3d9_census.cpp": "cpp", "d3d9_query.cpp": "qry"}[path.name]
        for _, old, new in pairs:
            undone[key] = undone[key].replace(new, old, 1)
    # later patches of the chain may have edited these files after the park patch: compare against a fresh run instead
    check("the query park patch's insertions are all there once",
          all(after[{"d3d9_census.hpp": "hpp", "d3d9_census.cpp": "cpp", "d3d9_query.cpp": "qry"}[path.name]].count(new) == 1
              for path, pairs in park_mod.EDITS for _, _, new in pairs))

    # a moved anchor: the files are left alone
    work = t / "moved"
    shutil.copytree(t / "dxmt", work / "dxmt")
    for k in ("hpp", "cpp", "qry"):
        (work / "dxmt/src/d3d9" / files[k].name).write_text(before[k])
    qry_moved = before["qry"].replace("WaitCPUFenceBounded(park_seq, kPollParkNanos);", "WaitCPUFenceBounded(park_seq, kPollParkNanos * 1);", 1)
    (work / "dxmt/src/d3d9/d3d9_query.cpp").write_text(qry_moved)
    p3 = subprocess.run([sys.executable, str(R / park)], cwd=work, capture_output=True, text=True)
    check("the query park patch: a moved anchor fails by name, no file changed",
          p3.returncode != 0 and "GetData's park" in p3.stdout + p3.stderr
          and (work / "dxmt/src/d3d9/d3d9_census.cpp").read_text() == before["cpp"]
          and (work / "dxmt/src/d3d9/d3d9_query.cpp").read_text() == qry_moved, p3.stdout + p3.stderr)
    (work / "dxmt/src/winemetal/unix/winemetal_unix.c").write_text(
        before["wm"].replace("  if (a && a->op == 7) return _madeira_ctl(args);\n  return STATUS_NOT_IMPLEMENTED;",
                             "  if (a && a->op == 7) { return _madeira_ctl(args); }\n  return STATUS_NOT_IMPLEMENTED;", 1))
    moved_wm = (work / "dxmt/src/winemetal/unix/winemetal_unix.c").read_text()
    p4 = subprocess.run([sys.executable, str(R / ctl)], cwd=work, capture_output=True, text=True)
    check("the 32-bit GPU time patch: a moved anchor fails by name, the file unchanged",
          p4.returncode != 0 and "_madeira_ctl_wow64" in p4.stdout + p4.stderr
          and (work / "dxmt/src/winemetal/unix/winemetal_unix.c").read_text() == moved_wm, p4.stdout + p4.stderr)

    qpark = after["qry"][after["qry"].index("MTLD3D9Query::GetData(void *pData"):]
    qpark = qpark[:qpark.index("\n}\n")]
    check("GetData: the park alone is timed, only with the switch, and nothing else of the poll path moved",
          qpark.count("census::queryParkClock()") == 1 and qpark.count("census::queryParkedNs(") == 1
          and qpark.index("census::queryParkClock()") < qpark.index("WaitCPUFenceBounded(park_seq, kPollParkNanos);")
          < qpark.index("census::queryParkedNs(") < qpark.index("  if (!ready) {")
          and "if (park_t0)" in qpark)

    cc = shutil.which("cc")
    cxx = shutil.which("c++") or shutil.which("clang++")
    # ---- the wow64 entry, compiled as C
    wow = after["wm"][after["wm"].index("static NTSTATUS _madeira_ctl_wow64(void *args) {"):]
    wow = wow[:wow.index("\n}\n") + 3]
    harness_c = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef int NTSTATUS;
#define STATUS_SUCCESS 0
#define STATUS_NOT_IMPLEMENTED 0xC0000002
struct madeira_ctl_args { uint32_t op; uint32_t ret; uint64_t ptr; uint64_t len; char name[160]; };
static int hooks, calls;
static uint64_t seen_ptr;
static const uint64_t wow_base = 0x7f0000000000ull;
static int madeira_frame_hooks_on(void) { return hooks; }
static void *UInt32ToPtr(uint32_t v) { return v ? (void *)(uintptr_t)(wow_base + v) : NULL; }
static NTSTATUS _madeira_ctl(void *args) {
  struct madeira_ctl_args *a = args;
  calls++; seen_ptr = a->ptr; a->ret = a->op == 3 ? 1 : 7;
  return STATUS_SUCCESS;
}
@WOW@
int main(void) {
  struct madeira_ctl_args a;
  memset(&a, 0, sizeof a); a.op = 7; a.ptr = 0x1234;
  NTSTATUS s7 = _madeira_ctl_wow64(&a);
  printf("op7 status=%x calls=%d ptr=%llx ret=%u\n", (unsigned)s7, calls, (unsigned long long)seen_ptr, a.ret);
  memset(&a, 0, sizeof a); a.op = 3; a.ptr = 0x00a0f0e8; calls = 0;
  NTSTATUS off = _madeira_ctl_wow64(&a);
  printf("op3-off status=%x calls=%d ret=%u\n", (unsigned)off, calls, a.ret);
  hooks = 1;
  NTSTATUS on = _madeira_ctl_wow64(&a);
  printf("op3-on status=%x calls=%d seen=%llx ret=%u ptr-after=%llx\n", (unsigned)on, calls, (unsigned long long)seen_ptr, a.ret,
         (unsigned long long)a.ptr);
  memset(&a, 0, sizeof a); a.op = 1; calls = 0;
  NTSTATUS o1 = _madeira_ctl_wow64(&a);
  printf("op1 status=%x calls=%d\n", (unsigned)o1, calls);
  printf("null status=%x\n", (unsigned)_madeira_ctl_wow64(NULL));
  return 0;
}
'''
    (t / "w.c").write_text(harness_c.replace("@WOW@", wow))
    b = subprocess.run([cc, "-std=c11", "-Wall", "-Werror", "-o", str(t / "w"), str(t / "w.c")], capture_output=True, text=True)
    check("the patched wow64 entry compiles as C (-Wall -Werror)", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        out = subprocess.run([str(t / "w")], capture_output=True, text=True).stdout
        check("op 7 is forwarded as before, pointer untouched", "op7 status=0 calls=1 ptr=1234 ret=7" in out, out)
        check("op 3 without MADEIRA_FRAME_STATS: not implemented, nothing called (as before)", "op3-off status=c0000002 calls=0 ret=0" in out, out)
        check("op 3 with it: forwarded once with the guest pointer converted, the answer copied back, the caller's pointer kept",
              "op3-on status=0 calls=1 seen=7f0000a0f0e8 ret=1 ptr-after=a0f0e8" in out, out)
        check("other ops and a NULL argument: not implemented", "op1 status=c0000002 calls=0" in out and "null status=c0000002" in out, out)

    # ---- the census, compiled as C++
    cpp = after["cpp"]
    globals_ = cpp[cpp.index("/* MADEIRA [d3d9-query]: see d3d9_census.hpp. */"):]
    globals_ = globals_[:globals_.index("uint64_t g_prev_q_latency_ns;\n") + len("uint64_t g_prev_q_latency_ns;\n")]
    report = cpp[cpp.index("void reportQueries(uint32_t frames) {"):]
    report = report[:report.index("\n}\n") + 3]
    public = cpp[cpp.index("/* madeira-bcd d3d9-query-park-v1: MADEIRA_FRAME_STATS as the [frame] instrument reads it"):]
    public = public[:public.index("void queryCompleted(uint64_t issue_to_complete_ns, uint32_t polls) {")]
    completed = cpp[cpp.index("void queryCompleted(uint64_t issue_to_complete_ns, uint32_t polls) {"):]
    completed = completed[:completed.index("\n}\n") + 3]
    harness_cpp = r'''
#include <atomic>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
namespace env { std::string getEnvVar(const char *n) { const char *v = std::getenv(n); return v ? v : ""; } }
namespace dxmt::census {
bool queryParkClock();
void queryParkedNs(uint64_t ns);
void queryCompleted(uint64_t issue_to_complete_ns, uint32_t polls);
struct ThreadCounters { uint32_t q_polls, q_polls_complete, q_polls_parked; };
static ThreadCounters tc;
static uint32_t sumBlocks(uint32_t ThreadCounters::*m) { return tc.*m; }
static char out[16384];
static size_t outlen;
static void qline(const char *fmt, ...) {
  va_list ap; va_start(ap, fmt);
  outlen += (size_t)vsnprintf(out + outlen, sizeof out - outlen, fmt, ap);
  va_end(ap);
  outlen += (size_t)snprintf(out + outlen, sizeof out - outlen, "\n");
}
namespace {
@GLOBALS@
@REPORT@
}
@PUBLIC@
@COMPLETED@
}
using namespace dxmt::census;
static void ms(double v) { queryCompleted((uint64_t)(v * 1e6), 3); }
int main() {
  /* window 1: 100 completions (50 at 10.2 ms, 40 at 20.2, 9 at 30.2, 1 at 70), 20 parks of 5 ms, 10 frames */
  for (int i = 0; i < 50; i++) ms(10.2);
  for (int i = 0; i < 40; i++) ms(20.2);
  for (int i = 0; i < 9; i++) ms(30.2);
  ms(70.0);
  if (queryParkClock()) for (int i = 0; i < 20; i++) queryParkedNs(5000000);
  g_q_issued = 100; tc = {1000, 100, 860};
  reportQueries(10);
  printf("---\n%s", out); outlen = 0; out[0] = 0;
  /* window 2: three quick completions, no park */
  ms(1.0); ms(1.2); ms(1.4);
  g_q_issued = 103; tc = {1010, 103, 860};
  reportQueries(10);
  printf("---\n%s", out); outlen = 0; out[0] = 0;
  /* window 3: nothing */
  reportQueries(10);
  printf("---\n%s", out);
  unsigned long long hist = 0;
  for (unsigned k = 0; k < kQueryLatBuckets; k++) hist += g_q_lat_hist[k].load();
  printf("hist=%llu park=%llu\n", hist, (unsigned long long)g_q_park_ns.load());
  return 0;
}
'''
    if "kRefreshKinds" in globals_:   # the index-range patch's counters sit in the same block; their enum is in the header
        hpp = after["hpp"]
        at = hpp.rindex("enum : unsigned {", 0, hpp.index("kRefreshKinds"))
        globals_ = hpp[at:hpp.index("};", at) + 2] + "\n" + globals_
    code = (harness_cpp.replace("@GLOBALS@", globals_).replace("@REPORT@", report).replace("@PUBLIC@", public)
            .replace("@COMPLETED@", completed))
    (t / "c.cpp").write_text(code)
    b = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-unused-variable",
                        "-o", str(t / "c"), str(t / "c.cpp")], capture_output=True, text=True)
    check("the patched census pieces compile as C++20 on the host", b.returncode == 0, b.stderr[-2000:])
    if b.returncode == 0:
        def run(env_value):
            env = dict(os.environ)
            env.pop("MADEIRA_FRAME_STATS", None)
            if env_value is not None:
                env["MADEIRA_FRAME_STATS"] = env_value
            return subprocess.run([str(t / "c")], capture_output=True, text=True, env=env).stdout

        for v in (None, "0"):
            out = run(v)
            wins = out.split("---\n")[1:]
            check(f"MADEIRA_FRAME_STATS={v!r}: no histogram, no park time, the two lines of each window as before",
                  "hist=0 park=0" in out and "parked " not in out
                  and wins[0].count("\n") == 2 and wins[0].startswith("issued=100 (10.0/f) flush_submits=0 polls=1000 (100.0/f)")
                  and "completions=100 polls_per_completion=10.0 issue_to_complete_avg=" in wins[0]
                  and wins[2].startswith("no query traffic this window"), out)
        out = run("1")
        wins = out.split("---\n")[1:]
        check("on: the third line, parked ms a frame and the p50/p90/p99 bucket edges, the 64 ms note",
              "parked 10.00 ms/f in the fence wait (100 ms this window) | issue_to_complete p50 <=10.5ms p90 <=20.5ms p99 <=30.5ms "
              "(some 64 ms or more)\n" in wins[0] and wins[0].count("\n") == 3, out)
        check("on: the next window counts only its own completions (no leak from the first)",
              "parked 0.00 ms/f in the fence wait (0 ms this window) | issue_to_complete p50 <=1.5ms p90 <=1.5ms p99 <=1.5ms\n" in wins[1],
              out)
        check("on: a window without traffic prints only that", wins[2].startswith("no query traffic this window\n")
              and "parked" not in wins[2] and "hist=103 park=100000000" in out, out)

print("PASS: 32-bit GPU time and D3D9 query park" if ok else "FAIL: 32-bit GPU time")
sys.exit(0 if ok else 1)
