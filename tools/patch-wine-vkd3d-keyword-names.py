#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's HLSL compiler (vkd3d-shader): six more words that Direct3D 9 allows as names are names unless a type follows (night round 2026-11-11, G11 S2).

Round 630 made `point` a name unless white space and a letter follow it (tools/patch-wine-vkd3d-point-ident.py; S.T.A.L.K.E.R. Clear Sky named
a variable `point`). Microsoft's Direct3D 9 compiler reserves only its own list of words (asm, class, compile, const, matrix, pass, register,
sampler, shared, string, technique, texture, vector, volatile, ...); vkd3d also reserves the Shader Model 4/5 words, and compiled as ps_3_0 each
of `line`, `lineadj`, `triangle`, `triangleadj`, `linear` and `centroid` as a variable name fails today with "E5000: syntax error, unexpected
KW_<WORD>" (G11 tried 53 candidate words; these six fail). xrEngine has ~100 shaders and each device round has found one more wall, so any of
them may be the next. In the grammar the six words appear only as a geometry-shader primitive (`line S v[2]`, `triangle S v[3]`, `lineadj`,
`triangleadj`) or an interpolation modifier (`linear float4 x`, `centroid float4 y`): a type or another modifier always follows, after white space.

The lexer now returns the keyword for these six words only when white space and a letter or underscore follow, and treats every other use as a
name, the way its {IDENTIFIER} rule does (the same two rules the `point` patch writes). Every source that compiles today tokenises the same:
a keyword followed by anything else was a syntax error before, and an identifier that merely starts with one of the words ("lines", "linear_x",
"trianglefan") is still one identifier (the longest match). Independent of the `point` patch (its own marker; either order works). The i386 farm
builds the lexer with flex. Idempotent; every rule is checked before the file is written; fails by name when a rule moved.
Usage: patch-wine-vkd3d-keyword-names.py WINE-DIR
"""
from pathlib import Path
import re
import sys

MARKER = "madeira-bcd: keyword-names"
FILE = "libs/vkd3d/libs/vkd3d-shader/hlsl.l"
WORDS = [("centroid", "KW_CENTROID"), ("line", "KW_LINE"), ("lineadj", "KW_LINEADJ"), ("linear", "KW_LINEAR"),
         ("triangle", "KW_TRIANGLE"), ("triangleadj", "KW_TRIANGLEADJ")]
IDENT = ("{IDENTIFIER}            {\n"
         "                            struct hlsl_ctx *ctx = yyget_extra(yyscanner);\n")


def old_rule(word, token):
    return "%-24s{return %s;%s}\n" % (word, token, " " * (31 - len("return ;") - len(token)))


def new_rule(word, token):
    return (
        "%s/({WS}|{NEWLINE})+[A-Za-z_] {return %s;}\n" % (word, token) +
        "%-24s{\n" % word +
        "                            /* " + MARKER + " (tools/patch-wine-vkd3d-keyword-names.py, night round 2026-11-11):\n"
        "                             * `" + word + "` is the keyword only where white space and a type or a modifier follow it; anywhere else it\n"
        "                             * is a name (Direct3D 9 shaders use it so), which before was a syntax error. Same action as {IDENTIFIER}. */\n"
        "                            struct hlsl_ctx *ctx = yyget_extra(yyscanner);\n"
        "\n"
        "                            yylval->name = hlsl_strdup(ctx, yytext);\n"
        "                            if (hlsl_get_var(ctx->cur_scope, yytext) || hlsl_get_function(ctx, yytext))\n"
        "                                return VAR_IDENTIFIER;\n"
        "                            if (hlsl_get_type(ctx->cur_scope, yytext, true, true))\n"
        "                                return TYPE_IDENTIFIER;\n"
        "                            return NEW_IDENTIFIER;\n"
        "                        }\n")


def patch(src):
    if MARKER in src:
        if src.count(MARKER) != len(WORDS) or any(src.count(new_rule(w, t)) != 1 for w, t in WORDS) \
                or any(old_rule(w, t) in src for w, t in WORDS):
            raise ValueError("partial or changed keyword-names patch")
        return src
    ident_at = src.find(IDENT)
    if src.count(IDENT) != 1:
        raise ValueError("the {IDENTIFIER} rule of hlsl.l not found once (vkd3d changed)")
    for w, t in WORDS:
        if src.count(old_rule(w, t)) != 1:
            raise ValueError("the `%s` keyword rule of hlsl.l not found once (vkd3d changed)" % w)
        if src.index(old_rule(w, t)) > ident_at:
            raise ValueError("the `%s` keyword rule of hlsl.l is not before the {IDENTIFIER} rule (vkd3d changed)" % w)
    for line in ("WS                      [ \\t]\n", "NEWLINE                 (\\n)|(\\r\\n)\n"):
        if line not in src:
            raise ValueError("the WS/NEWLINE definitions of hlsl.l changed (vkd3d changed)")
    for w, t in WORDS:
        src = src.replace(old_rule(w, t), new_rule(w, t), 1)
    return src


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-vkd3d-keyword-names.py WINE-DIR")
    path = Path(sys.argv[1]) / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-vkd3d-keyword-names: " + str(error))
    if result == source:
        print("patch-wine-vkd3d-keyword-names: already in")
    else:
        path.write_text(result)
        print("wine vkd3d-shader: line, lineadj, triangle, triangleadj, linear and centroid are names unless a type or a modifier follows")


if __name__ == "__main__":
    main()
