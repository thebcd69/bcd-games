#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT marks the presents of Lossless Scaling's generated frames (build 707; the in-game HUD's "15+15").

DXMT's Direct3D 11 swap chain presents each frame Lossless Scaling generated (tools/patch-dxmt-lsfg.py) through the same present
as the game's own frame, so the HUD counted them as the game's: its rate was the sum and its frame times and lows were a mix. A
present now carries whether it is a generated frame (PresentData::madeira_made, set by the two presents of generated frames in
Present1 only), and the encode thread, which calls the present when it flushes the encoders, tells winemetal right before it
(MadeiraCtl op 31, tools/patch-winemetal-frames-made.py): that present goes into the app's ring of frames made instead of the frame
ring, and the HUD writes it after the game's rate. Every other present, of every game, is encoded exactly as before (the new
argument defaults to false). The first marked present says so once in the log ("[frames-made] madeira-bcd", the marker
tools/build-d3d11-dll.sh looks for in d3d11-src.dll).

Applied in the tree (the i386 d3d11.dll) and to d3d11-src.dll's copy (BCD_PATCHES), after tools/patch-dxmt-lsfg.py. Idempotent;
fails by name if an anchor moved, every file unchanged. Run from the repository root.
"""
import pathlib
import sys

MARKER = "madeira-bcd frames made"
HPP = pathlib.Path("dxmt/src/dxmt/dxmt_context.hpp")
CPP = pathlib.Path("dxmt/src/dxmt/dxmt_context.cpp")
SWAP = pathlib.Path("dxmt/src/d3d11/d3d11_swapchain.cpp")

EDITS = {
    HPP: (
        ("PresentData", """struct PresentData : EncoderData {
  WMT::Reference<WMT::Texture> backbuffer;
  Rc<Presenter> presenter;
  double after;
  DXMTPresentMetadata metadata;
};
""", """struct PresentData : EncoderData {
  WMT::Reference<WMT::Texture> backbuffer;
  Rc<Presenter> presenter;
  double after;
  DXMTPresentMetadata metadata;
  bool madeira_made = false;   /* """ + MARKER + """ (tools/patch-dxmt-frames-made.py): a frame frame generation made, not the game's */
};
"""),
        ("present()'s declaration", "  void present(Rc<Texture> &texture, Rc<Presenter> &presenter, double after, DXMTPresentMetadata metadata);\n",
         "  void present(Rc<Texture> &texture, Rc<Presenter> &presenter, double after, DXMTPresentMetadata metadata,\n"
         "               bool madeira_made = false);   /* " + MARKER + " */\n"),
    ),
    CPP: (
        ("present()'s definition",
         "ArgumentEncodingContext::present(Rc<Texture> &texture, Rc<Presenter> &presenter, double after, DXMTPresentMetadata metadata) {\n",
         "ArgumentEncodingContext::present(Rc<Texture> &texture, Rc<Presenter> &presenter, double after, DXMTPresentMetadata metadata,\n"
         "                                 bool madeira_made) {\n"),
        ("present()'s fields", "  encoder_info->after = after;\n  encoder_info->metadata = metadata;\n",
         "  encoder_info->after = after;\n  encoder_info->metadata = metadata;\n  encoder_info->madeira_made = madeira_made;   /* " + MARKER + " */\n"),
        ("the present at the flush", """      if (drawable)
        cmdbuf.presentDrawable(drawable);
      data->~PresentData();
""", """      if (drawable && data->madeira_made) {
        /* """ + MARKER + """ (tools/patch-dxmt-frames-made.py): this present is a frame frame generation made; winemetal counts it
         * into the app's ring of frames made instead of the frame ring (MadeiraCtl op 31, on this thread, right before the present) */
        struct madeira_ctl_args made_args = {};
        made_args.op = 31;
        MadeiraCtl(&made_args);
        static bool made_said = false;
        if (!made_said) {
          made_said = true;
          WARN("[frames-made] madeira-bcd: frames made by frame generation are counted apart from the game's (the HUD's \\"+N\\")");
        }
      }
      if (drawable)
        cmdbuf.presentDrawable(drawable);
      data->~PresentData();
"""),
    ),
    SWAP: (
        ("the enlarged generated frame's present", "            ctx.present(dst, presenter, vsync_duration, meta);\n",
         "            ctx.present(dst, presenter, vsync_duration, meta, true);   /* " + MARKER + ": a generated frame */\n"),
        ("the generated frame's present", "            ctx.present(src, presenter, vsync_duration, meta);\n",
         "            ctx.present(src, presenter, vsync_duration, meta, true);   /* " + MARKER + ": a generated frame */\n"),
    ),
}


def main():
    texts = {p: p.read_text() for p in EDITS}
    done = [p for p, s in texts.items() if MARKER in s]
    if len(done) == len(EDITS):
        print("dxmt: frames made already patched")
        return 0
    if done:
        print(f"::error::patch-dxmt-frames-made: patched in part ({', '.join(map(str, done))}): put the files back first")
        return 1
    for p, edits in EDITS.items():
        for name, old, _ in edits:
            if texts[p].count(old) != 1:
                print(f"::error::patch-dxmt-frames-made: {name}: anchor found {texts[p].count(old)} times (want 1) in {p}")
                return 1
    for p, edits in EDITS.items():
        s = texts[p]
        for _, old, new in edits:
            s = s.replace(old, new)
        p.write_text(s)
    print("dxmt: frames made patched (PresentData::madeira_made, op 31 before a generated frame's present, Lossless Scaling's two presents)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
