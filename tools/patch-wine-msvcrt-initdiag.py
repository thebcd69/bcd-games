#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""msvcrt init diag: say which step of msvcrt's DLL_PROCESS_ATTACH failed (round 624, agent E2 S13; log only).

Guacamelee Gold (0P65SX), Hitman: Contracts (KBBQJK) and Guacamelee STCE (DDFJM7, DNWRXA), all 32-bit, end with Wine's loader line
'"msvcrt.dll" failed to initialize': msvcrt's DllMain returned FALSE, and of its three failing steps only the TLS one says so ("TlsAlloc()
failed!"). This adds an ERR line before the other two FALSE returns:

  msvcrt: HeapCreate failed, error N                                   (msvcrt_init_heap)
  msvcrt: _create_locale("C") failed, ACP N OEMCP N error N             (msvcrt_init_locale)

Both sit on branches that end the process today; nothing else changes. dlls/msvcrt/main.c is shared by msvcrt and msvcr70-120: applied for
the i386 farm only and put back right after it (.github/workflows/build-ipa.yml), so the arm64ec msvcr* builds of
tools/build-wine-extra-dlls.sh (and their data-export patch) see the file as committed.

Usage: patch-wine-msvcrt-initdiag.py <wine source root> (idempotent: a second run says "already applied").
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: msvcrt init diag"

HEAP_OLD = """  case DLL_PROCESS_ATTACH:
    msvcrt_init_exception(hinstDLL);
    if(!msvcrt_init_heap())
        return FALSE;
"""
HEAP_NEW = """  case DLL_PROCESS_ATTACH:
    msvcrt_init_exception(hinstDLL);
    if(!msvcrt_init_heap()) {
        ERR("msvcrt: HeapCreate failed, error %lu\\n", GetLastError());   /* madeira-bcd: msvcrt init diag */
        return FALSE;
    }
"""
LOCALE_OLD = """    msvcrt_init_mt_locks();
    if(!msvcrt_init_locale()) {
        msvcrt_free_locks();
"""
LOCALE_NEW = """    msvcrt_init_mt_locks();
    if(!msvcrt_init_locale()) {
        ERR("msvcrt: _create_locale(\\"C\\") failed, ACP %u OEMCP %u error %lu\\n", GetACP(), GetOEMCP(), GetLastError());   /* madeira-bcd: msvcrt init diag */
        msvcrt_free_locks();
"""


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-msvcrt-initdiag.py <wine source root>")
    path = Path(sys.argv[1]) / "dlls/msvcrt/main.c"
    text = path.read_text()
    if MARKER in text:
        print("msvcrt init diag: already applied")
        return
    for old, new, what in ((HEAP_OLD, HEAP_NEW, "msvcrt_init_heap"), (LOCALE_OLD, LOCALE_NEW, "msvcrt_init_locale")):
        if text.count(old) != 1:
            sys.exit("patch-wine-msvcrt-initdiag: anchor '%s' not found exactly once in %s" % (what, path))
        text = text.replace(old, new)
    path.write_text(text)
    print("msvcrt init diag: applied")


if __name__ == "__main__":
    main()
