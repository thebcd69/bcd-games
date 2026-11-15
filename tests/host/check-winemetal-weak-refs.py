#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Every weak reference of DXMT's native side has its function somewhere in the app (upstream merge 2026-11-14).

winemetal's unix side, airconv and the rest of dxmt/src are compiled into the app as a static library. A declaration with
__attribute__((weak)) or ((weak_import)) only lets the function be absent when the program runs; Apple's linker still refuses to
link an iOS app when nothing defines it ("Undefined symbols for architecture arm64"). Upstream Madeira's winemetal refers that way to
functions of Madeira's own app: madeira_frame_mark (its overlay, dxmt 67079a57) and madeira_swap_overlay_stats (its swap tier's donor
pool, dxmt ea19ddf4 / e0abd4d1). BCD's app has neither, so those three commits were left out of the 2026-11-14 merge; a later merge
that brings such a reference must bring the function too (or leave the commit out), and this check says so before the app link does.

It reads the weak references of the pinned source (dxmt/src, or $DXMT_SRC_DIR) and of the code the workflow's patch scripts insert
(tools/patch-dxmt-*.py, patch-airconv-*.py, patch-winemetal-*.py), and wants each function defined in app/Madeira, in the unix sides
under build/, or to be one of Metal's own (the shader cache path functions, which the Metal framework provides). When the iPhone SDK
is at hand it also links a two-line program for iOS to prove the linker's rule itself. SKIP (exit 0) without the dxmt sources."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
S = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
if not (S / "winemetal/unix/winemetal_unix.c").exists():
    print(f"SKIP: no dxmt sources at {S}")
    sys.exit(0)
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


METAL_OWN = {"MTLSetShaderCachePath", "MTLGetShaderCachePath"}   # Metal.framework's, present on every iOS the app runs on
WEAK = re.compile(r"extern\s+[^;{}]*?\b([A-Za-z_][A-Za-z_0-9]*)\s*\([^;{}]*?\)\s*__attribute__\(\(\s*weak(?:_import)?\s*\)\)", re.S)

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-winemetal-weak-refs.py" in wf)

refs = {}
native = [p for d in ("winemetal/unix", "airconv", "dxmt", "util", "d3d9", "nativemetal") if (S / d).is_dir()
          for p in (S / d).rglob("*") if p.suffix in (".c", ".cpp", ".h", ".hpp", ".m", ".mm")]
native += [S / "winemetal/winemetal_thunks.c", S / "winemetal/wmt_api_census.c"]
for p in native:
    if p.exists():
        for name in WEAK.findall(p.read_text(errors="replace")):
            refs.setdefault(name, set()).add(str(p.relative_to(S)))
scripts = re.findall(r"python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)", wf)
for s in sorted(set(scripts)):
    for name in WEAK.findall((R / s).read_text()):
        refs.setdefault(name, set()).add(s)
check("the scan finds the weak references that are known to be there (the frame ring's and the shader cache path's)",
      "bcd_frame_ring_push" in refs and "MTLSetShaderCachePath" in refs, sorted(refs))

app = ""
for d in ("app/Madeira", "build"):
    for p in (R / d).rglob("*"):
        if p.suffix in (".c", ".m", ".mm", ".h") and p.is_file() and "/obj/" not in str(p):
            app += p.read_text(errors="replace") + "\n"
for name in sorted(refs):
    if name in METAL_OWN:
        print(f"ok   {name}: Metal's own")
        continue
    defined = re.search(r"^[A-Za-z_][^;=\n(]*\b" + re.escape(name) + r"\s*\([^;{]*\)\s*\{", app, re.M) is not None
    check(f"{name} (referenced weakly in {', '.join(sorted(refs[name]))}) is defined in the app or a unix side", defined,
          "nothing defines it: the iOS link of the app fails with \"Undefined symbols\". Bring the function, or leave the commit out.")
for gone in ("madeira_frame_mark", "madeira_swap_overlay_stats"):
    check(f"{gone} of Madeira's app is not referenced (BCD's app has no such function)", gone not in refs)

sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True) if shutil.which("xcrun") else None
if not sdk or sdk.returncode != 0 or not sdk.stdout.strip():
    print("note: no iPhone SDK here; the linker's rule is not demonstrated (it runs where the app is built)")
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / "a.c").write_text("extern void bcd_nobody_defines_this(void) __attribute__((weak));\n"
                               "int main(void) { if (bcd_nobody_defines_this) bcd_nobody_defines_this(); return 0; }\n")
        r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", "-arch", "arm64", "-isysroot", sdk.stdout.strip(),
                            "-miphoneos-version-min=18.0", str(t / "a.c"), "-o", str(t / "a")], capture_output=True, text=True)
        check("the rule itself: an iOS link with an undefined weak reference fails with \"Undefined symbols\"",
              r.returncode != 0 and "Undefined symbols" in r.stderr and "_bcd_nobody_defines_this" in r.stderr, r.stderr)
if not ok:
    print("FAILED: winemetal-weak-refs")
    sys.exit(1)
print(f"PASS: all {len(refs)} weak references of DXMT's native side have their functions")
