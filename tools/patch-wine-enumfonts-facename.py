#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's win32u NtGdiEnumFonts cuts a face name that does not fit its LOGFONTW (night round 2026-11-11, G4 S13).

FINAL FANTASY VII (2013), Steam 39140, Send Log 9J06NQ (build 634): the Qt 4 launcher FF7_Launcher.exe ends with c0000026 about 11 s into
its start, when its page (a web view) asks Windows for a font family. gdi32's EnumFontFamiliesExW passes lstrlenW(lf->lfFaceName) as the
length of the name; Qt 4 hands it a LOGFONTW whose face name holds 32 characters and no terminator, so the length reads 33, and win32u's
NtGdiEnumFonts copied 33 WCHARs (66 bytes) into the 32-WCHAR (64 bytes) lfFaceName of its local `lf`. The iOS SDK's headers fortify
memcpy (build/win32u-unix/build.sh compiles with -O2 and no -D_FORTIFY_SOURCE=0), so the host aborted in __chk_fail_overflow
("[mach_exc] bt[1] ... NtGdiEnumFonts+0x74", host x2=0x42, x3=0x40) and the abort reached the program as an exception no frame could take.

A name longer than the field is now cut to 31 characters before the copy, so the field keeps its terminator: a family of that name does not
exist, nothing is listed, as on Windows. A name of 32 characters or fewer is copied exactly as before (a length of 32 still copies the same 64
bytes), so only a call that aborted the process changes. Idempotent; fails by name when the function moved.
Usage: patch-wine-enumfonts-facename.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: NtGdiEnumFonts face name"
FILE = "dlls/win32u/font.c"
OLD = "    if (face_name_len) memcpy( lf.lfFaceName, face_name, face_name_len * sizeof(WCHAR) );\n"
NEW = (
    "    /* " + MARKER + " (tools/patch-wine-enumfonts-facename.py, night round 2026-11-11, FINAL FANTASY VII's Qt 4 launcher, 9J06NQ):\n"
    "     * gdi32's EnumFontFamiliesExW passes lstrlenW(lf->lfFaceName). A LOGFONTW whose face name holds 32 characters and no terminator\n"
    "     * (Qt 4 copies qMin(length + 1, 32) characters of a family name of 32 or more) makes that 33, and the copy overran the 32-WCHAR\n"
    "     * field of `lf`: the iOS SDK fortifies memcpy, so the host aborted in __chk_fail_overflow and the program ended with c0000026.\n"
    "     * A name that does not fit is cut to 31 characters (the field keeps its terminator); 32 or fewer are copied as before. */\n"
    "    if (face_name_len > LF_FACESIZE) face_name_len = LF_FACESIZE - 1;\n"
) + OLD
FUNCTION_ANCHOR = "BOOL WINAPI NtGdiEnumFonts( HDC hdc, ULONG type, ULONG win32_compat, ULONG face_name_len,\n"


def patch(src):
    if MARKER in src:
        if src.count(NEW) != 1 or src.count(OLD) != 1:
            raise ValueError("partial or changed NtGdiEnumFonts face-name patch")
        return src
    if src.count(FUNCTION_ANCHOR) != 1:
        raise ValueError("NtGdiEnumFonts of win32u/font.c not found once (Wine changed)")
    if src.count(OLD) != 1:
        raise ValueError("the face name copy of NtGdiEnumFonts not found once (Wine changed)")
    start = src.index(FUNCTION_ANCHOR)
    if not start < src.index(OLD) < start + 1500:
        raise ValueError("the face name copy is no longer inside NtGdiEnumFonts (Wine changed)")
    return src.replace(OLD, NEW, 1)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-enumfonts-facename.py WINE-DIR")
    path = Path(sys.argv[1]) / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-enumfonts-facename: " + str(error))
    if result == source:
        print("patch-wine-enumfonts-facename: already in")
    else:
        path.write_text(result)
        print("patch-wine-enumfonts-facename: %s patched" % FILE)


if __name__ == "__main__":
    main()
