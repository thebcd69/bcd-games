#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""CreateTexture1D/2D/3D refuse a size Direct3D 11 refuses, with E_INVALIDARG, instead of handing it to Metal (night round 2026-11-11, G10 S9).

The Witness (E9TQZT, J5NRJG, build 634), Rise of the Tomb Raider (six logs, builds 625-626), Shadow of the Tomb Raider (25PM6N, CEES6A),
Transformers (WBHMA9) and Danganronpa (TBNGB4) ended with Metal's own abort:
  -[MTLTextureDescriptorInternal validateWithDevice:]: failed assertion `Texture Descriptor Validation
   MTLTextureDescriptor has height (3221225536) greater than the maximum allowed size of 32768.'
(a SIGABRT, which Wine turns into exception 0x80000101 and the process ends with it). The sizes are of the shape 0x80000000 + n (the
game computes them from something it was told: a window left at CW_USEDEFAULT is the suspect, the call site is in no log); Windows'
Direct3D 11 never accepts them: a feature level 11 device refuses a texture over 16384 in any dimension (1D/2D; 3D: 2048) and an array
over 2048 slices (D3D11_REQ_TEXTURE1D_U_DIMENSION, REQ_TEXTURE2D_U_OR_V_DIMENSION, REQ_TEXTURE3D_U_V_OR_W_DIMENSION,
REQ_TEXTURE1D/2D_ARRAY_AXIS_DIMENSION) and returns E_INVALIDARG, which the game can handle (the same game handled an earlier
E_INVALIDARG with its own message and a clean exit).

The guard sits at the top of the three public creation methods; a request inside the limits goes through unchanged (nothing below it
is touched). Every refusal is logged ("[dxmt-tex-limit] CreateTexture2D refused WxH array=N mips=M format=F bind=0xB misc=0xC
prev=WxH,WxH,WxH": the first 8, then powers of two); prev= are the last three CreateTexture2D requests that passed, so the next log
shows whether the garbage is the window's own (CW_USEDEFAULT) size that the game derived it from. 32-bit games get it through the
farm's d3d11.dll, a 64-bit game through d3d11-src.dll (tools/build-d3d11-dll.sh, env.MADEIRA_D3D11_SRC = 1). Idempotent; fails by name
when an anchor moved. Run from the repository root.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd: texture limit"
REL = "d3d11/d3d11_device.cpp"

OLD1 = """    if (pDesc->MiscFlags & D3D11_RESOURCE_MISC_TILED)
      return E_INVALIDARG; // not supported yet

    try {
      switch (pDesc->Usage) {
      case D3D11_USAGE_DEFAULT:
      case D3D11_USAGE_IMMUTABLE:
        return CreateDeviceTexture1D("""
NEW1 = """    if (pDesc->MiscFlags & D3D11_RESOURCE_MISC_TILED)
      return E_INVALIDARG; // not supported yet

    /* """ + MARKER + """ (night round 2026-11-11, G10 S9): Direct3D 11 refuses a 1D texture over 16384 texels or 2048 slices with E_INVALIDARG;
     * Metal aborts the process on it. */
    if (pDesc->Width > 16384u || pDesc->ArraySize > 2048u) {
      static std::atomic<unsigned> refused{0};
      unsigned n = ++refused;
      if (n <= 8 || !(n & (n - 1)))
        ERR("[dxmt-tex-limit] CreateTexture1D refused ", pDesc->Width, " array=", pDesc->ArraySize, " mips=", pDesc->MipLevels,
            " format=", (unsigned)pDesc->Format, " bind=0x", str::format(std::hex, pDesc->BindFlags), " misc=0x", str::format(std::hex, pDesc->MiscFlags));
      return E_INVALIDARG;
    }

    try {
      switch (pDesc->Usage) {
      case D3D11_USAGE_DEFAULT:
      case D3D11_USAGE_IMMUTABLE:
        return CreateDeviceTexture1D("""

OLD2 = """    if ((pDesc->MiscFlags & D3D11_RESOURCE_MISC_TILED))
      return E_INVALIDARG; // not supported yet

    BCCensusRecord(pDesc, pInitialData);   /* ml652: read-only, no behaviour change */
"""
NEW2 = """    if ((pDesc->MiscFlags & D3D11_RESOURCE_MISC_TILED))
      return E_INVALIDARG; // not supported yet

    /* """ + MARKER + """ (night round 2026-11-11, G10 S9: The Witness, Tomb Raider, Transformers, Danganronpa): Direct3D 11 refuses a 2D texture
     * over 16384 texels in a dimension or 2048 slices with E_INVALIDARG; Metal aborts the process on it. prev= are the last three
     * requests that passed (the origin of the garbage sizes is not known: 0x80000000 + n, a window left at CW_USEDEFAULT is the suspect). */
    {
      static std::atomic<uint64_t> prev_sizes[3];
      static std::atomic<unsigned> prev_count{0};
      if (pDesc->Width > 16384u || pDesc->Height > 16384u || pDesc->ArraySize > 2048u) {
        static std::atomic<unsigned> refused{0};
        unsigned n = ++refused;
        if (n <= 8 || !(n & (n - 1))) {
          unsigned p = prev_count.load();
          uint64_t a = prev_sizes[(p + 0) % 3], b = prev_sizes[(p + 1) % 3], c = prev_sizes[(p + 2) % 3];
          ERR("[dxmt-tex-limit] CreateTexture2D refused ", pDesc->Width, "x", pDesc->Height, " array=", pDesc->ArraySize, " mips=", pDesc->MipLevels,
              " format=", (unsigned)pDesc->Format, " bind=0x", str::format(std::hex, pDesc->BindFlags), " misc=0x", str::format(std::hex, pDesc->MiscFlags),
              " prev=", (uint32_t)(a >> 32), "x", (uint32_t)a, ",", (uint32_t)(b >> 32), "x", (uint32_t)b, ",", (uint32_t)(c >> 32), "x", (uint32_t)c);
        }
        return E_INVALIDARG;
      }
      prev_sizes[prev_count++ % 3] = ((uint64_t)pDesc->Width << 32) | pDesc->Height;
    }

    BCCensusRecord(pDesc, pInitialData);   /* ml652: read-only, no behaviour change */
"""

OLD3 = """    if ((pDesc->MiscFlags & D3D11_RESOURCE_MISC_TILED))
      return E_INVALIDARG; // not supported yet

    try {
      switch (pDesc->Usage) {
      case D3D11_USAGE_DEFAULT:
      case D3D11_USAGE_IMMUTABLE:
        return CreateDeviceTexture3D("""
NEW3 = """    if ((pDesc->MiscFlags & D3D11_RESOURCE_MISC_TILED))
      return E_INVALIDARG; // not supported yet

    /* """ + MARKER + """ (night round 2026-11-11, G10 S9): Direct3D 11 refuses a 3D texture over 2048 texels in a dimension with E_INVALIDARG;
     * Metal aborts the process on it. */
    if (pDesc->Width > 2048u || pDesc->Height > 2048u || pDesc->Depth > 2048u) {
      static std::atomic<unsigned> refused{0};
      unsigned n = ++refused;
      if (n <= 8 || !(n & (n - 1)))
        ERR("[dxmt-tex-limit] CreateTexture3D refused ", pDesc->Width, "x", pDesc->Height, "x", pDesc->Depth, " mips=", pDesc->MipLevels,
            " format=", (unsigned)pDesc->Format, " bind=0x", str::format(std::hex, pDesc->BindFlags), " misc=0x", str::format(std::hex, pDesc->MiscFlags));
      return E_INVALIDARG;
    }

    try {
      switch (pDesc->Usage) {
      case D3D11_USAGE_DEFAULT:
      case D3D11_USAGE_IMMUTABLE:
        return CreateDeviceTexture3D("""
PAIRS = ((OLD1, NEW1), (OLD2, NEW2), (OLD3, NEW3))


def main():
    path = ROOT / REL
    if not path.exists():
        sys.exit(f"patch-dxmt-texture-limit: {path} not found (run from the repository root)")
    s = path.read_text()
    if MARKER in s:
        print(f"{REL}: already patched")
        return
    for old, _ in PAIRS:
        if s.count(old) != 1:
            sys.exit("patch-dxmt-texture-limit: an anchor was not found exactly once in " + REL + " (DXMT changed): " + old.strip().splitlines()[-1].strip())
    for old, new in PAIRS:
        s = s.replace(old, new, 1)
    if "#include <atomic>" not in s:
        first = s.index("#include")
        s = s[:first] + "#include <atomic>\n" + s[first:]
    path.write_text(s)
    print(f"{REL}: patched")


main()
