#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""KosmicKrisp logs the MSL source Metal refused (build 674; build/mesa-ios/playport-patches/0034, [kk-msl-fail]).

The owner's X-Plane 12 test on build 673 (2026-11-19 18:55) stopped on VK_ERROR_INVALID_SHADER_NV from vkCreateComputePipelines:
Metal refused KosmicKrisp's MSL ("invalid access value 'a' for 'texture2d_ms_array'", "no member named 'write'") and the error
names lines of a "program_source" no log showed. 0034 logs the refused source.

  1. the patch: BCD's header, one file (bridge/mtl_compiler.m), the dump is called only inside `if (lib == nil)` after Metal's
     own error line, nothing else of the function changes;
  2. kk_msl_fail_dump cut out of the patch and compiled on the host: a source is logged between begin/end lines with line
     numbers; only the first three calls log; a source longer than 800 lines is cut at 800 and the end line says so;
     a NULL source logs nothing.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
P = R / "build/mesa-ios/playport-patches/0034-kk-log-the-msl-source-metal-refused.patch"
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    if not cond:
        failures += 1


text = P.read_text() if P.exists() else ""
check(P.exists(), "the patch is build/mesa-ios/playport-patches/0034-kk-log-the-msl-source-metal-refused.patch")
head, _, body = text.partition("\n---\n")
body = body.split("\n-- \n")[0]
check(head.startswith("From: BCD <190844990+thebcd69@users.noreply.github.com>") and "[kk-msl-fail]" in head, "BCD's header names the log line")
files = re.findall(r"^diff --git a/(\S+) b/", body, re.M)
check(files == ["src/kosmickrisp/bridge/mtl_compiler.m"], "one file: src/kosmickrisp/bridge/mtl_compiler.m", files)
removed = [l[1:] for l in body.splitlines() if l.startswith("-") and not l.startswith("---")]
added = [l[1:] for l in body.splitlines() if l.startswith("+") and not l.startswith("+++")]
check(removed == [], "nothing of the driver is taken out", removed)
ctx = body[body.index("if (lib == nil) {"):] if "if (lib == nil) {" in body else ""
check(re.search(r'if \(lib == nil\) \{\n \s+fprintf\(stderr, "Failed to create MTLLibrary: %s\\n".*\n\+\s+kk_msl_fail_dump\(src\);\n', ctx) is not None
      and sum("kk_msl_fail_dump(src);" in l for l in added) == 1,
      "the dump is called once, inside `if (lib == nil)`, after Metal's own error line", ctx[:400])
helper = "\n".join(added)
m = re.search(r"static void\nkk_msl_fail_dump\(const char \*src\)\n\{.*?\n\}\n", helper, re.S)
check(m is not None, "kk_msl_fail_dump() is in the patch", helper[:500])
fn = m.group(0) if m else ""

cc = os.environ.get("CC", "cc")
if not fn or not shutil.which(cc):
    print("skip: no C compiler or no function to compile")
else:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
@FN@
int main(int argc, char **argv)
{
   (void)argc;
   if (!strcmp(argv[1], "null")) { kk_msl_fail_dump(NULL); kk_msl_fail_dump("a\nb"); return 0; }
   if (!strcmp(argv[1], "five")) { for (int i = 0; i < 5; i++) kk_msl_fail_dump("x\ny"); return 0; }
   if (!strcmp(argv[1], "long")) {
      size_t n = 1000; char *s = malloc(n * 4 + 1), *q = s;
      for (size_t i = 0; i < n; i++) q += sprintf(q, "l%zu\n", i);
      kk_msl_fail_dump(s); free(s); return 0;
   }
   kk_msl_fail_dump("#include <metal_stdlib>\nkernel void k() {}\n");
   return 0;
}
'''.replace("@FN@", fn)
    with tempfile.TemporaryDirectory(prefix="kk-msl-") as d:
        c = Path(d) / "t.c"
        c.write_text(harness)
        exe = Path(d) / "t"
        r = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(c), "-o", str(exe)], capture_output=True, text=True)
        check(r.returncode == 0, "the function compiles on the host (-Wall -Wextra -Werror)", r.stderr)
        if r.returncode == 0:
            def run(arg):
                return subprocess.run([str(exe), arg], capture_output=True, text=True).stderr.splitlines()
            one = run("one")
            check(one == ["[kk-msl-fail] madeira-bcd begin: the source Metal refused",
                          "[kk-msl-fail]    1: #include <metal_stdlib>", "[kk-msl-fail]    2: kernel void k() {}",
                          "[kk-msl-fail] madeira-bcd end (2 lines)"],
                  "a refused source is logged between begin and end, each line numbered", one)
            five = run("five")
            check(sum(l.endswith("begin: the source Metal refused") for l in five) == 3, "only the first three refusals are logged", five)
            nul = run("null")
            check(sum(l.endswith("begin: the source Metal refused") for l in nul) == 1 and "[kk-msl-fail]    1: a" in nul,
                  "a NULL source logs nothing and does not use up one of the three (the next one is still logged)", nul)
            lg = run("long")
            check(len([l for l in lg if re.match(r"\[kk-msl-fail\] +\d+: ", l)]) == 800 and lg[-1].endswith(", cut at 800)"),
                  "a source longer than 800 lines is cut at 800 and the end line says so", lg[-3:])

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("run: python3 tests/host/check-kk-msl-fail.py" in wf, "CI runs this check")
readme = (R / "build/mesa-ios/playport-patches/README.md").read_text()
check("0034-kk-log-the-msl-source-metal-refused.patch" in readme and "[kk-msl-fail]" in readme, "the patch notes describe 0034")

print(("PASS" if not failures else "FAILED") + ": KosmicKrisp logs the MSL Metal refused ([kk-msl-fail])")
sys.exit(1 if failures else 0)
