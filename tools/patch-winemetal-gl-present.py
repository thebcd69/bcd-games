#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A GL game's frames count as presents (OpenGL, gl-backend = zink or gles).

winemetal's madeira_get_present_count() is what the HUD, the launch checks that wait for a first frame ("the game is
presenting"), the Dock's starting screen, the pause logic and the engine's spin census read. DXMT and madeira-d3d12 count
their presents in it; the winios OpenGL presenter (app/Madeira/Winios/WiniosGL.m) does not go through winemetal, so a GL
game would show 0 FPS and the library would wait for a first frame for ever.

This patch makes the getter add madeira_gl_present_count() (WiniosGL.m, linked into the app) to its own count. The symbol
is a weak import, so a link without WiniosGL.m still links and adds nothing; without OpenGL in use the added value is 0, so
the getter answers exactly what it did. Native only (winemetal unix side), not in the i386 farm key. Idempotent; fails by
name if the anchor moves. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd gl present"

OLD = """uint64_t madeira_get_present_count(void) {
  return atomic_load_explicit(&g_madeira_present_count, memory_order_relaxed);
}
"""
NEW = """uint64_t madeira_get_present_count(void) {
  uint64_t n = atomic_load_explicit(&g_madeira_present_count, memory_order_relaxed);
  {   /* madeira-bcd gl present (tools/patch-winemetal-gl-present.py): the frames the winios OpenGL presenter put on screen */
    extern uint64_t madeira_gl_present_count(void) __attribute__((weak_import));
    if (madeira_gl_present_count)
      n += madeira_gl_present_count();
  }
  return n;
}
"""


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("winemetal_unix.c: GL presents already counted")
        return 0
    if s.count(OLD) != 1:
        sys.exit(f"patch-winemetal-gl-present: anchor found {s.count(OLD)} times (want 1) in {PATH}:\n{OLD}")
    PATH.write_text(s.replace(OLD, NEW))
    print("winemetal_unix.c: GL presents counted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
