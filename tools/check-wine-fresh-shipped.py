#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The rebuilt 64-bit Wine set that is about to ship is sound (round 2026-11-15; tools/build-wine-fresh-dlls.sh runs in a step that may fail without failing the build).

  check-wine-fresh-shipped.py APP_MADEIRA_DIR [LLVM_READOBJ]
      APP_MADEIRA_DIR holds arm64ec-windows and aarch64-windows. For every <name>-fresh.<ext> in a folder (and the ntdll variants):
        - the file it replaces (<name>.<ext>, committed or built) is in the same folder, has the same machine type, and the fresh file carries the
          "Wine builtin DLL" signature of a builtin;
        - it exports every name the file it replaces exports (a rebuild that lost an export would turn a working import into a stub);
        - ntdll-fresh*.dll: the ARM64EC ones keep the two things build/ntdll-unix/virtual_ios.c reads from the image of ntdll without asking: the
          RtlPcToFileHeader export is the x64 fast-forward thunk it patches and its body starts with the prologue it expects, with room for its
          trampoline (ios_patch_rtl_pc_to_file_header: without them the patch refuses and a C++ throw in a pool copy loses its image base);
        - arm64ec kernel32-fresh.dll: tools/verify-kernel32-usd.py (the exports of the committed kernel32.dll, and GetTickCount / GetTickCount64 no longer
          build 0x7ffe0000: a kernel32 without tools/patch-wine-kernel32-usd-direct.py would bring back the fault per tick-count read that kernel32-usd.dll
          took away);
        - a folder that has any fresh file has its ntdll-fresh.dll (the loader and the bridge look for that name).
      Exit 1 naming each problem; prints what it checked.
"""
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wine_fresh_pe", ROOT / "tools/wine-fresh-pe.py")
PEMOD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PEMOD)

FFS = bytes([0x48, 0x8b, 0xc4, 0x48, 0x89, 0x58, 0x20, 0x55, 0x5d, 0xe9])
PROLOGUE = (0xa9be53f3, 0xa9017bf5, 0xaa0003f4)


def readobj():
    if len(sys.argv) > 2:
        return sys.argv[2]
    found = shutil.which("llvm-readobj")
    if found:
        return found
    for tc in sorted((ROOT / "toolchains").glob("llvm-mingw-*/bin")):
        if (tc / "llvm-readobj").is_file():
            return str(tc / "llvm-readobj")
    sys.exit("check-wine-fresh-shipped: llvm-readobj not found")


def machine(path, tool):
    out = subprocess.run([tool, "--file-headers", str(path)], capture_output=True, text=True).stdout
    m = re.search(r"Machine:\s*(\S+)", out)
    return m.group(1) if m else None


def exports(path, tool):
    out = subprocess.run([tool, "--coff-exports", str(path)], capture_output=True, text=True).stdout
    names, rvas = set(), {}
    cur = None
    for line in out.splitlines():
        m = re.match(r"\s*Name:\s*(\S+)", line)
        if m:
            cur = m.group(1)
            names.add(cur)
        m = re.match(r"\s*RVA:\s*(0x[0-9A-Fa-f]+)", line)
        if m and cur:
            rvas[cur] = int(m.group(1), 16)
            cur = None
    return names, rvas


def pc_to_file_header_ok(path, rvas):
    """the preconditions of build/ntdll-unix/virtual_ios.c ios_patch_rtl_pc_to_file_header, read from the file"""
    d = Path(path).read_bytes()
    p = PEMOD.PE(d)
    rva = rvas.get("RtlPcToFileHeader")
    if rva is None:
        return "no RtlPcToFileHeader export"
    off = p.rva2off(rva)
    if off is None or d[off:off + 10] != FFS:
        return "the RtlPcToFileHeader export is not the x64 fast-forward thunk the runtime patch expects"
    rel = struct.unpack_from("<i", d, off + 10)[0]
    body = rva + 14 + rel
    boff = p.rva2off(body)
    if boff is None or struct.unpack_from("<3I", d, boff) != PROLOGUE:
        return "the body of RtlPcToFileHeader does not start with the prologue the runtime patch expects"
    sec = next(((va, vs) for _n, va, vs, _rp, _rs in p.sections if va <= body < va + vs), None)
    if not sec:
        return "the body of RtlPcToFileHeader is in no section"
    gap_end = min([va for _n, va, _vs, _rp, _rs in p.sections if va > sec[0]] + [p.size_of_image])
    lo = ((sec[0] + sec[1] + 15) & ~15) + 16
    hi = min((sec[0] + sec[1] + 0x3fff) & ~0x3fff, gap_end)
    if lo + 48 > hi:
        return "no room for the runtime patch's trampoline after .text (%#x..%#x)" % (lo, hi)
    return None


def kernel32_usd_ok(fresh, base, tool):
    """the rebuilt ARM64EC kernel32 carries the tick-count patch (tools/verify-kernel32-usd.py: it needs llvm-objdump beside llvm-readobj)"""
    verifier = ROOT / "tools/verify-kernel32-usd.py"
    if not verifier.is_file():
        return None
    env = dict(os.environ, PATH=os.path.dirname(os.path.realpath(tool)) + os.pathsep + os.environ.get("PATH", ""))
    r = subprocess.run([sys.executable, str(verifier), str(fresh), str(base)], capture_output=True, text=True, env=env)
    if r.returncode:
        return (r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout + r.stderr).strip() else "tools/verify-kernel32-usd.py failed"
    return None


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    app = Path(sys.argv[1])
    tool = readobj()
    problems, checked = [], 0
    for folder in ("arm64ec-windows", "aarch64-windows"):
        d = app / folder
        if not d.is_dir():
            continue
        fresh = sorted(f for f in os.listdir(d) if re.fullmatch(r".+-fresh(-sync|-xsec)?\.(dll|exe|drv|cpl)", f))
        if fresh and not (d / "ntdll-fresh.dll").is_file():
            problems.append("%s has fresh files but no ntdll-fresh.dll" % folder)
        for f in fresh:
            m = re.fullmatch(r"(.+?)-fresh(-sync|-xsec)?\.(dll|exe|drv|cpl)", f)
            base = d / ("%s.%s" % (m.group(1), m.group(3)))
            if not base.is_file():
                problems.append("%s/%s: the file it replaces (%s) is not in the folder" % (folder, f, base.name))
                continue
            checked += 1
            if b"Wine builtin DLL" not in (d / f).read_bytes()[:0x400]:
                problems.append("%s/%s lacks the \"Wine builtin DLL\" signature the loader looks for in a builtin's DOS header" % (folder, f))
            if machine(d / f, tool) != machine(base, tool):
                problems.append("%s/%s: machine type %s, the file it replaces has %s" % (folder, f, machine(d / f, tool), machine(base, tool)))
            fn, frva = exports(d / f, tool)
            bn, _ = exports(base, tool)
            if bn - fn:
                problems.append("%s/%s lacks exports of %s: %s" % (folder, f, base.name, " ".join(sorted(bn - fn)[:8])))
            if m.group(1) == "ntdll" and folder == "arm64ec-windows":
                why = pc_to_file_header_ok(d / f, frva)
                if why:
                    problems.append("%s/%s: %s" % (folder, f, why))
            if m.group(1) == "kernel32" and not m.group(2) and folder == "arm64ec-windows":
                why = kernel32_usd_ok(d / f, base, tool)
                if why:
                    problems.append("%s/%s: %s" % (folder, f, why))
    for p in problems:
        print("::error::" + p)
    if problems:
        sys.exit(1)
    print("rebuilt 64-bit Wine set: %d fresh files checked (machine type, exports, ntdll's runtime-patch preconditions, kernel32's tick-count patch)" % checked)


if __name__ == "__main__":
    main()
