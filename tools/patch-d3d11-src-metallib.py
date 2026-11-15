#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT's internal shaders compiled from the pin, chosen by env.MADEIRA_DXMT_FRESH = 1 (d3d11-src.dll and d3d9-dxmt.dll).

The DXMT core embeds one Metal library of its own shaders (src/dxmt/dxmt_command.metal: the clear kernels, the GS/TS draw-argument
marshals, the presenter's and the blit/resolve functions) and loads it once per device (InternalCommandLibrary). tools/build-d3d11-dll.sh
and tools/build-d3d9-x64-dll.sh take that library byte for byte out of upstream's committed 64-bit d3d11.dll (built 2026-10-14 from
dxmt 2dc8d16), so a change to dxmt_command.metal reaches no 64-bit DLL: the GS/TS marshal fix (dxmt d811847f, tools/patch-dxmt-gsts-
marshal.py; a batch of two or more geometry/tessellation draws spun on its first task until the GPU timed out, Dark Souls III froze in
combat) runs in every 32-bit game and in the native Direct3D 9 frontend, and in no 64-bit one.

The build scripts now also compile dxmt_command.metal from the sources they build (with the flags of DXMT's meson build and of
build/dxmt-ios/build.sh: -std=metal3.1 --target=air64-apple-macos14.0) into dxmt_command_fresh.h. This patch, applied to the
script's COPY of dxmt_command.cpp, makes InternalCommandLibrary load that library instead of the embedded upstream one when the
process has MADEIRA_DXMT_FRESH=1 (the app sets it for a session whose four fresh DXMT DLLs it linked, WineProcessBridge.m), and say
so once ("[dxmt-shaders] madeira-bcd"). Without the variable the upstream library loads exactly as before.

Exits 1, leaving the file as it was, if an anchor moved. Idempotent.

Usage: patch-d3d11-src-metallib.py <copy of dxmt_command.cpp>
"""
import pathlib
import sys

MARKER = "madeira-bcd: [dxmt-shaders]"
INC_OLD = '#include "dxmt_command.h"\n'
INC_NEW = ('#include "dxmt_command.h"\n'
           '/* madeira-bcd: [dxmt-shaders] the same library compiled from the pin (tools/patch-d3d11-src-metallib.py) */\n'
           '#include "dxmt_command_fresh.h"\n'
           '#include "util_env.hpp"\n')
LOAD_OLD = """  library_ = device.newLibrary(dxmt_command, dxmt_command_len, error);
"""
LOAD_NEW = """  /* madeira-bcd: [dxmt-shaders] env MADEIRA_DXMT_FRESH=1 (the fresh 64-bit DXMT set, WineProcessBridge.m): DXMT's internal shaders as
   * compiled from this DLL's own sources (dxmt_command_fresh: the GS/TS marshal fix d811847f among them) instead of the library
   * taken out of upstream's committed d3d11.dll (dxmt_command). Unset: the embedded upstream library, as before. */
  static const bool madeira_fresh = env::getEnvVar("MADEIRA_DXMT_FRESH") == "1";
  static bool madeira_said = false;
  if (!madeira_said) {
    madeira_said = true;
    WARN("[dxmt-shaders] madeira-bcd: DXMT's internal shaders: ", madeira_fresh
             ? "compiled from this DLL's sources (MADEIRA_DXMT_FRESH=1), " : "upstream's committed library, ",
         madeira_fresh ? dxmt_command_fresh_len : dxmt_command_len, " bytes");
  }
  if (madeira_fresh)
    library_ = device.newLibrary(dxmt_command_fresh, dxmt_command_fresh_len, error);
  else
    library_ = device.newLibrary(dxmt_command, dxmt_command_len, error);
"""


def main():
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1])
        return 2
    path = pathlib.Path(sys.argv[1])
    s = path.read_text()
    if MARKER in s:
        print(f"{path.name}: [dxmt-shaders] already patched")
        return 0
    for old in (INC_OLD, LOAD_OLD):
        if s.count(old) != 1:
            print(f"patch-d3d11-src-metallib: anchor not found once in {path}: {old.strip()[:70]!r}")
            return 1
    s = s.replace(INC_OLD, INC_NEW, 1).replace(LOAD_OLD, LOAD_NEW, 1)
    path.write_text(s)
    print(f"{path.name}: [dxmt-shaders] MADEIRA_DXMT_FRESH=1 loads the library compiled from the pin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
