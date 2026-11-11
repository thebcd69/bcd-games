/* The return addresses a message box's caller left on the game's stack ([dlg-code]; env.MADEIRA_DIALOG_CODE_DUMP, default off, log only)
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * The Last of Us Part I (builds 638 to 640, the owner's logs of 2026-11-07) creates its Direct3D 12 device at 12_0, reads its capabilities
 * and NVIDIA's driver interface, destroys the device and shows "a GPU supporting D3D_FEATURE_LEVEL_12_0 or higher is required". The log shows
 * every answer BCD gave, but not which one the game rejects: three guesses in a row (Device9, two capabilities, the PCI device id) did not
 * move it. When the box's text is shown, the game's code that decided to show it is on the game's stack, above the call into user32. So
 * the stack is scanned for words that point into the program's image right after a call instruction, and the code before each such return
 * address is printed as hex (virtual_ios.c, madeira_dialog_code_dump; tools/jumbo-code-disasm.py turns it into assembly offline).
 *
 * This file holds the one decision that is easy to get wrong, free of Wine types, so tests/host/check-dlg-code.py compiles and runs it on the
 * host: whether the 7 bytes before a candidate return address end in an x86-64 call instruction.
 */
#ifndef IOS_DLG_CODE_H
#define IOS_DLG_CODE_H

/* b[0..6] are the 7 bytes just before the candidate return address (b[6] is the last byte of the instruction, if it is one). 1 when
 * they end in a call: call rel32 (E8), call [rip+disp32] (FF 15), call [reg+disp32] (FF 90..97, with a SIB byte FF 94 xx), call [reg+disp8]
 * (FF 50..57, with a SIB byte FF 54 xx), call reg / call [reg] (FF D0..D7 / FF 10..17), each also with a REX prefix (40..4F) where the
 * register form needs it (r8..r15). A word that is merely an address inside the image (a pointer to data or to a jump table) does not end a
 * call and is left out. */
static inline int ios_dlg_call_site( const unsigned char b[7] )
{
    if (b[2] == 0xe8) return 1;                                            /* E8 rel32 */
    if (b[1] == 0xff && b[2] == 0x15) return 1;                            /* FF 15 disp32 */
    if (b[1] == 0xff && (b[2] & 0xf8) == 0x90 && b[2] != 0x94) return 1;   /* FF /2 [reg+disp32] */
    if (b[0] == 0xff && b[1] == 0x94) return 1;                            /* FF 94 SIB disp32 */
    if (b[4] == 0xff && (b[5] & 0xf8) == 0x50 && b[5] != 0x54) return 1;   /* FF /2 [reg+disp8] */
    if (b[3] == 0xff && b[4] == 0x54) return 1;                            /* FF 54 SIB disp8 */
    if (b[5] == 0xff && (b[6] & 0xf8) == 0xd0) return 1;                   /* FF D0+r: call reg */
    if (b[5] == 0xff && (b[6] & 0xf8) == 0x10 && b[6] != 0x14 && b[6] != 0x15) return 1;   /* FF /2 [reg] */
    if (b[4] == 0xff && b[5] == 0x14) return 1;                            /* FF 14 SIB: call [base+index*s] */
    return 0;
}

#endif /* IOS_DLG_CODE_H */
