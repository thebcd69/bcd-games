#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A shader resource view on a device texture reports the real MipLevels / ArraySize from GetDesc (opt-in).

Dark Souls III (Steam 374320; 10 logs of builds 565-622, EQKH79 and SJ0TZ6 of build 622) ends 26 s in with a write through NULL
at DarkSoulsIII.exe+0x176d65b: the game creates SRVs with MipLevels = -1 (Direct3D 11: "all mips from MostDetailedMip"), reads
the description back with GetDesc and sizes an array with the count. DXMT keeps the logical description unresolved (only the
translated copy goes through InitializeAndNormalizeViewDescriptor) and returned 0xFFFFFFFF; Windows returns the resolved
count. With env.MADEIRA_SRV_GETDESC_RESOLVE = 1 (or the DXMT option d3d11.srvGetDescResolve=1) CreateShaderResourceView on a
device texture resolves MipLevels (and ArraySize) = -1 in the description the view keeps; the Metal view is made from the
translated copy as before. Off by default: nothing changes for a game without the line. The first resolutions are logged
([srv-getdesc]). A 64-bit game gets it with env.MADEIRA_D3D11_SRC = 1 (the CI compiles the 32-bit d3d11.dll from the patched
sources and tools/build-d3d11-dll.sh the 64-bit d3d11-src.dll).

Idempotent; fails by name if an anchor moved. Run from the repository root.
"""
import pathlib
import sys

ROOT = pathlib.Path("dxmt/src")
MARKER = "madeira-bcd: SRV GetDesc resolves"
DEV = ROOT / "d3d11/d3d11_texture_device.cpp"

NS_OLD = "\nnamespace dxmt {\n\n#pragma region DeviceTexture\n"
NS_NEW = r'''
namespace dxmt {

/* madeira-bcd: SRV GetDesc resolves MipLevels / ArraySize = -1 (Dark Souls III, builds 565-622: it sized an array with the
 * 0xFFFFFFFF GetDesc returned). env.MADEIRA_SRV_GETDESC_RESOLVE = 1 (or d3d11.srvGetDescResolve=1); off by default. */
namespace {

bool MadeiraSrvResolveOn() {
  static const bool on = [] {
    const std::string env_v = env::getEnvVar("MADEIRA_SRV_GETDESC_RESOLVE");
    if (!env_v.empty())
      return env_v != "0";
    return Config::getInstance().getOption<int>("d3d11.srvGetDescResolve", 0) > 0;
  }();
  return on;
}

template <typename SrvDesc>
void MadeiraResolveSrvCounts(SrvDesc &d, uint32_t mips, uint32_t slices) {
  static std::atomic<unsigned> resolved_n{0};
  auto fix = [&](UINT &count, UINT first, uint32_t total) {
    if (~count != 0)
      return;
    UINT real = total > first ? total - first : 1;
    unsigned n = ++resolved_n;
    if (n <= 8 || !(n & (n - 1)))
      ERR("[srv-getdesc] madeira-bcd #", n, " view dimension ", (unsigned)d.ViewDimension, ": count 0xffffffff -> ", real,
          " (first ", first, ", resource total ", total, ")");
    count = real;
  };
  switch (d.ViewDimension) {
  case D3D11_SRV_DIMENSION_TEXTURE1D:
    fix(d.Texture1D.MipLevels, d.Texture1D.MostDetailedMip, mips);
    break;
  case D3D11_SRV_DIMENSION_TEXTURE1DARRAY:
    fix(d.Texture1DArray.MipLevels, d.Texture1DArray.MostDetailedMip, mips);
    fix(d.Texture1DArray.ArraySize, d.Texture1DArray.FirstArraySlice, slices);
    break;
  case D3D11_SRV_DIMENSION_TEXTURE2D:
    fix(d.Texture2D.MipLevels, d.Texture2D.MostDetailedMip, mips);
    break;
  case D3D11_SRV_DIMENSION_TEXTURE2DARRAY:
    fix(d.Texture2DArray.MipLevels, d.Texture2DArray.MostDetailedMip, mips);
    fix(d.Texture2DArray.ArraySize, d.Texture2DArray.FirstArraySlice, slices);
    break;
  case D3D11_SRV_DIMENSION_TEXTURE2DMSARRAY:
    fix(d.Texture2DMSArray.ArraySize, d.Texture2DMSArray.FirstArraySlice, slices);
    break;
  case D3D11_SRV_DIMENSION_TEXTURE3D:
    fix(d.Texture3D.MipLevels, d.Texture3D.MostDetailedMip, mips);
    break;
  case D3D11_SRV_DIMENSION_TEXTURECUBE:
    fix(d.TextureCube.MipLevels, d.TextureCube.MostDetailedMip, mips);
    break;
  case D3D11_SRV_DIMENSION_TEXTURECUBEARRAY:
    fix(d.TextureCubeArray.MipLevels, d.TextureCubeArray.MostDetailedMip, mips);
    break;
  default:
    break;
  }
}

} // namespace

#pragma region DeviceTexture
'''

CALL_OLD = """    TextureViewKey key = underlying_texture_->createView(descriptor);
    *ppView = ref(new TextureSRV(key, &finalDesc, this, this->m_parent));
    return S_OK;
"""
CALL_NEW = """    TextureViewKey key = underlying_texture_->createView(descriptor);
    if (MadeiraSrvResolveOn())   /* madeira-bcd: SRV GetDesc resolves MipLevels / ArraySize = -1 */
      MadeiraResolveSrvCounts(finalDesc, this->desc.MipLevels, arraySize);
    *ppView = ref(new TextureSRV(key, &finalDesc, this, this->m_parent));
    return S_OK;
"""


def main():
    text = DEV.read_text()
    if MARKER in text:
        print("already patched:", DEV)
        return 0
    if text.count(NS_OLD) != 1:
        sys.exit("anchor not found (namespace dxmt / DeviceTexture) in " + str(DEV))
    if text.count(CALL_OLD) != 1:
        sys.exit("anchor not found (TextureSRV creation in CreateShaderResourceView) in " + str(DEV))
    text = text.replace(NS_OLD, NS_NEW).replace(CALL_OLD, CALL_NEW)
    DEV.write_text(text)
    print("patched:", DEV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
