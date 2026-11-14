#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A buffer UAV clear through a typed view (build 656; madeira-d3d12/src/pe/madeira_d3d12.c).

Direct3D 12 clears a typed buffer view as it clears a texture: every element gets the four values, each in its own channel
(ClearUnorderedAccessViewUint copies each value's low bits). Only raw and structured views take Values[0] for every 32 bits,
which is what BCD wrote for every buffer view. The Last of Us Part I (the owner's log of 2026-11-11 21:58, build 655) clears
the 44 indirect dispatch records of its deferred lighting (16 bytes each: thread groups X, Y, Z, a spare word) to (0, 1, 1, 0)
through an R32G32B32A32_UINT view every frame, then counts screen tiles into X. With zeros in Y and Z no lighting dispatch ran
a single thread group ("[probe] dispatch 'CS_DeferredLighting' first record: 258 0 0"): no deferred light, only forward-drawn
things on a target nothing rewrote.
Checks:
  - the source: a typed view (a texture-buffer UAV at the descriptor's address) is looked up for its format and cleared per
    element; the plain path (raw, structured, a view without a texture buffer) is the code it was; an integer view cleared
    with ClearUnorderedAccessViewFloat stays plain; the fill takes a byte, a cached 16-byte pattern or a one-off one;
  - on the host: the packing and the period, compiled from the source, against a model of Direct3D 12's rule written here,
    for the game's clear and for every view format the game's clear helper picks (by stride: 1 R8_UINT, 2 R8G8_UINT,
    4 R8G8B8A8_UINT, 8 R32G32_UINT, else R32G32B32A32_UINT), float views, and the cases that must stay on the plain path;
  - a model of the game's frame: 44 records cleared, tiles counted into X, every record still has Y = Z = 1 (0 before);
  - the whole runtime compiles (the full tree's toolchain); CI runs this check.
"""
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


SRC = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()

# --- the source ---------------------------------------------------------------
rec = SRC[SRC.index("static void mad_record_uav_clear("):SRC.index("static void STDMETHODCALLTYPE list_ClearUnorderedAccessViewUint(")]
typed = SRC[SRC.index("static int mad_record_typed_buf_clear("):SRC.index("static obj_handle_t mad_fill_pattern(struct mad_device *d, UINT32 v);\nstatic void mad_record_uav_tex_clear(")]
fmtfn = SRC[SRC.index("static DXGI_FORMAT mad_typed_uav_format("):SRC.index("/* 1 when the fill command c was set up per element")]
blk = SRC[SRC.index("/* uav-buf-clear-test:begin */"):SRC.index("/* uav-buf-clear-test:end */")]

check("mad_record_uav_clear tries the typed view after the fill command has its resource and range, before the plain block",
      rec.index("c->u.fill.res = r; c->u.fill.off = off; c->u.fill.len = len;")
      < rec.index("if (mad_record_typed_buf_clear(l, c, r, e, v, is_float, what)) return;")
      < rec.index("UINT32 x = v ? v[0] : 0; UINT8 b = (UINT8)(x & 0xff);"))
check("...a texture view never reaches it (the texture path returns first)",
      rec.index("if (!e->gpu_va) {   /* a texture view */") < rec.index("if (mad_record_typed_buf_clear("))
check("the plain path is the code it was (Values[0] in every 32 bits: a byte, a cached pattern, a one-off one)",
      "c->u.fill.byte = b; c->u.fill.pattern = 0; c->u.fill.oneoff = 0;" in rec
      and "c->u.fill.pattern = mad_fill_pattern(l->device, x);   /* ml1151 */" in rec
      and "c->u.fill.per[0] = c->u.fill.per[1] = c->u.fill.per[2] = c->u.fill.per[3] = x; c->u.fill.oneoff = 1;" in rec
      and "if (!exact && !c->u.fill.pattern && !c->u.fill.oneoff && said_approx++ < 4)" in rec)
check("the view's format: only a typed descriptor (bit 63) with a texture id, matched to a UAV view of the resource at the descriptor's address",
      "if (!d || !r || !e->texture_view_id || !(e->metadata >> 63)) return f;" in fmtfn
      and "if (r->tview[k].id == e->texture_view_id && r->tview[k].uav && r->gpu_address + r->tview[k].off == e->gpu_va) {" in fmtfn
      and "EnterCriticalSection(&d->view_lock);" in fmtfn and "LeaveCriticalSection(&d->view_lock);" in fmtfn)
check("...a counter's view (no bit 63) and a typed view that fell back to a plain descriptor (no texture id) stay plain",
      "e->metadata = (e->metadata & 0xffffffffull) | ((elem_off & 0xffull) << 32);" in SRC   # the counter keeps bit 63 clear
      and "mad_set_buffer_descriptor(e, r->gpu_address + first * stride, num * stride);" in SRC)
check("recording: nothing without values; the decision is mad_buf_clear_typed's; a byte when the 16 bytes are one byte",
      "if (!v) return 0;" in typed
      and "if (!mad_buf_clear_typed(fmt, v, is_float, mad_format_is_int(fmt), per, &bpp)) return 0;" in typed
      and "c->u.fill.byte = (UINT8)(per[0] & 0xff); c->u.fill.pattern = 0; c->u.fill.oneoff = 0;" in typed
      and "if (!mad_buf_clear_uniform(per)) {" in typed)
check("...else the device's 16-byte pattern cache, and a one-off pattern with the period when the cache is full",
      "c->u.fill.pattern = mad_fill_pattern16(l->device, per);" in typed
      and "if (!c->u.fill.pattern) { memcpy(c->u.fill.per, per, 16); c->u.fill.oneoff = 1; }" in typed)
check("...the first eight are logged with the format, the values and the element",
      "through a typed buffer view (format %u, %u-byte elements): values %#x %#x %#x %#x -> every element" in typed
      and "if (InterlockedIncrement(&said) <= 8)" in typed)
check("the decision: unknown formats and integer views under a float clear stay plain; so does a result the plain path writes",
      "if (fmt == DXGI_FORMAT_UNKNOWN || (is_float && fmt_is_int)) return 0;" in blk
      and "plain[0] = plain[1] = plain[2] = plain[3] = v[0];" in blk and "if (!memcmp(per, plain, 16)) return 0;" in blk
      and "if (bpp != 1 && bpp != 2 && bpp != 4 && bpp != 8 && bpp != 16) return 0;" in blk)
ex = SRC[SRC.index("static void exec_copy(struct mad_exec *e, const struct mad_cmd *c) {"):]
fb = ex[ex.index("case MC_FILL_BB: {"):ex.index("case MC_WBI: {")]
check("running: the pattern is copied from its start to the view's start in 256 KB steps (a multiple of 16, so elements stay whole)",
      "cp.src = pat; cp.src_offset = 0;" in fb and "cp.dst_offset = c->u.fill.off + o;" in fb
      and "for (o = 0; o < c->u.fill.len; o += MAD_FILLPAT_BYTES) {" in fb
      and "#define MAD_FILLPAT_BYTES (256u << 10)" in SRC and (256 << 10) % 16 == 0)
check("the texture clear path is not touched by this (its own function, before the buffer one)",
      SRC.index("static void mad_record_uav_tex_clear(") < SRC.index("static void mad_record_uav_clear(")
      and "mad_record_typed_buf_clear" not in SRC[SRC.index("static void mad_record_uav_tex_clear("):SRC.index("static void mad_record_uav_clear(")])

# --- Direct3D 12's rule, written here -------------------------------------------
FMT = {  # name: (value, channels, bits, kind)   kind: f float, u unorm, s snorm, ui uint, si sint, p packed (not modelled)
    "DXGI_FORMAT_UNKNOWN": (0, 0, 0, ""),
    "DXGI_FORMAT_R32G32B32A32_FLOAT": (2, 4, 32, "f"), "DXGI_FORMAT_R32G32B32A32_UINT": (3, 4, 32, "ui"),
    "DXGI_FORMAT_R32G32B32A32_SINT": (4, 4, 32, "si"),
    "DXGI_FORMAT_R32G32B32_FLOAT": (6, 3, 32, "f"), "DXGI_FORMAT_R32G32B32_UINT": (7, 3, 32, "ui"),
    "DXGI_FORMAT_R16G16B16A16_FLOAT": (10, 4, 16, "f"), "DXGI_FORMAT_R16G16B16A16_UNORM": (11, 4, 16, "u"),
    "DXGI_FORMAT_R16G16B16A16_UINT": (12, 4, 16, "ui"), "DXGI_FORMAT_R16G16B16A16_SNORM": (13, 4, 16, "s"),
    "DXGI_FORMAT_R16G16B16A16_SINT": (14, 4, 16, "si"),
    "DXGI_FORMAT_R32G32_FLOAT": (16, 2, 32, "f"), "DXGI_FORMAT_R32G32_UINT": (17, 2, 32, "ui"), "DXGI_FORMAT_R32G32_SINT": (18, 2, 32, "si"),
    "DXGI_FORMAT_R10G10B10A2_UNORM": (24, 0, 0, "p"), "DXGI_FORMAT_R10G10B10A2_UINT": (25, 0, 0, "p"),
    "DXGI_FORMAT_R11G11B10_FLOAT": (26, 0, 0, "p"),
    "DXGI_FORMAT_R8G8B8A8_UNORM": (28, 4, 8, "u"), "DXGI_FORMAT_R8G8B8A8_UINT": (30, 4, 8, "ui"),
    "DXGI_FORMAT_R8G8B8A8_SNORM": (31, 4, 8, "s"), "DXGI_FORMAT_R8G8B8A8_SINT": (32, 4, 8, "si"),
    "DXGI_FORMAT_R16G16_FLOAT": (34, 2, 16, "f"), "DXGI_FORMAT_R16G16_UNORM": (35, 2, 16, "u"), "DXGI_FORMAT_R16G16_UINT": (36, 2, 16, "ui"),
    "DXGI_FORMAT_R16G16_SNORM": (37, 2, 16, "s"), "DXGI_FORMAT_R16G16_SINT": (38, 2, 16, "si"),
    "DXGI_FORMAT_R32_FLOAT": (41, 1, 32, "f"), "DXGI_FORMAT_R32_UINT": (42, 1, 32, "ui"), "DXGI_FORMAT_R32_SINT": (43, 1, 32, "si"),
    "DXGI_FORMAT_R8G8_UNORM": (49, 2, 8, "u"), "DXGI_FORMAT_R8G8_UINT": (50, 2, 8, "ui"), "DXGI_FORMAT_R8G8_SNORM": (51, 2, 8, "s"),
    "DXGI_FORMAT_R8G8_SINT": (52, 2, 8, "si"),
    "DXGI_FORMAT_R16_FLOAT": (54, 1, 16, "f"), "DXGI_FORMAT_R16_UNORM": (56, 1, 16, "u"), "DXGI_FORMAT_R16_UINT": (57, 1, 16, "ui"),
    "DXGI_FORMAT_R16_SNORM": (58, 1, 16, "s"), "DXGI_FORMAT_R16_SINT": (59, 1, 16, "si"),
    "DXGI_FORMAT_R8_UNORM": (61, 1, 8, "u"), "DXGI_FORMAT_R8_UINT": (62, 1, 8, "ui"), "DXGI_FORMAT_R8_SNORM": (63, 1, 8, "s"),
    "DXGI_FORMAT_R8_SINT": (64, 1, 8, "si"),
    "DXGI_FORMAT_B8G8R8A8_UNORM": (87, 4, 8, "u"),
}
BYVAL = {v[0]: (k,) + v[1:] for k, v in FMT.items()}


def f2bits(f):
    return struct.unpack("<I", struct.pack("<f", f))[0]


def half(f):
    return struct.unpack("<H", struct.pack("<e", f))[0]


def d3d12_element(fmt, vals, is_float):
    """The bytes Direct3D 12 writes into one element, or None for a format the test does not model."""
    name, nch, bits, kind = BYVAL[fmt]
    if not nch or nch == 3:
        return None
    out = b""
    for i in range(nch):
        raw = vals[i] & 0xffffffff
        mask = (1 << bits) - 1
        if not is_float:
            c = raw & mask                       # ClearUnorderedAccessViewUint: the low bits, no conversion
        else:
            f = struct.unpack("<f", struct.pack("<I", raw))[0]
            if kind == "f":
                c = raw if bits == 32 else half(f)
            elif kind == "u":
                c = int(min(max(f, 0.0), 1.0) * mask + 0.5) & mask
            elif kind == "s":
                m = mask >> 1
                cl = min(max(f, -1.0), 1.0)
                c = int(cl * m + (-0.5 if cl < 0 else 0.5)) & mask
            else:
                return None                      # an integer view under a float clear: not valid Direct3D 12
        out += c.to_bytes(bits // 8, "little")
    return out


def expect(fmt, vals, is_float):
    """(taken, 16 bytes): what the new path must decide and write."""
    el = d3d12_element(fmt, vals, is_float)
    plain = struct.pack("<I", vals[0] & 0xffffffff) * 4
    if el is None or len(el) not in (1, 2, 4, 8, 16):
        return 0, plain
    per = (el * 16)[:16]
    return (0, plain) if per == plain else (1, per)


F = f2bits
CASES = [
    # (format, values, is_float, what)
    (3, (0, 1, 1, 0), 0, "the game: dispatch records cleared to X 0, Y 1, Z 1"),
    (3, (5, 5, 5, 5), 0, "four equal values: the plain path's result"),
    (3, (0, 0, 0, 0), 0, "zero"),
    (3, (0xdead0001,) * 4, 0, "the game's ray-cast guards through the 16-byte view (stride 888)"),
    (3, (1, 2, 3, 4), 0, "four different values"),
    (4, (0xffffffff, 0, 0xffffffff, 0), 0, "R32G32B32A32_SINT"),
    (17, (1, 2, 3, 4), 0, "R32G32_UINT (stride 8): X, Y alternate"),
    (17, (1, 1, 9, 9), 0, "R32G32_UINT with equal X and Y: plain"),
    (30, (1, 1, 1, 1), 0, "R8G8B8A8_UINT (stride 4): one byte per channel, 0x01010101"),
    (30, (0x12, 0x34, 0x56, 0x78), 0, "R8G8B8A8_UINT: bytes 12 34 56 78"),
    (30, (1, 0, 0, 0), 0, "R8G8B8A8_UINT (1, 0, 0, 0): the dword 1, plain"),
    (30, (0xdead0001,) * 4, 0, "R8G8B8A8_UINT keeps the low 8 bits of each value"),
    (50, (1, 2, 0, 0), 0, "R8G8_UINT (stride 2)"),
    (62, (0x1ff, 0, 0, 0), 0, "R8_UINT (stride 1): the low byte in every byte"),
    (62, (0, 7, 7, 7), 0, "R8_UINT zero"),
    (57, (0x1234, 0, 0, 0), 0, "R16_UINT"),
    (36, (0x1111, 0x2222, 0, 0), 0, "R16G16_UINT"),
    (12, (1, 2, 3, 4), 0, "R16G16B16A16_UINT"),
    (42, (7, 9, 9, 9), 0, "R32_UINT: only R, the plain path"),
    (43, (0xfffffffe, 1, 2, 3), 0, "R32_SINT: plain"),
    (41, (F(1.0), 0, 0, 0), 0, "R32_FLOAT cleared with Uint: the bits, plain"),
    (2, (F(1.0), F(0.5), 0, F(2.0)), 0, "R32G32B32A32_FLOAT cleared with Uint: the bits per channel"),
    (2, (F(1.0), F(0.5), F(0.0), F(2.0)), 1, "R32G32B32A32_FLOAT, float clear"),
    (2, (F(3.0),) * 4, 1, "R32G32B32A32_FLOAT, four equal floats: plain"),
    (16, (F(-1.0), F(1.0), 0, 0), 1, "R32G32_FLOAT, float clear"),
    (41, (F(1.0), F(2.0), 0, 0), 1, "R32_FLOAT, float clear: plain"),
    (10, (F(1.0), F(0.5), F(0.0), F(1.0)), 1, "R16G16B16A16_FLOAT, float clear: halves"),
    (34, (F(1.0), F(-2.0), 0, 0), 1, "R16G16_FLOAT, float clear"),
    (54, (F(1.0), 0, 0, 0), 1, "R16_FLOAT, float clear"),
    (28, (F(0.5), F(1.0), F(0.0), F(1.0)), 1, "R8G8B8A8_UNORM, float clear"),
    (61, (F(1.0), 0, 0, 0), 1, "R8_UNORM 1.0: ff in every byte"),
    (11, (F(1.0), F(0.0), F(0.25), F(1.0)), 1, "R16G16B16A16_UNORM, float clear"),
    (31, (F(-1.0), F(1.0), F(0.0), F(0.5)), 1, "R8G8B8A8_SNORM, float clear"),
    (42, (F(1.0), 0, 0, 0), 1, "an integer view under a float clear: plain, as before"),
    (3, (F(0.0), F(1.0), F(1.0), F(0.0)), 1, "R32G32B32A32_UINT under a float clear: plain, as before"),
    (0, (0, 1, 1, 0), 0, "no typed view (raw, structured, a fallen-back view): plain"),
    (6, (0, 1, 1, 0), 0, "R32G32B32_FLOAT (12 bytes, never a texture buffer here): plain"),
    (7, (0, 1, 1, 0), 0, "R32G32B32_UINT: plain"),
]

check("the model: the game's clear is (0, 1, 1, 0) in every 16 bytes; R32 views and equal values stay plain",
      expect(3, (0, 1, 1, 0), 0) == (1, struct.pack("<4I", 0, 1, 1, 0))
      and expect(42, (7, 9, 9, 9), 0)[0] == 0 and expect(3, (5, 5, 5, 5), 0)[0] == 0
      and expect(30, (1, 1, 1, 1), 0) == (1, b"\x01" * 16) and expect(62, (0x1ff, 0, 0, 0), 0) == (1, b"\xff" * 16))

# --- the runtime's packing and period against the model, on the host ----------------
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
b0 = SRC.index("static USHORT mad_f32_to_f16(float f) {")
b1 = SRC.index("/* uav-buf-clear-test:end */")
enum = ",\n".join("    %s = %d" % (k, v[0]) for k, v in sorted(FMT.items(), key=lambda kv: kv[1][0]))
HARNESS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef unsigned short USHORT; typedef uint32_t UINT32; typedef int32_t INT32; typedef unsigned UINT;
typedef enum {
''' + enum + r'''
} DXGI_FORMAT;
''' + SRC[b0:b1] + r'''
/* as the runtime's mad_format_is_int answers for the formats tested */
static int is_int(DXGI_FORMAT f) {
    switch (f) {
    case DXGI_FORMAT_R32G32B32A32_UINT: case DXGI_FORMAT_R32G32B32A32_SINT: case DXGI_FORMAT_R32G32B32_UINT:
    case DXGI_FORMAT_R16G16B16A16_UINT: case DXGI_FORMAT_R16G16B16A16_SINT: case DXGI_FORMAT_R32G32_UINT: case DXGI_FORMAT_R32G32_SINT:
    case DXGI_FORMAT_R10G10B10A2_UINT: case DXGI_FORMAT_R8G8B8A8_UINT: case DXGI_FORMAT_R8G8B8A8_SINT: case DXGI_FORMAT_R16G16_UINT:
    case DXGI_FORMAT_R16G16_SINT: case DXGI_FORMAT_R32_UINT: case DXGI_FORMAT_R32_SINT: case DXGI_FORMAT_R8G8_UINT: case DXGI_FORMAT_R8G8_SINT:
    case DXGI_FORMAT_R16_UINT: case DXGI_FORMAT_R16_SINT: case DXGI_FORMAT_R8_UINT: case DXGI_FORMAT_R8_SINT: return 1;
    default: return 0;
    }
}
int main(int argc, char **argv) {
    int i;
    for (i = 1; i + 5 < argc; i += 6) {
        UINT32 v[4], per[4]; UINT bpp = 0; int rc, k; const unsigned char *p = (const unsigned char *)per;
        DXGI_FORMAT f = (DXGI_FORMAT)atoi(argv[i]);
        for (k = 0; k < 4; k++) v[k] = (UINT32)strtoul(argv[i + 1 + k], NULL, 0);
        per[0] = per[1] = per[2] = per[3] = v[0];
        rc = mad_buf_clear_typed(f, v, atoi(argv[i + 5]), is_int(f), per, &bpp);
        if (!rc) per[0] = per[1] = per[2] = per[3] = v[0];   /* the plain path */
        printf("%d %u %d ", rc, bpp, mad_buf_clear_uniform(per));
        for (k = 0; k < 16; k++) printf("%02x", p[k]);
        printf("\n");
    }
    return 0;
}
'''
isint_src = SRC[SRC.index("static int mad_format_is_int(DXGI_FORMAT f) {"):]
isint_src = isint_src[:isint_src.index("default: return 0;")]
check("the harness's integer formats are the runtime's mad_format_is_int list",
      all(n in isint_src for n in ("DXGI_FORMAT_R32G32B32A32_UINT", "DXGI_FORMAT_R32G32_UINT", "DXGI_FORMAT_R8G8B8A8_UINT",
                                   "DXGI_FORMAT_R8G8_UINT", "DXGI_FORMAT_R8_UINT", "DXGI_FORMAT_R32_UINT", "DXGI_FORMAT_R16_UINT"))
      and "FLOAT" not in isint_src and "NORM" not in isint_src)
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = pathlib.Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = pathlib.Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the packing and the period compile on the host", r.returncode == 0, r.stderr)
        if r.returncode == 0:
            args = []
            for fmt, vals, isf, what in CASES:
                args += [str(fmt)] + [str(x) for x in vals] + [str(isf)]
            run = subprocess.run([str(exe)] + args, capture_output=True, text=True)
            lines = run.stdout.split()
            rows = [lines[i:i + 4] for i in range(0, len(lines), 4)]
            bad = []
            for (fmt, vals, isf, what), row in zip(CASES, rows):
                t, per = expect(fmt, vals, isf)
                got_t, got_uni, got_hex = int(row[0]), int(row[2]), row[3]
                want_uni = 1 if len(set(per)) == 1 else 0
                if got_t != t or got_hex != per.hex() or got_uni != want_uni:
                    bad.append("%s: taken %d (want %d), bytes %s (want %s), uniform %d (want %d)"
                               % (what, got_t, t, got_hex, per.hex(), got_uni, want_uni))
            check("%d clears: the runtime writes what Direct3D 12 writes, and leaves the plain path its own results" % len(CASES),
                  run.returncode == 0 and len(rows) == len(CASES) and not bad, "\n     ".join(bad) or run.stderr)
            g = rows[0] if rows else []
            check("...the game's clear: taken, 16-byte elements, the pattern 00000000 01000000 01000000 00000000",
                  g == ["1", "16", "0", "00000000010000000100000000000000"], g)
else:
    print("note: no host C compiler; the packing was not run")

# --- a model of the game's frame ---------------------------------------------------
RECORDS, STRIDE = 44, 16                       # "uber-shader-offset": 0x2c records of 0x10 bytes (tlou-i-l.exe 0x142164250)


def frame(typed_fix):
    buf = bytearray(RECORDS * STRIDE)
    t, per = expect(3, (0, 1, 1, 0), 0)
    if not typed_fix:
        per = struct.pack("<I", 0) * 4          # build 655: Values[0] in every 32 bits
    for o in range(0, len(buf), 16):            # the fill copies the 16-byte period from the view's start
        buf[o:o + 16] = per
    tiles = [258, 0, 3, 21] + [0] * 40          # the classify pass counts tiles into X (the log's samples)
    for k, n in enumerate(tiles):
        x = struct.unpack_from("<I", buf, k * STRIDE)[0] + n
        struct.pack_into("<I", buf, k * STRIDE, x)
    return [struct.unpack_from("<3I", buf, k * STRIDE) for k in range(RECORDS)]


new, old = frame(True), frame(False)
check("the game's frame: every record keeps Y = Z = 1, so a record with tiles runs its thread groups (258 x 1 x 1)",
      all(y == 1 and z == 1 for _, y, z in new) and new[0] == (258, 1, 1) and new[1] == (0, 1, 1))
check("...and with Values[0] in every word (build 655) every record had Y = Z = 0: the log's '258 0 0', no thread group",
      all(y == 0 and z == 0 for _, y, z in old) and old[0] == (258, 0, 0) and old[0][0] * old[0][1] * old[0][2] == 0)

# --- the whole runtime, for syntax, with the repo's toolchain -------------------------
mingw = R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
winemetal = R / "dxmt/src/winemetal"
if mingw.exists() and winemetal.exists():
    r = subprocess.run([str(mingw), "-fsyntax-only", "-O2", "-Wall", '-DMAD_SC_CONVERTER_ID="0000000000000000"',
                        "-I" + str(R / "madeira-d3d12/src/pe"), "-I" + str(R / "madeira-d3d12/src"), "-I" + str(winemetal),
                        str(R / "madeira-d3d12/src/pe/madeira_d3d12.c")], capture_output=True, text=True)
    check("madeira_d3d12.c compiles (arm64ec, -fsyntax-only)", r.returncode == 0, r.stderr[-3000:])
else:
    print("note: llvm-mingw / dxmt not in this tree; the syntax compile runs in the full tree")

ci = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-uav-typed-buffer-clear.py" in ci)

print("PASS: uav-typed-buffer-clear" if ok else "FAILED: uav-typed-buffer-clear")
sys.exit(0 if ok else 1)
