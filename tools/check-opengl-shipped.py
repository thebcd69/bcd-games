#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Every piece of BCD's OpenGL is in the package before it is archived (OpenGL, build 632; docs/OPENGL.md).

A game whose gl-backend is zink or gles runs the winios OpenGL driver (build/win32u-unix/opengl_ios.c), the real opengl32 unix side
(build/ntdll-unix), the opt-in opengl32.dll and, for zink, Mesa's OSMesa + Zink and MoltenVK as two dylibs in the bundle's gl folder. The
recommended lists and auto-tune turn it on for the games that cannot start without it, so a bundle that lacks a piece is refused here
instead of being found on a phone. Run from the repository root after the build steps (CI: "Verify the OpenGL pieces").

  check-opengl-shipped.py            errors for what the games need; warnings for LuaJIT (only the LOVE games' substitution)
"""
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "Madeira"
errors, warnings = [], []

CPU_TYPE_ARM64 = 0x0100000C
MH_MAGIC_64 = 0xFEEDFACF
LC_ID_DYLIB = 0x0D


def dylib_info(path):
    """(cputype, install name) of a thin 64-bit Mach-O dylib, or None."""
    d = path.read_bytes()
    if len(d) < 32 or struct.unpack_from("<I", d, 0)[0] != MH_MAGIC_64:
        return None
    cputype, _, filetype, ncmds, _, _ = struct.unpack_from("<iiIIII", d, 4)   # cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags
    off, name = 32, None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", d, off)
        if cmd == LC_ID_DYLIB:
            name_off = struct.unpack_from("<I", d, off + 8)[0]
            raw = d[off + name_off:off + size]
            name = raw.split(b"\0")[0].decode("latin-1")
        off += size
    return cputype, filetype, name


for lib in ("libOSMesa.dylib", "libMoltenVK.dylib"):
    p = APP / "gl" / lib
    if not p.is_file():
        errors.append(f"app/Madeira/gl/{lib} is missing: build/{'mesa-ios' if 'OSMesa' in lib else 'moltenvk-ios'}/build.sh did not run or failed")
        continue
    info = dylib_info(p)
    if not info:
        errors.append(f"app/Madeira/gl/{lib} is not a thin 64-bit Mach-O dylib")
    elif info[0] != CPU_TYPE_ARM64 or info[1] != 6:   # MH_DYLIB
        errors.append(f"app/Madeira/gl/{lib}: cpu {info[0]:#x} / file type {info[1]}, want arm64 and a dylib")
    elif info[2] != f"@rpath/{lib}":
        errors.append(f"app/Madeira/gl/{lib}: install name {info[2]!r}, want @rpath/{lib}")
    elif p.stat().st_size < 100_000:
        errors.append(f"app/Madeira/gl/{lib} is only {p.stat().st_size} bytes")

for name in ("Mesa-license.rst", "MoltenVK-Apache-2.0.txt", "SPIRV-Cross-Apache-2.0.txt", "SPIRV-Tools-Apache-2.0.txt", "cereal-BSD-3-Clause.txt"):
    if not (APP / "licenses" / name).is_file():
        errors.append(f"app/Madeira/licenses/{name} is missing (the build scripts copy it; THIRD-PARTY-NOTICES.md names it)")

if not (APP / "arm64ec-windows" / "opengl32-optin.dll").is_file():
    errors.append("app/Madeira/arm64ec-windows/opengl32-optin.dll is missing: tools/build-wine-extra-dlls.sh (block OGL) did not build it, "
                  "so a 64-bit game with gl-backend on would run the shipped opengl32.dll")

for name in ("lua51.dll",):
    if not (APP / "compat" / "love" / name).is_file():
        warnings.append(f"app/Madeira/compat/love/{name} is missing: a LOVE game that ships a LuaJIT needing memory below 2 GB (Balatro) keeps its own, as before")
if (APP / "compat" / "love" / "lua51.dll").is_file() and not (APP / "licenses" / "LuaJIT-MIT.txt").is_file():
    errors.append("app/Madeira/licenses/LuaJIT-MIT.txt is missing next to compat/love/lua51.dll")

nm = shutil.which("nm")
for lib, symbols in (("libntdll_unix.a", ("_opengl32_unix_call_funcs", "_opengl32_unix_call_wow64_funcs")),
                     ("libwin32u_unix.a", ("_winios_OpenGLInit",))):
    a = APP / lib
    if not a.is_file():
        errors.append(f"app/Madeira/{lib} is missing")
    elif nm:
        out = subprocess.run([nm, "-gU", str(a)], capture_output=True, text=True).stdout
        defined = set(re.findall(r"\s(_\S+)$", out, re.M))
        for sym in symbols:
            if sym not in defined:
                errors.append(f"app/Madeira/{lib} does not define {sym}")
    else:
        warnings.append(f"no nm here: {lib} was not searched for {', '.join(symbols)}")

for w in warnings:
    print(f"::warning::{w}")
for e in errors:
    print(f"::error::{e}")
if errors:
    sys.exit(1)
print("OpenGL pieces: the dylibs, opengl32-optin.dll, the licence texts, the driver and the unix tables are in place")
