/* Points on a windowed game's picture go into its window (library sessions; Winios.m, [ptr-map])
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * WHY THIS EXISTS
 * ---------------
 * In a library session the game view shows the picture of the window the game's swapchain presents into, scaled to the view. A finger,
 * the iPad pointer or the Pencil was mapped to the whole guest screen, as if that window covered it. A game that starts in a window
 * does not: Slime Rancher 2 (a tester's log of 2026-11-07, build 634, iPad13,16) opened a 1158x832 window at (285,184) on a 1728x1200
 * screen, so every point landed somewhere else in Windows than on the picture, a click at the picture's right edge left the window, and
 * the game's menu took no input although every click and key reached its window. Here a point in the picture's space (the guest
 * screen's size, which the app's mapping already gives) is moved into the window's client rect, in screen pixels, and back for drawing a
 * cursor Windows placed. A window that covers the screen exactly maps to itself, so a full-screen game sees no change.
 *
 * Free of Wine and UIKit types so tests/host/check-ptr-map.py compiles and runs it on the host.
 */
#ifndef WINIOS_PTR_MAP_H
#define WINIOS_PTR_MAP_H

/* 1 when a client rect (cx, cy, cw, ch) on a sw x sh screen is one points are moved into: a real size (64 pixels or more each way), not
 * the whole screen itself (nothing to move), and at least partly on the screen (a window parked off the screen is not shown). */
static inline int winios_ptr_map_wanted( int sw, int sh, int cx, int cy, int cw, int ch )
{
    if (sw <= 0 || sh <= 0 || cw < 64 || ch < 64) return 0;
    if (cx == 0 && cy == 0 && cw == sw && ch == sh) return 0;
    if (cx >= sw || cy >= sh || cx + cw <= 0 || cy + ch <= 0) return 0;
    return 1;
}

/* A point in the picture's space ([0, sw) x [0, sh)) to screen pixels inside the client rect, rounded to the nearest pixel and kept
 * inside the rect. */
static inline void winios_ptr_map_apply( int *x, int *y, int sw, int sh, int cx, int cy, int cw, int ch )
{
    long long nx = cx + ((long long)*x * cw + sw / 2) / sw;
    long long ny = cy + ((long long)*y * ch + sh / 2) / sh;

    if (nx < cx) nx = cx;
    if (nx > cx + cw - 1) nx = cx + cw - 1;
    if (ny < cy) ny = cy;
    if (ny > cy + ch - 1) ny = cy + ch - 1;
    *x = (int)nx;
    *y = (int)ny;
}

/* The way back: screen pixels (a cursor Windows placed) to the picture's space, kept inside [0, sw) x [0, sh). */
static inline void winios_ptr_map_invert( int *x, int *y, int sw, int sh, int cx, int cy, int cw, int ch )
{
    long long px = ((long long)(*x - cx) * sw + cw / 2) / cw;
    long long py = ((long long)(*y - cy) * sh + ch / 2) / ch;

    if (*x < cx) px = 0;
    if (*y < cy) py = 0;
    if (px > sw - 1) px = sw - 1;
    if (py > sh - 1) py = sh - 1;
    *x = (int)px;
    *y = (int)py;
}

/* Four 16-bit fields in one word, so a reader on another thread never sees half of an update: x and y signed, w and h unsigned. */
static inline unsigned long long winios_ptr_map_pack( int x, int y, int w, int h )
{
    return ((unsigned long long)(unsigned short)(short)x << 48) | ((unsigned long long)(unsigned short)(short)y << 32)
         | ((unsigned long long)(unsigned short)w << 16) | (unsigned long long)(unsigned short)h;
}

static inline void winios_ptr_map_unpack( unsigned long long v, int *x, int *y, int *w, int *h )
{
    *x = (short)(unsigned short)(v >> 48);
    *y = (short)(unsigned short)(v >> 32);
    *w = (unsigned short)(v >> 16);
    *h = (unsigned short)v;
}

#endif /* WINIOS_PTR_MAP_H */
