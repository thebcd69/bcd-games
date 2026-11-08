#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The view lists' lock is held until the draw or dispatch is encoded (upstream Madeira ml1302, b1386ac),
opt-in d3d12-hold-view-lists = 1; no GPU, no Wine.

While the device has 256 SRV views or fewer (loading screens, any device without a residency set) every draw and
dispatch also declares every resource in the device's SRV and UAV lists resident. The lists are read under list_lock
(shared), but the lock was released before the commands were encoded, while res_Release on another thread takes it
exclusively, removes the resource and releases its Metal objects. D3D12 lets a loader thread free a texture the command
list never uses, so useResource could meet a dead object (Final Fantasy Tactics - The Ivalice Chronicles, AGX's
objc_retain, loading screen). With the switch on: exec_draw reads the lists right before the final encode (after every
early return) and keeps the lock until the chain has reached Metal (an encode-batched chain is queued, so it is sent at
once); exec_dispatch releases after the encode. Off, the lock is released where it always was.

1. Source, key off: the early read in exec_draw is the old block byte for byte (inside `!ml1302_hold`), and exec_dispatch
   releases at its old place.
2. Source, key on: one acquire in exec_draw's new block and one release after the encode; no return or goto between them;
   the same for exec_dispatch (acquire at its old place, release after the encode).
3. The new block of exec_draw is cut out and compiled on the host with fakes: the declarations are chained right after the
   pipeline command and before the draw commands, in SRV then UAV order, duplicates the encoder already has are dropped, 256
   at most, the tail moves when the chain was only the pipeline command, the lock is held on exit and the entries count
   is added to the encode-split counter.
4. CI runs this check.
SKIP (exit 0) of the behaviour part without a C++ compiler.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


def cut(text, a, b):
    i = text.index(a)
    return text[i:text.index(b, i)]


def code_only(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


OLD_BLOCK = '''    if (!ml1060_skip_lists && !ml1302_hold) {
        AcquireSRWLockShared(&dev->list_lock);   /* views are created on other threads */
        for (i = 0; i < dev->nsrv && nur < 256; i++) {
            struct mad_resource *r = dev->srv_res[i];
            if (r) MAD_USE(r->texture ? r->texture : r->buffer);
        }
        for (i = 0; i < dev->nuav && nur < 256; i++) {
            struct mad_resource *r = dev->uav_res[i];
            if (r) MAD_USE_U(r->texture ? r->texture : r->buffer, WMTResourceUsageRead | WMTResourceUsageWrite);
        }
        ReleaseSRWLockShared(&dev->list_lock);
    }
'''
draw = cut(src, "static void exec_draw(struct mad_exec *e, const struct mad_cmd *c) {", "\n}\n")
disp_start = src.index("    const int ml1060_cskip = dev->resset && dev->nsrv > 256;")
disp = src[disp_start:src.index("\n}\n", disp_start)]

# ---- 1. key off
check("the key is read once, default 0",
      'on = mad_cfg_int_pe("d3d12-hold-view-lists", 0) ? 1 : 0;' in src and src.count('"d3d12-hold-view-lists"') == 1)
check("exec_draw, key off: the early list read is the old block, unchanged (only guarded by the switch)", OLD_BLOCK in draw)
check("exec_draw: the switch is evaluated only when the lists are used at all (not with a residency set over 256 views)",
      "const int ml1302_hold = !ml1060_skip_lists && mad_hold_view_lists_on();" in draw)
check("exec_dispatch, key off: acquire and release are where they were (the release only waits for the switch)",
      "if (!ml1060_cskip) AcquireSRWLockShared(&dev->list_lock);   /* madeira-bcd */" in disp
      and "    if (!ml1060_cskip && !ml1302_chold) ReleaseSRWLockShared(&dev->list_lock);\n    InterlockedExchangeAdd(&g_es_use_n, (LONG)nur);" in disp)

# ---- 2. key on
new_block = cut(draw, "    if (ml1302_hold) {", "    {   /* ml1088: count this draw into the current slot while a query is open.")
after = draw[draw.index("    if (ml1302_held) {"):]
after = after[:after.index("mad_use_commit(e);")]
check("exec_draw, key on: one acquire in the new block, after every early return of the function (nothing in it returns)",
      new_block.count("AcquireSRWLockShared(&dev->list_lock)") == 1 and "ml1302_held = 1;" in new_block
      and not re.search(r"\b(return|goto)\b", code_only(new_block)))
rest = draw[draw.index("    {   /* ml1088: count this draw into the current slot while a query is open."):draw.index("    if (ml1302_held) {")]
check("exec_draw, key on: from the new block to the release there is no return or goto",
      not re.search(r"\b(return|goto)\b", code_only(rest)))
check("exec_draw, key on: the release comes after the chain reached Metal (and after the batch flush), before the use list is committed",
      "mad_eb_flush_cur(EBF_OTHER);\n        ReleaseSRWLockShared(&dev->list_lock);" in after
      and draw.count("ReleaseSRWLockShared(&dev->list_lock)") == 2
      and draw.index("MTLRenderCommandEncoder_encodeCommands(e->renc, (const struct wmtcmd_base *)&c_pso);") < draw.index("    if (ml1302_held) {"))
check("exec_draw: the hold covers the same lists as before (SRV, then UAV with write usage)",
      "dev->srv_res[i]" in new_block and "dev->uav_res[i]" in new_block and "WMTResourceUsageRead | WMTResourceUsageWrite" in new_block)
tail_disp = disp[disp.index("MTLComputeCommandEncoder_encodeCommands(e->cenc, (const struct wmtcmd_base *)&c_pso);"):]
tail_disp = tail_disp[:tail_disp.index("mad_use_commit(e);")]
between = disp[disp.index("if (!ml1060_cskip) AcquireSRWLockShared"):disp.index("MTLComputeCommandEncoder_encodeCommands(e->cenc")]
check("exec_dispatch, key on: held from the list read to the encode (no return or goto between), released after it and after the batch flush",
      not re.search(r"\b(return|goto)\b", code_only(between)) and "if (ml1302_chold) {" in tail_disp
      and "mad_eb_flush_cur(EBF_OTHER);\n        ReleaseSRWLockShared(&dev->list_lock);" in tail_disp
      and disp.count("ReleaseSRWLockShared(&dev->list_lock)") == 2)

# ---- 3. the new block, compiled with fakes
cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
if not cxx:
    print("SKIP: no C++ compiler for the behaviour part")
else:
    harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <vector>
#include <set>
#include <utility>
typedef int32_t LONG; typedef uint32_t UINT32; typedef uint64_t obj_handle_t;
struct ptr_t { void *ptr; };
struct wmtcmd_base { uint32_t type; ptr_t next; };
enum { WMTRenderCommandUseResource = 7, WMTRenderCommandSetPSO = 1, WMTRenderCommandDraw = 2 };
enum WMTResourceUsage { WMTResourceUsageRead = 1, WMTResourceUsageWrite = 2 };
enum WMTRenderStages { WMTRenderStageVertex = 1, WMTRenderStageFragment = 2 };
struct wmtcmd_render_useresource : wmtcmd_base { obj_handle_t resource; enum WMTResourceUsage usage; enum WMTRenderStages stages; };
struct wmtcmd_render_setpso : wmtcmd_base {};
struct mad_resource { obj_handle_t texture, buffer; };
struct mad_device { unsigned nsrv, nuav; mad_resource *srv_res[300]; mad_resource *uav_res[300]; int lock_state; int acquires; };
struct mad_exec { obj_handle_t renc; std::set<std::pair<obj_handle_t, int>> seen; };
static volatile LONG g_es_use_n;
static LONG InterlockedExchangeAdd(volatile LONG *p, LONG v) { LONG o = *p; *p += v; return o; }
static void AcquireSRWLockShared(int *l) { (*l)++; }
static bool mad_use_seen(mad_exec *e, obj_handle_t enc, obj_handle_t h, UINT32 usage, UINT32 stages) {
  auto k = std::make_pair(h, (int)usage); if (e->seen.count(k)) return true; e->seen.insert(k); return false;
}
/* the block under test, in a function with the names exec_draw gives it */
static unsigned run(mad_exec *e, mad_device *dev, wmtcmd_render_setpso &c_pso, wmtcmd_base *&tail, wmtcmd_render_useresource *ur, unsigned &nur,
                    unsigned &i, const UINT32 use_stages, bool ml1302_hold, int &ml1302_held) {
''' + new_block.replace("AcquireSRWLockShared(&dev->list_lock)", "AcquireSRWLockShared(&dev->lock_state)") + r'''
  return nur;
}
static int fails = 0;
static void expect(bool c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
int main() {
  static mad_resource rs[300], us[300];
  for (int i = 0; i < 300; i++) { rs[i].texture = 1000 + i; rs[i].buffer = 0; us[i].texture = 0; us[i].buffer = 5000 + i; }
  {
    mad_device dev{}; dev.nsrv = 3; dev.nuav = 2;
    for (int i = 0; i < 3; i++) dev.srv_res[i] = &rs[i];
    for (int i = 0; i < 2; i++) dev.uav_res[i] = &us[i];
    mad_exec e{}; e.renc = 9;
    wmtcmd_render_setpso c_pso{}; wmtcmd_render_useresource ur[256]{}; unsigned nur = 1, i = 0; int held = 0;
    wmtcmd_base draw{}; draw.type = WMTRenderCommandDraw; c_pso.type = WMTRenderCommandSetPSO; c_pso.next.ptr = &draw;
    wmtcmd_base *tail = &draw;
    ur[0].type = WMTRenderCommandUseResource; ur[0].resource = 77;   /* an entry the draw itself added earlier */
    run(&e, &dev, c_pso, tail, ur, nur, i, WMTRenderStageVertex | WMTRenderStageFragment, true, held);
    std::vector<uint64_t> order; wmtcmd_base *p = (wmtcmd_base *)c_pso.next.ptr; bool ends_at_draw = false;
    for (; p; p = (wmtcmd_base *)p->next.ptr) { if (p == &draw) { ends_at_draw = true; break; } order.push_back(((wmtcmd_render_useresource *)p)->resource); }
    expect(held == 1 && dev.lock_state == 1, "the lock was taken once and is held on exit");
    expect(order.size() == 5 && order[0] == 1000 && order[1] == 1001 && order[2] == 1002 && order[3] == 5000 && order[4] == 5001 && ends_at_draw,
           "the declarations follow the pipeline command, SRVs then UAVs, and the draw command comes last");
    expect(tail == &draw && nur == 6 && g_es_use_n == 5, "the tail stays on the draw; the counter got the 5 entries");
    expect(ur[3].usage == WMTResourceUsageRead && ur[4].usage == (WMTResourceUsageRead | WMTResourceUsageWrite) == 0 || true, "");
    expect(((wmtcmd_render_useresource *)c_pso.next.ptr)->usage == WMTResourceUsageRead && ur[nur - 1].usage == (enum WMTResourceUsage)(WMTResourceUsageRead | WMTResourceUsageWrite),
           "SRVs are declared for read, UAVs for read and write");
    expect(ur[1].stages == (WMTRenderStageVertex | WMTRenderStageFragment), "the stages are the draw's");
  }
  {   /* the chain is only the pipeline command: the tail follows the list */
    mad_device dev{}; dev.nsrv = 2; for (int i = 0; i < 2; i++) dev.srv_res[i] = &rs[i];
    mad_exec e{}; wmtcmd_render_setpso c_pso{}; wmtcmd_render_useresource ur[256]{}; unsigned nur = 0, i = 0; int held = 0;
    wmtcmd_base *tail = (wmtcmd_base *)&c_pso;
    run(&e, &dev, c_pso, tail, ur, nur, i, 3, true, held);
    expect(tail == (wmtcmd_base *)&ur[1] && c_pso.next.ptr == &ur[0] && ur[1].next.ptr == nullptr, "a chain of only the pipeline command: the tail moves to the last declaration");
  }
  {   /* the encoder already has some */
    mad_device dev{}; dev.nsrv = 3; for (int i = 0; i < 3; i++) dev.srv_res[i] = &rs[i];
    mad_exec e{}; e.seen.insert({1001, WMTResourceUsageRead});
    wmtcmd_render_setpso c_pso{}; wmtcmd_render_useresource ur[256]{}; unsigned nur = 0, i = 0; int held = 0; wmtcmd_base d{}; c_pso.next.ptr = &d; wmtcmd_base *tail = &d;
    run(&e, &dev, c_pso, tail, ur, nur, i, 3, true, held);
    expect(nur == 2 && ((wmtcmd_render_useresource *)c_pso.next.ptr)->resource == 1000 && ((wmtcmd_render_useresource *)((wmtcmd_base *)c_pso.next.ptr)->next.ptr)->resource == 1002,
           "a resource the encoder already holds is not declared again");
  }
  {   /* 256 at most; the lock is still taken */
    mad_device dev{}; dev.nsrv = 300; for (int i = 0; i < 300; i++) dev.srv_res[i] = &rs[i];
    mad_exec e{}; wmtcmd_render_setpso c_pso{}; wmtcmd_render_useresource ur[256]{}; unsigned nur = 10, i = 0; int held = 0; wmtcmd_base d{}; c_pso.next.ptr = &d; wmtcmd_base *tail = &d;
    run(&e, &dev, c_pso, tail, ur, nur, i, 3, true, held);
    expect(nur == 256 && held == 1, "never more than 256 entries in all");
  }
  {   /* nothing viewed */
    mad_device dev{}; mad_exec e{}; wmtcmd_render_setpso c_pso{}; wmtcmd_render_useresource ur[256]{}; unsigned nur = 0, i = 0; int held = 0; wmtcmd_base d{}; c_pso.next.ptr = &d; wmtcmd_base *tail = &d;
    run(&e, &dev, c_pso, tail, ur, nur, i, 3, true, held);
    expect(c_pso.next.ptr == &d && held == 1 && dev.lock_state == 1, "no views: the chain is untouched, the lock is still held for the encode");
  }
  return fails;
}
'''
    # keep only the tests that mean something
    harness = harness.replace('    expect(ur[3].usage == WMTResourceUsageRead && ur[4].usage == (WMTResourceUsageRead | WMTResourceUsageWrite) == 0 || true, "");\n', "")
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "h.cpp").write_text(harness)
        flags = ["-std=c++17", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-parameter", "-Wno-parentheses"]
        b = subprocess.run([cxx, *flags, "-fsanitize=address,undefined", "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
        if b.returncode:
            b = subprocess.run([cxx, *flags, "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
        check("the new block of exec_draw compiles on the host with fakes", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-3500:])
        else:
            r = subprocess.run([str(t / "h")], capture_output=True, text=True)
            print(r.stdout.rstrip())
            if r.returncode:
                print(r.stderr[-1500:])
            check("the block builds the chain as described", r.returncode == 0)

check("this check runs in the workflow", "tests/host/check-d3d12-hold-view-lists.py" in wf)
print("FAILED: hold view lists" if failures else "PASS: hold view lists")
sys.exit(1 if failures else 0)
