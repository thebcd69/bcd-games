#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""One encoder per draw in an attachment-less pass (upstream Madeira ml1311, 85d475f), opt-in attachless-split = 1;
no GPU, no Wine.

Final Fantasy Tactics - The Ivalice Chronicles' enhanced mode composites its UI layers with draws that read back and rewrite
one UAV in a render pass with no colour target and no depth, with no barrier between them. On Apple GPUs the fragment work
of consecutive draws in one pass overlaps unless the shader uses raster order groups, so blocks of the highlighted
buttons and dialogue boxes went missing. With `attachless-split = 1` (bcd.cfg or the game's file) every draw of an
attachment-less pass gets its own encoder (the encoder fence chain orders the next one after it). Off by default: Nanite-style
passes run many UAV-atomic draws that need no order. The count joins the ml1091 summary line.

1. Source: the key is read once, default 0; the split sits in exec_draw after the skin-check note and right before the
   pass is begun; the line of the summary names the count.
2. The gate is cut out and compiled on the host with fakes: key off: no encoder is ended; key on: the second and later
   draws of an attachment-less pass end the open encoder (counted), the first draw of a pass, a pass with a colour target or a
   depth target (open or about to be bound) and a draw with no open pass are untouched.
3. CI runs this check.
"""
from pathlib import Path
import os
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


gate = cut(src, "    if (g_attachless_split < 0) {", "    if (!exec_begin_render(e)) { MAD_SKIP(e); return; }")
check("the key is read once, default 0, logged when on",
      'g_attachless_split = mad_cfg_int_pe("attachless-split", 0) ? 1 : 0;' in gate and src.count('"attachless-split"') == 1
      and "static int g_attachless_split = -1;" in src)
draw = cut(src, "static void exec_draw(struct mad_exec *e, const struct mad_cmd *c) {", "\n}\n")
check("the split is in exec_draw after the skin-check note and right before the pass is begun",
      draw.index("if (g_skin_check > 0) mad_skin_draw(e, c);") < draw.index("if (g_attachless_split < 0)") < draw.index("exec_end(e);")
      < draw.index("if (!exec_begin_render(e)) { MAD_SKIP(e); return; }"))
check("the ml1091 summary line names the count", "ml1311 attachment-less passes split %ld" in src and "g_barrier_renc_closed, g_attachless_split_n, g_zero_inst" in src)
check("this check runs in the workflow", "tests/host/check-d3d12-attachless-split.py" in wf)

cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
if not cxx:
    print("SKIP: no C++ compiler for the behaviour part")
else:
    harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string>
typedef int32_t LONG; typedef uint64_t obj_handle_t; typedef uint32_t UINT;
struct mad_resource {};
struct mad_exec { obj_handle_t renc; UINT enc_nrt; mad_resource *enc_depth; UINT nrt; mad_resource *depth; unsigned pass_draws; int ended; };
static int g_attachless_split = -1; static LONG g_attachless_split_n;
static std::string g_log; static int g_cfg;
static long long mad_cfg_int_pe(const char *k, long long d) { return k[0] == 'a' ? g_cfg : d; }
static void d3d12_log(const char *f, ...) { g_log += f; }
static LONG InterlockedIncrement(volatile LONG *p) { return ++*p; }
static void exec_end(mad_exec *e) { e->ended++; e->renc = 0; e->pass_draws = 0; }
static void gate(mad_exec *e) {
''' + gate + r'''}
static int fails = 0;
static void expect(bool c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
int main(int argc, char **argv) {
  g_cfg = argv[1][0] == '1';
  static mad_resource r;
  mad_exec e = {7, 0, nullptr, 0, nullptr, 3, 0};
  gate(&e);
  if (!g_cfg) { expect(e.ended == 0 && g_attachless_split_n == 0 && g_log.empty(), "key off: the open pass is left alone, nothing logged"); return fails; }
  expect(e.ended == 1 && g_attachless_split_n == 1 && g_log.find("attachless-split = 1") != std::string::npos, "key on: the third draw of an attachment-less pass ends the encoder (counted, logged)");
  mad_exec first = {7, 0, nullptr, 0, nullptr, 0, 0}; gate(&first);
  expect(first.ended == 0, "the first draw of a pass (no draw in it yet): untouched");
  mad_exec rt = {7, 1, nullptr, 1, nullptr, 3, 0}; gate(&rt);
  expect(rt.ended == 0, "a pass with a colour target: untouched");
  mad_exec dp = {7, 0, &r, 0, &r, 3, 0}; gate(&dp);
  expect(dp.ended == 0, "a pass with a depth target: untouched");
  mad_exec rebind = {7, 0, nullptr, 1, nullptr, 3, 0}; gate(&rebind);
  expect(rebind.ended == 0, "a draw that binds a target the open pass does not have: untouched (the normal path ends it)");
  mad_exec none = {0, 0, nullptr, 0, nullptr, 3, 0}; gate(&none);
  expect(none.ended == 0 && g_attachless_split_n == 1, "no open pass: untouched");
  return fails;
}
'''
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "h.cpp").write_text(harness)
        flags = ["-std=c++17", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-missing-field-initializers"]
        b = subprocess.run([cxx, *flags, "-fsanitize=address,undefined", "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
        if b.returncode:
            b = subprocess.run([cxx, *flags, "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
        check("the gate compiles on the host with fakes", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-2500:])
        else:
            for arg, what in (("0", "key off"), ("1", "key on")):
                r = subprocess.run([str(t / "h"), arg], capture_output=True, text=True)
                print(r.stdout.rstrip())
                check(f"the gate behaves ({what})", r.returncode == 0)

print("FAILED: attachless-split" if failures else "PASS: attachless-split")
sys.exit(1 if failures else 0)
