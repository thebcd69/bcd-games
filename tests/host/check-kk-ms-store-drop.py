#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Stores to multisampled storage images in KosmicKrisp for X-Plane 12 (build 675; env.MADEIRA_KK_MS_STORE_DROP = 1).

The owner's X-Plane 12 test on build 674 (2026-11-19 19:51, iPhone18,2) stopped on VK_ERROR_INVALID_SHADER_NV from
vkCreateComputePipelines. Build 674's [kk-msl-fail] log (playport patch 0034) showed the refused shader: a compute pass that reads
a multisampled HDR image and, only where a component is infinite, clamps it to +-65504 and writes it back. Metal has no writable
multisampled texture. build/mesa-ios/playport-patches/0035 binds such an image read-only and leaves the stores and fences on it
out, with MADEIRA_KK_MS_STORE_DROP=1 only.

  1. the patch: BCD's header, one file (compiler/nir_to_msl.c), nothing of the emitter taken out; every added branch sits behind
     the key (the texture type, the store, the fence);
  2. the key and the decision (both functions cut out of the patch and compiled on the host with stand-ins): unset, "0", "10",
     " 1" and "" are off, "1" is on and logs [kk-ms-store-drop] once; only a multisampled image's store or fence is left out, the
     first eight left-out stores are logged;
  3. Metal itself (when xcrun metal is here): a kernel of BCD's own that writes a texture2d_ms_array does not compile, the same
     kernel with the image read-only and the store left out does;
  4. the list policy, the catalog, the X-Plane 12 row, the auto-tune rule with its fixture, the patch notes and CI.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
P = R / "build/mesa-ios/playport-patches/0035-kk-drop-stores-to-multisampled-storage-images.patch"
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    if not cond:
        failures += 1


# ---- 1. the patch ------------------------------------------------------------------------------------------------------
text = P.read_text() if P.exists() else ""
check(P.exists(), "the patch is build/mesa-ios/playport-patches/0035-kk-drop-stores-to-multisampled-storage-images.patch")
head, _, body = text.partition("\n---\n")
body = body.split("\n-- \n")[0]   # the signature after "-- " is no part of the diff
check(head.startswith("From: BCD <190844990+thebcd69@users.noreply.github.com>") and "MADEIRA_KK_MS_STORE_DROP=1" in head,
      "BCD's header names the key", head[:300])
files = re.findall(r"^diff --git a/(\S+) b/", body, re.M)
check(files == ["src/kosmickrisp/compiler/nir_to_msl.c"], "one file: src/kosmickrisp/compiler/nir_to_msl.c", files)
removed = [l[1:] for l in body.splitlines() if l.startswith("-") and not l.startswith("---")]
added = [l[1:] for l in body.splitlines() if l.startswith("+") and not l.startswith("+++")]
check(removed == [], "nothing of the emitter is taken out", removed)
helper = "\n".join(added)
m1 = re.search(r"static bool\nkk_ms_store_drop_enabled\(void\)\n\{.*?\n\}\n", helper, re.S)
m2 = re.search(r"static bool\nkk_ms_store_dropped\(nir_intrinsic_instr \*instr\)\n\{.*?\n\}\n", helper, re.S)
m3 = re.search(r"static bool\nkk_ms_image\(nir_intrinsic_instr \*instr\)\n\{.*?\n\}\n", helper, re.S)
check(m1 is not None and m2 is not None and m3 is not None,
      "kk_ms_store_drop_enabled(), kk_ms_image() and kk_ms_store_dropped() are in the patch", helper[:600])
fn1 = m1.group(0) if m1 else ""
fn2 = m2.group(0) if m2 else ""
fn3 = m3.group(0) if m3 else ""
check('getenv("MADEIRA_KK_MS_STORE_DROP")' in fn1 and "s && s[0] == '1' && !s[1]" in fn1 and "static int enabled = -1;" in fn1,
      "the key is read once (a static) and only the value 1 turns it on", fn1)
check("kk_ms_image(instr)" in fn2 and "kk_ms_store_drop_enabled()" in fn2 and "GLSL_SAMPLER_DIM_MS" in fn3
      and "nir_src_as_intrinsic(instr->src[0])" in fn3 and "nir_intrinsic_load_texture_handle_kk" in fn3,
      "only a multisampled image is ever left out, judged by the image handle's dimension (build 675: the fence that "
      "kk_nir_lower_textures adds has none of its own)", fn2 + fn3)
check(re.search(r"\n\+      if \(nir_intrinsic_image_dim\(instr\) == GLSL_SAMPLER_DIM_MS &&\n\+          access\[0\] && "
                r"kk_ms_store_drop_enabled\(\)\) \{\n\+         access = \", access::read\";\n\+         coherent = \"\";\n\+      \}\n",
                body) is not None,
      "the texture type: a multisampled storage image is read-only (no coherence) with the key, untouched without it")
for case in ("nir_intrinsic_bindless_image_store", "nir_intrinsic_bindless_image_fence_kk"):
    blk = body[body.find("   case " + case):][:500]
    check(re.search(r"P_INDENT\(ctx\);\n\+      if \(kk_ms_store_dropped\(instr\)\) \{\n\+         P\(ctx, \"/\* madeira-bcd: .*?\*/\\n\"\);\n"
                    r"\+         break;\n\+      \}\n", blk) is not None,
          f"{case}: left out (a comment in the MSL) only when kk_ms_store_dropped() says so", blk[:400])
check("#include <stdio.h>" in helper and "#include <stdlib.h>" in helper, "nir_to_msl.c gets the headers getenv and fprintf need")

# ---- 2. compiled on the host -------------------------------------------------------------------------------------------
cc = os.environ.get("CC", "cc")
if not (fn1 and fn2 and fn3) or not shutil.which(cc):
    print("skip: no C compiler or no function to compile")
else:
    harness = r'''
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
enum { GLSL_SAMPLER_DIM_2D = 1, GLSL_SAMPLER_DIM_MS = 6 };
enum { GLSL_SAMPLER_DIM_1D = 0 };
enum { nir_intrinsic_bindless_image_store = 1, nir_intrinsic_bindless_image_fence_kk = 2, nir_intrinsic_load_texture_handle_kk = 3 };
/* stand-ins: an instruction's src[0] points at the instruction that made the image handle */
typedef struct nir_intrinsic_instr nir_intrinsic_instr;
typedef struct { nir_intrinsic_instr *parent; } nir_src;
struct nir_intrinsic_instr { int intrinsic; int dim; nir_src src[1]; };
#define nir_intrinsic_image_dim(i) ((i)->dim)
static nir_intrinsic_instr *nir_src_as_intrinsic(nir_src s) { return s.parent; }
@FN1@
@FN3@
@FN2@
int main(int argc, char **argv)
{
   nir_intrinsic_instr ms = { nir_intrinsic_load_texture_handle_kk, GLSL_SAMPLER_DIM_MS, { { NULL } } };
   nir_intrinsic_instr tex_2d = { nir_intrinsic_load_texture_handle_kk, GLSL_SAMPLER_DIM_2D, { { NULL } } };
   /* as kk_nir_lower_textures builds them: the store has the image's dimension, the fence after it has none (1D) */
   nir_intrinsic_instr ms_store = { nir_intrinsic_bindless_image_store, GLSL_SAMPLER_DIM_MS, { { &ms } } };
   nir_intrinsic_instr ms_fence = { nir_intrinsic_bindless_image_fence_kk, GLSL_SAMPLER_DIM_1D, { { &ms } } };
   nir_intrinsic_instr store_2d = { nir_intrinsic_bindless_image_store, GLSL_SAMPLER_DIM_2D, { { &tex_2d } } };
   nir_intrinsic_instr fence_2d = { nir_intrinsic_bindless_image_fence_kk, GLSL_SAMPLER_DIM_1D, { { &tex_2d } } };
   if (argc > 1 && !strcmp(argv[1], "many")) {
      int n = 0;
      for (int i = 0; i < 12; i++) n += kk_ms_store_dropped(&ms_store);
      printf("dropped=%d\n", n);
      return 0;
   }
   printf("on=%d ms_store=%d ms_fence=%d store_2d=%d fence_2d=%d\n", kk_ms_store_drop_enabled(),
          kk_ms_store_dropped(&ms_store), kk_ms_store_dropped(&ms_fence),
          kk_ms_store_dropped(&store_2d), kk_ms_store_dropped(&fence_2d));
   return 0;
}
'''.replace("@FN1@", fn1).replace("@FN3@", fn3).replace("@FN2@", fn2)
    with tempfile.TemporaryDirectory(prefix="kk-ms-") as d:
        c = Path(d) / "t.c"
        c.write_text(harness)
        exe = Path(d) / "t"
        r = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", str(c), "-o", str(exe)],
                           capture_output=True, text=True)
        check(r.returncode == 0, "both functions compile on the host (-Wall -Wextra -Werror)", r.stderr)
        if r.returncode == 0:
            def run(value, *args):
                env = dict(os.environ)
                env.pop("MADEIRA_KK_MS_STORE_DROP", None)
                if value is not None:
                    env["MADEIRA_KK_MS_STORE_DROP"] = value
                return subprocess.run([str(exe), *args], capture_output=True, text=True, env=env)
            off = [run(v) for v in (None, "0", "10", " 1", "", "true")]
            check(all(o.stdout.strip() == "on=0 ms_store=0 ms_fence=0 store_2d=0 fence_2d=0" and "[kk-ms-store-drop]" not in o.stderr
                      for o in off),
                  "unset, 0, 10, ' 1', empty and 'true' are off: nothing is left out, no log line",
                  [(o.stdout, o.stderr) for o in off])
            on = run("1")
            check(on.stdout.strip() == "on=1 ms_store=1 ms_fence=1 store_2d=0 fence_2d=0",
                  "1 is on: a multisampled image's store and its fence (no dimension of its own) are left out, a 2D image's are not",
                  on.stdout)
            check(on.stderr.count("MADEIRA_KK_MS_STORE_DROP=1") == 1 and on.stderr.count("a store to a multisampled storage image") == 1,
                  "...one [kk-ms-store-drop] line for the key and one for the left-out store (the fence is not logged)", on.stderr)
            many = run("1", "many")
            check(many.stdout.strip() == "dropped=12" and many.stderr.count("a store to a multisampled storage image") == 8,
                  "every store is left out, the first eight are logged", (many.stdout, many.stderr[-300:]))

# ---- 3. Metal itself ---------------------------------------------------------------------------------------------------
xcrun = shutil.which("xcrun")
probe = subprocess.run([xcrun, "-sdk", "iphoneos", "-f", "metal"], capture_output=True, text=True) if xcrun else None
if not probe or probe.returncode != 0:
    print("skip: no Metal compiler here (xcrun -sdk iphoneos metal)")
else:
    kernel = r'''#include <metal_stdlib>
using namespace metal;
kernel void clamp_inf(uint3 id [[thread_position_in_grid]], @TEX@ img [[texture(0)]])
{
   float4 v = img.read(id.xy, id.z, 0u);
   if (any(isinf(v))) {
      @STORE@
   }
}
'''
    rw = kernel.replace("@TEX@", "texture2d_ms_array<float, access::read_write>").replace(
        "@STORE@", "img.write(clamp(v, -65504.0f, 65504.0f), id.xy, id.z, 0u);")
    ro = kernel.replace("@TEX@", "texture2d_ms_array<float, access::read>").replace(
        "@STORE@", "/* madeira-bcd: store to a multisampled image left out (MADEIRA_KK_MS_STORE_DROP) */")
    with tempfile.TemporaryDirectory(prefix="kk-ms-metal-") as d:
        res = {}
        for name, src in (("rw", rw), ("ro", ro)):
            f = Path(d) / f"{name}.metal"
            f.write_text(src)
            res[name] = subprocess.run([xcrun, "-sdk", "iphoneos", "metal", "-c", str(f), "-o", str(Path(d) / f"{name}.air")],
                                       capture_output=True, text=True)
        check(res["rw"].returncode != 0 and "texture2d_ms_array" in res["rw"].stderr,
              "Metal refuses a kernel that writes a texture2d_ms_array (what X-Plane's pass asks for)", res["rw"].stderr[-600:])
        check(res["ro"].returncode == 0, "...and compiles it with the image read-only and the store left out (what 0035 emits)",
              res["ro"].stderr[-600:])

# ---- 4. the rest of the wiring -------------------------------------------------------------------------------------------
swift = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check('"MADEIRA_KK_MS_STORE_DROP",' in swift and 'case "MADEIRA_KK_MS_STORE_DROP":' in swift
      and 'throw RecommendationsError.policy("\\(path): MADEIRA_KK_MS_STORE_DROP is 1")' in swift,
      "the list policy allows the key, with the value 1 only")
gen = (R / "build/tools/gen-config-catalog.py").read_text()
m = re.search(r'"env\.MADEIRA_KK_MS_STORE_DROP": \{(.*?)\},\n', gen, re.S)
check(m is not None and '"kind": "bool"' in m.group(1) and '"default": "0"' in m.group(1)
      and "0035-kk-drop-stores-to-multisampled-storage-images.patch" in m.group(1) and "[kk-ms-store-drop]" in m.group(1),
      "the catalog has the key: a switch, off by default, its source the patch, its log line named")
cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check('key: "env.MADEIRA_KK_MS_STORE_DROP"' in cat, "...and the generated catalog carries it")
lst = json.loads((R / "recommended/recommendations.json").read_text())
xp = [r for r in lst["rules"] if r["id"] == "x-plane-12"]
lines = [c for c in xp[0]["config"] if isinstance(c, dict)] if xp else []
check({"line": "env.MADEIRA_KK_MS_STORE_DROP = 1", "minBuild": 675} in lines
      and {"line": "env.MADEIRA_KK_SNORM_BLEND = 1", "minBuild": 673} in lines,
      "X-Plane 12's row has the line from build 675, next to SNORM blending", lines)
other = [r["id"] for r in lst["rules"] if r["id"] != "x-plane-12"
         and any("MADEIRA_KK_MS_STORE_DROP" in (c if isinstance(c, str) else c["line"]) for c in r["config"])]
check(not other, "no other game's row sets it (what the shader would have written is not written)", other)
rules = (R / "build/tools/autotune_rules.py").read_text()
m = re.search(r'"id": "xplane-kk-ms-store-drop",(.*?)\n    \},\n', rules, re.S)
check(m is not None and '"lines": ["env.MADEIRA_KK_MS_STORE_DROP = 1"]' in m.group(1)
      and 's("VK_ERROR_INVALID_SHADER_NV")' in m.group(1) and "invalid access value 'a' for 'texture2d_ms" in m.group(1)
      and 's("[madeira-env] game MADEIRA_KK_MS_STORE_DROP=")' in m.group(1),
      "auto-tune rule xplane-kk-ms-store-drop proposes the line on Metal's refusal, with KosmicKrisp loaded, until the key is set")
fx = R / "tests/fixtures/autotune/x-plane-12-kk-ms-store-drop-2026-11-19"
exp = json.loads(fx.with_suffix(".expect").read_text()) if fx.with_suffix(".expect").exists() else {}
check(fx.with_suffix(".log").exists() and exp.get("matched") == ["xplane-kk-ms-store-drop"]
      and exp.get("next") == ["env.MADEIRA_KK_MS_STORE_DROP = 1"] and exp.get("outcome") == "crashed",
      "its fixture (the owner's log) matches it alone and proposes the line", exp)
readme = (R / "build/mesa-ios/playport-patches/README.md").read_text()
check("0035-kk-drop-stores-to-multisampled-storage-images.patch" in readme and "MADEIRA_KK_MS_STORE_DROP=1" in readme,
      "the patch notes describe 0035")
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("run: python3 tests/host/check-kk-ms-store-drop.py" in wf and "'build/mesa-ios/playport-patches/*.patch'" in wf,
      "CI runs this check, and the KosmicKrisp cache key covers the patch folder")

print(("PASS" if not failures else "FAILED") + ": KosmicKrisp multisampled storage stores (env.MADEIRA_KK_MS_STORE_DROP)")
sys.exit(1 if failures else 0)
