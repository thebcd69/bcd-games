#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Blending on SNORM color formats in KosmicKrisp for X-Plane 12's Vulkan renderer (build 673; env.MADEIRA_KK_SNORM_BLEND = 1).

The owner's X-Plane 12 test on build 672 (2026-11-19, iPhone18,2) got past Chromium and stopped on X-Plane's own assertion:
"Format r16s used as a blended color attachment but doesn't support blending capability" (gfx_vk_pipeline_graphics.cpp:862,
then __fastfail). KosmicKrisp holds VK_FORMAT_FEATURE_2_COLOR_ATTACHMENT_BLEND_BIT back for every SNORM format while Khronos
issue 4293 is open, although it blends in the fragment shader (nir_lower_blend, which handles SNORM targets).
build/mesa-ios/playport-patches/0033 reports the bit for SNORM formats with MADEIRA_KK_SNORM_BLEND=1 only.

  1. the patch: BCD's header, one file (kk_image.c), the only changed condition is the SNORM one, now
     `!vk_format_is_snorm(vk_format) || kk_snorm_blend_enabled()`; the key is read once and only "1" turns it on;
  2. the key (kk_snorm_blend_enabled cut out of the patch and compiled on the host): unset, "0", "10", " 1" and "" are off,
     "1" is on and logs [kk-snorm-blend] once; the answer of the first call is kept;
  3. the feature bit (the patched condition compiled with stand-ins): without the key a SNORM format has no blend bit and every
     other colour format keeps it; with it SNORM formats get it too;
  4. the list policy, the catalog, the X-Plane 12 row, the auto-tune rule with its fixture, the patch notes and CI.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
P = R / "build/mesa-ios/playport-patches/0033-kk-snorm-color-attachments-report-blending.patch"
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    if not cond:
        failures += 1


# ---- 1. the patch ------------------------------------------------------------------------------------------------------
text = P.read_text() if P.exists() else ""
check(P.exists(), "the patch is build/mesa-ios/playport-patches/0033-kk-snorm-color-attachments-report-blending.patch")
head, _, body = text.partition("\n---\n")
body = body.split("\n-- \n")[0]   # the signature after "-- " is no part of the diff
check(head.startswith("From: BCD <190844990+thebcd69@users.noreply.github.com>") and "MADEIRA_KK_SNORM_BLEND=1" in head,
      "BCD's header names the key", head[:300])
files = re.findall(r"^diff --git a/(\S+) b/", body, re.M)
check(files == ["src/kosmickrisp/vulkan/kk_image.c"], "one file: src/kosmickrisp/vulkan/kk_image.c", files)
removed = [l[1:] for l in body.splitlines() if l.startswith("-") and not l.startswith("---")]
added = [l[1:] for l in body.splitlines() if l.startswith("+") and not l.startswith("+++")]
check(removed == ["      if (!vk_format_is_snorm(vk_format))"],
      "the one line taken out is the SNORM condition (nothing else of the driver changes)", removed)
check("      if (!vk_format_is_snorm(vk_format) || kk_snorm_blend_enabled())" in added,
      "...and it comes back as `!vk_format_is_snorm(vk_format) || kk_snorm_blend_enabled()`", added)
helper = "\n".join(added)
m = re.search(r"static bool\nkk_snorm_blend_enabled\(void\)\n\{.*?\n\}\n", helper, re.S)
check(m is not None, "kk_snorm_blend_enabled() is in the patch", helper[:600])
fn = m.group(0) if m else ""
check('getenv("MADEIRA_KK_SNORM_BLEND")' in fn and "s && s[0] == '1' && !s[1]" in fn and "static int enabled = -1;" in fn,
      "the key is read once (a static) and only the value 1 turns it on", fn)
check("[kk-snorm-blend]" in fn and fn.count("fprintf") == 1, "it logs one [kk-snorm-blend] line when on", fn)
check("#include <stdio.h>" in helper and "#include <stdlib.h>" in helper, "kk_image.c gets the headers getenv and fprintf need")

# ---- 2 and 3. compiled on the host ---------------------------------------------------------------------------------------
cc = os.environ.get("CC", "cc")
if not fn or not shutil.which(cc):
    print("skip: no C compiler or no function to compile")
else:
    harness = r'''
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
@FN@
enum { BLEND = 1 };
static int is_snorm(int f) { return f == 2; }   /* stand-ins: 1 unorm, 2 snorm, 3 float */
static unsigned features(int f)
{
   unsigned features = 0;
   if (!is_snorm(f) || kk_snorm_blend_enabled())
      features |= BLEND;
   return features;
}
int main(int argc, char **argv)
{
   if (argc > 1 && !strcmp(argv[1], "twice")) {
      int a = kk_snorm_blend_enabled();
      setenv("MADEIRA_KK_SNORM_BLEND", a ? "0" : "1", 1);
      printf("first=%d second=%d\n", a, kk_snorm_blend_enabled());
      return 0;
   }
   printf("on=%d unorm=%u snorm=%u float=%u\n", kk_snorm_blend_enabled(), features(1), features(2), features(3));
   return 0;
}
'''.replace("@FN@", fn)
    with tempfile.TemporaryDirectory(prefix="kk-snorm-") as d:
        c = Path(d) / "t.c"
        c.write_text(harness)
        exe = Path(d) / "t"
        r = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(c), "-o", str(exe)], capture_output=True, text=True)
        check(r.returncode == 0, "the function and the patched condition compile on the host (-Wall -Wextra -Werror)", r.stderr)
        if r.returncode == 0:
            def run(value, *args):
                env = dict(os.environ)
                env.pop("MADEIRA_KK_SNORM_BLEND", None)
                if value is not None:
                    env["MADEIRA_KK_SNORM_BLEND"] = value
                return subprocess.run([str(exe), *args], capture_output=True, text=True, env=env)
            off = [run(v) for v in (None, "0", "10", " 1", "", "true")]
            check(all(o.stdout.strip() == "on=0 unorm=1 snorm=0 float=1" and "[kk-snorm-blend]" not in o.stderr for o in off),
                  "unset, 0, 10, ' 1', empty and 'true' are off: a SNORM format has no blend bit, the others keep it, no log line",
                  [(o.stdout, o.stderr) for o in off])
            on = run("1")
            check(on.stdout.strip() == "on=1 unorm=1 snorm=1 float=1" and on.stderr.count("[kk-snorm-blend]") == 1,
                  "1 is on: SNORM formats get the blend bit too, and one [kk-snorm-blend] line", (on.stdout, on.stderr))
            t1, t0 = run("1", "twice"), run(None, "twice")
            check(t1.stdout.strip() == "first=1 second=1" and t0.stdout.strip() == "first=0 second=0",
                  "the first answer is kept (the key is read once per process)", (t1.stdout, t0.stdout))

# ---- 4. the rest of the wiring -------------------------------------------------------------------------------------------
swift = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check('"MADEIRA_KK_SNORM_BLEND",' in swift and 'case "MADEIRA_KK_SNORM_BLEND":' in swift
      and 'throw RecommendationsError.policy("\\(path): MADEIRA_KK_SNORM_BLEND is 1")' in swift,
      "the list policy allows the key, with the value 1 only")
gen = (R / "build/tools/gen-config-catalog.py").read_text()
m = re.search(r'"env\.MADEIRA_KK_SNORM_BLEND": \{(.*?)\},\n', gen, re.S)
check(m is not None and '"kind": "bool"' in m.group(1) and '"default": "0"' in m.group(1)
      and "0033-kk-snorm-color-attachments-report-blending.patch" in m.group(1) and "[kk-snorm-blend]" in m.group(1),
      "the catalog has the key: a switch, off by default, its source the patch, its log line named")
cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("env.MADEIRA_KK_SNORM_BLEND" in cat, "...and the generated catalog carries it")
import json
lst = json.loads((R / "recommended/recommendations.json").read_text())
xp = [r for r in lst["rules"] if r["id"] == "x-plane-12"]
lines = [c for c in xp[0]["config"] if isinstance(c, dict)] if xp else []
check({"line": "env.MADEIRA_KK_SNORM_BLEND = 1", "minBuild": 673} in lines
      and {"line": "env.MADEIRA_CEF_POOL_IMAGES = x-plane.exe,cefclient.exe", "minBuild": 671} in lines
      and {"line": "env.MADEIRA_SC_PA_POOLS = 2", "minBuild": 671} in lines,
      "X-Plane 12's row has the line from build 673, with the two Chromium lines the owner's test used", lines)
other = [r["id"] for r in lst["rules"] if r["id"] != "x-plane-12" and any("MADEIRA_KK_SNORM_BLEND" in (c if isinstance(c, str) else c["line"]) for c in r["config"])]
check(not other, "no other game's row sets it (it changes what the driver reports for every SNORM format)", other)
rules = (R / "build/tools/autotune_rules.py").read_text()
m = re.search(r'"id": "xplane-kk-snorm-blend",(.*?)\n    \},\n', rules, re.S)
check(m is not None and '"lines": ["env.MADEIRA_KK_SNORM_BLEND = 1"]' in m.group(1)
      and "used as a blended color attachment but doesn't support blending capability" in m.group(1)
      and 's("[madeira-env] game MADEIRA_KK_SNORM_BLEND=")' in m.group(1),
      "auto-tune rule xplane-kk-snorm-blend proposes the line on the assertion, with KosmicKrisp loaded, until the key is set")
fx = R / "tests/fixtures/autotune/x-plane-12-kk-snorm-blend-2026-11-19"
exp = json.loads(fx.with_suffix(".expect").read_text()) if fx.with_suffix(".expect").exists() else {}
check(fx.with_suffix(".log").exists() and (exp.get("matched") or [None])[0] == "xplane-kk-snorm-blend"
      and exp.get("next") == ["env.MADEIRA_KK_SNORM_BLEND = 1"] and exp.get("outcome") == "crashed",
      "its fixture (the owner's log) matches it first and proposes the line (the diagnostic rule x64-null-fault-diag also "
      "matches the __fastfail's null read; it proposes nothing)", exp)
readme = (R / "build/mesa-ios/playport-patches/README.md").read_text()
check("0033-kk-snorm-color-attachments-report-blending.patch" in readme and "MADEIRA_KK_SNORM_BLEND=1" in readme,
      "the patch notes describe 0033")
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("run: python3 tests/host/check-kk-snorm-blend.py" in wf and "'build/mesa-ios/playport-patches/*.patch'" in wf,
      "CI runs this check, and the KosmicKrisp cache key covers the patch folder")

print(("PASS" if not failures else "FAILED") + ": KosmicKrisp SNORM blending (env.MADEIRA_KK_SNORM_BLEND)")
sys.exit(1 if failures else 0)
