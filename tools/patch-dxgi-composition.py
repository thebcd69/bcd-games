#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Serve a DirectComposition swap chain as a swap chain of the game's window, for one game (round 624, agent E2 S2b).

Slay the Spire 2 (Godot 4.5, Direct3D 12; Send Log BMVD7C, build 622, and 20 more archive logs of builds 600-622): Godot's
Direct3D 12 driver calls IDXGIFactory2::CreateSwapChainForComposition; DXMT's factory (dxmt/src/dxgi/dxgi_factory.cpp) answers
ERR("Not implemented") and E_NOTIMPL, and Godot stops at "swap_chain_resize ... ERR_CANT_CREATE". With
env.MADEIRA_DXGI_COMPOSITION = 1 the method looks for the window the swap chain belongs to (the calling thread's visible
top-level window of the largest client area; else the foreground window if this process owns it) and returns
CreateSwapChainForHwnd on it, with the alpha mode ignored and stretch scaling (madeira-d3d12 presents to the window; the
composition tree Godot builds next goes to dcomp-optin.dll's null compositor, tools/patch-wine-dcomp-null.py). Without the key the
method is exactly what it was (the same text, the same answer), for every game.

Applied by tools/build-dxgi-dll.sh to its copy of the factory (dxgi-src.dll, env.MADEIRA_DXGI_SRC = 1). Idempotent (marker
"dxgi-composition-v1"); exits 1 naming the anchor when DXMT changed.

  patch-dxgi-composition.py dxgi_factory.cpp
"""
import sys
from pathlib import Path

MARK = "dxgi-composition-v1"

OLD = '''  HRESULT STDMETHODCALLTYPE CreateSwapChainForComposition(
      IUnknown *pDevice, const DXGI_SWAP_CHAIN_DESC1 *pDesc,
      IDXGIOutput *pRestrictToOutput, IDXGISwapChain1 **ppSwapChain) override {
    InitReturnPtr(ppSwapChain);

    ERR("Not implemented");
    return E_NOTIMPL;
  }
'''

NEW = '''  HRESULT STDMETHODCALLTYPE CreateSwapChainForComposition(
      IUnknown *pDevice, const DXGI_SWAP_CHAIN_DESC1 *pDesc,
      IDXGIOutput *pRestrictToOutput, IDXGISwapChain1 **ppSwapChain) override {
    InitReturnPtr(ppSwapChain);

    /* madeira-bcd dxgi-composition-v1 (round 624; Slay the Spire 2, Godot 4.5 BMVD7C): env.MADEIRA_DXGI_COMPOSITION = 1 serves a
     * composition swap chain as a swap chain of the game's window (tools/patch-dxgi-composition.py); unset, today's answer. */
    if (!madeira_composition_wanted()) {
      ERR("Not implemented");
      return E_NOTIMPL;
    }
    if (!ppSwapChain || !pDesc || !pDevice)
      return DXGI_ERROR_INVALID_CALL;
    HWND madeira_window = madeira_composition_window();
    if (!madeira_window) {
      ERR("[dxgi-composition] madeira-bcd: no visible window of this thread or process for a composition swap chain");
      return DXGI_ERROR_UNSUPPORTED;
    }
    DXGI_SWAP_CHAIN_DESC1 madeira_desc = *pDesc;
    madeira_desc.AlphaMode = DXGI_ALPHA_MODE_IGNORE;
    madeira_desc.Scaling = DXGI_SCALING_STRETCH;
    HRESULT madeira_hr = CreateSwapChainForHwnd(pDevice, madeira_window, &madeira_desc, nullptr, pRestrictToOutput, ppSwapChain);
    ERR("[dxgi-composition] madeira-bcd: composition swap chain ", madeira_desc.Width, "x", madeira_desc.Height, " (format ",
        (unsigned)madeira_desc.Format, ", ", madeira_desc.BufferCount, " buffers) served on window ", (void *)madeira_window,
        ": hr ", (void *)(uintptr_t)(unsigned)madeira_hr);
    return madeira_hr;
  }
'''

HELPERS_ANCHOR = "class MTLDXGIFactory"

HELPERS = '''/* madeira-bcd dxgi-composition-v1 (round 624, tools/patch-dxgi-composition.py): read once; only the exact value 1 turns it on. */
static bool madeira_composition_wanted() {
  static int wanted = -1;
  if (wanted < 0) {
    char v[4] = {0};
    DWORD n = GetEnvironmentVariableA("MADEIRA_DXGI_COMPOSITION", v, sizeof(v));
    wanted = (n == 1 && v[0] == '1') ? 1 : 0;
  }
  return wanted == 1;
}

/* The window a composition swap chain is shown on: the calling thread's visible top-level window with the largest client area
 * (Godot creates its swap chain on the thread that owns its window), else the foreground window if this process owns it. */
struct madeira_window_pick {
  HWND best;
  LONGLONG area;
};

static BOOL CALLBACK madeira_pick_window(HWND hwnd, LPARAM param) {
  auto *pick = reinterpret_cast<madeira_window_pick *>(param);
  RECT r;
  if (!IsWindowVisible(hwnd) || !GetClientRect(hwnd, &r))
    return TRUE;
  LONGLONG area = (LONGLONG)(r.right - r.left) * (LONGLONG)(r.bottom - r.top);
  if (area > pick->area) {
    pick->best = hwnd;
    pick->area = area;
  }
  return TRUE;
}

static HWND madeira_composition_window() {
  madeira_window_pick pick = {nullptr, 0};
  EnumThreadWindows(GetCurrentThreadId(), madeira_pick_window, (LPARAM)&pick);
  if (pick.best)
    return pick.best;
  HWND foreground = GetForegroundWindow();
  DWORD pid = 0;
  if (foreground && GetWindowThreadProcessId(foreground, &pid) && pid == GetCurrentProcessId())
    return foreground;
  return nullptr;
}

'''


def main(path):
    p = Path(path)
    text = p.read_text()
    if MARK in text:
        print("already patched")
        return
    if text.count(OLD) != 1:
        print("::error::patch-dxgi-composition: CreateSwapChainForComposition is not the stub this patch replaces (found %d)" % text.count(OLD))
        sys.exit(1)
    at = text.find("\n" + HELPERS_ANCHOR)
    if at < 0:
        print("::error::patch-dxgi-composition: no '%s' to put the helpers before" % HELPERS_ANCHOR)
        sys.exit(1)
    text = text[:at + 1] + HELPERS + text[at + 1:]
    text = text.replace(OLD, NEW, 1)
    p.write_text(text)
    print("patched")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
