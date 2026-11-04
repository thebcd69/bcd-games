#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's HLSL compiler (vkd3d-shader): `point` is a name unless a type or a modifier follows it (round 630, agent O3 S19).

S.T.A.L.K.E.R. Clear Sky (32-bit xrEngine.exe, Send Log RVFPNE, build 623) compiles its Direct3D 9 shaders at run time, and one of them
names a variable `point`: "<anonymous>:400:40: E5000: syntax error, unexpected KW_POINT", and the game stopped. vkd3d's lexer makes
`point` a keyword everywhere; its only use in the grammar is the geometry-shader input primitive of a parameter (`point S v[1]`), where
a type or a modifier always follows, after white space. Direct3D 9's compiler had no such keyword.

The lexer now returns the keyword only when white space and a letter or underscore follow `point`, and treats every other `point` as a
name, the way its {IDENTIFIER} rule does. Every source that compiles today tokenises the same: a keyword `point` followed by anything
else was a syntax error before (the grammar needs a type or a modifier after it), and an identifier that starts with "point" ("points",
"point_x") is still one identifier (the longest match). So only shaders that failed to compile change. The farms build the lexer with
flex from this file; the i386 farm is built from the patched tree. Idempotent; fails by name when the rule moved.
Usage: patch-wine-vkd3d-point-ident.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: point-ident"
FILE = "libs/vkd3d/libs/vkd3d-shader/hlsl.l"
OLD = "point                   {return KW_POINT;               }\n"
NEW = (
    "point/({WS}|{NEWLINE})+[A-Za-z_] {return KW_POINT;      }\n"
    "point                   {\n"
    "                            /* " + MARKER + " (tools/patch-wine-vkd3d-point-ident.py, round 630, S.T.A.L.K.E.R. RVFPNE):\n"
    "                             * `point` is the geometry-shader primitive only where white space and a type or a\n"
    "                             * modifier follow it; anywhere else it is a name (Direct3D 9 shaders use it so), which\n"
    "                             * before was a syntax error. Same action as {IDENTIFIER}. */\n"
    "                            struct hlsl_ctx *ctx = yyget_extra(yyscanner);\n"
    "\n"
    "                            yylval->name = hlsl_strdup(ctx, yytext);\n"
    "                            if (hlsl_get_var(ctx->cur_scope, yytext) || hlsl_get_function(ctx, yytext))\n"
    "                                return VAR_IDENTIFIER;\n"
    "                            if (hlsl_get_type(ctx->cur_scope, yytext, true, true))\n"
    "                                return TYPE_IDENTIFIER;\n"
    "                            return NEW_IDENTIFIER;\n"
    "                        }\n"
)
IDENT = ("{IDENTIFIER}            {\n"
         "                            struct hlsl_ctx *ctx = yyget_extra(yyscanner);\n")


def patch(src):
    if MARKER in src:
        if src.count(NEW) != 1 or OLD in src:
            raise ValueError("partial or changed point-ident patch")
        return src
    if src.count(OLD) != 1:
        raise ValueError("the `point` keyword rule of hlsl.l not found once (vkd3d changed)")
    if src.count(IDENT) != 1 or src.index(OLD) > src.index(IDENT):
        raise ValueError("the {IDENTIFIER} rule of hlsl.l not found once after the `point` rule (vkd3d changed)")
    for line in ("WS                      [ \\t]\n", "NEWLINE                 (\\n)|(\\r\\n)\n"):
        if line not in src:
            raise ValueError("the WS/NEWLINE definitions of hlsl.l changed (vkd3d changed)")
    return src.replace(OLD, NEW, 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = Path(sys.argv[1]) / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-vkd3d-point-ident: " + str(error))
    if result == source:
        print("patch-wine-vkd3d-point-ident: already in")
    else:
        path.write_text(result)
        print("wine vkd3d-shader: `point` is a name unless white space and a type or a modifier follow it")
