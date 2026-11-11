#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Wine's shell32 explorer browser: closing a folder window whose navigation pane never opened does not dereference NULL (night round 2026-11-11, A4 S5).

The Desktop's Explorer folder window (a second explorer.exe; Send Logs BWGQE7, N1455G and eight more with the same `navpane_on_wm_create Failed
(0x80070005)`): explorerframe.dll (CLSID_NamespaceTreeControl) is in the 32-bit set but not in aarch64-windows/ or arm64ec-windows/, so the navigation pane's
tree control cannot be created, `navpane_on_wm_create` leaves `This->navpane.pnstc2 = NULL` and returns FALSE (the pane window stays alive), and when the
window is destroyed `navpane_on_wm_destroy` calls INameSpaceTreeControl2_TreeUnadvise through that NULL pointer: the faulting `ldr x8,[x0]` is at RVA 0x2c9f0
of the aarch64 shell32.dll of build 651. Only the folder-window process dies, when the player closes the window; the Desktop and the game go on. The code adds
`if (!This->navpane.pnstc2) return TRUE;`, the guard the function needs on Windows too for a failed create.

WHERE THIS REACHES: nothing yet. The crashing binary is the COMMITTED 64-bit shell32.dll (app/Madeira/aarch64-windows/shell32.dll, the Explorer's), no CI step
rebuilds it, and the i386 farm's shell32 never shows the crash. build/wine-pe/build-modules.sh can rebuild shell32 for arm64ec-windows only; the aarch64 set has
no build script. So this script is for whoever rebuilds the committed shell32 (see the night-round note W-WINE.md, A4-S5); no CI step applies it.
Idempotent; fails by name when the function moved.
Usage: patch-wine-shell32-navpane-null.py WINE-DIR | path/to/wine/dlls/shell32/ebrowser.c
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: navpane null"
FILE = "dlls/shell32/ebrowser.c"
OLD = ("static LRESULT navpane_on_wm_destroy(ExplorerBrowserImpl *This)\n"
       "{\n"
       "    INameSpaceTreeControl2_TreeUnadvise(This->navpane.pnstc2, This->navpane.nstc_cookie);\n")
NEW = ("static LRESULT navpane_on_wm_destroy(ExplorerBrowserImpl *This)\n"
       "{\n"
       "    /* " + MARKER + " (tools/patch-wine-shell32-navpane-null.py, night round 2026-11-11, A4 S5): when explorerframe.dll is missing the tree control\n"
       "     * was never created (navpane_on_wm_create failed and left pnstc2 NULL); closing the window must not call through it. */\n"
       "    if (!This->navpane.pnstc2) return TRUE;\n"
       "    INameSpaceTreeControl2_TreeUnadvise(This->navpane.pnstc2, This->navpane.nstc_cookie);\n")


def patch(src):
    if MARKER in src:
        if src.count(NEW) != 1:
            raise ValueError("partial or changed navpane null patch")
        return src
    if src.count(OLD) != 1:
        raise ValueError("navpane_on_wm_destroy of shell32/ebrowser.c not found once as the fork has it (Wine changed)")
    return src.replace(OLD, NEW, 1)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: patch-wine-shell32-navpane-null.py WINE-DIR | path/to/wine/dlls/shell32/ebrowser.c")
    arg = Path(sys.argv[1])
    path = arg if arg.suffix == ".c" else arg / FILE
    source = path.read_text()
    try:
        result = patch(source)
    except ValueError as error:
        sys.exit("patch-wine-shell32-navpane-null: " + str(error))
    if result == source:
        print("patch-wine-shell32-navpane-null: already patched")
    else:
        path.write_text(result)
        print("patch-wine-shell32-navpane-null: patched")


if __name__ == "__main__":
    main()
