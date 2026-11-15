#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The dxcompiler.dll that tools/build-dxcompiler.sh built, or the dxcompiler-optin.dll it shipped, is the DLL BCD means (round 663).

  check-dxcompiler-shipped.py FILE
      FILE: build/dxc-win/out/dxcompiler.dll, or app/Madeira/arm64ec-windows/dxcompiler-optin.dll in a built tree.

What it holds the file to:
  - a PE32+ DLL for x86-64 (machine 0x8664): a 64-bit Windows program loads it, and FEX runs it as it runs a game's own x64
    dxcompiler.dll (Horizon Zero Dawn Remastered's tools\\bin\\x64\\DXCompiler.dll, Send Log C7X0G9);
  - it exports DxcCreateInstance and DxcCreateInstance2, the two entry points every DXC user (wgpu among them) looks up;
  - its static imports are only Windows DLLs Wine ships for 64-bit programs: kernel32, ole32, oleaut32 and the UCRT's api-ms-win-crt-*
    sets. libc++, libunwind and libwinpthread are linked in (-static), and dxil.dll, Microsoft's separate validator, is not imported:
    since DXC 1.8.2502 the compiler signs (hashes) its output itself;
  - it carries DXC's version (1.9) and is between 15 and 40 MB (a stripped build of the whole compiler is about 21 MB);
  - it holds no absolute path of the machine that built it (/Users/<name>/, /home/<name>/, /private/, /Volumes/): the source file names inside are
    mapped to dxc/...; only the GitHub runner's /Users/runner/work/llvm-mingw/... of the toolchain's own libc++abi may remain.
Exit 1 naming each failed point. Reads only.
"""
import importlib.util
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOWED = {"kernel32.dll", "ole32.dll", "oleaut32.dll"}
EXPORTS = {"DxcCreateInstance", "DxcCreateInstance2"}


def load_pe():
    spec = importlib.util.spec_from_file_location("check_farm_imports", HERE / "check-farm-imports.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def exports(pe):
    """The exported names (export directory, data directory 0)."""
    rva, _size = pe.directories[0] if pe.directories else (0, 0)
    if not rva:
        return set()
    at = pe.offset(rva, 40)
    count, names_rva = struct.unpack_from("<I", pe.data, at + 24)[0], struct.unpack_from("<I", pe.data, at + 32)[0]
    out = set()
    for i in range(min(count, 4096)):
        name_rva = struct.unpack_from("<I", pe.data, pe.offset(names_rva + 4 * i, 4))[0]
        at = pe.offset(name_rva)
        end = pe.data.find(b"\0", at, at + 260)
        out.add(pe.data[at:end].decode("latin-1"))
    return out


def problems(path):
    data = Path(path).read_bytes()
    mod = load_pe()
    try:
        pe = mod.PE(data)
    except (mod.PEError, struct.error) as error:
        return ["not a readable PE file (%s)" % error]
    bad = []
    if pe.machine != 0x8664:
        bad.append("machine %#x, not x86-64 (0x8664)" % pe.machine)
    if struct.unpack_from("<H", data, struct.unpack_from("<I", data, 0x3C)[0] + 24)[0] != 0x20B:
        bad.append("not a PE32+ (64-bit) image")
    have = exports(pe)
    if not EXPORTS <= have:
        bad.append("exports %s, missing %s" % (sorted(have), sorted(EXPORTS - have)))
    imports = pe.imports() | pe.imports(delayed=True)
    extra = sorted(i for i in imports if i not in ALLOWED and not i.startswith("api-ms-win-crt-"))
    if extra:
        bad.append("imports beyond kernel32, ole32, oleaut32 and the UCRT sets: %s" % extra)
    if "dxil.dll" in imports:
        bad.append("imports dxil.dll")
    # BCD's own source and build paths are mapped away (-ffile-prefix-map in tools/build-dxcompiler.sh). What is left are the file names inside
    # llvm-mingw's prebuilt libc++abi (/Users/runner/work/llvm-mingw/...: the GitHub runner that built the toolchain): any other user's directory is a leak.
    paths = sorted({m.group(0) for m in re.finditer(rb"/(?:Users|home)/([A-Za-z0-9_.-]+)/", data) if m.group(1) != b"runner"}
                   | {m for m in (b"/private/", b"/Volumes/", b"C:\\Users\\") if m in data})
    if paths:
        bad.append("the build machine's paths are inside (%s): the build maps them away with -ffile-prefix-map"
                   % ", ".join(x.decode("latin-1") for x in paths))
    if b"1.9.0." not in data:
        bad.append("no DXC 1.9 version string")
    if not 15 << 20 <= len(data) <= 40 << 20:
        bad.append("%d bytes, outside 15-40 MB" % len(data))
    return bad


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    found = problems(sys.argv[1])
    for p in found:
        print("::error::%s: %s" % (sys.argv[1], p))
    if found:
        sys.exit(1)
    print("dxcompiler: %s is the x86-64 DXC 1.9 DLL (exports %s; imports kernel32, ole32, oleaut32 and the UCRT only)"
          % (sys.argv[1], ", ".join(sorted(EXPORTS))))
