#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""madeira-d3d12: SRC1 blend factors where Metal cannot honour them are neutralised before the pipeline is built (round 714, M1 S1).

Cyberpunk 2077 (D1DGGH, 5DE5T4, DNXJPE, B2RSK8, M4Y4B4, P85MWH, build 712, iPhone19,7) played 67-126 s and then ended on Metal's
"failed assertion `Render Pipeline Descriptor Validation` ... color render target(2) is using a blend factor (MTLBlendFactorSource1Color)
which can only be used for render target 0 / Dual source blending is used in combination with multiple render targets": with
IndependentBlendEnable = 0 every target gets RT0's blend description, SRC1 included.

Checks: mad_rp_neutralise_src1 and its helpers, cut from madeira_d3d12.c and compiled on the host against winemetal.h's real enums:
a target other than 0, or a disabled blend, with a SRC1 factor becomes pass-through; target 0 with blending on beside a second valid
target gets SRC1 -> ZERO, INV_SRC1 -> ONE; target 0 with blending on as the only valid target is left alone (the one case Metal
honours); ordinary factors are never touched. The call sits after the colour loop and before the depth format; the pixel stage is not
compiled for dual source when two or more targets are valid.
"""
import re, shutil, subprocess, sys, tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
SRC = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:800]) if detail and not cond else ""))
    ok &= bool(cond)


def body(name):
    i = SRC.find(name)
    return "" if i < 0 else SRC[i:SRC.index("\n}\n", i) + 3]


helpers = [body("static int mad_blend_factor_is_src1(enum WMTBlendFactor f) {"),
           body("static int mad_color_has_src1(const struct WMTColorAttachmentBlendInfo *c) {"),
           body("static void mad_color_passthrough(struct WMTColorAttachmentBlendInfo *c) {"),
           body("static void mad_color_drop_src1(struct WMTColorAttachmentBlendInfo *c) {"),
           body("static unsigned mad_rp_neutralise_src1(struct WMTRenderPipelineInfo *rp, unsigned *first) {")]
check("the helpers are there", all(helpers))
loop = SRC.find("rp.colors[i].write_mask = mad_write_mask(b->RenderTargetWriteMask);")
call = SRC.find("unsigned first = 0, n = mad_rp_neutralise_src1(&rp, &first);")
depth = SRC.find("rp.depth_pixel_format = WMTPixelFormatInvalid;", loop)
check("the call sits after the colour loop and before the depth format, once", 0 < loop < call < depth
      and SRC.count("mad_rp_neutralise_src1(&rp, &first)") == 1)
lazy = SRC.find("p->lazy = 1; p->rp = rp;", call)
eager = SRC.find("p->rps = mad_new_render_pipeline(d->mtl_device, &rp,", call)
check("both pipeline builds (lazy and eager) come after it", call < lazy and call < eager)
flag = SRC.find("if (valid > 1) flags &= ~MADEIRA_IR_PS_DUAL_SOURCE_BLEND;")
ds = SRC.find("flags |= MADEIRA_IR_PS_DUAL_SOURCE_BLEND;")
check("the pixel stage is not compiled for dual source beside a second valid target", 0 < ds < flag
      and "if (desc->RTVFormats[k] != DXGI_FORMAT_UNKNOWN) valid++;" in SRC[ds:flag])
check("the log line names the pipeline", '"[madeira-d3d12] ml-r714 SRC1 blend factors on %u target(s), first target %u' in SRC)

wm = None
for cand in (R / "dxmt/src/winemetal/winemetal.h", Path.home() / "madeira-bcd/dxmt/src/winemetal/winemetal.h"):
    if cand.is_file():
        wm = cand.read_text()
        break
cc = shutil.which("cc") or shutil.which("clang")
if not wm or not cc:
    print("skip: the compiled part needs dxmt/src/winemetal/winemetal.h and a C compiler")
else:
    bf = re.search(r"enum WMTBlendFactor(?:\s*:\s*\w+)?\s*\{[^}]*\}\s*;", wm, re.S)
    check("winemetal.h has Metal's blend factor enum", bf is not None)
    if bf:
        prog = ("#include <stdint.h>\n#include <stdbool.h>\n" + bf.group(0)
                + "\nenum WMTPixelFormat { WMTPixelFormatInvalid = 0, WMTPixelFormatRGBA8Unorm = 70 };\n"
                  "struct WMTColorAttachmentBlendInfo { enum WMTPixelFormat pixel_format; enum WMTBlendFactor src_rgb_blend_factor, dst_rgb_blend_factor,"
                  " src_alpha_blend_factor, dst_alpha_blend_factor; bool blending_enabled; };\n"
                  "struct WMTRenderPipelineInfo { struct WMTColorAttachmentBlendInfo colors[8]; };\n"
                + "\n".join(helpers) + r'''
#include <stdio.h>
static void set(struct WMTColorAttachmentBlendInfo *c, int on, enum WMTBlendFactor s, enum WMTBlendFactor d) {
    c->pixel_format = WMTPixelFormatRGBA8Unorm; c->blending_enabled = on;
    c->src_rgb_blend_factor = c->src_alpha_blend_factor = s; c->dst_rgb_blend_factor = c->dst_alpha_blend_factor = d;
}
int main(void) {
    int bad = 0; unsigned first = 9, n;
    struct WMTRenderPipelineInfo a = {0}, b = {0}, c = {0}, d = {0};
    /* 1: RT0 blending with SRC1 and two more targets with RT0's copy (IndependentBlendEnable = 0) */
    set(&a.colors[0], 1, WMTBlendFactorOne, WMTBlendFactorOneMinusSource1Color);
    set(&a.colors[1], 1, WMTBlendFactorOne, WMTBlendFactorOneMinusSource1Color);
    set(&a.colors[2], 1, WMTBlendFactorSource1Color, WMTBlendFactorZero);
    n = mad_rp_neutralise_src1(&a, &first);
    if (n != 3 || first != 0) bad |= 1;
    if (a.colors[0].blending_enabled != 1 || a.colors[0].src_rgb_blend_factor != WMTBlendFactorOne
        || a.colors[0].dst_rgb_blend_factor != WMTBlendFactorOne) bad |= 2;
    if (a.colors[2].blending_enabled || a.colors[2].src_rgb_blend_factor != WMTBlendFactorOne
        || a.colors[2].dst_rgb_blend_factor != WMTBlendFactorZero || a.colors[1].blending_enabled) bad |= 4;
    /* 2: RT0 the only valid target, blending on: Metal honours it, nothing changes */
    set(&b.colors[0], 1, WMTBlendFactorSource1Alpha, WMTBlendFactorOneMinusSource1Alpha);
    if (mad_rp_neutralise_src1(&b, &first) || b.colors[0].src_rgb_blend_factor != WMTBlendFactorSource1Alpha) bad |= 8;
    /* 3: RT0 blending off with SRC1 left in it, beside a second target */
    set(&c.colors[0], 0, WMTBlendFactorSource1Color, WMTBlendFactorZero);
    set(&c.colors[1], 1, WMTBlendFactorSourceAlpha, WMTBlendFactorOneMinusSourceAlpha);
    first = 9;
    if (mad_rp_neutralise_src1(&c, &first) != 1 || first != 0 || c.colors[0].src_rgb_blend_factor != WMTBlendFactorOne) bad |= 16;
    if (c.colors[1].src_rgb_blend_factor != WMTBlendFactorSourceAlpha || !c.colors[1].blending_enabled) bad |= 32;
    /* 4: ordinary factors on every target: untouched */
    for (int i = 0; i < 8; i++) set(&d.colors[i], 1, WMTBlendFactorSourceAlpha, WMTBlendFactorOneMinusSourceAlpha);
    if (mad_rp_neutralise_src1(&d, &first)) bad |= 64;
    printf("%s\n", bad ? "FAIL" : "ok: later targets pass-through, RT0 beside a second target drops SRC1, RT0 alone kept, ordinary kept");
    return bad;
}
''')
        with tempfile.TemporaryDirectory(prefix="bcd-d3d12-src1-") as tmp:
            cf = Path(tmp) / "t.c"
            cf.write_text(prog)
            bld = subprocess.run([cc, "-std=c2x", "-Wall", "-Werror", str(cf), "-o", str(Path(tmp) / "t")], capture_output=True, text=True)
            check("the helpers compile on the host", bld.returncode == 0, bld.stderr[-800:])
            if bld.returncode == 0:
                r = subprocess.run([str(Path(tmp) / "t")], capture_output=True, text=True)
                print(r.stdout, end="")
                check("the helpers' cases", r.returncode == 0, r.stdout)

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-src1-later-targets.py" in wf)
print("PASS: SRC1 blend factors neutralised where Metal cannot honour them" if ok else "FAILED: SRC1 later targets")
sys.exit(0 if ok else 1)
