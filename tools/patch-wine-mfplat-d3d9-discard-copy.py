#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
# Portions copyright 2026 wenze-allen: the change to dlls/mfplat/buffer.c, taken from https://github.com/willfaust/wine (pull request #78, commit
# 34765056b9) under LGPL-2.1-or-later and modified for BCD in November 2026 (written as a build-time script). See THIRD-PARTY-NOTICES.md.
# Copyright 2026 thebcd69 (BCD): the script and its check; BCD's section 7 terms (LICENSE-BCD.md) apply to those, not to the Wine lines it writes.
"""IMF2DBuffer2::ContiguousCopyFrom of a Direct3D 9 surface buffer locks the surface with D3DLOCK_DISCARD (Wine pull request #78 by wenze-allen).

mfplat's d3d9 surface buffer used the generic memory_2d_buffer_ContiguousCopyFrom, which locks the surface with no flags: the surface is
read back first, although the copy then overwrites every byte of it. The sample copier of Media Foundation's EVR path copies each video
frame into such a surface; on DXMT (BCD's Direct3D 9) the read back waits for the GPU (DXMT's MTLD3D9Surface::LockRect calls
readbackSurfaceMirror for a DEFAULT-pool surface unless the lock discards: 9 to 12 ms a frame in the pull request's log, "[d9-drain] ...
avg-us=9300"), on the session's work queue thread that also feeds the audio renderer, and the audio ran short every few seconds.

With this the surface buffer has its own ContiguousCopyFrom: the same bounds check, then one LockRect with D3DLOCK_DISCARD, copy_image
(the same call as the generic one) and UnlockRect. A buffer that is already locked, or that is a linear buffer, takes the generic path as before
(so the lock-state errors and the MF_E_UNEXPECTED of a linear buffer are unchanged). DXMT strips DISCARD from a partial or non-DEFAULT lock
and refuses only DISCARD together with READONLY, so the flag costs nothing where it does not help.

WHERE THIS REACHES: the 32-bit mfplat.dll through the i386 farm and, through tools/build-wine-fresh-dlls.sh, the 64-bit one of the rebuilt set
(env.MADEIRA_WIN64_FRESH = 1); the committed 64-bit mfplat.dll is a frozen binary. Only a program that copies into a d3d9 surface buffer
(MFCreateDXSurfaceBuffer) reaches the new function.
Idempotent (marker "madeira-bcd: mfplat-d3d9-discard-copy-v1"); every anchor must be found exactly once or nothing is written.
Usage: patch-wine-mfplat-d3d9-discard-copy.py WINE-DIR | path/to/wine/dlls/mfplat/buffer.c
"""
from pathlib import Path
import sys

FILE = "dlls/mfplat/buffer.c"
MARKER = "madeira-bcd: mfplat-d3d9-discard-copy-v1"

FUNC = """/* """ + MARKER + """ (tools/patch-wine-mfplat-d3d9-discard-copy.py; Wine pull request #78 by
 * wenze-allen). The whole image is overwritten, so the surface is locked with D3DLOCK_DISCARD: a plain lock
 * reads the surface back first, which waits for the GPU (once per frame the sample copier
 * delivers to the EVR). A buffer that is already locked takes the generic path. */
static HRESULT WINAPI d3d9_surface_buffer_ContiguousCopyFrom(IMF2DBuffer2 *iface, const BYTE *src_buffer,
        DWORD src_length)
{
    struct buffer *buffer = impl_from_IMF2DBuffer2(iface);
    D3DLOCKED_RECT rect;
    HRESULT hr;

    TRACE("%p, %p, %lu.\\n", iface, src_buffer, src_length);

    if (src_length < buffer->_2d.plane_size)
        return E_INVALIDARG;

    EnterCriticalSection(&buffer->cs);

    if (buffer->_2d.linear_buffer || buffer->_2d.locks)
    {
        LeaveCriticalSection(&buffer->cs);
        return memory_2d_buffer_ContiguousCopyFrom(iface, src_buffer, src_length);
    }

    if (SUCCEEDED(hr = IDirect3DSurface9_LockRect(buffer->d3d9_surface.surface, &rect, NULL, D3DLOCK_DISCARD)))
    {
        copy_image(buffer, rect.pBits, rect.Pitch, src_buffer, buffer->_2d.width,
                buffer->_2d.width, buffer->_2d.height);
        IDirect3DSurface9_UnlockRect(buffer->d3d9_surface.surface);
    }

    LeaveCriticalSection(&buffer->cs);

    return hr;
}

"""

VTBL_OLD = """static const IMF2DBuffer2Vtbl d3d9_surface_buffer_vtbl =
{
    memory_2d_buffer_QueryInterface,
    memory_2d_buffer_AddRef,
    memory_2d_buffer_Release,
    d3d9_surface_buffer_Lock2D,
    d3d9_surface_buffer_Unlock2D,
    d3d9_surface_buffer_GetScanline0AndPitch,
    memory_2d_buffer_IsContiguousFormat,
    memory_2d_buffer_GetContiguousLength,
    memory_2d_buffer_ContiguousCopyTo,
    memory_2d_buffer_ContiguousCopyFrom,
    d3d9_surface_buffer_Lock2DSize,
    memory_2d_buffer_Copy2DTo,
};
"""
VTBL_NEW = VTBL_OLD.replace("    memory_2d_buffer_ContiguousCopyFrom,\n", "    d3d9_surface_buffer_ContiguousCopyFrom,\n")


def main():
    arg = Path(sys.argv[1] if len(sys.argv) > 1 else "wine")
    path = arg if arg.suffix == ".c" else arg / FILE
    text = path.read_text()
    if MARKER in text:
        print("mfplat d3d9 discard copy: already patched")
        return
    if text.count(VTBL_OLD) != 1:
        sys.exit("patch-wine-mfplat-d3d9-discard-copy: the d3d9 surface buffer's vtable is not where it was in %s "
                 "(anchor not found exactly once); nothing written" % path)
    # the two functions the new one calls: the generic copy and copy_image
    for need in ("static HRESULT WINAPI memory_2d_buffer_ContiguousCopyFrom(IMF2DBuffer2 *iface, const BYTE *src_buffer, DWORD src_length)\n{",
                 "static void copy_image(const struct buffer *buffer, BYTE *dest, LONG dest_stride, const BYTE *src,\n"):
        if text.count(need) != 1:
            sys.exit("patch-wine-mfplat-d3d9-discard-copy: %r is not where it was in %s; nothing written" % (need.splitlines()[0], path))
    path.write_text(text.replace(VTBL_OLD, FUNC + VTBL_NEW))
    print("mfplat d3d9 discard copy: patched")


if __name__ == "__main__":
    main()
