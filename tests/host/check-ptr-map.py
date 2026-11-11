#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[ptr-map] (build 646): points on a windowed game's picture go into its window (app/Madeira/Winios/winios_ptr_map.h, Winios.m).

Slime Rancher 2 (a tester's log of 2026-11-07, build 634, iPad13,16) started in a 1158x832 window at (285,184) on a 1728x1200 screen;
the game view showed that window's picture, but fingers, the pointer and the Pencil were placed on the whole screen, so the game's menu
took no click although every click and key reached its window. Checks:
  - the header compiled on the host: when a rect is used, the mapping both ways, the 16-bit packing;
  - Winios.m: every absolute mouse event is mapped where it is queued, the rect is kept per game-mode window, updated where windows
    change, off in a desktop session, while an overlay window is shown, and at a new session;
  - Winios.h declares the two calls; HardwareInput.swift draws a cursor Windows placed back in the picture's space;
  - CI runs this check.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


header = root / "app/Madeira/Winios/winios_ptr_map.h"
winios = (root / "app/Madeira/Winios/Winios.m").read_text()
winios_h = (root / "app/Madeira/Winios/Winios.h").read_text()
hw = (root / "app/Madeira/HardwareInput.swift").read_text()

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include "winios_ptr_map.h"
static int fails;
#define EXPECT(c, what) do { if (!(c)) { printf("FAIL %s\n", what); fails++; } } while (0)
int main(void)
{
    const int sw = 1728, sh = 1200, cx = 285, cy = 184, cw = 1158, ch = 832;   /* Slime Rancher 2's window, the tester's screen */
    int x, y, i, worst = 0, ox, oy, px, py, w, h;

    EXPECT(winios_ptr_map_wanted(sw, sh, cx, cy, cw, ch), "Slime Rancher 2's window is one points go into");
    EXPECT(!winios_ptr_map_wanted(sw, sh, 0, 0, sw, sh), "a window that covers the screen exactly maps to itself: off");
    EXPECT(winios_ptr_map_wanted(sw, sh, 0, 0, sw, sh - 30), "a full-width window with a title bar is moved into");
    EXPECT(!winios_ptr_map_wanted(sw, sh, cx, cy, 63, ch), "a window under 64 pixels wide is not");
    EXPECT(!winios_ptr_map_wanted(sw, sh, cx, cy, cw, 10), "...nor one under 64 pixels high");
    EXPECT(!winios_ptr_map_wanted(sw, sh, -32000, -32000, cw, ch), "a window parked off the screen is not shown: off");
    EXPECT(!winios_ptr_map_wanted(sw, sh, sw, 0, cw, ch), "...nor one right of the screen");
    EXPECT(!winios_ptr_map_wanted(0, 0, cx, cy, cw, ch), "no screen size yet: off");
    EXPECT(winios_ptr_map_wanted(sw, sh, -100, -50, sw + 200, sh + 100), "a window larger than the screen, partly on it: moved into");

    x = sw / 2; y = sh / 2; winios_ptr_map_apply(&x, &y, sw, sh, cx, cy, cw, ch);
    EXPECT(x == cx + cw / 2 && y == cy + ch / 2, "the picture's centre is the window's centre");
    x = 0; y = 0; winios_ptr_map_apply(&x, &y, sw, sh, cx, cy, cw, ch);
    EXPECT(x == cx && y == cy, "the picture's top-left is the client's top-left");
    x = sw - 1; y = sh - 1; winios_ptr_map_apply(&x, &y, sw, sh, cx, cy, cw, ch);
    EXPECT(x == cx + cw - 1 && y == cy + ch - 1, "the picture's last pixel is the client's last (inside the window)");
    x = 1182; y = 593; winios_ptr_map_apply(&x, &y, sw, sh, cx, cy, cw, ch);
    EXPECT(x == 285 + (1182 * 1158 + 864) / 1728 && y == 184 + (593 * 832 + 600) / 1200,
           "the tester's click at (1182,593) on the picture lands at the same place in the window");
    x = -50; y = 99999; winios_ptr_map_apply(&x, &y, sw, sh, cx, cy, cw, ch);
    EXPECT(x == cx && y == cy + ch - 1, "points outside the picture stay inside the window");

    for (i = 0; i < sw; i += 7)
    {
        ox = i; oy = (i * 7) % sh;
        px = ox; py = oy;
        winios_ptr_map_apply(&px, &py, sw, sh, cx, cy, cw, ch);
        winios_ptr_map_invert(&px, &py, sw, sh, cx, cy, cw, ch);
        if (abs(px - ox) > worst) worst = abs(px - ox);
        if (abs(py - oy) > worst) worst = abs(py - oy);
    }
    EXPECT(worst <= 2, "there and back moves a point by at most 2 pixels (one window pixel is 1.5 picture pixels)");
    x = cx - 40; y = cy - 40; winios_ptr_map_invert(&x, &y, sw, sh, cx, cy, cw, ch);
    EXPECT(x == 0 && y == 0, "a cursor left or above the window is drawn at the picture's edge");
    x = cx + cw + 40; y = cy + ch + 40; winios_ptr_map_invert(&x, &y, sw, sh, cx, cy, cw, ch);
    EXPECT(x == sw - 1 && y == sh - 1, "...right or below, at the far edge");

    winios_ptr_map_unpack(winios_ptr_map_pack(-120, 2000, 1158, 832), &x, &y, &w, &h);
    EXPECT(x == -120 && y == 2000 && w == 1158 && h == 832, "packing keeps a negative x and the rest");
    winios_ptr_map_unpack(winios_ptr_map_pack(0, 0, 65535, 1), &x, &y, &w, &h);
    EXPECT(w == 65535 && h == 1, "sizes are unsigned 16-bit");
    EXPECT(winios_ptr_map_pack(cx, cy, cw, ch) != 0, "a real rect never packs to 0 (0 means off)");
    if (!fails) printf("header-ok\n");
    return fails != 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "t.c"
        c.write_text(harness)
        exe = Path(tmp) / "t"
        b = subprocess.run([cc, "-Wall", "-Werror", "-I", str(header.parent), "-o", str(exe), str(c)], capture_output=True, text=True)
        check("winios_ptr_map.h compiles on the host (-Wall -Werror)", b.returncode == 0, b.stderr)
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            check("the map is used only for a real window, moves the picture into it and back, and packs safely",
                  r.returncode == 0 and "header-ok" in r.stdout, r.stdout)
else:
    print("skip the host compile: no C compiler")


def body(src, start):
    i = src.find(start)
    if i < 0:
        return ""
    j = src.find("\n}\n", i)
    return src[i:j + 3]


push = body(winios, "static void winios_q_push_ev(unsigned int type, int x, int y, unsigned int flags, unsigned int data) {")
check("every absolute mouse event is mapped where it is queued (fingers, pointer, Pencil, trackpad all pass there)",
      "if (type == WINIOS_EV_MOUSE && (flags & MOUSEEVENTF_ABSOLUTE)) (void)winios_pointer_map_apply(&x, &y);" in push
      and push.find("winios_pointer_map_apply") < push.find("pthread_mutex_lock(&g_input_q.lock);"), push)
check("...and Winios.m posts mouse events only through that queue (the touch bridge and winios_pointer)",
      winios.count("winios_q_push_ev(WINIOS_EV_MOUSE") == 4 and "send_hardware_message" not in winios, winios.count("winios_q_push_ev(WINIOS_EV_MOUSE"))
load = body(winios, "static int winios_ptr_map_load(")
check("a reader takes the rect only while it is a real one (winios_ptr_map_wanted), the screen written before the client rect",
      "return winios_ptr_map_wanted(*sw, *sh, *cx, *cy, *cw, *ch);" in load and "if (!c) return 0;" in load, load)
upd = body(winios, "static void winios_ptr_map_update(const char *why) {")
check("the update runs only in a game session with a Metal window, and not while an overlay window is shown",
      "if (g_comp_game && g_game_metal.count) {" in upd
      and "if (![g_game_metal containsObject:k] && !l.hidden && !CGRectIsEmpty(l.bounds)) { overlay = YES; break; }" in upd
      and "if (!overlay) {" in upd, upd)
check("...takes the swapchain window shown last, else the only Metal window with a known client rect",
      "if (lk && [g_game_metal containsObject:lk] && g_client_rects[lk]) pick = lk;" in upd and "if (n != 1) pick = nil;" in upd, upd)
check("...writes the screen before the client rect, 0 turns it off, and logs each change (32 lines)",
      upd.find("atomic_store_explicit(&g_ptr_map_screen, screen, memory_order_release);")
      < upd.find("atomic_store_explicit(&g_ptr_map_client, client, memory_order_release);")
      and "if (++n <= 32) {" in upd and "[ptr-map] off: points map to the whole screen" in upd, upd)
frame = body(winios, "void winios_window_frame(HWND hwnd, int x, int y, int w, int h, int visible,")
check("a game-mode window keeps its client rect before the overlay decides whether it is drawn (a Metal window has no layer)",
      frame.find("g_client_rects[key] = [NSValue valueWithCGRect:CGRectMake(cx, cy, cw, ch)];")
      < frame.find("if (!winios_game_window_shown(key)) { winios_ptr_map_update(\"window frame\"); return; }")
      and 'if (g_comp_game) winios_ptr_map_update("window frame");' in frame, frame[:1500])
for where, start in (("a window shown or hidden", "void winios_window_visibility(HWND hwnd, int visible) {"),
                     ("a window removed", "static void winios_remove_layer(HWND hwnd) {"),
                     ("a swapchain window", "void winios_note_game_metal_hwnd(void *hwnd) {"),
                     ("new session", "void winios_session_reset(void) {")):
    check("the map is updated: %s" % where, 'winios_ptr_map_update("%s")' % where in body(winios, start), body(winios, start)[:600])
check("the map turns off with the game overlay", "winios_ptr_map_update(why);   /* build 646, [ptr-map]: no game overlay, no map */"
      in body(winios, "static void winios_drop_compositor(const char *why) {"))
check("Winios.h declares both calls for Swift",
      "int winios_pointer_map_apply(int *x, int *y);" in winios_h and "int winios_pointer_map_invert(int *x, int *y);" in winios_h)
rc = body(hw, "    private func renderCursor() {")
check("HardwareInput draws Windows' cursor back in the picture's space, clipping the pointer's own position in screen pixels",
      "_ = winios_pointer_map_apply(&sx, &sy)" in rc and "_ = winios_pointer_map_invert(&px, &py)" in rc
      and "DirectCursorOverlay.shared.render(visible: visible, x: px, y: py, guestW: g.w, guestH: g.h)" in rc, rc)
check("...and measures a warp in the picture's space too", "_ = winios_pointer_map_invert(&wx, &wy)" in hw)
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-ptr-map.py" in ci)

print("PASS: pointer map" if ok else "FAILED: pointer map")
sys.exit(0 if ok else 1)
