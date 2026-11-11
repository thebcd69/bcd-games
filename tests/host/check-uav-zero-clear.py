#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A UAV texture clear to zero through a view whose texel size differs from the texture's (build 648; madeira_d3d12.c,
mad_record_uav_tex_clear).

The Last of Us Part I clears a 2-byte (R16_FLOAT) view of a texture with another texel size to 0 every frame (the owner's log of
2026-11-09 11:25: "ClearUnorderedAccessViewUint on texture 'Texture' (view format 54, 1 samples, values 0 0 0 0) is not supported;
skipped", 8 times), and the texture kept the last frame's data. Zero is zero bytes in every format, so such a clear now fills the
texture in its own texel size; any other value is still skipped, and that line now names the texture's own format. Checks the source
and that CI runs this check.
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
fn = src[src.index("static void mad_record_uav_tex_clear("):src.index("static void mad_record_uav_clear(")]
mismatch = fn[fn.index("if (bpp && rblock == 1 && rbytes && rbytes != bpp) {"):fn.index("memcpy(w, px, 16);")]
check("a texel-size mismatch is still refused, except a clear whose packed value is all zero bytes",
      "if (!memcmp(px, zero, sizeof zero) && (rbytes == 1 || rbytes == 2 || rbytes == 4 || rbytes == 8 || rbytes == 16)) {" in mismatch
      and "bpp = rbytes;" in mismatch and "} else bpp = 0;" in mismatch, mismatch)
check("...the zero clear is logged (8 lines) with both formats and the texture's texel size",
      "if (said_zero++ < 8)" in mismatch and "cleared in the texture's own texel size" in mismatch, mismatch)
check("...the packed value is computed before (mad_pack_clear zeroes the 16 bytes first), the fill uses bpp as the row's texel size",
      "memset(out, 0, 16);" in src[src.index("static UINT mad_pack_clear("):src.index("static UINT mad_pack_clear(") + 400]
      and "row = w * c->u.filltex.bpp;" in src)
check("a clear still skipped names the texture's own format and size",
      "texture format %u, \"\n                      \"%ux%u) is not supported; skipped" in fn and "(unsigned)u.res->desc.Format, u.res->width, u.res->height);" in fn, fn[-1200:])
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-uav-zero-clear.py" in ci)
print("PASS: UAV texture clear to zero" if ok else "FAILED: UAV texture clear to zero")
sys.exit(0 if ok else 1)
