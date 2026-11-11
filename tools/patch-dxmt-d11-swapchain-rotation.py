#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""IDXGISwapChain1::SetRotation / GetRotation answer the identity rotation instead of ending the process (night round 2026-11-11, G1 S13).

The Battle for Wesnoth 1.18.8 (wesnoth.exe, 64-bit SDL2 + Direct3D 11; Send Log CF2N9T, build 634) stopped 5 s in, at its first
frame: "err: src\\d3d11\\d3d11_swapchain.cpp:SetRotation is not implemented." and NtTerminateProcess(exit_code=0x3). SDL2's Direct3D 11
renderer calls IDXGISwapChain1::SetRotation(DXGI_MODE_ROTATION_IDENTITY) right after it creates a flip-model swap chain, and fails
renderer creation when it does not return S_OK. DXMT's two methods were `{ IMPLEMENT_ME; }`, which logs and abort()s (exit code 3).

A desktop (non-Store) swap chain accepts the identity rotation (and "unspecified") and has no other: SetRotation answers S_OK for
those two and DXGI_ERROR_INVALID_CALL for a real rotation; GetRotation reports DXGI_MODE_ROTATION_IDENTITY (E_INVALIDARG for a null
pointer); the first four SetRotation calls are logged ("[swapchain-rotation] madeira-bcd: SetRotation(n) -> ...", which is also the marker
tools/build-d3d11-dll.sh and the CI look for in the 64-bit DLL). Only a call that aborts the process today changes; the other methods and every game that never calls these two are untouched.
Reaches the 32-bit d3d11.dll through the CI's DXMT patch steps and a 64-bit game through d3d11-src.dll (tools/build-d3d11-dll.sh,
env.MADEIRA_D3D11_SRC = 1): the committed 64-bit d3d11.dll is upstream's binary. Idempotent; fails by name when an anchor moved. Run from
the repository root.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd: d11-swapchain-rotation"
REL = "d3d11/d3d11_swapchain.cpp"

OLD_SET = "  SetRotation(DXGI_MODE_ROTATION Rotation) final { IMPLEMENT_ME; };\n"
NEW_SET = """  SetRotation(DXGI_MODE_ROTATION Rotation) final {
    /* """ + MARKER + """ (night round 2026-11-11, Wesnoth CF2N9T): SDL2's Direct3D 11 renderer calls this after CreateSwapChain;
     * IMPLEMENT_ME aborted the process. A desktop swap chain has the identity rotation only. */
    const bool identity = Rotation == DXGI_MODE_ROTATION_UNSPECIFIED || Rotation == DXGI_MODE_ROTATION_IDENTITY;
    static std::atomic<unsigned> madeira_rotation_calls{0};
    if (++madeira_rotation_calls <= 4)
      Logger::info(str::format("[swapchain-rotation] madeira-bcd: SetRotation(", (unsigned)Rotation, ") -> ", identity ? "S_OK" : "DXGI_ERROR_INVALID_CALL"));
    return identity ? S_OK : DXGI_ERROR_INVALID_CALL;
  };
"""
OLD_GET = "  GetRotation(DXGI_MODE_ROTATION *pRotation) final { IMPLEMENT_ME; };\n"
NEW_GET = """  GetRotation(DXGI_MODE_ROTATION *pRotation) final {
    /* """ + MARKER + """: the identity rotation, as a desktop swap chain reports it */
    if (!pRotation)
      return E_INVALIDARG;
    *pRotation = DXGI_MODE_ROTATION_IDENTITY;
    return S_OK;
  };
"""


def main():
    path = ROOT / REL
    if not path.exists():
        sys.exit(f"patch-dxmt-d11-swapchain-rotation: {path} not found (run from the repository root)")
    s = path.read_text()
    if MARKER in s:
        print(f"{REL}: already patched")
        return
    if s.count(OLD_SET) != 1 or s.count(OLD_GET) != 1:
        sys.exit(f"patch-dxmt-d11-swapchain-rotation: SetRotation / GetRotation not found once each in {REL} (DXMT changed)")
    s = s.replace(OLD_SET, NEW_SET, 1).replace(OLD_GET, NEW_GET, 1)
    if "#include <atomic>" not in s:
        first = s.index("#include")
        s = s[:first] + "#include <atomic>\n" + s[first:]
    path.write_text(s)
    print(f"{REL}: patched")


main()
