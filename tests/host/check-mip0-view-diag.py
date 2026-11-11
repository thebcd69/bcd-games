#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[mip0-view]: the D3D12 runtime names a texture view that Metal would refuse; no device.

Marvel's Spider-Man: Miles Morales (Steam 1817190), build 576 log of 2026-10-21 10:37 (L83617-83627) and PD7S1E (build 574, L88752-88762):
the game makes a 256x256 RGBA32F render-target + UAV texture with MipLevels 0 (the full 9-level chain in D3D12), this runtime makes one level
unless mip0-full = 1, the game then makes a UAV of level 1, and Metal's validation ("(newLevelRange.location + newLevelRange.length)(2) must
be <= [texture mipmapLevelCount](1)") ends the process (exception 80000101). The fix is the config line (mip0-full = 1, auto-tune rule
mip0-texture-view-beyond-levels, the game's recommended list). madeira_d3d12.c only gained a log line on the path that would have failed:
mad_texture_view_id logs "[mip0-view] r#N name WxH: a view of level L of a texture with M level(s) ..." (8 times at most) when the first
level lies beyond the texture's levels, and then does exactly what it did before.

  * checks statically that the log line sits in mad_texture_view_id after the clamps and before the Metal call, that its block changes
    nothing (no return, no assignment, no call but the log), that the clamps and the Metal call are the old text, and that mip0-full
    still reads 0 by default;
  * cuts mad_view_level_beyond and the level clamp out of the source, compiles them on the host and checks, for every first level, requested
    length and level count up to 12, that the helper is true exactly when the clamped view would still run past the texture's levels
    (Metal's rule: location + length <= level count);
  * checks the auto-tune rule's signals against what the runtime and Metal print, and that CI runs this check;
  * compiles the whole runtime for syntax with the repo's llvm-mingw when its toolchain is present (the full tree), else says so.

The runtime part is skipped when no host C compiler is found; the static checks still run.
"""
import pathlib, re, shutil, subprocess, sys, tempfile

R = pathlib.Path(__file__).resolve().parents[2]
SRC = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


def cut(sig):
    i = SRC.index(sig)
    # the definition, not a prototype: ml1308 (flat views) declares mad_texture_view_id ahead of mad_air_flat_view (round 632)
    while SRC.find(";", i) < SRC.find("{", i):
        i = SRC.index(sig, i + 1)
    j = SRC.index("{", i)
    depth = 0
    for k in range(j, len(SRC)):
        if SRC[k] == "{":
            depth += 1
        elif SRC[k] == "}":
            depth -= 1
            if depth == 0:
                return SRC[i:k + 1]
    raise SystemExit("unbalanced " + sig)


fn = cut("static UINT64 mad_texture_view_id(struct mad_device *d, struct mad_resource *r, enum WMTTextureType want,")
CLAMP = "if (nlvl == 0 || nlvl == ~0u || lvl0 + nlvl > r->tex_mips) nlvl = r->tex_mips > lvl0 ? r->tex_mips - lvl0 : 1;"
SLICES = "if (nsl == 0 || nsl == ~0u || sl0 + nsl > r->tex_layers) nsl = r->tex_layers > sl0 ? r->tex_layers - sl0 : 1;"
METAL = "tex = MTLTexture_newTextureView(r->texture, pf, want, (uint16_t)lvl0, (uint16_t)nlvl, (uint16_t)sl0, (uint16_t)nsl, sw, &id);"
GATE = "if (mad_view_level_beyond(lvl0, r->tex_mips)) {"

# --- static checks ---------------------------------------------------------
check("the level clamp is the old text", fn.count(CLAMP) == 1)
check("the slice clamp is the old text", fn.count(SLICES) == 1)
check("the Metal view call is the old text, once", fn.count(METAL) == 1)
check("the log line sits after both clamps and before the Metal call",
      fn.index(CLAMP) < fn.index(SLICES) < fn.index(GATE) < fn.index(METAL))
start = fn.index(GATE)
depth, end = 0, None
for k in range(start, len(fn)):
    if fn[k] == "{":
        depth += 1
    elif fn[k] == "}":
        depth -= 1
        if depth == 0:
            end = k
            break
block = fn[start:end + 1]
body = block[len(GATE):]
rest = fn[end + 1:].lstrip()
if rest.startswith("{   /* night round 2026-11-11 (G1 S8"):
    # build 652: the view-sample-count guard (tests/host/check-d3d12-view-sample-count.py) is a block of its own between this block and the early return
    depth = 0
    for k, ch in enumerate(rest):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                rest = rest[k + 1:].lstrip()
                break
check("the block ends in its own closing brace before the old early return (the view-sample-count guard of build 652 may sit between)",
      rest.startswith("if (want == r->tex_type && lvl0 == 0"))
check("the block only logs: no return, no goto, no assignment to a view argument or to the resource",
      not re.search(r"\breturn\b|\bgoto\b", body)
      and not re.search(r"\b(lvl0|nlvl|sl0|nsl|pf|swz|want|id|tex)\s*(=[^=]|\+=|-=|\+\+|--)", body)
      and "r->" not in re.sub(r"r->(serial|name|width|height|tex_mips|desc\.MipLevels)", "", body))
check("it logs at most 8 times", "InterlockedIncrement(&said_beyond) <= 8" in body)
check("the line is tagged [mip0-view] and names the resource, its size, the level and the count",
      '"[mip0-view] r#%u %s %ux%u: a view of level %u of a texture with %u level(s) (made with MipLevels %u)' in body
      and "r->serial, r->name ? r->name : \"?\", r->width, r->height, lvl0, r->tex_mips" in body)
check("it names the fix only when the texture was made with MipLevels 0",
      'r->desc.MipLevels ? "" : "; bcd.cfg mip0-full = 1 gives a MipLevels 0 texture its full chain"' in body)
check("mip0-full still reads 0 by default (no behaviour change)",
      'g_mip0_full = mad_cfg_int_pe("mip0-full", 0) ? 1 : 0;' in SRC)
check("the runtime's own [mip0] line still says what the auto-tune rule looks for",
      'GetDesc answers 0 (bcd.cfg mip0-full = 1 resolves it)' in SRC and '"[mip0] r#%u %llux%ux%u dx%u flags %#x%s' in SRC)

# --- auto-tune rule and CI -------------------------------------------------
rules = (R / "build/tools/autotune_rules.py").read_text()
i = rules.index('"id": "mip0-texture-view-beyond-levels"')
rule = rules[i:rules.index('"id": "locale-en-us"', i)]
check("the auto-tune rule adds mip0-full = 1 for Direct3D 12, without a retry",
      '"lines": ["mip0-full = 1"]' in rule and '"api": "d3d12"' in rule and '"noRetry": True' in rule)
check("its signals are the runtime's [mip0] line and Metal's two assertion lines",
      's("[mip0]", "GetDesc answers 0")' in rule and 's("_mtlValidateArgumentsForTextureViewOnDevice", "failed assertion")' in rule
      and 's("newLevelRange.location", "mipmapLevelCount")' in rule)
check("the recommended list of the game has the line",
      '"mip0-full = 1"' in (R / "recommended/recommendations.json").read_text().split('"id": "marvels-spider-man-miles-morales"')[1])
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "tests/host/check-mip0-view-diag.py" in wf)

# --- the helper against the clamp, on the host -----------------------------
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
b = SRC.index("/* mip0-view-test:begin */")
e = SRC.index("/* mip0-view-test:end */")
HARNESS = r'''
#include <stdio.h>
typedef unsigned UINT;
''' + SRC[b:e] + r'''
/* the clamp, as the runtime writes it, over the texture's level count */
static UINT clamp_len(UINT lvl0, UINT nlvl, UINT tex_mips) {
    struct { UINT tex_mips; } rr = { tex_mips }, *r = &rr;
    ''' + CLAMP + r'''
    return nlvl;
}
int main(void) {
    UINT tex_mips, lvl0, req, cases = 0, beyond = 0;
    UINT reqs[] = { 0, ~0u, 1, 2, 3, 5, 9, 12 };
    for (tex_mips = 1; tex_mips <= 12; tex_mips++)
        for (lvl0 = 0; lvl0 <= 13; lvl0++)
            for (req = 0; req < sizeof reqs / sizeof reqs[0]; req++) {
                UINT len = clamp_len(lvl0, reqs[req], tex_mips);
                int metal_refuses = lvl0 + len > tex_mips;   /* location + length <= mipmapLevelCount */
                cases++;
                if (metal_refuses) beyond++;
                if (metal_refuses != !!mad_view_level_beyond(lvl0, tex_mips)) {
                    printf("FAIL lvl0=%u req=%u mips=%u len=%u refused=%d helper=%d\n", lvl0, reqs[req], tex_mips, len, metal_refuses,
                           mad_view_level_beyond(lvl0, tex_mips));
                    return 1;
                }
            }
    /* the Miles Morales case: a UAV of level 1 on the one-level texture, length 1 */
    if (!mad_view_level_beyond(1, 1) || clamp_len(1, 1, 1) != 1 || !(1 + clamp_len(1, 1, 1) > 1)) { puts("FAIL the Miles Morales case"); return 1; }
    /* with the full chain (mip0-full = 1) level 1 of 9 is fine; a MipLevels 0 texture's level 0 never is "beyond" */
    if (mad_view_level_beyond(1, 9) || mad_view_level_beyond(0, 1) || mad_view_level_beyond(0, 0) || mad_view_level_beyond(5, 0)) { puts("FAIL ok cases"); return 1; }
    printf("clamp x helper: %u cases, %u refused by Metal's rule, helper agrees on all\n", cases, beyond);
    return 0;
}
'''
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = pathlib.Path(tmp) / "t.c"
        c.write_text(HARNESS)
        exe = pathlib.Path(tmp) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the helper and the clamp compile on the host", r.returncode == 0)
        if r.returncode:
            print(r.stderr)
        else:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print(run.stdout.rstrip())
            check("the helper is true exactly when the clamped view still runs past the texture's levels", run.returncode == 0)
else:
    print("note: no host C compiler; the helper was not run")

# --- the whole runtime, for syntax, with the repo's toolchain -----------------
mingw = R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
winemetal = R / "dxmt/src/winemetal"
if mingw.exists() and winemetal.exists():
    r = subprocess.run([str(mingw), "-fsyntax-only", "-O2", "-Wall", '-DMAD_SC_CONVERTER_ID="0000000000000000"',
                        "-I" + str(R / "madeira-d3d12/src/pe"), "-I" + str(R / "madeira-d3d12/src"), "-I" + str(winemetal),
                        str(R / "madeira-d3d12/src/pe/madeira_d3d12.c")], capture_output=True, text=True)
    check("madeira_d3d12.c compiles (arm64ec, -fsyntax-only)", r.returncode == 0)
    if r.returncode:
        print(r.stderr[-3000:])
else:
    print("note: llvm-mingw / dxmt not in this tree; the syntax compile runs in the full tree")

sys.exit(0 if ok else 1)
