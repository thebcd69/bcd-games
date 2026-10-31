#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[d3d9-tex-fmt]: which texture formats a Direct3D 9 game creates (round 624, Discord forum item 11; tools/patch-dxmt-d9-texfmt-log.py).

LEGO Batman, Silent Hunter 3 ("pink popping textures") and Requiem: Avenging Angel (solid black UI sprites) draw wrong textures on
DXMT's 32-bit Direct3D 9, and no log line says which formats they create. The patch makes CreateTexture, CreateCubeTexture and
CreateVolumeTexture report each distinct (kind, format, pool, usage), first sight and the 100th and 1000th creation, 40 lines at
most, as part of the D3D9 census (off with MADEIRA_D3D9_CENSUS=0).

Checks: the workflow (after the query park patch, before the i386 farm key that hashes it); the whole CI patch chain applies with
it, once, then "already patched"; a moved anchor fails by name with nothing written; each create reports right after its format
lowering, with its kind and sizes; textureFormat itself cut from the patched census and compiled on the host (C++20), driven: the
line's fields, a FOURCC by its letters, a format DXMT cannot create, the 100th and 1000th creations, the 40-line budget, the
64-key table, nothing while the census is off. SKIP of the patch part (exit 0) when no dxmt sources are available.
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
me = "tools/patch-dxmt-d9-texfmt-log.py"
check("the workflow runs it right after the query park patch, before the i386 farm key (which hashes tools/patch-dxmt-*.py)",
      me in scripts and scripts.index(me) == scripts.index("tools/patch-dxmt-d3d9-query-park.py") + 1
      and wf.index("python3 " + me) < wf.index("name: i386 farm revision")
      and "'tools/patch-dxmt-*.py'" in wf[wf.index("name: i386 farm revision"):][:600])
check("CI runs this check", "python3 tests/host/check-d9-texfmt.py" in wf)

if not (src / "d3d9" / "d3d9_census.cpp").is_file():
    print(f"SKIP: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)")
    print("PASS: d3d9 texture formats (static part)" if ok else "FAIL: d3d9 texture formats")
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
    d9 = t / "dxmt/src/d3d9"
    before = {}
    chain_ok = True
    for s in scripts:
        if s == me:
            before = {f: (d9 / f).read_text() for f in ("d3d9_census.hpp", "d3d9_census.cpp", "d3d9_device.cpp")}
        p = subprocess.run([sys.executable, str(R / s)], cwd=t, capture_output=True, text=True)
        if p.returncode:
            chain_ok = False
            check(f"patch {s} applies in the chain", False, (p.stdout + p.stderr).strip())
        if s == me:
            p2 = subprocess.run([sys.executable, str(R / s)], cwd=t, capture_output=True, text=True)
            check("applies once, then says already patched",
                  p.returncode == 0 and "patched" in p.stdout and p2.returncode == 0 and "already patched" in p2.stdout, p.stdout + p2.stdout)
            after = {f: (d9 / f).read_text() for f in before}
    check("the whole CI patch chain applies with it", chain_ok)

    work = t / "moved"
    shutil.copytree(t / "dxmt", work / "dxmt")
    for f, text in before.items():
        (work / "dxmt/src/d3d9" / f).write_text(text)
    dev_moved = before["d3d9_device.cpp"].replace("  WMTPixelFormat pixelFormat = D3DFormatToMetal(Format, D3D9FormatUsage::SampleableTexture);\n",
                                                  "  WMTPixelFormat pixelFormat = D3DFormatToMetal(Format, D3D9FormatUsage::SampleableTexture );\n", 1)
    (work / "dxmt/src/d3d9/d3d9_device.cpp").write_text(dev_moved)
    p3 = subprocess.run([sys.executable, str(R / me)], cwd=work, capture_output=True, text=True)
    check("a moved anchor fails by name and writes nothing",
          p3.returncode != 0 and "CreateVolumeTexture" in p3.stdout + p3.stderr
          and (work / "dxmt/src/d3d9/d3d9_census.cpp").read_text() == before["d3d9_census.cpp"]
          and (work / "dxmt/src/d3d9/d3d9_census.hpp").read_text() == before["d3d9_census.hpp"]
          and (work / "dxmt/src/d3d9/d3d9_device.cpp").read_text() == dev_moved, p3.stdout + p3.stderr)

    dev = after["d3d9_device.cpp"]
    def fbody(sig):
        a = dev.index(sig)
        return dev[a:dev.index("\n}\n", a)]
    tex, vol, cube = fbody("MTLD3D9Device::CreateTexture(\n"), fbody("MTLD3D9Device::CreateVolumeTexture(\n"), fbody("MTLD3D9Device::CreateCubeTexture(\n")
    check("CreateTexture reports right after its format lowering (the NULL render target folded in), before the reject",
          tex.count("census::textureFormat(0, (unsigned)Format, (unsigned)Pool, (unsigned)Usage, (unsigned)pixelFormat, Width, Height, 1,") == 1
          and tex.index("pixelFormat = WMTPixelFormatBGRA8Unorm;") < tex.index("census::textureFormat(0,")
          < tex.index("if (pixelFormat == WMTPixelFormatInvalid && !IsScratchableUnsupportedFormat(Format)) {"))
    check("CreateVolumeTexture and CreateCubeTexture report right after theirs, with depth and the six faces",
          vol.count("census::textureFormat(2, (unsigned)Format, (unsigned)Pool, (unsigned)Usage, (unsigned)pixelFormat, Width, Height, Depth,") == 1
          and vol.index("D3DFormatToMetal(Format, D3D9FormatUsage::SampleableTexture);") < vol.index("census::textureFormat(2,")
          and cube.count("census::textureFormat(1, (unsigned)Format, (unsigned)Pool, (unsigned)Usage, (unsigned)pixelFormat, EdgeLength, EdgeLength, 6,") == 1
          and cube.index("D3DFormatToMetal(Format, formatUsage);") < cube.index("census::textureFormat(1,"))
    check("no other change to d3d9_device.cpp", re.sub(r"  census::textureFormat\([^;]*;   /\* madeira-bcd d9-texfmt-v1 \*/\n", "", dev)
          == before["d3d9_device.cpp"])

    cpp = after["d3d9_census.cpp"]
    fn = cpp[cpp.index("void textureFormat(unsigned kind, unsigned format, unsigned pool"):]
    fn = fn[:fn.index("\n}\n") + 3]
    harness = r'''
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <string>
static bool g_on = true;
static std::string out;
static unsigned lines_out;
namespace Logger { static void info(const std::string &s) { out += s + "\n"; lines_out++; } }
@FN@
int main(int argc, char **argv) {
  if (argc > 1) g_on = false;
  textureFormat(0, 26, 1, 0, 42, 256, 256, 1, 1);                       /* A4R4G4B4, managed */
  textureFormat(0, 0x31545844u, 0, 0, 130, 512, 512, 1, 10);            /* DXT1 */
  textureFormat(1, 41, 0, 0, 0, 64, 64, 6, 1);                          /* P8: none */
  for (int i = 1; i < 1000; i++) textureFormat(0, 26, 1, 0, 42, 32, 32, 1, 1);   /* 100th and 1000th */
  textureFormat(2, 21, 0, 0x200, 80, 16, 16, 16, 0);                    /* volume, A8R8G8B8, usage */
  for (unsigned f = 100; f < 200; f++) textureFormat(0, f, 0, 0, 1, 8, 8, 1, 1);   /* a hundred more keys */
  printf("%s", out.c_str());
  printf("lines=%u\n", lines_out);
  return 0;
}
'''
    cxx = shutil.which("c++") or shutil.which("clang++")
    (t / "h.cpp").write_text(harness.replace("@FN@", fn))
    b = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
    check("textureFormat compiles on the host as C++20 (-Wall -Wextra)", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        out = subprocess.run([str(t / "h")], capture_output=True, text=True).stdout
        lines = out.splitlines()
        check("first sight: kind, format, pool, usage, the Metal format, size and levels",
              lines[0] == "[d3d9-tex-fmt] 2D fmt=26 pool=MANAGED usage=0 metal=42 first=256x256x1 levels=1 count=1", lines[:3])
        check("a FOURCC by its letters; a format DXMT cannot create says so",
              lines[1] == "[d3d9-tex-fmt] 2D fmt=DXT1 pool=DEFAULT usage=0 metal=130 first=512x512x1 levels=10 count=1"
              and lines[2] == "[d3d9-tex-fmt] cube fmt=41 pool=DEFAULT usage=0 metal=0 (none: the create fails) first=64x64x6 levels=1 count=1", lines[:3])
        check("the 100th and 1000th creation of a key are reported again, nothing in between",
              lines[3].endswith("first=32x32x1 levels=1 count=100") and lines[4].endswith("first=32x32x1 levels=1 count=1000")
              and lines[5] == "[d3d9-tex-fmt] volume fmt=21 pool=DEFAULT usage=0x200 metal=80 first=16x16x16 levels=0 count=1", lines[3:6])
        check("40 lines at most a process (the table holds 64 keys)", lines[-1] == "lines=40" and len(lines) == 41, lines[-3:])
        off = subprocess.run([str(t / "h"), "off"], capture_output=True, text=True).stdout
        check("the census off: nothing", off.strip() == "lines=0", off)

print("PASS: d3d9 texture formats" if ok else "FAIL: d3d9 texture formats")
sys.exit(0 if ok else 1)
