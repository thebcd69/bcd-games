#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A D3D12 texture with a zero width or height is refused (E_INVALIDARG), not handed to Metal (build 580); no Wine runs.

Marvel's Spider-Man Remastered (T3EXJB, L21972) and Miles Morales (X1TNT2, L18933) end with
    -[MTLTextureDescriptorInternal validateWithDevice:]:1421: failed assertion `Texture Descriptor Validation
    MTLTextureDescriptor has width of zero. MTLTextureDescriptor has height of zero.'
which aborts the whole process; Direct3D 12 answers such a description with E_INVALIDARG. madeira-d3d12's
mad_create_resource_at builds the Metal description (mad_texinfo_from_desc) and now refuses a zero extent before
any Metal call, with the game's own description in the log (mad_refuse_log). GetResourceAllocationInfo, the other
caller that hands the description to Metal (heapTextureSizeAndAlign), skips that query for a zero extent.

Checks on the source (madeira-d3d12/src/pe/madeira_d3d12.c):
  - the guard is right after the format mapping, before the first Metal call of the texture branch (the placed
    heap size query, the heap texture, newTexture), frees the half-built resource, logs through mad_refuse_log and
    returns E_INVALIDARG;
  - it tests the Metal description's own width and height (so a Width whose low 32 bits are zero counts too) and
    nothing else: a description that works today (non-zero extent) never reaches the new return;
  - the Metal description cannot have a zero depth or array length (mad_texinfo_from_desc takes 1 for 0), and a
    1D texture's height is forced to 1, so width and height are the whole zero-extent set;
  - GetResourceAllocationInfo's Metal size query needs a non-zero extent.
"""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[2]
src = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


fn_start = src.index('static HRESULT mad_create_resource_at(struct mad_device *d, D3D12_HEAP_TYPE heap_type,\n'
                     '                                      const D3D12_RESOURCE_DESC *desc, REFIID riid, void **out,\n'
                     '                                      struct mad_memheap *ph, UINT64 poff) {')
fn = src[fn_start:src.index('\n}\n', fn_start)]
tex = fn[fn.index('if (desc->Dimension != D3D12_RESOURCE_DIMENSION_BUFFER) {'):]

fmt = tex.index('texture format has no Metal mapping')
guard = tex.index('if (!ti.width || !ti.height)')
heap_q = tex.index('MTLDevice_heapTextureSizeAndAlign')
new_tex = tex.index('MTLDevice_newTexture')
at_off = tex.index('MTLHeap_newTextureAtOffset')
check('the guard follows the format mapping and precedes every Metal texture call of the branch',
      fmt < guard < heap_q and guard < new_tex and guard < at_off)
line = tex[guard:tex.index('\n', guard)]
check('it frees the half-built resource, logs the description (mad_refuse_log) and returns E_INVALIDARG',
      'free(r);' in line and 'mad_refuse_log(desc, heap_type, "zero-size texture' in line and 'return E_INVALIDARG;' in line)
check('it tests only width and height of the Metal description', re.fullmatch(r'if \(!ti\.width \|\| !ti\.height\) \{.*\}', line.strip()) is not None)

info = src[src.index('static int mad_texinfo_from_desc(struct mad_device *d, const D3D12_RESOURCE_DESC *desc, struct WMTTextureInfo *ti, enum WMTPixelFormat *pf, int *is_depth) {'):]
info = info[:info.index('\n}\n')]
check('depth and array length are at least 1 by construction, a 1D height is 1',
      'UINT layers = desc->DepthOrArraySize ? desc->DepthOrArraySize : 1;' in info and 'ti->depth = 1;' in info
      and 'ti->array_length = 1;' in info and 'ti->height = desc->Dimension == D3D12_RESOURCE_DIMENSION_TEXTURE1D ? 1 : desc->Height;' in info
      and 'ti->width = (uint32_t)desc->Width;' in info)

ai = src[src.index('device_GetResourceAllocationInfo('):]
ai = ai[:ai.index('\n}\n')]
check("GetResourceAllocationInfo's Metal size query needs a non-zero extent",
      'if (mad_texinfo_from_desc(md, d, &ti, &pf, &isd) && ti.width && ti.height)' in ai
      and ai.index('ti.width && ti.height') < ai.index('MTLDevice_heapTextureSizeAndAlign'))
print('PASS: d3d12 zero extent' if ok else 'FAIL: d3d12 zero extent')
raise SystemExit(0 if ok else 1)
