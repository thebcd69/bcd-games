#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-dxmt-layer-format.py: CAMetalLayer never gets a format it refuses.

Warcraft III (32-bit D3D9, build 559, log war3.exe 2026-10-16 22:24:07) made
an 800x600 R5G6B5 device; the first Present set B5G6R5Unorm on the layer and
the app ended: "'CAMetalLayerInvalid', reason: 'invalid pixel format 40'".

Checks:
  - madeira_layer_format, cut out of the patch and run on the host against
    winemetal.h's enum (or the same values when dxmt is not checked out):
    - the layer formats (BGRA8, RGBA16F, RGB10A2, BGR10A2, the XR formats,
      their sRGB forms) pass unchanged, swizzle bits included
      (D3D9 X8R8G8B8 is BGRX8Unorm), so a chain that worked is unchanged;
    - Invalid (LEGO City Undercover BHZRNE, build 600), B5G6R5 (R5G6B5),
      BGR5A1 (X1R5G5B5 / A1R5G5B5), A1BGR5, ABGR4 and RGBA8 become BGRA8Unorm;
    - at most 4 log lines;
  - on a copy of dxmt/src/dxmt/dxmt_presenter.cpp (when checked out): the
    patch applies, a second run changes nothing, it only adds lines and the
    one changed line, and the present pipeline still takes its format from
    layer_props_.pixel_format, the field the layer gets;
  - CI runs this check and the patch before the i386 farm key and the DXMT
    builds.
The host run is skipped when no C++ compiler is found.
"""
import difflib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
PATCH = R / "tools/patch-dxmt-layer-format.py"
p = PATCH.read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


new_fn = re.search(r'NEW_FN = """(.*?)"""', p, re.S).group(1)
fn = new_fn[new_fn.index("static WMTPixelFormat"):new_fn.index("\nbool\nPresenter::")]

src = pathlib.Path(os.environ.get("DXMT_SRC_DIR", R / "dxmt")) / "src"
header = src / "winemetal/winemetal.h"
names = ("Invalid", "A1BGR5Unorm", "ABGR4Unorm", "B5G6R5Unorm", "BGR5A1Unorm", "RGBA8Unorm", "BGRA8Unorm",
         "BGRA8Unorm_sRGB", "RGB10A2Unorm", "BGR10A2Unorm", "RGBA16Float", "BGRA10_XR", "BGRA10_XR_sRGB",
         "BGR10_XR", "BGR10_XR_sRGB")
fallback = {"Invalid": 0, "B5G6R5Unorm": 40, "A1BGR5Unorm": 41, "ABGR4Unorm": 42, "BGR5A1Unorm": 43,
            "RGBA8Unorm": 70, "BGRA8Unorm": 80, "BGRA8Unorm_sRGB": 81, "RGB10A2Unorm": 90, "BGR10A2Unorm": 94,
            "RGBA16Float": 115, "BGRA10_XR": 552, "BGRA10_XR_sRGB": 553, "BGR10_XR": 554, "BGR10_XR_sRGB": 555}
vals = dict(fallback)
if header.is_file():
    h = header.read_text()
    for n in names:
        m = re.search(r"\bWMTPixelFormat%s = (\d+)," % re.escape(n), h)
        check(f"winemetal.h: WMTPixelFormat{n} = {fallback[n]}", m and int(m.group(1)) == fallback[n])
    check("winemetal.h: AlphaIsOne is 0x00800000 and in the custom swizzle mask",
          "WMTPixelFormatAlphaIsOne = 0x00800000," in h and "WMTPixelFormatCustomSwizzle = WMTPixelFormatRGB1Swizzle |" in h)

cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
if not cxx:
    print("SKIP host run: no C++ compiler")
else:
    enum = ",\n".join(f"  WMTPixelFormat{n} = {vals[n]}" for n in names)
    harness = r'''
#include <atomic>
#include <cstdint>
#include <cstdio>
enum WMTPixelFormat : uint32_t {
''' + enum + r''',
  WMTPixelFormatAlphaIsOne = 0x00800000,
  WMTPixelFormatBGRX8Unorm = WMTPixelFormatAlphaIsOne | WMTPixelFormatBGRA8Unorm,
  WMTPixelFormatR001Swizzle = 0x00400000,
  WMTPixelFormat0R01Swizzle = 0x00200000,
  WMTPixelFormatGBARSwizzle = 0x00100000,
  WMTPixelFormatCustomSwizzle = WMTPixelFormatAlphaIsOne | WMTPixelFormatR001Swizzle | WMTPixelFormat0R01Swizzle | WMTPixelFormatGBARSwizzle,
};
#define ORIGINAL_FORMAT(format) (format & ~WMTPixelFormatCustomSwizzle)
static int lines;
#define WARN(...) (lines++)
''' + fn + r'''
int main() {
  const WMTPixelFormat keep[] = {WMTPixelFormatBGRA8Unorm, WMTPixelFormatBGRX8Unorm,
    WMTPixelFormatBGRA8Unorm_sRGB, WMTPixelFormatRGBA16Float, WMTPixelFormatRGB10A2Unorm, WMTPixelFormatBGR10A2Unorm,
    WMTPixelFormatBGRA10_XR, WMTPixelFormatBGRA10_XR_sRGB, WMTPixelFormatBGR10_XR, WMTPixelFormatBGR10_XR_sRGB};
  const WMTPixelFormat fix[] = {WMTPixelFormatInvalid, (WMTPixelFormat)(WMTPixelFormatAlphaIsOne | WMTPixelFormatInvalid),
    WMTPixelFormatB5G6R5Unorm, WMTPixelFormatBGR5A1Unorm, WMTPixelFormatA1BGR5Unorm,
    WMTPixelFormatABGR4Unorm, (WMTPixelFormat)(WMTPixelFormatGBARSwizzle | WMTPixelFormatABGR4Unorm),
    WMTPixelFormatRGBA8Unorm};
  for (auto f : keep)
    if (madeira_layer_format(f) != f) { printf("FAIL format %u changed\n", (unsigned)f); return 1; }
  if (lines) { printf("FAIL a layer format logged\n"); return 1; }
  for (int i = 0; i < 3; i++)
    for (auto f : fix)
      if (madeira_layer_format(f) != WMTPixelFormatBGRA8Unorm) { printf("FAIL format %u kept\n", (unsigned)f); return 1; }
  if (lines != 4) { printf("FAIL %d log lines, not 4\n", lines); return 1; }
  printf("harness ok\n");
  return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "t.cpp")
        exe = os.path.join(tmp, "t")
        open(c, "w").write(harness)
        b = subprocess.run([cxx, "-std=c++17", "-Wall", "-Werror", "-o", exe, c], capture_output=True, text=True)
        check("madeira_layer_format builds on the host", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-2000:])
        else:
            r = subprocess.run([exe], capture_output=True, text=True)
            print(r.stdout.strip())
            check("layer formats pass unchanged; Invalid, 16-bit, 4444 and RGBA8 become BGRA8Unorm; 4 log lines at most",
                  r.returncode == 0)

presenter = src / "dxmt/dxmt_presenter.cpp"
if not presenter.is_file():
    print(f"SKIP patch run: no dxmt sources at {src}")
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / "dxmt/src/dxmt").mkdir(parents=True)
        shutil.copy(presenter, t / "dxmt/src/dxmt/dxmt_presenter.cpp")
        before = presenter.read_text()
        already = "madeira-bcd: layer-safe format" in before
        first = subprocess.run([sys.executable, str(PATCH)], cwd=t, capture_output=True, text=True)
        first_bytes = (t / "dxmt/src/dxmt/dxmt_presenter.cpp").read_bytes()
        second = subprocess.run([sys.executable, str(PATCH)], cwd=t, capture_output=True, text=True)
        runs = [first, second]
        after = (t / "dxmt/src/dxmt/dxmt_presenter.cpp").read_text()
        check("the patch applies", runs[0].returncode == 0 and "madeira-bcd: layer-safe format" in after)
        check("a second run changes nothing", runs[1].returncode == 0 and "already patched" in runs[1].stdout
              and (t / "dxmt/src/dxmt/dxmt_presenter.cpp").read_bytes() == first_bytes)
        if not already:
            removed = [l for l in difflib.ndiff(before.splitlines(), after.splitlines()) if l.startswith("- ")]
            check("it only adds lines and changes the one assignment",
                  removed == ["-   layer_props_.pixel_format = Forget_sRGB(format);"])
        check("the layer and the present pipeline take the same field",
              "layer_props_.pixel_format = madeira_layer_format(Forget_sRGB(format));" in after
              and "present_pipeline.colors[0].pixel_format = layer_props_.pixel_format;" in after)
        anchor = "  switch ((uint32_t)ORIGINAL_FORMAT(format)) {\n"
        legacy = after.replace(anchor, anchor + "  case WMTPixelFormatInvalid:\n", 1)
        target = t / "dxmt/src/dxmt/dxmt_presenter.cpp"
        target.write_text(legacy)
        upgrade = subprocess.run([sys.executable, str(PATCH)], cwd=t, capture_output=True, text=True)
        check("an older already-patched source is upgraded without changing supported formats",
              upgrade.returncode == 0 and target.read_text() == after)
        repeat = subprocess.run([sys.executable, str(PATCH)], cwd=t, capture_output=True, text=True)
        check("the upgrade is idempotent", repeat.returncode == 0 and target.read_text() == after)

wf = (R / ".github/workflows/build-ipa.yml").read_text()
step = wf.find("run: python3 tools/patch-dxmt-layer-format.py")
check("CI runs this check before the patch",
      0 < wf.find("run: python3 tests/host/check-dxmt-layer-format.py") < step)
check("CI patches before the i386 farm key and the native DXMT build",
      0 < step < wf.find("- name: i386 farm revision") and step < wf.find("bash build/dxmt-ios/build.sh"))

# Round 660 (M002): a 64-bit Direct3D 11 game gets the fix only through d3d11-src.dll (LEGO City Undercover X025QA ended with format 0 on
# build 653, the 32-bit DLLs having had the fix since build 559): BCD_PATCHES names the script with its marker, and the IPA is refused
# when no 64-bit d3d11.dll carries the marker.
sh = (R / "tools/build-d3d11-dll.sh").read_text()
check("tools/build-d3d11-dll.sh applies it to d3d11-src.dll and requires its marker in the result",
      '"patch-dxmt-layer-format.py" "[layer-format] madeira-bcd: back buffer format "' in sh[sh.index("BCD_PATCHES=("):sh.index("SHIP_HAS_BCD=1")])
loop = wf[wf.index('for m in "[copy-structure-count] skipped #"'):]
check("the IPA check refuses a package whose 64-bit d3d11.dll lacks it", '"[layer-format] madeira-bcd: back buffer format "' in loop[:loop.index("; do")])

print("PASS: dxmt layer format" if ok else "FAILED: dxmt layer format")
sys.exit(0 if ok else 1)
