#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The frames frame generation made, counted apart from the game's (build 707): the in-game HUD's "15+15".

Every frame generator BCD has shows frames of its own besides the game's: Apple's VideoToolbox interpolation (winemetal's for a
DirectX game, KosmicKrisp's for a Vulkan one), Lossless Scaling (DXMT's Direct3D 11 swap chain, the Direct3D 12 bridge,
KosmicKrisp's present) and MetalFX frame interpolation (winemetal). Checks:
  1. the app: a second ring for them (BCDFrameRing.c: bcd_frame_made_push_n, exported by name), the HUD reads it next to the frame
     ring and writes its rate after the game's ("15+15"; the ring's and the rules' own tests are tests/host/check-bcd-hud.py's), the
     [hud] log line gets made=, the [session] line made= (the frames made that session; frames, avg and the lows stay the game's);
  2. winemetal (tools/patch-winemetal-frames-made.py): MadeiraCtl op 31 marks only the calling thread's next present, which then goes
     into the ring of frames made instead of the frame ring; VideoToolbox's and MetalFX's pictures are counted where they are shown;
     the 32-bit forwarder passes op 31 (no pointer); with a DXMT tree here (DXMT_SRC_DIR or ./dxmt) the patch is applied to a copy of
     its winemetal_unix.c, twice, and its blocks compile;
  3. DXMT (tools/patch-dxmt-frames-made.py): only Lossless Scaling's two presents of generated frames carry the mark, the encode
     thread sends op 31 right before such a present and nowhere else; d3d11-src.dll gets it (BCD_PATCHES, its marker);
  4. the Direct3D 12 bridge sends op 31 right before the present of each generated frame and nowhere else;
  5. KosmicKrisp (playport patch 0036): every picture shown goes to the app's ring, found in the process (its runtime count is
     tests/host/check-kk-vt-interp.py's and tests/host/check-kk-lsfg.py's);
  6. the workflow: the steps, in an order that works.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    ok = ok and bool(cond)


def code(text):
    """Without comments (C and Swift)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def block(text, start):
    i = text.index(start)
    depth, j = 0, text.index("{", i)
    while True:
        c = text[j]
        depth += c == "{"
        depth -= c == "}"
        j += 1
        if depth == 0:
            return text[i:j]


# 1. the app
ring_h, ring_c = (APP / "BCDFrameRing.h").read_text(), (APP / "BCDFrameRing.c").read_text()
check("the ring of frames made: its own slots and head, the same drain as the frame ring's, nothing of the game's statistics",
      "static _Atomic uint64_t g_made_ring[RING_SIZE];" in ring_c and "return ring_drain(g_made_ring, &g_made_head, reader, out, max, lost);" in ring_c
      and "return ring_drain(g_ring, &g_head, reader, out, max, lost);" in ring_c
      and all(w not in block(ring_c, "BCD_FR_EXPORT void bcd_frame_made_push_at(uint64_t t) {") for w in ("g_hist", "g_prev", "g_head", "bcd_pause_gate_pass")))
check("bcd_frame_made_push_n is exported by name (KosmicKrisp finds it in the process) and kept by the linker",
      "#define BCD_FR_EXPORT __attribute__((used, visibility(\"default\")))" in ring_c and "BCD_FR_EXPORT void bcd_frame_made_push_n(uint32_t count) {" in ring_c
      and "void bcd_frame_made_push_n(uint32_t count);" in ring_h)
exports = [l.strip() for l in (APP / "madeira-exports.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
check("the app exports it (its exported-symbols list puts it in the export trie; build 707's IPA lacked it, so KosmicKrisp's dlsym found nothing)",
      "_bcd_frame_made_push_n" in exports)
live = code((APP / "BCDHUDLive.swift").read_text())
check("the HUD drains both rings, keeps the frames made apart and passes their rate on",
      "bcd_frame_made_drain(&madeReader, &buffer, UInt32(buffer.count), &lost)" in live and "d.madeFps = BCDFrameMath.fps(made, now: now)" in live
      and "madeReader = bcd_frame_made_reader_start()" in block(live, "func start() {") and "made.removeAll()" in block(live, "func stop() {"))
check("the HUD's warning and calm colours go by every frame shown; the [hud] line gets made=",
      "let shown = BCDHUDRules.shownFps(d)" in live and "BCDHUDRules.rateTone(d)" in live
      and 'if d.madeFps >= 0.5 { parts.append(String(format: "made=%.0f", d.madeFps)) }' in live)
core = code((APP / "BCDHUDCore.swift").read_text())
check("every layout writes the rate through BCDHUDRules.rate (\"15+15\"), none of them the game's number alone",
      core.count("BCDHUDRules.rate(d)") >= 6 and 'String(format: "%.0f FPS", d.fps)' not in core and "fpsTone(d.fps," not in core)
session = (APP / "BCDSessionCore.swift").read_text()
check("the [session] line: made= before the game's name, only when frames were made; the recorder counts the session's",
      'if made > 0 { parts.append("made=\\(made)") }' in session
      and session.index('parts.append("made=') < session.index('parts.append("game="')
      and "madeAtStart = bcd_frame_made_count()" in (APP / "BCDSessionRecorderLive.swift").read_text())

# 2. winemetal
wm_patch = (R / "tools/patch-winemetal-frames-made.py").read_text()
ns = {}
exec(compile(wm_patch.split("\ndef main():", 1)[0], "patch-winemetal-frames-made.py", "exec"), ns)
check("op 31 sets the calling thread's mark and nothing else",
      "    t_madeira_made_next = 1;\n    a->ret = 1;\n    break;\n" in ns["CTL"] and "static __thread int t_madeira_made_next;" in ns["HELPERS"])
check("the present counter: a marked present goes into the ring of frames made, every other one into the frame ring as before",
      ns["RING"].replace(ns["A_RING"].split("\n")[0] + "\n", "") .startswith("    if (madeira_made_next_take())")
      and "    else if (bcd_frame_ring_push)\n      bcd_frame_ring_push();\n" in ns["RING"] and "madeira_made_note(1);" in ns["RING"])
check("VideoToolbox's pictures are counted where they are counted as shown, MetalFX's after its interpolated frame was presented",
      ns["VTI"].startswith(ns["A_VTI"]) and "madeira_made_note((unsigned)shown);" in ns["VTI"]
      and "madeira_made_note(1);" in ns["FG"] and ns["FG"].index("g_fg.generated++;") < ns["FG"].index("madeira_made_note(1);"))
check("the 32-bit forwarder passes op 31 (it carries no pointer), and only it besides op 7",
      ns["WOW64"] == ns["A_WOW64"] + "  if (a && a->op == 31) return _madeira_ctl(args);   /* madeira-bcd frames made: no pointer in it */\n")
check("in this repository's dxmt (its pin) VideoToolbox's and MetalFX's edits are required; an older checkout without their code leaves them out",
      [needs for *_, needs in ns["PAIRS"]] == [None, None, None, None, "madeira-bcd: vt-interp", "static int madeira_fg_present_inner("]
      and "fails by name" in wm_patch)
check("the call into the app is a weak import, as the frame ring's",
      "extern void bcd_frame_made_push_n(uint32_t count) __attribute__((weak_import));" in ns["HELPERS"] and "if (n && bcd_frame_made_push_n)" in ns["HELPERS"])
src = Path(os.environ["DXMT_SRC_DIR"]) if os.environ.get("DXMT_SRC_DIR") else R / "dxmt"
wm = src / "src/winemetal/unix/winemetal_unix.c"
if not wm.exists():
    print(f"SKIP: no DXMT tree with winemetal_unix.c at {src}: the patch is not applied here (CI applies it, and its own anchors fail by name)")
elif "madeira-bcd frame ring" not in wm.read_text(errors="replace"):
    print(f"SKIP: {wm} is not patched up to here (no frame ring yet): CI runs this check right after the VideoToolbox patch; "
          "tests/host/check-dxmt-patch-chain.py applies the whole chain")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-made-") as t:
        tdir = Path(t)
        (tdir / "dxmt/src/winemetal/unix").mkdir(parents=True)
        shutil.copy(wm, tdir / "dxmt/src/winemetal/unix/winemetal_unix.c")
        before = (tdir / "dxmt/src/winemetal/unix/winemetal_unix.c").read_text()
        if ns["MARKER"] in before:
            print("note: the tree's winemetal_unix.c is patched already")
        first = subprocess.run([sys.executable, str(R / "tools/patch-winemetal-frames-made.py")], cwd=tdir, capture_output=True, text=True)
        second = subprocess.run([sys.executable, str(R / "tools/patch-winemetal-frames-made.py")], cwd=tdir, capture_output=True, text=True)
        out = (tdir / "dxmt/src/winemetal/unix/winemetal_unix.c").read_text()
        check("the patch applies to this tree's winemetal_unix.c, then says it is patched", first.returncode == 0 and second.returncode == 0
              and "already patched" in second.stdout, first.stdout + first.stderr + second.stdout)
        if first.returncode == 0:
            fn = block(out, "static inline void madeira_log_present_cadence(const char *path, double after) {")
            check("...the frame ring's call is in the present counter, behind the mark, once in the file",
                  out.count("bcd_frame_ring_push();") == 1 and "if (madeira_made_next_take())" in fn and out.index("t_madeira_made_next") < out.index(fn))
            snippet = ns["HELPERS"] + "void f(void) {\n  {\n" + ns["RING"] + "  }\n}\n"
            (tdir / "s.c").write_text("#include <stdint.h>\n" + snippet)
            cc = subprocess.run(["cc", "-fsyntax-only", "-Wall", "-Werror", "-Wno-unused-function", str(tdir / "s.c")], capture_output=True, text=True)
            check("...its blocks compile as C", cc.returncode == 0, cc.stderr)

# 3. DXMT
dx_patch = (R / "tools/patch-dxmt-frames-made.py").read_text()
dns = {}
exec(compile(dx_patch.split("\ndef main():", 1)[0], "patch-dxmt-frames-made.py", "exec"), dns)
swap = dict((n, (o, w)) for n, o, w in dns["EDITS"][dns["SWAP"]])
check("only Lossless Scaling's two presents of generated frames pass the mark (the game's own present is not touched)",
      all(", true);" in w and "ctx.present(" in o and ", meta);" in o for o, w in swap.values()) and len(swap) == 2
      and "state.metadata" not in "".join(o for o, _ in swap.values()))
cpp = dict((n, (o, w)) for n, o, w in dns["EDITS"][dns["CPP"]])
flush_new = cpp["the present at the flush"][1]
check("the encode thread sends op 31 right before the present of a marked frame, and only of a drawable it presents",
      flush_new.index("if (drawable && data->madeira_made) {") < flush_new.index("made_args.op = 31;") < flush_new.index("MadeiraCtl(&made_args);")
      < flush_new.index("      if (drawable)\n        cmdbuf.presentDrawable(drawable);"))
hpp = dict((n, (o, w)) for n, o, w in dns["EDITS"][dns["HPP"]])
check("the mark defaults to false: every other present is encoded as before",
      "bool madeira_made = false;" in hpp["PresentData"][1] and "bool madeira_made = false);" in hpp["present()'s declaration"][1])
lsfg = (R / "tools/patch-dxmt-lsfg.py").read_text()
check("the two presents it marks are the ones tools/patch-dxmt-lsfg.py writes, once each",
      all(lsfg.count(o) == 1 for o, _ in swap.values()))
b11 = (R / "tools/build-d3d11-dll.sh").read_text()
patches = re.findall(r'"(patch-[\w.-]+\.py)" "', b11[b11.index("BCD_PATCHES=("):])
check("d3d11-src.dll gets it after the Lossless Scaling patch, before the last one, with a marker its code writes",
      "patch-dxmt-frames-made.py" in patches and patches.index("patch-dxmt-lsfg.py") < patches.index("patch-dxmt-frames-made.py") < len(patches) - 1
      and '"patch-dxmt-frames-made.py" "[frames-made] madeira-bcd: "' in b11 and 'WARN("[frames-made] madeira-bcd: ' in flush_new)

# 4. the Direct3D 12 bridge
d12 = code((R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text())
show = block(d12, "static void mad_lsfg_show(struct mad_swapchain *s, struct lsfg_bridge_frame *lf, UINT n, UINT w, UINT h) {")
check("the Direct3D 12 bridge sends op 31 right before each generated frame's present, and nowhere else",
      d12.count("made.op = 31;") == 1 and show.index("made.op = 31;") < show.index("MadeiraCtl(&made);") < show.index("MTLCommandBuffer_presentDrawable(cb, dr);"))

# 5. KosmicKrisp
patch = (R / "build/mesa-ios/playport-patches/0036-wsi-metal-apple-frame-interpolation-MADEIRA_VT_INTERP.patch").read_text()
check("KosmicKrisp: every picture shown (VideoToolbox's or Lossless Scaling's) goes to the app's ring, found in the process once",
      '+   dispatch_once(&once, ^{ push = (void (*)(uint32_t))dlsym(RTLD_DEFAULT, "bcd_frame_made_push_n"); });' in patch
      and "+         kk_vti_made((unsigned)shown);" in patch
      and patch.index("+         atomic_fetch_add_explicit(&g_kk_vti.generated, (unsigned long long)shown, memory_order_relaxed);")
      < patch.index("+         kk_vti_made((unsigned)shown);"))

# 6. the workflow
wf = (R / ".github/workflows/build-ipa.yml").read_text()
i = lambda s: wf.index(s)
check("CI: DXMT's mark after the Lossless Scaling patch and before the i386 farm's key; winemetal's after VideoToolbox's, this check first",
      i("run: python3 tools/patch-dxmt-lsfg.py") < i("run: python3 tools/patch-dxmt-frames-made.py") < i("key=i386-farm-")
      and i("run: python3 tools/patch-winemetal-vt-interp.py") < i("run: python3 tests/host/check-frames-made.py")
      < i("run: python3 tools/patch-winemetal-frames-made.py"))

print("PASS: frames made" if ok else "FAIL: frames made")
sys.exit(0 if ok else 1)
