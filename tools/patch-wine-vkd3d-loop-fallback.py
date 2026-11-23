#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's HLSL compiler (vkd3d-shader) writes a loop it cannot unroll as a bounded run of guarded copies, on request (round 707, S.T.A.L.K.E.R. Call of Pripyat 13CYSY).

Call of Pripyat (xrEngine compiles ~100 Direct3D 9 shaders at run time, build 686 log 13CYSY) stops compiling its R2 shader accum_volumetric_sun
(ps_3_0, entry main) with "E5017: Aborting due to not yet implemented feature: Instruction type HLSL_IR_LOOP". The shader loops
    int n = (int)((P.z - 0.3) / deltaDepth);  for (int i = 0; i < n; ++i) { ... }
where `n` comes from a texture sample (at most 39). vkd3d-shader's unroll_loops() (hlsl_codegen.c) tries to unroll every loop by running the
body through copy propagation until the loop's break is known; a count that is not a constant is never known, the unroller gives up at its
default limit (1024 iterations for Shader Model < 4), marks the loop HLSL_LOOP_FORCE_LOOP, and the Shader Model 1-3 (D3DBC) writer has no loop
instruction: sm1_generate_vsir_block() reports every HLSL_IR_LOOP as "Instruction type". Nothing that reaches that point compiled before.

With env.MADEIRA_HLSL_LOOP_FALLBACK set (off by default; a game's list row sets it) unroll_loops() gives such a loop one more try, with an
explicit limit: the loop is written as N copies of its body, each behind the guard `if (!broken)` the unroller already generates for a `break`
(the count test of the loop turns into the guard), so a loop that runs fewer than N times behaves exactly as before and the rest of the copies
are skipped at run time. The key reads once per process: `1` means 64 copies, a whole number from 8 to 256 means that many, unset, 0 or
anything else means off. A loop that would run more than N times is cut at N copies without a message (the stock unroller does the same for an
explicit [unroll(N)]); a game's row names a count above the loop's real bound (Call of Pripyat: n is at most 39, the row says 40).

What the code does, which decides the shape of the change (read in the tree, not assumed):
  - loop_unrolling_unroll_loop() builds everything in scratch blocks (`draft`, the cloned body) and touches the program only at its end; every
    failure path (`fail:`) frees them and returns false with the loop untouched. The only things the failed attempt leaves are a copy-propagation
    pass over the instructions in front of the loop (it keeps the program's meaning and a second pass changes nothing more) and two unused
    synthetic variables, which a failed unroll leaves today too. So asking again for the same loop is valid.
  - the explicit limit is what makes the second try work: `if (!unroll_limit && i == max_iterations) goto fail;` only fails without an
    explicit limit; with one the unroller stops quietly with iterations left, returns true, frees the loop node and puts the copies in its place.
  - unroll_loops() returns true both when the loop was unrolled and when it was given up (the caller reruns the folding passes and calls it again
    until a pass changes nothing; a loop given up is marked FORCE_LOOP and is not asked again). The change keeps that: the retry sits between the
    first failure and the FORCE_LOOP mark and the function still returns true.
  - a [unroll] without an explicit limit that failed has already recorded an error (VKD3D_SHADER_ERROR_HLSL_FAILED_FORCED_UNROLL) and Direct3D's
    compiler refuses it too, so it is not retried; an explicit [unroll(N)] answered for itself on the first try (no retry either); [loop]
    (FORCE_LOOP) never reaches the unroller; Shader Model 4 and later write a real loop (the TPF writer has one) and are never touched.
  - the first attempt runs as before, with its own limit, so a loop that unrolled before is byte for byte the same with the key on; only a loop
    that was an error ("Instruction type HLSL_IR_LOOP") before is written differently. The price is that such a loop is first tried at the stock
    limit (1024 iterations) and then again at N; with the key off nothing else is read or run and the generated code is identical. The cost of the
    second try grows faster than N (each copy is simplified against all the ones before it): for the game's shader, in a host build on the Mac,
    refusing it takes 0.37 s, 40 copies 0.47 s, 64 copies 0.67 s, 128 copies 2.3 s, 256 copies 17 s; a row names the smallest count above the
    loop's real bound.
The first success of a process writes one line to stderr (the session log), like the [hlsl-fail] lines of tools/patch-wine-vkd3d-hlsl-fail-dump.py:
  [hlsl-loop-fallback] madeira-bcd: a loop is written as 40 guarded copies
The i386 farm builds it (the 64-bit d3dcompiler_47.dll is a committed binary). Idempotent; fails by name when an anchor moved.
Usage: patch-wine-vkd3d-loop-fallback.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: hlsl loop fallback"
FILE = "libs/vkd3d/libs/vkd3d-shader/hlsl_codegen.c"
UNROLL = ("static bool loop_unrolling_unroll_loop(struct hlsl_ctx *ctx, struct hlsl_block *block, struct hlsl_ir_loop *loop,\n"
          "        unsigned int unroll_limit)\n")
QUIET = "    if (!unroll_limit && i == max_iterations)\n"
ANCHOR = "static bool unroll_loops(struct hlsl_ctx *ctx, struct hlsl_ir_node *node, void *context)\n"
CALL = ("    if (!loop_unrolling_unroll_loop(ctx, program, loop, unroll_limit))\n"
        "        loop->unroll_type = HLSL_LOOP_FORCE_LOOP;\n")
HELPER = (
    "/* " + MARKER + " (tools/patch-wine-vkd3d-loop-fallback.py, round 707, S.T.A.L.K.E.R. Call of Pripyat 13CYSY): with\n"
    " * env.MADEIRA_HLSL_LOOP_FALLBACK set, a loop the unroller gave up on is written as N guarded copies of its body when the target is below\n"
    " * Shader Model 4 (its writer has no loop instruction). 1 means 64, a whole number from 8 to 256 means that count; unset, 0 or anything\n"
    " * else means off. Read once per process. */\n"
    "static unsigned int madeira_hlsl_loop_fallback(void)\n"
    "{\n"
    "    static int copies = -1;\n"
    "\n"
    "    if (copies < 0)\n"
    "    {\n"
    "        const char *value = getenv(\"MADEIRA_HLSL_LOOP_FALLBACK\"), *p;\n"
    "        unsigned int n = 0;\n"
    "\n"
    "        copies = 0;\n"
    "        if (value && value[0])\n"
    "        {\n"
    "            for (p = value; *p >= '0' && *p <= '9' && n < 1000; ++p)\n"
    "                n = n * 10 + (*p - '0');\n"
    "            if (*p)\n"
    "                n = 0;\n"
    "            copies = n == 1 ? 64 : (n >= 8 && n <= 256 ? n : 0);\n"
    "        }\n"
    "    }\n"
    "    return copies;\n"
    "}\n"
    "\n"
    "/* " + MARKER + ": the second try for a loop whose first try failed. The failed try leaves the loop as it was, and an\n"
    " * explicit limit makes the unroller stop quietly with iterations left, so the loop becomes N guarded copies. Not for an explicit limit\n"
    " * (that was the first try), a forced [unroll] (its error is recorded) or Shader Model 4 and later (real loops). */\n"
    "static bool madeira_hlsl_unroll_fallback(struct hlsl_ctx *ctx, struct hlsl_block *program, struct hlsl_ir_loop *loop,\n"
    "        unsigned int unroll_limit)\n"
    "{\n"
    "    static uint32_t reported;\n"
    "    unsigned int copies;\n"
    "\n"
    "    if (unroll_limit || loop->unroll_type == HLSL_LOOP_FORCE_UNROLL || hlsl_version_ge(ctx, 4, 0))\n"
    "        return false;\n"
    "    if (!(copies = madeira_hlsl_loop_fallback()))\n"
    "        return false;\n"
    "    if (!loop_unrolling_unroll_loop(ctx, program, loop, copies))\n"
    "        return false;\n"
    "\n"
    "    if (vkd3d_atomic_increment_u32(&reported) == 1)\n"
    "        ERR(\"[hlsl-loop-fallback] madeira-bcd: a loop is written as %u guarded copies\\n\", copies);\n"
    "    return true;\n"
    "}\n"
    "\n"
)
CALL_NEW = ("    if (!loop_unrolling_unroll_loop(ctx, program, loop, unroll_limit)\n"
            "            && !madeira_hlsl_unroll_fallback(ctx, program, loop, unroll_limit)) /* " + MARKER + " */\n"
            "        loop->unroll_type = HLSL_LOOP_FORCE_LOOP;\n")


def patch(src):
    if MARKER in src:
        if src.count(HELPER) != 1 or src.count(CALL_NEW) != 1:
            raise ValueError("partial or changed hlsl loop fallback patch")
        return src
    if src.count(UNROLL) != 1:
        raise ValueError("loop_unrolling_unroll_loop of hlsl_codegen.c not found once (vkd3d changed)")
    if src.count(QUIET) != 1 or src.index(QUIET) < src.index(UNROLL):
        raise ValueError("the explicit-limit exit of loop_unrolling_unroll_loop (`!unroll_limit && i == max_iterations`) not found once "
                         "after the function (vkd3d changed; the retry relies on it)")
    if src.count(ANCHOR) != 1 or src.index(ANCHOR) < src.index(QUIET):
        raise ValueError("unroll_loops of hlsl_codegen.c not found once after loop_unrolling_unroll_loop (vkd3d changed)")
    if src.count(CALL) != 1 or src.index(CALL) < src.index(ANCHOR):
        raise ValueError("the loop_unrolling_unroll_loop call of unroll_loops not found once after the function (vkd3d changed)")
    return src.replace(ANCHOR, HELPER + ANCHOR, 1).replace(CALL, CALL_NEW, 1)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-vkd3d-loop-fallback.py WINE-DIR")
    path = Path(sys.argv[1]) / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-vkd3d-loop-fallback: " + str(error))
    if result == source:
        print("patch-wine-vkd3d-loop-fallback: already in")
    else:
        path.write_text(result)
        print("patch-wine-vkd3d-loop-fallback: %s patched" % FILE)


if __name__ == "__main__":
    main()
