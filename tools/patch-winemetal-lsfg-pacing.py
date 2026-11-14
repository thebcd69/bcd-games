#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The frame-rate limit while Lossless Scaling frame generation adds frames (tools/patch-dxmt-lsfg.py).

DXMT presents every generated picture like a frame of the game, so each of them comes through winemetal's present call, where the
60, 40 and 30 FPS limits are applied (a minimum time between two presents). Left alone, a game limited to 30 with one frame added
would show 15 of its own frames and 15 generated ones a second.

The app tells winemetal how many frames are shown per game frame (madeira_lsfg_set_shown: 2, 3 or 4; 1 = none, the default). While
that number is above 1 the present call takes the limit times that number: 30 with one added runs as the 60 limit, and a product at
or above the screen's top rate (60 with one added on a 120 Hz screen, 30 with three) or one the present call has no limit for (80,
90) runs as "the screen's top rate", where the screen itself and the two or more presents per frame hold the game back. "No limit"
and "the screen's top rate" stay what they are.

With the number at 1, which it is unless the app raised it for a session with the game's `lossless-scaling` line, the present call
reads its mode exactly as before. Runs after tools/patch-winemetal-present-fx.py. Idempotent; fails by name if an anchor moved. Run
from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: lsfg pacing"

A_THUNK = """static NTSTATUS
_MTLCommandBuffer_presentDrawable(void *obj) {
"""
BLOCK = r'''/* madeira-bcd: lsfg pacing (tools/patch-winemetal-lsfg-pacing.py). While Lossless Scaling frame generation adds frames, each of
 * them comes through the present call below, so the frame-rate limit counts every frame shown: the app says how many are shown per
 * game frame, and the limit used is the game's limit times that number. 1 (the default): nothing changes. */
static _Atomic int g_lsfg_shown = 1;
void madeira_lsfg_set_shown(int frames_per_game_frame) {
  int n = frames_per_game_frame < 1 ? 1 : frames_per_game_frame > 4 ? 4 : frames_per_game_frame;
  if (atomic_exchange_explicit(&g_lsfg_shown, n, memory_order_relaxed) != n)
    dprintf(STDERR_FILENO, "[lsfg] madeira-bcd pacing: %d frame(s) shown per game frame; the frame-rate limit %s\n", n,
            n > 1 ? "counts every one of them" : "is the game's own again");
}
/* lsfgp-pure-begin: plain C (tests/host/check-lsfg.py compiles it on the host) */
/* The present call's mode for the game's mode (1 = 60, 3 = 30, 4 = 40, 0 = the screen's top rate, 2 = no limit) while `shown`
 * frames are shown per game frame: the limit times `shown` as a mode of its own (60 = 1), or the screen's top rate when the product
 * reaches it or is none of the limits the call has. panel_hz: the screen's top rate, 0 when not known. */
static int madeira_lsfg_present_mode(int mode, int shown, int panel_hz) {
  int limit, total;
  if (shown <= 1) return mode;
  limit = mode == 1 ? 60 : mode == 3 ? 30 : mode == 4 ? 40 : 0;
  if (!limit) return mode;
  total = limit * shown;
  if (panel_hz > 0 && total >= panel_hz) return 0;
  return total == 60 ? 1 : 0;
}
/* lsfgp-pure-end */

'''

A_MODE = "  int mode = g_madeira_vsync_mode;\n"
MODE = ("  int mode = madeira_lsfg_present_mode(g_madeira_vsync_mode, atomic_load_explicit(&g_lsfg_shown, memory_order_relaxed),\n"
        "                                       g_madeira_panel_hz);   /* " + MARKER + ": the game's own mode unless frames are being added */\n")

PAIRS = (
    ("present thunk", A_THUNK, BLOCK + A_THUNK),
    ("present mode", A_MODE, MODE),
)


def main():
    if not PATH.exists():
        print(f"::error::{PATH} not found (run from the repository root)")
        return 1
    s = PATH.read_text()
    if MARKER in s:
        print(f"{PATH}: lsfg pacing already patched")
        return 0
    for name, anchor, _ in PAIRS:
        if s.count(anchor) != 1:
            print(f"::error::{PATH}: {name} anchor found {s.count(anchor)} times, not once -- dxmt moved, review tools/patch-winemetal-lsfg-pacing.py")
            return 1
    for _, anchor, new in PAIRS:
        s = s.replace(anchor, new, 1)
    PATH.write_text(s)
    print(f"{PATH}: lsfg pacing patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
