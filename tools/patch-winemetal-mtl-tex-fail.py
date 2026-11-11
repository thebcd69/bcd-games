#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""winemetal's newTexture refuses a descriptor no Apple GPU accepts before Metal sees it (night round 2026-11-11, G10 S9 (a2)).

The Witness, Rise/Shadow of the Tomb Raider, Transformers and Danganronpa died with Metal's own abort,
  -[MTLTextureDescriptorInternal validateWithDevice:]: failed assertion `Texture Descriptor Validation
   MTLTextureDescriptor has height (3221225536) greater than the maximum allowed size of 32768.'
which is a SIGABRT (Wine reports it as exception 0x80000101, flags 1 = EXCEPTION_WINE_ASSERTION): it is NOT an Objective-C exception,
so an @try/@catch around newTextureWithDescriptor: cannot stop it (the first draft of this solution proposed one; the log's own
"[exc-disp] raise ... code=80000101 flags=1" is the abort handler's). The only way to keep the process alive is to not hand Metal the
descriptor: _MTLDevice_newTexture now returns no texture (ret = 0, as its remote-Metal branch already does when the host cannot make
one) when the descriptor is a real texture (not a buffer texture) with a width or height above 32768, a depth above 2048 or an array
length above 2048. Those are the limits of the newest Apple GPUs; every older one has a lower limit, so nothing a device can make
today is refused (a size between the device's own limit and 32768, e.g. Danganronpa's 32767 on a 16384 device, still reaches Metal:
the Direct3D 11 guard of tools/patch-dxmt-texture-limit.py answers it at 16384 for a game that has it).

This is an abort-avoider and a diagnostic, not a repair: the caller gets a null texture (DXMT's Texture::allocate wraps it unchecked;
a draw that uses it does nothing, Metal calls on nil are no-ops), so the game may still fail a little later, but the log names what
it asked for: "[mtl-tex-fail] madeira-bcd #N: refused WxHxD (array A, mips M, format F, type T, usage 0xU)" (the first 8, then powers
of two). Native only (every game, Direct3D 9, 11 and 12, 32- and 64-bit, no list line). Idempotent; fails by name when the anchor moved.
Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: mtl-tex-fail"

OLD = """  fill_texture_descriptor(desc, info);

  id<MTLTexture> ret = [device newTextureWithDescriptor:desc];
  params->ret = (obj_handle_t)ret;
"""
NEW = """  fill_texture_descriptor(desc, info);

  /* """ + MARKER + """ (night round 2026-11-11, G10 S9: The Witness, Tomb Raider, Transformers, Danganronpa): Metal's descriptor validation
   * aborts the process (SIGABRT, not an exception) on a size beyond the device's limit; refuse the sizes no Apple GPU accepts. */
  if (desc.textureType != MTLTextureTypeTextureBuffer &&
      (desc.width > 32768 || desc.height > 32768 || desc.depth > 2048 || desc.arrayLength > 2048)) {
    static unsigned madeira_tex_refused;
    unsigned n = __atomic_add_fetch(&madeira_tex_refused, 1, __ATOMIC_RELAXED);
    if (n <= 8 || (n & (n - 1)) == 0)
      fprintf(stderr, "[mtl-tex-fail] madeira-bcd #%u: refused %lux%lux%lu (array %lu, mips %lu, format %lu, type %lu, usage 0x%lx): "
              "Metal aborts on a size above every Apple GPU's limit (32768; 3D depth and array length 2048)\\n",
              n, (unsigned long)desc.width, (unsigned long)desc.height, (unsigned long)desc.depth, (unsigned long)desc.arrayLength,
              (unsigned long)desc.mipmapLevelCount, (unsigned long)desc.pixelFormat, (unsigned long)desc.textureType,
              (unsigned long)desc.usage);
    params->ret = 0;
    info->gpu_resource_id = 0;
    info->mach_port = 0;
    [desc release];
    return STATUS_SUCCESS;
  }

  id<MTLTexture> ret = [device newTextureWithDescriptor:desc];
  params->ret = (obj_handle_t)ret;
"""


def main():
    if not PATH.exists():
        sys.exit(f"patch-winemetal-mtl-tex-fail: {PATH} not found (run from the repository root)")
    s = PATH.read_text()
    if MARKER in s:
        print("winemetal_unix.c: already patched")
        return 0
    if s.count(OLD) != 1:
        sys.exit("patch-winemetal-mtl-tex-fail: the _MTLDevice_newTexture anchor was not found exactly once in winemetal_unix.c (dxmt changed)")
    PATH.write_text(s.replace(OLD, NEW, 1))
    print("winemetal_unix.c: _MTLDevice_newTexture refuses impossible texture sizes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
