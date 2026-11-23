#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-gl-null-sync.py: get_unix_sync(NULL) is NULL (round 707, M3 S2; DOOM 3: BFG Edition 705H3D); no Wine runs.

The patch applies once to the pinned unix_private.h (the Wine tree of this checkout, WINE_SRC or ~/madeira-bcd/wine; skipped without one),
a moved anchor is refused by name and leaves the file alone, and the patched function, compiled on the host with a model struct, maps NULL
to NULL and any other handle to its unix_handle. CI applies it before the unix build and runs this check.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check and applies the patch before the unix build",
      "python3 tests/host/check-gl-null-sync.py" in wf and "python3 tools/patch-wine-gl-null-sync.py wine" in wf
      and wf.index("python3 tools/patch-wine-gl-null-sync.py wine") < wf.index("bash build/ntdll-unix/build.sh"))
wine = next((w for w in (Path(os.environ["WINE_SRC"]) if os.environ.get("WINE_SRC") else None, root / "wine", Path.home() / "madeira-bcd/wine")
             if w and (w / "dlls/opengl32/unix_private.h").is_file()), None)
if not wine:
    print("SKIP: no Wine tree (WINE_SRC)")
else:
    rel = "dlls/opengl32/unix_private.h"
    src = subprocess.run(["git", "-C", str(wine), "show", "HEAD:" + rel], capture_output=True, text=True).stdout or (wine / rel).read_text()
    tmp = Path(tempfile.mkdtemp(prefix="madeira-gl-sync-"))
    (tmp / "dlls/opengl32").mkdir(parents=True)
    (tmp / rel).write_text(src)
    tool = [sys.executable, str(root / "tools/patch-wine-gl-null-sync.py"), str(tmp)]
    r1, r2 = subprocess.run(tool, capture_output=True, text=True), subprocess.run(tool, capture_output=True, text=True)
    check("it applies once, then says it is in", "applied" in r1.stdout and "already" in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
    patched = (tmp / rel).read_text()
    fn = patched[patched.index("static inline GLsync get_unix_sync( GLsync sync )"):]
    fn = fn[:fn.index("\n}\n") + 3]
    moved = tmp / "moved"
    (moved / "dlls/opengl32").mkdir(parents=True)
    (moved / rel).write_text(src.replace("return (GLsync)(UINT_PTR)sync->unix_handle;", "return (GLsync)(UINT_PTR) sync->unix_handle;"))
    r3 = subprocess.run([sys.executable, str(root / "tools/patch-wine-gl-null-sync.py"), str(moved)], capture_output=True, text=True)
    check("a moved anchor is refused by name and the file is left alone", r3.returncode != 0 and "get_unix_sync" in r3.stdout
          and (moved / rel).read_text() == src.replace("return (GLsync)(UINT_PTR)sync->unix_handle;", "return (GLsync)(UINT_PTR) sync->unix_handle;"))
    prog = ("#include <stdint.h>\n#include <stdio.h>\n#include <stddef.h>\ntypedef uintptr_t UINT_PTR;\ntypedef uint64_t UINT64;\n"
            "struct opengl_sync { UINT64 unix_handle; };\ntypedef struct opengl_sync *GLsync;\n" + fn +
            "int main(void) { struct opengl_sync s = { 0x1234 }; printf(\"%d %d\\n\", get_unix_sync(NULL) == NULL, get_unix_sync(&s) == (GLsync)(UINT_PTR)0x1234); return 0; }\n")
    (tmp / "m.c").write_text(prog)
    b = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Werror", "-o", str(tmp / "m"), str(tmp / "m.c")], capture_output=True, text=True)
    check("the patched function compiles on the host", b.returncode == 0, b.stderr)
    if b.returncode == 0:
        out = subprocess.run([str(tmp / "m")], capture_output=True, text=True).stdout.strip()
        check("NULL maps to NULL and a handle to its unix_handle", out == "1 1", out)
print("PASS: opengl32 NULL sync" if ok else "FAIL: opengl32 NULL sync")
sys.exit(0 if ok else 1)
