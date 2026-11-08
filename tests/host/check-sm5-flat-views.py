#!/usr/bin/env python3
"""ml1308 (Madeira 1390345, spitefulowl): Direct3D 12 DXBC texture2d bindings get a 2D view; madeira-bcd: automatic below Apple10.

Every 1D/2D texture is an array and every SRV/UAV view an array view (ml932), which Apple's converter wants for DXIL. A DXBC
shader declares texture2d instead, and on GPUs older than Apple10 a sample through the array view reads the texture's origin:
Spider-Man Remastered and Miles Morales drew one colour per pass on A17 Pro / A18 / M3 / M4 devices (BCD logs BZQMTZ, 6NN9D6,
PD7S1E...), while the owner's Apple10 phone draws them. madeira_d3d12.c binds a 2D sibling view when the stage did not declare
an array (MADEIRA_IR_AIR_F_TEXTURE_ARRAY); bcd.cfg sm5-flat-views: 2 (default) = on only where the GPU lacks Apple10,
1 = on, 0 = off. This checks the wiring, runs the real default decision line for every case, and checks the catalog entry.
Only madeira-d3d12 changes: the DXMT (Direct3D 9/10/11) paths are untouched.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
src = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
fails = []


def need(cond, what):
    if not cond:
        fails.append(what)


need(re.search(r'struct mad_viewrec \{ UINT64 id; UINT32 a, b; UINT64 flat; \}', src), 'view map entry carries the flat sibling')
need('d->vmap[i].id = id; d->vmap[i].a = a; d->vmap[i].b = b; d->vmap[i].flat = 0;' in src, 'a new view entry starts with no sibling')
fn = src.find('static UINT64 mad_air_flat_view(struct mad_device *d, UINT64 id) {')
arr = src.find('static UINT mad_air_array_len(struct mad_device *d, UINT64 view_id) {')
need(0 < fn < arr, 'mad_air_flat_view is defined before mad_air_array_len')
body = src[fn:arr]
need('if (!id || id == MAD_VREC_TOMB) return id;' in body, 'no view / a dead view is passed through')
need('if (type == (UINT)WMTTextureType2DArray && r->desc.Dimension == D3D12_RESOURCE_DIMENSION_TEXTURE2D) want = WMTTextureType2D;' in body,
     'a 2D array view of a 2D texture gets a 2D sibling')
need('else if (type == (UINT)WMTTextureType2DMultisampleArray) want = WMTTextureType2DMultisample;' in body,
     'a multisample array view gets a multisample sibling')
need('mad_texture_view_id(d, r, want, lvl0, nlvl, sl0, 1, (enum WMTPixelFormat)pf, swz)' in body, 'the sibling covers the same levels, first slice')
need(src.count('#define MAD_SWZ_IDENTITY') == 1, 'MAD_SWZ_IDENTITY is defined once')
need('if (g_flat_views && !(rg->flags & MADEIRA_IR_AIR_F_TEXTURE_ARRAY)) vid = mad_air_flat_view(d, vid);   /* ml1308 */' in src,
     'only a stage that did not declare an array gets the sibling')
need('tab[off + 0] = vid;' in src and 'tab[off + 1] = ((UINT64)mad_air_array_len(d, vid) << 32)' in src,
     'the table entry and its array length use the bound view')
need('mad_cfg_int_pe("sm5-flat-views", 2)' in src, 'sm5-flat-views defaults to 2 (automatic)')
need('#define MAD_MTL_FAMILY_APPLE10_FV 1010' in src, 'Apple10 is MTLGPUFamilyApple10 (1010)')
need('MTLDevice_supportsFamily(d->mtl_device, MAD_MTL_FAMILY_APPLE10_FV)' in src, 'the device is asked for Apple10')

# Run the real decision line for every case.
m = re.search(r'\n\s*(g_flat_views = fv == 2 \? !apple10 : fv \? 1 : 0;)', src)
need(m, 'the decision line is present')
if m:
    prog = ('#include <stdio.h>\nint main(void) { int bad = 0;\n'
            ' long long cases[][3] = {{2,0,1},{2,1,0},{1,0,1},{1,1,1},{0,0,0},{0,1,0},{7,1,1}};\n'
            ' for (unsigned k = 0; k < sizeof cases / sizeof cases[0]; k++) { long long fv = cases[k][0]; int apple10 = (int)cases[k][1];'
            ' int g_flat_views; ' + m.group(1) + '\n'
            '  if (g_flat_views != cases[k][2]) { printf("fv=%lld apple10=%d -> %d\\n", fv, apple10, g_flat_views); bad = 1; } }\n'
            ' return bad; }\n')
    with tempfile.TemporaryDirectory(prefix='bcd-sm5fv-') as tmp:
        c, exe = os.path.join(tmp, 'd.c'), os.path.join(tmp, 'd')
        Path(c).write_text(prog)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Werror', c, '-o', exe], check=True)
        r = subprocess.run([exe], capture_output=True, text=True)
        need(r.returncode == 0, 'decision: 2 = on below Apple10 only, 1 = on, 0 = off (%s)' % r.stdout.strip())

cat = (root / 'build/tools/gen-config-catalog.py').read_text()
e = re.search(r'"sm5-flat-views": \{"category": "Direct3D 12".*?"kind": "int", "default": "2"', cat, re.S)
need(e, 'the catalog entry: Direct3D 12, int, default 2')
gen = root / 'app/Madeira/ConfigCatalog.generated.swift'
if gen.exists():
    need('key: "sm5-flat-views"' in gen.read_text(), 'the generated catalog carries sm5-flat-views')

if fails:
    for f in fails:
        print('FAIL:', f)
    sys.exit(1)
print('PASS: ml1308 2D views for DXBC texture2d bindings in Direct3D 12, automatic below Apple10 (sm5-flat-views = 2)')
