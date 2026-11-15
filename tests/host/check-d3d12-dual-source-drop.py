#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""madeira-d3d12: a pipeline Metal refuses for its dual-source blend factors is built once more without them (round 660, M010).

Freddy's House (Z2YH7Y, build 653), Poppy Playtime and ReSkate (16 logs): "Fragment shader does not write to render target color(0),
index(1) that is required for blending", then "lazy pipeline failed at first draw ... its draws are skipped". The DXIL pixel path
compiles no second output, so a blend that reads it can never be built.

Checks: mad_rp_drop_dual_source, cut from madeira_d3d12.c and compiled on the host against Metal's real enum values (winemetal.h),
turns SRC1 factors into ZERO and INV_SRC1 into ONE on every target and leaves every other factor alone; mad_err_wants_dual_source
asks for exactly that refusal text; mad_new_render_pipeline tries a second time only after that refusal and only when a factor
changed; both pipeline builds (the lazy one at first draw and the eager one) go through it, and nothing else does.
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


drop = body("static int mad_rp_drop_dual_source(struct WMTRenderPipelineInfo *rp) {")
wants = body("static int mad_err_wants_dual_source(obj_handle_t err) {")
new = body("static obj_handle_t mad_new_render_pipeline(")
check("the three helpers are there", drop and wants and new)
check("only Metal's dual-source refusal asks for the second try",
      'return strstr(text, "index(1) that is required for blending") != NULL;' in wants)
check("the second try comes only after that refusal and only when a factor changed, and the first refusal is logged",
      "if (!rps && *err && mad_err_wants_dual_source(*err) && mad_rp_drop_dual_source(rp)) {" in new
      and new.index("mad_log_nserror(what, *err);") < new.index("*err = 0;"))
calls = SRC.count("mad_new_render_pipeline(")
check("both pipeline builds use it (lazy at first draw, eager), and only they", calls == 3
      and "built = mad_new_render_pipeline(p->device_handle, &p->rp, p->has_vd ? &p->vd : NULL, &err, p->vs_name);" in SRC
      and "p->rps = mad_new_render_pipeline(d->mtl_device, &rp, has_vd ? &vd : NULL, &err, p->vs_name);" in SRC, calls)

wm = None
for cand in (R / "dxmt/src/winemetal/winemetal.h", Path.home() / "madeira-bcd/dxmt/src/winemetal/winemetal.h"):
    if cand.is_file():
        wm = cand.read_text()
        break
cc = shutil.which("cc") or shutil.which("clang")
if not wm or not cc:
    print("skip: the compiled part needs dxmt/src/winemetal/winemetal.h and a C compiler")
else:
    enum = re.search(r"enum WMTBlendFactor(?:\s*:\s*\w+)?\s*\{[^}]*\}\s*;", wm, re.S)
    check("winemetal.h has Metal's blend factor enum", enum is not None)
    if enum:
        prog = ("#include <stdint.h>\n" + enum.group(0) + "\nstruct WMTColorAttachmentBlendInfo { enum WMTBlendFactor src_rgb_blend_factor, dst_rgb_blend_factor,"
                " src_alpha_blend_factor, dst_alpha_blend_factor; };\nstruct WMTRenderPipelineInfo { struct WMTColorAttachmentBlendInfo colors[8]; };\n"
                + drop + r'''
#include <stdio.h>
int main(void) {
    struct WMTRenderPipelineInfo rp = {0};
    int bad = 0, i;
    rp.colors[0].src_rgb_blend_factor = WMTBlendFactorOne;
    rp.colors[0].dst_rgb_blend_factor = WMTBlendFactorOneMinusSource1Color;
    rp.colors[0].src_alpha_blend_factor = WMTBlendFactorSource1Alpha;
    rp.colors[0].dst_alpha_blend_factor = WMTBlendFactorOneMinusSource1Alpha;
    rp.colors[3].src_rgb_blend_factor = WMTBlendFactorSource1Color;
    rp.colors[3].dst_rgb_blend_factor = WMTBlendFactorSourceAlpha;
    if (!mad_rp_drop_dual_source(&rp)) bad |= 1;
    if (rp.colors[0].src_rgb_blend_factor != WMTBlendFactorOne || rp.colors[0].dst_rgb_blend_factor != WMTBlendFactorOne ||
        rp.colors[0].src_alpha_blend_factor != WMTBlendFactorZero || rp.colors[0].dst_alpha_blend_factor != WMTBlendFactorOne) bad |= 2;
    if (rp.colors[3].src_rgb_blend_factor != WMTBlendFactorZero || rp.colors[3].dst_rgb_blend_factor != WMTBlendFactorSourceAlpha) bad |= 4;
    if (mad_rp_drop_dual_source(&rp)) bad |= 8;   /* nothing left to drop: no second try */
    for (i = 0; i < 8; i++) {
        struct WMTRenderPipelineInfo plain = {0};
        plain.colors[i].src_rgb_blend_factor = WMTBlendFactorSourceAlpha;
        plain.colors[i].dst_rgb_blend_factor = WMTBlendFactorOneMinusSourceAlpha;
        if (mad_rp_drop_dual_source(&plain)) bad |= 16;
    }
    printf("%s\n", bad ? "FAIL" : "ok: SRC1 -> ZERO, INV_SRC1 -> ONE, other factors kept, nothing to drop -> no retry");
    return bad;
}
''')
        with tempfile.TemporaryDirectory(prefix="bcd-d3d12-ds-") as tmp:
            c = Path(tmp) / "t.c"
            c.write_text(prog)
            b = subprocess.run([cc, "-std=c2x", "-Wall", "-Werror", str(c), "-o", str(Path(tmp) / "t")], capture_output=True, text=True)
            check("the helper compiles on the host", b.returncode == 0, b.stderr[-800:])
            if b.returncode == 0:
                r = subprocess.run([str(Path(tmp) / "t")], capture_output=True, text=True)
                print(r.stdout, end="")
                check("the helper's mapping", r.returncode == 0, r.stdout)

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-dual-source-drop.py" in wf)
print("PASS: dual-source blend dropped after Metal's refusal" if ok else "FAILED: dual-source blend drop")
sys.exit(0 if ok else 1)
