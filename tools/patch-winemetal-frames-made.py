#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Frames made by frame generation, counted apart from the game's own (build 707; the in-game HUD's "15+15").

The HUD's rate is the app's frame ring (app/Madeira/BCDFrameRing.c), which winemetal's present counter feeds
(tools/patch-winemetal-frame-ring.py). Frame generation showed up in it one way or another: Lossless Scaling's generated frames
(DXMT's Direct3D 11 swap chain, the Direct3D 12 bridge) are presented through the same present call as the game's, so the HUD
read game and generated frames as one rate, with the generated frames' times in the frame-time graph and the lows; Apple's
interpolation and MetalFX frame interpolation present their pictures inside winemetal, so the HUD never saw them. Now every
frame made goes into the app's second ring, bcd_frame_made_push_n (a weak import, as the frame ring's), and the HUD writes it after
the game's rate:

  - MadeiraCtl op 31: the calling thread's next present is a frame made by frame generation. DXMT calls it right before the
    present of a generated frame on its encode thread (tools/patch-dxmt-frames-made.py), the Direct3D 12 bridge right before its
    own (madeira-d3d12, mad_lsfg_show). The present call then counts it into the ring of frames made instead of the frame ring;
    every other present is counted as before. A 32-bit caller's op 31 is forwarded too (it carries no pointer).
  - VideoToolbox's pictures (tools/patch-winemetal-vt-interp.py) and MetalFX's interpolated frame (tools/patch-winemetal-framegen-
    ios26.py) are counted where they are presented, only when they are shown (MetalFX's "measure only" shows none).

Nothing changes for a session without frame generation: no op 31 is sent, no picture is made, and every present goes into the frame
ring exactly as before. Native only (the winemetal unix side), not in the i386 farm key. After tools/patch-winemetal-vt-interp.py.
Idempotent; fails by name if an anchor moved, the file unchanged. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd frames made"

A_CADENCE = "static inline void madeira_log_present_cadence(const char *path, double after) {\n"
HELPERS = """/* """ + MARKER + """ (tools/patch-winemetal-frames-made.py): a frame made by frame generation and shown besides the game's own
 * goes into the app's ring of frames made (app/Madeira/BCDFrameRing.c), never into the frame ring, so the HUD writes "15+15" and the
 * frame times and lows stay the game's. MadeiraCtl op 31 marks the calling thread's next present as one (DXMT's Lossless Scaling, the
 * Direct3D 12 bridge); VideoToolbox's and MetalFX's pictures are counted where they are shown. */
static __thread int t_madeira_made_next;
static inline int madeira_made_next_take(void) {
  int v = t_madeira_made_next;
  t_madeira_made_next = 0;
  return v;
}
static void madeira_made_note(unsigned n) {
  extern void bcd_frame_made_push_n(uint32_t count) __attribute__((weak_import));
  if (n && bcd_frame_made_push_n)
    bcd_frame_made_push_n(n);
}

"""

A_RING = """    extern void bcd_frame_ring_push(void) __attribute__((weak_import));
    if (bcd_frame_ring_push)
      bcd_frame_ring_push();
"""
RING = """    extern void bcd_frame_ring_push(void) __attribute__((weak_import));
    if (madeira_made_next_take())   /* """ + MARKER + """: a frame frame generation made (op 31), not the game's */
      madeira_made_note(1);
    else if (bcd_frame_ring_push)
      bcd_frame_ring_push();
"""

A_CTL = "  case 6:     /* ml1136: requested fence-chain mode (0 = none) */\n"
CTL = """  case 31:    /* """ + MARKER + """ (tools/patch-winemetal-frames-made.py): the calling thread's next present is a frame made by frame
               * generation, counted apart from the game's (no pointer, no length) */
    t_madeira_made_next = 1;
    a->ret = 1;
    break;
""" + A_CTL

A_WOW64 = "  if (a && a->op == 7) return _madeira_ctl(args);\n"
WOW64 = A_WOW64 + "  if (a && a->op == 31) return _madeira_ctl(args);   /* " + MARKER + ": no pointer in it */\n"

A_VTI = "        atomic_fetch_add_explicit(&g_vti.generated, (unsigned long long)shown, memory_order_relaxed);\n"
VTI = A_VTI + "        madeira_made_note((unsigned)shown);   /* " + MARKER + ": VideoToolbox's pictures shown, for the HUD's \"+N\" */\n"

A_FG = "  g_fg.generated++;\n  if (g_fg.generated == 1 || (g_fg.generated % 600) == 0)\n"
FG = "  g_fg.generated++;\n  madeira_made_note(1);   /* " + MARKER + ": MetalFX's interpolated frame, shown */\n  if (g_fg.generated == 1 || (g_fg.generated % 600) == 0)\n"

# (name, anchor, new text, the text that says the code is in this tree: None = always). An older dxmt checkout (the base some host
# checks patch: tests/host/dxmt_base.py) has no VideoToolbox experiment (tools/patch-winemetal-vt-interp.py leaves itself out there)
# and may have no MetalFX frame generation: their edits are made where their code is, and an anchor missing in code that is there
# fails by name as any other.
PAIRS = (
    ("the present counter", A_CADENCE, HELPERS + A_CADENCE, None),
    ("the frame ring's call in the present counter", A_RING, RING, None),
    ("MadeiraCtl's op 6", A_CTL, CTL, None),
    ("the 32-bit MadeiraCtl's op 7", A_WOW64, WOW64, None),
    ("VideoToolbox's shown pictures", A_VTI, VTI, "madeira-bcd: vt-interp"),
    ("MetalFX's interpolated frame", A_FG, FG, "static int madeira_fg_present_inner("),
)


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("winemetal_unix.c: frames made already patched")
        return 0
    edits = [(name, old, new) for name, old, new, needs in PAIRS if needs is None or needs in s]
    left = [name for name, _, _, needs in PAIRS if needs is not None and needs not in s]
    for name, old, _ in edits:
        if s.count(old) != 1:
            print(f"::error::patch-winemetal-frames-made: {name}: anchor found {s.count(old)} times (want 1) in {PATH}")
            return 1
    for _, old, new in edits:
        s = s.replace(old, new)
    PATH.write_text(s)
    print("winemetal_unix.c: frames made patched (" + ", ".join(name for name, _, _ in edits) + ")"
          + (" -- not in this tree, left out: " + ", ".join(left) if left else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
