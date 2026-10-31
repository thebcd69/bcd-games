#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""present-lum = 1 (bcd.cfg, default off): the brightness of presents 2, 30, 120, 600 and 1800 of each swapchain (round 624, agent B S1).

Animal Well (4S7NA9, RK9NPZ, 37F86Z, builds 622 and 623): 45-48 s of presents at 57-59 FPS, a black screen with sound, and the log could
not tell a black picture from a dark scene waiting for input. madeira_d3d12.c copies 16 texels (a 4x4 grid) of the frame about to be
presented into a small shared buffer, in the present's own command buffer after the frame copy, and logs them once that command buffer has
completed. Log only.

Checks: the switch is read once with mad_cfg_int_pe("present-lum", 0) and starts unset; the copies are encoded only for the five presents,
inside the present's blit encoder between the frame copy and endEncoding; the queue is bounded and locked; the poll frees what it read;
the line's words; the catalog entry and the list line of Animal Well (v2, minBuild 624); the texel conversion it uses (mad_texel_rgb, the
same code the capture sheets use) compiled and run on the host: black is (0,0,0), BGRA8 swaps to RGB, a half-float 1.0 is tone-mapped grey,
a NaN half is reported.
"""
from pathlib import Path
import json, os, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


block = src[src.index('/* madeira-bcd present-lum (bcd.cfg present-lum = 1'):src.index('static void mad_present_run(struct mad_swapchain *s, UINT idx) {')]
run = src[src.index('static void mad_present_run(struct mad_swapchain *s, UINT idx) {'):]
run = run[:run.index('\n}\n')]
check('the switch is read once, default 0, and starts unset (-1): nothing runs before it is read',
      src.count('mad_cfg_int_pe("present-lum"') == 1 and 'g_pl_on = mad_cfg_int_pe("present-lum", 0) ? 1 : 0;' in block
      and 'static int g_pl_on = -1;' in block)
check('only presents 2, 30, 120, 600 and 1800 are probed',
      'return g_pl_on > 0 && (presents == 2 || presents == 30 || presents == 120 || presents == 600 || presents == 1800);' in block)
enc = run.index('if (mad_pl_wanted(s->presents)) mad_pl_encode(s, src, enc, cb);')
check('the copies are encoded in the present\'s own blit encoder, after the frame copy and before endEncoding',
      run.index('MTLBlitCommandEncoder_encodeCommands(enc, (const struct wmtcmd_base *)&t2t);') < enc < run.index('MTLCommandEncoder_endEncoding(enc);', enc))
check('the poll runs once a present next to the other per-present polls, only once the switch was read on',
      'mad_ffc_poll();   /* madeira-bcd fi-field-check: copies whose command buffer has completed */\n    if (g_pl_on != 0) mad_pl_poll();' in run)
check('the queue holds four probes at most, under one lock, and a full queue, a MetalFX scaler or an odd format is not probed',
      '#define MAD_PL_PENDING 4' in block and block.count('AcquireSRWLockExclusive(&g_pl_lock);') == 2
      and block.count('ReleaseSRWLockExclusive(&g_pl_lock);') == 4
      and 'if (g_pl_n >= MAD_PL_PENDING || s->fx_scaler || !src->texture || !src->width || !src->height || bpp > 16) {' in block)
check('16 one-texel copies into a shared buffer of 256 bytes, read only after the command buffer completed, then freed',
      'buf = mad_capbuf_alloc(s->queue->device, 16 * 16, &cpu);' in block and 'k.size.width = 1; k.size.height = 1; k.size.depth = 1;' in block
      and 'if (st < WMTCommandBufferStatusCompleted) break;' in block and 'mad_capbuf_free(&t);' in block and 'NSObject_release(q->cb);' in block
      and 'NSObject_retain(cb);' in block)
check('the line says the present, the size, the format and the colours',
      '"[present-lum] present #%llu (%ux%u dxgi %u pf %u): 16 samples in a 4x4 grid, max rgb=(%u,%u,%u) avg rgb=(%lu,%lu,%lu) non-black=%u/16 nan-or-inf=%u"' in block)
check('it reads the game\'s frame and writes nothing the game or the screen sees (no store into the texture, no new command buffer)',
      'MTLCommandQueue_commandBuffer' not in block and 'WMTBlitCommandCopyFromBufferToTexture' not in block and 'k.src = src->texture;' in block)

cat = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the catalog describes the key (Direct3D 12, default off)', '"present-lum": {"category": "Direct3D 12", "title": "Diagnostic: brightness of presented frames",' in cat)
recs = json.loads((root / 'recommended/recommendations.json').read_text())
aw = [r for r in recs['rules'] if r['id'] == 'animal-well'][0]
check('Animal Well v2 carries present-lum = 1 from build 624', aw['version'] == 2 and {'line': 'present-lum = 1', 'minBuild': 624} in aw['config'], aw)

# The texel conversion, compiled on the host.
a = src.index('static float mad_h2f(UINT16 h) {')
b = src.index('static UINT mad_pf_texel_bytes(UINT pf) {')
b = src.index('\n}\n', b) + 3
harness = r'''
#include <math.h>
#include <stdio.h>
#include <string.h>
typedef unsigned int UINT; typedef unsigned int UINT32; typedef unsigned short UINT16;
''' + src[a:b] + r'''
int main(void)
{
    unsigned char o[3]; int r;
    const unsigned char black[4] = { 0, 0, 0, 0 }, bgra[4] = { 0x10, 0x20, 0x30, 0xff };
    const UINT16 one[4] = { 0x3c00, 0x3c00, 0x3c00, 0x3c00 }, nanh[4] = { 0x7e00, 0x3c00, 0x3c00, 0x3c00 };
    r = mad_texel_rgb(black, 70, mad_pf_texel_bytes(70), 0, 1, o, 0); printf("rgba8-black %d %u %u %u\n", r, o[0], o[1], o[2]);
    r = mad_texel_rgb(bgra, 80, mad_pf_texel_bytes(80), 0, 1, o, 0); printf("bgra8 %d %u %u %u\n", r, o[0], o[1], o[2]);
    r = mad_texel_rgb((const unsigned char *)one, 115, mad_pf_texel_bytes(115), 0, 1, o, 0); printf("rgba16f-one %d %u %u %u\n", r, o[0], o[1], o[2]);
    r = mad_texel_rgb((const unsigned char *)nanh, 115, mad_pf_texel_bytes(115), 0, 1, o, 0); printf("rgba16f-nan %d %u %u %u\n", r, o[0], o[1], o[2]);
    printf("bytes %u %u %u\n", mad_pf_texel_bytes(70), mad_pf_texel_bytes(80), mad_pf_texel_bytes(115));
    return 0;
}
'''
with tempfile.TemporaryDirectory() as t:
    c = Path(t) / 't.c'
    c.write_text(harness)
    exe = Path(t) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c), '-lm'], capture_output=True, text=True)
    check('the texel conversion compiles on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-1500:])
    if cc.returncode == 0:
        out = dict((l.split(' ', 1)[0], l.split(' ', 1)[1]) for l in subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines())
        one = [int(x) for x in out['rgba16f-one'].split()]
        check('black is (0,0,0), BGRA8 comes out as RGB, the texel sizes are 4, 4 and 8',
              out['rgba8-black'] == '0 0 0 0' and out['bgra8'] == '0 48 32 16' and out['bytes'] == '4 4 8', out)
        check('a half-float 1.0 is tone-mapped to one mid-light grey; a NaN half is reported (magenta, return 1)',
              one[0] == 0 and one[1] == one[2] == one[3] and 150 < one[1] < 220 and out['rgba16f-nan'] == '1 255 0 255', out)

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-present-lum.py' in wf)
print('PASS: present-lum' if ok else 'FAIL: present-lum')
raise SystemExit(0 if ok else 1)
