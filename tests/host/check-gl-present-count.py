#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A GL game's frames count as presents (OpenGL, build 632; tools/patch-winemetal-gl-present.py, app/Madeira/Winios/WiniosGL.m).

madeira_get_present_count() (winemetal) is what the HUD, the launch checks that wait for a first frame, the Dock's starting screen, the
pause logic and the engine's census read. The winios OpenGL presenter does not go through winemetal, so its frames are added to that
getter: a weak import of madeira_gl_present_count() (0 without a GL game, so the getter answers what it did). WiniosGL.m also stamps each
GL present into the frame-time ring (BCDFrameRing.c), like a DXMT present.

Checks:
  1. the patch applies once to the pinned winemetal_unix.c (a second run only says so) and fails by name when its anchor is missing;
  2. the getter, compiled on the host: with madeira_gl_present_count defined it adds its answer, without it (a link without WiniosGL.m) it
     answers the count it had;
  3. WiniosGL.m: the count and the ring stamp come from the same place (present_slot, after the commit), the madeira_frame_count and
     madeira_frame_mark of Madeira's own overlay are not in BCD's copy;
  4. CI: check before patch, the patch after the frame-ring patch, native only.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-1500:]) if detail and not cond else ""))
    ok = ok and bool(cond)


def pinned_source():
    p = R / "dxmt/src/winemetal/unix/winemetal_unix.c"
    if p.is_file():
        return p.read_text()
    if os.environ.get("WINEMETAL_SRC"):
        return Path(os.environ["WINEMETAL_SRC"]).read_text()
    # a developer machine: the fork's local bare copy at the commit the tree pins
    bare = Path.home() / "madeira-local/kaynak/dxmt.git"
    if bare.is_dir():
        pin = subprocess.run(["git", "-C", str(R), "ls-tree", "HEAD", "dxmt"], capture_output=True, text=True).stdout.split()
        if len(pin) >= 3:
            r = subprocess.run(["git", "-C", str(bare), "show", f"{pin[2]}:src/winemetal/unix/winemetal_unix.c"], capture_output=True, text=True)
            if r.returncode == 0:
                return r.stdout
    return None


tool = R / "tools/patch-winemetal-gl-present.py"
text = tool.read_text()
check("BCD's header on the patch script", text.startswith("#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-3.0-only\n# Copyright 2026 thebcd69 (BCD)"))

wm = pinned_source()
if wm is None:
    print("SKIP: no dxmt sources for the patch (check out the submodule, or set WINEMETAL_SRC)")
else:
    with tempfile.TemporaryDirectory() as t:
        dst = Path(t) / "dxmt/src/winemetal/unix/winemetal_unix.c"
        dst.parent.mkdir(parents=True)
        dst.write_text(wm)
        r1 = subprocess.run([sys.executable, str(tool)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(tool)], cwd=t, capture_output=True, text=True)
        patched = dst.read_text()
        check("the patch applies once; a second run only says it is there",
              r1.returncode == 0 and "GL presents counted" in r1.stdout and r2.returncode == 0 and "already counted" in r2.stdout
              and patched.count("madeira-bcd gl present") == 1, (r1.stdout + r1.stderr + r2.stdout + r2.stderr))
        getter = patched[patched.index("uint64_t madeira_get_present_count(void) {"):]
        getter = getter[:getter.index("\n}\n") + 3]
        check("the getter keeps its own count first and adds the weak import's answer",
              "atomic_load_explicit(&g_madeira_present_count, memory_order_relaxed)" in getter
              and "extern uint64_t madeira_gl_present_count(void) __attribute__((weak_import));" in getter
              and "if (madeira_gl_present_count)" in getter and "n += madeira_gl_present_count();" in getter and getter.rstrip().endswith("return n;\n}"), getter)
        # nothing but the getter changed
        before = wm.replace("\r", "")
        after = patched.replace("\r", "").replace(getter, wm[wm.index("uint64_t madeira_get_present_count(void) {"):][:wm[wm.index("uint64_t madeira_get_present_count(void) {"):].index("\n}\n") + 3])
        check("no other line of winemetal_unix.c changed", before == after)
        # compiled
        harness = Path(t) / "h.c"
        harness.write_text("#include <stdatomic.h>\n#include <stdint.h>\n#include <stdio.h>\nstatic _Atomic uint64_t g_madeira_present_count = 100;\n"
                           + getter + "\n#ifdef WITH_GL\nuint64_t madeira_gl_present_count(void) { return 7; }\n#endif\n"
                           "int main(void) { printf(\"%llu\\n\", (unsigned long long)madeira_get_present_count()); return 0; }\n")
        outs = {}
        for name, extra in (("with", ["-DWITH_GL"]), ("without", ["-Wl,-U,_madeira_gl_present_count"])):
            exe = Path(t) / f"h-{name}"
            cc = subprocess.run(["cc", "-std=gnu11", "-Wall", "-o", str(exe), str(harness)] + extra, capture_output=True, text=True)
            outs[name] = (subprocess.run([str(exe)], capture_output=True, text=True).stdout.strip() if cc.returncode == 0 else cc.stderr)
        check("with WiniosGL's count the getter adds it (100 + 7)", outs["with"] == "107", outs)
        check("without it (a link without WiniosGL.m) the getter answers its own count (100)", outs["without"] == "100", outs)
        bad = Path(t) / "bad/dxmt/src/winemetal/unix"
        bad.mkdir(parents=True)
        (bad / "winemetal_unix.c").write_text(wm.replace("uint64_t madeira_get_present_count(void) {", "uint64_t madeira_get_present_count2(void) {"))
        r = subprocess.run([sys.executable, str(tool)], cwd=str(Path(t) / "bad"), capture_output=True, text=True)
        check("an anchor that moved fails by name", r.returncode != 0 and "anchor found 0 times" in (r.stdout + r.stderr), r.stdout + r.stderr)

gl = (R / "app/Madeira/Winios/WiniosGL.m").read_text()
ps = gl[gl.index("static BOOL present_slot("):]
ps = ps[:ps.index("\n}\n") + 3]
check("WiniosGL.m exports the GL present count and stamps the frame ring where it counts",
      "uint64_t madeira_gl_present_count(void) {" in gl and "extern void bcd_frame_ring_push(void);" in gl
      and ps.index("[cb commit];") < ps.index("atomic_fetch_add_explicit(&gl_present_count") < ps.index("bcd_frame_ring_push();"), ps)
check("Madeira's overlay hooks are not in BCD's copy (madeira_frame_count, madeira_frame_mark)",
      "madeira_frame_count" not in gl and "madeira_frame_mark" not in gl)
check("the Swift side still reads madeira_get_present_count (one counter, no rename)",
      "madeira_get_present_count" in (R / "app/Madeira/Madeira-Bridging-Header.h").read_text()
      and "madeira_frame_count" not in (R / "app/Madeira/Madeira-Bridging-Header.h").read_text())

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI: the check, then the patch, after the frame-ring patch",
      wf.index("run: python3 tools/patch-winemetal-frame-ring.py") < wf.index("- name: Check winemetal GL presents")
      < wf.index("run: python3 tools/patch-winemetal-gl-present.py") and "python3 tests/host/check-gl-present-count.py" in wf)

print("PASS: GL present count" if ok else "FAIL: GL present count")
sys.exit(0 if ok else 1)
