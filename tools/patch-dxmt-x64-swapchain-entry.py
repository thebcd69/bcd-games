#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Bogdan Seniuc
# Taken by BCD (thebcd69) from https://github.com/thebcd69/bcd-games (pull request #55 by bogsen), November 2026. See THIRD-PARTY-NOTICES.md.
"""Extend MADEIRA_X64_GRAPHICS_ENTRY to DXMT's D3D11 swapchains.

Ubisoft's overlay patches Present/Present1 with x64 instructions. ARM64EC
native method bodies cannot accept those patches. Use compiler-generated,
typed entries, as the DXGI factory already does, only when explicitly enabled.
"""
from pathlib import Path
import re

REL = Path('dxmt/src/d3d11/d3d11_swapchain.cpp')
MARKER = '/* madeira x64 graphics swapchain begin */'
WRAPPERS = r'''
/* madeira x64 graphics swapchain begin */
#if defined(__arm64ec__)
template <bool EnableMetalFX>
class MTLD3D11HookSwapChain final : public MTLD3D11SwapChain<EnableMetalFX> {
  using Base = MTLD3D11SwapChain<EnableMetalFX>;
public:
  using Base::Base;
  void InitializeEntries() {
    void **original, **replacement = entry_vtable_ + 2;
    void *object = static_cast<IDXGISwapChain4 *>(this);
    __builtin_memcpy(&original, object, sizeof(original));
    // 41 COM slots, two destructors, and two Itanium ABI header words.
    for (unsigned i = 0; i < 45; ++i) entry_vtable_[i] = original[(int)i - 2];
    for (unsigned slot : {8u, 13u, 22u, 39u})
      entry_vtable_[2 + slot] = mad_x64_graphics_entry_pe(original[slot], &__ImageBase);
    __builtin_memcpy(object, &replacement, sizeof(replacement));
  }
  MAD_X64_GRAPHICS_ENTRY HRESULT STDMETHODCALLTYPE Present(UINT sync, UINT flags) override;
  MAD_X64_GRAPHICS_ENTRY HRESULT STDMETHODCALLTYPE Present1(
      UINT sync, UINT flags, const DXGI_PRESENT_PARAMETERS *params) override;
  MAD_X64_GRAPHICS_ENTRY HRESULT STDMETHODCALLTYPE ResizeBuffers(
      UINT count, UINT width, UINT height, DXGI_FORMAT format, UINT flags) override;
  MAD_X64_GRAPHICS_ENTRY HRESULT STDMETHODCALLTYPE ResizeBuffers1(
      UINT count, UINT width, UINT height, DXGI_FORMAT format, UINT flags,
      const UINT *masks, IUnknown *const *queues) override;
private:
  void *entry_vtable_[45];
};

template <bool EnableMetalFX>
HRESULT STDMETHODCALLTYPE MTLD3D11HookSwapChain<EnableMetalFX>::Present(UINT sync, UINT flags) {
  return Base::Present(sync, flags);
}
template <bool EnableMetalFX>
HRESULT STDMETHODCALLTYPE MTLD3D11HookSwapChain<EnableMetalFX>::Present1(
    UINT sync, UINT flags, const DXGI_PRESENT_PARAMETERS *params) {
  return Base::Present1(sync, flags, params);
}
template <bool EnableMetalFX>
HRESULT STDMETHODCALLTYPE MTLD3D11HookSwapChain<EnableMetalFX>::ResizeBuffers(
    UINT count, UINT width, UINT height, DXGI_FORMAT format, UINT flags) {
  return Base::ResizeBuffers(count, width, height, format, flags);
}
template <bool EnableMetalFX>
HRESULT STDMETHODCALLTYPE MTLD3D11HookSwapChain<EnableMetalFX>::ResizeBuffers1(
    UINT count, UINT width, UINT height, DXGI_FORMAT format, UINT flags,
    const UINT *masks, IUnknown *const *queues) {
  return Base::ResizeBuffers1(count, width, height, format, flags, masks, queues);
}
#endif

template <bool EnableMetalFX>
static IDXGISwapChain1 *CreatePatchableSwapChain(
    IDXGIFactory1 *factory, MTLD3D11Device *device, IMTLDXGIDevice *dxgi_device,
    HWND window, const DXGI_SWAP_CHAIN_DESC1 *desc,
    const DXGI_SWAP_CHAIN_FULLSCREEN_DESC *fullscreen) {
#if defined(__arm64ec__)
  if (mad_x64_graphics_entry_enabled()) {
    auto *swap = new MTLD3D11HookSwapChain<EnableMetalFX>(
        factory, device, dxgi_device, window, desc, fullscreen);
    swap->InitializeEntries();
    static std::atomic<bool> noted{false};
    if (!noted.exchange(true))
      Logger::info("[graphics-entry] x64 patchable D3D11 swapchain methods enabled (PE entries)");
    return swap;
  }
#endif
  return new MTLD3D11SwapChain<EnableMetalFX>(
      factory, device, dxgi_device, window, desc, fullscreen);
}
/* madeira x64 graphics swapchain end */

'''


def patch(src):
    if MARKER in src:
        return src
    anchor = '#include "d3d11_swapchain.hpp"\n'
    if src.count(anchor) != 1:
        raise ValueError('swapchain include anchor missing or ambiguous')
    src = src.replace(anchor, anchor + '#include "madeira_graphics_entry.h"\n')
    anchor = 'class MTLD3D11SwapChain final :'
    if src.count(anchor) != 1:
        raise ValueError('swapchain class anchor missing or ambiguous')
    src = src.replace(anchor, 'class MTLD3D11SwapChain :')
    for method in ('Present', 'Present1', 'ResizeBuffers'):
        pattern = rf'(\b{method}\s*\([^{{;]*?\))\s+final\s*\{{'
        src, count = re.subn(pattern, r'\1 override {', src)
        if count != 1:
            raise ValueError(f'{method}: expected one final method, found {count}')
    anchor = '\nHRESULT\nCreateSwapChain(\n'
    if src.count(anchor) != 1:
        raise ValueError('CreateSwapChain anchor missing or ambiguous')
    # Replace only the two original allocation expressions, before inserting
    # the helper, whose default path deliberately keeps the original class.
    for value in ('true', 'false'):
        old = f'new MTLD3D11SwapChain<{value}>('
        if src.count(old) != 1:
            raise ValueError(f'{value}: allocation anchor missing or ambiguous')
        src = src.replace(old, f'CreatePatchableSwapChain<{value}>(')
    return src.replace(anchor, '\n' + WRAPPERS + anchor)


def main():
    before = REL.read_text()
    after = patch(before)
    # Use the same production header as the existing DXGI/D3D12 entries.
    header = Path(__file__).resolve().parents[1] / 'madeira-d3d12/src/pe/madeira_graphics_entry.h'
    REL.with_name('madeira_graphics_entry.h').write_bytes(header.read_bytes())
    if after != before:
        REL.write_text(after)
    print(f'{REL}: ' + ('patched' if after != before else 'already patched'))


if __name__ == '__main__':
    main()
