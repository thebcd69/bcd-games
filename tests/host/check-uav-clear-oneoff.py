#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""UAV clears past the pattern cache (build 650; madeira-d3d12/src/pe/madeira_d3d12.c).

ml1151 copies a clear value whose bytes differ from a 256 KB pattern buffer, cached per device for 32 values and never
evicted (a command buffer may still read one). The Last of Us Part I clears its ray-cast results to 0xdead0000 plus a job
counter, a new value for every job; build 649's trace (the owner's start of 2026-11-09 22:00) showed the cache full at
0xdead001b in the main menu, and past it a buffer clear fell back to the value's low byte: the new game's job 0xe5 left
0xe5e5e5e5 in the guards and the game stopped ("Invalid result data for ray 0"). A texture clear past it was dropped.
Checks:
  - recording: a buffer clear the cache has no room for keeps its value (oneoff, the 16-byte period), the low-byte note is
    not printed for it; a texture clear keeps its period instead of returning;
  - running: such a clear copies from a pattern buffer of its own (shared, the period repeated, at most 256 KB), in the same
    256 KB steps as a cached one, and gives it back after encoding (the command buffer retains what it reads);
  - a cached value and a byte-equal value take the paths they took before;
  - the cache itself is unchanged (32 values, never evicted);
  - a model of the recording decision: 40 distinct counter values, every one ends up exact;
  - CI runs this check.
"""
import re
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


pe = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()

check("the fill commands carry the value when the cache has no room (oneoff + the 16-byte period)",
      "struct { struct mad_resource *res; UINT64 off, len; UINT8 byte, oneoff; obj_handle_t pattern; UINT32 per[4]; } fill;" in pe
      and "struct { struct mad_resource *res; UINT level, sl0, nsl, bpp; obj_handle_t pattern; UINT32 per[4]; UINT8 oneoff; } filltex;" in pe)

rec = pe[pe.index("static void mad_record_uav_clear("):pe.index("static void STDMETHODCALLTYPE list_ClearUnorderedAccessViewUint(")]
check("recording a buffer clear: oneoff starts at 0, set with the value when the cache gave no pattern",
      "c->u.fill.byte = b; c->u.fill.pattern = 0; c->u.fill.oneoff = 0;" in rec
      and "if (!c->u.fill.pattern) {   /* madeira-bcd: the cache is full; was the low byte */" in rec
      and "c->u.fill.per[0] = c->u.fill.per[1] = c->u.fill.per[2] = c->u.fill.per[3] = x; c->u.fill.oneoff = 1;" in rec)
check("...the low-byte note is only for a clear that really is approximated",
      "if (!exact && !c->u.fill.pattern && !c->u.fill.oneoff && said_approx++ < 4)" in rec)
check("...the oneoff is decided only for a value whose bytes differ (a byte-equal value still fills by its byte)",
      rec.index("if (!(((x >> 8) & 0xff) == b && ((x >> 16) & 0xff) == b && ((x >> 24) & 0xff) == b))")
      < rec.index("c->u.fill.oneoff = 1;"))

tex = pe[pe.index("static void mad_record_uav_tex_clear("):pe.index("static void mad_record_uav_clear(")]
check("recording a texture clear: no longer returns when the cache is full, keeps the period",
      "if (!pattern) return;" not in tex
      and "if (!pattern) { memcpy(c->u.filltex.per, per, 16); c->u.filltex.oneoff = 1; }   /* madeira-bcd: was dropped */" in tex)

one = pe[pe.index("static obj_handle_t exec_oneoff_pattern("):pe.index("/* ml1310: BC uploads on a GPU that cannot sample BC.")]
check("the one-off pattern sits before the BC upload block (check-d3d12-bc-decode compiles that block on its own)",
      pe.index("static obj_handle_t exec_oneoff_pattern(") < pe.index("/* ml1310: BC uploads on a GPU that cannot sample BC.")
      and pe.index("#define MAD_FILLPAT_BYTES") < pe.index("static obj_handle_t exec_oneoff_pattern("))
check("the one-off pattern: shared storage, the period repeated, at most MAD_FILLPAT_BYTES, 16-byte rounded",
      "bi.options = WMTResourceStorageModeShared;" in one and "for (k = 0; k < n / 4; k++) w[k] = per[k & 3];" in one
      and "n = bytes < MAD_FILLPAT_BYTES ? (bytes + 15) & ~(UINT64)15 : MAD_FILLPAT_BYTES;" in one
      and "if (b) NSObject_release(b);" in one)

ex = pe[pe.index("static void exec_copy(struct mad_exec *e, const struct mad_cmd *c) {"):]
ex = ex[:ex.index("case MC_COPY_T2B:")]
fb = ex[ex.index("case MC_FILL_BB: {"):ex.index("case MC_WBI: {")]
check("running a buffer clear: the cached pattern first, else a one-off one, released after the copies",
      "obj_handle_t pat = c->u.fill.pattern, own = 0;" in fb
      and "if (!pat && c->u.fill.oneoff) pat = own = exec_oneoff_pattern(e, c->u.fill.per, c->u.fill.len);" in fb
      and "cp.src = pat; cp.src_offset = 0;" in fb
      and fb.index("MTLBlitCommandEncoder_encodeCommands(e->benc, (const struct wmtcmd_base *)&cp);")
      < fb.index("if (own) NSObject_release(own);"))
check("...in the same 256 KB steps; without any pattern it still fills by the byte (as before)",
      "for (o = 0; o < c->u.fill.len; o += MAD_FILLPAT_BYTES) {" in fb
      and "k.type = WMTBlitCommandFillBuffer;" in fb and "k.value = c->u.fill.byte;" in fb)
ft = ex[ex.index("case MC_FILL_TEX: {"):]
check("running a texture clear: a one-off pattern of the full band size, skipped only if Metal gives none, released after",
      "if (!r || !r->texture || (!pat && !c->u.filltex.oneoff)) { MAD_SKIP(e); return; }" in ft
      and "if (!pat && !(pat = own = exec_oneoff_pattern(e, c->u.filltex.per, MAD_FILLPAT_BYTES))) { MAD_SKIP(e); return; }" in ft
      and "k.src = pat; k.src_offset = 0;" in ft
      and ft.index("MTLBlitCommandEncoder_encodeCommands(e->benc, (const struct wmtcmd_base *)&k);") < ft.index("if (own) NSObject_release(own);"))

cache = pe[pe.index("static obj_handle_t mad_fill_pattern16("):pe.index("static obj_handle_t mad_fill_pattern(struct mad_device *d, UINT32 v) {")]
check("the cache is unchanged: 32 values, filled once, never evicted",
      "struct { UINT32 value[4]; obj_handle_t buf; } fillpat[32];" in pe
      and "if (!buf && d->nfillpat < (sizeof d->fillpat / sizeof d->fillpat[0])) {" in cache
      and "d->nfillpat--" not in pe and "NSObject_release(d->fillpat[" not in cache)

# A model of the recording decision against the cache, for The Last of Us's counter.
CAP = 32
cache_vals = []


def record(x):
    b = x & 0xff
    if all(((x >> s) & 0xff) == b for s in (8, 16, 24)):
        return ("byte", b)
    if x in cache_vals:
        return ("cached", x)
    if len(cache_vals) < CAP:
        cache_vals.append(x)
        return ("cached", x)
    return ("oneoff", x)   # build 649 and earlier: ("byte", b)


def written(kind, v):
    return v * 0x01010101 if kind == "byte" else v


bad = [hex(0xdead0000 + j) for j in range(40) if written(*record(0xdead0000 + j)) != 0xdead0000 + j]
check("model: 40 job values 0xdead0000.. each written exactly (the 33rd on take a one-off pattern)", not bad, bad)
check("model: a byte-equal value (0x01010101) and zero still fill by their byte", record(0x01010101) == ("byte", 1) and record(0) == ("byte", 0))

ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-uav-clear-oneoff.py" in ci)

print("PASS: uav-clear-oneoff" if ok else "FAILED: uav-clear-oneoff")
sys.exit(0 if ok else 1)
