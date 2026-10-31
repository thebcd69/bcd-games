#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A DirectComposition device that keeps no tree and draws nothing, for dcomp-optin.dll (round 624, agent E2 S2b; Slay the Spire 2).

Slay the Spire 2 (Godot 4.5, Direct3D 12; Send Log BMVD7C, build 622) creates its swap chain for DirectComposition and then hands
it to a composition target of its window (DCompositionCreateDevice, CreateTargetForHwnd, CreateVisual, SetContent, SetRoot,
Commit). Wine's dcomp.dll (dlls/dcomp/device.c) answers E_NOTIMPL to all three DCompositionCreateDevice functions. With DXMT's
dxgi-src.dll serving the composition swap chain as a swap chain of the game's window (tools/patch-dxgi-composition.py,
env.MADEIRA_DXGI_COMPOSITION = 1) the swap chain presents by itself; the composition calls only have to succeed. This file makes
DCompositionCreateDevice return a device whose target and visual accept every call and do nothing (the first 16 calls are
logged as "[dcomp] madeira-bcd null compositor: ..."); every other factory method answers E_NOTIMPL as Wine's stub did, and
DCompositionCreateDevice2/3 stay Wine's stubs. tools/build-wine-extra-dlls.sh (block DCO) builds it as dcomp-optin.dll next to
the shipped dcomp.dll; a game gets it only with env.MADEIRA_OPTIN_DLLS = dcomp.

  patch-wine-dcomp-null.py WINE_TREE                 replace dlls/dcomp/device.c (idempotent; refuses a device.c it does not know)
  patch-wine-dcomp-null.py --verify DLL SHIPPED      the built DLL carries the null compositor and exports the shipped dcomp.dll's names
"""
import importlib.util
import struct
import sys
from pathlib import Path

MARK = "dcomp-null-v1"
SAY = "[dcomp] madeira-bcd null compositor"

STUB_SIGNS = ["WINE_DEFAULT_DEBUG_CHANNEL(dcomp);",
              "HRESULT WINAPI DCompositionCreateDevice(IDXGIDevice *dxgi_device, REFIID iid, void **device)",
              "HRESULT WINAPI DCompositionCreateDevice2(IUnknown *rendering_device, REFIID iid, void **device)",
              "HRESULT WINAPI DCompositionCreateDevice3(IUnknown *rendering_device, REFIID iid, void **device)"]

DEVICE_BODY = r'''/* madeira-bcd dcomp-null-v1 (round 624, agent E2 S2b; Slay the Spire 2, Send Log BMVD7C; tools/patch-wine-dcomp-null.py): a
 * DirectComposition device that keeps no tree and draws nothing. Godot 4's Direct3D 12 driver creates its swap chain for composition
 * (DXMT's dxgi-src.dll serves it as a swap chain of the game's window under env.MADEIRA_DXGI_COMPOSITION = 1, so it presents by
 * itself) and then gives it to a composition target of its window: CreateTargetForHwnd, CreateVisual, SetContent, SetRoot, Commit.
 * Those succeed here and do nothing; every other factory method answers E_NOTIMPL, as this file did before. Shipped as
 * dcomp-optin.dll: only a game with env.MADEIRA_OPTIN_DLLS = dcomp gets it. */

#include <stdarg.h>

#include "windef.h"
#include "winbase.h"
#include "objidl.h"
#include "dxgi.h"
#include "dcomp.h"
#include "wine/debug.h"

WINE_DEFAULT_DEBUG_CHANNEL(dcomp);

static const GUID null_IID_IUnknown = {0x00000000, 0x0000, 0x0000, {0xc0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46}};
static const GUID null_IID_IDCompositionDevice = {0xc37ea93a, 0xe7aa, 0x450d, {0xb1, 0x6f, 0x97, 0x46, 0xcb, 0x04, 0x07, 0xf3}};
static const GUID null_IID_IDCompositionTarget = {0xeacdd04c, 0x117e, 0x4e17, {0x88, 0xf4, 0xd1, 0xb1, 0x2b, 0x0e, 0x3d, 0x89}};
static const GUID null_IID_IDCompositionVisual = {0x4d93059d, 0x097b, 0x4651, {0x9a, 0x60, 0xf0, 0xf2, 0x51, 0x16, 0xe2, 0xf3}};

static LONG null_said;

static void null_say(const char *what, const void *a, const void *b)
{
    if (InterlockedIncrement(&null_said) <= 16)
        ERR("[dcomp] madeira-bcd null compositor: %s %p %p\n", what, a, b);
}

/* ---- IDCompositionTarget ------------------------------------------------------------------------------------------- */

struct null_target
{
    IDCompositionTarget IDCompositionTarget_iface;
    LONG refcount;
};

static struct null_target *impl_from_IDCompositionTarget(IDCompositionTarget *iface)
{
    return CONTAINING_RECORD(iface, struct null_target, IDCompositionTarget_iface);
}

static HRESULT STDMETHODCALLTYPE null_target_QueryInterface(IDCompositionTarget *iface, REFIID iid, void **out)
{
    if (IsEqualGUID(iid, &null_IID_IUnknown) || IsEqualGUID(iid, &null_IID_IDCompositionTarget))
    {
        InterlockedIncrement(&impl_from_IDCompositionTarget(iface)->refcount);
        *out = iface;
        return S_OK;
    }
    *out = NULL;
    return E_NOINTERFACE;
}

static ULONG STDMETHODCALLTYPE null_target_AddRef(IDCompositionTarget *iface)
{
    return InterlockedIncrement(&impl_from_IDCompositionTarget(iface)->refcount);
}

static ULONG STDMETHODCALLTYPE null_target_Release(IDCompositionTarget *iface)
{
    struct null_target *target = impl_from_IDCompositionTarget(iface);
    ULONG refcount = InterlockedDecrement(&target->refcount);

    if (!refcount) HeapFree(GetProcessHeap(), 0, target);
    return refcount;
}

static HRESULT STDMETHODCALLTYPE null_target_SetRoot(IDCompositionTarget *iface, IDCompositionVisual *visual)
{
    null_say("SetRoot", iface, visual);
    return S_OK;
}

static const IDCompositionTargetVtbl null_target_vtbl =
{
    null_target_QueryInterface,
    null_target_AddRef,
    null_target_Release,
    null_target_SetRoot,
};

/* ---- IDCompositionVisual ------------------------------------------------------------------------------------------- */

struct null_visual
{
    IDCompositionVisual IDCompositionVisual_iface;
    LONG refcount;
};

static struct null_visual *impl_from_IDCompositionVisual(IDCompositionVisual *iface)
{
    return CONTAINING_RECORD(iface, struct null_visual, IDCompositionVisual_iface);
}

static HRESULT STDMETHODCALLTYPE null_visual_QueryInterface(IDCompositionVisual *iface, REFIID iid, void **out)
{
    if (IsEqualGUID(iid, &null_IID_IUnknown) || IsEqualGUID(iid, &null_IID_IDCompositionVisual))
    {
        InterlockedIncrement(&impl_from_IDCompositionVisual(iface)->refcount);
        *out = iface;
        return S_OK;
    }
    *out = NULL;
    return E_NOINTERFACE;
}

static ULONG STDMETHODCALLTYPE null_visual_AddRef(IDCompositionVisual *iface)
{
    return InterlockedIncrement(&impl_from_IDCompositionVisual(iface)->refcount);
}

static ULONG STDMETHODCALLTYPE null_visual_Release(IDCompositionVisual *iface)
{
    struct null_visual *visual = impl_from_IDCompositionVisual(iface);
    ULONG refcount = InterlockedDecrement(&visual->refcount);

    if (!refcount) HeapFree(GetProcessHeap(), 0, visual);
    return refcount;
}

static HRESULT STDMETHODCALLTYPE null_visual_SetOffsetXAnimation(IDCompositionVisual *iface, IDCompositionAnimation *animation) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetOffsetX(IDCompositionVisual *iface, float offset_x) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetOffsetYAnimation(IDCompositionVisual *iface, IDCompositionAnimation *animation) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetOffsetY(IDCompositionVisual *iface, float offset_y) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetTransformObject(IDCompositionVisual *iface, IDCompositionTransform *transform) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetTransform(IDCompositionVisual *iface, const D2D_MATRIX_3X2_F *matrix) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetTransformParent(IDCompositionVisual *iface, IDCompositionVisual *visual) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetEffect(IDCompositionVisual *iface, IDCompositionEffect *effect) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetBitmapInterpolationMode(IDCompositionVisual *iface,
        enum DCOMPOSITION_BITMAP_INTERPOLATION_MODE interpolation_mode) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetBorderMode(IDCompositionVisual *iface, enum DCOMPOSITION_BORDER_MODE border_mode) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetClipObject(IDCompositionVisual *iface, IDCompositionClip *clip) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetClip(IDCompositionVisual *iface, const D2D_RECT_F *rect) { return S_OK; }

static HRESULT STDMETHODCALLTYPE null_visual_SetContent(IDCompositionVisual *iface, IUnknown *content)
{
    null_say("SetContent", iface, content);
    return S_OK;
}

static HRESULT STDMETHODCALLTYPE null_visual_AddVisual(IDCompositionVisual *iface, IDCompositionVisual *visual, BOOL insert_above,
        IDCompositionVisual *reference_visual) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_RemoveVisual(IDCompositionVisual *iface, IDCompositionVisual *visual) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_RemoveAllVisuals(IDCompositionVisual *iface) { return S_OK; }
static HRESULT STDMETHODCALLTYPE null_visual_SetCompositeMode(IDCompositionVisual *iface,
        enum DCOMPOSITION_COMPOSITE_MODE composite_mode) { return S_OK; }

static const IDCompositionVisualVtbl null_visual_vtbl =
{
    null_visual_QueryInterface,
    null_visual_AddRef,
    null_visual_Release,
    null_visual_SetOffsetXAnimation,
    null_visual_SetOffsetX,
    null_visual_SetOffsetYAnimation,
    null_visual_SetOffsetY,
    null_visual_SetTransformObject,
    null_visual_SetTransform,
    null_visual_SetTransformParent,
    null_visual_SetEffect,
    null_visual_SetBitmapInterpolationMode,
    null_visual_SetBorderMode,
    null_visual_SetClipObject,
    null_visual_SetClip,
    null_visual_SetContent,
    null_visual_AddVisual,
    null_visual_RemoveVisual,
    null_visual_RemoveAllVisuals,
    null_visual_SetCompositeMode,
};

/* ---- IDCompositionDevice ------------------------------------------------------------------------------------------- */

struct null_device
{
    IDCompositionDevice IDCompositionDevice_iface;
    LONG refcount;
};

static struct null_device *impl_from_IDCompositionDevice(IDCompositionDevice *iface)
{
    return CONTAINING_RECORD(iface, struct null_device, IDCompositionDevice_iface);
}

static HRESULT STDMETHODCALLTYPE null_device_QueryInterface(IDCompositionDevice *iface, REFIID iid, void **out)
{
    if (IsEqualGUID(iid, &null_IID_IUnknown) || IsEqualGUID(iid, &null_IID_IDCompositionDevice))
    {
        InterlockedIncrement(&impl_from_IDCompositionDevice(iface)->refcount);
        *out = iface;
        return S_OK;
    }
    *out = NULL;
    return E_NOINTERFACE;
}

static ULONG STDMETHODCALLTYPE null_device_AddRef(IDCompositionDevice *iface)
{
    return InterlockedIncrement(&impl_from_IDCompositionDevice(iface)->refcount);
}

static ULONG STDMETHODCALLTYPE null_device_Release(IDCompositionDevice *iface)
{
    struct null_device *device = impl_from_IDCompositionDevice(iface);
    ULONG refcount = InterlockedDecrement(&device->refcount);

    if (!refcount) HeapFree(GetProcessHeap(), 0, device);
    return refcount;
}

static HRESULT STDMETHODCALLTYPE null_device_Commit(IDCompositionDevice *iface)
{
    null_say("Commit", iface, NULL);
    return S_OK;
}

static HRESULT STDMETHODCALLTYPE null_device_WaitForCommitCompletion(IDCompositionDevice *iface)
{
    return S_OK;
}

static HRESULT STDMETHODCALLTYPE null_device_GetFrameStatistics(IDCompositionDevice *iface, DCOMPOSITION_FRAME_STATISTICS *statistics)
{
    if (statistics) memset(statistics, 0, sizeof(*statistics));
    return E_NOTIMPL;
}

static HRESULT STDMETHODCALLTYPE null_device_CreateTargetForHwnd(IDCompositionDevice *iface, HWND hwnd, BOOL topmost,
        IDCompositionTarget **target)
{
    struct null_target *object;

    if (!target) return E_POINTER;
    *target = NULL;
    if (!(object = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*object)))) return E_OUTOFMEMORY;
    object->IDCompositionTarget_iface.lpVtbl = &null_target_vtbl;
    object->refcount = 1;
    *target = &object->IDCompositionTarget_iface;
    null_say("CreateTargetForHwnd", hwnd, *target);
    return S_OK;
}

static HRESULT STDMETHODCALLTYPE null_device_CreateVisual(IDCompositionDevice *iface, IDCompositionVisual **visual)
{
    struct null_visual *object;

    if (!visual) return E_POINTER;
    *visual = NULL;
    if (!(object = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*object)))) return E_OUTOFMEMORY;
    object->IDCompositionVisual_iface.lpVtbl = &null_visual_vtbl;
    object->refcount = 1;
    *visual = &object->IDCompositionVisual_iface;
    null_say("CreateVisual", iface, *visual);
    return S_OK;
}

#define NULL_DEVICE_NOTIMPL(name, ...) \
    static HRESULT STDMETHODCALLTYPE null_device_##name(IDCompositionDevice *iface, __VA_ARGS__) \
    { \
        null_say(#name " (not implemented)", iface, NULL); \
        return E_NOTIMPL; \
    }

NULL_DEVICE_NOTIMPL(CreateSurface, UINT width, UINT height, DXGI_FORMAT pixel_format, DXGI_ALPHA_MODE alpha_mode, IDCompositionSurface **surface)
NULL_DEVICE_NOTIMPL(CreateVirtualSurface, UINT width, UINT height, DXGI_FORMAT pixel_format, DXGI_ALPHA_MODE alpha_mode,
        IDCompositionVirtualSurface **surface)
NULL_DEVICE_NOTIMPL(CreateSurfaceFromHandle, HANDLE handle, IUnknown **surface)
NULL_DEVICE_NOTIMPL(CreateSurfaceFromHwnd, HWND hwnd, IUnknown **surface)
NULL_DEVICE_NOTIMPL(CreateTranslateTransform, IDCompositionTranslateTransform **transform)
NULL_DEVICE_NOTIMPL(CreateScaleTransform, IDCompositionScaleTransform **transform)
NULL_DEVICE_NOTIMPL(CreateRotateTransform, IDCompositionRotateTransform **transform)
NULL_DEVICE_NOTIMPL(CreateSkewTransform, IDCompositionSkewTransform **transform)
NULL_DEVICE_NOTIMPL(CreateMatrixTransform, IDCompositionMatrixTransform **transform)
NULL_DEVICE_NOTIMPL(CreateTransformGroup, IDCompositionTransform **transforms, UINT elements, IDCompositionTransform **transform_group)
NULL_DEVICE_NOTIMPL(CreateTranslateTransform3D, IDCompositionTranslateTransform3D **transform_3d)
NULL_DEVICE_NOTIMPL(CreateScaleTransform3D, IDCompositionScaleTransform3D **transform_3d)
NULL_DEVICE_NOTIMPL(CreateRotateTransform3D, IDCompositionRotateTransform3D **transform_3d)
NULL_DEVICE_NOTIMPL(CreateMatrixTransform3D, IDCompositionMatrixTransform3D **transform_3d)
NULL_DEVICE_NOTIMPL(CreateTransform3DGroup, IDCompositionTransform3D **transforms_3d, UINT elements, IDCompositionTransform3D **transform_3d_group)
NULL_DEVICE_NOTIMPL(CreateEffectGroup, IDCompositionEffectGroup **effect_group)
NULL_DEVICE_NOTIMPL(CreateRectangleClip, IDCompositionRectangleClip **clip)
NULL_DEVICE_NOTIMPL(CreateAnimation, IDCompositionAnimation **animation)

static HRESULT STDMETHODCALLTYPE null_device_CheckDeviceState(IDCompositionDevice *iface, BOOL *valid)
{
    if (!valid) return E_POINTER;
    *valid = TRUE;
    return S_OK;
}

static const IDCompositionDeviceVtbl null_device_vtbl =
{
    null_device_QueryInterface,
    null_device_AddRef,
    null_device_Release,
    null_device_Commit,
    null_device_WaitForCommitCompletion,
    null_device_GetFrameStatistics,
    null_device_CreateTargetForHwnd,
    null_device_CreateVisual,
    null_device_CreateSurface,
    null_device_CreateVirtualSurface,
    null_device_CreateSurfaceFromHandle,
    null_device_CreateSurfaceFromHwnd,
    null_device_CreateTranslateTransform,
    null_device_CreateScaleTransform,
    null_device_CreateRotateTransform,
    null_device_CreateSkewTransform,
    null_device_CreateMatrixTransform,
    null_device_CreateTransformGroup,
    null_device_CreateTranslateTransform3D,
    null_device_CreateScaleTransform3D,
    null_device_CreateRotateTransform3D,
    null_device_CreateMatrixTransform3D,
    null_device_CreateTransform3DGroup,
    null_device_CreateEffectGroup,
    null_device_CreateRectangleClip,
    null_device_CreateAnimation,
    null_device_CheckDeviceState,
};

HRESULT WINAPI DCompositionCreateDevice(IDXGIDevice *dxgi_device, REFIID iid, void **device)
{
    struct null_device *object;

    if (!device) return E_POINTER;
    *device = NULL;
    if (!IsEqualGUID(iid, &null_IID_IDCompositionDevice) && !IsEqualGUID(iid, &null_IID_IUnknown))
    {
        FIXME("%p, %s, %p: the null compositor has IDCompositionDevice only.\n", dxgi_device, debugstr_guid(iid), device);
        return E_NOINTERFACE;
    }
    if (!(object = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*object)))) return E_OUTOFMEMORY;
    object->IDCompositionDevice_iface.lpVtbl = &null_device_vtbl;
    object->refcount = 1;
    *device = &object->IDCompositionDevice_iface;
    null_say("DCompositionCreateDevice", dxgi_device, *device);
    return S_OK;
}

HRESULT WINAPI DCompositionCreateDevice2(IUnknown *rendering_device, REFIID iid, void **device)
{
    FIXME("%p, %s, %p.\n", rendering_device, debugstr_guid(iid), device);

    return E_NOTIMPL;
}

HRESULT WINAPI DCompositionCreateDevice3(IUnknown *rendering_device, REFIID iid, void **device)
{
    FIXME("%p, %s, %p.\n", rendering_device, debugstr_guid(iid), device);

    return E_NOTIMPL;
}
'''


def patch(tree):
    path = Path(tree) / "dlls/dcomp/device.c"
    text = path.read_text()
    if MARK in text:
        print("already patched")
        return
    missing = [s for s in STUB_SIGNS if s not in text]
    if missing or text.count("return E_NOTIMPL;") != 3 or len(text.splitlines()) > 70:
        print("::error::patch-wine-dcomp-null: dlls/dcomp/device.c is not the stub this patch replaces (%s)" % missing)
        sys.exit(1)
    # The file's own header (its copyright and licence) stays word for word; only the code after it is replaced.
    head = text[:text.index("#include <stdarg.h>")]
    path.write_text(head + "\n" + DEVICE_BODY)
    print("patched")


def verify(dll, shipped):
    spec = importlib.util.spec_from_file_location("kb", Path(__file__).resolve().parent / "patch-wine-kernelbase-fibertrace.py")
    kb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kb)
    d, machine, names = kb.pe_exports(dll)
    _, smachine, snames = kb.pe_exports(shipped)
    bad = []
    if SAY.encode() not in d:
        bad.append("no '%s' string: not built from the patched device.c" % SAY)
    if machine != smachine:
        bad.append("machine %#x, the shipped dcomp.dll's is %#x" % (machine, smachine))
    if names != snames:
        bad.append("exports differ from the shipped dcomp.dll: only here %s, only there %s" % (sorted(names - snames)[:8], sorted(snames - names)[:8]))
    if bad:
        sys.exit("verify %s:\n  %s" % (dll, "\n  ".join(bad)))
    print("verify %s: the null compositor, the %d exports of %s" % (dll, len(names), shipped))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--verify":
        verify(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 2:
        patch(sys.argv[1])
    else:
        sys.exit(__doc__)
