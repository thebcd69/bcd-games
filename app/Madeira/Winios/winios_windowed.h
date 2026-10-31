/* Is a game's window a Windows window with a title bar that is larger than BCD's screen? (round 625)
 *
 * SPDX-License-Identifier: GPL-3.0-only
 * Copyright 2026 thebcd69 (BCD)
 * BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
 *
 * The owner's item 29 (Discord cluster W: Fallout 4 at a high resolution, Resident Evil 7's 1920x1080 window on a 1408x648
 * screen, Metro Exodus's "4A Engine" caption, Dying Light): a game whose display mode is "Windowed" gets a window with a
 * title bar, and when that window is larger than the guest's virtual monitor part of the picture is off the screen. The
 * in-game answer is Fullscreen or Borderless at the screen's size. Winios.m keeps the style and the window size of each
 * swapchain window (fed from driver_ios.c's WindowPosChanged and SetWindowStyle hooks) and answers with this test; the
 * app shows its one-time hint from that (Library.swift watchWindowedHint). Pure C, no Apple or Wine headers, so
 * tests/host/check-windowed-hint.py compiles it on the host. */
#pragma once

#define WINIOS_WS_CAPTION 0x00C00000u   /* WS_BORDER | WS_DLGFRAME: a title bar */

/* 1 when `style` has a title bar and the window (`win_w` x `win_h`, its outer size with the frame) is wider or taller
 * than the screen (`scr_w` x `scr_h`); 0 for a borderless or fullscreen window, a window that fits, or a size not known. */
static inline int winios_window_too_big(unsigned style, int win_w, int win_h, int scr_w, int scr_h)
{
    if ((style & WINIOS_WS_CAPTION) != WINIOS_WS_CAPTION) return 0;
    if (win_w <= 0 || win_h <= 0 || scr_w <= 0 || scr_h <= 0) return 0;
    return win_w > scr_w || win_h > scr_h;
}
