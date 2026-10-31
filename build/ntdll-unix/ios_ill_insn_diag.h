/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * [ill-insn] (round 624, agent R S14; GTA IV 1WFP0J on an iPhone 11 Pro Max, A13): the name of an instruction word that raised SIGILL, for
 * ill_handler's "ILL diag: insn_stream" line. The A13 lacks several ARMv8.5 features the A14 and later have (the FEX probe line of that
 * log: FLAGM2=0, FRINTTS=0, CSSC=0); a word from one of them in FEX-generated code names the FEX path that emitted it. Pure: no memory,
 * no state; every encoding below was checked with `llvm-mc --disassemble -triple=arm64 -mattr=+all` (tests/host/check-ill-insn-diag.py
 * repeats that when llvm-mc is present). */
#ifndef IOS_ILL_INSN_DIAG_H
#define IOS_ILL_INSN_DIAG_H

#include <stdint.h>

/* The ARMv8 feature an exactly known post-A13 encoding belongs to, or NULL (only these; the word itself is printed either way). */
static const char *ios_ill_insn_note( uint32_t insn )
{
    if (insn == 0xd500403fu) return "XAFLAG (FEAT_FlagM2)";
    if (insn == 0xd500405fu) return "AXFLAG (FEAT_FlagM2)";
    if (insn == 0xd500401fu) return "CFINV (FEAT_FlagM)";
    if (insn == 0xd50330ffu) return "SB (FEAT_SB)";
    if ((insn & 0xffffffe0u) == 0xd53b2400u) return "MRS RNDR (FEAT_RNG)";
    if ((insn & 0xffffffe0u) == 0xd53b2420u) return "MRS RNDRRS (FEAT_RNG)";
    switch (insn & 0xff3ffc00u)   /* scalar, single or double (ftype 00/01) */
    {
    case 0x1e284000u: return "FRINT32Z scalar (FEAT_FRINTTS)";
    case 0x1e28c000u: return "FRINT32X scalar (FEAT_FRINTTS)";
    case 0x1e294000u: return "FRINT64Z scalar (FEAT_FRINTTS)";
    case 0x1e29c000u: return "FRINT64X scalar (FEAT_FRINTTS)";
    }
    if (!((insn >> 22) & 1) || ((insn >> 30) & 1))   /* vector: 2S, 4S or 2D (Q=0 with sz=1 is unallocated) */
    {
        switch (insn & 0xbfbffc00u)
        {
        case 0x0e21e800u: return "FRINT32Z vector (FEAT_FRINTTS)";
        case 0x2e21e800u: return "FRINT32X vector (FEAT_FRINTTS)";
        case 0x0e21f800u: return "FRINT64Z vector (FEAT_FRINTTS)";
        case 0x2e21f800u: return "FRINT64X vector (FEAT_FRINTTS)";
        }
    }
    switch (insn & 0x7ffffc00u)   /* W or X form */
    {
    case 0x5ac01c00u: return "CNT (FEAT_CSSC)";
    case 0x5ac01800u: return "CTZ (FEAT_CSSC)";
    case 0x5ac02000u: return "ABS (FEAT_CSSC)";
    }
    return NULL;
}

/* The top-level class of an A64 word (bits 28:25, "op0" of the Arm ARM's encoding index). */
static const char *ios_ill_insn_class( uint32_t insn )
{
    switch ((insn >> 25) & 15)
    {
    case 0:  return insn == 0 ? "all zero (UDF #0: an empty page)" : "reserved/SME";
    case 2:  return "SVE";
    case 1: case 3: return "unallocated";
    case 8: case 9: return "data processing (immediate)";
    case 10: case 11: return "branch, exception, system";
    case 4: case 6: case 12: case 14: return "load/store";
    case 5: case 13: return "data processing (register)";
    default: return "data processing (scalar FP and SIMD)";   /* 7, 15 */
    }
}

#endif
