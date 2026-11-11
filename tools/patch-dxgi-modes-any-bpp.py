#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""IDXGIOutput::GetDisplayModeList for a format whose bits per pixel differ from the monitor's modes lists them anyway (opt-in at run time:
env.DXMT_MODE_LIST_ANY_BPP = 1; night round 2026-11-11, M14 = A1 S6 step 2, Crash Bandicoot N. Sane Trilogy).

Crash Bandicoot N. Sane Trilogy (64-bit D3D11 through DXMT; XQ53FC build 634, 3CH670 build 579, SWC63G build 634) stops 3 s into its start:
the game builds a vector of display modes, takes its LAST element without a check (`mov eax,[rdx+rax*8]` at +0x2fef7a, index count-1 of an
empty vector: address 0xFFFFFFFFFFFFFFE8) and reads from it. DXMT's GetDisplayModeList1 returns zero modes in three cases: the
format is DXGI_FORMAT_UNKNOWN, it is not a back-buffer format, or its bits per pixel (GetMonitorFormatBpp: 32 for the 8888 and 10:10:10:2
formats, 64 for R16G16B16A16_FLOAT) differ from the monitor's modes' (BCD's mode list is 32 bpp). If the engine asks for the FP16 list
(an HDR probe) it gets none and takes the empty vector's last element. Which case it is is not known: round 630's DXMT_WSI_MONITOR_IDENTITY /
DXMT_WSI_MODE_TABLE lines and the DXMT_DISPLAY_MODE_STATS log ("[dxgi-modes] ml1190 monitor= format= count=") decide between the monitor-handle
and the format candidates first; this is the second step for the format one.

With env.DXMT_MODE_LIST_ANY_BPP = 1 (default off: nothing changes for a game without the line) a mode whose bits per pixel differ from the
requested format's is listed too, with the requested format, instead of being skipped; the first four such modes are logged
("[dxgi-modes] madeira-bcd any-bpp #N: format F wants B bpp, the monitor's WxH mode has b: listed"). A game that asks for the FP16 list to find out
whether HDR exists would then see modes: only for the game whose file sets the key.

Applied by tools/build-dxgi-dll.sh to a COPY of dxgi_output.cpp (a missed anchor warns and the build goes on without it); the 64-bit dxgi.dll is
upstream's binary, so the game's line is env.MADEIRA_DXGI_SRC = 1 together with the key. Idempotent; exits 1 with a message if the anchor is missing.
Usage: patch-dxgi-modes-any-bpp.py <copy of dxmt/src/dxgi/dxgi_output.cpp>
"""
import sys

MARKER = "madeira-bcd: mode list any bpp"

OLD = """      // Skip modes with incompatible formats
      if (devMode.bitsPerPixel != GetMonitorFormatBpp(EnumFormat))
        continue;
"""
NEW = """      // Skip modes with incompatible formats
      if (devMode.bitsPerPixel != GetMonitorFormatBpp(EnumFormat)) {
        /* """ + MARKER + """ (night round 2026-11-11, Crash Bandicoot N. Sane Trilogy): opt-in, env.DXMT_MODE_LIST_ANY_BPP = 1. */
        static const bool anyBpp = [] {
          const std::string v = env::getEnvVar("DXMT_MODE_LIST_ANY_BPP");
          return !v.empty() && v != "0";
        }();
        if (!anyBpp)
          continue;
        static std::atomic<unsigned> admitted{0};
        unsigned an = admitted.fetch_add(1, std::memory_order_relaxed) + 1;
        if (an <= 4)
          Logger::warn(str::format("[dxgi-modes] madeira-bcd any-bpp #", an, ": format ", unsigned(EnumFormat), " wants ",
              GetMonitorFormatBpp(EnumFormat), " bpp, the monitor's ", devMode.width, "x", devMode.height, " mode has ",
              devMode.bitsPerPixel, ": listed"));
      }
"""


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-dxgi-modes-any-bpp.py <copy of dxmt/src/dxgi/dxgi_output.cpp>")
    path = sys.argv[1]
    s = open(path).read()
    if MARKER in s:
        print(path + ": already patched")
        return 0
    if s.count(OLD) != 1:
        print("::error::patch-dxgi-modes-any-bpp: the bits-per-pixel filter of GetDisplayModeList1 was not found exactly once in " + path + " (dxmt moved)")
        return 1
    open(path, "w").write(s.replace(OLD, NEW, 1))
    print(path + ": mode lists with any bits per pixel (env.DXMT_MODE_LIST_ANY_BPP) added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
