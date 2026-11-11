#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's HLSL compiler (vkd3d-shader): a register reservation that names a shader target is honoured (night round 2026-11-11, D S4).

S.T.A.L.K.E.R. Clear Sky (32-bit xrEngine.exe; Send Logs N8E3WW, 3Z65YN, ZN046C, EJTKYE, R0RC90, F5HGRE, PHSYGM, build 630) compiles its
Direct3D 9 shaders at run time; after the `point` fix (tools/patch-wine-vkd3d-point-ident.py) the next shader, accum_sun_near, stops at
"<anonymous>:496:37: E5017: Aborting due to not yet implemented feature: Reservation shader target ps." and the game never opens its window.
vkd3d's grammar turns every reservation of the form `register(ps, c0)` (a shader type or a profile such as `ps_3_0` before the register)
into hlsl_fixme(), a compile error. Direct3D's compiler applies such a reservation only when it compiles for that shader type and ignores it
for the others.

The four grammar rules now ask madeira_reservation_target_applies(): a target whose type (ps, vs, gs, hs, ds, cs; a profile counts by its
type prefix) is the profile's keeps the reservation; another type drops it (the variable is placed as if it had none); an unknown target
gets a warning and keeps the reservation. Every source that compiles today is unchanged: each of them stopped at the fixme before.
Idempotent; fails by name when the rules moved.
Usage: patch-wine-vkd3d-register-target.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: register-target"
FILE = "libs/vkd3d/libs/vkd3d-shader/hlsl.y"
FIXME = '            hlsl_fixme(ctx, &@4, "Reservation shader target %s.", $4);\n'
ANCHOR = ("static bool parse_reservation_index(struct hlsl_ctx *ctx, const char *string, unsigned int bracket_offset,\n"
          "        struct hlsl_reg_reservation *reservation)\n")
HELPER = (
    "/* " + MARKER + " (tools/patch-wine-vkd3d-register-target.py, S.T.A.L.K.E.R. Clear Sky N8E3WW): a reservation that names a shader\n"
    " * target, register(ps, c0) or register(ps_3_0, c0), applies only when compiling for that shader type, as Direct3D's compiler does;\n"
    " * for another type it is ignored. Before, every such reservation was a compile error (hlsl_fixme). */\n"
    "static bool madeira_reservation_target_applies(struct hlsl_ctx *ctx, const char *target,\n"
    "        const struct vkd3d_shader_location *loc)\n"
    "{\n"
    "    static const struct\n"
    "    {\n"
    "        const char *name;\n"
    "        enum vkd3d_shader_type type;\n"
    "    }\n"
    "    types[] =\n"
    "    {\n"
    "        {\"ps\", VKD3D_SHADER_TYPE_PIXEL},\n"
    "        {\"vs\", VKD3D_SHADER_TYPE_VERTEX},\n"
    "        {\"gs\", VKD3D_SHADER_TYPE_GEOMETRY},\n"
    "        {\"hs\", VKD3D_SHADER_TYPE_HULL},\n"
    "        {\"ds\", VKD3D_SHADER_TYPE_DOMAIN},\n"
    "        {\"cs\", VKD3D_SHADER_TYPE_COMPUTE},\n"
    "    };\n"
    "    size_t len = strcspn(target, \"_\");\n"
    "    unsigned int i;\n"
    "\n"
    "    for (i = 0; i < ARRAY_SIZE(types); ++i)\n"
    "    {\n"
    "        if (len == 2 && !ascii_strncasecmp(target, types[i].name, 2))\n"
    "            return ctx->profile->type == types[i].type;\n"
    "    }\n"
    "    hlsl_warning(ctx, loc, VKD3D_SHADER_WARNING_HLSL_UNKNOWN_ATTRIBUTE,\n"
    "            \"Unknown shader target '%s' in a register reservation; the reservation is kept.\", target);\n"
    "    return true;\n"
    "}\n"
    "\n"
)
# rule 1: register(target, reg) -- the register is parsed first, the fixme only when it was a register
R1_OLD = ("            if (parse_reservation_index(ctx, $6, 0, &$$))\n"
          "            {\n"
          "                hlsl_fixme(ctx, &@4, \"Reservation shader target %s.\", $4);\n"
          "            }\n")
R1_NEW = ("            if (parse_reservation_index(ctx, $6, 0, &$$))\n"
          "            {\n"
          "                /* " + MARKER + " */\n"
          "                if (!madeira_reservation_target_applies(ctx, $4, &@4))\n"
          "                    memset(&$$, 0, sizeof($$));\n"
          "            }\n")
# rules 2-4: the fixme comes first; keep the flag and drop the reservation at the end of the action
TAIL_OLD = {
    "reg[expr]": ("            vkd3d_free($4);\n            vkd3d_free($6);\n            vkd3d_free($8);\n        }\n"
                  "    | ':' KW_REGISTER '(' any_identifier ',' any_identifier ',' any_identifier ')'\n"),
    "reg,space": ("            vkd3d_free($4);\n            vkd3d_free($6);\n            vkd3d_free($8);\n        }\n"
                  "    | ':' KW_REGISTER '(' any_identifier ',' any_identifier '[' expr ']' ',' any_identifier ')'\n"),
    "reg[expr],space": ("            vkd3d_free($4);\n            vkd3d_free($6);\n            vkd3d_free($8);\n            vkd3d_free($11);\n"
                        "        }\n"),
}
FLAG = ("            /* " + MARKER + " */\n"
        "            bool madeira_applies = madeira_reservation_target_applies(ctx, $4, &@4);\n")
DROP = "            if (!madeira_applies)\n                memset(&$$, 0, sizeof($$));\n"


def patch(src):
    if MARKER in src:
        if src.count(FIXME) or "hlsl_fixme(ctx, &@4, \"Reservation shader target" in src or src.count(HELPER) != 1:
            raise ValueError("partial or changed register-target patch")
        return src
    if src.count(ANCHOR) != 1:
        raise ValueError("parse_reservation_index() of hlsl.y not found once (vkd3d changed)")
    if src.count(R1_OLD) != 1:
        raise ValueError("the register(target, reg) rule of hlsl.y changed (vkd3d changed)")
    src = src.replace(ANCHOR, HELPER + ANCHOR, 1)
    src = src.replace(R1_OLD, R1_NEW, 1)
    if src.count(FIXME) != 3:
        raise ValueError("expected three more 'Reservation shader target' fixmes in hlsl.y, found %d (vkd3d changed)" % src.count(FIXME))
    # the three remaining actions, in file order, each start with the fixme; their ends are told apart by what follows them
    for key in ("reg[expr]", "reg,space", "reg[expr],space"):
        i = src.index(FIXME)
        tail = TAIL_OLD[key]
        j = src.find(tail, i)
        if j < 0 or (src.find(FIXME, i + 1) != -1 and j > src.find(FIXME, i + 1)):
            raise ValueError("the end of the %s reservation rule of hlsl.y not found (vkd3d changed)" % key)
        src = src[:i] + FLAG + src[i + len(FIXME):]
        j = src.find(tail, i)
        src = src[:j] + DROP + src[j:]
    if src.count(FIXME) or src.count("madeira_reservation_target_applies(ctx, $4, &@4)") != 4:
        raise ValueError("register-target patch did not apply cleanly")
    return src


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-vkd3d-register-target.py WINE-DIR")
    p = Path(sys.argv[1]) / FILE
    src = p.read_text()
    out = patch(src)
    if out != src:
        p.write_text(out)
        print("patch-wine-vkd3d-register-target: %s patched" % FILE)
    else:
        print("patch-wine-vkd3d-register-target: %s already patched" % FILE)


if __name__ == "__main__":
    main()
